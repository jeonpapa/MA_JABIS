"""JPY 환율 단위 — KEB 는 '100엔당 KRW' 로 고시한다. 모든 소비처는 per-1 엔 으로 본다.

2026-10 감사: parse_keb_excel 은 통화 셀에 "100" 이 있을 때만 /100 했는데 현재 KEB 행은 "일본 JPY" 라 939.36(100엔당)이
그대로 저장됐다. PriceCalculator(>100 → /100)·프론트는 보정하지만 foreign_report 는 923.21 을 '적용환율' 로 xlsx/hwpx 에
썼다(환산가는 per-1 로 계산돼 표가 자기모순). 또 월간 recompute_foreign_fx 는 old/new 를 그대로 나누므로 저장 923.21
vs 새 9.39 가 만나면 100× 축소가 났을 것이다.

결정: 단일 헬퍼 `per_unit_rate(currency, rate)` 를 소스(parse)·계산기·리포트·재계산이 공유한다. 저장된 레거시 행
(923.21) 은 건드리지 않고 읽을 때 정규화한다.

실행: .venv/bin/python -m pytest tests/test_exchange_rate_jpy_per_unit.py -q
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.exchange_rate import PriceCalculator, parse_keb_excel, per_unit_rate


def _keb_tsv(path: Path, jpy_label: str, jpy_rate: str) -> Path:
    header = "통화\t현찰사실때\t현찰파실때\t송금보내실때\t송금받으실때\tT/C사실때\t외화수표파실때\t매매기준율\t환가료율\t미화환산율"
    rows = [
        "미국 USD\t0\t0\t0\t0\t0\t0\t1,351.85\t0\t1",
        f"{jpy_label}\t0\t0\t0\t0\t0\t0\t{jpy_rate}\t0\t0.0068",
        "유로 EUR\t0\t0\t0\t0\t0\t0\t1,544.43\t0\t1.1",
    ]
    path.write_bytes(("기간평균환율\n" + header + "\n" + "\n".join(rows) + "\n").encode("euc-kr"))
    return path


def test_per_unit_rate_normalizes_only_jpy_per_100():
    assert per_unit_rate("JPY", 923.21) == pytest.approx(9.2321)
    assert per_unit_rate("JPY", 9.39) == 9.39                     # 이미 per-1
    assert per_unit_rate("jpy", 939.36) == pytest.approx(9.3936)
    assert per_unit_rate("USD", 1351.85) == 1351.85
    assert per_unit_rate("JPY", None) is None


def test_parse_keb_excel_normalizes_jpy_regardless_of_label(tmp_path):
    with_100 = parse_keb_excel(_keb_tsv(tmp_path / "a.xlsx", "일본 JPY (100)", "939.36"))
    without = parse_keb_excel(_keb_tsv(tmp_path / "b.xlsx", "일본 JPY", "939.36"))
    assert with_100["JPY"] == pytest.approx(9.3936)
    assert without["JPY"] == pytest.approx(9.3936)                # 라벨에 100 이 없어도 100엔당 고시
    assert without["USD"] == 1351.85 and without["EUR"] == 1544.43


def test_calculator_accepts_both_conventions():
    c = PriceCalculator()
    a = c.calculate_adjusted_price("JP", 199462.0, 923.21, pack_count=1)
    b = c.calculate_adjusted_price("JP", 199462.0, 9.2321, pack_count=1)
    assert a["adjusted_price_krw"] == b["adjusted_price_krw"] == 1_739_282
    assert a["exchange_rate"] == pytest.approx(9.2321)


def test_report_applied_rate_is_per_unit_jpy():
    from agents.foreign_report import build_report_model, _rows_for_render
    cached = {"JP": [{"adjusted_price_krw": 1739282, "adjusted_price_krw_normalized": 1739282, "local_price": 199462.0,
                      "per_unit_local": 199462.0, "exchange_rate": 923.21, "krw_converted": 1841453, "pack_count": 1,
                      "currency": "JPY", "a8_aggregate_eligible": True, "source_label": "MHLW",
                      "exchange_rate_from": "20230603", "exchange_rate_to": "20260531"}]}
    m = build_report_model(cached)
    jp = m["cols"][0]
    assert jp["exchange_rate"] == pytest.approx(9.2321)
    assert jp["krw_converted"] == 1841453                         # 환산가 = 199,462 × 9.2321
    _, body = _rows_for_render(m)
    fx_row = next(r for r in body if r[0].startswith("적용환율"))
    assert fx_row[1] == "9.23"
    # krw_converted 가 없을 때도 per-unit 환율로 환산한다 (923.21 로 곱하면 100× 과대)
    cached["JP"][0].pop("krw_converted")
    assert build_report_model(cached)["cols"][0]["krw_converted"] == pytest.approx(199462.0 * 9.2321)


def test_recompute_foreign_fx_scales_jpy_by_per_unit_ratio(tmp_path):
    """저장 923.21(100엔당) ↔ 새 9.3936(per-1) 이 만나도 scale 은 9.3936/9.2321 = 1.0175 (0.0102 가 아니다)."""
    from agents.db import DrugPriceDB
    db = tmp_path / "fx.db"
    d = DrugPriceDB(db)
    d.save_foreign_price({
        "searched_at": "2026-07-27T21:09:15", "query_name": "keytruda", "country": "JP", "product_name": "キイトルーダ",
        "ingredient": "", "dosage_strength": "１００ｍｇ４ｍＬ１瓶", "dosage_form": "注射薬", "package_unit": "",
        "local_price": 199462.0, "currency": "JPY", "exchange_rate": 923.21, "exchange_rate_from": "20230603",
        "exchange_rate_to": "20260531", "factory_price_krw": 1454747, "vat_rate": 0.1, "distribution_margin": 0.0869,
        "adjusted_price_krw": 1739282, "pack_count": 1, "per_unit_local": 199462.0, "daily_cost_krw": 165646,
        "source_url": "", "source_label": "MHLW", "raw_data": None, "form_type": "injection",
    })
    rc = d.recompute_foreign_fx({"JPY": 9.3936, "USD": 1360.0}, {"from": "20230703", "to": "20260630"})
    assert rc["updated"] == 1
    new = sqlite3.connect(db).execute(
        "SELECT exchange_rate, adjusted_price_krw, daily_cost_krw FROM foreign_drug_prices ORDER BY id DESC LIMIT 1").fetchone()
    assert new[0] == pytest.approx(9.3936)
    assert new[1] == pytest.approx(1739282 * 9.3936 / 9.2321, rel=1e-6)
    assert new[2] == pytest.approx(165646 * 9.3936 / 9.2321, rel=1e-6)
    assert rc["details"][0]["old_rate"] == pytest.approx(9.2321) and rc["details"][0]["new_rate"] == pytest.approx(9.3936)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
