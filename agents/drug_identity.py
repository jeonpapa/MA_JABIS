"""자유 텍스트 약제 입력 → 확정 정체성 후보(brand + INN) 해석.

우선순위:
  1) drug_aliases (product_alias_map + BRAND_TO_MOLECULE) — substring 매칭, 즉시
  2) 미스 시 kr_mfds_permit.lookup_permit — 제품명↔주성분(영문), 권위 소스

후보: {slug, brand, inn, display, matched_as('brand'|'ingredient'), source('alias'|'mfds')}
검색 파이프라인은 이 후보의 brand/inn/slug 만 소비한다(단방향).
"""
from __future__ import annotations

import logging

from agents.db import drug_aliases
from agents.scrapers.kr_mfds_permit import lookup_permit

logger = logging.getLogger(__name__)


def _alias_candidates(q: str) -> list[dict]:
    """alias 맵 키(slug·inn·brand_aliases)에 substring 매칭 → slug 기준 dedupe."""
    key = q.strip().lower()
    if not key:
        return []
    cache = drug_aliases._load_db_cache()          # {alias_lower: {slug, inn, brand_aliases}}
    by_slug: dict[str, dict] = {}
    # 1) DB 맵
    for alias_lower, entry in cache.items():
        if key in alias_lower:
            slug = entry["slug"]
            inn = entry.get("inn") or ""
            matched_as = "ingredient" if (inn and key in inn.lower()) else "brand"
            prev = by_slug.get(slug)
            # 더 정확한(=exact) 매칭 또는 brand 매칭을 우선 표시
            if prev is None or (alias_lower == key):
                by_slug[slug] = {
                    "slug": slug,
                    "brand": drug_aliases.display_name(slug),
                    "inn": inn,
                    "display": drug_aliases.display_name(slug),
                    "matched_as": matched_as,
                    "source": "alias",
                }
    # 2) in-memory BRAND_TO_MOLECULE 보충 (DB 미적용 환경)
    for brand, mol in drug_aliases.BRAND_TO_MOLECULE.items():
        if key in brand or key in mol:
            slug = brand
            if slug in by_slug:
                continue
            by_slug[slug] = {
                "slug": slug, "brand": brand, "inn": mol, "display": brand,
                "matched_as": "ingredient" if key in mol else "brand",
                "source": "alias",
            }
    # exact 매칭을 앞으로 정렬
    out = list(by_slug.values())
    out.sort(key=lambda c: (key not in c["slug"].lower(), c["slug"]))
    return out


def _mfds_candidate(q: str) -> list[dict]:
    """alias 미스 시 MFDS 허가 API 로 제품명↔주성분(영문) 후보 1건."""
    try:
        res = lookup_permit(item_name=q) or {}
    except Exception as e:            # 키 미설정·네트워크 실패 → 후보 없음(호출측이 raw 폴백)
        logger.info("[identity] MFDS 폴백 실패: %s", e)
        return []
    if res.get("source") == "miss":
        return []
    # lookup_permit 은 summary 를 flat dict 로 반환 — 래핑 형태도 함께 수용
    summary = res.get("summary") or res
    brand = (summary.get("item_eng_name") or summary.get("item_name") or "").strip()
    inn = (summary.get("main_ingr_eng") or "").strip()
    if not brand and not inn:
        return []
    slug = (brand or inn).lower()
    return [{
        "slug": slug, "brand": brand or slug, "inn": inn,
        "display": brand or slug, "matched_as": "brand", "source": "mfds",
    }]


def resolve_identity(q: str, limit: int = 6) -> list[dict]:
    """자유 텍스트 → 확정 후보 리스트. alias 우선, 미스 시 MFDS 폴백."""
    if not q or not q.strip():
        return []
    cands = _alias_candidates(q)
    if cands:
        return cands[:limit]
    return _mfds_candidate(q)[:limit]
