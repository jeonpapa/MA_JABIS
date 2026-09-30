from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.daily_mailing import dispatch as dispatch_mod
from agents.daily_mailing.dispatch import (
    ApprovalError,
    DispatchDeps,
    approve_run,
    dispatch_due_subscriptions,
    health_check,
    reject_run,
    send_test,
)
from agents.daily_mailing.run_store import claim_for_sending, find_regular_run, get_run, list_runs_for_date

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


def _persist_run(db, run_id, *, sub=19, delivery, approval="not_required", is_test=False,
                 generated_at=None, attempts=0, run_date="2026-09-18"):
    """프로세스가 죽어 남은 run 행을 직접 심는다 (persist 직후·선점 전 상태 재현)."""
    from agents.daily_mailing.run_store import update_run
    from agents.daily_mailing.storage import persist_daily_mailing_run
    persist_daily_mailing_run({
        "run_id": run_id, "generated_at": (generated_at or FRI_0810).isoformat(), "keywords": [],
        "subscription_id": sub, "status": "ready", "run_date": run_date, "is_test": is_test,
        "email_subject": "[MA Daily] 파드셉", "email_html": "<p>orphan</p>", "recipients": ["r@msd.com"],
        "delivery_status": delivery, "approval_status": approval}, articles=[], db_path=db)
    if attempts:
        update_run(run_id, db_path=db, send_attempts=attempts)


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
    """재시도는 주기 간격을 둔다 — 같은 tick 안에서 실패 직후 재시도하지 않는다 (T → T+10 → T+20)."""
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    rec = Recorder(send_ok=False)

    def _state():
        run = find_regular_run(19, "2026-09-18", db_path=db)
        return run["delivery_status"], run["send_attempts"]

    dispatch_due_subscriptions(_deps(db, rec))                                         # T: 생성 즉시 attempt 1
    assert _state() == ("send_failed", 1)                                              # 같은 주기 재시도 없음
    dispatch_due_subscriptions(_deps(db, rec, now=FRI_0810 + timedelta(minutes=10)))  # T+10: attempt 2
    assert _state() == ("send_failed", 2)
    dispatch_due_subscriptions(_deps(db, rec, now=FRI_0810 + timedelta(minutes=20)))  # T+20: attempt 3 → held
    assert _state() == ("held", 3)
    dispatch_due_subscriptions(_deps(db, rec, now=FRI_0810 + timedelta(minutes=30)))  # T+30: held 는 재시도 없음
    assert _state() == ("held", 3)
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert run["delivery_error"] == "token expired"
    assert len(rec.sent) == 3


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


def test_concurrent_approve_second_request_loses_claim(tmp_path, monkeypatch):
    """동시 승인: 두 요청 모두 상태 검사는 통과하지만 선점은 한 번만 — 진 쪽은 ApprovalError, 발송은 정확히 1회.

    요청 B 의 선점 직전에 요청 A 가 끼어들어 선점·승인·발송을 끝내는 인터리빙을 재현한다.
    """
    db = _db(tmp_path, {"id": 19})
    rec = Recorder()
    deps = _deps(db, rec)
    dispatch_due_subscriptions(deps)
    run_id = find_regular_run(19, "2026-09-18", db_path=db)["run_id"]
    real_claim = dispatch_mod.claim_for_sending
    interleaved = {"done": False}

    def racing_claim(rid, **kw):
        if not interleaved["done"]:
            interleaved["done"] = True
            approve_run(rid, approver="first@msd.com", deps=deps)   # 요청 A 가 먼저 끝난다
        return real_claim(rid, **kw)                                 # 요청 B 의 선점 → 실패

    monkeypatch.setattr(dispatch_mod, "claim_for_sending", racing_claim)
    with pytest.raises(ApprovalError, match="이미 발송을 진행"):
        approve_run(run_id, approver="second@msd.com", deps=deps)
    run = get_run(run_id, db_path=db)
    assert (run["delivery_status"], run["approved_by"]) == ("sent", "first@msd.com")   # 진 요청은 승인자도 덮어쓰지 않음
    assert len(rec.sent) == 1


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


