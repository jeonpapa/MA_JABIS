"""뉴스 아카이브(competitor_news) 에서 스콥 매칭 후보를 읽는다 — 읽기 전용.

설계 §5 ②. 스케줄러가 매일 06:30 수집한 기사를 Naver 검색 후보와 병합해,
Naver 호출 상한/429 상황에서도 후보 풀을 유지한다. pub_date 는 날짜만 있으므로
시간 창이 아니라 날짜(오늘·어제 KST)로 거른다.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from .discovery import NewsDiscoveryItem, canonicalize_url
from .source_registry import SourceRegistry

_SCAN_LIMIT = 2000


def load_archive_candidates(
    scope_terms: Iterable[str],
    *,
    db_path: str | Path,
    today: date,
    days: int = 1,
    registry: SourceRegistry | None = None,
    limit: int = 200,
) -> list[NewsDiscoveryItem]:
    terms = [t.strip() for t in scope_terms if t and t.strip()]
    path = Path(db_path)
    if not terms or not path.exists():
        return []
    since = (today - timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT title, url, naver_link, description, pub_date, source_name "
                "FROM competitor_news WHERE pub_date >= ? ORDER BY pub_date DESC, id DESC LIMIT ?",
                (since, _SCAN_LIMIT),
            ).fetchall()
    except sqlite3.Error:
        return []
    out: list[NewsDiscoveryItem] = []
    seen: set[str] = set()
    for title, url, naver_link, description, pub_date, source_name in rows:
        haystack = f"{title or ''} {description or ''}".lower()
        matched = tuple(t for t in terms if t.lower() in haystack)
        if not matched or not url:
            continue
        canonical = canonicalize_url(url)
        if canonical in seen:
            continue
        seen.add(canonical)
        source = registry.match_url(canonical) if registry else None
        out.append(NewsDiscoveryItem(
            title=str(title or ""),
            publisher_url=canonical,
            naver_url=str(naver_link or ""),
            description=str(description or ""),
            published_at=f"{pub_date}T00:00:00+09:00" if len(str(pub_date or "")) == 10 else str(pub_date or ""),
            keyword=matched[0],
            discovery_channel="news_archive",
            source_name=source.name if source else str(source_name or "Unknown publisher"),
            source_tier=source.source_tier if source else "unregistered",
            source_weight=source.weight if source else 1.0,
            ma_depth=(source.ma_depth or 0) if source else 0,
            novelty=(source.novelty or 0) if source else 0,
            volume=(source.volume or 0) if source else 0,
            matched_keywords=matched,
        ))
        if len(out) >= limit:
            break
    return out
