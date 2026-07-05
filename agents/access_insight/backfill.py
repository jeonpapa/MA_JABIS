"""기존 competitor_news 아카이브 → amjilsim_media_signals 백필 — Access Insight S1.

READ-ONLY: competitor_news / amjilsim_drugs / product_alias_map / amjilsim_sessions.
WRITE: amjilsim_media_signals 만 (INSERT, 기존 행 변경 없음).

멱등: (url, drug_id) 조합이 이미 있으면 skip (check-before-insert).
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

# competitor_news.tier(INTEGER, 1/2/3) → amjilsim_media_signals.tier(TEXT, A/B/C/D).
# 1=Tier1 전문지, 2=Tier2, 3=미분류 도메인(사실상 미등록) → 'D'.
_TIER_LETTER = {1: "A", 2: "B", 3: "D"}

DEFAULT_KINDS: tuple[str, ...] = ("competitor", "gov_policy", "msd_asset")


def _connect(db_path: PathLike) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _nearest_session_id(sessions_sorted: list[tuple[str, int]], pub_date: str) -> Optional[int]:
    """pub_date 이후(>=) 가장 이른 session_id. 위원회 구분 없이 전체 중 최근접."""
    for session_date, session_id in sessions_sorted:
        if session_date and session_date >= pub_date:
            return session_id
    return None


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

        session_rows = conn.execute(
            "SELECT session_id, session_date FROM amjilsim_sessions ORDER BY session_date ASC"
        ).fetchall()
        sessions_sorted = [(r["session_date"], r["session_id"]) for r in session_rows]

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
            tier_letter = _TIER_LETTER.get(row["tier"], "D")
            weight = signal_weight(tier_letter, signal_type)
            outlet = row["source_name"] or row["source_domain"] or "unknown"
            pub_date = row["pub_date"]

            session_id = expected_session.get(drug_id)
            if not session_id and pub_date:
                session_id = _nearest_session_id(sessions_sorted, pub_date)

            existing = conn.execute(
                "SELECT 1 FROM amjilsim_media_signals WHERE url = ? AND drug_id = ? LIMIT 1",
                (row["url"], drug_id),
            ).fetchone()
            if existing:
                stats["duplicate_skipped"] += 1
                continue

            conn.execute(
                """
                INSERT INTO amjilsim_media_signals (
                    drug_id, session_id, tier, outlet, url, title, published_at,
                    snippet, signal_type, signal_phrases, crossref_count, weight,
                    crawled_at, source_verified
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    drug_id,
                    session_id,
                    tier_letter,
                    outlet,
                    row["url"],
                    title,
                    pub_date,
                    snippet,
                    signal_type,
                    json.dumps(phrases, ensure_ascii=False),
                    0,
                    weight,
                    _now_iso(),
                    "snippet_match",
                ),
            )
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