def test_stale_sending_recovered_to_held_without_resend(tmp_path):
    db = _db(tmp_path, {"id": 19}, {"id": 20})
    rec = Recorder()
    deps = _deps(db, rec)
    dispatch_due_subscriptions(deps)
    r19 = find_regular_run(19, "2026-09-18", db_path=db)["run_id"]
    r20 = find_regular_run(20, "2026-09-18", db_path=db)["run_id"]
    # 19: 20분 전에 선점된 채 프로세스 중단 (stale) / 20: 5분 전 선점 (fresh, 진행 중)
    assert claim_for_sending(r19, from_statuses=("pending_approval",),
                             claimed_at=(FRI_0810 - timedelta(minutes=20)).isoformat(), db_path=db)
    assert claim_for_sending(r20, from_statuses=("pending_approval",),
                             claimed_at=(FRI_0810 - timedelta(minutes=5)).isoformat(), db_path=db)
    summary = dispatch_due_subscriptions(deps)
    assert summary["recovered"] == 1
    stale = get_run(r19, db_path=db)
    assert stale["delivery_status"] == "held" and "프로세스 중단" in stale["delivery_error"]
    assert get_run(r20, db_path=db)["delivery_status"] == "sending"
    assert rec.sent == []   # 이미 나갔을 수 있는 메일 — 자동 재발송 금지
    problems = {(p["subscription_id"], p["problem"]) for p in health_check(deps)["problems"]}
    assert (19, "held") in problems and all(p[0] != 20 for p in problems)


def test_send_test_failure_is_recorded_as_test_error_run(tmp_path):
    """테스트 메일 생성 실패는 보이지 않게 사라지면 안 된다 — is_test 오류 run 으로 남기되 정규 슬롯은 점유하지 않는다."""
    db = _db(tmp_path, {"id": 19, "time": "09:00"})
    rec = Recorder(raise_on_generate=True)
    with pytest.raises(RuntimeError, match="engine down"):
        send_test(19, requester_email="me@msd.com", deps=_deps(db, rec))
    runs = list_runs_for_date("2026-09-18", db_path=db, include_tests=True)
    assert [(r["is_test"], r["status"], r["delivery_status"]) for r in runs] == [(1, "error", "error")]
    assert "engine down" in runs[0]["delivery_error"] and runs[0]["run_id"].endswith("_test_error")
    assert find_regular_run(19, "2026-09-18", db_path=db) is None
    assert rec.sent == []
    ok = Recorder()   # 정규 발송은 영향 없음 — 시각 도래 후 정상 생성
    dispatch_due_subscriptions(_deps(db, ok, now=datetime(2026, 9, 18, 9, 10, tzinfo=KST)))
    assert find_regular_run(19, "2026-09-18", db_path=db)["delivery_status"] == "pending_approval"


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


def test_orphaned_ready_to_send_is_sent_on_next_tick(tmp_path):
    """persist(ready_to_send) 직후 프로세스가 죽어 선점(claim)이 없었던 run — send_attempts=0 이라 재시도가 안전.

    이전에는 슬롯만 점유한 채 영원히 ready_to_send 로 남았다 (재시도는 send_failed 만, 복구는 sending 만 봄).
    """
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    _persist_run(db, "dm_19_orphan", delivery="ready_to_send")
    rec = Recorder()
    summary = dispatch_due_subscriptions(_deps(db, rec))
    assert summary["skipped"] == 1 and rec.generated == []          # 슬롯 점유 — 재생성 없음
    assert summary["retried"] == 1
    run = get_run("dm_19_orphan", db_path=db)
    assert (run["delivery_status"], run["send_attempts"], run["gmail_message_id"]) == ("sent", 1, "m1")
    assert rec.sent == [(["r@msd.com"], "[MA Daily] 파드셉")]
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT last_sent_at FROM mail_subscription WHERE id=19").fetchone()[0]


def test_orphaned_or_failed_test_run_is_never_retried_to_real_recipients(tmp_path):
    """[TEST] run 의 recipients_json 은 구독 수신자 — 재시도 경로가 집으면 테스트 메일이 실수신자에게 간다."""
    db = _db(tmp_path, {"id": 19, "time": "09:00"})
    _persist_run(db, "dm_19_t1_test", delivery="ready_to_send", is_test=True)
    _persist_run(db, "dm_19_t2_test", delivery="send_failed", is_test=True, attempts=1)
    rec = Recorder()
    summary = dispatch_due_subscriptions(_deps(db, rec))
    assert summary["retried"] == 0 and rec.sent == []
    assert get_run("dm_19_t1_test", db_path=db)["delivery_status"] == "ready_to_send"
    assert get_run("dm_19_t2_test", db_path=db)["delivery_status"] == "send_failed"


def test_health_flags_ready_to_send_stuck_over_five_minutes(tmp_path):
    db = _db(tmp_path, {"id": 19}, {"id": 20})
    _persist_run(db, "dm_19_stuck", delivery="ready_to_send", generated_at=FRI_0810 - timedelta(minutes=6))
    _persist_run(db, "dm_20_fresh", sub=20, delivery="ready_to_send", generated_at=FRI_0810 - timedelta(minutes=2))
    problems = {(p["subscription_id"], p["problem"]) for p in health_check(_deps(db, Recorder()))["problems"]}
    assert (19, "ready_to_send") in problems
    assert all(p[0] != 20 for p in problems)   # 2분 전 생성 — 진행 중으로 본다


