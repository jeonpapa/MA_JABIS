"""국가별 가격 공개 정책 메타 — 스크레이퍼 결과 빈값의 원인을 UI 에 투명화.

"스크레이퍼 실패" 와 "데이터 원천 자체 미공개" 를 구분해야 사용자가 판단 가능.
빈 결과 반환 시 스크레이퍼가 `raw_data.coverage_policy` 에 이 메타를 주입하면
프론트엔드가 국가 카드에 정책 안내를 표시.
"""
from __future__ import annotations

from typing import Optional

# 국가 × (제품 카테고리) → 공개 정책
# category 는 단순화해 `oncology_hospital` / `oncology` / `general` / `*`(전체)
COVERAGE_POLICY: dict[tuple[str, str], dict] = {
    ("FR", "oncology_hospital"): {
        "policy": "프랑스 BDPM 공식 DB 는 병원 전용 항암제의 PPH(병원가) 를 공개하지 않음",
        "public_db_has_price": False,
        "source_hint": "제조사 공식 보도자료 또는 학회 발표 참조",
    },
    ("FR", "*"): {
        "policy": "BDPM 에 등록된 약제는 PPC(소비자가) 공시. 병원 전용 약제는 미공개.",
        "public_db_has_price": "partial",
    },
    ("CA", "oncology"): {
        "policy": "Ontario EAP 미등재 시 전국 통합 공개 DB 없음. pCPA 협약가는 비공개.",
        "public_db_has_price": "partial",
        "structural_absence": True,     # EAP 목록에 추적 제품 미등재 — 빈 결과는 제도상 공백(수집 실패 아님)
    },
    ("CA", "*"): {
        "policy": "캐나다는 연방 통합 공개 가격 DB 없음 — Ontario EAP 가 유일한 공개 소스.",
        "public_db_has_price": "partial",
        "structural_absence": True,     # 위와 같음 — 신선도 판정(foreign_country_coverage)이 과제 대상에서 뺀다
    },
    ("DE", "*"): {
        "policy": "Rote Liste 는 DocCheck (의료전문가 인증) 로그인 필수",
        "requires_auth": True,
    },
    ("UK", "*"): {
        "policy": "MIMS 구독 필요 (BNF/NICE 는 공개)",
        "requires_auth": True,
    },
    ("US", "*"): {
        "policy": "Micromedex 구독 필요. WAC 만 사용 (AWP 는 double-count 금지)",
        "requires_auth": True,
    },
    ("JP", "*"): {
        "policy": "후생노동성 약가기준 공개 (NHI price). 中医協 답신 후 등재.",
        "public_db_has_price": True,
    },
    ("AU", "*"): {
        "policy": "PBS Schedule 공개. PBAC 권고 후 등재 — 미등재 시 사보험 적용",
        "public_db_has_price": True,
    },
    ("AU", "oncology_hospital"): {
        "policy": "PBS S100 (특수의약품) 항암제는 공공 병원 한정. PBAC 권고 후 effective.",
        "public_db_has_price": True,
    },
    ("IT", "*"): {
        "policy": "AIFA 공시가격 (PNT_C). 병원약제는 가격 협상 결과 비공개일 수 있음.",
        "public_db_has_price": "partial",
    },
    ("CH", "*"): {
        "policy": "Compendium 등재 가격 (Publikumspreis) 공개. SL (Spezialitätenliste) 미등재 약제는 보험 적용 X.",
        "public_db_has_price": True,
    },
    ("EU", "*"): {
        "policy": "EU 단위 통합 가격 DB 없음. EMA 는 허가만 — 가격은 회원국별",
        "public_db_has_price": False,
    },
    ("KR", "*"): {
        "policy": "HIRA 약가기준 공개. RSA(위험분담제) 약은 표시가 ≠ 실제가 (계약 비공개)",
        "public_db_has_price": True,
    },
}


# 제품 slug → 카테고리 간단 매핑 (확장 가능)
PRODUCT_CATEGORY: dict[str, str] = {
    "keytruda":  "oncology_hospital",
    "opdivo":    "oncology_hospital",
    "tecentriq": "oncology_hospital",
    "imfinzi":   "oncology_hospital",
    "welireg":   "oncology",
    "lynparza":  "oncology",
    "lenvima":   "oncology",
    "gardasil":  "general",
    "januvia":   "general",
    "prevymis":  "general",   # 항바이러스
}


def lookup_policy(country: str, product_slug: str | None = None) -> Optional[dict]:
    """국가 + (선택) product slug 로 공개 정책 조회.

    우선순위: (country, category) → (country, "*") → None
    """
    cc = (country or "").upper()
    if not cc:
        return None
    category = None
    if product_slug:
        category = PRODUCT_CATEGORY.get(product_slug.lower())
    if category:
        key = (cc, category)
        if key in COVERAGE_POLICY:
            return dict(COVERAGE_POLICY[key])
    key = (cc, "*")
    if key in COVERAGE_POLICY:
        return dict(COVERAGE_POLICY[key])
    return None


def is_structural_absence(country: str, product_slug: str | None = None) -> bool:
    """빈 결과가 **제도상 사실**인가 — 수집 실패로 보지 않는다.

    True 인 경우: 정책 메타에 structural_absence (CA: EAP 에 추적 제품 없음) 또는 public_db_has_price=False
    (FR 병원 전용 항암제 PPH 비공개). '구독·로그인 필요'(US/UK/DE) 는 제도상 공백이 아니다 — 거기서 비면 로그인이 깨진 것이다.
    신선도 판정(agents/agent_tasks/freshness.py:foreign_country_coverage)이 이 한 곳을 기준으로 과제 대상을 가른다.
    """
    policy = lookup_policy(country, product_slug) or {}
    return bool(policy.get("structural_absence")) or policy.get("public_db_has_price") is False


def describe_empty_result(country: str, product_slug: str | None = None) -> str:
    """빈 결과에 대한 사용자용 한 줄 설명. 정책 메타 있으면 policy, 없으면 generic."""
    policy = lookup_policy(country, product_slug)
    if policy:
        return policy.get("policy", f"{country} 공개 가격 정보 없음")
    return f"{country} 스크레이퍼가 결과를 찾지 못함 (검색어 확인 필요)"
