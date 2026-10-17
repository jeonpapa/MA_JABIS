"""제형별 용법 라벨·분모 회귀 (2026-10-17 사용자 테스트).

1) Keytruda SC 395mg/790mg 바이알에 IV '200mg Q3W' 라벨·용량이 붙던 문제
   — UK #1301 "395mg/2.4ml soln for inj in vial, 1" 의 daily = 200/21 × adj/395 (~2× 과소).
   SC 허가 용법: 395 mg Q3W · 790 mg Q6W (바이알 1개 = 1회 투여) → daily = adj/21 · adj/42.
   IV 100mg 바이알은 그대로 200mg Q3W (Keytruda baseline 수치 불변).
2) JP 자료원 라벨 오타 '後生労働省' → '厚生労働省' (저장 행은 두고 표시만 교정).
3) 국내 키트루다 100mg 바이알 일일비용 — 200 mg Q3W = 바이알 2개 / 21일 (기존 1개 → ₩100,026 과소).

실행 (Linux CI / .venv): python -m pytest tests/test_dosing_labels_sc.py -q
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.exchange_rate import PriceCalculator
from agents.foreign_price_agent import (
    FORMULATION_DOSING_OVERRIDES,
    ForeignPriceAgent,
    display_source_label,
)

# foreign_drug_dosing 실제 DB 행 (성분 + 브랜드 alias)
DOSING_INN = {
    "ingredient_key": "pembrolizumab", "display_name": "Pembrolizumab",
    "dose_per_cycle_mg": 200.0, "cycle_days": 21, "schedule_label": "200mg Q3W",
    "default_pack_count": 1,
}
DOSING_BRAND = {**DOSING_INN, "ingredient_key": "keytruda", "display_name": "Keytruda (pembrolizumab)"}

# 실제 DB 행 복사 (UK MIMS SC, 2026-07 수집)
UK_1301_SC395 = {
    "id": 1301, "query_name": "keytruda", "country": "UK", "product_name": "Keytruda",
    "ingredient": "Pembrolizumab",
    "dosage_strength": "395mg/2.4ml soln for inj in vial, 1",
    "dosage_form": "", "package_unit": "",
    "local_price": 2630.0, "currency": "GBP", "exchange_rate": 1803.75,
    "form_type": "injection", "source_label": "MIMS online (UK, 로그인)",
}
UK_1302_SC790 = {
    **UK_1301_SC395, "id": 1302,
    "dosage_strength": "790mg/4.8ml soln for inj in vial, 1",
    "local_price": 5260.0,
}
UK_1300_IV100 = {
    "id": 1300, "query_name": "keytruda", "country": "UK", "product_name": "Keytruda",
    "ingredient": "Pembrolizumab",
    "dosage_strength": "100mg/4ml conc for soln for inf in vial, 2",
    "dosage_form": "", "package_unit": "",
    "local_price": 5260.0, "currency": "GBP", "exchange_rate": 1803.75,
    "form_type": "injection", "source_label": "MIMS online (UK, 로그인)",
}


def _agent() -> ForeignPriceAgent:
    a = ForeignPriceAgent.__new__(ForeignPriceAgent)
    a.calculator = PriceCalculator()
    return a


def _price(row: dict, dosing: dict) -> dict:
    a = _agent()
    item = dict(row)
    item.pop("id", None)
    pack = a._resolve_pack_count(item["country"], item, dosing)
    calc = a.calculator.calculate_adjusted_price(
        country=item["country"], listed_price=item["local_price"],
        exchange_rate=item["exchange_rate"], pack_count=pack, source_type=None,
    )
    item.update({"adjusted_price_krw": calc["adjusted_price_krw"], "pack_count": calc["pack_count"]})
    a._populate_daily_cost(item, dosing)
    return item


@pytest.mark.parametrize("dosing", [DOSING_INN, DOSING_BRAND])
def test_sc_395_uses_q3w_395mg(dosing):
    r = _price(UK_1301_SC395, dosing)
    assert r["dosing_schedule_label"] == "395 mg Q3W SC"
    # daily = 395/21 × adj/395 = adj/21
    assert r["daily_cost_krw"] == pytest.approx(r["adjusted_price_krw"] / 21, abs=1)
    assert r["daily_dose_mg"] == pytest.approx(395 / 21, abs=1e-3)


@pytest.mark.parametrize("dosing", [DOSING_INN, DOSING_BRAND])
def test_sc_790_uses_q6w_790mg(dosing):
    r = _price(UK_1302_SC790, dosing)
    assert r["dosing_schedule_label"] == "790 mg Q6W SC"
    assert r["daily_cost_krw"] == pytest.approx(r["adjusted_price_krw"] / 42, abs=1)
    assert r["daily_dose_mg"] == pytest.approx(790 / 42, abs=1e-3)


def test_sc_daily_no_longer_uses_iv_200mg_denominator():
    """회귀 — 이전 산식(200/21 × adj/395)이 아니어야 한다 (~1.98× 과소였다)."""
    r = _price(UK_1301_SC395, DOSING_INN)
    old = 200 / 21 * r["adjusted_price_krw"] / 395
    assert r["daily_cost_krw"] > old * 1.9


def test_iv_100mg_keeps_200mg_q3w_and_baseline():
    """Keytruda IV baseline (tests/test_keytruda_injection_baseline.py UK #1300) 불변."""
    r = _price(UK_1300_IV100, DOSING_INN)
    assert r["pack_count"] == 2
    assert r["dosing_schedule_label"] == "200mg Q3W"
    assert abs(r["daily_cost_krw"] - 394_319) <= max(1, 394_319 * 0.001)


def test_override_does_not_mutate_shared_dosing():
    before = dict(DOSING_INN)
    _price(UK_1301_SC395, DOSING_INN)
    assert DOSING_INN == before


def test_override_needs_injection_and_exact_strength():
    f = ForeignPriceAgent._formulation_dosing
    assert f(DOSING_INN, {}, "oral", 395.0) is DOSING_INN          # 경구면 미적용
    assert f(DOSING_INN, {}, "injection", None) is DOSING_INN       # per-unit mg 불명 → 미적용
    assert f(DOSING_INN, {}, "injection", 100.0) is DOSING_INN      # IV 100mg
    assert f(DOSING_INN, {}, "injection", 25.0) is DOSING_INN       # 농도는 매칭 키가 아니다
    assert f(None, {}, "injection", 395.0) is None
    other = {**DOSING_INN, "ingredient_key": "belzutifan"}
    assert f(other, {}, "injection", 395.0) is other


def test_override_table_shape():
    for key in ("pembrolizumab", "keytruda"):
        rules = FORMULATION_DOSING_OVERRIDES[key]
        labels = {r["schedule_label"] for r in rules}
        assert labels == {"395 mg Q3W SC", "790 mg Q6W SC"}
        for r in rules:
            assert r["dose_per_cycle_mg"] == r["unit_mg"]               # SC 바이알 1개 = 1회 투여


def test_no_dosing_leaves_label_empty():
    r = _price(UK_1301_SC395, None)
    assert r["dosing_schedule_label"] is None
    assert r["daily_cost_krw"] is None


# ── JP 자료원 라벨 오타 ─────────────────────────────────────────────────────

def test_jp_source_label_typo_corrected_on_read():
    row = {"country": "JP", "source_label": "後生労働省 薬価基準収載品目リスト"}
    assert display_source_label(row) == "厚生労働省 薬価基準収載品目リスト"
    assert row["source_label"].startswith("後生")                    # 저장 행은 건드리지 않는다


def test_jp_scraper_label_has_no_typo():
    from agents.scrapers.jp_mhlw import JpMhlwScraper
    assert "厚生労働省" in JpMhlwScraper.SOURCE_LABEL
    assert "後生" not in JpMhlwScraper.SOURCE_LABEL


# ── 국내 고정용량 주기 요법 (api/server.py) ───────────────────────────────────

class _FakeDB:
    def __init__(self, rows):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE foreign_drug_dosing (ingredient_key TEXT PRIMARY KEY, display_name TEXT, "
            "dose_per_cycle_mg REAL, cycle_days INTEGER, schedule_label TEXT, notes TEXT, "
            "default_pack_count INTEGER, updated_at TEXT)")
        self.conn.executemany(
            "INSERT INTO foreign_drug_dosing (ingredient_key, dose_per_cycle_mg, cycle_days, schedule_label) "
            "VALUES (?,?,?,?)", rows)

    @contextmanager
    def _connect(self):
        yield self.conn


KEYTRUDA_KR = {
    "insurance_code": "655501901", "brand_name": "키트루다주",
    "product_name": "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)",
    "dosage_form": "0.1g/4mL", "dosage_strength": "4",
    "hira_ingredient": "pembrolizumab   0.1g(25mg/mL)", "current_price": 2100556,
}


@pytest.fixture
def server(monkeypatch):
    import api.server as s
    monkeypatch.setattr(s, "db", _FakeDB([
        ("pembrolizumab", 200.0, 21, "200mg Q3W"),
        ("belzutifan", 120.0, 1, "120mg daily"),
    ]))
    return s


def test_domestic_vial_total_from_amount_over_volume(server):
    assert server._domestic_vial_total_mg(KEYTRUDA_KR) == 100.0
    # 농도만 있으면 바이알 총량으로 쓰지 않는다
    assert server._domestic_vial_total_mg({"dosage_form": "25mg/mL", "product_name": "X주(25mg/mL)"}) is None


def test_domestic_keytruda_two_vials_per_21_days(server):
    r = server._fixed_dose_cycle_cost(KEYTRUDA_KR, 2100556, None)
    assert r is not None
    assert r["vials_per_cycle"] == 2
    assert r["daily_cost"] == round(2100556 * 2 / 21)                 # ₩200,053 (기존 ₩100,026)
    assert r["assumption"] == "200 mg Q3W = 100 mg 바이알 2개 / 21일"
    assert r["source"] == "foreign_drug_dosing"


def test_domestic_daily_regimen_not_touched(server):
    """cycle_days < 7 (경구 매일 복용) 은 고정용량 주기 경로 대상 아님."""
    welireg = {"product_name": "웰리렉정40밀리그램", "dosage_form": "40mg",
               "hira_ingredient": "belzutifan 40mg", "current_price": 100000}
    assert server._fixed_dose_cycle_cost(welireg, 100000, None) is None


def test_domestic_usage_text_fallback(server):
    p = {"product_name": "가상주_(240mg/24mL)", "dosage_form": "240mg/24mL",
         "hira_ingredient": "unknownmab 240mg", "current_price": 1_000_000}
    r = server._fixed_dose_cycle_cost(p, 1_000_000, "이 약 480 mg을 매 4주마다 정맥 투여한다.")
    assert r["vials_per_cycle"] == 2 and r["cycle_days"] == 28
    assert r["assumption"] == "480 mg Q4W = 240 mg 바이알 2개 / 28일"
    assert r["source"] == "usage_text"