def test_post_send_bookkeeping_retries_on_locked_db(tmp_path, monkeypatch):
    """Gmail 발송 성공 후 update_run 이 'database is locked' 면 짧게 재시도 — 발송 결과(sent_at·message id)를 잃지 않는다."""
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    rec = Recorder()
    sleeps: list[float] = []
    deps = _deps(db, rec)
    deps.sleep = sleeps.append
    real_update = dispatch_mod.update_run
    fails = {"left": 2}

    def flaky_update(run_id, **kw):
        if kw.get("delivery_status") == "sent" and fails["left"] > 0:
            fails["left"] -= 1
            raise sqlite3.OperationalError("database is locked")
        return real_update(run_id, **kw)

    monkeypatch.setattr(dispatch_mod, "update_run", flaky_update)
    dispatch_due_subscriptions(deps)
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert (run["delivery_status"], run["gmail_message_id"], run["send_attempts"]) == ("sent", "m1", 1)
    assert run["sent_at"]
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps)
    assert len(rec.sent) == 1                                   # 재시도는 DB 쓰기만 — 재발송 없음
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT last_sent_at FROM mail_subscription WHERE id=19").fetchone()[0]


def test_post_send_bookkeeping_gives_up_with_recoverable_error_log(tmp_path, monkeypatch, caplog):
    """3회 모두 실패하면 예외 대신 run_id + gmail_message_id 를 ERROR 로 남겨 운영자가 복구할 수 있게 한다."""
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    rec = Recorder()
    deps = _deps(db, rec)
    deps.sleep = lambda s: None
    real_update = dispatch_mod.update_run

    def locked_update(run_id, **kw):
        if kw.get("delivery_status") == "sent":
            raise sqlite3.OperationalError("database is locked")
        return real_update(run_id, **kw)

    monkeypatch.setattr(dispatch_mod, "update_run", locked_update)
    with caplog.at_level(logging.ERROR, logger="agents.daily_mailing.dispatch"):
        summary = dispatch_due_subscriptions(deps)
    run = find_regular_run(19, "2026-09-18", db_path=db)
    assert run["delivery_status"] == "sending" and len(rec.sent) == 1   # 예외 전파 없음, 재발송 없음
    assert summary["generated"][0]["delivery_status"] == "sending"
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(run["run_id"] in m and "m1" in m for m in msgs), msgs


