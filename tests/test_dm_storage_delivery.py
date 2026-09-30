from __future__ import annotations

import sqlite3

import pytest

from agents.daily_mailing.storage import (
    DuplicateRegularRunError,
    load_admin_kanban,
    persist_daily_mailing_run,
)


def _payload(**kw):
    base = {"run_id": "dm_19_a", "generated_at": "2026-09-18T08:00:00+09:00", "window_label": "이전 24시간",
            "lookback_hours": 24, "keywords": ["파드셉"], "media": [], "subscription_id": 19,
            "owner_email": "o@msd.com", "recipients": ["r@msd.com"], "status": "ready",
            "quality_report": {"status": "ready"}, "personas": ["ma_lead"],
            "items": [{"title": "t", "ma_implication": "LLM 시사점", "work_note": "LLM 시사점"}],
            "dashboard_scope": {}, "run_date": "2026-09-18", "is_test": False,
            "email_subject": "[MA Daily] 파드셉 · 9/18(금)", "email_html": "<p>x</p>",
            "delivery_status": "pending_approval", "approval_status": "pending"}
    base.update(kw)
    return base


def _row(db, run_id):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return dict(conn.execute("SELECT * FROM daily_mailing_run WHERE run_id=?", (run_id,)).fetchone())


def test_persist_stores_delivery_fields(tmp_path):
    db = tmp_path / "d.db"
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)
    row = _row(db, "dm_19_a")
    assert row["run_date"] == "2026-09-18"
    assert row["is_test"] == 0
    assert row["email_subject"] == "[MA Daily] 파드셉 · 9/18(금)"
    assert row["email_html"] == "<p>x</p>"
    assert row["send_attempts"] == 0
    assert row["delivery_status"] == "pending_approval"
    assert row["claimed_at"] is None   # 발송 선점 시각 컬럼 (선점 전 NULL)


def test_duplicate_regular_run_rejected_but_tests_and_reimport_allowed(tmp_path):
    db = tmp_path / "d.db"
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)  # 같은 run_id 재적재 OK
    with pytest.raises(DuplicateRegularRunError):
        persist_daily_mailing_run(_payload(run_id="dm_19_b"), articles=[], db_path=db)
    persist_daily_mailing_run(_payload(run_id="dm_19_c_test", is_test=True), articles=[], db_path=db)
    assert _row(db, "dm_19_c_test")["is_test"] == 1


def test_llm_items_not_overwritten_by_rule_based_notes(tmp_path):
    import json
    db = tmp_path / "d.db"
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)
    items = json.loads(_row(db, "dm_19_a")["draft_items_json"])
    assert items[0]["work_note"] == "LLM 시사점"


def test_kanban_omits_email_html(tmp_path):
    db = tmp_path / "d.db"
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)
    run = load_admin_kanban(db)["runs"][0]
    assert "email_html" not in run
    assert run["has_email_html"] is True
    assert run["email_subject"].startswith("[MA Daily]")


def test_every_daily_mailing_connection_sets_busy_timeout(tmp_path, monkeypatch):
    """WAL DB 를 다른 스케줄러 잡이 쓰는 동안 'database is locked' 로 발송 후 bookkeeping 이 죽지 않도록
    daily-mailing 4개 모듈의 모든 sqlite 연결은 storage.connect(timeout=30) 헬퍼를 지나야 한다."""
    import sqlite3
    from agents.daily_mailing import dispatch, run_store, storage, top_news

    real_connect = sqlite3.connect
    seen: list = []

    def spy(*args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    db = tmp_path / "d.db"
    with real_connect(db) as c:
        c.execute("""CREATE TABLE mail_subscription (id INTEGER PRIMARY KEY, owner_email TEXT, name TEXT,
            keywords_json TEXT, media_json TEXT, schedule TEXT, time TEXT, week_day TEXT, emails_json TEXT,
            active INTEGER, companies_json TEXT, brands_json TEXT, policy_topics_json TEXT,
            disease_areas_json TEXT, custom_sources_json TEXT, created_at TEXT, updated_at TEXT, last_sent_at TEXT)""")
    persist_daily_mailing_run(_payload(), articles=[], db_path=db)
    storage.load_admin_kanban(db)
    storage.load_latest_run_for_subscription(19, db_path=db)
    storage.purge_expired_daily_mailing_rows(db)
    run_store.get_run("dm_19_a", db_path=db)
    run_store.update_run("dm_19_a", db_path=db, send_attempts=1)
    top_news.store_top_ma_news("2026-09-18", [], db_path=db)
    top_news.load_top_ma_news("2026-09-18", db_path=db)
    dispatch.load_subscriptions(db)
    assert seen and all(t == storage.SQLITE_BUSY_TIMEOUT_SECONDS for t in seen), seen
    assert storage.SQLITE_BUSY_TIMEOUT_SECONDS >= 30


def test_connect_helper_waits_for_lock_instead_of_failing_immediately(tmp_path):
    """timeout 이 실제로 busy handler 로 적용되는지 — 잠긴 DB 에 쓰기가 즉시 실패하지 않고 timeout 만큼 기다린다."""
    import sqlite3
    import time
    from agents.daily_mailing.storage import connect
    db = tmp_path / "d.db"
    with connect(db) as c:
        c.execute("CREATE TABLE t (x)")
    holder = connect(db)
    holder.execute("BEGIN IMMEDIATE")
    waiter = connect(db, timeout=0.3)
    t0 = time.monotonic()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        waiter.execute("INSERT INTO t VALUES (1)")
    assert time.monotonic() - t0 >= 0.25
    holder.rollback()
    holder.close()
    waiter.close()
