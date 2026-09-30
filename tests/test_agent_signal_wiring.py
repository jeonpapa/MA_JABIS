from __future__ import annotations

import scheduler as sch


def test_health_job_records_problems(monkeypatch, tmp_path):
    recorded = {}
    monkeypatch.setattr(sch, "_agent_record_health_problems",
                        lambda report: recorded.setdefault("report", report))

    class FakeDeps:
        @classmethod
        def production(cls):
            return cls()

    monkeypatch.setattr("agents.daily_mailing.dispatch.DispatchDeps", FakeDeps)
    monkeypatch.setattr("agents.daily_mailing.dispatch.health_check",
                        lambda deps: {"ok": False, "run_date": "2026-09-30",
                                      "problems": [{"subscription_id": 14, "name": "n", "problem": "held"}]})
    sch.daily_mailing_health_job()
    assert recorded["report"]["problems"][0]["problem"] == "held"


def test_health_job_ok_records_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(sch, "_agent_record_health_problems", lambda report: called.append(report))

    class FakeDeps:
        @classmethod
        def production(cls):
            return cls()

    monkeypatch.setattr("agents.daily_mailing.dispatch.DispatchDeps", FakeDeps)
    monkeypatch.setattr("agents.daily_mailing.dispatch.health_check",
                        lambda deps: {"ok": True, "run_date": "2026-09-30", "problems": []})
    sch.daily_mailing_health_job()
    assert called == []


def test_signal_failure_does_not_break_the_job(monkeypatch):
    def boom(report):
        raise RuntimeError("DB 잠김")

    monkeypatch.setattr(sch, "_agent_record_health_problems", boom)

    class FakeDeps:
        @classmethod
        def production(cls):
            return cls()

    monkeypatch.setattr("agents.daily_mailing.dispatch.DispatchDeps", FakeDeps)
    monkeypatch.setattr("agents.daily_mailing.dispatch.health_check",
                        lambda deps: {"ok": False, "run_date": "2026-09-30",
                                      "problems": [{"subscription_id": 14, "name": "n", "problem": "held"}]})
    sch.daily_mailing_health_job()          # 예외가 밖으로 나오면 실패


# ── 국내 약가 파이프라인: 캐치업 자체가 예외로 죽어도 신호를 남긴다 ───────────────────

def _run_pipeline_with_catchup_raising(monkeypatch, record_failure):
    import asyncio
    import types

    async def boom(config, base_dir):
        raise RuntimeError("HIRA 게시판 timeout")

    fake_catchup = types.ModuleType("agents.domestic_catchup")
    fake_catchup.catch_up = boom
    monkeypatch.setitem(__import__("sys").modules, "agents.domestic_catchup", fake_catchup)
    monkeypatch.setattr(sch, "load_config", lambda: {})
    monkeypatch.setattr(sch, "_agent_record_job_failure", record_failure)

    class FakeDashboard:
        def __init__(self, config, base_dir):
            pass

        def run(self):
            from pathlib import Path
            return Path("/tmp/dashboard.html")

    monkeypatch.setattr(sch, "DashboardAgent", FakeDashboard)
    asyncio.run(sch.run_pipeline())


def test_pipeline_exception_branch_records_job_failure(monkeypatch):
    recorded = []
    _run_pipeline_with_catchup_raising(monkeypatch, lambda job_id, err: recorded.append((job_id, err)))
    assert recorded == [("domestic_price_pipeline", "RuntimeError: HIRA 게시판 timeout")]


def test_pipeline_exception_branch_signal_failure_does_not_break_the_job(monkeypatch):
    def boom_signal(job_id, err):
        raise RuntimeError("DB 잠김")

    _run_pipeline_with_catchup_raising(monkeypatch, boom_signal)   # 예외가 밖으로 나오면 실패
