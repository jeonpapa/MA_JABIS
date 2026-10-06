"""QG 해외약가 데이터 검사가 **매일 실제로 도는 경로**에 붙어 있는지 — review_codebase → record_quality_review.

2026-10 감사: check_db_records / validate_keytruda 는 어디서도 호출되지 않는 죽은 안전장치였다(agents.pipeline 만
언급했고 그 모듈은 아무도 import 하지 않는다). 06:00 잡(scheduler.quality_guard_review_job) 이 부르는 것은
QualityGuardAgent.review_codebase() 뿐이고 그 결과가 _agent_record_quality_review → signals.record_quality_review 로
원장에 간다. 그래서 데이터 검사는 review_codebase 안에서 돌고, 결과는 record_quality_review 가 이미 읽는
rule_drifts({rule, file, severity}) 로 들어간다.

실행: .venv/bin/python -m pytest tests/test_quality_guard_data_check_wiring.py -q
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.quality_guard import agent as qg_agent
from agents.quality_guard import checks as qg_checks
from agents.quality_guard import deviations


FR_1504 = {
    "searched_at": "2026-07-28T10:05:00", "query_name": "keytruda", "country": "FR", "currency": "EUR",
    "product_name": "KEYTRUDA 25 mg/ml sol diluer p perf", "dosage_strength": "25 mg",
    "dosage_form": "solution pour perfusion", "package_unit": "", "ingredient": "", "local_price": 2000.74,
    "exchange_rate": 1544.43, "exchange_rate_from": "20230603", "exchange_rate_to": "20260531",
    "factory_price_krw": 2379302, "vat_rate": 0.1, "distribution_margin": 0.0869, "adjusted_price_krw": 2844669,
    "pack_count": 1, "per_unit_local": 2000.74, "form_type": "injection", "unit_strength_mg": 25.0,
    "total_pkg_mg": 25.0, "daily_cost_krw": 1083683, "dose_norm_factor": 4.0,
    "adjusted_price_krw_normalized": 11378676, "source_url": "", "source_label": "VIDAL.fr", "raw_data": None,
}
CH_UNVERIFIED = {
    **FR_1504, "query_name": "sitagliptin", "country": "CH", "currency": "CHF",
    "product_name": "SITAGLIPTIN NOBEL Filmtabl 100 mg", "dosage_strength": "SITAGLIPTIN NOBEL Filmtabl 100 mg",
    "dosage_form": "", "local_price": 31.75, "exchange_rate": 1700.0, "adjusted_price_krw": 47326,
    "form_type": "oral", "unit_strength_mg": 100.0, "total_pkg_mg": 100.0, "daily_cost_krw": None,
    "dose_norm_factor": 1.0, "adjusted_price_krw_normalized": 47326, "pack_count_source": "default",
}


@pytest.fixture
def guard(tmp_path, monkeypatch):
    """리뷰 산출물·편차 로그는 임시 경로. 코드 스캔/MFDS/제안은 비워 데이터 검사 배선만 본다 (제안의 DocCheck 는 네트워크)."""
    qdir = tmp_path / "qg"
    qdir.mkdir()
    for mod in (deviations, qg_agent):
        monkeypatch.setattr(mod, "GUARD_DIR", qdir)
        monkeypatch.setattr(mod, "DEVIATION_LOG", qdir / "deviation_log.jsonl")
    monkeypatch.setattr(qg_agent.QualityGuardAgent, "scan_codebase", lambda self, target_dir=None: [])
    monkeypatch.setattr(qg_agent.QualityGuardAgent, "scan_rule_drift", lambda self: [])
    monkeypatch.setattr(qg_agent.QualityGuardAgent, "check_mfds_baseline", lambda self: [])
    monkeypatch.setattr(qg_agent.QualityGuardAgent, "generate_suggestions", lambda self: [])
    from agents.db import DrugPriceDB
    db = tmp_path / "p.db"
    d = DrugPriceDB(db)
    d.save_foreign_price(FR_1504)
    d.save_foreign_price(CH_UNVERIFIED)
    g = qg_agent.QualityGuardAgent()
    g.db_path = db
    return g


def test_dead_safeguards_are_gone():
    assert not hasattr(qg_checks, "check_db_records") and not hasattr(qg_checks, "validate_keytruda")
    assert not hasattr(qg_agent.QualityGuardAgent, "validate_db_records")
    assert not hasattr(qg_agent.QualityGuardAgent, "run_keytruda_validation")
    import agents.quality_guard as pkg
    assert "check_db_records" not in pkg.__all__ and "check_foreign_price_data" in pkg.__all__


def test_review_codebase_runs_data_check_and_lands_in_rule_drifts(guard, tmp_path):
    result = guard.review_codebase()
    data = [d for d in result["rule_drifts"] if d.get("kind") == "data"]
    assert len(data) == 1 and data[0]["check"] == "injection_concentration_as_unit" and data[0]["severity"] == "ERROR"
    assert data[0]["country"] == "FR" and "foreign_drug_prices id=" in data[0]["file"]
    assert result["data_issues"] == data                                  # 같은 항목을 이름 있는 키로도 노출
    # pack_count 미확인 요약은 제안(🧮 그룹) 으로 — record_quality_review 가 improvement 로 묶는다
    assert any(s.startswith("🧮") and "CH sitagliptin 1" in s for s in result["suggestions"])
    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "해외약가 데이터 검사" in report and "id=" in report
    assert "injection_concentration_as_unit" in (tmp_path / "qg" / "deviation_log.jsonl").read_text(encoding="utf-8")


def test_data_findings_become_ledger_incidents(guard, tmp_path):
    """record_quality_review 가 바꾸지 않은 채 데이터 drift 를 ops_incident 로 — 같은 (국가, 약제) 는 다음 날도 1건."""
    from agents.agent_tasks.signals import record_quality_review
    from agents.agent_tasks.store import list_tasks
    ledger = tmp_path / "ledger.db"
    result = guard.review_codebase()
    ids = record_quality_review(result, db_path=ledger)
    assert len(ids["incident"]) == 1
    tasks = list_tasks(db_path=ledger)
    inc = next(t for t in tasks if t["kind"] == "ops_incident")
    assert "§최소단위" in inc["title"] and "id=" in inc["detail"] and inc["priority"] == 2
    again = record_quality_review(guard.review_codebase(), db_path=ledger)
    assert again["incident"] == ids["incident"]
    assert sum(1 for t in list_tasks(db_path=ledger) if t["kind"] == "ops_incident") == 1
    assert any(t["source_ref"] == "qg:suggestion:🧮" for t in list_tasks(db_path=ledger))


def test_scheduler_job_calls_review_codebase_and_records():
    """06:00 잡의 호출 경로가 바뀌면 이 배선도 끊긴다 — 소스에서 고정."""
    import scheduler
    src = inspect.getsource(scheduler.quality_guard_review_job)
    assert "review_codebase()" in src and "_agent_record_quality_review(result)" in src
    assert "check_foreign_price_data(" in inspect.getsource(qg_agent.QualityGuardAgent.review_codebase)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
