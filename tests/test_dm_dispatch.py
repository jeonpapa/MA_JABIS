from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.daily_mailing.dispatch import (
    ApprovalError,
    DispatchDeps,
    approve_run,
    dispatch_due_subscriptions,
    health_check,
    reject_run,
    send_test,
)
from agents.daily_mailing.run_store import find_regular_run, get_run, list_runs_for_date

KST = timezone(timedelta(hours=9))
FRI_0810 = datetime(2026, 9, 18, 8, 10, tzinfo=KST)

DDL = """CREATE TABLE mail_subscription (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT NOT NULL, name TEXT NOT NULL,
    keywords_json TEXT NOT NULL, media_json TEXT NOT NULL, schedule TEXT NOT NULL, time TEXT NOT NULL,
    week_day TEXT, emails_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    companies_json TEXT NOT NULL DEFAULT '[]', brands_json TEXT NOT NULL DEFAULT '[]',
    policy_topics_json TEXT NOT NULL DEFAULT '[]', disease_areas_json TEXT NOT NULL DEFAULT '[]',
    custom_sources_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_sent_at TEXT)"""


def _db(tmp_path, *subs):
    db = tmp_path / "d.db"
    with sqlite3.connect(db) as c:
        c.execute(DDL)
        c.execute("ALTER TABLE mail_subscription ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'approval'")
        c.execute("ALTER TABLE mail_subscription ADD COLUMN include_top_ma_news INTEGER NOT NULL DEFAULT 1")
        for s in subs:
            c.execute("INSERT INTO mail_subscription (id, owner_email, name, keywords_json, media_json, schedule, time, "
                      "emails_json, brands_json, active, delivery_mode, created_at, updated_at) "
                      "VALUES (?, 'o@msd.com', ?, '[]', '[]', 'Daily', ?, ?, ?, ?, ?, 'x', 'x')",
                      (s["id"], s.get("name", "파드셉"), s.get("time", "08:00"), json.dumps(["r@msd.com"]),
                       json.dumps(["파드셉"]), 1 if s.get("active", True) else 0, s.get("mode", "approval")))
    return db


class Recorder:
    def __init__(self, results=None, raise_on_generate=False, send_ok=True):
        self.generated = []
        self.sent = []
        self.results = results or {}
        self.raise_on_generate = raise_on_generate
        self.send_ok = send_ok

    def generate(self, scope, *, name, now, top_ma_news, is_test):
        if self.raise_on_generate:
            raise RuntimeError("engine down")
        self.generated.append((scope.subscription_id, is_test))
        status = self.results.get(scope.subscription_id, "ready")
        run_id = f"dm_{scope.subscription_id}_{now:%H%M%S}" + ("_test" if is_test else "")
        payload = {"run_id": run_id, "generated_at": now.isoformat(), "keywords": list(scope.brands),
                   "subscription_id": int(scope.subscription_id), "recipients": list(scope.recipients),
                   "status": status, "run_date": now.date().isoformat(), "is_test": is_test,
                   "email_subject": ("[TEST] " if is_test else "") + f"[MA Daily] {name}",
                   "email_html": "<p>brief</p>", "items": []}
        return SimpleNamespace(run_id=run_id, status=status, payload=payload, articles=[])

    def send(self, *, recipients, subject, body_html):
        self.sent.append((list(recipients), subject))
        if self.send_ok:
            return {"ok": True, "sent": True, "gmail_message_id": f"m{len(self.sent)}"}
        return {"ok": False, "sent": False, "message": "token expired"}


def _deps(db, rec, now=FRI_0810, gmail_ready=True):
    return DispatchDeps(db_path=db, generate=rec.generate, load_top_ma_news=lambda d: [],
                        send=rec.send, now=lambda: now, gmail_ready=lambda: gmail_ready)


def test_not_due_does_nothing(tmp_path):
    db = _db(tmp_path, {"id": 19, "time": "09:00"})
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec))
    assert rec.generated == []


def test_approval_mode_waits_and_is_idempotent(tmp_path):
    db = _db(tmp_path, {"id": 19})
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec))
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert (run["delivery_status"], run["approval_status"]) == ("pending_approval", "pending")
    assert rec.sent == []
    summary = dispatch_due_subscriptions(_deps(db, rec, now=FRI_0810 + timedelta(minutes=10)))
    assert len(rec.generated) == 1 and summary["skipped"] == 1


def test_auto_mode_sends_and_marks_last_sent(tmp_path):
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec))
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert run["delivery_status"] == "sent" and run["gmail_message_id"] == "m1"
    assert rec.sent == [(["r@msd.com"], "[MA Daily] 파드셉")]
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT last_sent_at FROM mail_subscription WHERE id=19").fetchone()[0]


