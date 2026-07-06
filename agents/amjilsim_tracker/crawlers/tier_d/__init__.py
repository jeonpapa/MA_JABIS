"""Tier D 크롤러 — 미등록/미분류 매체 (media_tiers.json 미매핑 도메인).

Naver News API 검색 결과 중 tier1/tier2 에 매핑되지 않은 기타 매체.
신호 가중치는 최저 배율(classify._TIER_MULTIPLIER['D']=0.7)로 반영된다.
공통 엔진은 crawlers/tiered_news.py.
"""
from __future__ import annotations

from agents.amjilsim_tracker.crawlers.tiered_news import TieredArticle, crawl_tier

MEDIA_TIER = "D"


def crawl(keywords=None, lookback_days=None, db_path=None, **fetchers) -> list[TieredArticle]:
    """Tier D 신선 기사 수집. 반환: 정규화 TieredArticle 리스트."""
    from agents.amjilsim_tracker.crawlers.tiered_news import DEFAULT_LOOKBACK_DAYS

    return crawl_tier(
        MEDIA_TIER, keywords,
        lookback_days if lookback_days is not None else DEFAULT_LOOKBACK_DAYS,
        db_path=db_path, **fetchers,
    )
