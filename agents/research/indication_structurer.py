"""Indication Structurer — FDA 라벨 본문 → 5-anchor + FDA variant 구조화.

discussion.md (2026-04-16) 결정 기반:
  - anchor: pivotal_trial / disease / stage / line_of_therapy / population
  - variant (FDA): biomarker_label / combination_label / approval_date / label_excerpt / label_url

LLM = Gemini 2.5-pro grounded.
입력 = USFDAScraper 의 FDARecord (브랜드 + 1.x 적응증 블록 list).
출력 = [{master: {...}, agency: {...}}, ...]  적응증 1개당 dict 하나.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Iterable

from typing import Protocol

from agents.hta_scrapers.us_fda import FDARecord
from agents.research.clients import ask_gemini_grounded


class IndicationBlock(Protocol):
    """FDAIndication / EMAIndication 둘 다 만족하는 공통 shape."""
    code:  str
    label: str
    body:  str

logger = logging.getLogger(__name__)

MAX_RETRIES = 2  # 총 시도 횟수 = MAX_RETRIES + 1
BACKOFF_SECONDS = 5.0

SYSTEM_PROMPT = """당신은 의약품 허가 라벨(FDA Indications and Usage / EMA SmPC section 4.1 등)을 구조화 분석하는 약가·시장접근 전문가입니다.

목표: 단일 적응증 블록 본문을 받아서, 한국 급여 협상 근거로 쓰일 6개 anchor 필드와 허가 라벨 variant 필드를 정확하게 추출합니다.

규칙:
- 모든 값은 라벨 본문에 명시적으로 있는 것만 추출. 추론/유추 금지.
- 본문에 없으면 null. 빈 문자열 금지.
- pivotal_trial 은 KEYNOTE-XXX, CHECKMATE-XXX 같은 trial 명칭만. 본문에 없으면 null. (EMA SmPC 4.1 은 trial 명을 거의 언급하지 않음 → null 이 정상)
- disease 는 **반드시 약어** 로 출력 (풀네임 금지). 표준 약어 표:
    NSCLC (non-small cell lung cancer) / SCLC / RCC (renal cell) / MEL (melanoma) /
    HNSCC (head and neck SCC) / UC (urothelial) / CRC (colorectal) / TNBC (triple-neg breast) /
    BC (breast cancer) / EC (endometrial) / HCC (hepatocellular) / BTC (biliary tract) /
    MCC (Merkel cell) / CC (cervical) / MPM (mesothelioma) / GC (gastric) /
    cHL (classical Hodgkin) / cSCC (cutaneous SCC) / PPGL (pheochromocytoma/paraganglioma) /
    OC (ovarian cancer, incl. fallopian tube, primary peritoneal) /
    PAAD (pancreatic adenocarcinoma) / mCRPC (metastatic castration-resistant prostate) /
    PC (prostate, 비-mCRPC) / DTC (differentiated thyroid) / MTC (medullary thyroid) /
    ATC (anaplastic thyroid) / AML / ALL / MM (multiple myeloma) / DLBCL / FL / MCL /
    ESC (esophageal cancer, 식도암) / SOLID (tumor-agnostic, 고형암 통합).
    - tumor-agnostic indication (MSI-H/dMMR, TMB-H 등 조직 무관): disease=SOLID
    - 식도암(esophageal): disease=ESC (EC=endometrial 과 구분)
    표에 없으면 최대 6자 대문자 약어로 자체 작명.
- **유전성 종양 증후군은 disease 자체로 취급** (biomarker 가 아님): VHL (von Hippel-Lindau disease), Lynch syndrome, BRCA1/2 hereditary syndrome, NF1/2, Cowden, Li-Fraumeni 등.
  본문이 "patients with VHL disease who require therapy for associated RCC tumours" 처럼 syndrome + 그로 인한 종양 형태이면, **disease=VHL** (syndrome 우선), 종양 부위는 restriction_note 에 기술. biomarker_class 는 all_comers.
  단, syndrome 명시 없이 단순히 "MSI-H/dMMR Lynch-related CRC" 처럼 종양 + 분자마커 형태이면 disease=원종양, biomarker_class=msi_h.
