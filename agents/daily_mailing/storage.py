from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .review_board import build_article_card

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"
RETENTION_DAYS = 183  # 약 6개월

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_mailing_run (
    run_id          TEXT PRIMARY KEY,
    generated_at    TEXT NOT NULL,
    window_label    TEXT,
    lookback_hours  INTEGER NOT NULL DEFAULT 24,
    keywords_json   TEXT NOT NULL,
    media_json      TEXT NOT NULL DEFAULT '[]',
    subscription_id INTEGER,
    owner_email     TEXT,
    recipients_json TEXT NOT NULL DEFAULT '[]',
    delivery_status TEXT NOT NULL DEFAULT 'draft_only',
    approval_status TEXT NOT NULL DEFAULT 'not_requested',
    gmail_draft_id  TEXT,
    gmail_message_id TEXT,
    sent_at         TEXT,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    recent_count    INTEGER NOT NULL DEFAULT 0,
    selected_count  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    markdown_path   TEXT,
    html_path       TEXT,
    json_path       TEXT,
    review_board_path TEXT,
    expires_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_mailing_article (
    article_id      TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    title           TEXT NOT NULL,
    publisher_url   TEXT,
    naver_url       TEXT,
    source_name     TEXT,
    source_tier     TEXT,
    source_status   TEXT,
    priority        TEXT,
    ma_relevance    INTEGER,
    review_status   TEXT,
    quality_flags_json TEXT NOT NULL,
    selected_for_draft INTEGER NOT NULL DEFAULT 0,
    score           REAL NOT NULL DEFAULT 0,
    published_at    TEXT,
    matched_keywords_json TEXT NOT NULL,
    keyword         TEXT,
    verification_caveat TEXT,
    expires_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (run_id, article_id)
);

CREATE INDEX IF NOT EXISTS idx_daily_mailing_run_generated ON daily_mailing_run(generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_daily_mailing_run_expires ON daily_mailing_run(expires_at);
CREATE INDEX IF NOT EXISTS idx_daily_mailing_article_run ON daily_mailing_article(run_id);
CREATE INDEX IF NOT EXISTS idx_daily_mailing_article_expires ON daily_mailing_article(expires_at);
CREATE INDEX IF NOT EXISTS idx_daily_mailing_article_status ON daily_mailing_article(review_status, selected_for_draft);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_daily_mailing_tables(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_SQL)
        # Lightweight migrations for DBs created before Daily Mailing delivery fields.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_mailing_run)").fetchall()}
        for col, ddl in {
            "media_json": "ALTER TABLE daily_mailing_run ADD COLUMN media_json TEXT NOT NULL DEFAULT '[]'",
            "subscription_id": "ALTER TABLE daily_mailing_run ADD COLUMN subscription_id INTEGER",
            "owner_email": "ALTER TABLE daily_mailing_run ADD COLUMN owner_email TEXT",
            "recipients_json": "ALTER TABLE daily_mailing_run ADD COLUMN recipients_json TEXT NOT NULL DEFAULT '[]'",
            "delivery_status": "ALTER TABLE daily_mailing_run ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'draft_only'",
            "approval_status": "ALTER TABLE daily_mailing_run ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'not_requested'",
            "gmail_draft_id": "ALTER TABLE daily_mailing_run ADD COLUMN gmail_draft_id TEXT",
            "gmail_message_id": "ALTER TABLE daily_mailing_run ADD COLUMN gmail_message_id TEXT",
            "sent_at": "ALTER TABLE daily_mailing_run ADD COLUMN sent_at TEXT",
        }.items():
            if col not in existing:
                conn.execute(ddl)
        conn.commit()


def purge_expired_daily_mailing_rows(db_path: str | Path = DEFAULT_DB_PATH, *, now: datetime | None = None) -> dict:
    ensure_daily_mailing_tables(db_path)
    now = now or _utc_now()
    cutoff = now.isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        article_deleted = conn.execute(
            "DELETE FROM daily_mailing_article WHERE expires_at < ?", (cutoff,)
        ).rowcount
        run_deleted = conn.execute(
            "DELETE FROM daily_mailing_run WHERE expires_at < ?", (cutoff,)
        ).rowcount
        conn.commit()
    return {"runs_deleted": run_deleted, "articles_deleted": article_deleted, "cutoff": cutoff}


def persist_daily_mailing_run(
    payload: dict,
    *,
    articles: Iterable,
    markdown_path: str | None = None,
    html_path: str | None = None,
    json_path: str | None = None,
    review_board_path: str | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    retention_days: int = RETENTION_DAYS,
) -> dict:
    """Persist a daily mailing run and article cards for cumulative 6-month history.

    The admin board consumes this store. It is deliberately operational-state storage,
    not an article-by-article approval workflow.
    """
    ensure_daily_mailing_tables(db_path)
    now = _utc_now()
    expires_at = (now + timedelta(days=retention_days)).isoformat()
    run_id = str(payload.get("run_id") or now.strftime("%Y%m%d_%H%M%S"))
    generated_at = str(payload.get("generated_at") or now.isoformat())
    article_cards = []
    for item in articles:
        if isinstance(item, dict) and "article_id" in item:
            article_cards.append(dict(item))
        else:
            article_cards.append(build_article_card(item, selected_for_draft=True))

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_mailing_run (
                run_id, generated_at, window_label, lookback_hours, keywords_json, media_json,
                subscription_id, owner_email, recipients_json, delivery_status, approval_status,
                discovered_count, recent_count, selected_count, status,
                markdown_path, html_path, json_path, review_board_path, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                generated_at,
                payload.get("window_label"),
                int(payload.get("lookback_hours") or 24),
                json.dumps(payload.get("keywords") or [], ensure_ascii=False),
                json.dumps(payload.get("media") or [], ensure_ascii=False),
                payload.get("subscription_id"),
                payload.get("owner_email"),
                json.dumps(payload.get("recipients") or [], ensure_ascii=False),
                payload.get("delivery_status") or "draft_only",
                payload.get("approval_status") or "not_requested",
                int(payload.get("discovered_count") or 0),
                int(payload.get("recent_count") or 0),
                int(payload.get("selected_count") or len(article_cards)),
                payload.get("status") or "quality_gated_draft",
                markdown_path,
                html_path,
                json_path,
                review_board_path,
                expires_at,
                now.isoformat(),
            ),
        )
        for card in article_cards:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_mailing_article (
                    article_id, run_id, title, publisher_url, naver_url, source_name, source_tier,
                    source_status, priority, ma_relevance, review_status, quality_flags_json,
                    selected_for_draft, score, published_at, matched_keywords_json, keyword,
                    verification_caveat, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card.get("article_id"),
                    run_id,
                    card.get("title") or "",
                    card.get("publisher_url"),
                    card.get("naver_url"),
                    card.get("source_name"),
                    card.get("source_tier"),
                    card.get("source_status"),
                    card.get("priority"),
                    card.get("ma_relevance"),
                    card.get("review_status"),
                    json.dumps(card.get("quality_flags") or [], ensure_ascii=False),
                    1 if card.get("selected_for_draft") else 0,
                    float(card.get("score") or 0),
                    card.get("published_at"),
                    json.dumps(card.get("matched_keywords") or [], ensure_ascii=False),
                    card.get("keyword"),
                    card.get("verification_caveat"),
                    expires_at,
                    now.isoformat(),
                ),
            )
        conn.commit()
    purge_expired_daily_mailing_rows(db_path, now=now)
    return {"run_id": run_id, "article_count": len(article_cards), "expires_at": expires_at}


