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
