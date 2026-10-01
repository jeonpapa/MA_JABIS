"""스케줄러 등록부가 데이터(JobSpec)로 바뀌었고, 모든 잡이 guarded 이며 misfire 정책과 리스너가 걸려 있는지."""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

import scheduler as sch
from agents.agent_tasks.signals import JOB_RUNBOOK
from agents.agent_tasks.store import list_events, list_tasks

SCHED_CFG = {"day": "1-7", "hour": 9, "minute": 0}


def test_every_job_is_guarded_with_matching_id():
    for spec in sch.build_job_specs(SCHED_CFG):
        assert getattr(spec.fn, "__job_id__", None) == spec.id, spec.id


def test_job_ids_match_runbook_map_exactly():
    ids = {s.id for s in sch.build_job_specs(SCHED_CFG)}
    assert ids == set(JOB_RUNBOOK)


def test_misfire_policy_is_explicit_for_every_job():
    specs = {s.id: s for s in sch.build_job_specs(SCHED_CFG)}
    assert specs["domestic_price_pipeline"].misfire_grace_time == 86400          # 기존 정책 유지
    assert specs["daily_mailing_dispatch"].misfire_grace_time == sch.INTERVAL_GRACE
    assert specs["reimbursement_xnational_sync"].misfire_grace_time == sch.PERIODIC_GRACE
    for s in specs.values():
        assert s.coalesce is True and s.misfire_grace_time >= 30 and s.max_instances == 1


def test_main_registers_listener_and_all_specs(monkeypatch):
    captured = {}

    class CapturingScheduler(sch.BlockingScheduler):
        def add_listener(self, callback, mask=None):
            captured["mask"] = mask
            captured["callback"] = callback
            return super().add_listener(callback, mask)

        def start(self, *args, **kwargs):
            captured["ids"] = {job.id for job in self.get_jobs()}
            raise KeyboardInterrupt

    monkeypatch.setattr(sch, "BlockingScheduler", CapturingScheduler)
    monkeypatch.setattr(sys, "argv", ["scheduler.py"])
    sch.main()
    assert captured["callback"] is sch._on_job_event
    assert captured["mask"] & EVENT_JOB_ERROR and captured["mask"] & EVENT_JOB_MISSED
    assert captured["ids"] == {s.id for s in sch.build_job_specs(SCHED_CFG)}


class _Event:
    def __init__(self, code, job_id, exception=None, scheduled_run_time=None):
        self.code, self.job_id, self.exception = code, job_id, exception
        self.scheduled_run_time = scheduled_run_time


def test_missed_event_records_one_task_with_events(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    now = datetime.now(timezone.utc)
    sch._on_job_event(_Event(EVENT_JOB_MISSED, "competitor_news_daily", scheduled_run_time=now))
    sch._on_job_event(_Event(EVENT_JOB_MISSED, "competitor_trend_promote", scheduled_run_time=now))
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source"] == "scheduler_missed"
    assert [e["event"] for e in list_events(tasks[0]["id"], db_path=db)] == ["created", "missed", "missed"]


def test_error_event_records_failure_without_double_task(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    sch._on_job_event(_Event(EVENT_JOB_ERROR, "nhis_negotiation_sync", exception=RuntimeError("boom")))
    sch._on_job_event(_Event(EVENT_JOB_ERROR, "nhis_negotiation_sync", exception=RuntimeError("boom again")))
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"].startswith("nhis_negotiation_sync:")
    assert "RuntimeError: boom" in tasks[0]["detail"]


def test_listener_swallows_signal_errors(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("DB 잠김")
    monkeypatch.setattr("agents.agent_tasks.signals.record_job_missed", boom)
    sch._on_job_event(_Event(EVENT_JOB_MISSED, "x", scheduled_run_time="t"))   # 예외가 밖으로 나오면 실패


def _install_fake_news_modules(monkeypatch, cn, gpn):
    """`from agents import x` 는 sys.modules 보다 패키지 속성을 먼저 보므로(앞선 테스트가 api.server 를 import 하면
    실제 모듈이 속성으로 남는다) 둘 다 바꿔야 실행 순서와 무관하게 가짜가 잡힌다."""
    import agents
    monkeypatch.setitem(sys.modules, "agents.competitor_news_agent", cn)
    monkeypatch.setitem(sys.modules, "agents.gov_policy_news", gpn)
    monkeypatch.setattr(agents, "competitor_news_agent", cn, raising=False)
    monkeypatch.setattr(agents, "gov_policy_news", gpn, raising=False)


def test_converted_jobs_return_standard_dict_and_propagate_errors(monkeypatch, tmp_path):
    """뉴스 매일 잡: 두 크롤 중 하나가 죽어도 다른 하나는 돌고, 실패는 errors 로 반환된다."""
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    import types
    cn = types.ModuleType("agents.competitor_news_agent")
    cn.DEFAULT_LOOKBACK_DAYS = 183
    cn.crawl = lambda **kw: {"total_stored": 4}
    gpn = types.ModuleType("agents.gov_policy_news")
    gpn.DEFAULT_LOOKBACK_DAYS = 31

    def gov_boom(**kw):
        raise RuntimeError("Naver 5xx")
    gpn.crawl = gov_boom
    _install_fake_news_modules(monkeypatch, cn, gpn)
    res = sch.competitor_news_daily_job()
    assert res["ok"] is False and res["counts"] == {"competitor": 4} and res["errors"] == ["gov_policy: RuntimeError: Naver 5xx"]
    assert list_tasks(db_path=db)[0]["source_ref"].startswith("competitor_news_daily:")


def test_naver_key_missing_is_config_class(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    import types
    cn = types.ModuleType("agents.competitor_news_agent")
    cn.DEFAULT_LOOKBACK_DAYS = 183
    cn.crawl = lambda **kw: {"error": "Naver API 키 미설정", "results": []}
    gpn = types.ModuleType("agents.gov_policy_news")
    gpn.DEFAULT_LOOKBACK_DAYS = 31
    gpn.crawl = lambda **kw: {"total_stored": 0}
    _install_fake_news_modules(monkeypatch, cn, gpn)
    res = sch.competitor_news_daily_job()
    assert res["ok"] is False and res["error_class"] == "config"
    assert list_tasks(db_path=db)[0]["source_ref"] == "competitor_news_daily:config"
