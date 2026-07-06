"""Tier A 크롤러 — 제약·의료 전문지 (config/media_tiers.json tier1 도메인).

예: 데일리팜·메디칼타임즈·히트뉴스·뉴스더보이스·약업신문 등 24개 전문지.
수집 축: Naver News API(tier1 도메인 필터) + T1 전문지 직접 검색(갭필러).
공통 엔진은 crawlers/tiered_news.py — 본 패키지는 tier 상수 + 진입점만 유지.
"""
from __future__ import annotations

from agents.amjilsim_tracker.crawlers.tiered_news import TieredArticle, crawl_tier

MEDIA_TIER = "A"


def crawl(keywords=None, lookback_days=None, db_path=None, **fetchers) -> list[TieredArticle]:
    """Tier A 신선 기사 수집. 반환: 정규화 TieredArticle 리스트."""
    from agents.amjilsim_tracker.crawlers.tiered_news import DEFAULT_LOOKBACK_DAYS

    return crawl_tier(
        MEDIA_TIER, keywords,
        lookback_days if lookback_days is not None else DEFAULT_LOOKBACK_DAYS,
        db_path=db_path, **fetchers,
    )
