"""기존 competitor_news 아카이브 → amjilsim_media_signals 백필 — Access Insight S1.

READ-ONLY: competitor_news / amjilsim_drugs / product_alias_map / amjilsim_sessions.
WRITE: amjilsim_media_signals 만 (INSERT, 기존 행 변경 없음).

멱등: (url, drug_id) 조합이 이미 있으면 skip (check-before-insert).

S5 (신선 신호 크롤러) 공유 계약: `insert_signal` / `nearest_session_id` /
`load_sessions_sorted` / `TIER_LETTER` 는 `agents/amjilsim_tracker/signal_extractor.py`
가 재사용하는 공용 헬퍼 — 백필과 신선 경로가 동일한 (url, drug_id) 멱등 INSERT
계약을 유지하도록 여기 한 곳에만 둔다 (복붙 금지).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from .classify import classify_signal_type, signal_weight
from .link import DEFAULT_DB_PATH, build_alias_index, resolve_drug

PathLike = Union[str, Path]

# competitor_news.tier(INTEGER, 1/2/3) → amjilsim_media_signals.tier(TEXT, A/B/D).
# 1=Tier1 전문지, 2=Tier2 종합·경제·통신, 3=미분류 도메인(사실상 미등록) → 'D'.
TIER_LETTER = {1: "A", 2: "B", 3: "D"}
_TIER_LETTER = TIER_LETTER  # 하위호환 별칭

DEFAULT_KINDS: tuple[str, ...] = ("competitor", "gov_policy", "msd_asset")


def _connect(db_path: PathLike) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def nearest_session_id(sessions_sorted: list[tuple[str, int]], pub_date: str) -> Optional[int]:
    """pub_date 이후(>=) 가장 이른 session_id. 위원회 구분 없이 전체 중 최근접."""
    for session_date, session_id in sessions_sorted:
        if session_date and session_date >= pub_date:
            return session_id
    return None


_nearest_session_id = nearest_session_id  # 하위호환 별칭


def load_sessions_sorted(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """amjilsim_sessions 를 (session_date, session_id) 오름차순 리스트로 로드."""
    rows = conn.execute(
        "SELECT session_id, session_date FROM amjilsim_sessions ORDER BY session_date ASC"
    ).fetchall()
    return [(r["session_date"], r["session_id"]) for r in rows]


def insert_signal(
    conn: sqlite3.Connection,
    *,
    drug_id: int,
    session_id: Optional[int],
    tier: str,
    outlet: str,
    url: str,
    title: str,
    published_at: Optional[str],
    snippet: str,
    signal_type: str,
    signal_phrases: list[str],
    crossref_count: int = 0,
    weight: float = 1.0,
    source_verified: str = "snippet_match",
    committee_target: str = "UNKNOWN",
) -> bool:
    """amjilsim_media_signals 에 1행 INSERT. (url, drug_id) 멱등 — 이미 있으면 False.

    커밋은 호출자 책임. 기존 행은 절대 UPDATE/DELETE 하지 않는다 (INSERT-only).
    스키마의 UNIQUE(outlet, url) 충돌(예: alias 인덱스 변경으로 같은 기사가 다른
    drug 으로 재해석된 경우)도 duplicate 로 흡수해 기존 행을 보호한다.
    """
    existing = conn.execute(
        "SELECT 1 FROM amjilsim_media_signals WHERE url = ? AND drug_id = ? LIMIT 1",
        (url, drug_id),
    ).fetchone()
    if existing:
        return False
    try:
        conn.execute(
            """
            INSERT INTO amjilsim_media_signals (
                drug_id, session_id, tier, outlet, url, title, published_at,
                snippet, signal_type, signal_phrases, crossref_count, weight,
                crawled_at, source_verified, committee_target
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                drug_id,
                session_id,
                tier,
                outlet,
                url,
                title,
                published_at,
                snippet,
                signal_type,
                json.dumps(signal_phrases, ensure_ascii=False),
                crossref_count,
                weight,
                _now_iso(),
                source_verified,
                committee_target,
            ),
        )
    except sqlite3.IntegrityError:
        return False
    return True


def backfill_signals(
    db_path: Optional[PathLike] = None,
    kinds: tuple[str, ...] = DEFAULT_KINDS,
    limit: Optional[int] = None,
) -> dict:
    """competitor_news 를 스캔해 amjilsim_media_signals 로 백필. 통계 dict 반환."""
    path = str(db_path or DEFAULT_DB_PATH)
    index = build_alias_index(path)

    conn = _connect(path)
    try:
        drug_rows = conn.execute(
            "SELECT drug_id, brand_kr, expected_session_id FROM amjilsim_drugs"
        ).fetchall()
        expected_session = {r["drug_id"]: r["expected_session_id"] for r in drug_rows}
        drug_names = {r["drug_id"]: r["brand_kr"] for r in drug_rows}

        sessions_sorted = load_sessions_sorted(conn)

        placeholders = ",".join("?" for _ in kinds)
        query = (
            f"SELECT id, brand, kind, title, url, source_name, source_domain, tier, "
            f"description, pub_date FROM competitor_news WHERE kind IN ({placeholders}) ORDER BY id"
        )
        params: tuple = tuple(kinds)
        if limit:
            query += " LIMIT ?"
            params = params + (limit,)
        rows = conn.execute(query, params).fetchall()

        stats: dict = {
            "scanned": 0,
            "matched": 0,
            "inserted": 0,
            "unmatched": 0,
            "duplicate_skipped": 0,
            "by_signal_type": {},
            "by_drug": {},
        }

        for row in rows:
            stats["scanned"] += 1
            title = row["title"] or ""
            snippet = row["description"] or ""
            brand = row["brand"] or ""
            kind = row["kind"] or ""
            text = f"{title} {snippet} {brand}"

            drug_id = resolve_drug(text, index)
            if drug_id is None:
                stats["unmatched"] += 1
                continue
            stats["matched"] += 1

            signal_type, phrases = classify_signal_type(title, snippet, kind)
            tier_letter = TIER_LETTER.get(row["tier"], "D")
            weight = signal_weight(tier_letter, signal_type)
            outlet = row["source_name"] or row["source_domain"] or "unknown"
            pub_date = row["pub_date"]

            session_id = expected_session.get(drug_id)
            if not session_id and pub_date:
                session_id = nearest_session_id(sessions_sorted, pub_date)

            inserted = insert_signal(
                conn,
                drug_id=drug_id,
                session_id=session_id,
                tier=tier_letter,
                outlet=outlet,
                url=row["url"],
                title=title,
                published_at=pub_date,
                snippet=snippet,
                signal_type=signal_type,
                signal_phrases=phrases,
                crossref_count=0,
                weight=weight,
                source_verified="snippet_match",
            )
            if not inserted:
                stats["duplicate_skipped"] += 1
                continue
            stats["inserted"] += 1
            stats["by_signal_type"][signal_type] = stats["by_signal_type"].get(signal_type, 0) + 1
            stats["by_drug"][drug_id] = stats["by_drug"].get(drug_id, 0) + 1

        conn.commit()
    finally:
        conn.close()

    top_drugs = sorted(stats["by_drug"].items(), key=lambda kv: -kv[1])[:15]
    stats["by_drug_top"] = [
        {"drug_id": drug_id, "brand_kr": drug_names.get(drug_id), "count": count}
        for drug_id, count in top_drugs
    ]
    return stats


if __name__ == "__main__":
    result = backfill_signals()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
