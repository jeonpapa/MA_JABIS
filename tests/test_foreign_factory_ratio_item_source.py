"""실효 factory_ratio 는 스크레이퍼 클래스가 아니라 **행(item)의 가격 종류**가 정한다 (P1.6 수집 가능성, Item 3).

감사: `search_one_country`/`get_cached_results` 가 `scraper.SOURCE_TYPE`(IT=aifa_exfactory → 1.0) 을 모든 행에 썼다. 그런데
IT CSV Class A 행은 공중가(prezzo al pubblico, raw_data.price_kind) 라 IT 국가 기본 0.93 이 맞다 — Class A 행이 +7.5% 과대
(sitagliptin #1304–#1380, letermovir 경구 #1481·#1482). source_label 도 "Classe H" 로 고정돼 있었다.

수정은 **앞으로만**(신규 수집 + 캐시 화면의 읽기-재계산 경로). 저장 행은 다시 쓰지 않는다 — 재계산 경로가 화면/리포트
값을 바로잡고, 저장 adjusted_price_krw 는 그대로 남는다(보고서에 id 목록).

`tests/test_foreign_factory_ratio_effective.py`(클래스 선언 기준) 와 같은 스타일로, 여기서는 (국가, 행 source_type) 기준을
고정한다. 네트워크 금지 — fixture CSV 와 임시 DB.

실행: .venv/bin/python -m pytest tests/test_foreign_factory_ratio_item_source.py -q
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import foreign_price_agent as fpa
from agents.exchange_rate import PriceCalculator
from agents.scrapers.it_aifa import ItAifaScraper
from agents.scrapers.it_aifa_tnf import ItAifaTnfScraper

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "it_aifa"

# (국가, 행의 raw_data/extra, 클래스 기본 SOURCE_TYPE, 실효 ratio)
EFFECTIVE = [
    ("IT", {"aifa_class": "H", "price_kind": "ex-factory", "source_type": "aifa_exfactory"}, "aifa_exfactory", 1.0),
    ("IT", {"aifa_class": "A", "price_kind": "prezzo al pubblico", "source_type": None}, "aifa_exfactory", 0.93),   # 공중가 → 국가 기본
    ("IT", {"channel": "tnf_determina", "price_kind": "ex-factory", "source_type": "aifa_exfactory"}, "aifa_exfactory", 1.0),
    ("UK", {"company": "MSD", "source_type": None}, None, 0.73),
    ("CH", {"source_type": "compendium"}, "compendium", 0.73),
    ("FR", {"source_type": "vidal"}, "vidal", 0.77),
    ("US", {"source_type": "redbook_wac"}, "redbook_wac", 0.74),
    ("JP", {"company": "ＭＳＤ"}, None, 0.79),              # 행에 선언 없음 → 클래스 기본(없음) → 국가 기본
    ("IT", {}, "aifa_exfactory", 1.0),                     # 행에 선언 없음(레거시) → 클래스 기본 유지
]


@pytest.mark.parametrize("country,extra,class_default,ratio", EFFECTIVE)
def test_effective_ratio_follows_item_source_type(country, extra, class_default, ratio):
    row = {"country": country, "raw_data": json.dumps(extra, ensure_ascii=False)}
    src = fpa.resolve_item_source_type(row, class_default)
    assert PriceCalculator().resolve_factory_ratio(country, src) == ratio, (country, extra, src)


def test_top_level_item_source_type_wins_over_raw_data():
    row = {"country": "IT", "source_type": None, "raw_data": json.dumps({"source_type": "aifa_exfactory"})}
    assert fpa.resolve_item_source_type(row, "aifa_exfactory") is None
    row = {"country": "IT", "source_type": "aifa_exfactory", "raw_data": "{}"}
    assert fpa.resolve_item_source_type(row, None) == "aifa_exfactory"


def test_unparseable_raw_data_falls_back_to_class_default():
    assert fpa.resolve_item_source_type({"raw_data": "not json"}, "aifa_exfactory") == "aifa_exfactory"
    assert fpa.resolve_item_source_type({"raw_data": None}, None) is None


# ── IT CSV 스크레이퍼: 행마다 source_type · source_label 을 선언한다 ───────────────────────────────────────────

def test_it_csv_rows_declare_per_class_source_type_and_label(tmp_path):
    import shutil
    d = tmp_path / "it"; d.mkdir()
    for n in ("Classe_A_per_nome_commerciale_30-09-2025.csv", "Classe_H_per_nome_commerciale_30-09-2025.csv"):
        shutil.copy(FIXTURES / n, d / n)
    s = ItAifaScraper(cache_dir=d)
    rows = asyncio.run(s.search("prevymis", page=None))
    by_class = {}
    for r in rows:
        by_class.setdefault(r["extra"]["aifa_class"], []).append(r)
    a, h = by_class["A"], by_class["H"]
    assert {r["source_type"] for r in a} == {None} and {r["extra"]["price_kind"] for r in a} == {"prezzo al pubblico"}
    assert {r["source_label"] for r in a} == {"AIFA Lista Classe A (Prezzo al pubblico)"}
    assert {r["source_type"] for r in h} == {"aifa_exfactory"} and {r["extra"]["price_kind"] for r in h} == {"ex-factory"}
    assert {r["source_label"] for r in h} == {"AIFA Lista Classe H (Prezzo Ex-factory)"}
    assert a[0]["local_price"] == 6931.68 and h[0]["local_price"] == 165.01


def test_base_run_record_carries_item_source_type_and_label(tmp_path, monkeypatch):
    """BaseScraper.run() 의 DB 레코드가 행의 source_type/source_label 을 클래스 기본 대신 쓴다."""
    import shutil
    from agents.scrapers import base as base_mod
    d = tmp_path / "it"; d.mkdir()
    for n in ("Classe_A_per_nome_commerciale_30-09-2025.csv", "Classe_H_per_nome_commerciale_30-09-2025.csv"):
        shutil.copy(FIXTURES / n, d / n)
    s = ItAifaScraper(cache_dir=d)

    class _Ctx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        @property
        def chromium(self):
            return self

        async def launch(self, **kw):
            return self

        async def new_context(self, **kw):
            return self

        async def new_page(self):
            return None

        async def close(self):
            pass
    monkeypatch.setattr(base_mod, "async_playwright", lambda: _Ctx())
    recs = asyncio.run(s.run("prevymis"))
    labels = {r["source_label"] for r in recs}
    assert labels == {"AIFA Lista Classe A (Prezzo al pubblico)", "AIFA Lista Classe H (Prezzo Ex-factory)"}
    assert {r["source_type"] for r in recs} == {None, "aifa_exfactory"}
    assert all(json.loads(r["raw_data"])["price_kind"] in ("prezzo al pubblico", "ex-factory") for r in recs)


# ── 에이전트: 신규 수집(search_one_country) + 캐시 읽기(get_cached_results) 모두 행 기준 ─────────────────────

RATE = {"rate": 1544.43, "from_month": "2023-06", "to_month": "2026-05"}
# 기대값은 계산기에 ratio 를 **명시**해 독립적으로 만든다: Class A 공중가(None → 0.93) 와 Class H ex-factory(1.0)
A_EXPECTED = PriceCalculator().calculate_adjusted_price("IT", 6931.68, 1544.43, pack_count=28, source_type=None)["adjusted_price_krw"]
A_STORED_AS_EXFACTORY = 457_120     # 실제 DB #1481 저장값 (ratio 1.0 으로 계산됐던 값)
H_EXPECTED = PriceCalculator().calculate_adjusted_price("IT", 165.01, 1544.43, pack_count=1, source_type="aifa_exfactory")["adjusted_price_krw"]


def _rec(**kw) -> dict:
    base = {"searched_at": "2026-07-27T03:02:57", "query_name": "letermovir", "country": "IT", "product_name": "PREVYMIS*28x1 cpr rivestite 240 mg",
            "ingredient": "Letermovir", "dosage_strength": "PREVYMIS*28x1 cpr rivestite 240 mg", "dosage_form": "LETERMOVIR 240MG 28 UNITA' USO ORALE",
            "package_unit": "", "local_price": 6931.68, "currency": "EUR", "exchange_rate": None, "exchange_rate_from": None,
            "exchange_rate_to": None, "factory_price_krw": None, "vat_rate": None, "distribution_margin": None,
            "adjusted_price_krw": None, "source_url": "", "source_label": "AIFA Lista Classe A (Prezzo al pubblico)",
            "raw_data": json.dumps({"aifa_class": "A", "price_kind": "prezzo al pubblico", "source_type": None}),
            "form_type": "oral", "source_type": None}
    return {**base, **kw}


class _Stub:
    COUNTRY = "IT"; CURRENCY = "EUR"; SOURCE_TYPE = "aifa_exfactory"; FALLBACK_TO_INN = False

    def __init__(self, rows):
        self.rows, self.warnings = rows, []

    def _resolve_form_type(self, item):
        return item.get("form_type") or "unknown"

    async def run(self, term):
        return [dict(r) for r in self.rows]


class _NoTnf:
    def __init__(self, **kw):
        pass

    async def run(self, term):
        return []


def _agent(monkeypatch, tmp_path, rows):
    (tmp_path / "data" / "db").mkdir(parents=True)
    agent = fpa.ForeignPriceAgent(tmp_path)
    monkeypatch.setattr(agent, "_build_scraper", lambda c: _Stub(rows))
    monkeypatch.setattr(fpa, "ItAifaTnfScraper", _NoTnf)
    monkeypatch.setattr(agent.rate_fetcher, "get_36m_average", lambda *a, **k: RATE)
    monkeypatch.setattr(agent, "_assign_formulations_and_persist", lambda q, items: None)
    return agent


def test_search_one_country_applies_class_a_country_default_and_class_h_override(monkeypatch, tmp_path):
    h = _rec(product_name="PREVYMIS*1 flaconcino EV infus 240 mg 12 ml", dosage_strength="PREVYMIS*1 flaconcino EV infus 240 mg 12 ml",
             dosage_form="LETERMOVIR 240MG 12ML 1 UNITA' USO PARENTERALE", local_price=165.01, form_type="injection",
             source_label="AIFA Lista Classe H (Prezzo Ex-factory)", source_type="aifa_exfactory",
             raw_data=json.dumps({"aifa_class": "H", "price_kind": "ex-factory", "source_type": "aifa_exfactory"}))
    agent = _agent(monkeypatch, tmp_path, [_rec(), h])
    saved = asyncio.run(agent.search_one_country("letermovir", "IT"))
    a_row = next(r for r in saved if r["form_type"] == "oral")
    h_row = next(r for r in saved if r["form_type"] == "injection")
    assert a_row["factory_ratio"] == 0.93 and a_row["factory_ratio_label"] == "IT factory_ratio=0.93" and a_row["source_type"] is None
    assert a_row["pack_count"] == 28
    # 6931.68/28 × 1544.43 × 0.93 × 1.10 × 1.0869 ≈ 425,122 (이전 1.0 이면 457,120 — +7.5%)
    assert a_row["adjusted_price_krw"] == A_EXPECTED and a_row["adjusted_price_krw"] < A_STORED_AS_EXFACTORY * 0.94
    assert h_row["factory_ratio"] == 1.0 and h_row["factory_ratio_label"] == "source:aifa_exfactory=1.0"
    assert h_row["adjusted_price_krw"] == H_EXPECTED == 304_691


def test_get_cached_results_recomputes_stored_class_a_rows_with_country_default(monkeypatch, tmp_path):
    """저장 행은 ratio 1.0 으로 계산돼 있다(실제 DB #1481 adj 457,120). 읽기-재계산이 0.93 으로 바로잡고 라벨도 Classe A 로 보인다.
    저장값(adjusted_price_krw 컬럼)은 건드리지 않는다."""
    import sqlite3
    agent = _agent(monkeypatch, tmp_path, [])
    stored_a = _rec(exchange_rate=1544.43, exchange_rate_from="2023-06", exchange_rate_to="2026-05", factory_price_krw=382_339,
                    adjusted_price_krw=457_120, pack_count=28, per_unit_local=247.56, vat_rate=0.1, distribution_margin=0.0869,
                    source_label="AIFA Lista Classe H (Prezzo Ex-factory)")        # 과거 수집: 라벨도 H 로 잘못 고정
    stored_h = _rec(product_name="PREVYMIS*1 flaconcino EV infus 240 mg 12 ml", dosage_strength="PREVYMIS*1 flaconcino EV infus 240 mg 12 ml",
                    dosage_form="LETERMOVIR 240MG 12ML 1 UNITA' USO PARENTERALE", local_price=165.01, form_type="injection",
                    exchange_rate=1544.43, factory_price_krw=254_846, adjusted_price_krw=304_691, pack_count=1,
                    source_label="AIFA Lista Classe H (Prezzo Ex-factory)",
                    raw_data=json.dumps({"aifa_class": "H", "price_kind": "ex-factory", "source_type": "aifa_exfactory"}))
    ida = agent.db.save_foreign_price(stored_a)
    agent.db.save_foreign_price(stored_h)
    by_cc = agent.get_cached_results("letermovir")
    rows = {r["form_type"]: r for r in by_cc["IT"]}
    a, h = rows["oral"], rows["injection"]
    assert a["factory_ratio"] == 0.93 and a["adjusted_price_krw"] == A_EXPECTED and a["source_type"] is None
    assert a["adjusted_price_krw"] < A_STORED_AS_EXFACTORY * 0.94
    assert a["source_label"] == "AIFA Lista Classe A (Prezzo al pubblico)"
    assert h["factory_ratio"] == 1.0 and h["adjusted_price_krw"] == H_EXPECTED == 304_691
    assert h["source_label"] == "AIFA Lista Classe H (Prezzo Ex-factory)"
    with sqlite3.connect(agent.db.db_path) as c:
        stored = c.execute("SELECT adjusted_price_krw, source_label FROM foreign_drug_prices WHERE id=?", (ida,)).fetchone()
    assert stored == (457_120, "AIFA Lista Classe H (Prezzo Ex-factory)")       # 저장 행은 그대로


def test_keytruda_tnf_rows_keep_ex_factory_ratio():
    """Keytruda baseline(IT TNF #1495/#1496) 보호 — TNF 행의 raw_data 는 ex-factory 를 선언하므로 1.0 그대로."""
    row = {"country": "IT", "raw_data": json.dumps({"channel": "tnf_determina", "price_kind": "ex-factory", "source_type": "aifa_exfactory"})}
    src = fpa.resolve_item_source_type(row, ItAifaTnfScraper.SOURCE_TYPE)
    assert PriceCalculator().calculate_adjusted_price("IT", 3798.34, 1544.43, pack_count=1, source_type=src)["adjusted_price_krw"] == 7_013_654
