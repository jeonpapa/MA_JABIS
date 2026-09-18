"""구독 스콥 입력 검증 (설계 §7).

- 스콥 항목(keywords·brands·companies·policy_topics·disease_areas)이 모두 비면 오류.
- 구독 이름에 알려진 약제명이 있는데 스콥에 없으면 경고 (2026-08 '파드셉' 구독 사례:
  이름만 파드셉이고 키워드는 '약가 인하'·'급여 등재' → 파드셉 기사 0건).
"""
from __future__ import annotations

from typing import Iterable

SCOPE_FIELDS = ("keywords", "brands", "companies", "policy_topics", "disease_areas")
MSD_BRANDS = ("키트루다", "가다실", "린파자", "웰리렉", "브리디온", "에멘드", "윈레브에어", "박스뉴반스",
              "엔플론시아", "Keytruda", "Gardasil", "Lynparza", "Welireg")


def validate_scope(sub: dict, known_brands: Iterable[str]) -> tuple[list[str], list[str]]:
    terms = [str(t) for field in SCOPE_FIELDS for t in (sub.get(field) or []) if str(t).strip()]
    errors: list[str] = []
    if not terms:
        errors.append("모니터링 스콥이 비어 있습니다 — 키워드·브랜드·회사·정책토픽·질환 중 1개 이상 필요합니다")
    name = str(sub.get("name") or "").lower()
    lowered = [t.lower() for t in terms]
    warnings: list[str] = []
    for brand in sorted({b.strip() for b in known_brands if b and len(b.strip()) >= 2}, key=len, reverse=True):
        b = brand.lower()
        if b in name and not any(b in t for t in lowered):
            warnings.append(f"구독 이름의 '{brand}' 가 키워드·브랜드에 없습니다 — 이 약제 기사는 수집되지 않습니다")
    return errors, warnings
