"""약제 등재 아날로그 검색 — 패싯 도메인 용어 → 구조화 필드 술어.

온톨로지 concept 로 표현되지 않는 흔한 검색어(희귀/항암제/경평면제/총액제한 등)를
analog_reports 의 구조화 컬럼 술어로 매핑한다. concept_resolver 가 쿼리를 공백 단위
그룹으로 쪼갤 때, 온톨로지에 없는 청크는 여기서 도메인 용어로 해석되어 AND 그룹이 된다.

설계
  - 각 DomainTerm 은 동일한 의미의 SQL 술어(where_sql, 후보 풀 수집용)와
    Python 술어(row_pred, 후보별 그룹 충족 판정용)를 둘 다 제공한다 (일관성 보장).
  - 매칭은 정규화된 청크 전체 일치 (tag_seeds.normalize). 부분 포함 매칭 금지(오탐 방지).
  - 온톨로지 concept 가 우선. concept 매칭 실패 청크만 도메인 용어로 시도.

예: "희귀 항암제" → [orphan, oncology] 두 그룹의 AND.
    "경평면제 총액제한" → [pe_waiver, rsa_total_cap] 두 그룹의 AND.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from agents.analog import tag_seeds as ts


def _truthy(v) -> bool:
    return v is not None and str(v).strip() not in ("", "0", "None")


def _col(row, key):
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


@dataclass
class DomainTerm:
    key: str
    label: str                         # 칩 표시용 한국어
    aliases: tuple[str, ...]           # raw 표기 (정규화 전)
    where_sql: str                     # analog_reports alias 'a' 기준 SQL 술어
    row_pred: Callable[[object], bool] # 후보 row(dict/Row) → 충족 여부


# ── 도메인 용어 정의 ────────────────────────────────────────────────────────────
DOMAIN_TERMS: list[DomainTerm] = [
    # 질환군 ----------------------------------------------------------------
    DomainTerm(
        "oncology", "항암제",
        ("항암", "항암제", "항암요법", "항암화학요법", "항암약", "암치료제", "oncology", "anticancer"),
        "(a.disease_category IN ('항암','Oncology') OR a.cancer_type IS NOT NULL "
        "OR a.disease_category_detail IN ('고형암','혈액종양'))",
        lambda r: (_col(r, "disease_category") in ("항암", "Oncology")
                   or _truthy(_col(r, "cancer_type"))
                   or _col(r, "disease_category_detail") in ("고형암", "혈액종양")),
    ),
    DomainTerm(
        "non_oncology", "비항암",
        ("비항암", "비항암제", "general"),
        "(a.disease_category IN ('비항암','General'))",
        lambda r: _col(r, "disease_category") in ("비항암", "General"),
    ),
    DomainTerm(
        "orphan", "희귀",
        ("희귀", "희귀의약품", "희귀질환", "희귀질환치료제", "희귀약", "orphan"),
        "(a.disease_category IN ('희귀','Orphan') OR a.disease_category_detail = '희귀')",
        lambda r: (_col(r, "disease_category") in ("희귀", "Orphan")
                   or _col(r, "disease_category_detail") == "희귀"),
    ),
    DomainTerm(
        "solid_tumor", "고형암",
        ("고형암", "고형종양"),
        "a.disease_category_detail = '고형암'",
        lambda r: _col(r, "disease_category_detail") == "고형암",
    ),
    DomainTerm(
        "heme", "혈액종양",
        ("혈액종양", "혈액암"),
        "a.disease_category_detail = '혈액종양'",
        lambda r: _col(r, "disease_category_detail") == "혈액종양",
    ),
    DomainTerm(
        "autoimmune", "자가면역",
        ("자가면역", "자가면역질환"),
        "a.disease_category_detail = '자가면역'",
        lambda r: _col(r, "disease_category_detail") == "자가면역",
    ),
    DomainTerm(
        "metabolic", "대사질환",
        ("대사질환", "대사성질환"),
        "a.disease_category_detail = '대사질환'",
        lambda r: _col(r, "disease_category_detail") == "대사질환",
    ),
    # 등재 트랙 / RSA -------------------------------------------------------
    DomainTerm(
        "pe_waiver", "경평면제",
        ("경평면제", "경제성평가면제", "경제성평가생략", "경평생략", "경평제외", "pewaiver"),
        "a.pe_waiver = 1",
        lambda r: _col(r, "pe_waiver") in (1, "1"),
    ),
    DomainTerm(
        "rsa", "위험분담",
        ("위험분담", "위험분담제", "위험분담계약", "rsa"),
        "a.has_rsa = 1",
        lambda r: _col(r, "has_rsa") in (1, "1"),
    ),
    DomainTerm(
        "rsa_total_cap", "총액제한",
        ("총액제한", "총액제한형", "총액제한제"),
        "a.rsa_type_hint = '총액제한'",
        lambda r: _col(r, "rsa_type_hint") == "총액제한",
    ),
    DomainTerm(
        "rsa_refund", "환급형",
        ("환급형", "환급형rsa"),
        "a.rsa_type_hint = '환급형'",
        lambda r: _col(r, "rsa_type_hint") == "환급형",
    ),
    DomainTerm(
        "postmarket", "사후관리",
        ("사후관리", "사후관리조건", "사후관리조건부"),
        "a.has_postmarket_condition = 1",
        lambda r: _col(r, "has_postmarket_condition") in (1, "1"),
    ),
    DomainTerm(
        "cost_effective", "비용효과",
        ("비용효과", "비용효과성", "비용효과입증"),
        "(a.approval_driver = 'COST_EFFECTIVE' OR a.reimbursement_track_ko LIKE '%비용효과%')",
        lambda r: (_col(r, "approval_driver") == "COST_EFFECTIVE"
                   or "비용효과" in (_col(r, "reimbursement_track_ko") or "")),
    ),
    DomainTerm(
        "cua", "비용효용",
        ("비용효용", "비용효용분석", "cua"),
        "a.reimbursement_track_ko LIKE '%비용효용%'",
        lambda r: "비용효용" in (_col(r, "reimbursement_track_ko") or ""),
    ),
    DomainTerm(
        "wap", "대체약제 가중평균가",
        ("가중평균가", "대체약제가중평균가", "wap", "대체약제"),
        "a.reimbursement_track_ko LIKE '%가중평균가%'",
        lambda r: "가중평균가" in (_col(r, "reimbursement_track_ko") or ""),
    ),
    # 심의 결과 ------------------------------------------------------------
    DomainTerm(
        "rejected", "급여 불인정",
        ("불인정", "급여불인정", "비급여", "등재거부", "급여거부"),
        "(a.review_result IN ('REJECTED','REJECTED_COST') OR a.approval_driver = 'REJECTED_COST')",
        lambda r: (_col(r, "review_result") in ("REJECTED", "REJECTED_COST")
                   or _col(r, "approval_driver") == "REJECTED_COST"),
    ),
]


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, str]:
    """normalized alias → domain_key."""
    idx: dict[str, str] = {}
    for dt in DOMAIN_TERMS:
        for a in dt.aliases:
            n = ts.normalize(a)
            if n:
                idx.setdefault(n, dt.key)
    return idx


@lru_cache(maxsize=1)
def _by_key() -> dict[str, DomainTerm]:
    return {dt.key: dt for dt in DOMAIN_TERMS}


def match(chunk_norm: str) -> DomainTerm | None:
    """정규화된 청크 전체 일치로 도메인 용어 탐색."""
    key = _alias_index().get(chunk_norm)
    return _by_key().get(key) if key else None


def by_key(key: str) -> DomainTerm | None:
    return _by_key().get(key)
