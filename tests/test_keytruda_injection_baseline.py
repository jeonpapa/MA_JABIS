"""Keytruda(주사·농도×volume) per-vial A8 조정가 + 일일투약비 baseline 회귀 테스트.

foreign_agent_rules.md §신규 스크레이퍼 검증 필수 2-케이스: Welireg(경구·pack count) 는
`tests/test_welireg_excel_baseline.py` 가, Keytruda(주사) 는 이 파일이 고정한다.

fixture 는 실제 플랫폼 DB(foreign_drug_prices) 의 행을 그대로 복사한 값이고 (id 명시),
기대값은 저장값이 아니라 **규칙대로 다시 계산한 값** 이다 — 저장값 중 UK pack_count 와
FR 주사 분모는 틀려 있었다(2026-10 감사). 산식:

    adj_per_unit = (local_price / pack_count) × fx × factory_ratio × (1+KR_VAT 0.10) × (1+KR_DIST_MARGIN 0.0869)
    daily_cost   = (200 mg / 21 d) × adj_per_unit / per_vial_mg          (pembrolizumab 200mg Q3W)

  UK #1300  £5,260 "100mg/4ml ... in vial, 2" → pack 2 → per-vial £2,630
            2,630 × 1803.75 = 4,743,862.5 → ×0.73 = 3,463,019.6 → ×1.10 = 3,809,321.6 → ×1.0869 = 4,140,351.6 → ₩4,140,351
            daily = 9.5238 × 4,140,351 / 100 mg = ₩394,319          (저장값: pack 1 → ₩8,280,703 / ₩788,638 — 2×)
  FR #1504  €2,000.74 (Vidal UCD = 바이알 1개) "25 mg" — product_name "KEYTRUDA 25 mg/ml" 이므로 25 는 per-mL 농도
            2,000.74 × 1544.43 = 3,090,002.9 → ×0.77 = 2,379,302.2 → ×1.10 → ×1.0869 = 2,844,669.9 → ₩2,844,669
            volume 이 행에 없으면 per-vial mg 복원 불가 → daily_cost **계산 skip(None)**  (저장값: /25 → ₩1,083,683 — 4×)
            같은 품목의 BDPM 포장문(#1502 "1 flacon(s) en verre de 4 ml") 이 있으면 25×4 = 100 mg
            → daily = 9.5238 × 2,844,669 / 100 = ₩270,921
  JP #1497  ¥199,462 per vial, KEB 100엔당 923.21 → per-1 9.2321
            199,462 × 9.2321 = 1,841,453.1 → ×0.79 = 1,454,748.0 → ×1.10 → ×1.0869 = 1,739,282.1 → ₩1,739,282
            daily = 9.5238 × 1,739,282 / 100 = ₩165,646                  (저장값과 동일 — 고정)
  IT #1495  €3,798.34 "1 flaconcino" ex-factory(ratio 1.0): 3,798.34 × 1544.43 = 5,866,270.2 → ×1.10 → ×1.0869 = 7,013,654.0
  IT #1496  €7,596.68 "2 flaconcini" pack 2 → per-vial 동일 ₩7,013,654; daily = 9.5238 × 7,013,654 / 100 = ₩667,967 (고정)

실행: .venv/bin/python -m pytest tests/test_keytruda_injection_baseline.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.exchange_rate import PriceCalculator
from agents.foreign_price_agent import ForeignPriceAgent

# foreign_drug_dosing 의 pembrolizumab 행 (실제 DB 값)
DOSING = {
    "ingredient_key": "pembrolizumab", "display_name": "Pembrolizumab",
    "dose_per_cycle_mg": 200.0, "cycle_days": 21, "schedule_label": "200mg Q3W",
    "default_pack_count": 1,
}

# ── 실제 DB 행 복사 (2026-07 수집, searched_at 생략) ─────────────────────────
UK_1300 = {
    "id": 1300, "query_name": "keytruda", "country": "UK", "product_name": "Keytruda",
    "ingredient": "Pembrolizumab",
    "dosage_strength": "100mg/4ml conc for soln for inf in vial, 2",
    "dosage_form": "", "package_unit": "",
    "local_price": 5260.0, "currency": "GBP", "exchange_rate": 1803.75,
    "form_type": "injection", "source_label": "MIMS online (UK, 로그인)",
    "pack_count": 1,            # 저장값 — 에이전트 default 였지 스크레이퍼 값이 아니다
}
FR_1504 = {
    "id": 1504, "query_name": "keytruda", "country": "FR",
    "product_name": "KEYTRUDA 25 mg/ml sol diluer p perf",
    "dosage_strength": "25 mg", "dosage_form": "solution pour perfusion", "package_unit": "",
    "local_price": 2000.74, "currency": "EUR", "exchange_rate": 1544.43,
    "form_type": "injection", "source_label": "VIDAL.fr",
    "pack_count": 1,
}
FR_1502_PACKAGE = "1 flacon(s) en verre de 4 ml"     # 같은 CIS 62689214 의 BDPM 포장문 (#1502)
JP_1497 = {
    "id": 1497, "query_name": "keytruda", "country": "JP",
    "product_name": "キイトルーダ点滴静注１００ｍｇ", "ingredient": "ペムブロリズマブ（遺伝子組換え）",
    "dosage_strength": "１００ｍｇ４ｍＬ１瓶", "dosage_form": "注射薬", "package_unit": "",
    "local_price": 199462.0, "currency": "JPY", "exchange_rate": 923.21,
    "form_type": "injection", "source_label": "厚生労働省 薬価基準収載品目リスト",
    "pack_count": 1,
}
IT_1495 = {
    "id": 1495, "query_name": "keytruda", "country": "IT", "product_name": "KEYTRUDA",
    "ingredient": "PEMBROLIZUMAB",
    "dosage_strength": ("25 mg/ml - concentrato per soluzione per infusione - uso endovenoso"
                        " - flaconcino (vetro) - 4 ml” 1 flaconcino"),
    "dosage_form": ("25 mg/ml - concentrato per soluzione per infusione - uso endovenoso"
                    " - flaconcino (vetro) - 4 ml” 1 flaconcino"),
    "package_unit": "1",
    "local_price": 3798.34, "currency": "EUR", "exchange_rate": 1544.43,
    "form_type": "injection", "source_label": "AIFA TrovaNorme (Determina prezzo, ex-factory)",
    "pack_count": 1,
}
IT_1496 = {
    **IT_1495, "id": 1496,
    "dosage_strength": ("25 mg/ml - concentrato per soluzione per infusione - uso endovenoso"
                        " - flaconcino (vetro) 4 ml - 2 flaconcini"),
    "dosage_form": ("25 mg/ml - concentrato per soluzione per infusione - uso endovenoso"
                    " - flaconcino (vetro) 4 ml - 2 flaconcini"),
    "package_unit": "2", "local_price": 7596.68, "pack_count": 2,
}

SOURCE_TYPE = {"UK": None, "FR": "vidal", "JP": None, "IT": "aifa_exfactory"}
TOL = 0.001   # ±0.1% — int 절사 외 편차 없음


def _agent() -> ForeignPriceAgent:
    """DB·스크레이퍼 없이 가격 산출 메서드만 쓴다."""
    a = ForeignPriceAgent.__new__(ForeignPriceAgent)
    a.calculator = PriceCalculator()
    return a


def _price(agent: ForeignPriceAgent, row: dict, *, fresh: bool = False) -> dict:
    """search_one_country / get_cached_results 의 per-item 산출 경로 재현:
    pack_count 결정 → A8 per-unit 조정가 → 일일투약비.  fresh=True 는 스크레이퍼 직후 item
    (id·저장 pack_count 없음), False 는 DB 캐시 행(저장 pack_count 는 에이전트 산물)."""
    item = dict(row)
    if fresh:
        item.pop("id", None)
        if row["country"] != "IT":          # IT TNF 만 스크레이퍼가 pack_count 를 명시한다
            item.pop("pack_count", None)
    pack_count = agent._resolve_pack_count(item["country"], item, DOSING)
    calc = agent.calculator.calculate_adjusted_price(
        country=item["country"], listed_price=item["local_price"],
        exchange_rate=item["exchange_rate"], pack_count=pack_count,
        source_type=SOURCE_TYPE[item["country"]],
    )
    item.update({
        "factory_ratio": calc["factory_ratio"], "adjusted_price_krw": calc["adjusted_price_krw"],
        "pack_count": calc["pack_count"], "per_unit_local": calc["per_unit_listed"],
    })
    agent._populate_daily_cost(item, DOSING)
    return item


def _close(actual, expected) -> bool:
    return actual is not None and abs(actual - expected) <= max(1, expected * TOL)


def test_uk_two_vial_pack_is_halved():
    """UK MIMS "in vial, 2" = 2 바이알 팩 → per-vial 는 팩가의 절반 (저장값은 2× 과대)."""
    a = _agent()
    for fresh in (True, False):
        r = _price(a, UK_1300, fresh=fresh)
        assert r["pack_count"] == 2, f"fresh={fresh}: pack_count {r['pack_count']} (expected 2)"
        assert _close(r["adjusted_price_krw"], 4_140_351), r["adjusted_price_krw"]
        assert _close(r["daily_cost_krw"], 394_319), r["daily_cost_krw"]
        assert r["factory_ratio"] == 0.73


def test_fr_vidal_bare_concentration_without_volume_skips_daily_cost():
    """FR Vidal "25 mg" 는 product_name "25 mg/ml" 의 per-mL 농도 — 분모로 쓰면 4× 과대(절대 금지).
    volume 단서가 행에 없으면 per-vial mg 복원 불가 → daily_cost 는 None (추정 금지). 조정가는 그대로."""
    a = _agent()
    r = _price(a, FR_1504)
    assert r["pack_count"] == 1
    assert _close(r["adjusted_price_krw"], 2_844_669), r["adjusted_price_krw"]
    assert r["factory_ratio"] == 0.77                     # vidal 은 override 미적용 (Item F)
    assert r["daily_cost_krw"] is None, f"농도 25mg 를 분모로 썼다: {r['daily_cost_krw']}"
    assert r.get("total_pkg_mg") is None


def test_fr_concentration_times_bdpm_volume_gives_100mg_vial():
    """같은 품목의 BDPM 포장문 "1 flacon(s) en verre de 4 ml" 이 있으면 25 mg/mL × 4 mL = 100 mg."""
    a = _agent()
    r = _price(a, {**FR_1504, "package_unit": FR_1502_PACKAGE})
    assert r["pack_count"] == 1
    assert _close(r["adjusted_price_krw"], 2_844_669)
    assert r["total_pkg_mg"] == 100.0
    assert _close(r["daily_cost_krw"], 270_921), r["daily_cost_krw"]


def test_jp_per_vial_with_per100_jpy_rate_is_pinned():
    """JP MHLW 는 per-unit 체계, KEB JPY 는 100엔당 고시 → calculator 가 per-1 로 정규화. 저장값과 동일."""
    a = _agent()
    r = _price(a, JP_1497)
    assert r["pack_count"] == 1
    assert _close(r["adjusted_price_krw"], 1_739_282), r["adjusted_price_krw"]
    assert _close(r["daily_cost_krw"], 165_646), r["daily_cost_krw"]
    assert r["total_pkg_mg"] == 100.0


def test_it_tnf_one_and_two_vial_packs_pin_per_vial():
    """IT TrovaNorme ex-factory(ratio 1.0): 1 flaconcino 와 2 flaconcini 의 per-vial 가 같아야 한다."""
    a = _agent()
    for row, pc in ((IT_1495, 1), (IT_1496, 2)):
        for fresh in (True, False):
            r = _price(a, row, fresh=fresh)
            assert r["pack_count"] == pc, (row["id"], fresh, r["pack_count"])
            assert r["factory_ratio"] == 1.0
            assert _close(r["adjusted_price_krw"], 7_013_654), (row["id"], r["adjusted_price_krw"])
            assert _close(r["daily_cost_krw"], 667_967), (row["id"], r["daily_cost_krw"])
            assert r["total_pkg_mg"] == 100.0 * pc


def test_injection_never_uses_mg_ratio_for_pack_count():
    """CLAUDE.md 절대 금지: 농도×volume/농도 = volume 을 pack_count 로 쓰면 안 된다 ("100mg/4ml" 1 vial ≠ 4)."""
    a = _agent()
    item = {"country": "CH", "dosage_strength": "KEYTRUDA Inf Konz 100 mg/4ml", "package_unit": "",
            "form_type": "injection", "local_price": 3000.0}
    assert a._resolve_pack_count("CH", item, DOSING) == 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
