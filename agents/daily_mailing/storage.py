from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .personas import (
    PERSONAS,
    REVIEWER_ROLES,
    persona_to_dict,
    resolve_personas,
    resolve_reviewer_roles,
    reviewer_role_to_dict,
)
from .review_board import DEFAULT_OPERATING_POLICY, KANBAN_LANES, build_article_card
from .writer import infer_ma_implication, infer_news_insight

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"
RETENTION_DAYS = 183  # 약 6개월

# 이메일 브리프 렌더러(writer._render_html)와 동일한 monitoring-only fallback 문구.
_MA_IMPLICATION_FALLBACK = (
    "현재 기사만으로는 별도 MA implication을 쓰지 않습니다. "
    "Dashboard monitoring/watch 관점에서만 추적합니다."
)


def _enrich_draft_items(items) -> list[dict]:
    """payload.items 는 monitoring_point/work_note 를 저장하지 않음(writer 가 렌더 시 계산).

    이메일 브리프가 보여주는 핵심 인사이트라 여기서 미리 계산해 draft_items 에 심는다.
    writer 실패가 적재를 막지 않도록 방어적으로 감싼다.
    """
    enriched: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            enriched.append(item)
            continue
        out = dict(item)
        try:
            out["monitoring_point"] = infer_news_insight(item)
            out["work_note"] = infer_ma_implication(item) or _MA_IMPLICATION_FALLBACK
        except Exception:
            pass
        enriched.append(out)
    return enriched

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
    quality_report_json TEXT NOT NULL DEFAULT 'null',
    personas_json   TEXT NOT NULL DEFAULT '[]',
    reviewer_roles_json TEXT NOT NULL DEFAULT '[]',
    operating_policy_json TEXT NOT NULL DEFAULT 'null',
    counts_json     TEXT NOT NULL DEFAULT 'null',
    draft_items_json TEXT NOT NULL DEFAULT '[]',
    dashboard_scope_json TEXT NOT NULL DEFAULT 'null',
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
    tracking_lane   TEXT,
    reviewer_findings_json TEXT NOT NULL DEFAULT '[]',
    next_action     TEXT,
    tracker_tags_json TEXT NOT NULL DEFAULT '[]',
    verification_method TEXT,
    official_url    TEXT,
    content_completeness_json TEXT NOT NULL DEFAULT 'null',
    persona_ids_json TEXT NOT NULL DEFAULT '[]',
    reviewer_note   TEXT,
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


def _json_or(raw, default):
    """Parse a stored *_json column, returning ``default`` on null/empty/invalid."""
    if raw is None or raw == "":
        return default
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return default if parsed is None else parsed


def _normalize_personas(entries) -> list[dict]:
    """Accept persona dicts or persona_id strings (헤르메스 payload는 id 리스트) → dict 리스트."""
    out: list[dict] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            out.append(entry)
        elif isinstance(entry, str) and entry in PERSONAS:
            out.append(persona_to_dict(PERSONAS[entry]))
    return out


def _normalize_reviewer_roles(entries) -> list[dict]:
    out: list[dict] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            out.append(entry)
        elif isinstance(entry, str) and entry in REVIEWER_ROLES:
            out.append(reviewer_role_to_dict(REVIEWER_ROLES[entry]))
    return out


def _default_personas() -> list[dict]:
    return [persona_to_dict(p) for p in resolve_personas(None)]


def _default_reviewer_roles() -> list[dict]:
    return [reviewer_role_to_dict(r) for r in resolve_reviewer_roles(None)]


