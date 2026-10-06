"""ForeignPrice 백필 잡은 guarded(실패 → 원장)이되 **스케줄에는 없다** (P1.6 관측 슬라이스).

감사: foreign_price_backfill_job 은 guarded_job 도 등록도 없이 예외를 logger.exception 으로 삼켰다 — 수동 실행이 실패해도
원장에 아무것도 남지 않았다. identity 라우팅·IT 채널 문제가 풀리기 전까지 켜지 않는다는 합의는 유지한다(build_job_specs 미등록).
네트워크·실제 스크레이퍼·실제 DB 금지: ForeignPriceAgent 와 DrugPriceDB 를 가짜로 바꾼다.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

import scheduler as sch
from agents.agent_tasks.jobs import recent_job_runs
from agents.agent_tasks.signals import JOB_RUNBOOK, MANUAL_JOB_RUNBOOK, job_runbook
from agents.agent_tasks.store import list_tasks

SCHED_CFG = {"day": "1-7", "hour": 9, "minute": 0}


class _Conn:
    def execute(self, sql, *a):
        return self

    def fetchall(self):
        return [("keytruda",), ("welireg",)]


class _FakeDB:
    def __init__(self, path):
        self.path = path

    def _connect(self):
        return contextlib.nullcontext(_Conn())

    def get_product_alias(self, slug):
        return {"inn": "pembrolizumab"} if slug == "keytruda" else None


def _fake_agent(monkeypatch, fail_slug: str | None = None):
    calls: list[str] = []

    class _Agent:
        def __init__(self, base_dir):
            pass

        async def search_all(self, query, countries=None, reference_date=None, identity=None):
            calls.append(query)
            if query == fail_slug:
                raise RuntimeError("IT 채널 SSL 오류")
            return {"JP": [{"local_price": 1}], "US": []}
    import agents.db
    import agents.foreign_price_agent
    monkeypatch.setattr(agents.db, "DrugPriceDB", _FakeDB)
    monkeypatch.setattr(agents.foreign_price_agent, "ForeignPriceAgent", _Agent)
    return calls


def test_backfill_is_guarded_but_not_scheduled():
    assert getattr(sch.foreign_price_backfill_job, "__job_id__", None) == "foreign_price_backfill"
    assert "foreign_price_backfill" not in {s.id for s in sch.build_job_specs(SCHED_CFG)}     # 켜지 않는다
    assert "foreign_price_backfill" not in JOB_RUNBOOK                                       # 등록 잡 집합과 분리
    assert "foreign_price_backfill" in MANUAL_JOB_RUNBOOK and job_runbook("foreign_price_backfill") is None


def test_backfill_returns_job_contract_and_queries_inn_first(monkeypatch):
    calls = _fake_agent(monkeypatch)
    res = sch.foreign_price_backfill_job()
    assert res["ok"] is True and res["errors"] == []
    assert res["counts"]["products"] == 2 and res["counts"]["ok"] == 2 and res["counts"]["fail"] == 0
    assert calls == ["pembrolizumab", "welireg"]                                              # alias inn 우선, 없으면 slug


def test_backfill_failure_reaches_the_ledger(monkeypatch):
    db = Path(os.environ["AGENT_TASK_DB"])
    _fake_agent(monkeypatch, fail_slug="welireg")
    res = sch.foreign_price_backfill_job()
    assert res["ok"] is False and res["counts"] == {"products": 2, "ok": 1, "fail": 1, "country_errors": 0}
    assert res["errors"] == ["welireg: RuntimeError: IT 채널 SSL 오류"]
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"].startswith("foreign_price_backfill:") and "런북 없음" in tasks[0]["detail"]
    runs = [r for r in recent_job_runs(db_path=db) if r["job_id"] == "foreign_price_backfill"]
    assert len(runs) == 1 and runs[0]["ok"] == 0


def test_backfill_exception_is_swallowed_into_the_ledger(monkeypatch):
    db = Path(os.environ["AGENT_TASK_DB"])
    import agents.db

    class _Boom(_FakeDB):
        def _connect(self):
            raise RuntimeError("DB 잠김")
    monkeypatch.setattr(agents.db, "DrugPriceDB", _Boom)
    res = sch.foreign_price_backfill_job()                                                    # 예외가 밖으로 나오지 않는다
    assert res["ok"] is False and "RuntimeError: DB 잠김" in res["errors"][0]
    assert list_tasks(db_path=db)[0]["source_ref"].startswith("foreign_price_backfill:")