def test_auto_no_news_is_sent_as_no_news(tmp_path):
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    dispatch_due_subscriptions(_deps(db, Recorder(results={"19": "no_news"})))
    assert find_regular_run(19, "2026-09-18", db_path=db)["delivery_status"] == "no_news_sent"


def test_held_is_not_sent_even_in_auto(tmp_path):
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    rec = Recorder(results={"19": "held"})
    dispatch_due_subscriptions(_deps(db, rec))
    assert find_regular_run(19, "2026-09-18", db_path=db)["delivery_status"] == "held"
    assert rec.sent == []


def test_generate_error_records_error_run_once(tmp_path):
    db = _db(tmp_path, {"id": 19})
    dispatch_due_subscriptions(_deps(db, Recorder(raise_on_generate=True)))
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert run["delivery_status"] == "error" and "engine down" in run["delivery_error"]
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec, now=FRI_0810 + timedelta(minutes=10)))
    assert rec.generated == []


def test_send_failure_retries_then_holds(tmp_path):
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    rec = Recorder(send_ok=False)
    dispatch_due_subscriptions(_deps(db, rec))   # 생성 즉시 발송 attempt 1 + 같은 주기 끝 재시도 attempt 2
    assert find_regular_run(19, "2026-09-18", db_path=db)["delivery_status"] == "send_failed"
    dispatch_due_subscriptions(_deps(db, rec))   # 다음 주기 재시도 attempt 3 → held
    dispatch_due_subscriptions(_deps(db, rec))   # held 는 더 이상 재시도하지 않음
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert (run["delivery_status"], run["send_attempts"]) == ("held", 3)
    assert run["delivery_error"] == "token expired"


def test_approve_and_reject(tmp_path):
    db = _db(tmp_path, {"id": 19}, {"id": 20})
    rec = Recorder()
    deps = _deps(db, rec)
    dispatch_due_subscriptions(deps)
    r19 = find_regular_run(19, "2026-09-18", db_path=db)["run_id"]
    r20 = find_regular_run(20, "2026-09-18", db_path=db)["run_id"]
    sent = approve_run(r19, approver="admin@msd.com", deps=deps)
    assert sent["delivery_status"] == "sent" and sent["approved_by"] == "admin@msd.com"
    with pytest.raises(ApprovalError):
        approve_run(r19, approver="admin@msd.com", deps=deps)
    rejected = reject_run(r20, approver="admin@msd.com", deps=deps)
    assert (rejected["delivery_status"], rejected["approval_status"]) == ("held", "rejected")
    with pytest.raises(LookupError):
        approve_run("nope", approver="a", deps=deps)


def test_stale_pending_expires_next_day(tmp_path):
    db = _db(tmp_path, {"id": 19})
    rec = Recorder()
    dispatch_due_subscriptions(_deps(db, rec))
    run_id = find_regular_run(19, "2026-09-18", db_path=db)["run_id"]
    next_day = _deps(db, rec, now=FRI_0810 + timedelta(days=1))
    with pytest.raises(ApprovalError):
        approve_run(run_id, approver="a", deps=next_day)
    assert get_run(run_id, db_path=db)["delivery_status"] == "expired"


def test_send_test_goes_to_requester_only_and_does_not_block_regular(tmp_path):
    db = _db(tmp_path, {"id": 19, "time": "09:00"})
    rec = Recorder()
    deps = _deps(db, rec)
    out = send_test(19, requester_email="me@msd.com", deps=deps)
    assert out["delivery_status"] == "sent"
    assert rec.sent == [(["me@msd.com"], "[TEST] [MA Daily] 파드셉")]
    assert find_regular_run(19, "2026-09-18", db_path=db) is None
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT last_sent_at FROM mail_subscription WHERE id=19").fetchone()[0] is None
    assert [r["is_test"] for r in list_runs_for_date("2026-09-18", db_path=db, include_tests=True)] == [1]


def test_health_check(tmp_path):
    db = _db(tmp_path, {"id": 19}, {"id": 20, "time": "23:00"})
    rec = Recorder(results={"19": "held"})
    report = health_check(_deps(db, rec, gmail_ready=False))
    assert report["ok"] is False
    problems = {(p["subscription_id"], p["problem"]) for p in report["problems"]}
    assert (19, "not_generated") in problems and (None, "gmail_token_invalid") in problems
    assert all(p["subscription_id"] != 20 for p in report["problems"])   # 23:00 아직 예정 전
    dispatch_due_subscriptions(_deps(db, rec))
    problems = {(p["subscription_id"], p["problem"]) for p in health_check(_deps(db, rec))["problems"]}
    assert problems == {(19, "held")}