def _normalize_findings(value) -> list[dict]:
    """reviewer_findings 는 단일 dict 또는 배열로 올 수 있음 → 항상 배열."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [f for f in value if isinstance(f, dict)]
    return []


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
            # 2026-07-06 헤르메스 run-bundle 스키마 (KANBAN_MIGRATION_SPEC_20260706)
            "quality_report_json": "ALTER TABLE daily_mailing_run ADD COLUMN quality_report_json TEXT NOT NULL DEFAULT 'null'",
            "personas_json": "ALTER TABLE daily_mailing_run ADD COLUMN personas_json TEXT NOT NULL DEFAULT '[]'",
            "reviewer_roles_json": "ALTER TABLE daily_mailing_run ADD COLUMN reviewer_roles_json TEXT NOT NULL DEFAULT '[]'",
            "operating_policy_json": "ALTER TABLE daily_mailing_run ADD COLUMN operating_policy_json TEXT NOT NULL DEFAULT 'null'",
            "counts_json": "ALTER TABLE daily_mailing_run ADD COLUMN counts_json TEXT NOT NULL DEFAULT 'null'",
            "draft_items_json": "ALTER TABLE daily_mailing_run ADD COLUMN draft_items_json TEXT NOT NULL DEFAULT '[]'",
            "dashboard_scope_json": "ALTER TABLE daily_mailing_run ADD COLUMN dashboard_scope_json TEXT NOT NULL DEFAULT 'null'",
        }.items():
            if col not in existing:
                conn.execute(ddl)
        existing_article = {row[1] for row in conn.execute("PRAGMA table_info(daily_mailing_article)").fetchall()}
        for col, ddl in {
            "tracking_lane": "ALTER TABLE daily_mailing_article ADD COLUMN tracking_lane TEXT",
            "reviewer_findings_json": "ALTER TABLE daily_mailing_article ADD COLUMN reviewer_findings_json TEXT NOT NULL DEFAULT '[]'",
            "next_action": "ALTER TABLE daily_mailing_article ADD COLUMN next_action TEXT",
            "tracker_tags_json": "ALTER TABLE daily_mailing_article ADD COLUMN tracker_tags_json TEXT NOT NULL DEFAULT '[]'",
            "verification_method": "ALTER TABLE daily_mailing_article ADD COLUMN verification_method TEXT",
            "official_url": "ALTER TABLE daily_mailing_article ADD COLUMN official_url TEXT",
            "content_completeness_json": "ALTER TABLE daily_mailing_article ADD COLUMN content_completeness_json TEXT NOT NULL DEFAULT 'null'",
            "persona_ids_json": "ALTER TABLE daily_mailing_article ADD COLUMN persona_ids_json TEXT NOT NULL DEFAULT '[]'",
            "reviewer_note": "ALTER TABLE daily_mailing_article ADD COLUMN reviewer_note TEXT",
        }.items():
            if col not in existing_article:
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

    # Run-level 파생/정규화 (헤르메스 run-bundle: personas/reviewer_roles 는 id 리스트).
    selected_in_cards = sum(1 for c in article_cards if c.get("selected_for_draft"))
    counts = {
        "discovered": int(payload.get("discovered_count") or 0),
        "recent": int(payload.get("recent_count") or 0),
        "selected": int(payload.get("selected_count") or selected_in_cards),
        "needs_review": sum(1 for c in article_cards if c.get("review_status") == "needs_review"),
        "ready_for_writer": sum(1 for c in article_cards if c.get("review_status") == "ready_for_writer"),
    }
    personas = _normalize_personas(payload.get("personas") or payload.get("persona_ids"))
    reviewer_roles = _normalize_reviewer_roles(payload.get("reviewer_roles") or payload.get("reviewer_role_ids"))
    operating_policy = payload.get("operating_policy") or dict(DEFAULT_OPERATING_POLICY)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_mailing_run (
                run_id, generated_at, window_label, lookback_hours, keywords_json, media_json,
                subscription_id, owner_email, recipients_json, delivery_status, approval_status,
                discovered_count, recent_count, selected_count, status,
                markdown_path, html_path, json_path, review_board_path,
                quality_report_json, personas_json, reviewer_roles_json, operating_policy_json,
                counts_json, draft_items_json, dashboard_scope_json,
                expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(payload.get("quality_report"), ensure_ascii=False),
                json.dumps(personas, ensure_ascii=False),
                json.dumps(reviewer_roles, ensure_ascii=False),
                json.dumps(operating_policy, ensure_ascii=False),
                json.dumps(counts, ensure_ascii=False),
                json.dumps(_enrich_draft_items(payload.get("items")), ensure_ascii=False),
                json.dumps(payload.get("dashboard_scope"), ensure_ascii=False),
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
                    verification_caveat, tracking_lane, reviewer_findings_json, next_action,
                    tracker_tags_json, verification_method, official_url, content_completeness_json,
                    persona_ids_json, reviewer_note, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    card.get("tracking_lane"),
                    json.dumps(_normalize_findings(card.get("reviewer_findings")), ensure_ascii=False),
                    card.get("next_action"),
                    json.dumps(card.get("tracker_tags") or [], ensure_ascii=False),
                    card.get("verification_method"),
                    card.get("official_url"),
                    json.dumps(card.get("content_completeness"), ensure_ascii=False),
                    json.dumps(card.get("persona_ids") or [], ensure_ascii=False),
                    card.get("reviewer_note"),
                    expires_at,
                    now.isoformat(),
                ),
            )
        conn.commit()
    purge_expired_daily_mailing_rows(db_path, now=now)
    return {"run_id": run_id, "article_count": len(article_cards), "expires_at": expires_at}


def load_latest_run_for_subscription(
    subscription_id: int | None,
    owner_email: str | None = None,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> dict | None:
    """구독의 최신 헤르메스 run 1건 반환 (없으면 None).

    '최근 발송 보기'가 대쉬보드 digest 가 아닌 실제 헤르메스 브리프를 보여주기 위해 사용.
    폴백 순서: subscription_id 일치 → owner_email 일치 → 전체 최신.
    반환 dict 에는 raw 컬럼(html_path, draft_items_json 등) 외에 파싱된
    ``draft_items``/``keywords`` 키를 추가한다.
    """
    ensure_daily_mailing_tables(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = None
        if subscription_id is not None:
            row = conn.execute(
                "SELECT * FROM daily_mailing_run WHERE subscription_id=? ORDER BY generated_at DESC LIMIT 1",
                (subscription_id,),
            ).fetchone()
        if row is None and owner_email:
            row = conn.execute(
                "SELECT * FROM daily_mailing_run WHERE owner_email=? ORDER BY generated_at DESC LIMIT 1",
                (owner_email,),
            ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM daily_mailing_run ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["draft_items"] = _json_or(run.get("draft_items_json"), [])
    run["keywords"] = _json_or(run.get("keywords_json"), [])
    return run


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

    # Run rows → parsed dicts (신규 *_json 컬럼은 parsed 키로 대체).
    runs_out: list[dict] = []
    for r in runs:
        run = dict(r)
        run["quality_report"] = _json_or(run.pop("quality_report_json", None), None)
        run["personas"] = _normalize_personas(_json_or(run.pop("personas_json", None), []))
        run["reviewer_roles"] = _normalize_reviewer_roles(_json_or(run.pop("reviewer_roles_json", None), []))
        run["operating_policy"] = _json_or(run.pop("operating_policy_json", None), None)
        run["counts"] = _json_or(run.pop("counts_json", None), None) or {
            "discovered": run.get("discovered_count", 0),
            "recent": run.get("recent_count", 0),
            "selected": run.get("selected_count", 0),
            "needs_review": 0,
            "ready_for_writer": 0,
        }
        run["draft_items"] = _json_or(run.pop("draft_items_json", None), [])
        run["dashboard_scope"] = _json_or(run.pop("dashboard_scope_json", None), None)
        runs_out.append(run)
    run_by_id = {run["run_id"]: run for run in runs_out}

    # Top-level 정책/페르소나/리뷰어 렌즈 — 최신 run 값 우선, 없으면 빌더 기본값.
    operating_policy = next((run["operating_policy"] for run in runs_out if run.get("operating_policy")), None) or dict(DEFAULT_OPERATING_POLICY)
    personas = next((run["personas"] for run in runs_out if run.get("personas")), None) or _default_personas()
    reviewer_roles = next((run["reviewer_roles"] for run in runs_out if run.get("reviewer_roles")), None) or _default_reviewer_roles()

    lane_names = list(KANBAN_LANES)
    lanes: dict[str, list] = {name: [] for name in lane_names}
    for row in articles:
        item = dict(row)
        item["quality_flags"] = _json_or(item.pop("quality_flags_json", None), [])
        item["matched_keywords"] = _json_or(item.pop("matched_keywords_json", None), [])
        item["reviewer_findings"] = _normalize_findings(_json_or(item.pop("reviewer_findings_json", None), []))
        item["tracker_tags"] = _json_or(item.pop("tracker_tags_json", None), [])
        item["content_completeness"] = _json_or(item.pop("content_completeness_json", None), {})
        item["persona_ids"] = _json_or(item.pop("persona_ids_json", None), [])
        run = run_by_id.get(item["run_id"], {})
        item["generated_at"] = run.get("generated_at")
        item["html_path"] = run.get("html_path")
        # 6-lane 배정 (KANBAN_MIGRATION_SPEC_20260706 §6 레인)
        if item.get("selected_for_draft"):
            lane = "Writer Agent"
        elif item.get("review_status") == "ready_for_writer":
            lane = "Review Board"
        elif item.get("source_status") in {"publisher_verified", "official_verified"}:
            lane = "Triage/Verify"
        else:
            lane = "Source Intake"
        lanes[lane].append(item)

    # Dashboard Scope: 최신 run 스콥 요약 synthetic 카드 1장 (기사 아님, type='run_scope').
    if runs_out:
        latest = runs_out[0]
        lanes["Dashboard Scope"].append({
            "type": "run_scope",
            "run_id": latest["run_id"],
            "generated_at": latest.get("generated_at"),
            "window_label": latest.get("window_label"),
            "keywords": _json_or(latest.get("keywords_json"), []),
            "media": _json_or(latest.get("media_json"), []),
            "personas": personas,
            "reviewer_roles": reviewer_roles,
            "dashboard_scope": latest.get("dashboard_scope"),
            "owner_email": latest.get("owner_email"),
            "recipients": _json_or(latest.get("recipients_json"), []),
        })
    # Delivery/History: run별 발송상태·산출물 카드 (type='run_delivery').
    for run in runs_out:
        lanes["Delivery/History"].append({
            "type": "run_delivery",
            "run_id": run["run_id"],
            "generated_at": run.get("generated_at"),
            "status": run.get("status"),
            "delivery_status": run.get("delivery_status"),
            "approval_status": run.get("approval_status"),
            "sent_at": run.get("sent_at"),
            "gmail_draft_id": run.get("gmail_draft_id"),
            "gmail_message_id": run.get("gmail_message_id"),
            "counts": run.get("counts"),
            "quality_report": run.get("quality_report"),
            "markdown_path": run.get("markdown_path"),
            "html_path": run.get("html_path"),
            "json_path": run.get("json_path"),
            "review_board_path": run.get("review_board_path"),
        })

    return {
        "status": "admin_operational_board",
        "retention_days": RETENTION_DAYS,
        "article_approval_required": False,
        "operating_policy": operating_policy,
        "personas": personas,
        "reviewer_roles": reviewer_roles,
        "lanes": [{"name": name, "items": lanes.get(name, [])} for name in lane_names],
        "runs": runs_out,
    }
