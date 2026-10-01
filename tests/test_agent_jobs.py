from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agents.agent_tasks import jobs
from agents.agent_tasks.store import add_event, connect, list_events, list_tasks, update_task


def _db(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    return db


def test_normalize_result_shapes():
    assert jobs.normalize_result(None) == {"ok": True, "counts": {}, "errors": [], "error_class": "transient"}
    r = jobs.normalize_result({"built": ["a"], "failed": [{"slug": "x", "reason": "ID 미확인"}]})
    assert r["ok"] is False and r["errors"] == ["x: ID 미확인"]
    r2 = jobs.normalize_result({"copied": 0, "skipped": 0, "rejected": 0,
                                "error": "auth required: POLICY_ANALYSIS_TOKEN missing", "error_class": "config"})
    assert r2["ok"] is False and r2["error_class"] == "config"
    assert r2["counts"] == {"copied": 0, "skipped": 0, "rejected": 0}
    r3 = jobs.normalize_result({"generated": [], "errors": [14], "skipped": 2})
    assert r3["errors"] == ["14"] and r3["counts"] == {"skipped": 2}
    r4 = jobs.normalize_result({"ok": False, "counts": {"n": 1}})
    assert r4["ok"] is False and r4["errors"] == []


def test_guarded_job_records_exception_as_failure(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("x_job")
    def boom():
        raise RuntimeError("HIRA timeout")

    res = boom()
    assert res["ok"] is False and res["errors"] == ["RuntimeError: HIRA timeout"]
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"].startswith("x_job:") and tasks[0]["source"] == "scheduler_job"
    runs = sqlite3.connect(db).execute("SELECT job_id, ok FROM scheduler_job_run").fetchall()
    assert runs == [("x_job", 0)]


def test_guarded_job_success_records_run_and_no_task(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("y_job")
    def fine():
        return {"counts": {"rows": 3}}

    res = fine()
    assert res == {"ok": True, "counts": {"rows": 3}, "errors": [], "error_class": "transient"}
    assert list_tasks(db_path=db) == []
    assert sqlite3.connect(db).execute("SELECT ok, counts FROM scheduler_job_run").fetchall() == [(1, '{"rows": 3}')]


def test_guarded_job_config_error_uses_stable_ref(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("z_job")
    def misconfigured():
        return {"error": "auth required", "error_class": "config"}

    misconfigured()
    misconfigured()
    tasks = list_tasks(db_path=db)
    assert [t["source_ref"] for t in tasks] == ["z_job:config"]
    assert "설정 문제" in tasks[0]["title"]


def test_guarded_job_exposes_job_id_and_skips_run_record_when_asked(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("hb", record_runs=False)
    def hb():
        return None

    assert hb.__job_id__ == "hb"
    hb()
    conn = connect(db)
    conn.executescript(jobs.JOB_RUN_SCHEMA)
    assert conn.execute("SELECT COUNT(*) FROM scheduler_job_run").fetchone()[0] == 0


def test_signal_failure_does_not_break_job(monkeypatch, tmp_path, caplog):
    _db(monkeypatch, tmp_path)

    def boom(*a, **kw):
        raise RuntimeError("DB 잠김")

    monkeypatch.setattr("agents.agent_tasks.signals.record_job_failure", boom)

    @jobs.guarded_job("w_job")
    def failing():
        return {"errors": ["x"]}

    with caplog.at_level("WARNING", logger="agents.agent_tasks.jobs"):
        res = failing()                       # 예외가 밖으로 나오면 실패
    assert res["ok"] is False
    assert any("DB 잠김" in r.getMessage() for r in caplog.records)


def test_blocked_task_suppresses_new_task_until_closed(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("v_job")
    def failing():
        return {"errors": ["같은 오류"]}

    first = failing()
    tid = list_tasks(db_path=db)[0]["id"]
    update_task(tid, db_path=db, status="blocked", actor="dispatcher", resolution="런북 없음")
    failing()
    assert len(list_tasks(db_path=db, statuses=("new", "blocked"))) == 1     # blocked 도 중복 억제
    update_task(tid, db_path=db, status="closed", actor="admin", resolution="처리")
    failing()
    assert len(list_tasks(db_path=db, statuses=("new",))) == 1               # 닫힌 뒤에는 새 과제


def test_recurrence_event_once_per_day(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    @jobs.guarded_job("u_job")
    def failing():
        return {"errors": ["오류"]}

    failing()
    failing()
    tid = list_tasks(db_path=db)[0]["id"]
    assert [e["event"] for e in list_events(tid, db_path=db)] == ["created"]      # 같은 날 반복은 이벤트 없음
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = connect(db)
    conn.execute("UPDATE agent_task_event SET created_at=? WHERE task_id=?", (yesterday, tid))
    conn.commit()
    failing()
    failing()
    assert [e["event"] for e in list_events(tid, db_path=db)] == ["created", "recurred"]


def test_recent_job_runs_returns_last_run_per_job(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    started = datetime.now(timezone.utc)
    jobs.record_job_run("a", started, {"ok": False, "counts": {}, "errors": ["e"], "error_class": "transient"}, db_path=db)
    jobs.record_job_run("a", started, {"ok": True, "counts": {}, "errors": [], "error_class": "transient"}, db_path=db)
    jobs.record_job_run("b", started, {"ok": False, "counts": {}, "errors": ["e"], "error_class": "transient"}, db_path=db)
    rows = jobs.recent_job_runs(hours=24, db_path=db)
    assert {(r["job_id"], r["ok"]) for r in rows} == {("a", 1), ("b", 0)}
