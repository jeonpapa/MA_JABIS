"""KR MFDS 변경이력 → indications_master 적응증 매핑.

목적:
  indications_by_agency.agency='MFDS' 레코드의 approval_date 가 추정값이었는데,
  `kr_mfds_history` 로 수집한 공식 변경이력을 기반으로 *실제 승인일* 로 교체한다.

전략:
  1) 각 indication 에 대해 "disease_kr + LoT_kr + combo_kr + biomarker_kr" 키워드 시그니처 생성
  2) 변경이력 버전을 시간 오름차순으로 훑으며 → 시그니처가 모두 충족되는 가장 이른 버전을 선택
  3) 그 버전의 change_date = MFDS 공식 approval_date (date_source='mfds_official')
  4) 매칭 실패 시 date_source='unverified' 로 마킹 (기존 추정값은 유지)

본 모듈은 DB 를 직접 업데이트하지 않고 (indication_id, official_date, matched_version, confidence)
튜플을 돌려준다. DB 반영은 호출 측 (e.g. scripts/apply_mfds_official_dates.py) 이 담당.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agents.hta_scrapers.kr_mfds_history import (
    MFDSHistVersion,
    fetch_versions,
)

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"


# ─── 한국어 키워드 사전 ─────────────────────────────────────────────────────

# disease_area(영문) → 한국어 disease 키워드 (여러 개면 OR)
DISEASE_KR: dict[str, list[str]] = {
    "MEL":    ["흑색종"],
    "NSCLC":  ["비소세포폐암"],
    "HNSCC":  ["두경부"],             # 두경부편평상피세포암 / 두경부암
    "cHL":    ["호지킨"],             # 전형적 호지킨 림프종
    "UC":     ["요로상피", "방광"],     # 요로상피암 / 비근침습성 방광암
    "RCC":    ["신세포"],             # 신세포암
    "EC":     ["자궁내막"],
    "TNBC":   ["삼중음성"],
    "CRC":    ["직결장"],             # 직결장암
    "SOLID":  ["MSI-H", "TMB", "현미부수체"],
    "GC":     ["위 또는", "위식도", "위암"],
    "CC":     ["자궁경부"],
    "ESC":    ["식도"],
    "HCC":    ["간세포"],
    "MPM":    ["중피종", "흉막"],
    "BTC":    ["담도"],
    "OC":     ["난소"],
    "cSCC":   ["피부편평"],
    "MCC":    ["메르켈"],
    "PMBCL":  ["원발성 종격동 거대 B세포", "PMBCL"],
    "VHL":    ["폰히펠", "폰 히펠", "von Hippel", "VHL"],
    "BC":     ["유방암"],
    "PAAD":   ["췌장"],
    "mCRPC":  ["전립선"],
    "DTC":    ["갑상선"],
    # ── MFDS 현행 라벨에 미등장 적응증 (FDA/EMA 만) — 향후 커버리지 대비 ──
    # THYC: DTC("갑상선") 와 충돌 방지 위해 역형성/미분화/수질 한정
    "PPGL":   ["크롬친화세포종", "부신경절종", "부갈색세포종"],
    "T2DM":   ["제2형 당뇨병", "2형 당뇨", "제2형당뇨병"],
    "THYC":   ["역형성 갑상선", "미분화 갑상선", "수질 갑상선"],
    "THYMIC": ["흉선"],
}

# ─── Layer 스펙 (AND/OR/NOT 조합) ──────────────────────────────────────────

@dataclass
class LayerSpec:
    """한 layer 가 blob 에 대해 충족해야 할 조건.

    - include_any: 이 리스트 중 하나라도 포함 (OR) — 비어 있으면 조건 없음
    - include_all: 이 리스트 모두 포함 (AND)
    - exclude:     이 리스트 중 하나라도 포함되면 거부 (NOT)
    """
    include_any: list[str] = field(default_factory=list)
    include_all: list[str] = field(default_factory=list)
    exclude:     list[str] = field(default_factory=list)

    def matches(self, blob: str) -> bool:
        if self.include_any and not any(k in blob for k in self.include_any):
            return False
        if self.include_all and not all(k in blob for k in self.include_all):
            return False
        if self.exclude and any(k in blob for k in self.exclude):
            return False
        return True


# line_of_therapy → LayerSpec
# 주의: 1L 은 한국어 라벨에서 "1차" 가 생략되는 경우가 잦아 layer 를 추가하지 않는다.
#       (disease + combo + biomarker 조합이 이미 1L 를 식별)
# adj/neo/peri 는 MFDS 에서 한 문단이 "neoadjuvant .. adjuvant" 둘 다 언급하는
# perioperative 구조를 쓰기 때문에 include/exclude 로 명확히 구분해야 한다.
LOT_KR: dict[str, LayerSpec] = {
    "2L":   LayerSpec(include_any=["2차", "이전 치료", "이전의 치료", "치료 후",
                                    "받은 후", "이후에 진행", "진행이 확인", "재발", "도중"]),
    "2L+":  LayerSpec(include_any=["2차", "3차", "이전 치료", "이전의 치료", "이전의 요법",
                                    "치료 후", "받은 후", "이후에 진행", "진행이 확인",
                                    "재발", "도중", "이전의 전신"]),
    "3L+":  LayerSpec(include_any=["3차 이상", "3차", "이전의 치료", "이전 치료"]),
    # adj-only: "현재 요법" 표기 두 형태 중 하나 필수 + neo 요법 괄호표기 없음
    #   1) 키트루다 style: "수술 후 보조요법(adjuvant)" (영문 괄호)
    #   2) 린파자 style:   "환자의 수술 후 보조요법" (문장 말미)
    #   이렇게 해야 전이성 indication 의 적격성 조건
    #   ("…환자의 치료. 환자는 수술 전 보조요법, 수술 후 보조요법, 또는 전이성 조건에서…")
    #   이 adjuvant 로 오매칭되지 않는다.
    "adjuvant":    LayerSpec(include_any=["수술 후 보조요법(adjuvant)", "환자의 수술 후 보조요법"],
                             exclude=["(neoadjuvant)"]),
    # neo-only: 대칭적 규칙
    "neoadjuvant": LayerSpec(include_any=["수술 전 보조요법(neoadjuvant)", "환자의 수술 전 보조요법"],
                             exclude=["(adjuvant)"]),
    # perioperative: 두 영문괄호 모두 (동일 문장에 neo+adj 현재요법이 모두 선언)
    "perioperative": LayerSpec(include_all=["(neoadjuvant)", "(adjuvant)"]),
    "maintenance":   LayerSpec(include_any=["유지"]),
}

# combination_label 부분 문자열 → 한국어 키워드 (매칭 layer 로 추가, layer 내부는 OR)
# 주의: 'monotherapy' / 'chemotherapy' 일반 화학요법은 너무 흔해 discriminative 하지 않아 제외.
COMBO_KR: list[tuple[str, list[str]]] = [
    ("axitinib",          ["엑시티닙"]),
    ("lenvatinib",        ["렌바티닙"]),
    ("bevacizumab",       ["베바시주맙"]),
    ("trastuzumab",       ["트라스투주맙"]),
    ("enfortumab",        ["엔포투맙"]),
    ("pemetrexed",        ["페메트렉시드"]),
    ("carboplatin",       ["카보플라틴"]),
    ("paclitaxel",        ["파클리탁셀"]),
    ("cisplatin",         ["시스플라틴"]),
    ("gemcitabine",       ["젬시타빈"]),
    ("fluoropyrimidine",  ["플루오로피리미딘", "플루오로우라실"]),
    ("fluorouracil",      ["플루오로우라실", "5-FU"]),
    ("chemoradiotherapy", ["화학방사선"]),
    ("platinum",          ["백금"]),
]

# biomarker_class → 한국어 hint (optional AND)
BIOMARKER_KR: dict[str, list[str]] = {
    "pdl1_50":  ["TPS≥50", "발현 비율≥50", "CPS≥50"],
    "pdl1_10":  ["CPS≥10", "TPS≥10"],
    "pdl1_1":   ["CPS≥1", "TPS≥1", "발현 비율≥1"],
    "pdl1_pos": ["PD-L1 발현 양성"],
    "msi_h":    ["MSI-H", "고빈도-현미부수체", "현미부수체 불안정성", "dMMR"],
    "tmb_h":    ["TMB"],
    "her2_neg": ["HER2 음성"],
    "her2_pos": ["HER2 양성"],
}


# ─── 시그니처 구성 ──────────────────────────────────────────────────────────

@dataclass
class IndicationSig:
    indication_id: str
    disease_area:  str
    line_of_therapy: Optional[str]
    biomarker_class: Optional[str]
    combination_label: Optional[str]
    # 첫 번째 layer 는 disease (segment 레벨), 이후 layers 는 sub-indication 레벨
    disease_layer: LayerSpec
    sub_layers: list[LayerSpec]


def _combo_layers(combo_label: Optional[str]) -> list[LayerSpec]:
    if not combo_label:
        return []
    s = combo_label.lower()
    layers: list[LayerSpec] = []
    for key, kr_keys in COMBO_KR:
        if key in s and kr_keys:
            layers.append(LayerSpec(include_any=kr_keys))
    return layers


def build_signatures(db_path: Path = DB_PATH, product: str = "keytruda") -> list[IndicationSig]:
    sigs: list[IndicationSig] = []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT m.indication_id, m.disease, m.line_of_therapy, m.biomarker_class, "
            "       a.combination_label "
            "FROM indications_by_agency a "
            "JOIN indications_master m ON m.indication_id = a.indication_id "
            "WHERE a.agency='MFDS' AND m.product=? "
            "ORDER BY m.disease, m.line_of_therapy",
            (product,),
        ).fetchall()

    for ind_id, disease, lot, biomarker, combo in rows:
        disease_kr = DISEASE_KR.get(disease or "", [])
        disease_layer = LayerSpec(include_any=disease_kr) if disease_kr else LayerSpec()

        sub_layers: list[LayerSpec] = []
        lot_spec = LOT_KR.get(lot or "")
        if lot_spec is not None:
            sub_layers.append(lot_spec)
        for cl in _combo_layers(combo):
            sub_layers.append(cl)
        bm_kr = BIOMARKER_KR.get(biomarker or "", [])
        if bm_kr:
            sub_layers.append(LayerSpec(include_any=bm_kr))

        sigs.append(IndicationSig(
            indication_id=ind_id,
            disease_area=disease or "",
            line_of_therapy=lot,
            biomarker_class=biomarker,
            combination_label=combo,
            disease_layer=disease_layer,
            sub_layers=sub_layers,
        ))
    return sigs


# ─── 매칭 ───────────────────────────────────────────────────────────────────

# Post-2018 flat 구조에서 "disease header 전용 PARAGRAPH" 를 감지하기 위한 휴리스틱.
# 예: "흑색종", "비소세포폐암", "두경부암", "고빈도-현미부수체 불안정성(MSI-H) 암"
# 특징: 번호(1./2.) 없음, 마침표 없음, 길이 짧음, '치료'/'환자' 같은 단어 없음.
def _is_disease_header(p: str) -> bool:
    s = p.strip()
    if not s or len(s) > 40:
        return False
    # 번호/불릿 제거 전 원본 그대로 기준
    if s[:2] in ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."):
        return False
    if any(ch in s for ch in [".", "、", ",", ":"]):
        return False
    if any(kw in s for kw in ["치료", "환자", "요법", "병용", "단독", "수술", "PD-L1"]):
        return False
    return True


def _disease_segments(ver: MFDSHistVersion) -> list[tuple[str, str]]:
    """한 버전을 (disease_title, blob) 단위 세그먼트로 분해.

    - ARTICLE 에 title 이 있으면 (pre-2018, 2021+): ARTICLE = 한 세그먼트
    - title 이 없으면 (2018-01-24 ~ 2020-08-27): PARAGRAPH 중 disease header 같은
      bare 단어를 구분자로 segment
    """
    titled = [a for a in ver.articles if a.title]
    if titled:
        return [(a.title, "\n".join(a.paragraphs)) for a in titled]

    all_paras: list[str] = []
    for a in ver.articles:
        all_paras.extend(a.paragraphs)

    segments: list[tuple[str, str]] = []
    current_title = ""
    current_buf: list[str] = []
    for p in all_paras:
        if _is_disease_header(p):
            if current_title or current_buf:
                segments.append((current_title, "\n".join(current_buf)))
            current_title = p.strip()
            current_buf = []
        else:
            current_buf.append(p)
    if current_title or current_buf:
        segments.append((current_title, "\n".join(current_buf)))
    return segments


# 숫자 번호 단위 sub-indication 분리: "1. xx\n2. yy\n3. zz" → ["1. xx", "2. yy", "3. zz"]
# - 번호가 없는 단일 indication (e.g. welireg VHL): 전체 body 를 하나의 block
# - sub-bullet (&#x2981; / &#xff65; ...) 은 상위 번호 block 에 포함
# - HTML span wrapping (<span class="indent2">5. ...</span>) 도 처리
_RE_NUMBERED = re.compile(r"(?:^|\n)(?:<span[^>]*>)?(?=\d{1,2}[\.)]\s)")


def _split_sub_indications(body: str) -> list[str]:
    parts = [p.strip() for p in _RE_NUMBERED.split(body) if p and p.strip()]
    return parts if parts else [body.strip()]


def _version_has_match(ver: MFDSHistVersion, sig: IndicationSig) -> Optional[str]:
    """버전 내 disease 세그먼트의 sub-indication 블록 중 하나가 시그니처를 만족하면 excerpt 반환.

    2단계 매칭:
      1) segment (disease ARTICLE / disease-header 블록) 가 disease layer 만족
      2) segment body 를 숫자 단위 sub-indication 으로 split → 각 sub-item 이
         LoT/combo/biomarker layers 를 모두 만족해야 함

    segment blob 단위가 아닌 sub-indication 단위로 평가해야 "perioperative (neo+adj)"
    항목이 "adjuvant only" 시그니처에 오매칭되는 문제를 막을 수 있다.
    """
    dlayer = sig.disease_layer
    has_disease_constraint = bool(dlayer.include_any or dlayer.include_all or dlayer.exclude)

    for title, body in _disease_segments(ver):
        seg_blob = title + "\n" + body if title else body
        if has_disease_constraint and not dlayer.matches(seg_blob):
            continue
        if not sig.sub_layers:
            # disease 만 있으면 세그먼트 만족으로 충분
            return (title + ": " + body).replace("\n", " ")[:200] if title else body[:200]
        for sub in _split_sub_indications(body):
            if all(layer.matches(sub) for layer in sig.sub_layers):
                return (title + ": " + sub).replace("\n", " ")[:200]
    return None


@dataclass
class MappingResult:
    indication_id: str
    disease_area:  str
    line_of_therapy: Optional[str]
    combination_label: Optional[str]
    official_date: Optional[str]       # 시간상 첫 매칭 버전의 change_date
    matched_excerpt: Optional[str]
    n_layers: int
    confidence: str                    # 'high' | 'medium' | 'low' | 'unmatched'


def find_missing_disease_kr(product: str, db_path: Path = DB_PATH) -> list[str]:
    """product 의 MFDS indication 중 DISEASE_KR 미등록 disease 키 반환.

    리턴 값이 비어있지 않으면 해당 disease 는 signature 에 disease_layer 가 공란이라
    매칭 0 을 유발한다. `DISEASE_KR` dict 에 실제 MFDS 라벨의 한국어 표현을
    추가해야 한다.
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT m.disease FROM indications_master m "
            "JOIN indications_by_agency a ON m.indication_id = a.indication_id "
            "WHERE m.product = ? AND a.agency = 'MFDS' AND m.disease IS NOT NULL",
            (product,),
        ).fetchall()
    return sorted({r[0] for r in rows if r[0] and r[0] not in DISEASE_KR})


