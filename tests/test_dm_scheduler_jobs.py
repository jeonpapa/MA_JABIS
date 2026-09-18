from __future__ import annotations

import sys

import scheduler as sch


def test_registers_mailing_jobs_and_retires_bus(monkeypatch):
    captured = {}

    class CapturingScheduler(sch.BlockingScheduler):
        def start(self, *args, **kwargs):
            captured["ids"] = {job.id for job in self.get_jobs()}
            raise KeyboardInterrupt

    monkeypatch.setattr(sch, "BlockingScheduler", CapturingScheduler)
    monkeypatch.setattr(sys, "argv", ["scheduler.py"])
    sch.main()
    ids = captured["ids"]
    assert {"daily_mailing_dispatch", "daily_mailing_top_news", "daily_mailing_health"} <= ids
    assert "daily_mailing_scope_export" not in ids
    assert "daily_mailing_runs_sync" not in ids