def load_admin_kanban(db_path: str | Path = DEFAULT_DB_PATH, *, limit_runs: int = 20) -> dict:
    ensure_daily_mailing_tables(db_path)
    purge_expired_daily_mailing_rows(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        runs = conn.execute(
            "SELECT * FROM daily_mailing_run ORDER BY generated_at DESC LIMIT ?", (limit_runs,)
        ).fetchall()
        run_ids = [r["run_id"] for r in runs]
        articles: list[sqlite3.Row] = []
        if run_ids:
            ph = ",".join("?" for _ in run_ids)
            articles = conn.execute(
                f"SELECT * FROM daily_mailing_article WHERE run_id IN ({ph}) ORDER BY selected_for_draft DESC, score DESC",
                run_ids,
            ).fetchall()

    lane_names = ["Dashboard Scope", "Source Intake", "Triage/Verify", "Writer Agent", "Delivery/History"]
    lanes = {name: [] for name in lane_names}
    run_by_id = {r["run_id"]: dict(r) for r in runs}
    for row in articles:
        item = dict(row)
        item["quality_flags"] = json.loads(item.pop("quality_flags_json") or "[]")
        item["matched_keywords"] = json.loads(item.pop("matched_keywords_json") or "[]")
        run = run_by_id.get(item["run_id"], {})
        item["generated_at"] = run.get("generated_at")
        item["html_path"] = run.get("html_path")
        if item.get("review_status") == "excluded":
            lane = "Source Intake"
        elif item.get("selected_for_draft"):
            lane = "Writer Agent"
        elif item.get("source_status") in {"publisher_verified", "official_verified"}:
            lane = "Triage/Verify"
        else:
            lane = "Source Intake"
        lanes.setdefault(lane, []).append(item)
    return {
        "status": "admin_operational_board",
        "retention_days": RETENTION_DAYS,
        "article_approval_required": False,
        "lanes": [{"name": name, "items": lanes.get(name, [])} for name in lane_names],
        "runs": [dict(r) for r in runs],
    }
