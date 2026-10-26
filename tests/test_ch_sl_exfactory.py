"""스위스 공식 공장도가(BAG SL FAP) 사용 + 출처 링크 slug 수정 (개선 요청 #3, 2026-10).

실행: .venv/bin/python -m pytest tests/test_ch_sl_exfactory.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.exchange_rate import PriceCalculator
from agents.foreign_price_agent import official_exfactory_price
from agents.scrapers import ch_compendium
from agents.scrapers.ch_compendium import ChCompendiumScraper, product_slug
from agents.scrapers.ch_sl import flatten_packs, match_pack, pack_unit_count

SL_SAMPLE = {"items": [
    {"trademark": "Winrevair", "medicinalProducts": [
        {"dosageFormAndStrength": "Trockensub 45 mg c Solv", "packagedMedicinalProducts": [
            {"packSize": "Durchstf 1 Stk", "exFactoryPrice": 5895.82, "retailPrice": 6356.9, "gtin": "A"}]},
        {"dosageFormAndStrength": "Trockensub 60 mg c Solv", "packagedMedicinalProducts": [
            {"packSize": "Durchstf 1 Stk", "exFactoryPrice": 7864.23, "retailPrice": 8376.5, "gtin": "B"}]},
    ]},
    {"trademark": "Keytruda", "medicinalProducts": [
        {"dosageFormAndStrength": "Inf Konz 100 mg/4 ml Durchstf", "packagedMedicinalProducts": [
            {"packSize": "2 Stk", "exFactoryPrice": 3933.27, "retailPrice": 4294.1, "gtin": "C"}]},
        {"dosageFormAndStrength": "Inj Lös 395 mg/2.4 ml Durchstf", "packagedMedicinalProducts": [
            {"packSize": "1 Stk", "exFactoryPrice": 3933.27, "retailPrice": 4294.1, "gtin": "D"}]},
    ]},
]}


def test_calculator_uses_fap_times_country_ratio():
    """별첨5: 스위스 = SL 공장도가 × 0.73 (리베이트 반영). 판매가에 비율을 곱하지 않는다."""
    c = PriceCalculator()
    r = c.calculate_adjusted_price("CH", 6356.9, 1673.54, pack_count=1, source_type="compendium", exfactory_price=5895.82)
    assert r["factory_price_krw"] == int(5895.82 * 0.73 * 1673.54)
    assert abs(r["factory_ratio"] - 5895.82 * 0.73 / 6356.9) < 1e-9
    assert "공식 공장도가" in r["factory_ratio_label"] and "0.73" in r["factory_ratio_label"]
    est = c.calculate_adjusted_price("CH", 6356.9, 1673.54, pack_count=1, source_type="compendium")
    assert est["factory_ratio"] == 0.65            # FAP 없으면 Compendium × 0.65 (별첨5 각주2)


def test_calculator_divides_pack_for_fap():
    c = PriceCalculator()
    r = c.calculate_adjusted_price("CH", 4294.1, 1673.54, pack_count=2, source_type="compendium", exfactory_price=3933.27)
    assert r["factory_price_krw"] == int(3933.27 / 2 * 0.73 * 1673.54)
    assert r["factory_price"] == round(3933.27 / 2 * 0.73, 4)


def test_calculator_ignores_nonpositive_fap():
    c = PriceCalculator()
    for bad in (0, -5, None):
        r = c.calculate_adjusted_price("CH", 100.0, 1700.0, source_type="compendium", exfactory_price=bad)
        assert r["factory_ratio"] == 0.65


def test_official_exfactory_only_for_ch_and_valid():
    row = {"raw_data": '{"ex_factory_price": 5895.82}'}
    assert official_exfactory_price("CH", row) == 5895.82
    assert official_exfactory_price("IT", row) is None          # 다른 국가는 건드리지 않는다
    assert official_exfactory_price("CH", {"raw_data": '{"ex_factory_price": 0}'}) is None
    assert official_exfactory_price("CH", {"raw_data": "{}"}) is None
    assert official_exfactory_price("CH", {"extra": {"ex_factory_price": "x"}}) is None


def test_match_pack_exact_and_pack_count():
    packs = flatten_packs(SL_SAMPLE)
    hit = match_pack(packs, 6356.9, strength="Trockensub 45 mg c Solv")
    assert hit["ex_factory_price"] == 5895.82 and pack_unit_count(hit["pack_size"]) == 1
    k = match_pack(packs, 4294.1, strength="Inf Konz 100 mg/4ml")
    assert k["ex_factory_price"] == 3933.27 and pack_unit_count(k["pack_size"]) == 2 and k["gtin"] == "C"


def test_match_pack_none_when_price_differs_or_missing():
    packs = flatten_packs(SL_SAMPLE)
    assert match_pack(packs, 9999.0) is None
    assert match_pack(packs, None) is None
    assert match_pack([], 6356.9) is None


def test_match_pack_ambiguous_different_fap_is_none():
    packs = [
        {"strength": "X", "pack_size": "1 Stk", "ex_factory_price": 100.0, "retail_price": 120.0},
        {"strength": "Y", "pack_size": "1 Stk", "ex_factory_price": 105.0, "retail_price": 120.0},
    ]
    assert match_pack(packs, 120.0) is None


def test_pack_unit_count_variants():
    assert pack_unit_count("2 Stk") == 2
    assert pack_unit_count("Durchstf 1 Stk") == 1
    assert pack_unit_count("Btl 30 Stk") == 30
    assert pack_unit_count("Packung") is None


def test_product_slug_per_product_not_keytruda():
    assert product_slug("WINREVAIR Trockensub 45 mg c Solv") == "winrevair-trockensub-45-mg-c-solv"
    assert product_slug("PREVYMIS Inf Konz 240 mg/12ml") == "prevymis-inf-konz-240-mg-12ml"
    assert product_slug("") == "product"


def test_scraper_parse_uses_product_slug(tmp_path):
    s = ChCompendiumScraper(cache_dir=tmp_path)
    html = '<html><h1>Winrevair</h1> CHF 6\'356.90 MSD Merck Sharp</html>'
    parsed = s._parse_product_html(html, "1625001", "WINREVAIR Trockensub 45 mg c Solv")
    assert parsed["source_url"].endswith("/product/1625001/winrevair-trockensub-45-mg-c-solv")
    assert "keytruda" not in parsed["source_url"]


def test_scraper_attaches_fap_and_pack_count(tmp_path, monkeypatch):
    s = ChCompendiumScraper(cache_dir=tmp_path)
    monkeypatch.setattr(ch_compendium, "fetch_sl_packs", lambda q, session=None: flatten_packs(SL_SAMPLE))
    results = [
        {"local_price": 4294.1, "dosage_strength": "KEYTRUDA Inf Konz 100 mg/4ml", "extra": {"source_type": "compendium"}},
        {"local_price": 1.23, "dosage_strength": "NO MATCH", "extra": {"source_type": "compendium"}},
    ]
    s._attach_sl_exfactory(results, "keytruda")
    a, b = results
    assert a["extra"]["ex_factory_price"] == 3933.27 and a["pack_count"] == 2
    assert "ex_factory_url" in a["extra"] and a["extra"]["sl_gtin"] == "C"
    assert "ex_factory_price" not in b["extra"] and "pack_count" not in b     # 불일치는 채우지 않는다


def test_scraper_sl_failure_keeps_estimate_path(tmp_path, monkeypatch):
    s = ChCompendiumScraper(cache_dir=tmp_path)
    monkeypatch.setattr(ch_compendium, "fetch_sl_packs", lambda q, session=None: [])
    results = [{"local_price": 4294.1, "dosage_strength": "x", "extra": {}}]
    s._attach_sl_exfactory(results, "keytruda")
    assert "ex_factory_price" not in results[0]["extra"]


# ── 저장 행 복구 스크립트 ───────────────────────────────────────────────
import importlib.util
import json
import sqlite3


def _load_repair():
    spec = importlib.util.spec_from_file_location(
        "repair_ch_exfactory", Path(__file__).resolve().parent.parent / "scripts" / "repair_ch_exfactory.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mem_row(**kw):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    base = dict(id=1, query_name="keytruda", product_name="KEYTRUDA Inf Konz 100 mg/4ml", local_price=4294.1,
                exchange_rate=1673.54, pack_count=1, adjusted_price_krw=6272105, adjusted_price_krw_normalized=6231779,
                daily_cost_krw=597343, raw_data='{"source_type": "compendium"}',
                source_url="https://www.compendium.ch/product/1346803/keytruda")
    base.update(kw)
    c.execute("CREATE TABLE t (" + ",".join(base) + ")")
    c.execute("INSERT INTO t VALUES (" + ",".join("?" * len(base)) + ")", list(base.values()))
    return c.execute("SELECT * FROM t").fetchone()


def test_repair_fixed_url_only_for_non_keytruda():
    m = _load_repair()
    assert m.fixed_url(_mem_row()) is None                                   # 진짜 키트루다는 그대로
    w = _mem_row(product_name="WINREVAIR Trockensub 45 mg c Solv", source_url="https://www.compendium.ch/product/1625001/keytruda")
    assert m.fixed_url(w) == "https://www.compendium.ch/product/1625001/winrevair-trockensub-45-mg-c-solv"
    ok = _mem_row(source_url="https://compendium.ch/product/1382335-prevymis-filmtabl-240-mg")
    assert m.fixed_url(ok) is None


def test_repair_derive_scales_and_sets_pack_count():
    m = _load_repair()
    row = _mem_row()
    entry = {"fap": 3933.27, "sl_pack_size": "2 Stk", "gtin": "C", "sl_pack_count": 2, "fap_url": "u"}
    new = m.derive(row, entry)
    assert new["pack_count"] == 2 and new["pack_count_source"] == "scraper"
    assert new["factory_price_krw"] == int(3933.27 / 2 * 0.73 * 1673.54)
    assert json.loads(new["raw_data"])["ex_factory_price"] == 3933.27
    assert json.loads(new["raw_data"])["source_type"] == "compendium"        # 기존 raw_data 보존
    ratio = new["adjusted_price_krw"] / row["adjusted_price_krw"]
    assert abs(new["daily_cost_krw"] - round(row["daily_cost_krw"] * ratio)) <= 1
    assert m.derive(row, {}) is None                                           # FAP 없으면 손대지 않음
