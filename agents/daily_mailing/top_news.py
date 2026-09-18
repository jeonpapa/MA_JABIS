"""'오늘의 주요 MA 뉴스' 일일 캐시 (설계 §5.1) — 06:40 1회 산출, 구독 공통 재사용."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .storage import DEFAULT_DB_PATH

SCHEMA = ("CREATE TABLE IF NOT EXISTS daily_mailing_top_news ("
          "run_date TEXT PRIMARY KEY, items_json TEXT NOT NULL, created_at TEXT NOT NULL)")


def load_top_ma_news(run_date: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict] | None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(SCHEMA)
        row = conn.execute("SELECT items_json FROM daily_mailing_top_news WHERE run_date=?", (run_date,)).fetchone()
    if row is None:
        return None
    items = json.loads(row[0] or "[]")
    return items if isinstance(items, list) else []


def store_top_ma_news(run_date: str, items: list[dict], *, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(SCHEMA)
        conn.execute("INSERT OR REPLACE INTO daily_mailing_top_news (run_date, items_json, created_at) VALUES (?, ?, ?)",
                     (run_date, json.dumps(items, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        conn.commit()
