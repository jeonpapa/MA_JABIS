"""Tier B 크롤러 — 종합일간·경제·통신 (config/media_tiers.json tier2 도메인).

예: 연합뉴스·뉴시스·조선일보·중앙일보 등 25개 종합 매체.
수집 축: Naver News API(tier2 도메인 필터). 공통 엔진은 crawlers/tiered_news.py.
"""
from __future__ import annotations

from agents.amjilsim_tracker.crawlers.tiered_news import TieredArticle, crawl_tier

MEDIA_TIER = "B"


def crawl(keywords=None, lookback_days=None, db_path=None, **fetchers) -> list[TieredArticle]:
    """Tier B 신선 기사 수집. 반환: 정규화 TieredArticle 리스트."""
    from agents.amjilsim_tracker.crawlers.tiered_news import DEFAULT_LOOKBACK_DAYS

    return crawl_tier(
        MEDIA_TIER, keywords,
        lookback_days if lookback_days is not None else DEFAULT_LOOKBACK_DAYS,
        db_path=db_path, **fetchers,
    )
