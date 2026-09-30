from __future__ import annotations

import pytest

from agents.agent_tasks import runbooks


def test_registry_lists_known_runbooks():
    assert set(runbooks.list_runbooks()) == {
        "domestic_price_catchup", "daily_mailing_dispatch", "daily_mailing_top_news"}


def test_unknown_runbook_raises():
    with pytest.raises(runbooks.RunbookError):
        runbooks.run("does_not_exist")


def test_domestic_catchup_reports_ingested(monkeypatch):
    calls = {}

    def fake_run_sync(config, base_dir, max_pages=3, force=False):
        calls["force"] = force
        return {"ingested": [{"apply_date": "2026.09.01", "rows": 21980}], "errors": []}

    monkeypatch.setattr(runbooks, "_domestic_run_sync", fake_run_sync)
    out = runbooks.run("domestic_price_catchup")
    assert out["ok"] is True and calls["force"] is True
    assert "2026.09.01" in out["summary"]


def test_domestic_catchup_failure_is_not_ok(monkeypatch):
    monkeypatch.setattr(runbooks, "_domestic_run_sync",
                        lambda *a, **k: {"ingested": [], "errors": ["HIRA 게시판 로딩 실패"]})
    out = runbooks.run("domestic_price_catchup")
    assert out["ok"] is False and "HIRA" in out["summary"]


def test_daily_mailing_dispatch_runbook(monkeypatch):
    monkeypatch.setattr(runbooks, "_dispatch_due",
                        lambda deps: {"generated": [{"subscription_id": 14}], "errors": [], "retried": 1})
    monkeypatch.setattr(runbooks, "_dispatch_deps", lambda: object())
    out = runbooks.run("daily_mailing_dispatch")
    assert out["ok"] is True and "1건 생성" in out["summary"]


def test_top_news_runbook(monkeypatch):
    monkeypatch.setattr(runbooks, "_dispatch_deps", lambda: type("D", (), {
        "load_top_ma_news": staticmethod(lambda run_date, retry_failed=False: [1, 2])})())
    out = runbooks.run("daily_mailing_top_news")
    assert out["ok"] is True and "2건" in out["summary"]
