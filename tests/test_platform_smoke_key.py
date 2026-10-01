"""platform_smoke 과제 키는 날짜가 아니라 실패 체크 집합의 내용 해시 (P1.5 fix E).

재현됐던 결함: platform_smoke:{날짜} 라 화면 하나가 계속 깨져 있으면(예: 정책인텔 overview 500) 고쳐질 때까지 하루 1건씩 생겨
"정상 ≤3건/일" 예산을 먹었다. 이제 같은 실패 집합이면 날이 바뀌어도 1건, 집합이 바뀌면 새 과제. scheduler_heartbeat 는 날짜 키 유지.
"""
from __future__ import annotations

import os
from pathlib import Path

from agents.agent_tasks import signals
from agents.agent_tasks.signals import CONTENT_KEYED_JOBS, record_job_failure
from agents.agent_tasks.store import get_task, list_events, list_tasks

BROKEN = ["policy-intelligence /api/policy-intelligence/overview → HTTP 500"]


def test_same_failure_set_across_days_is_one_task(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    monkeypatch.setattr(signals, "_today", lambda: "2026-10-01")
    a = record_job_failure("platform_smoke", "; ".join(BROKEN), errors=BROKEN, db_path=db)
    monkeypatch.setattr(signals, "_today", lambda: "2026-10-02")
    b = record_job_failure("platform_smoke", "; ".join(BROKEN), errors=BROKEN, db_path=db)
    monkeypatch.setattr(signals, "_today", lambda: "2026-10-03")
    c = record_job_failure("platform_smoke", "; ".join(BROKEN), errors=list(BROKEN), db_path=db)
    assert a == b == c and len(list_tasks(db_path=db)) == 1
    t = get_task(a, db_path=db)
    assert t["source_ref"].startswith("platform_smoke:") and not t["source_ref"].startswith("platform_smoke:2026")
    assert [e["event"] for e in list_events(a, db_path=db)] == ["created", "recurred", "recurred"]   # 하루 1건


def test_changed_failure_set_is_a_new_task_and_status_code_noise_is_not(tmp_path):
    db = tmp_path / "a.db"
    a = record_job_failure("platform_smoke", "x", errors=BROKEN, db_path=db)
    noise = ["policy-intelligence /api/policy-intelligence/overview → HTTP 502"]        # 숫자만 다름 — 같은 화면
    assert record_job_failure("platform_smoke", "x", errors=noise, db_path=db) == a
    more = BROKEN + ["home /api/home/media-intelligence → HTTP 500"]
    b = record_job_failure("platform_smoke", "x", errors=more, db_path=db)
    assert b != a and len(list_tasks(db_path=db)) == 2
    assert record_job_failure("platform_smoke", "x", errors=list(reversed(more)), db_path=db) == b   # 순서 무관


def test_heartbeat_and_ordinary_jobs_keep_date_key(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    monkeypatch.setattr(signals, "_today", lambda: "2026-10-01")
    hb = record_job_failure("scheduler_heartbeat", "heartbeat age=1000s", db_path=db)
    job = record_job_failure("competitor_news_daily", "Naver 5xx", errors=["Naver 5xx"], db_path=db)
    assert get_task(hb, db_path=db)["source_ref"] == "scheduler_heartbeat:2026-10-01"
    assert get_task(job, db_path=db)["source_ref"] == "competitor_news_daily:2026-10-01"
    assert CONTENT_KEYED_JOBS == {"platform_smoke"}


def test_platform_smoke_job_uses_content_key(monkeypatch):
    import scheduler as sch
    from scripts import smoke
    db = Path(os.environ["AGENT_TASK_DB"])
    bad = {"ok": False, "counts": {"screens": 22, "passed": 43, "failed": 1, "skipped": 0}, "errors": BROKEN}
    monkeypatch.setattr(smoke, "run_smoke", lambda base, token_for, **kw: bad)
    monkeypatch.setattr(smoke, "mint_token_factory", lambda base, **kw: (lambda role: None))
    monkeypatch.setattr(signals, "_today", lambda: "2026-10-01")
    sch.platform_smoke_job()
    monkeypatch.setattr(signals, "_today", lambda: "2026-10-02")
    sch.platform_smoke_job()
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1
    assert tasks[0]["source_ref"] == f"platform_smoke:{signals._content_key(BROKEN[0])}"
