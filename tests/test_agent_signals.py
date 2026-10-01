from __future__ import annotations

from agents.agent_tasks.signals import record_health_problems, record_job_failure
from agents.agent_tasks.store import get_task, list_tasks


def test_job_failure_creates_task_with_runbook_hint(tmp_path):
    db = tmp_path / "a.db"
    tid = record_job_failure("domestic_price_pipeline", "HIRA 게시판 로딩 실패", db_path=db)
    t = get_task(tid, db_path=db)
    assert t["kind"] == "ops_incident" and t["source"] == "scheduler_job"
    assert t["source_ref"].startswith("domestic_price_pipeline:")
    assert "domestic_price_catchup" in t["detail"]      # 런북 힌트
    assert t["priority"] == 2


def test_repeated_failure_same_day_does_not_duplicate(tmp_path):
    db = tmp_path / "a.db"
    a = record_job_failure("domestic_price_pipeline", "오류1", db_path=db)
    b = record_job_failure("domestic_price_pipeline", "오류2", db_path=db)
    assert a == b
    assert len(list_tasks(db_path=db)) == 1


def test_health_problems_create_one_task_per_subscription(tmp_path):
    db = tmp_path / "a.db"
    report = {"run_date": "2026-09-30", "ok": False, "problems": [
        {"subscription_id": 14, "name": "약가 정책 모니터링", "problem": "not_generated"},
        {"subscription_id": 15, "name": "새 메일링 설정", "problem": "held", "detail": "llm_unavailable"},
        {"subscription_id": None, "name": "-", "problem": "gmail_token_invalid"}]}
    ids = record_health_problems(report, db_path=db)
    assert len(ids) == 3
    tasks = {t["source_ref"]: t for t in list_tasks(db_path=db)}
    assert "daily_mailing:2026-09-30:14:not_generated" in tasks
    assert tasks["daily_mailing:2026-09-30:14:not_generated"]["detail"].count("daily_mailing_dispatch") == 1
    gmail = tasks["daily_mailing:gmail_token_invalid"]          # 설정 문제 — 날짜 없는 ref
    assert gmail["priority"] == 1 and "런북 없음" in gmail["detail"]


def test_healthy_report_creates_nothing(tmp_path):
    db = tmp_path / "a.db"
    assert record_health_problems({"run_date": "2026-09-30", "ok": True, "problems": []}, db_path=db) == []
    assert list_tasks(db_path=db) == []


def test_missed_runs_collapse_into_one_task_per_day(tmp_path):
    from agents.agent_tasks.signals import record_job_missed
    from agents.agent_tasks.store import list_events
    db = tmp_path / "a.db"
    a = record_job_missed("competitor_news_daily", "2026-09-30 06:30:00+09:00", db_path=db)
    b = record_job_missed("competitor_trend_promote", "2026-09-30 07:00:00+09:00", db_path=db)
    assert a == b
    assert [e["event"] for e in list_events(a, db_path=db)] == ["created", "missed", "missed"]


def test_job_runbook_covers_none_for_unmapped_jobs():
    from agents.agent_tasks.signals import JOB_RUNBOOK
    assert JOB_RUNBOOK["reimb_data_sync"] is None and JOB_RUNBOOK["domestic_price_pipeline"] == "domestic_price_catchup"
