"""Welireg per-tablet A8 조정가 회귀 테스트.

출처: data/raw/한국엠에스디_웰리렉정_재정영향분석.xlsx (Cost_drug 시트, 2025.3 업데이트)

**PriceCalculator 또는 factory_ratio 수정 시 반드시 통과.**
5개국 각 Excel 값과 ±1% 이내여야 함 (동일 FX 조건 하).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.exchange_rate import PriceCalculator

# Excel (Cost_drug 시트) 에 기재된 per-tablet A8 조정가 KRW
# FX 는 Excel 고유 값 사용 (2023-02-28 ~ 2025-02-28 KEB 평균)
WELIREG_EXCEL_BASELINE = [
    # (country, pack_local, pack_count, fx, source_type, expected_per_tablet_krw)
    ("UK", 11936.70, 90, 1707.75, None, 197_684),
    ("US", 31162.50, 90, 1351.85, None, 414_126),
    ("CA",   213.33,  1,  988.63, None, 204_246),
    ("JP", 21916.80,  1,    9.1559, None, 189_534),
    ("DE", 17830.31, 90, 1457.84, None, 240_163),
]

TOLERANCE = 0.01   # ±1%


def test_welireg_excel_baseline():
    calc = PriceCalculator()
    failures = []
    for country, pack_local, pack_count, fx, src_type, expected in WELIREG_EXCEL_BASELINE:
        result = calc.calculate_adjusted_price(
            country=country,
            listed_price=pack_local,
            exchange_rate=fx,
            pack_count=pack_count,
            source_type=src_type,
        )
        actual = result["adjusted_price_krw"]
        diff_pct = (actual - expected) / expected
        ok = abs(diff_pct) < TOLERANCE
        status = "OK" if ok else "FAIL"
        print(
            f"  [{status}] {country}: expected={expected:>9} actual={actual:>9} "
            f"diff={diff_pct*100:+.3f}%"
        )
        if not ok:
            failures.append((country, expected, actual, diff_pct))

    assert not failures, (
        "Welireg Excel baseline 회귀 — 재정영향분석 공식과 ±1% 초과 편차: "
        + ", ".join(
            f"{c}: {exp} vs {act} ({d*100:+.2f}%)"
            for c, exp, act, d in failures
        )
    )


def test_kr_constants_unchanged():
    """Korean A8 uplift 상수 변경 방지 — 재정영향분석 표준값."""
    assert PriceCalculator.KR_VAT == 0.10, "KR_VAT 는 한국 부가가치세 10% (고정)"
    assert PriceCalculator.KR_DIST_MARGIN == 0.0869, "KR_DIST_MARGIN 은 8.69% (고정)"


def test_factory_ratio_unchanged():
    """국가별 factory_ratio 변경 방지 — 재정영향분석 표준값."""
    expected = {
        "US": 0.74, "UK": 0.73, "DE": 0.6955,
        "FR": 0.77, "IT": 0.93, "CH": 0.73,
        "JP": 0.79, "CA": 0.81,
    }
    for country, ratio in expected.items():
        assert PriceCalculator.FACTORY_RATIO[country] == ratio, (
            f"{country} factory_ratio 변경됨 (expected {ratio})"
        )


if __name__ == "__main__":
    print("=== Welireg Excel baseline regression ===")
    test_kr_constants_unchanged()
    print("  KR_VAT / KR_DIST_MARGIN OK")
    test_factory_ratio_unchanged()
    print("  factory_ratio table OK")
    test_welireg_excel_baseline()
    print("\n✔ All Welireg Excel baseline checks passed")
