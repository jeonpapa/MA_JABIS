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
