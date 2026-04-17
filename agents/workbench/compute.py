"""Scenario Computation — 시나리오별 A8 조정가 산출

입력: {country: local_price} + 시나리오 설정 + 가정치 [+ dose 메타]
출력: 조정가 dict + 통계 (min, avg, min*N%, avg*N%)

dose_info (옵션): dose_normalizer.normalize_prices() 반환값.
제공 시 각 국가의 local_price 대신 equivalent_price (기준 mg 환산가) 사용.
"""

from __future__ import annotations

from typing import TypedDict

from .dose_normalizer import normalize_prices


class ScenarioSpec(TypedDict, total=False):
    name:             str         # "A안" | "B안" | ...
    include_countries: list[str]  # ["JP","IT","FR",...]
    formula:          str         # "min_n" | "avg_n"
    percent:          float       # 0.90 (= 90%)
    fx_override:      dict        # {country: fx_rate} — 환율 override
    notes:            str


def _calc_row(local_price: float, fx_rate: float, factory_ratio: float,
              vat_rate: float, margin_rate: float) -> dict:
    """한 국가의 11-컬럼 조정가 산출."""
    krw_converted = local_price * fx_rate
    factory_local = local_price * factory_ratio
    factory_krw   = factory_local * fx_rate
    vat_applied   = factory_krw * (1 + vat_rate)
    adjusted      = vat_applied * (1 + margin_rate)
    return {
        "local_price":   round(local_price, 4),
        "fx_rate":       round(fx_rate, 4),
        "krw_converted": round(krw_converted),
        "factory_ratio": factory_ratio,
        "factory_local": round(factory_local, 4),
        "factory_krw":   round(factory_krw),
        "vat_rate":      vat_rate,
        "vat_applied":   round(vat_applied),
        "margin_rate":   margin_rate,
        "adjusted":      round(adjusted),
    }


def compute_scenario(
    prices: dict[str, float],           # {country: local_price}
    scenario: ScenarioSpec,
    assumptions: dict,
    dose_info: dict | None = None,      # normalize_prices() 반환값
) -> dict:
    """단일 시나리오 조정가 산출.

    dose_info 제공 시 각 국가의 local_price 대신 equivalent_price (reference_mg
    기준 환산가) 사용. confidence='combo' 또는 equivalent_price=None 국가는
    계산 제외되며 `excluded` 리스트에 사유와 함께 기록.

    Returns:
        {
          "name": "A안",
          "rows": {country: {11-col dict + dose info}, ...},
          "excluded": {country: "reason", ...},
          "stats": {...},
          "proposed_ceiling": <KRW>,
          "basis": "JP × 90%" | "avg × 85%",
          "reference_mg": float | None,
        }
    """
    include = scenario.get("include_countries") or list(prices.keys())
    formula = scenario.get("formula", "min_n")
    percent = float(scenario.get("percent", 0.90))
    fx_override = scenario.get("fx_override") or {}
    reference_mg = (dose_info or {}).get("_reference_mg")

    rows: dict[str, dict] = {}
    excluded: dict[str, str] = {}
    for country in include:
        if country not in prices or prices[country] is None:
            excluded[country] = "local_price=None"
            continue
        ac = assumptions["countries"].get(country)
        if not ac:
            excluded[country] = "국가가정치 없음"
            continue
        if ac.get("phase") == 2:
            excluded[country] = "Phase 2 (계산 제외)"
            continue

        effective_price = prices[country]
        dinfo = (dose_info or {}).get(country) if dose_info else None
        if dinfo:
            if dinfo["confidence"] == "combo":
                excluded[country] = "복합제 (동등비교 불가)"
                continue
            if dinfo["equivalent_price"] is None:
                excluded[country] = f"dose 파싱 실패 ({dinfo.get('confidence')})"
                continue
            effective_price = dinfo["equivalent_price"]

        fx = fx_override.get(country, ac["fx_rate_default"])
        row = _calc_row(
            local_price=effective_price,
            fx_rate=fx,
            factory_ratio=ac["factory_ratio"],
            vat_rate=ac["vat_rate"],
            margin_rate=ac["margin_rate"],
        )
        if dinfo:
            row.update({
                "raw_local_price":  prices[country],
                "mg_pack_total":    dinfo["mg_pack_total"],
                "price_per_mg":     round(dinfo["price_per_mg"], 6) if dinfo["price_per_mg"] else None,
                "reference_mg":     reference_mg,
                "dose_confidence":  dinfo["confidence"],
                "form":             dinfo.get("form"),
            })
        rows[country] = row

    if not rows:
        return {
            "name":             scenario.get("name", "unnamed"),
            "rows":             {},
            "excluded":         excluded,
            "stats":            {},
            "proposed_ceiling": None,
            "basis":            "제외국 없음",
            "reference_mg":     reference_mg,
        }

    adjusted_vals = {c: r["adjusted"] for c, r in rows.items()}
    min_country = min(adjusted_vals, key=adjusted_vals.get)
    min_val = adjusted_vals[min_country]
    avg_val = sum(adjusted_vals.values()) / len(adjusted_vals)

    stats = {
        "min":              min_val,
        "min_country":      min_country,
        "avg":              round(avg_val),
        "min_percent":      round(min_val * percent),
        "avg_percent":      round(avg_val * percent),
        "n_countries":      len(rows),
        "percent":          percent,
    }

    if formula == "min_n":
        ceiling = stats["min_percent"]
        basis   = f"{min_country} × {int(percent*100)}%"
    elif formula == "avg_n":
        ceiling = stats["avg_percent"]
        basis   = f"avg × {int(percent*100)}%"
    else:
        ceiling = None
        basis   = f"unknown formula: {formula}"

    return {
        "name":             scenario.get("name", "unnamed"),
        "rows":             rows,
        "excluded":         excluded,
        "stats":            stats,
        "proposed_ceiling": ceiling,
        "basis":            basis,
        "notes":            scenario.get("notes", ""),
        "reference_mg":     reference_mg,
    }


def compute_all_scenarios(
    prices: dict[str, float],
    scenarios: list[ScenarioSpec],
    assumptions: dict,
    rows_meta: dict | None = None,
    product_slug: str | None = None,
    reference_mg: float | None = None,
) -> list[dict]:
    """여러 시나리오 병렬 산출.

    rows_meta 가 있으면 dose 정규화를 수행 (국가별 SKU 가 다른 pack/strength 를
    동등 mg 기준으로 환산). rows_meta=None 이면 raw local_price 로 계산 (기존 동작).

    Args:
        rows_meta: {country: {product_name, strength, pack, form}}
        product_slug: 'keytruda' 등 — REFERENCE_SKU 폴백용
        reference_mg: 동등비교 기준 mg. None 이면 자동결정 (최빈값)
    """
    dose_info = None
    if rows_meta:
        dose_info = normalize_prices(prices, rows_meta, product_slug, reference_mg)
    return [compute_scenario(prices, s, assumptions, dose_info) for s in scenarios]
