"""
외국약가 A8 조정가격 산출기

근거: `_resource/국가별 공장도 출하가격 산출식_2025.3월 개정 버전.xlsx`
     규정: 2025.03.05 HIRA 규정 제527호 제32조

수식:
  조정가(KRW) = [(각국 공장도출하가 × 환율) × (1 + VAT)] × (1 + 유통거래폭)

주의:
  - Excel K열 수식 중 France/Italy/Switzerland 는 0.79 하드코딩 오류.
    본 모듈은 H열(공식 비율)을 정상 적용하여 계산한다.
  - Germany 는 독립 수식 (참조세·도매마진 공제) 사용.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

# ────────────────────────────────────────────────────────────
# 규정 상수 (2025.3월 개정 기준)
# ────────────────────────────────────────────────────────────

VAT_RATE            = 0.10    # 부가가치세율
DISTRIBUTION_MARGIN = 0.0869  # 유통거래폭 (8.69%)

# 국가별 공장도 출하 비율 (Excel H열 기준; Germany 는 복합식)
FACTORY_RATIOS = {
    "UK":           0.73,
    "US":           0.74,
    "CA":           0.81,
    "JP":           0.79,
    "FR":           0.77,
    "IT":           0.93,
    "CH":           0.73,
    # DE: 복합식 — calculate_germany() 사용
}

# Excel 평균환율 (2024-01-31 ~ 2026-01-31). 런타임에 최신 환율로 덮어쓸 것.
DEFAULT_FX = {
    "UK": 1821.01, "US": 1398.72, "CA": 1009.68, "JP":   9.2645,
    "FR": 1552.42, "DE": 1552.42, "IT": 1552.42, "CH": 1645.20,
}


@dataclass
class AdjustmentResult:
    country:          str
    unit_price_local: float            # 최소단위당 판매가 (현지 통화)
    unit_price_krw:   float            # 최소단위당 KRW
    factory_price:    float            # 공장도출하가 (KRW, 비율 적용 후)
    adjusted_krw:     float            # 최종 A8 조정가
    ratio_used:       float            # 적용된 공장도 비율
    fx_rate:          float            # 환율
    formula:          str              # 적용 수식 (설명)


# ────────────────────────────────────────────────────────────
# 계산기
# ────────────────────────────────────────────────────────────

def calculate_one(
    country:       str,
    unit_price:    float,
    fx_rate:       Optional[float] = None,
    vat:           float = VAT_RATE,
    margin:        float = DISTRIBUTION_MARGIN,
) -> AdjustmentResult:
    """
    단일 국가 조정가 계산.

    Args:
        country: 'UK'|'US'|'CA'|'JP'|'FR'|'DE'|'IT'|'CH'
        unit_price: 최소단위당 판매가 (현지 통화)
        fx_rate: KRW/local 환율. None이면 DEFAULT_FX 사용.
    """
    country = country.upper()
    fx = fx_rate if fx_rate is not None else DEFAULT_FX.get(country)
    if fx is None:
        raise ValueError(f"환율 미지정 국가: {country}")

    if country == "DE":
        return _calculate_germany(unit_price, fx, margin)

    ratio = FACTORY_RATIOS.get(country)
    if ratio is None:
        raise ValueError(f"지원하지 않는 국가: {country}")

    unit_krw    = unit_price * fx
    factory_krw = unit_krw * ratio
    adjusted    = factory_krw * (1 + vat) * (1 + margin)

    return AdjustmentResult(
        country=country,
        unit_price_local=unit_price,
        unit_price_krw=unit_krw,
        factory_price=factory_krw,
        adjusted_krw=adjusted,
        ratio_used=ratio,
        fx_rate=fx,
        formula=f"(unit × {fx}) × {ratio} × (1+{vat}) × (1+{margin})",
    )


def _calculate_germany(unit_local: float, fx: float, margin: float) -> AdjustmentResult:
    """
    Germany 독립 수식 (Excel H22):
      공장도 = [(F/1.19 - 8.35)/1.03 - 0.7] / 1.0315
      이후 × 0.93 × 환율 × 1.1 × 1.0869
    """
    step1 = (unit_local / 1.19) - 8.35          # VAT 19% 제거, 약사 마진 공제
    step2 = step1 / 1.03                         # 도매 3% 제거
    step3 = (step2 - 0.7) / 1.0315              # 고정수수료 + 공보험 환급률
    ratio = 0.93
    factory_local = step3 * ratio
    factory_krw   = factory_local * fx
    adjusted      = factory_krw * 1.10 * (1 + margin)
    return AdjustmentResult(
        country="DE",
        unit_price_local=unit_local,
        unit_price_krw=unit_local * fx,
        factory_price=factory_krw,
        adjusted_krw=adjusted,
        ratio_used=ratio,
        fx_rate=fx,
        formula="[(F/1.19-8.35)/1.03-0.7]/1.0315 × 0.93 × fx × 1.10 × 1.0869",
    )


def calculate_a8_min(
    prices: dict[str, float],
    fx_rates: Optional[dict[str, float]] = None,
    subset: Optional[list[str]] = None,
) -> dict:
    """
    다국가 조정가 최저가 산출.

    Args:
        prices: {"UK": 132.63, "US": 339.46, ...}  최소단위당 현지 가격
        fx_rates: 국가별 환율 override
        subset: 최저가 산출 대상 국가 제한 (None → 제공된 전 국가)

    Returns:
        {"per_country": [...], "min_adjusted": {...}, "subset_used": [...]}
    """
    fx_rates = fx_rates or {}
    results = []
    for ctry, p in prices.items():
        try:
            r = calculate_one(ctry, p, fx_rate=fx_rates.get(ctry))
            results.append(r)
        except ValueError as e:
            results.append({"country": ctry, "error": str(e)})

    valid = [r for r in results if isinstance(r, AdjustmentResult)]
    if subset:
        pool = [r for r in valid if r.country in subset]
    else:
        pool = valid
    min_r = min(pool, key=lambda x: x.adjusted_krw) if pool else None

    return {
        "per_country": [
            r.__dict__ if isinstance(r, AdjustmentResult) else r for r in results
        ],
        "min_adjusted": min_r.__dict__ if min_r else None,
        "subset_used": [r.country for r in pool],
    }
