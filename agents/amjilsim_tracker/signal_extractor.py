"""신선 기사 → amjilsim_media_signals 신호 추출·적재 — Access Insight S5.

흐름 (S1 백필과 동일 계약, 소스만 신선 크롤):
  TieredArticle → 약물 거명 resolve (access_insight.link.resolve_drug)
               → signal_type 분류 + lexicon 매칭 (access_insight.classify)
               → 행 구성 (session_id 최근접 예정 차수, weight, crossref_count)
               → (url, drug_id) 멱등 INSERT (access_insight.backfill.insert_signal)

원칙
----
- INSERT-only: 기존 행(S1 백필 904건 포함)은 절대 UPDATE/DELETE 하지 않는다.
- 재실행 안전: 같은 기사(같은 url)+같은 약물은 두 번 적재되지 않는다.
- source_verified 는 발췌(snippet)에 약물 거명이 있으면 'snippet_match',
  제목에만 있으면 'headline_only' (본문 WebFetch 검증은 후속 확장 —
  signal_attribution_rules.md §3).
- crossref_count = 같은 배치 내 같은 약물을 거명한 **서로 다른 매체 수**
  (attribution rules 의 dual-source 표기 근거).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

from agents.access_insight.backfill import (
    insert_signal,
    load_sessions_sorted,
    nearest_session_id,
)
from agents.access_insight.classify import classify_signal_type, signal_weight
from agents.access_insight.link import build_alias_index, resolve_drug

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

PathLike = Union[str, Path]

# 위원회 타깃 추론 표면어 — 특이도 높은(암질심) 쪽 우선.
_AMJILSIM_TOKENS = ("암질심", "중증암질환", "중증(암)질환")
_YAKPYUNGWI_TOKENS = ("약평위", "약제급여평가위")


def _committee_target(text: str) -> str:
    for tok in _AMJILSIM_TOKENS:
        if tok in text:
            return "AMJILSIM"
    for tok in _YAKPYUNGWI_TOKENS:
        if tok in text:
            return "YAKPYUNGWI"
    return "UNKNOWN"


def extract_signals(
    articles: Iterable,
    db_path: Optional[PathLike] = None,
) -> dict:
    """정규화 기사 리스트를 신호로 변환해 amjilsim_media_signals 에 멱등 적재.

    articles: TieredArticle (또는 동일 속성 outlet/url/title/published_at/
    snippet/tier/kind 를 가진 객체) 리스트. 통계 dict 반환.
    """
    path = str(db_path or DEFAULT_DB_PATH)
    index = build_alias_index(path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        expected_session = {
            r["drug_id"]: r["expected_session_id"]
            for r in conn.execute(
                "SELECT drug_id, expected_session_id FROM amjilsim_drugs"
            )
        }
        sessions_sorted = load_sessions_sorted(conn)

        stats: dict = {
            "scanned": 0,
            "matched": 0,
            "unmatched": 0,
            "inserted": 0,
            "duplicate_skipped": 0,
            "by_signal_type": {},
            "by_drug": {},
            "by_tier": {},
        }

        # ── pass 1: 약물 resolve (crossref 집계용) ──
        candidates: list[tuple] = []  # (article, drug_id)
        for art in articles:
            stats["scanned"] += 1
            text = f"{art.title or ''} {art.snippet or ''}"
            drug_id = resolve_drug(text, index)
            if drug_id is None:
                stats["unmatched"] += 1
                continue
            stats["matched"] += 1
            candidates.append((art, drug_id))

        # crossref_count: 같은 약물을 거명한 서로 다른 매체 수 (배치 내)
        outlets_by_drug: dict[int, set[str]] = {}
        for art, drug_id in candidates:
            outlets_by_drug.setdefault(drug_id, set()).add(art.outlet or "unknown")

        # ── pass 2: 분류 + 행 구성 + 멱등 INSERT ──
        for art, drug_id in candidates:
            title = art.title or ""
            snippet = art.snippet or ""
            kind = getattr(art, "kind", "fresh_crawl") or "fresh_crawl"
            tier = (getattr(art, "tier", "D") or "D").upper()

            signal_type, phrases = classify_signal_type(title, snippet, kind)
            weight = signal_weight(tier, signal_type)
            pub_date = art.published_at.strftime("%Y-%m-%d") if art.published_at else None

            session_id = expected_session.get(drug_id)
            if not session_id and pub_date:
                session_id = nearest_session_id(sessions_sorted, pub_date)

            # 발췌에 약물 거명 → snippet_match, 제목에만 → headline_only
            source_verified = (
                "snippet_match"
                if snippet and resolve_drug(snippet, index) == drug_id
                else "headline_only"
            )

            inserted = insert_signal(
                conn,
                drug_id=drug_id,
                session_id=session_id,
                tier=tier,
                outlet=art.outlet or "unknown",
                url=art.url,
                title=title,
                published_at=pub_date,
                snippet=snippet,
                signal_type=signal_type,
                signal_phrases=phrases,
                crossref_count=len(outlets_by_drug.get(drug_id, set())),
                weight=weight,
                source_verified=source_verified,
                committee_target=_committee_target(f"{title} {snippet}"),
            )
            if not inserted:
                stats["duplicate_skipped"] += 1
                continue
            stats["inserted"] += 1
            stats["by_signal_type"][signal_type] = stats["by_signal_type"].get(signal_type, 0) + 1
            stats["by_drug"][drug_id] = stats["by_drug"].get(drug_id, 0) + 1
            stats["by_tier"][tier] = stats["by_tier"].get(tier, 0) + 1

        conn.commit()
    finally:
        conn.close()
    return stats


def run_fresh_crawl(
    lookback_days: Optional[int] = None,
    db_path: Optional[PathLike] = None,
    keywords: Optional[Sequence[str]] = None,
    tiers: Sequence[str] = ("A", "B", "D"),
    **fetchers,
) -> dict:
    """신선 신호 파이프라인 1회 실행: tier 크롤(1-pass) → 신호 추출·적재.

    스케줄러 잡(access_insight_fresh_signals)의 실행부. Naver 축은 키워드당
    1회만 호출하고 A/B/D 로 버킷 분류하므로 tier 별 재검색이 없다. 멱등 —
    재실행해도 (url, drug_id) 중복 적재 없음.
    """
    from agents.amjilsim_tracker.crawlers.tiered_news import (
        DEFAULT_LOOKBACK_DAYS,
        default_keywords,
        fetch_articles,
    )

    if lookback_days is None:
        lookback_days = DEFAULT_LOOKBACK_DAYS
    if keywords is None:
        keywords = default_keywords(db_path)

    wanted = {t.upper() for t in tiers}
    articles = [
        a for a in fetch_articles(keywords, lookback_days, **fetchers)
        if a.tier in wanted
    ]
    stats = extract_signals(articles, db_path=db_path)
    stats["articles"] = len(articles)
    stats["keywords"] = len(keywords)
    stats["lookback_days"] = lookback_days
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_fresh_crawl(), ensure_ascii=False, indent=2, default=str))