- stage: metastatic / advanced / locally advanced / unresectable / resectable / adjuvant / neoadjuvant / recurrent 중 본문 표현을 그대로 (조합이면 가장 구체적인 표현 하나).
- line_of_therapy: 1L / 2L / 3L+ / adjuvant / neoadjuvant / perioperative. "first-line" → 1L. "prior therapy" 언급이 있으면 2L 또는 3L+. 명시 없으면 null.
- population: adult / pediatric / adult_and_pediatric. 연령 cutoff 있으면 괄호로 (예: "adult_and_pediatric (>=12 years)").
- biomarker_label: 라벨 본문 표현 그대로 (예: "PD-L1 TPS >=50%", "MSI-H or dMMR", "CPS ≥10", "all comers").
- biomarker_class: 매칭용 정규화 키. 다음 중 하나만 사용:
    msi_h          : MSI-H 또는 dMMR
    tmb_h          : TMB-H (≥10 mut/Mb 등)
    pdl1_50        : PD-L1 TPS≥50% 또는 CPS≥50 또는 CPS≥20
    pdl1_10        : PD-L1 CPS≥10 (50 미만)
    pdl1_1         : PD-L1 TPS≥1% 또는 CPS≥1 (10 미만)
    pdl1_pos       : PD-L1 양성이지만 임계값 본문에 없음
    her2_pos / her2_neg / her2_low / egfr_mut / alk_pos / ros1_pos / ntrk_pos / braf_v600 / kras_g12c
    brca_mut       : BRCA1 또는 BRCA2 mutation (germline 또는 somatic). "deleterious" / "pathogenic" / "suspected" 포함
    hrd_pos        : Homologous Recombination Deficiency (HRD) positive — BRCA + genomic instability
    hrr_mut        : HRR (homologous recombination repair) gene mutation (BRCA 외 ATM/CHEK2/PALB2 등 포함)
    all_comers     : biomarker 무관 승인 (본문에 biomarker 언급 없음)
    null           : 어디에도 해당 안 되는 드문 케이스
- combination_label: monotherapy 또는 병용 약제 그대로 (예: "monotherapy", "in combination with pemetrexed and platinum chemotherapy", "in combination with axitinib").
- restriction_note: 추가 제한 (예: "following complete resection", "after progression on platinum-based chemo") 가 있으면 간단히. 없으면 null.
- 출력은 JSON 객체 1개. 배열로 감싸지 말 것. 코드블록 백틱 금지. 설명 금지.
- **PMDA 입력(일본어)**: 본문이 일본어(예: "○悪性黒色腫", "根治切除不能又は転移性の腎細胞癌") 일 수 있음.
  그대로 표준 영문 약어로 정규화:
    悪性黒色腫 / メラノーマ → MEL
    非小細胞肺癌 → NSCLC
    小細胞肺癌 → SCLC
    腎細胞癌 → RCC
    頭頸部癌 / 頭頸部扁平上皮癌 → HNSCC
    尿路上皮癌 / 膀胱癌 → UC
    結腸・直腸癌 / 大腸癌 → CRC
    乳癌 (HR+HER2-) → BC, (HER2-/HR-) → TNBC, (HER2+) → BC
    子宮体癌 / 子宮内膜癌 → EC (endometrial)
    子宮頸癌 → CC
    食道癌 / 食道扁平上皮癌 → ESC (esophageal, EC 와 충돌 방지)
    肝細胞癌 → HCC
    胆道癌 / 胆管癌 → BTC
    胃癌 / 胃・食道接合部腺癌 → GC
    悪性胸膜中皮腫 → MPM
    古典的ホジキンリンパ腫 → cHL
    原発性縦隔大細胞型B細胞リンパ腫 → PMBCL
    卵巣癌 / 卵管癌 / 原発性腹膜癌 → OC
    膵癌 → PAAD
    前立腺癌 (去勢抵抗性) → mCRPC
    フォン・ヒッペル・リンドウ病 → VHL (syndrome 우선)
  biomarker 일본어 표현 → class:
    高頻度マイクロサテライト不安定性 / MSI-High → msi_h
    高い腫瘍遺伝子変異量 / TMB-High → tmb_h
    PD-L1陽性 → pdl1_pos (TPS/CPS 수치 명시 없으면)
    BRCA遺伝子変異陽性 → brca_mut
    相同組換え修復欠損 / HRD → hrd_pos
    HER2陽性 → her2_pos / HER2陰性 → her2_neg
  stage 일본어:
    治癒切除不能 / 切除不能 / 根治切除不能 → unresectable
    進行・再発 / 進行又は再発 → advanced
    局所進行 → locally advanced
    転移性 / 遠隔転移 → metastatic
    術後補助 → adjuvant / 術前補助 → neoadjuvant / 術前・術後 → perioperative
  line_of_therapy:
    がん化学療法後に増悪 / 化学療法歴 → 2L 또는 3L+ (본문에서 구체화)
    初回化学療法後の維持療法 → 1L_maintenance
    一次化学療法 / 未治療 → 1L
