"""'오늘의 주요 MA 뉴스' 일일 캐시 (설계 §5.1) — 06:40 1회 산출, 구독 공통 재사용.

생성 실패도 그날의 negative 마커(status='failed', items=[])로 캐시한다 — 캐시가 없으면 10분 tick 마다
모든 구독 생성이 엔진(LLM·Naver)을 다시 돌린다. 하루 최대 1회 시도 원칙, 명시적 재시도(retry_failed)만 예외.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .storage import DEFAULT_DB_PATH, connect

TOP_NEWS_OK = "ok"
TOP_NEWS_FAILED = "failed"

SCHEMA = ("CREATE TABLE IF NOT EXISTS daily_mailing_top_news ("
          "run_date TEXT PRIMARY KEY, items_json TEXT NOT NULL, created_at TEXT NOT NULL, "
          "status TEXT NOT NULL DEFAULT 'ok', error TEXT)")


def _ensure(conn) -> None:
    conn.execute(SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_mailing_top_news)").fetchall()}
    if "status" not in existing:   # status/error 컬럼 도입 전 테이블
        conn.execute("ALTER TABLE daily_mailing_top_news ADD COLUMN status TEXT NOT NULL DEFAULT 'ok'")
    if "error" not in existing:
        conn.execute("ALTER TABLE daily_mailing_top_news ADD COLUMN error TEXT")


def load_top_ma_news_entry(run_date: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    """{"items": list, "status": ok|failed, "error": str|None, "created_at": str} — 캐시 없으면 None."""
    with connect(db_path) as conn:
        _ensure(conn)
        row = conn.execute("SELECT items_json, status, error, created_at FROM daily_mailing_top_news WHERE run_date=?",
                           (run_date,)).fetchone()
    if row is None:
        return None
    try:
        items = json.loads(row[0] or "[]")
    except ValueError:
        items = []
    return {"items": items if isinstance(items, list) else [], "status": row[1] or TOP_NEWS_OK,
            "error": row[2], "created_at": row[3]}


def load_top_ma_news(run_date: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict] | None:
    """캐시된 항목(실패 마커면 빈 목록). 캐시 자체가 없으면 None."""
    entry = load_top_ma_news_entry(run_date, db_path=db_path)
    return None if entry is None else entry["items"]


def store_top_ma_news(run_date: str, items: list[dict], *, db_path: str | Path = DEFAULT_DB_PATH,
                      status: str = TOP_NEWS_OK, error: str | None = None) -> None:
    with connect(db_path) as conn:
        _ensure(conn)
        conn.execute("INSERT OR REPLACE INTO daily_mailing_top_news (run_date, items_json, created_at, status, error) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (run_date, json.dumps(items, ensure_ascii=False), datetime.now(timezone.utc).isoformat(),
                      status, error))
        conn.commit()


def mark_top_ma_news_failed(run_date: str, error: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """생성 실패 negative 마커 — 그날은 빈 목록으로 진행하고 엔진을 다시 돌리지 않는다."""
    store_top_ma_news(run_date, [], db_path=db_path, status=TOP_NEWS_FAILED, error=error[:500])
