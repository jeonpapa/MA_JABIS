"""수동 전용 잡 exchange_rate_recompute — 실패를 삼키지 않는다 (P1.5 검증 결함 A).

재현됐던 결함: 잡이 예외를 logger.exception 으로 삼키고 None 을 돌려줘 normalize_result 가 ok=True 로 만들었다. 런북
exchange_rate_recompute 가 거짓 '복구 완료' 를 내고 디스패처가 foreign_price_fx_alignment 신선도 과제를 아무것도 재계산하지
않은 채 닫았다. 수동 전용(스케줄 미등록)이라 JOB_RUNBOOK 에 넣을 수 없어(키 집합 == 등록 잡 id 집합 테스트) 런북 힌트도 없었다.
실제 DB·환율 캐시·Playwright 에 닿지 않는다 — fetcher 와 DrugPriceDB 를 가짜로 바꾼다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import scheduler as sch
from agents.agent_tasks import runbooks
from agents.agent_tasks.signals import JOB_RUNBOOK, MANUAL_JOB_RUNBOOK, job_runbook
from agents.agent_tasks.store import list_tasks


class _NoDb:
    def __init__(self, *a, **k):
        raise AssertionError("캐시가 없거나 로드가 실패하면 DrugPriceDB 를 열면 안 된다")


@pytest.fixture
def ledger() -> Path:
    return Path(os.environ["AGENT_TASK_DB"])          # conftest 가 임시 파일로 돌린다


def test_recompute_exception_is_a_failure_with_runbook_hint(monkeypatch, ledger):
    class BoomFetcher:
        def _load_latest_cache(self):
            raise RuntimeError("xlsx 손상")
    monkeypatch.setattr("agents.exchange_rate.ExchangeRateFetcher", BoomFetcher)
    monkeypatch.setattr("agents.db.DrugPriceDB", _NoDb)
    res = sch.exchange_rate_recompute_job()
    assert res["ok"] is False and res["errors"] == ["RuntimeError: xlsx 손상"]
    tasks = list_tasks(db_path=ledger)
    assert len(tasks) == 1 and tasks[0]["source_ref"].startswith("exchange_rate_recompute:")
    assert "런북: exchange_rate_recompute" in tasks[0]["detail"]          # 수동 전용 잡도 런북 힌트를 가진다
    # 런북 경로(_from_job)도 같은 잡을 부르므로 거짓 '복구 완료' 가 아니라 실패다
    out = runbooks.run("exchange_rate_recompute")
    assert out["ok"] is False and "실패" in out["summary"] and "RuntimeError" in out["summary"]


def test_recompute_without_cache_is_a_failure_not_a_silent_return(monkeypatch, ledger):
    class EmptyFetcher:
        def _load_latest_cache(self):
            return False
    monkeypatch.setattr("agents.exchange_rate.ExchangeRateFetcher", EmptyFetcher)
    monkeypatch.setattr("agents.db.DrugPriceDB", _NoDb)
    res = sch.exchange_rate_recompute_job()
    assert res["ok"] is False and len(res["errors"]) == 1 and "환율 캐시 없음" in res["errors"][0]
    assert "exchange_rate_refresh" in res["errors"][0]                    # 사람에게 선행 런북을 알려준다
    assert len(list_tasks(db_path=ledger)) == 1


def test_recompute_success_reports_counts(monkeypatch, ledger):
    class Fetcher:
        _rates = {"USD": 1380.0}
        _rate_meta = {"from": "20230901", "to": "20260831"}

        def _load_latest_cache(self):
            return True

    class Db:
        def __init__(self, path):
            self.path = path

        def recompute_foreign_fx(self, rates, meta):
            assert rates == {"USD": 1380.0} and meta["to"] == "20260831"
            return {"candidates": 12, "updated": 9, "skipped": 3}
    monkeypatch.setattr("agents.exchange_rate.ExchangeRateFetcher", Fetcher)
    monkeypatch.setattr("agents.db.DrugPriceDB", Db)
    res = sch.exchange_rate_recompute_job()
    assert res["ok"] is True and res["errors"] == []
    assert res["counts"] == {"candidates": 12, "updated": 9, "skipped": 3, "to": "20260831"}
    assert list_tasks(db_path=ledger) == []
    assert runbooks.run("exchange_rate_recompute")["ok"] is True


def test_manual_job_runbook_is_separate_from_scheduled_map():
    """수동 전용 잡의 런북은 MANUAL_JOB_RUNBOOK — JOB_RUNBOOK 키 집합(== 등록 잡 id)을 건드리지 않는다."""
    assert MANUAL_JOB_RUNBOOK == {"exchange_rate_recompute": "exchange_rate_recompute"}
    assert set(MANUAL_JOB_RUNBOOK).isdisjoint(JOB_RUNBOOK)
    assert {v for v in MANUAL_JOB_RUNBOOK.values() if v} <= set(runbooks.list_runbooks())
    assert job_runbook("exchange_rate_recompute") == "exchange_rate_recompute"
    assert job_runbook("domestic_price_pipeline") == "domestic_price_catchup"     # 등록 잡은 JOB_RUNBOOK 우선
    assert job_runbook("git_auto_backup") is None and job_runbook("no_such_job") is None
