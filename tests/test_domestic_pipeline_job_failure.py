"""국내 약가 잡 — catch-up 전체 실패는 잡 실패(ok=False)다 (2026-09-01 '건강해 보이는 실패' 재현).

run_pipeline 이 전체 실패를 원장 과제로만 남기고 ok=True 를 돌려 scheduler_job_run.ok=1 이 기록됐고 /api/health 의
jobs_failed_24h 에 domestic_price_pipeline 이 끝내 뜨지 않았다. 고친 뒤: ok=False + errors → guarded_job 이 실행 기록(ok=0)
과 실패 신호를 남기고 /api/health 가 잡을 나열한다. 신호는 record_recurring 이 (source, source_ref) 로 dedup 하므로
과제는 1건이어야 한다 — 두 번 기록되지 않음을 증명한다.
네트워크·브라우저·실제 DB 금지: catch_up / DashboardAgent 를 가짜로, 원장은 conftest 의 임시 AGENT_TASK_DB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scheduler as sched  # noqa: E402
from agents.agent_tasks.jobs import recent_job_runs, resolve_db_path  # noqa: E402
from agents.agent_tasks.store import list_tasks  # noqa: E402

TOTAL_FAIL = {"ingested": [], "errors": ["HIRA 게시판 로딩 실패 (page 1..2 모두 타임아웃)"], "skipped": None,
              "latest_db_before": "2026.08.01"}


class _FakeDashboard:
    def __init__(self, config, base_dir):
        pass

    def run(self):
        return Path("/tmp/dashboard.html")


@pytest.fixture
def pipeline(monkeypatch):
    import agents.domestic_catchup as dc
    monkeypatch.setattr(sched, "load_config", lambda: {})
    monkeypatch.setattr(sched, "DashboardAgent", _FakeDashboard)
    state = {"result": TOTAL_FAIL, "raise": None}

    async def fake_catch_up(config, base_dir, **kw):
        if state["raise"]:
            raise state["raise"]
        return state["result"]

    monkeypatch.setattr(dc, "catch_up", fake_catch_up)
    return state


def _ledger_job_tasks() -> list[dict]:
    return [t for t in list_tasks(db_path=resolve_db_path(), statuses=("new", "triaged", "in_progress", "blocked"))
            if t["source"] == "scheduler_job" and str(t["source_ref"]).startswith("domestic_price_pipeline:")]


def test_total_catchup_failure_is_a_job_failure(pipeline):
    res = sched.job()
    assert res["ok"] is False
    assert any("HIRA 게시판 로딩 실패" in e for e in res["errors"])
    runs = {r["job_id"]: r for r in recent_job_runs(hours=24)}
    assert runs["domestic_price_pipeline"]["ok"] == 0


def test_total_failure_records_exactly_one_task(pipeline):
    sched.job()
    tasks = _ledger_job_tasks()
    assert len(tasks) == 1, [t["source_ref"] for t in tasks]
    # 다음 날 재시도(1~7일 일일 재시도)에서도 같은 행 — 재발 이벤트만 쌓인다
    sched.job()
    assert len(_ledger_job_tasks()) == 1


def test_health_lists_failed_domestic_job(pipeline, monkeypatch):
    import api.server as s
    monkeypatch.setattr(s, "_health_db_ok", lambda: True)
    sched.job()
    body = s.app.test_client().get("/api/health").get_json()
    assert "domestic_price_pipeline" in body["jobs_failed_24h"]


def test_partial_errors_with_ingest_still_ok(pipeline):
    pipeline["result"] = {"ingested": ["2026.09.01"], "errors": ["2026.10.01 파싱 실패"], "skipped": None}
    res = sched.job()
    assert res["ok"] is True and res["counts"]["ingested"] == 1
    assert _ledger_job_tasks() == []


def test_skip_when_already_loaded_is_ok(pipeline):
    pipeline["result"] = {"ingested": [], "errors": [], "skipped": "already_loaded"}
    res = sched.job()
    assert res["ok"] is True and _ledger_job_tasks() == []


def test_catchup_exception_is_a_job_failure_and_dashboard_still_runs(pipeline):
    pipeline["raise"] = RuntimeError("proxy down")
    res = sched.job()
    assert res["ok"] is False and any("proxy down" in e for e in res["errors"])
    assert res["counts"].get("dashboard")                      # 적재 실패해도 대쉬보드는 기존 데이터로 갱신
    assert len(_ledger_job_tasks()) == 1
