"""
암질심(중증암질환심의위원회) 매체 크롤러 추상 베이스.

설계 메모
---------
- agents/scrapers/base.py:BaseScraper는 "약가 검색 결과"(product_name/local_price 등) 반환용.
  본 클래스는 "기사 신호" dict(url/title/published_at/snippet/signal_phrases) 반환.
  결과 schema가 다르므로 직접 상속하지 않고 Playwright 라이프사이클 패턴만 차용.
- 각 매체 크롤러는 search_articles()를 구현. 운영은 run()에서 일괄 실행.
- 신호 추출(lexicon 매칭, 약물 거명, signal_type 분류)은 signal_extractor.py 책임.
  본 클래스는 raw article만 수집.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)


@dataclass
class Article:
    outlet: str
    url: str
    title: str
    published_at: Optional[datetime] = None
    snippet: str = ""
    raw_html_path: Optional[str] = None       # replay fixture용 (W1 freeze 시 사용)
    extra: dict = field(default_factory=dict)


class BaseAmjilsimCrawler(ABC):
    """매체별 약평위 기사 크롤러 추상 클래스."""

    MEDIA_NAME: str = ""
    MEDIA_TIER: str = ""                  # A / B / D / G
    MEDIA_WEIGHT: float = 1.0
    BASE_URL: str = ""
    CRAWL_DELAY_SEC: float = 2.0          # robots.txt courtesy default

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    # ────────────────────────────────────────────────────────────────────
    # 서브클래스 구현 필수
    # ────────────────────────────────────────────────────────────────────

    @abstractmethod
    async def search_articles(
        self,
        keyword: str,
        date_from: date,
        date_to: date,
        page: Page,
    ) -> list[Article]:
        """매체 사이트에서 키워드+기간으로 암질심 관련 기사 검색해 Article 리스트 반환."""
        raise NotImplementedError

    # ────────────────────────────────────────────────────────────────────
    # 공통 실행
    # ────────────────────────────────────────────────────────────────────

    async def run(
        self,
        keywords: list[str],
        date_from: date,
        date_to: date,
    ) -> list[Article]:
        """모든 키워드에 대해 search_articles 실행, 결과 통합."""
        all_articles: list[Article] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="ko-KR",
            )
            page = await context.new_page()
            try:
                for kw in keywords:
                    logger.info("[%s] '%s' 검색 (%s ~ %s)",
                                self.MEDIA_NAME, kw, date_from, date_to)
                    try:
                        articles = await self.search_articles(kw, date_from, date_to, page)
                        logger.info("[%s] '%s' 결과 %d건",
                                    self.MEDIA_NAME, kw, len(articles))
                        all_articles.extend(articles)
                    except Exception as e:
                        # 단일 매체 실패가 전체 파이프라인을 막지 않게 → deviation_log 패턴
                        logger.error("[%s] '%s' 실패: %s",
                                     self.MEDIA_NAME, kw, e, exc_info=True)
                        # TODO: agents/quality_guard/deviation_log.jsonl 기록
            finally:
                await context.close()
                await browser.close()
        return all_articles