- **MFDS 입력(한국어)**: 본문이 한국어(예: "비소세포폐암", "수술이 불가능하거나 전이성인") 일 수 있음.
  한국어 → 영문 약어 정규화는 일본어와 동일 원칙. 추가 매핑:
    흑색종 → MEL / 비소세포폐암 → NSCLC / 악성 흉막 중피종 → MPM
    두경부(편평상피세포)암 → HNSCC / 요로상피암 → UC / 직결장암 → CRC
    삼중음성 유방암 → TNBC / 자궁내막암 → EC / 자궁경부암 → CC
    식도암 → ESC / 위암(위식도접합부 포함) → GC / 신세포암 → RCC
    담도암 → BTC / 간세포암 → HCC / 전형적 호지킨 림프종 → cHL
    고형암(조직 무관, tumor-agnostic) → SOLID
  한국어 biomarker:
    고빈도-현미부수체 불안정성 / MSI-H / dMMR → msi_h
    PD-L1 발현 양성(TPS≥50%) → pdl1_50 / (CPS≥10) → pdl1_10 / (CPS≥1) → pdl1_1
    HER2 양성 → her2_pos / HER2 음성 → her2_neg
    EGFR 변이 → egfr_mut / ALK 변이 → alk_pos
  한국어 stage:
    수술이 불가능 / 절제 불가 → unresectable
    진행성 / 전이성 → advanced or metastatic
    재발성 → recurrent
    수술 후 보조요법 → adjuvant / 수술 전 보조요법 → neoadjuvant
  한국어 line_of_therapy:
    1차 치료 → 1L / 이전 요법 후 진행 → 2L+ / 보조요법 → adjuvant
"""

USER_TMPL = """다음은 {agency} 허가 라벨의 단일 적응증 블록입니다.

브랜드: {brand}
적응증 헤더: {label}
원본 코드: {code}

본문:
\"\"\"
{body}
\"\"\"

위 본문에서 다음 JSON 스키마로 추출하세요:

