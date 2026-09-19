from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agents.daily_mailing.run_store import (
    claim_for_sending,
    expire_pending_before,
    find_regular_run,
    get_run,
    list_failed_sends,
    list_runs_for_date,
    list_sending_runs,
    record_error_run,
    update_run,
)
from agents.daily_mailing.storage import persist_daily_mailing_run

KST = timezone(timedelta(hours=9))


def _persist(db, run_id, *, sub=19, run_date="2026-09-18", is_test=False, delivery="pending_approval"):
    persist_daily_mailing_run({
        "run_id": run_id, "generated_at": "2026-09-18T08:00:00+09:00", "keywords": [], "subscription_id": sub,
        "status": "ready", "run_date": run_date, "is_test": is_test, "email_subject": "s", "email_html": "<p/>",
        "delivery_status": delivery, "approval_status": "pending", "recipients": ["r@msd.com"],
    }, articles=[], db_path=db)


def test_find_and_list(tmp_path):
    db = tmp_path / "d.db"
    _persist(db, "a")
    _persist(db, "t", is_test=True)
    assert find_regular_run(19, "2026-09-18", db_path=db)["run_id"] == "a"
    assert find_regular_run(19, "2026-09-17", db_path=db) is None
    assert [r["run_id"] for r in list_runs_for_date("2026-09-18", db_path=db)] == ["a"]
    assert {r["run_id"] for r in list_runs_for_date("2026-09-18", db_path=db, include_tests=True)} == {"a", "t"}


def test_update_allows_only_known_fields(tmp_path):
    db = tmp_path / "d.db"
    _persist(db, "a")
    update_run("a", db_path=db, delivery_status="sent", send_attempts=1)
    assert get_run("a", db_path=db)["delivery_status"] == "sent"
    with pytest.raises(ValueError):
        update_run("a", db_path=db, email_html="x")


def test_claim_is_exclusive_and_records_claimed_at(tmp_path):
    db = tmp_path / "d.db"
    _persist(db, "a")
    _persist(db, "b", sub=20)
    assert claim_for_sending("a", from_statuses=("pending_approval",),
                             claimed_at="2026-09-18T08:10:00+09:00", db_path=db) is True
    assert claim_for_sending("a", from_statuses=("pending_approval",),
                             claimed_at="2026-09-18T08:20:00+09:00", db_path=db) is False
    run = get_run("a", db_path=db)
    assert run["delivery_status"] == "sending"
    assert run["claimed_at"] == "2026-09-18T08:10:00+09:00"   # 두 번째(실패한) 선점은 덮어쓰지 않음
    assert [r["run_id"] for r in list_sending_runs(db_path=db)] == ["a"]


def test_expire_and_failed_list(tmp_path):
    db = tmp_path / "d.db"
    _persist(db, "old", run_date="2026-09-17")
    _persist(db, "new", sub=20)
    assert expire_pending_before("2026-09-18", db_path=db) == 1
    assert get_run("old", db_path=db)["delivery_status"] == "expired"
    update_run("new", db_path=db, delivery_status="send_failed", send_attempts=1)
    assert [r["run_id"] for r in list_failed_sends(db_path=db)] == ["new"]
    update_run("new", db_path=db, send_attempts=3)
    assert list_failed_sends(db_path=db) == []


def test_error_run_occupies_day(tmp_path):
    db = tmp_path / "d.db"
    run_id = record_error_run(subscription_id=21, run_date="2026-09-18", error="boom",
                              now=datetime(2026, 9, 18, 8, 0, tzinfo=KST), db_path=db)
    run = find_regular_run(21, "2026-09-18", db_path=db)
    assert run["run_id"] == run_id
    assert (run["status"], run["delivery_status"], run["delivery_error"]) == ("error", "error", "boom")
