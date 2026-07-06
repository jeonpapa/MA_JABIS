"""Tier 기반 신선 신호 기사 수집 엔진 — Access Insight S5.

tier 매핑 (amjilsim_media_signals.tier CHECK ('A','B','D','G') 기준)
------------------------------------------------------------------
  A = 제약·의료 전문지        (config/media_tiers.json tier1 도메인)
  B = 종합일간·경제·통신      (media_tiers.json tier2 도메인)
  D = 미등록/미분류 매체      (tier 미매핑 도메인 = competitor_news tier 3)
  G = 정부·공식 소스 (HIRA/복지부 보도자료) — 미디어 크롤 대상 아님.
      official-source 파이프라인(reimb_data_sync 등) 소관, 본 모듈 범위 밖.

설계
----
- `base_amjilsim_crawler.BaseAmjilsimCrawler` 는 매체별 Playwright 크롤러용
  추상 베이스지만, S5 신선 신호 경로는 **검증된 기존 fetcher 를 재사용**한다
  (신규 HTTP 스크레이퍼 발명 금지 — S1 아카이브와 동일 소스 패밀리 유지):
    ① Naver News API (`agents.naver_news.NaverNewsClient`) — 전 매체 검색 후
       도메인 tier 로 A/B/D 버킷 분류 (`competitor_news_agent.classify_tier`).
    ② Tier-1 전문지 직접 검색 (`agents.scrapers.tier1_news_sites`) —
       Naver 미인덱싱 갭필러 (뉴스더보이스 등). 항상 tier A.
- 결과 schema 는 base 의 `Article` 관례를 따르되 (outlet/url/title/
  published_at/snippet/extra) tier 필드를 추가. Playwright import 를 피하려고
  직접 상속하지 않는다 (스케줄러 경로 경량 유지).
- rate limit·에러 격리는 위임한 fetcher 가 이미 보장 (NaverNewsClient
  min_delay_ms, tier1_news_sites SITE_DELAY_S + 사이트별 try/except). 본 모듈은
  키워드 단위 try/except 로 한 키워드 실패가 전체 수집을 막지 않게 한다.
- dedupe: canonical URL (`competitor_news_agent._canonical_url` 재사용) —
  Naver ↔ 사이트 직접검색 중복, 키워드 간 중복 제거.
- 신호 추출(약물 거명·signal_type·lexicon)은 `signal_extractor.py` 책임.
  본 모듈은 정규화된 raw article 만 반환한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from agents import competitor_news_agent as _cn
from agents.access_insight.backfill import TIER_LETTER

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

PathLike = Union[str, Path]

VALID_TIERS = ("A", "B", "D")

DEFAULT_LOOKBACK_DAYS = 8  # 주 1회 크롤 + 1일 오버랩 (멱등 INSERT 라 중복 무해)

# 위원회 일반 키워드 — 약제명 없이도 위원회 기사에서 약물 거명을 잡는 보조축.
COMMITTEE_KEYWORDS = ("암질심", "약제급여평가위원회")


@dataclass
class TieredArticle:
    """정규화된 신선 신호 기사 (base_amjilsim_crawler.Article 관례 + tier)."""

    outlet: str                              # 매체명 (미등록 매체는 도메인)
    url: str                                 # publisher 원문 절대 URL
    title: str
    published_at: Optional[datetime] = None
    snippet: str = ""                        # 검색 발췌 (본문 아님)
    tier: str = "D"                          # 'A' | 'B' | 'D'
    kind: str = "fresh_crawl"                # classify_signal_type 의 kind 인자
    extra: dict = field(default_factory=dict)

    @property
    def date_str(self) -> Optional[str]:
        return self.published_at.strftime("%Y-%m-%d") if self.published_at else None


# ── 키워드 ───────────────────────────────────────────────────────────────────

def default_keywords(db_path: Optional[PathLike] = None) -> list[str]:
    """amjilsim_drugs 추적 약제(brand_kr clean 후보) + 위원회 일반 키워드.

    brand_kr 원본('베오바정 50mg 외 1품목' 등)을 그대로 검색어로 쓰지 않고
    S1 link.py 의 clean 로직을 재사용해 노이즈를 제거한다.
    """
    import sqlite3

    from agents.access_insight.link import _clean_brand_candidates

    path = str(db_path or DEFAULT_DB_PATH)
    keywords: list[str] = []
    seen: set[str] = set()

    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT brand_kr FROM amjilsim_drugs ORDER BY drug_id").fetchall()
    finally:
        conn.close()

    for (brand_kr,) in rows:
        cands = _clean_brand_candidates(brand_kr or "")
        if not cands:
            continue
        kw = cands[0]  # 첫 후보 = 원형에 가장 가까운 표기
        if kw not in seen:
            seen.add(kw)
            keywords.append(kw)

    for kw in COMMITTEE_KEYWORDS:
        if kw not in seen:
            seen.add(kw)
            keywords.append(kw)
    return keywords


# ── 기본 fetcher (운영 경로 — 테스트에서는 주입으로 대체) ────────────────────

def _default_naver_fetch(keyword: str, lookback_days: int):
    """Naver News API 수집 — competitor_news_agent 의 페이지네이션 로직 재사용."""
    from agents.naver_news import NaverNewsClient

    client = _default_naver_fetch._client  # type: ignore[attr-defined]
    if client is None:
        client = NaverNewsClient()
        _default_naver_fetch._client = client  # type: ignore[attr-defined]
    if not client.is_configured:
        logger.warning("[tiered_news] Naver API 키 미설정 — Naver 축 skip")
        return []
    return _cn._fetch_brand(client, keyword, lookback_days)


_default_naver_fetch._client = None  # type: ignore[attr-defined]


def _default_site_search(keyword: str, lookback_days: int):
    """T1 전문지 직접 검색 (키 불필요) — tier1_news_sites 재사용."""
    from agents.scrapers import tier1_news_sites

    return tier1_news_sites.search_all_sites(keyword, lookback_days)


# ── 수집 ─────────────────────────────────────────────────────────────────────

def fetch_articles(
    keywords: Sequence[str],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    *,
    naver_fetch: Optional[Callable] = None,
    site_search: Optional[Callable] = None,
    tier_of: Optional[Callable] = None,
) -> list[TieredArticle]:
    """전체 tier(A/B/D) 기사를 1-pass 로 수집·정규화·dedupe.

    Naver 축은 키워드당 1회만 호출하고 도메인 tier 로 버킷 분류 —
    tier 별 크롤러가 각자 재검색해 API quota 를 3배 소모하지 않게 한다.

    naver_fetch(keyword, lookback_days) -> list[NewsItem]      (주입 가능)
    site_search(keyword, lookback_days) -> list[SiteNewsItem]  (주입 가능)
    tier_of(url) -> (tier_num|None, source_name|None)          (주입 가능)
    """
    naver_fetch = naver_fetch or _default_naver_fetch
    site_search = site_search or _default_site_search
    tier_of = tier_of or _cn.classify_tier

    out: list[TieredArticle] = []
    seen: set[str] = set()

    def _add(article: TieredArticle) -> None:
        key = _cn._canonical_url(article.url)
        if not key or key in seen:
            return
        seen.add(key)
        out.append(article)

    for kw in keywords:
        # ── 축 1: Naver News API (전 매체 → 도메인 tier 분류) ──
        try:
            items = naver_fetch(kw, lookback_days) or []
        except Exception as e:
            logger.warning("[tiered_news] '%s' Naver 축 실패: %s", kw, e)
            items = []
        for it in items:
            if not _cn._is_relevant(it, kw):
                continue  # 표면(제목+발췌)에 키워드 없음 — 무관 기사
            url = it.original_link or it.link
            tier_num, source_name = tier_of(url)
            if tier_num is None:
                continue
            _add(TieredArticle(
                outlet=source_name or _cn._domain(url) or "unknown",
                url=url,
                title=it.title,
                published_at=it.pub_date,
                snippet=it.description,
                tier=TIER_LETTER.get(tier_num, "D"),
                extra={"keyword": kw, "collected_via": "naver"},
            ))

        # ── 축 2: T1 전문지 직접 검색 (Naver 미인덱싱 갭필러 — 항상 tier A) ──
        try:
            site_items = site_search(kw, lookback_days) or []
        except Exception as e:
            logger.warning("[tiered_news] '%s' 사이트 축 실패: %s", kw, e)
            site_items = []
        for si in site_items:
            if kw not in f"{si.title} {si.description}":
                continue
            _add(TieredArticle(
                outlet=si.source_name,
                url=si.url,
                title=si.title,
                published_at=si.pub_date,
                snippet=si.description,
                tier="A",
                extra={"keyword": kw, "collected_via": "site"},
            ))

    return out


def crawl_tier(
    tier: str,
    keywords: Optional[Sequence[str]] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    db_path: Optional[PathLike] = None,
    **fetchers,
) -> list[TieredArticle]:
    """단일 tier('A'/'B'/'D') 기사만 반환 — tier 패키지(tier_a|b|d)의 실행부.

    주의: 운영 배치는 tier 별 개별 호출 대신 fetch_articles 1-pass 를 쓴다
    (signal_extractor.run_fresh_crawl). 이 함수는 단독 실행·테스트용.
    """
    tier = (tier or "").upper()
    if tier not in VALID_TIERS:
        raise ValueError(f"tier 는 {VALID_TIERS} 중 하나여야 함: {tier!r}")
    if keywords is None:
        keywords = default_keywords(db_path)
    articles = fetch_articles(keywords, lookback_days, **fetchers)
    return [a for a in articles if a.tier == tier]