{{
  "anchor": {{
    "pivotal_trial":   "KEYNOTE-XXX 같은 trial 명 또는 null",
    "disease":         "NSCLC 같은 질환 약어 또는 null",
    "stage":           "metastatic / advanced 등 또는 null",
    "line_of_therapy": "1L / 2L / 3L+ / adjuvant 등 또는 null",
    "population":      "adult / pediatric 등 또는 null",
    "biomarker_class": "msi_h / tmb_h / pdl1_50 / pdl1_10 / all_comers / ... 중 하나"
  }},
  "variant": {{
    "biomarker_label":   "라벨 원문 그대로 또는 null",
    "combination_label": "monotherapy 또는 병용약제 또는 null",
    "restriction_note":  "추가 제한 또는 null"
  }}
}}
"""


# ─── slug 생성 ───────────────────────────────────────────────────────────────
def _slugify(*parts: str | None) -> str:
    pieces = []
    for p in parts:
        if not p:
            continue
        s = re.sub(r"[^a-zA-Z0-9]+", "_", str(p).lower()).strip("_")
        if s:
            pieces.append(s)
    return "_".join(pieces)


_DISEASE_ALIASES = {
    # full name → canonical abbrev
    "ovarian cancer": "OC",
    "ovarian": "OC",
    "epithelial ovarian cancer": "OC",
    "fallopian tube cancer": "OC",
    "primary peritoneal cancer": "OC",
    "ovarian fallopian tube or primary peritoneal cancer": "OC",
    "pancreatic adenocarcinoma": "PAAD",
    "adenocarcinoma of the pancreas": "PAAD",
    "pancreatic cancer": "PAAD",
    "breast cancer": "BC",
    "breast": "BC",
    "early breast cancer": "BC",
    "hr positive breast cancer": "BC",
    "hr-positive breast cancer": "BC",
    "triple negative breast cancer": "TNBC",
    "triple-negative breast cancer": "TNBC",
    "metastatic castration resistant prostate cancer": "mCRPC",
    "metastatic castration-resistant prostate cancer": "mCRPC",
    "castration resistant prostate cancer": "mCRPC",
    "castration-resistant prostate cancer": "mCRPC",
    "prostate cancer": "PC",
    "non small cell lung cancer": "NSCLC",
    "non-small cell lung cancer": "NSCLC",
    "non-small-cell lung cancer": "NSCLC",
    "non small cell lung carcinoma": "NSCLC",
    "non-small cell lung carcinoma": "NSCLC",
    "small cell lung cancer": "SCLC",
    "renal cell carcinoma": "RCC",
    "melanoma": "MEL",
    "head and neck squamous cell carcinoma": "HNSCC",
    "squamous cell carcinoma of the head and neck": "HNSCC",
    "urothelial carcinoma": "UC",
    "urothelial cancer": "UC",
    "bladder cancer": "UC",
    "colorectal cancer": "CRC",
    "colorectal carcinoma": "CRC",
    "endometrial cancer": "EC",
    "endometrial carcinoma": "EC",
    "hepatocellular carcinoma": "HCC",
    "liver cancer": "HCC",
    "biliary tract cancer": "BTC",
    "cholangiocarcinoma": "BTC",
    "merkel cell carcinoma": "MCC",
    "cervical cancer": "CC",
    "mesothelioma": "MPM",
    "malignant pleural mesothelioma": "MPM",
    "gastric cancer": "GC",
    "gastric or gastroesophageal junction adenocarcinoma": "GC",
    "classical hodgkin lymphoma": "cHL",
    "hodgkin lymphoma": "cHL",
    "solid tumor": "SOLID",
    "solid tumors": "SOLID",
    "msi-h solid tumor": "SOLID",
    "msi-h solid tumors": "SOLID",
    "msi-high solid tumor": "SOLID",
    "tmb-h solid tumor": "SOLID",
    "tmb-h solid tumors": "SOLID",
    "tmb-high solid tumor": "SOLID",
    "msihca": "SOLID",
    "esophageal cancer": "ESC",
    "esophageal carcinoma": "ESC",
    "esophageal squamous cell carcinoma": "ESC",
    "oesophageal cancer": "ESC",
    "oesophageal carcinoma": "ESC",
    "gastroesophageal junction cancer": "GC",
    "cutaneous squamous cell carcinoma": "cSCC",
    "pheochromocytoma": "PPGL",
    "paraganglioma": "PPGL",
    "pheochromocytoma or paraganglioma": "PPGL",
    "differentiated thyroid cancer": "DTC",
    "medullary thyroid cancer": "MTC",
    "anaplastic thyroid cancer": "ATC",
    "acute myeloid leukemia": "AML",
    "acute lymphoblastic leukemia": "ALL",
    "multiple myeloma": "MM",
    "diffuse large b-cell lymphoma": "DLBCL",
    "follicular lymphoma": "FL",
    "mantle cell lymphoma": "MCL",
    # ── PMDA 일본어 (LLM 정규화 실패 대비) ──
    "悪性黒色腫": "MEL",
    "メラノーマ": "MEL",
    "非小細胞肺癌": "NSCLC",
    "小細胞肺癌": "SCLC",
    "腎細胞癌": "RCC",
    "頭頸部癌": "HNSCC",
    "頭頸部扁平上皮癌": "HNSCC",
    "尿路上皮癌": "UC",
    "膀胱癌": "UC",
    "結腸・直腸癌": "CRC",
    "大腸癌": "CRC",
    "乳癌": "BC",
    "子宮体癌": "EC",
    "子宮内膜癌": "EC",
    "子宮頸癌": "CC",
    "食道癌": "ESC",
    "食道扁平上皮癌": "ESC",
    "肝細胞癌": "HCC",
    "胆道癌": "BTC",
    "胆管癌": "BTC",
    "胃癌": "GC",
    "悪性胸膜中皮腫": "MPM",
    "中皮腫": "MPM",
    "古典的ホジキンリンパ腫": "cHL",
    "ホジキンリンパ腫": "cHL",
    "原発性縦隔大細胞型b細胞リンパ腫": "PMBCL",
    "原発性縦隔大細胞型B細胞リンパ腫": "PMBCL",
    "卵巣癌": "OC",
    "卵管癌": "OC",
    "原発性腹膜癌": "OC",
    "膵癌": "PAAD",
    "前立腺癌": "PC",
    "去勢抵抗性前立腺癌": "mCRPC",
    "転移性去勢抵抗性前立腺癌": "mCRPC",
    "フォン・ヒッペル・リンドウ病": "VHL",
    "フォン・ヒッペル・リンドウ病関連腫瘍": "VHL",
    "vhl病": "VHL",
    "固形癌": "SOLID",
    "固形腫瘍": "SOLID",
    # ── MFDS 한국어 (LLM 정규화 실패 대비) ──
    "흑색종": "MEL",
    "비소세포폐암": "NSCLC",
    "소세포폐암": "SCLC",
    "신세포암": "RCC",
    "두경부암": "HNSCC",
    "두경부편평상피세포암": "HNSCC",
    "요로상피암": "UC",
    "방광암": "UC",
    "직결장암": "CRC",
    "대장암": "CRC",
    "유방암": "BC",
    "삼중음성 유방암": "TNBC",
    "삼중음성유방암": "TNBC",
    "자궁내막암": "EC",
    "자궁체암": "EC",
    "자궁경부암": "CC",
    "식도암": "ESC",
    "간세포암": "HCC",
    "담도암": "BTC",
    "위암": "GC",
    "악성 흉막 중피종": "MPM",
    "호지킨 림프종": "cHL",
    "전형적 호지킨 림프종": "cHL",
    "난소암": "OC",
    "췌장암": "PAAD",
    "전립선암": "PC",
    "고형암": "SOLID",
    "고형종양": "SOLID",
}


def normalize_disease(disease: str | None) -> str | None:
    """LLM 이 약어 대신 풀네임/이형 표기를 낼 때 표준 약어로 매핑.

    이미 표준 약어인 경우(대소문자 정확)는 그대로, 풀네임/이형 표기는 _DISEASE_ALIASES 에서 탐색.
    매핑 실패 시 원문 보존하되 모든 공백을 단일 스페이스로 압축.
    복합 disease ("EC, GC, BTC") 는 각 파트를 개별 정규화 후 재결합.
    """
    if not disease or not isinstance(disease, str):
        return disease
    raw = re.sub(r"\s+", " ", disease).strip()
    if not raw:
        return None

    if "," in raw:
        parts = [normalize_disease(p.strip()) for p in raw.split(",")]
        parts = [p for p in parts if p]
        return ", ".join(parts) if parts else None

    key = raw.lower().rstrip(".")
    if key in _DISEASE_ALIASES:
        return _DISEASE_ALIASES[key]
    if 2 <= len(raw) <= 8 and raw.replace("-", "").isalnum():
        return raw
    return raw


def _norm_null(v):
    """LLM 이 문자열 'null' 로 반환한 경우 실제 None 으로."""
    if isinstance(v, str) and v.strip().lower() in ("null", "none", ""):
        return None
    return v


def normalize_combination(label: str | None) -> str | None:
    """combination_label → slug 토큰. 중복 anchor + 다른 병용약 충돌 방지용.

    같은 disease/LoT/stage/biomarker 라도 병용약이 다르면 별개 indication.
    예: RCC 1L + axitinib vs RCC 1L + lenvatinib.
    """
    if not label:
        return None
    s = label.lower().strip()
    if not s:
        return None
    if "monotherapy" in s or "single agent" in s or "single-agent" in s:
        return "mono"

    # 알려진 약제 키워드 (우선순위 = list 순서)
    drugs = re.findall(
        r"\b(axitinib|lenvatinib|trastuzumab|pemetrexed|paclitaxel|carboplatin|"
        r"cisplatin|gemcitabine|enfortumab|bevacizumab|chemoradiotherapy|"
        r"chemoradiation|fluorouracil|platinum|chemotherapy|radiotherapy)\b",
        s,
    )
    seen: list[str] = []
    for d in drugs:
        if d not in seen:
            seen.append(d)
    if not seen:
        return None  # 알 수 없는 병용 — slug 충돌 위험 감수
    # 차별화 강한 약제 우선
    for primary in ("axitinib", "lenvatinib", "trastuzumab", "enfortumab", "bevacizumab"):
        if primary in seen:
            return "ev" if primary == "enfortumab" else primary
    if "chemoradiotherapy" in seen or "chemoradiation" in seen:
        return "crt"
    if "carboplatin" in seen and "paclitaxel" in seen:
        return "carbo_pacl"
    if "gemcitabine" in seen and "cisplatin" in seen:
        return "gem_cis"
    if "pemetrexed" in seen:
        return "pemetrexed"
    if "platinum" in seen and "fluorouracil" in seen:
        return "platinum_fu"
    if "platinum" in seen:
        return "platinum"
    if "chemotherapy" in seen:
        return "chemo"
    return seen[0]


def make_indication_id(
    product: str,
    anchor: dict,
    combination_label: str | None = None,
    fallback_code: str = "",
) -> str:
    """6-anchor + combination 기반 slug. 빠진 필드는 건너뜀.

    예: keytruda_nsclc_1l_metastatic_pdl1_50_keynote_024
    예: keytruda_rcc_1l_advanced_axitinib (combination 으로 충돌 회피)
    """
    bio = anchor.get("biomarker_class")
    # all_comers / null 은 slug 에 넣어도 정보가 없어 생략
    bio_slug = bio if bio and bio not in ("all_comers", "null", None) else None
    combo_slug = normalize_combination(combination_label)
    parts = [
        product,
        anchor.get("disease"),
        anchor.get("line_of_therapy"),
        anchor.get("stage"),
        bio_slug,
        combo_slug,
        anchor.get("pivotal_trial"),
    ]
    sid = _slugify(*parts)
    if not sid or sid == _slugify(product):
        # anchor 가 거의 비어있으면 1.x 코드라도 붙여 충돌 방지
        sid = _slugify(product, "ind", fallback_code)
    return sid


# ─── LLM 호출 + JSON 파싱 ────────────────────────────────────────────────────
def _balanced_json_blocks(text: str):
    """text 내 brace-balanced {...} / [...] 블록 (start, end) 를 차례로 yield.

    문자열 리터럴(\"...\") 내부의 중괄호는 무시하며, 백슬래시 이스케이프 처리한다.
    JSON_RE 가 첫 '{' 부터 마지막 '}' 까지 greedy 매칭해 잡아내던 'extra data'
    오류를 막기 위함.
    """
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch not in "{[":
            i += 1
            continue
        open_ch  = ch
        close_ch = "}" if ch == "{" else "]"
        depth = 0
        in_str = False
        escape = False
        j = i
        closed = False
        while j < n:
            c = text[j]
            if in_str:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        closed = True
                        break
            j += 1
        if not closed:
            return  # 닫는 괄호 없이 종료
        yield (i, j + 1)
        i = j + 1


def _extract_json(text: str) -> dict | None:
    """LLM 응답에서 우리 스키마({anchor, variant}) 에 부합하는 JSON 객체 1개 추출.

    - 코드블록 ``` 제거
    - brace-balanced 스캔으로 첫 완전한 {...} 또는 [...] 블록부터 시도
    - schema-shaped (anchor/variant 키 포함) 가 우선, 아니면 첫 dict 폴백
    """
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    fallback: dict | None = None
    last_err: str | None = None
    for start, end in _balanced_json_blocks(text):
        candidate = text[start:end]
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError as e:
            last_err = f"{e} @ {start}:{end}"
            continue

        if isinstance(obj, list):
            picked = next((x for x in obj if isinstance(x, dict)), None)
            if picked is None:
                continue
            obj = picked

        if not isinstance(obj, dict):
            continue

        if "anchor" in obj or "variant" in obj:
            return obj
        if fallback is None:
            fallback = obj

    if fallback is None and last_err:
        logger.warning("JSON 파싱 실패: %s — text head: %r", last_err, text[:200])
    return fallback


def structure_indication(
    product: str,
    brand: str,
    indication: IndicationBlock,
    label_url: str | None = None,
    effective_time: str | None = None,
    agency: str = "FDA",
) -> dict | None:
    """단일 적응증 블록 → {master, agency} dict.

    agency: "FDA" | "EMA" | ...  (variant row 에 기록될 기관명)
    실패(LLM 에러 / JSON 파싱 실패) 시 None.
    """
    if not indication.body:
        # 라벨/요약 블록만 있고 본문이 없으면 구조화 불가
        return None

    prompt = USER_TMPL.format(
        agency=agency,
        code=indication.code,
        brand=brand,
        label=indication.label or "(헤더 없음)",
        body=indication.body[:3000],
    )

    parsed: dict | None = None
    last_err: str | None = None
    for attempt in range(MAX_RETRIES + 1):
        resp = ask_gemini_grounded(prompt=prompt, system=SYSTEM_PROMPT)
        if "error" in resp:
            last_err = resp["error"]
            logger.warning("LLM 실패 [%s %s %s] (%d/%d): %s",
                           agency, brand, indication.code, attempt + 1,
                           MAX_RETRIES + 1, last_err)
        else:
            parsed = _extract_json(resp.get("text", ""))
            if parsed:
                break
            last_err = "JSON 파싱 실패"
            logger.warning("JSON 파싱 실패 [%s %s %s] (%d/%d)",
                           agency, brand, indication.code, attempt + 1, MAX_RETRIES + 1)
        if attempt < MAX_RETRIES:
            time.sleep(BACKOFF_SECONDS)

    if not parsed:
        logger.warning("최종 실패 [%s %s %s]: %s", agency, brand, indication.code, last_err)
        return None

    anchor = parsed.get("anchor") or {}
    variant = parsed.get("variant") or {}

    # 정규화: 문자열 "null" → None, disease 풀네임 → 약어
    for k in ("pivotal_trial", "disease", "stage", "line_of_therapy",
              "population", "biomarker_class"):
        anchor[k] = _norm_null(anchor.get(k))
    for k in ("biomarker_label", "combination_label", "restriction_note"):
        variant[k] = _norm_null(variant.get(k))
    anchor["disease"] = normalize_disease(anchor.get("disease"))
    # biomarker_class None → all_comers (라벨에 biomarker 언급 없음 = 무제약 승인)
    # 매칭이 disease+bio 필수이므로 None 은 매칭 불가로 이어짐 — all_comers 가 합리적 기본값
    if anchor.get("biomarker_class") is None:
        anchor["biomarker_class"] = "all_comers"

    indication_id = make_indication_id(
        product, anchor,
        combination_label=variant.get("combination_label"),
        fallback_code=indication.code,
    )

    master = {
        "indication_id": indication_id,
        "product": product,
        "pivotal_trial":   anchor.get("pivotal_trial"),
        "disease":         anchor.get("disease"),
        "stage":           anchor.get("stage"),
        "line_of_therapy": anchor.get("line_of_therapy"),
        "population":      anchor.get("population"),
        "biomarker_class": anchor.get("biomarker_class"),
        "title": indication.label[:200] if indication.label else None,
        "fda_indication_code": indication.code if agency == "FDA" else None,
    }

    agency_row = {
        "indication_id": indication_id,
        "agency": agency,
        "biomarker_label":   variant.get("biomarker_label"),
        "combination_label": variant.get("combination_label"),
        "approval_date":     effective_time,
        "label_excerpt":     indication.body[:2000],
        "label_url":         label_url,
        "restriction_note":  variant.get("restriction_note"),
        "raw_source":        json.dumps({
            "source_code":   indication.code,
            "label_header":  indication.label,
            "source_agency": agency,
        }, ensure_ascii=False),
    }
    return {"master": master, "agency": agency_row}


def structure_record(record: FDARecord, product_slug: str) -> Iterable[dict]:
    """FDARecord 의 모든 1.x 적응증 → 구조화 dict iterator.

    None(실패) 은 skip.
    """
    brand = record.brand_names[0] if record.brand_names else product_slug
    for ind in record.indications:
        result = structure_indication(
            product=product_slug,
            brand=brand,
            indication=ind,
            label_url=record.label_url,
            effective_time=record.effective_time,
        )
        if result:
            yield result


if __name__ == "__main__":
    import sys
    from agents.hta_scrapers.us_fda import USFDAScraper

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    drug = sys.argv[1] if len(sys.argv) > 1 else "pembrolizumab"
    slug = sys.argv[2] if len(sys.argv) > 2 else drug.lower()

    print(f"\n=== FDA fetch: {drug} ===")
    records = USFDAScraper().search(drug)
    if not records:
        print("FDA 결과 없음")
        sys.exit(1)

    rec = records[0]
    print(f"brand: {rec.brand_names} / generic: {rec.generic_names}")
    print(f"indications: {len(rec.indications)}건")
    print(f"effective_time: {rec.effective_time}")
    print(f"label_url: {rec.label_url}")

    print(f"\n=== Structuring → product_slug={slug} ===")
    for i, item in enumerate(structure_record(rec, product_slug=slug), 1):
        m = item["master"]
        a = item["agency"]
        print(f"\n[{i}] {m['indication_id']}")
        print(f"    title:    {m['title']}")
        print(f"    anchor:   trial={m['pivotal_trial']} | disease={m['disease']} | "
              f"stage={m['stage']} | LoT={m['line_of_therapy']} | pop={m['population']}")
        print(f"    FDA var:  biomarker={a['biomarker_label']!r} | combo={a['combination_label']!r}")
        if a["restriction_note"]:
            print(f"    restrict: {a['restriction_note']}")
