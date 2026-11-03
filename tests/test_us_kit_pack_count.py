"""Winrevair US 2-vial 키트 (2026-11-03).

Red Book 은 키트 포장을 'ea' 로만 적어 바이알 수 단서가 없다 → 기본값 1 → 2-vial 키트 WAC($30,596.36)가 1 바이알 가격으로
잡혀 A8 최고가·평균이 부풀었다. FDA 라벨 Table 7: 0006-5087-01 = 45 mg ×2, 0006-5088-01 = 60 mg ×2.
1) 라벨로 확인한 제품 코드는 pack_count=2 (source=label) 로 읽기 경로에서 다시 결정한다.
2) 라벨 목록에 없는 주사 default 행이 같은 제형 다른 포장 가격의 정수배면 평균에서 뺀다(카드 숫자는 남김).
"""
import json

from agents.foreign_price_agent import ForeignPriceAgent as A, flag_injection_pack_multiples


def _row(ndc, price, **kw):
    base = {"searched_at": "2026-10-12T13:23:52", "query_name": "winrevair", "country": "US", "currency": "USD",
            "product_name": "WINREVAIR", "ingredient": "", "dosage_strength": "45 mg", "dosage_form": "",
            "package_unit": "ea", "local_price": price, "exchange_rate": 1405.63, "exchange_rate_from": "20231003",
            "exchange_rate_to": "20260930", "factory_price_krw": 1, "vat_rate": 0.1, "distribution_margin": 0.0869,
            "adjusted_price_krw": 1, "source_url": "", "source_label": "Micromedex Red Book (WAC)",
            "raw_data": json.dumps({"source_type": "redbook_wac", "ndc": ndc}), "form_type": "injection",
            "formulation_key": "45mg|injection", "pack_count": 1}
    return {**base, **kw}


def _agent(tmp_path, rows):
    from agents.db import DrugPriceDB
    from agents.exchange_rate import PriceCalculator
    d = DrugPriceDB(tmp_path / "u.db")
    for r in rows:
        d.save_foreign_price(r)
    a = A.__new__(A)
    a.db = d
    a.calculator = PriceCalculator()
    a._build_scraper = lambda country: type("S", (), {"SOURCE_TYPE": None})()
    a._assign_formulations_and_persist = lambda *args, **kw: None
    return a


def test_label_confirmed_two_vial_kit_gets_per_vial_price(tmp_path):
    a = _agent(tmp_path, [_row("00006-5090-01", 15298.18), _row("00006-5087-01", 30596.36)])
    us = {r["local_price"]: r for r in a.get_cached_results("winrevair")["US"]}
    kit, single = us[30596.36], us[15298.18]
    assert kit["pack_count"] == 2 and kit["pack_count_source"] == "label"
    assert single["pack_count"] == 1
    assert kit["per_unit_local"] == single["per_unit_local"] == 15298.18
    assert kit["adjusted_price_krw"] == single["adjusted_price_krw"]
    assert kit["a8_aggregate_eligible"] is True


def test_unknown_multiple_pack_injection_is_excluded_from_average():
    rows = [
        {"local_price": 100.0, "form_type": "injection", "pack_count_source": "default", "pack_count": 1,
         "formulation_key": "10mg|injection", "a8_aggregate_eligible": True, "a8_exclude_reason": None},
        {"local_price": 300.0, "form_type": "injection", "pack_count_source": "default", "pack_count": 1,
         "formulation_key": "10mg|injection", "a8_aggregate_eligible": True, "a8_exclude_reason": None},
        {"local_price": 250.0, "form_type": "injection", "pack_count_source": "default", "pack_count": 1,
         "formulation_key": "20mg|injection", "a8_aggregate_eligible": True, "a8_exclude_reason": None},
    ]
    flag_injection_pack_multiples(rows)
    assert [r["a8_aggregate_eligible"] for r in rows] == [True, False, True]
    assert rows[1]["a8_exclude_reason"] == "pack_count_unverified"


def test_parsed_or_oral_rows_are_not_touched_by_multiple_guard():
    rows = [
        {"local_price": 6136.0, "form_type": "injection", "pack_count_source": "default", "pack_count": 1,
         "formulation_key": "100mg|injection", "a8_aggregate_eligible": True},
        {"local_price": 12272.0, "form_type": "injection", "pack_count_source": "parsed", "pack_count": 2,
         "formulation_key": "100mg|injection", "a8_aggregate_eligible": True},   # Keytruda US '4 ml 2s' → 이미 2
        {"local_price": 20.0, "form_type": "oral", "pack_count_source": "default", "pack_count": 1,
         "formulation_key": "1mg|oral", "a8_aggregate_eligible": False},
        {"local_price": 40.0, "form_type": "oral", "pack_count_source": "default", "pack_count": 1,
         "formulation_key": "1mg|oral", "a8_aggregate_eligible": False},
    ]
    flag_injection_pack_multiples(rows)
    assert [r["a8_aggregate_eligible"] for r in rows] == [True, True, False, False]