def _confidence(n_layers: int, matched: bool) -> str:
    if not matched:
        return "unmatched"
    if n_layers >= 3:
        return "high"
    if n_layers == 2:
        return "medium"
    return "low"


def map_indications(item_seq: str, product: str = "keytruda",
                    db_path: Path = DB_PATH) -> list[MappingResult]:
    versions = fetch_versions(item_seq)
    sigs = build_signatures(db_path=db_path, product=product)

    out: list[MappingResult] = []
    for sig in sigs:
        hit_date: Optional[str] = None
        hit_excerpt: Optional[str] = None
        has_any_layer = bool(
            sig.disease_layer.include_any
            or sig.disease_layer.include_all
            or sig.sub_layers
        )
        if has_any_layer:
            for ver in versions:     # 시간 오름차순
                excerpt = _version_has_match(ver, sig)
                if excerpt:
                    hit_date = ver.change_date
                    hit_excerpt = excerpt
                    break

        n_layers = (1 if sig.disease_layer.include_any else 0) + len(sig.sub_layers)
        out.append(MappingResult(
            indication_id=sig.indication_id,
            disease_area=sig.disease_area,
            line_of_therapy=sig.line_of_therapy,
            combination_label=sig.combination_label,
            official_date=hit_date,
            matched_excerpt=hit_excerpt,
            n_layers=n_layers,
            confidence=_confidence(n_layers, hit_date is not None),
        ))
    return out


if __name__ == "__main__":
    import sqlite3
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # 키트루다
    results = map_indications("201501487", product="keytruda")

    # 기존 추정값 비교
    with sqlite3.connect(DB_PATH) as conn:
        existing = {
            r[0]: r[1] for r in conn.execute(
                "SELECT indication_id, approval_date FROM indications_by_agency WHERE agency='MFDS'"
            ).fetchall()
        }

    print(f"{'indication_id':<65}  {'disease':<8}  {'LoT':<14}  "
          f"{'old_est':<12}  {'official':<12}  {'conf':<10}  diff")
    print("-" * 150)
    for r in results:
        old = existing.get(r.indication_id, "")
        diff = ""
        if old and r.official_date:
            if old != r.official_date:
                diff = f"CHANGED (old={old})"
            else:
                diff = "same"
        elif not r.official_date:
            diff = "NO MATCH"
        print(f"{r.indication_id:<65}  {r.disease_area:<8}  "
              f"{str(r.line_of_therapy or ''):<14}  "
              f"{str(old or '-'):<12}  {str(r.official_date or '-'):<12}  "
              f"{r.confidence:<10}  {diff}")
