"""항암 공고 암종 → KCD(심평원 상병마스터, 현재 KCD-9) 매핑.

- 암종 단위 정본 매핑은 사람이 정한 사전(CANCER_KCD). 모든 코드는 실행 시 kcd_code 존재를 검증한다
  (없는 코드는 저장하지 않고 오류 보고).
- KCD 는 발생 부위 기준이다. 형태학적 암종(신경내분비·메르켈·횡문근육종·생식세포·신경모세포종·조직구증)은
  대표 부위 코드로만 매핑하고 confidence 를 낮춰 review_status='needs_review' 로 둔다.
- 행 단위 암종이 있는 표(40. 기타 항암제 병용요법)와 39. 기타 암은 행의 질환명으로 매핑(ROW_KEYWORDS).
"""
from __future__ import annotations

import re
import sqlite3

# code: ([KCD 3·4단 코드], confidence, note)
CANCER_KCD: dict[str, tuple[list[str], float, str]] = {
    "1": (["C34"], 0.95, "소세포 조직형은 KCD 미구분 — 폐 부위 코드"),
    "2": (["C34"], 0.95, "비소세포 조직형은 KCD 미구분 — 폐 부위 코드"),
    "3": (["C16"], 0.95, ""),
    "4": (["C15"], 0.95, ""),
    "5": (["C73"], 0.95, ""),
    "6": (["C25"], 0.95, ""),
    "7-1": (["C220"], 0.9, "간세포암종(C22.0). 상세불명 간암 C22.9 는 별도 확인"),
    "7-2": (["C221", "C23", "C24"], 0.9, "간내담관암(C22.1)·담낭(C23)·기타 담도(C24)"),
    "8": (["C18", "C19", "C20"], 0.95, ""),
    "9": (["C50"], 0.95, ""),
    "10": (["C56", "C570", "C481", "C482"], 0.9, "난소·난관·복막"),
    "11": (["C53"], 0.95, ""),
    "12": (["C54", "C55"], 0.9, "자궁체부·자궁 상세불명"),
    "13": (["C64"], 0.95, ""),
    "14": (["C65", "C66", "C67", "C68"], 0.9, "신우·요관·방광·기타 요로"),
    "15": (["C61"], 0.95, ""),
    "16": (["C00", "C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C10", "C11", "C12", "C13",
            "C14", "C30", "C31", "C32"], 0.85, "구강·인두·비강·부비동·후두"),
    "17-1": (["C17", "C25", "E340"], 0.5, "신경내분비신생물은 부위별 코드(형태코드 미수록) — 대표 부위만"),
    "17-2": (["C44"], 0.7, "메르켈세포암 — 피부 부위 코드"),
    "18": (["C44"], 0.9, ""),
    "19": (["C40", "C41"], 0.9, ""),
    "20": (["C70", "C71", "C72"], 0.9, ""),
    "21": (["C43"], 0.95, ""),
    "22": (["C49"], 0.85, "연조직육종 — 결합조직·연조직 부위 코드"),
    "23": (["C49"], 0.6, "횡문근육종 — 부위 다양(대표: 연조직)"),
    "24": (["C62", "C56", "C381"], 0.6, "생식세포종양 — 고환·난소·종격동 대표 부위"),
    "25": (["C741", "C749", "C47"], 0.7, "신경모세포종 — 부신수질·말초신경"),
    "26": (["C64"], 0.85, "윌름즈종양 — 신장"),
    "27": (["C692"], 0.95, "망막"),
    "28": (["C82", "C83", "C84", "C85", "C86", "C88"], 0.85, "비호지킨림프종 계열"),
    "29": (["C81"], 0.95, ""),
    "30": (["C960", "C965", "C966", "D761", "D763"], 0.6, "랑게르한스세포·혈구탐식성 조직구증 등"),
    "31": (["C900"], 0.95, ""),
    "32": (["C920", "C925", "C926", "C928", "C930", "C940", "C942"], 0.85, "급성골수성백혈병 계열"),
    "33": (["C924"], 0.95, ""),
    "34": (["C921"], 0.95, ""),
    "35": (["C910"], 0.95, ""),
    "36": (["C911"], 0.95, ""),
    "37": (["D46"], 0.95, ""),
    "38": ([], 0.0, "조혈모세포이식 전처치요법 — 질환이 아니라 시술 단계(기저 질환은 각 암종)"),
    "39": ([], 0.0, "기타 암 — 행 단위 매핑"),
    "40": ([], 0.0, "기타 항암제 병용요법 — 행의 '주요 암종' 으로 매핑"),
}

