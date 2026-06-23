"""허가사항 용법용량 → 구조화 dosing + 치료비 계산 검증 (regex 경로, LLM 미사용).

실행: PYTHONPATH=. .venv/bin/python tests/test_dosing_resolver.py
"""
from __future__ import annotations

from pathlib import Path

from agents.db import DrugPriceDB
from agents.dosing_resolver import resolve_dosing, _canonical_daily_mg
from agents.scrapers.health_kr_dose_parser import parse_dose_schedule, parse_weight_bsa


def test_parse_orderings():
    assert parse_dose_schedule("성인 1일 1회 100mg을 투여")["daily_dose_mg"] == 100.0
    r = parse_dose_schedule("1회 100mg을 1일 2회 경구투여")
    assert r["daily_dose_units"] == 2.0 and r["daily_dose_mg"] == 200.0
    assert parse_dose_schedule("200mg을 매 3주마다 정맥투여")["cycle_days"] == 21
    assert parse_dose_schedule("주 1회 피하 투여")["cycle_days"] == 7
    assert parse_dose_schedule("격일 1회 투여")["cycle_days"] == 2


def test_weight_bsa():
    wb = parse_weight_bsa("초회 4mg/kg, 이후 2mg/kg을 매주 투여")
    assert wb["per_kg_mg"] == 2.0 and wb["interval_days"] == 7   # maintenance 우선
    wb2 = parse_weight_bsa("1,400 mg/m²를 2주마다 투여")
    assert wb2.get("per_m2_mg") == 1400.0 and wb2["interval_days"] == 14


def test_canonical_daily_mg():
    # continuous mg
    assert _canonical_daily_mg({"schedule": "continuous", "daily_dose_mg": 100}, "") == 100
    # cycle: 200mg/21d
    v = _canonical_daily_mg({"schedule": "cycle", "cycle_days": 21, "doses_per_cycle": 1}, "200mg 매 3주")
    assert abs(v - 200 / 21) < 1e-6
    # 체중: 2mg/kg×60/7
    v2 = _canonical_daily_mg({"schedule": "cycle", "cycle_days": 7, "per_kg_mg": 2.0}, "")
    assert abs(v2 - 2 * 60 / 7) < 1e-6


def test_resolve_regex_and_cache(tmp_path=None):
    db = DrugPriceDB(Path("data/db/drug_prices.db"))
    import sqlite3
    sqlite3.connect("data/db/drug_prices.db").execute(
        "DELETE FROM dosing_resolved WHERE cache_key LIKE 'TEST_%'").connection.commit()

    r = resolve_dosing("성인 1일 1회 100mg을 경구투여", cache_key="TEST_sita", db=db, use_llm=False)
    assert r["schedule"] == "continuous" and r["daily_dose_mg"] == 100.0 and r["source"] == "regex"
    assert r["confidence"] == "high"

    r2 = resolve_dosing("본제 200mg을 매 3주마다 투여", cache_key="TEST_kt", db=db, use_llm=False)
    assert r2["schedule"] == "cycle" and r2["cycle_days"] == 21 and r2["confidence"] == "medium"

    # 캐시 hit (다른 usage_text 줘도 캐시 우선)
    cached = resolve_dosing("ignored", cache_key="TEST_sita", db=db, use_llm=False)
    assert cached["daily_dose_mg"] == 100.0

    # 미상
    none = resolve_dosing("효능효과만 기술됨", cache_key="TEST_none", db=db, use_llm=False)
    assert none["schedule"] is None and none["confidence"] == "low"


if __name__ == "__main__":
    test_parse_orderings()
    test_weight_bsa()
    test_canonical_daily_mg()
    test_resolve_regex_and_cache()
    print("OK — dosing resolver tests passed")
