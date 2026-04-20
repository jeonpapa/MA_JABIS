"""제형(formulation) 감지 공통 헬퍼.

해외 스크레이퍼가 검색 결과의 주변 context (제품명/규격/제형 원문/공고문 등) 를
입력받아 `oral` / `injection` / `unknown` 으로 분류한다.

동일 약제라도 국가별로 oral tablet 과 injection 의 가격이 다르므로 제형 단위
분리 저장이 필수. (예: prevymis — UK 는 oral tablet 240mg, DE/IT 는 injection 240mg).
"""
from __future__ import annotations

import re

ORAL_KEYWORDS = [
    # EN
    "tablet", "tablets", "capsule", "capsules", "oral", "suspension",
    "syrup", "solution for oral", "lozenge", "film-coated", "chewable",
    "tab", "cap",
    # FR
    "comprimé", "comprimés", "gélule", "gélules", "comprime",
    "voie orale", "suspension buvable",
    # DE
    "tablette", "tabletten", "filmtabletten", "filmtabl", "kapsel", "kapseln",
    "granulat", "granules", "granule", "gran ", "zum einnehmen",
    # IT
    "compressa", "compresse", "capsula", "capsule", "uso orale",
    "cpr", "cps", "rivestite", "rivestita",
    # JP (ひらがな + 漢字)
    "錠", "カプセル", "内服", "内用", "経口",
    # 한글
    "정", "캡슐", "경구", "내복",
]

INJECTION_KEYWORDS = [
    # EN
    "injection", "infusion", "intravenous", "iv ", "vial", "ampoule",
    "ampule", "powder for solution for infusion", "powder for concentrate",
    "solution for infusion", "parenteral", "syringe", "prefilled",
    "subcutaneous", "sc ", "intramuscular", "im ",
    # FR
    "solution pour perfusion", "solution injectable", "poudre pour solution",
    "pour perfusion", "flacon",
    # DE
    "injektion", "infusion", "infusionslösung", "infusionskonzentrat",
    "durchstechflasche", "pulver zur herstellung", "konzentrat zur",
    "konzentrat", "inf konz", "inj konz",
    # IT
    "soluzione iniettabile", "soluzione per infusione", "flaconcino",
    "polvere per soluzione", "per iniezione", "uso parenterale",
    "parenterale", "ev infus", "iniettabile",
    # JP
    "注射", "注", "点滴", "静注", "バイアル", "プレフィルド",
    # 한글
    "주사", "주사제", "바이알", "정맥",
]


_ORAL_RE = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(k) for k in ORAL_KEYWORDS if " " not in k and not any(ord(c) > 127 for c in k)) + r")\b"
)
_INJ_RE = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(k) for k in INJECTION_KEYWORDS if " " not in k and not any(ord(c) > 127 for c in k)) + r")\b"
)


def _count_matches(text: str, keywords: list[str]) -> int:
    t = text.lower()
    n = 0
    for kw in keywords:
        if not kw:
            continue
        if any(ord(c) > 127 for c in kw):
            # CJK / accents — 단순 substring
            if kw in text:
                n += 1
        else:
            # ASCII — 단어 경계 또는 substring (짧은 토큰은 단어경계, 나머지는 포함)
            if len(kw) <= 3 and " " not in kw:
                if re.search(rf"\b{re.escape(kw)}\b", t):
                    n += 1
            elif kw in t:
                n += 1
    return n


def detect_form(
    *texts: str,
    dosage_form: str | None = None,
    product_name: str | None = None,
) -> dict:
    """여러 텍스트 조각을 모아 제형을 판정한다.

    반환: {"form_type": "oral"|"injection"|"unknown", "confidence": "high"|"low"|"none"}

    - dosage_form / product_name 은 가장 결정적인 단서 (있으면 가중치 +2)
    - 기타 texts 는 설명문·규격·급여문·extra raw_data 등 어느 것이든 가능
    """
    pool: list[str] = [t for t in texts if t]
    if dosage_form:
        pool.append(dosage_form)
        pool.append(dosage_form)  # 가중치
    if product_name:
        pool.append(product_name)

    blob = " ".join(pool)
    if not blob.strip():
        return {"form_type": "unknown", "confidence": "none"}

    oral = _count_matches(blob, ORAL_KEYWORDS)
    inj  = _count_matches(blob, INJECTION_KEYWORDS)

    if oral == 0 and inj == 0:
        return {"form_type": "unknown", "confidence": "none"}
    if oral > inj:
        return {
            "form_type": "oral",
            "confidence": "high" if oral >= 2 else "low",
        }
    if inj > oral:
        return {
            "form_type": "injection",
            "confidence": "high" if inj >= 2 else "low",
        }
    # 동률 — 가장 강한 단서만 사용
    # injection 용어가 더 구체적인 경우가 많아 tie-break 은 injection 우선
    return {"form_type": "injection", "confidence": "low"}


def normalize_form_type(value: str | None) -> str:
    """외부 입력 정규화 (대소문자 · 약어)."""
    if not value:
        return "unknown"
    v = value.strip().lower()
    if v in {"oral", "po", "tablet", "capsule", "내복"}:
        return "oral"
    if v in {"injection", "iv", "sc", "im", "주사", "inj"}:
        return "injection"
    return "unknown"
