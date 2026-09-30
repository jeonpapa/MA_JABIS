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


def _persist(db, run_id, *, sub=19, run_date="2026-09-18", is_test=False, delivery="pending_approval",
             generated_at="2026-09-18T08:00:00+09:00"):
    persist_daily_mailing_run({
        "run_id": run_id, "generated_at": generated_at, "keywords": [], "subscription_id": sub,
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


def test_error_test_run_is_visible_but_does_not_occupy_regular_slot(tmp_path):
    db = tmp_path / "d.db"
    now = datetime(2026, 9, 18, 8, 0, tzinfo=KST)
    test_id = record_error_run(subscription_id=21, run_date="2026-09-18", error="boom", now=now,
                               is_test=True, db_path=db)
    assert test_id.endswith("_test_error")
    assert find_regular_run(21, "2026-09-18", db_path=db) is None
    runs = list_runs_for_date("2026-09-18", db_path=db, include_tests=True)
    assert [(r["run_id"], r["is_test"], r["status"], r["delivery_status"]) for r in runs] == [
        (test_id, 1, "error", "error")]
    regular_id = record_error_run(subscription_id=21, run_date="2026-09-18", error="boom", now=now, db_path=db)
    assert find_regular_run(21, "2026-09-18", db_path=db)["run_id"] == regular_id   # 정규 슬롯은 별도


def test_failed_list_includes_orphaned_ready_to_send_and_excludes_tests(tmp_path):
    """ready_to_send 는 선점 전 중단(send_attempts=0)이라 재시도 대상. [TEST] run 은 어떤 상태든 제외."""
    db = tmp_path / "d.db"
    _persist(db, "orphan", delivery="ready_to_send")
    _persist(db, "failed", sub=20, delivery="send_failed")
    _persist(db, "pending", sub=21)                                   # pending_approval — 승인 대기, 재시도 아님
    _persist(db, "t_orphan_test", sub=22, is_test=True, delivery="ready_to_send")
    _persist(db, "t_failed_test", sub=23, is_test=True, delivery="send_failed")
    assert {r["run_id"] for r in list_failed_sends(db_path=db)} == {"orphan", "failed"}
