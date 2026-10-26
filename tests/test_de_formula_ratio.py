"""독일 공장도가 — 별첨5 별도 산식 (2026-10-26, 사용자 승인). 고정 0.6955 대신 가격별 산식.

실행: .venv/bin/python -m pytest tests/test_de_formula_ratio.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.exchange_rate import PriceCalculator
from agents.price_adjustment import germany_exfactory_local


def test_formula_matches_official_steps():
    avp = 11458.02
    expect = (((avp / 1.19) - 8.35) / 1.03 - 0.7) / 1.0315 * 0.93
    assert abs(germany_exfactory_local(avp) - expect) < 1e-9


def test_ratio_depends_on_price():
    r = lambda p: germany_exfactory_local(p) / p
    assert r(100) < r(1000) < r(17831.08)
    assert abs(r(17831.08) - 0.7351) < 5e-4
    assert abs(r(198.11) - 0.6955) < 5e-4          # 기존 고정 비율은 약 €198 에서만 맞았다


def test_calculator_uses_formula_for_de_pack_then_divides():
    c = PriceCalculator()
    out = c.calculate_adjusted_price("DE", 9380.56, 1673.54, pack_count=28, source_type=None)
    exf = germany_exfactory_local(9380.56)
    assert out["factory_price_krw"] == int(exf / 28 * 1673.54)
    assert abs(out["factory_ratio"] - exf / 9380.56) < 1e-9
    assert "별첨5" in out["factory_ratio_label"]


def test_calculator_falls_back_when_price_below_fixed_fees():
    c = PriceCalculator()
    out = c.calculate_adjusted_price("DE", 8.0, 1700.0, pack_count=1)
    assert out["factory_ratio"] == 0.6955 and "산식 불가" in out["factory_ratio_label"]


def test_other_countries_unchanged():
    c = PriceCalculator()
    assert c.calculate_adjusted_price("UK", 100.0, 1800.0, source_type=None)["factory_ratio"] == 0.73


# ── Rote Liste Packungsangaben 파서 (실제 화면 텍스트, Preisstand 2026-10-15) ─────────────────
from agents.scrapers.de_rote_liste import parse_packungsangaben

KEYTRUDA_TXT = """LAGERUNGSHINWEISE Lagerungshinweis! Kühlkette!
PACKUNGSANGABEN
\t\tPZN\tAVP (EB)/FB\tAVP/UVP
1 Durchstechfl. 25 mg/ml\tN1\t10749897\tEB 2.456,22 €\t4.434,18 €
2 Durchstechfl. 25 mg/ml\t\t19478508\tEB 4.854,03 €\t8.809,93 €
"""
WINREVAIR_TXT = """PACKUNGSANGABEN
\t\tPZN\tAVP (EB)/FB\tAVP/UVP
1 Vial KIT 45 mg\tN1\t19158442\tEB 4.535,78 €\t7.894,51 €
2 Vial KIT 60 mg\tN2\t19159306\tEB 11.998,06 €\t20.954,65 €
"""
WELIREG_TXT = "PACKUNGSANGABEN\n\t\tPZN\tAVP (EB)/FB\tAVP/UVP\n90 Filmtbl.\t\t19460460\tEB 4.570,97 €\t17.831,08 €\n"
IQIRVO_TXT = "PACKUNGSANGABEN\n30 Filmtbl.\tN1\t19394987\tEB 4.607,58 €\t4.607,58 €\n90 Filmtbl.\t\t19877616\tEB 13.705,91 €\t13.705,91 €\n"


def test_parse_keytruda_picks_both_columns():
    rows = parse_packungsangaben(KEYTRUDA_TXT)
    assert [(r["pzn"], r["avp_eb_fb"], r["avp_uvp"]) for r in rows] == [
        ("10749897", 2456.22, 4434.18), ("19478508", 4854.03, 8809.93)]
    assert rows[0]["eb_label"] == "EB" and rows[0]["n"] == "N1" and rows[1]["n"] is None


def test_parse_other_products():
    w = parse_packungsangaben(WINREVAIR_TXT)
    assert (w[0]["avp_eb_fb"], w[0]["avp_uvp"]) == (4535.78, 7894.51) and w[1]["avp_uvp"] == 20954.65
    assert parse_packungsangaben(WELIREG_TXT)[0]["avp_uvp"] == 17831.08
    i = parse_packungsangaben(IQIRVO_TXT)
    assert [(r["pzn"], r["avp_uvp"]) for r in i] == [("19394987", 4607.58), ("19877616", 13705.91)]


def test_parse_single_column_is_not_guessed():
    only_eb = parse_packungsangaben("PACKUNGSANGABEN\n1 Filmtbl.\t\t12345678\tEB 10,50 €\n")
    assert only_eb[0]["avp_eb_fb"] == 10.50 and only_eb[0]["avp_uvp"] is None      # EB 만 있으면 UVP 를 만들어 내지 않는다
    only_uvp = parse_packungsangaben("PACKUNGSANGABEN\n1 Filmtbl.\t\t12345678\t10,50 €\n")
    assert only_uvp[0]["avp_uvp"] == 10.50 and only_uvp[0]["avp_eb_fb"] is None
    assert parse_packungsangaben("PACKUNGSANGABEN\nPreisangaben sind nur für Fachkreise verfügbar.\n") == []
