"""네이버 뉴스 검색 API 래퍼.

- client_id / client_secret 은 config/.env 의 NAVER_API_CLIENT_ID / NAVER_API_CLIENT_SECRET 로 주입
- 하드코딩 금지 (CLAUDE.md 규칙)
- 사용처: Home 미디어 인텔리전스 (1개월 브랜드 트래픽), Daily Mailing, CompetitorTrendsAgent
"""
from __future__ import annotations

import logging
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import requests

BASE_DIR = Path(__file__).parent.parent
_env_path = BASE_DIR / "config" / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=False)
    except ImportError:
        pass

logger = logging.getLogger(__name__)

NAVER_API_URL = "https://openapi.naver.com/v1/search/news.json"


@dataclass
class NewsItem:
    title: str          # HTML tag 제거된 제목
    link: str           # 네이버 뉴스 링크 (또는 원문)
    original_link: str  # 원문 링크 (있을 때)
    description: str    # 요약 (HTML 제거)
    pub_date: datetime  # 발행일 (RFC822 파싱)
    source: str = ""    # 매체명 (link 에서 파생)

    @property
    def date_str(self) -> str:
        return self.pub_date.strftime("%Y-%m-%d")


@dataclass
class NaverNewsClient:
    client_id: str = field(default_factory=lambda: os.getenv("NAVER_API_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("NAVER_API_CLIENT_SECRET", ""))
    timeout: int = 10
    min_delay_ms: int = 120  # API quota 보호 (초당 10회 제한)
    _last_call: float = 0.0

    def __post_init__(self):
        if not self.client_id or not self.client_secret:
            logger.warning(
                "[NaverNews] API 키 미설정 — config/.env NAVER_API_CLIENT_ID / NAVER_API_CLIENT_SECRET 확인"
            )

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _throttle(self):
        elapsed = time.time() - self._last_call
        wait = self.min_delay_ms / 1000.0 - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def search(
        self,
        query: str,
        display: int = 100,
        start: int = 1,
        sort: str = "date",
    ) -> list[NewsItem]:
        """네이버 뉴스 검색. 최신순(date) 기본. start 로 페이지네이션."""
        if not self.is_configured:
            return []
        self._throttle()
        try:
            resp = requests.get(
                NAVER_API_URL,
                headers={
                    "X-Naver-Client-Id": self.client_id,
                    "X-Naver-Client-Secret": self.client_secret,
                },
                params={"query": query, "display": display, "start": start, "sort": sort},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error("[NaverNews] 검색 실패 (%s): %s", query, e)
            return []

        items = []
        for it in data.get("items", []):
            try:
                pd = _parse_pub_date(it.get("pubDate", ""))
            except Exception:
                continue
            title = _strip_html(it.get("title", ""))
            link = it.get("link", "")
            orig = it.get("originallink", "") or link
            desc = _strip_html(it.get("description", ""))
            items.append(NewsItem(
                title=title,
                link=link,
                original_link=orig,
                description=desc,
                pub_date=pd,
                source=_extract_source(orig or link),
            ))
        return items

    def daily_counts(
        self,
        query: str,
        days: int = 30,
        max_pages: int = 10,
    ) -> tuple[dict[str, int], list[NewsItem]]:
        """지난 `days` 일간 일별 기사 건수 + raw item 목록.

        네이버 API 는 sort=date 일 때 최대 1,000건 (start ≤ 1000) 까지 제공.
        한 달 한국 제약 뉴스는 보통 수백 건 — display=100 × 10 페이지로 커버.
        """
        cutoff = datetime.now() - timedelta(days=days)
        all_items: list[NewsItem] = []
        for page in range(max_pages):
            start = 1 + page * 100
            if start > 1000:
                break
            batch = self.search(query, display=100, start=start, sort="date")
            if not batch:
                break
            all_items.extend(batch)
            # 마지막 item 이 cutoff 보다 과거면 중단
            if batch[-1].pub_date < cutoff:
                break

        # 기간 필터
        in_range = [it for it in all_items if it.pub_date >= cutoff]
        # 일별 집계
        counts: dict[str, int] = {}
        for it in in_range:
            counts[it.date_str] = counts.get(it.date_str, 0) + 1
        return counts, in_range

    def latest_news(self, query: str, limit: int = 5) -> list[NewsItem]:
        """최신 뉴스 상위 N건 — 브랜드 클릭 시 사용."""
        return self.search(query, display=limit, start=1, sort="date")[:limit]


def _strip_html(s: str) -> str:
    import re
    s = re.sub(r"<[^>]+>", "", s or "")
    # HTML entities
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", "\"").replace("&#39;", "'").replace("&nbsp;", " "))


def _parse_pub_date(s: str) -> datetime:
    # RFC822: "Fri, 18 Apr 2026 09:12:00 +0900"
    from email.utils import parsedate_to_datetime
    dt = parsedate_to_datetime(s)
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)
    return dt


def _extract_source(url: str) -> str:
    if not url:
        return ""
    try:
        netloc = urllib.parse.urlparse(url).netloc
        netloc = netloc.replace("www.", "").replace("news.", "")
        return netloc.split(".")[0] if netloc else ""
    except Exception:
        return ""


_client_singleton: NaverNewsClient | None = None


def get_client() -> NaverNewsClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = NaverNewsClient()
    return _client_singleton


def aggregate_brand_traffic(
    brands: Iterable[str],
    days: int = 30,
) -> list[dict]:
    """브랜드 리스트 → 각 브랜드의 1개월 트래픽 요약.

    반환 포맷 (Home 미디어 인텔리전스 소비용):
    [{
        "brand": "키트루다",
        "total_count": 47,
        "daily": {"2026-03-20": 3, "2026-03-21": 1, ...},
        "sparkline": [3,1,0,2,...],  // days 길이
        "latest_news": [{"title","url","source","date"}, ...5건]
    }, ...]
    """
    client = get_client()
    result = []
    cutoff = datetime.now() - timedelta(days=days)
    date_keys = [(cutoff + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

    for brand in brands:
        counts, items = client.daily_counts(brand, days=days)
        sparkline = [counts.get(d, 0) for d in date_keys]
        latest = sorted(items, key=lambda x: x.pub_date, reverse=True)[:5]
        result.append({
            "brand": brand,
            "total_count": sum(counts.values()),
            "daily": counts,
            "sparkline": sparkline,
            "latest_news": [{
                "title": n.title,
                "url": n.original_link or n.link,
                "source": n.source,
                "date": n.date_str,
                "description": n.description[:140],
            } for n in latest],
        })
    # 건수 내림차순
    result.sort(key=lambda x: x["total_count"], reverse=True)
    return result
