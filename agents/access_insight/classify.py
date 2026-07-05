"""뉴스 텍스트 → signal_type 휴리스틱 분류 + 가중치 — Access Insight S1.

키워드 매치는 모듈 상수 lexicon 기반 (추후 `amjilsim_signature_lexicon` DB
시딩으로 대체/보강 가능하도록 이름·구조를 단순하게 유지).
"""
from __future__ import annotations

# signal_type enum (scripts/migrate_amjilsim_v1.py 기준)
PRE_AGENDA_LEAK = "PRE_AGENDA_LEAK"
QUEUE_INVENTORY = "QUEUE_INVENTORY"  # 백필 휴리스틱에서는 미사용 (S5 신선 크롤러 전용)
IR_RELEASE = "IR_RELEASE"
GOV_STATEMENT = "GOV_STATEMENT"
PATIENT_PETITION = "PATIENT_PETITION"
KOL_OPINION = "KOL_OPINION"
RESULT_REPORT = "RESULT_REPORT"

# 우선순위 순서 (특이도 높은 카테고리 먼저 매치).
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (PATIENT_PETITION, ("환자단체", "환우회", "청원", "탄원")),
    (KOL_OPINION, ("학회", "의료진", "전문가", "교수", "의사회", "의학회")),
    (GOV_STATEMENT, ("국회", "복지위", "보건복지위", "의원", "국정감사")),
    (IR_RELEASE, ("ir", "실적", "컨퍼런스콜", "보도자료", "press release", "매출")),
    (RESULT_REPORT, ("암질심 결과", "약평위 결과", "급여 결정", "통과", "부결")),
    (PRE_AGENDA_LEAK, ("상정", "안건", "예정", "심의 예정")),
)

# gov_policy 아카이브 기사에 한해 GOV_STATEMENT 로 인정하는 기관 키워드.
_GOV_POLICY_AGENCY_KEYWORDS = ("복지부", "심평원", "공단", "건정심")


def classify_signal_type(title: str, snippet: str, kind: str) -> tuple[str, list[str]]:
    """(signal_type, matched_phrases) 반환. 키워드 미매치 시 kind 기반 fallback."""
    text = f"{title or ''} {snippet or ''}"
    lowered = text.lower()

    for signal_type, keywords in _KEYWORDS:
        matched = [kw for kw in keywords if kw.lower() in lowered]
        if signal_type == GOV_STATEMENT and kind == "gov_policy":
            matched = matched + [
                kw for kw in _GOV_POLICY_AGENCY_KEYWORDS if kw.lower() in lowered
            ]
        if matched:
            return signal_type, matched

    if kind == "gov_policy":
        return GOV_STATEMENT, []
    return IR_RELEASE, []


# 신호 유형별 기본 가중치 — "공식성이 높을수록 무겁게" (GOV/RESULT/PATIENT 상향, IR 하향).
_TYPE_WEIGHT: dict[str, float] = {
    GOV_STATEMENT: 1.5,
    RESULT_REPORT: 1.5,
    PATIENT_PETITION: 1.4,
    KOL_OPINION: 1.2,
    PRE_AGENDA_LEAK: 1.1,
    QUEUE_INVENTORY: 1.0,
    IR_RELEASE: 0.8,
}

# 매체 tier 배율 — 'D'(미등록/미분류 매체) 가 기본값.
_TIER_MULTIPLIER: dict[str, float] = {
    "A": 1.2,
    "B": 1.0,
    "C": 0.9,
    "D": 0.7,
}


def signal_weight(tier: str = "D", signal_type: str = "") -> float:
    """(tier, signal_type) → weight. 미지정/미매핑 값은 각각 default(1.0/'D') 로 수렴."""
    base = _TYPE_WEIGHT.get(signal_type, 1.0)
    mult = _TIER_MULTIPLIER.get((tier or "D").upper(), _TIER_MULTIPLIER["D"])
    return round(base * mult, 3)