# 행 질환명 → 암종 코드 (40번 표 '주요 암종', 39번 '대상질환' 등). 긴 키워드 먼저.
ROW_TO_CANCER = [
    ("비소세포폐암", "2"), ("소세포폐암", "1"), ("위식도접합부", "3"), ("위암", "3"), ("식도암", "4"),
    ("갑상선", "5"), ("췌장", "6"), ("간세포", "7-1"), ("간암", "7-1"), ("담도", "7-2"), ("담관", "7-2"),
    ("담낭", "7-2"), ("직결장", "8"), ("대장", "8"), ("결장", "8"), ("직장", "8"), ("유방", "9"),
    ("난소", "10"), ("난관", "10"), ("복막", "10"), ("자궁경부", "11"), ("자궁내막", "12"), ("자궁", "12"),
    ("신장암", "13"), ("신세포", "13"), ("요로상피", "14"), ("방광", "14"), ("전립선", "15"), ("두경부", "16"),
    ("신경내분비", "17-1"), ("메르켈", "17-2"), ("피부", "18"), ("골육종", "19"), ("골암", "19"), ("유잉", "19"),
    ("중추신경", "20"), ("뇌", "20"), ("교모세포", "20"), ("흑색종", "21"), ("연조직육종", "22"),
    ("횡문근", "23"), ("생식세포", "24"), ("고환", "24"), ("신경모세포", "25"), ("윌름즈", "26"),
    ("망막모세포", "27"), ("호지킨림프종", "29"), ("비호지킨", "28"), ("림프종", "28"), ("조직구", "30"),
    ("다발골수종", "31"), ("다발성골수종", "31"), ("급성전골수구성", "33"), ("급성골수성", "32"),
    ("만성골수성", "34"), ("급성림프모구", "35"), ("만성림프구성", "36"), ("골수형성이상", "37"),
]
# 공고 암종 체계 밖 질환 → 직접 KCD
ROW_TO_KCD = [
    ("아밀로이드", ["E85"], 0.8), ("중피종", ["C45"], 0.9), ("흉선", ["C37"], 0.9), ("원발부위불명", ["C80"], 0.85),
    ("카포시", ["C46"], 0.9), ("부신피질", ["C740"], 0.9), ("갈색세포종", ["C741"], 0.7), ("항문", ["C21"], 0.9),
    ("외음부", ["C51"], 0.9), ("질암", ["C52"], 0.85), ("음경", ["C60"], 0.9), ("소장", ["C17"], 0.85),
    ("진성적혈구증가증", ["D45"], 0.9), ("본태성혈소판증가증", ["D473"], 0.9), ("골수섬유증", ["D474"], 0.85),
    ("만성골수단핵구성", ["C931"], 0.9), ("발덴스트롬", ["C880"], 0.9), ("버킷", ["C837"], 0.9),
    ("위장관기질종양", ["C49"], 0.6), ("GIST", ["C49"], 0.6), ("융모막암", ["C58"], 0.9), ("융모상피암", ["C58"], 0.9),
    ("임신성융모", ["C58", "D392"], 0.8), ("H-Mole", ["C58", "D392"], 0.6), ("골수증식성", ["D471"], 0.8),
    ("모상세포백혈병", ["C914"], 0.95), ("털세포백혈병", ["C914"], 0.95), ("캐슬만", ["D477"], 0.6),
    ("골거대세포종", ["D480"], 0.6), ("후두유두종", ["D141"], 0.8), ("흉막삼출", ["C782", "J91"], 0.6),
    ("종격동속발성", ["C781"], 0.85), ("과호산구", ["D475"], 0.8), ("평활근종증", ["J848"], 0.5),
]
NOT_APPLICABLE = {"38": "조혈모세포이식 전처치요법 — 질환이 아닌 시술 단계(기저 질환은 각 암종 공고에서 확인)"}


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def cancer_of_text(text: str) -> str | None:
    t = re.sub(r"\s+", "", text or "")
    for kw, code in ROW_TO_CANCER:
        if kw in t:
            return code
    return None