def test_locked_db_during_persist_skips_tick_without_error_run(tmp_path, monkeypatch):
    """persist 단계의 database-is-locked 는 일시 장애 — 그날을 점유하는 error run 대신 로그만 남기고 다음 tick 에 재시도."""
    db = _db(tmp_path, {"id": 19})
    rec = Recorder()
    real_persist = dispatch_mod.persist_daily_mailing_run
    monkeypatch.setattr(dispatch_mod, "persist_daily_mailing_run",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    summary = dispatch_due_subscriptions(_deps(db, rec))
    assert summary["deferred"] == [19] and summary["errors"] == [] and summary["generated"] == []
    assert find_regular_run(19, "2026-09-18", db_path=db) is None       # 슬롯 비어 있음
    monkeypatch.setattr(dispatch_mod, "persist_daily_mailing_run", real_persist)
    summary = dispatch_due_subscriptions(_deps(db, rec, now=FRI_0810 + timedelta(minutes=10)))
    assert len(rec.generated) == 2 and summary["generated"][0]["delivery_status"] == "pending_approval"


def test_locked_db_while_recording_generate_error_does_not_abort_other_subscriptions(tmp_path, monkeypatch):
    db = _db(tmp_path, {"id": 19}, {"id": 20})
    rec = Recorder()
    calls = {"n": 0}
    real_generate = rec.generate

    def generate(scope, **kw):
        if scope.subscription_id == "19":
            raise RuntimeError("engine down")
        return real_generate(scope, **kw)

    def locked_record(**kw):
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(dispatch_mod, "record_error_run", locked_record)
    deps = DispatchDeps(db_path=db, generate=generate, load_top_ma_news=lambda d: [], send=rec.send,
                        now=lambda: FRI_0810, gmail_ready=lambda: True)
    summary = dispatch_due_subscriptions(deps)
    assert calls["n"] == 1 and summary["errors"] == [19]
    assert find_regular_run(20, "2026-09-18", db_path=db)["delivery_status"] == "pending_approval"


def test_previous_day_send_failed_is_expired_not_sent_late(tmp_path):
    """전날 send_failed(attempts 1) 가 오늘 tick 에서 재시도되어 하루 늦게 나가지 않는다 — stale pending 처럼 expired."""
    db = _db(tmp_path, {"id": 19, "mode": "auto"})
    _persist_run(db, "dm_19_yesterday", delivery="send_failed", attempts=1, run_date="2026-09-17",
                 generated_at=FRI_0810 - timedelta(days=1))
    rec = Recorder()
    summary = dispatch_due_subscriptions(_deps(db, rec))
    assert summary["expired"] == 1 and summary["retried"] == 0
    assert get_run("dm_19_yesterday", db_path=db)["delivery_status"] == "expired"
    assert rec.sent == [(["r@msd.com"], "[MA Daily] 파드셉")]        # 오늘 정규 run 만 발송
    assert find_regular_run(19, "2026-09-18", db_path=db)["delivery_status"] == "sent"


def test_health_does_not_report_admin_rejected_run_as_problem(tmp_path):
    """관리자 반려(held + rejected)는 의도된 결정 — 09:30 헬스체크가 매일 문제로 보고하면 안 된다. 품질 게이트 held 는 계속 문제."""
    db = _db(tmp_path, {"id": 19}, {"id": 20})
    rec = Recorder(results={"20": "held"})
    deps = _deps(db, rec)
    dispatch_due_subscriptions(deps)
    reject_run(find_regular_run(19, "2026-09-18", db_path=db)["run_id"], approver="admin@msd.com", deps=deps)
    report = health_check(deps)
    problems = {(p["subscription_id"], p["problem"]) for p in report["problems"]}
    assert problems == {(20, "held")}
    assert report["rejected"] == [{"subscription_id": 19, "name": "파드셉", "approved_by": "admin@msd.com"}]


def _production_top_news(monkeypatch, db, generate):
    """DispatchDeps.production 의 top_ma_news 클로저만 실행 — 엔진 의존성은 지연 생성이라 default 를 가짜로 막는다."""
    from agents.daily_mailing import engine as engine_mod
    monkeypatch.setattr(engine_mod.EngineDeps, "default", classmethod(lambda cls, **kw: SimpleNamespace()))
    monkeypatch.setattr(engine_mod, "generate_top_ma_news", generate)
    return DispatchDeps.production(db_path=db).load_top_ma_news


def test_production_top_news_failure_is_cached_for_the_day(tmp_path, monkeypatch, caplog):
    """generate_top_ma_news 예외가 캐시되지 않으면 10분마다 모든 tick 이 엔진(LLM·Naver)을 다시 돌린다."""
    from agents.daily_mailing.top_news import TOP_NEWS_FAILED, load_top_ma_news_entry
    db = tmp_path / "d.db"
    calls = {"n": 0}

    def failing(*, deps, config, **kw):
        calls["n"] += 1
        raise RuntimeError("openai down")

    top = _production_top_news(monkeypatch, db, failing)
    with caplog.at_level(logging.ERROR, logger="agents.daily_mailing.dispatch"):
        assert top("2026-09-18") == []
    assert calls["n"] == 1
    assert any("주요 MA 뉴스" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
    entry = load_top_ma_news_entry("2026-09-18", db_path=db)
    assert entry["status"] == TOP_NEWS_FAILED and "openai down" in entry["error"]
    assert top("2026-09-18") == [] and top("2026-09-18") == []
    assert calls["n"] == 1                                    # 같은 날 재시도 없음
    assert top("2026-09-19") == [] and calls["n"] == 2        # 다음 날은 새 시도
    assert top("2026-09-18", retry_failed=True) == [] and calls["n"] == 3   # 명시 재시도(06:40 잡용)만 다시 돈다


def test_production_top_news_success_is_cached_and_round_trips_brief_items(tmp_path, monkeypatch):
    from agents.daily_mailing.brief import BriefItem
    db = tmp_path / "d.db"
    calls = {"n": 0}
    item = BriefItem(title="약평위 결과", publisher_url="https://www.yakup.com/p1", source_name="약업신문",
                     published_at="2026-09-18T06:00:00+09:00", summary_bullets=("요약",), ma_implication="시사점",
                     matched_scope_terms=("약평위",), section="top_ma_news", body_status="ok")

    def ok(*, deps, config, **kw):
        calls["n"] += 1
        return [item]

    top = _production_top_news(monkeypatch, db, ok)
    assert [i.title for i in top("2026-09-18")] == ["약평위 결과"]
    assert [i.title for i in top("2026-09-18")] == ["약평위 결과"]
    assert calls["n"] == 1
    assert top("2026-09-18", retry_failed=True) and calls["n"] == 1   # 성공 캐시는 retry_failed 로도 다시 돌지 않는다
