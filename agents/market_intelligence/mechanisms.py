"""한국 약가 사후관리 기전 분류기.

약가 인하 4대 기전: 적응증 확대 / 특허 만료 / 사용량-연동 / 실거래가 연동.
약가 인상 기전: 약가 유연계약제(표시가 인상, KR-RULE-028).
"""
from __future__ import annotations


PRICE_MECHANISMS = {
    "indication_expansion": {
        "label": "적응증 확대",
        "description": (
            "급여 적응증이 추가·확대되면서 대상 환자군이 증가하고, "
            "국민건강보험공단과의 약가 재협상을 통해 가격이 인하됨. "
            "위험분담제(RSA) 또는 사용량-약가 연동 조항이 동반되는 경우가 많음."
        ),
        "keywords": [
            "적응증 확대", "급여 확대", "추가 적응증", "새 적응증",
            "보험 급여", "급여 기준 변경", "위험분담", "RSA",
            "적응증 추가", "indication", "재협상", "약가 협상",
        ],
    },
    "patent_expiration": {
        "label": "특허 만료",
        "description": (
            "오리지널 의약품의 물질특허·용도특허 만료 후 제네릭 또는 바이오시밀러가 등재되면, "
            "오리지널 약가가 자동으로 인하됨. "
            "이후 추가 제네릭 진입 시 오리지널도 추가 인하 가능."
        ),
        "keywords": [
            "특허 만료", "특허 종료", "제네릭 등재", "바이오시밀러",
            "오리지널 가격 인하", "후발 의약품", "특허 만료 후",
            "복제약", "동등생물의약품", "첫 번째 제네릭",
        ],
    },
    "volume_price": {
        "label": "사용량-연동 약가인하",
        "description": (
            "건강보험 약가 협상 시 설정된 '예상 사용량(보장금액)'을 초과했을 경우, "
            "초과분에 대해 제약사가 환급하거나 상한금액이 인하됨. "
            "주로 고가 항암제·생물학적 제제에 적용."
        ),
        "keywords": [
            "사용량 연동", "약가 연동", "사용량 초과", "환급",
            "약가 조정", "예상 사용량", "보장금액", "사용량-약가",
            "총액 초과", "청구액 초과", "사후 약가 조정",
        ],
    },
    "actual_transaction": {
        "label": "실거래가 연동 약가인하",
        "description": (
            "병·의원이 실제로 구매하는 가격(실거래가)이 보험 상한금액보다 낮을 경우 "
            "실거래가 조사를 통해 상한금액을 현실에 맞게 인하함. "
            "매년 실거래가 조사 결과를 반영하여 약가 고시 갱신."
        ),
        "keywords": [
            "실거래가", "거래가 조사", "실거래가 인하", "약가 현실화",
            "공급가 조정", "시장 거래가", "약가 고시 갱신", "실판매가",
        ],
    },
    # ── 약가 인상 기전 (positive delta) ──────────────────────────────────────
    "flexible_contract": {
        "label": "약가 유연계약제 (표시가 인상)",
        "description": (
            "건강보험공단과 제약사가 표시가(상한금액)와 별도로 실제 상한금액을 합의하는 "
            "계약(KR-RULE-028, 2026 상반기 시행). 해외 참조가격·환자 접근성 등을 위해 "
            "**표시가를 인상**하되 실제 계약가는 별도로 통제함(RSA 환급과 달리 처음부터 별도금액 청구). "
            "따라서 표시가 변동률이 양(+)으로 나타나도 실제가 인상이 아닐 수 있음. "
            "대상: 신규 등재 신약·특허만료 오리지널·위험분담 환급종료 신약·개량신약·바이오시밀러."
        ),
        "keywords": [
            "유연계약", "약가 유연계약", "유연계약제", "표시가 인상", "표시가 조정",
            "별도 상한금액", "별도계약금액", "상한금액 인상", "약가 인상",
            "환급 종료", "환급형 종료", "표시가와 별도",
        ],
    },
}


def classify_mechanism(text: str) -> list:
    """텍스트에서 약가 인하 기전을 탐지, 신뢰도 포함 반환."""
    text_lower = text.lower()
    results = []
    for mech_id, mech in PRICE_MECHANISMS.items():
        matched = [kw for kw in mech["keywords"] if kw.lower() in text_lower]
        if matched:
            confidence = min(1.0, len(matched) / 3)
            results.append({
                "mechanism_id": mech_id,
                "label": mech["label"],
                "description": mech["description"],
                "confidence": round(confidence, 2),
                "matched_keywords": matched[:5],
            })
    results.sort(key=lambda x: -x["confidence"])
    return results
