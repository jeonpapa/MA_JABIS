"""pack_count 출처(pack_count_source) 저장 + A8 집계 안전망.

경구 팩가격이 per-unit 으로 저장되는 문제(2026-10 감사: sitagliptin per-tablet IT ₩51,794 · CH ₩47,326 · DE ₩31,335 vs
FR ₩456) 의 안전망: pack_count 가 어떻게 정해졌는지(scraper|per_unit|parsed|dosing|default) 를 컬럼에 남기고,
pack-pricing 국가의 oral/unknown 행이 `default`(단서 없음·dosing 없음 → 1) 면 A8 min/avg 집계(UI·리포트)에서 제외한다.
injection 의 default=1 vial 은 규칙상 보수 fallback 이라 집계에 남긴다.

실행: .venv/bin/python -m pytest tests/test_foreign_pack_count_source.py -q
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.foreign_price_agent import ForeignPriceAgent as A, a8_aggregate_eligibility


def _base(**kw) -> dict:
    return {"searched_at": "2026-07-27T03:03:45", "query_name": "sitagliptin", "currency": "EUR", "source_url": "",
            "source_label": "", "raw_data": None, "exchange_rate": 1544.43, "exchange_rate_from": "20230603",
            "exchange_rate_to": "20260531", "vat_rate": 0.1, "distribution_margin": 0.0869, "ingredient": "",
            "dosage_form": "", "package_unit": "", "formulation_key": "100mg|oral", **kw}


def test_migration_adds_pack_count_source_idempotently(tmp_path):
    from agents.db import DrugPriceDB
    db = tmp_path / "m.db"
    DrugPriceDB(db)
    DrugPriceDB(db)                                   # 두 번 열어도 ALTER 가 두 번 돌지 않는다
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(foreign_drug_prices)")]
    assert cols.count("pack_count_source") == 1


def test_save_foreign_price_persists_pack_count_source(tmp_path):
    from agents.db import DrugPriceDB
    db = tmp_path / "s.db"
    d = DrugPriceDB(db)
    rid = d.save_foreign_price(_base(country="FR", product_name="SITAGLIPTINE ALMUS 100 mg", dosage_strength="100 mg",
                                     package_unit="plaquette(s) PVC de 30 comprimé(s)", local_price=9.64,
                                     factory_price_krw=1, adjusted_price_krw=456, pack_count=30, per_unit_local=0.3213,
                                     form_type="oral", pack_count_source="parsed"))
    assert sqlite3.connect(db).execute("SELECT pack_count_source FROM foreign_drug_prices WHERE id=?", (rid,)).fetchone()[0] == "parsed"
    rid2 = d.save_foreign_price(_base(country="CH", product_name="x", dosage_strength="x", local_price=1.0,
                                      factory_price_krw=1, adjusted_price_krw=1, form_type="oral"))
    assert sqlite3.connect(db).execute("SELECT pack_count_source FROM foreign_drug_prices WHERE id=?", (rid2,)).fetchone()[0] is None


@pytest.mark.parametrize("country,form,source,eligible,reason", [
    ("CH", "oral", "default", False, "pack_count_unverified"),       # 팩가가 per-unit 으로 저장된 미확인 행
    ("CH", "unknown", "default", False, "pack_count_unverified"),
    ("IT", "oral", "parsed", True, None),
    ("DE", "oral", "dosing", True, None),
    ("IT", "oral", "scraper", True, None),
    ("DE", "injection", "default", True, None),                       # 1 vial 보수 fallback 은 규칙
    ("JP", "oral", "per_unit", True, None),
    ("JP", "oral", "default", True, None),                            # per-unit 국가는 pack 개념이 없다
    ("FR", "oral", None, True, None),                                 # 출처 미기록(레거시) 은 재결정 전이라 판단 보류 → 포함
])
def test_a8_aggregate_eligibility(country, form, source, eligible, reason):
    row = {"form_type": form, "pack_count_source": source, "adjusted_price_krw": 100}
    assert a8_aggregate_eligibility(country, row) == (eligible, reason)


def _agent_with(tmp_path, rows):
    from agents.db import DrugPriceDB
    from agents.exchange_rate import PriceCalculator
    db = tmp_path / "c.db"
    d = DrugPriceDB(db)
    for r in rows:
        d.save_foreign_price(r)
    a = A.__new__(A)
    a.db = d
    a.calculator = PriceCalculator()
    a._build_scraper = lambda country: type("S", (), {"SOURCE_TYPE": None})()
    return a


def test_cached_results_flag_unverified_pack_rows(tmp_path):
    a = _agent_with(tmp_path, [
        _base(country="CH", currency="CHF", product_name="SITAGLIPTIN NOBEL Filmtabl 100 mg",
              dosage_strength="SITAGLIPTIN NOBEL Filmtabl 100 mg", local_price=31.75, exchange_rate=1700.0,
              factory_price_krw=1, adjusted_price_krw=1, pack_count=1, per_unit_local=31.75, form_type="oral"),
        _base(country="FR", product_name="SITAGLIPTINE ALMUS 100 mg, comprimé pelliculé", dosage_strength="100 mg",
              package_unit="plaquette(s) PVC polyéthylène PVDC aluminium de 30 comprimé(s)", local_price=9.64,
              factory_price_krw=1, adjusted_price_krw=1, pack_count=30, per_unit_local=0.3213, form_type="oral"),
        _base(country="IT", product_name="JANUVIA*28 cpr rivestite 100 mg", dosage_strength="JANUVIA*28 cpr rivestite 100 mg",
              dosage_form="SITAGLIPTIN 100MG 28 UNITA' USO ORALE", local_price=25.38, factory_price_krw=1,
              adjusted_price_krw=1, pack_count=1, per_unit_local=25.38, form_type="oral"),
    ])
    res = a.get_cached_results("sitagliptin")
    ch, fr, it = res["CH"][0], res["FR"][0], res["IT"][0]
    assert ch["pack_count_source"] == "default" and ch["a8_aggregate_eligible"] is False
    assert ch["a8_exclude_reason"] == "pack_count_unverified"
    assert fr["pack_count"] == 30 and fr["pack_count_source"] == "parsed" and fr["a8_aggregate_eligible"] is True
    assert it["pack_count"] == 28 and it["pack_count_source"] == "parsed" and it["a8_aggregate_eligible"] is True
    # 저장 pack_count 1 (IT) 은 바뀌지 않는다 — 읽기 경로 재결정만
    assert sqlite3.connect(tmp_path / "c.db").execute(
        "SELECT pack_count, pack_count_source FROM foreign_drug_prices WHERE country='IT'").fetchone() == (1, None)


def test_report_excludes_unverified_rows_from_average():
    from agents.foreign_report import build_report_model, _rows_for_render
    cached = {
        "CH": [{"adjusted_price_krw": 47326, "adjusted_price_krw_normalized": 47326, "local_price": 31.75,
                "per_unit_local": 31.75, "exchange_rate": 1700.0, "pack_count": 1, "currency": "CHF",
                "pack_count_source": "default", "a8_aggregate_eligible": False, "a8_exclude_reason": "pack_count_unverified",
                "source_label": "compendium.ch"}],
        "FR": [{"adjusted_price_krw": 456, "adjusted_price_krw_normalized": 456, "local_price": 9.64,
                "per_unit_local": 0.3213, "exchange_rate": 1544.43, "pack_count": 30, "currency": "EUR",
                "pack_count_source": "parsed", "a8_aggregate_eligible": True, "a8_exclude_reason": None,
                "source_label": "BDPM"}],
    }
    m = build_report_model(cached, applied_actual=500)
    assert [c["country"] for c in m["cols"]] == ["FR"]
    assert m["avg"]["adjusted"] == 456
    assert m["excluded"] == [{"country": "CH", "country_kr": "스위스", "reason": "pack_count_unverified"}]
    header, body = _rows_for_render(m)
    assert any("스위스" in cell and "포장수량 미확인" in cell for row in body for cell in row)


def test_vidal_ucd_price_is_per_unit():
    """Vidal 'par UCD' 가격은 UCD(=바이알 1개) 단가 — 스크레이퍼가 pack_count=1 을 명시해 출처가 scraper 가 된다."""
    from agents.scrapers.fr_vidal import FrVidalScraper
    html = ("<html><h1>KEYTRUDA 25 mg/ml sol diluer p perf</h1><body>Substance active : pembrolizumab "
            "solution pour perfusion 25 mg/ml Prix par UCD : 2000,740 euros Liste I</body></html>")
    s = FrVidalScraper.__new__(FrVidalScraper)
    s._session = type("Sess", (), {"get": staticmethod(lambda url, timeout=20: type("R", (), {
        "text": html, "raise_for_status": staticmethod(lambda: None)})())})()
    rec = s._extract_detail("https://www.vidal.fr/medicaments/keytruda-25-mg-ml-sol-diluer-p-perf-173141.html")
    assert rec["local_price"] == 2000.74 and rec["pack_count"] == 1 and rec["extra"]["price_basis"] == "ucd"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