def kcd_for_regimen(rec: dict) -> list[dict]:
    """regimen row(dict) → [{kcd_code, disease_label, method, confidence, review_status, note}]."""
    code = rec["cancer_code"]
    extra = rec.get("extra") or {}
    if code in ("39", "40"):
        label = extra.get("row_cancer") or rec.get("target_text", "")[:80]
        hit = None
        if code == "40":  # 행 '주요 암종' → 없으면(페이지 넘김으로 칸 누락) '허가사항' 문구
            hit = cancer_of_text(extra.get("row_cancer", "")) or cancer_of_text(extra.get("label", ""))
            if hit and not extra.get("row_cancer"):
                label = f"(허가사항 문구로 판정) {hit}"
        first = _nospace((extra.get("row_cancer") or "") + (rec.get("target_text", "").split("\n")[0]))
        for kw, codes, conf in ROW_TO_KCD:       # 행 질환명이 공고 암종 밖 질환이면 그쪽이 우선(아밀로이드증 ≠ 다발골수종)
            if _nospace(kw) in first:
                return [_m(c, kw, "curated", conf, f"{code}번 표 행 질환 키워드") for c in codes]
        if not hit and code == "39":
            hit = cancer_of_text(rec.get("target_text", "").split("\n")[0]) or cancer_of_text(rec.get("target_text", ""))
        if hit and CANCER_KCD[hit][0]:
            codes, conf, note = CANCER_KCD[hit]
            return [_m(c, label, "curated", conf * 0.95, f"{code}번 표 행 암종→{hit}. {note}".strip()) for c in codes]
        text = _nospace((extra.get("row_cancer") or "") + " " + rec.get("target_text", ""))
        for kw, codes, conf in ROW_TO_KCD:
            if _nospace(kw) in text:
                return [_m(c, kw, "curated", conf, f"{code}번 표 행 질환 키워드") for c in codes]
        if re.search(r"NTRK|tumor[\s-]*agnostic|고형암", text, re.I):
            return [_m("", label, "curated", 0.0, "tumor_agnostic")]
        return [_m("", label, "curated", 0.0, "unmapped")]
    if code in NOT_APPLICABLE:
        return [_m("", rec.get("cancer_name_kr", ""), "curated", 0.0, "not_applicable")]
    codes, conf, note = CANCER_KCD.get(code, ([], 0.0, "unknown cancer code"))
    if not codes:
        return [_m("", rec.get("cancer_name_kr", ""), "curated", 0.0, note or "unmapped")]
    return [_m(c, rec.get("cancer_name_kr", ""), "curated", conf, note) for c in codes]


def _m(code, label, method, conf, note):
    return {"kcd_code": code, "disease_label": label, "method": method, "confidence": round(conf, 3),
            "review_status": "auto" if conf >= 0.8 else "needs_review", "note": note}


def validate(conn: sqlite3.Connection, schema: str = "common") -> list[str]:
    """사전의 모든 코드가 kcd_code 에 있는지. → 없는 코드 목록."""
    allc = {c for codes, *_ in CANCER_KCD.values() for c in codes} | {c for _, codes, _ in ROW_TO_KCD for c in codes}
    missing = []
    for c in sorted(allc):
        if not conn.execute(f"SELECT 1 FROM {schema}.kcd_code WHERE code=?", (c,)).fetchone():
            missing.append(c)
    return missing
