from __future__ import annotations

import sys

import scheduler as sch


def test_registers_mailing_jobs_including_hermes_bus(monkeypatch):
    captured = {}

    class CapturingScheduler(sch.BlockingScheduler):
        def start(self, *args, **kwargs):
            captured["ids"] = {job.id for job in self.get_jobs()}
            raise KeyboardInterrupt

    monkeypatch.setattr(sch, "BlockingScheduler", CapturingScheduler)
    monkeypatch.setattr(sys, "argv", ["scheduler.py"])
    sch.main()
    ids = captured["ids"]
    # 2026-10-27: 두 모드 모두 같은 잡 id 등록 — sender 가 동작을 고른다(agents/daily_mailing/sender.py).
    assert {"daily_mailing_dispatch", "daily_mailing_top_news", "daily_mailing_health",
            "daily_mailing_scope_export", "daily_mailing_runs_sync"} <= ids


def test_sender_switch_gates_server_and_bus_jobs(monkeypatch):
    calls = []
    monkeypatch.setattr("agents.daily_mailing.dispatch.dispatch_due_subscriptions",
                        lambda deps: calls.append("dispatch") or {})
    monkeypatch.setattr(sch, "_export_daily_mailing_scopes",
                        lambda publish=True: calls.append("export") or {"active": 3, "errors": []})
    monkeypatch.setattr(sch, "_sync_daily_mailing_runs",
                        lambda: calls.append("sync") or {"imported": 0, "errors": []})

    monkeypatch.setenv("DAILY_MAILING_SENDER", "hermes")
    assert sch.daily_mailing_dispatch_job()["counts"] == {"skipped_sender": 1}
    assert sch.daily_mailing_top_news_job()["counts"] == {"skipped_sender": 1}
    assert sch.daily_mailing_scope_export_job()["ok"] is True
    assert sch.daily_mailing_runs_sync_job()["ok"] is True
    assert calls == ["export", "sync"]

    calls.clear()
    monkeypatch.setenv("DAILY_MAILING_SENDER", "server")
    assert sch.daily_mailing_scope_export_job()["counts"] == {"skipped_sender": 1}
    assert sch.daily_mailing_runs_sync_job()["counts"] == {"skipped_sender": 1}
    assert calls == []


def test_sender_setting_defaults_and_env_override(tmp_path, monkeypatch):
    from agents.daily_mailing.sender import mailing_sender
    cfg = tmp_path / "settings.json"
    cfg.write_text('{"daily_mailing": {"sender": "hermes"}}', encoding="utf-8")
    monkeypatch.delenv("DAILY_MAILING_SENDER", raising=False)
    assert mailing_sender(cfg) == "hermes"
    cfg.write_text('{"daily_mailing": {}}', encoding="utf-8")
    assert mailing_sender(cfg) == "server"
    monkeypatch.setenv("DAILY_MAILING_SENDER", "HERMES")
    assert mailing_sender(cfg) == "hermes"
