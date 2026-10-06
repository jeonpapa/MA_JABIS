"""injection 최소단위 mg = per-vial 총량 — per-mL 농도를 분모로 쓰는 경로 차단 (CLAUDE.md 절대 금지).

`_extract_per_unit_mg("injection", …)` 는 "N mg/M ml"(M>1) 또는 농도 × volume 으로만 per-vial 총량을 복원한다.
dosage_strength 가 "25 mg" 처럼 bare 여도 product_name/dosage_form 에 "25 mg/ml" 농도 표기가 있으면 25 는 농도다 —
volume 을 못 찾으면 None 을 돌려주고 호출측(daily_cost·제형 보정)은 **계산을 건너뛴다**(추정 금지).

데이터 검사(`check_foreign_price_data`)는 저장된 priced injection 행 중 unit_strength_mg 가 농도로 보이는 행을
QG drift 로 보고한다 — 코드 패턴 grep 만으로는 간접 fallback(`return cls._extract_mg(s)`) 이 통과했기 때문.

실행: .venv/bin/python -m pytest tests/test_foreign_injection_unit_mg.py -q
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.foreign_price_agent import ForeignPriceAgent as A

CASES = [
    # (dosage_strength, package_unit, product_name, dosage_form, expected per-vial mg)
    ("100mg/4ml conc for soln for inf in vial, 2", "", "Keytruda", "", 100.0),                          # UK
    ("395mg/2.4ml soln for inj in vial, 1", "", "Keytruda", "", 395.0),
    ("25 mg/ml - concentrato … flaconcino (vetro) - 4 ml” 1 flaconcino", "1", "KEYTRUDA", "", 100.0),  # IT TNF
    ("25 mg", "1 flacon(s) en verre de 4 ml", "KEYTRUDA 25 mg/mL, solution à diluer pour perfusion", "", 100.0),   # FR BDPM #1502
    ("25 mg", "", "KEYTRUDA 25 mg/ml sol diluer p perf", "solution pour perfusion", None),            # FR Vidal #1504 — volume 없음
    ("25 mg/ml", "", "KEYTRUDA® 25 mg/ml Konzentrat zur Herstellung einer Infusionslösung", "", None),  # DE #1470
    ("20 mg/1 ml", "12 ml", "Prevymis", "", 240.0),                                                     # US Redbook 농도 × 부피
    ("20 mg/1 ml", "24 ml", "Prevymis", "", 480.0),
    ("１００ｍｇ４ｍＬ１瓶", "", "キイトルーダ点滴静注１００ｍｇ", "注射薬", 100.0),                  # JP — bare 100mg + 4mL 는 총량 표기
    ("PREVYMIS*1 flaconcino EV infus 240 mg 12 ml", "", "", "LETERMOVIR 240MG 12ML 1 UNITA' USO PARENTERALE", 240.0),   # IT CSV
    ("240 mg", "", "PREVYMIS® 240 mg/-480 mg Konzentrat zur Herstellung einer Infusionslösung", "", 240.0),   # DE — 농도 표기 없음 → 총량
    ("KEYTRUDA Inf Konz 100 mg/4ml", "", "", "", 100.0),                                                 # CH
    ("25 mg/ml", "4 ml", "", "", 100.0),
    ("", "", "", "", None),
]


@pytest.mark.parametrize("ds,pu,pn,df,expected", CASES)
def test_injection_per_vial_mg(ds, pu, pn, df, expected):
    assert A._extract_per_unit_mg("injection", ds, pu, product_name=pn, dosage_form=df) == expected


def test_oral_is_per_tablet_mg_unchanged():
    assert A._extract_per_unit_mg("oral", "40mg blue oval f-c tab marked 177, 90") == 40.0
    assert A._extract_per_unit_mg("oral", "JANUVIA*28 cpr rivestite 100 mg") == 100.0


def test_daily_cost_skips_when_per_vial_mg_unknown():
    """volume 을 모르면 daily_cost 는 None + note — 농도 25 로 나눠 ₩1,083,683 을 만들면 안 된다."""
    a = A.__new__(A)
    dosing = {"dose_per_cycle_mg": 200.0, "cycle_days": 21}
    item = {"country": "FR", "form_type": "injection", "dosage_strength": "25 mg", "package_unit": "",
            "product_name": "KEYTRUDA 25 mg/ml sol diluer p perf", "adjusted_price_krw": 2_844_669, "pack_count": 1}
    a._populate_daily_cost(item, dosing)
    assert item["daily_cost_krw"] is None and item["total_pkg_mg"] is None
    assert item["daily_cost_note"] == "unit_mg_unresolved"
    assert item["unit_strength_unresolved"] is True


def test_formulation_layer_does_not_fall_back_to_concentration():
    """에이전트가 per-vial 복원 불가로 판정한 행은 process_formulations 의 regex fallback 이 25 를 강도로 쓰면 안 된다
    (저장된 FR #1504 dose_norm_factor 4.0 / normalized ₩11,378,676 가 그 결과였다)."""
    from agents.foreign_dose_normalize import _strength_of
    row = {"form_type": "injection", "dosage_strength": "25 mg", "unit_strength_mg": None,
           "unit_strength_unresolved": True}
    assert _strength_of(row) is None
    plain = {"form_type": "injection", "dosage_strength": "240 mg", "unit_strength_mg": None}
    assert _strength_of(plain) == 240.0        # 플래그 없으면 기존 동작 유지


# ── QG 데이터 검사: 저장 행의 unit_strength_mg 가 농도로 보이면 drift ─────────────

def _seed(db: Path, rows: list[dict]) -> None:
    from agents.db import DrugPriceDB
    d = DrugPriceDB(db)                     # 스키마 + 마이그레이션 (임시 파일)
    for r in rows:
        d.save_foreign_price({"searched_at": "2026-07-28T10:05:00", "query_name": "keytruda", "currency": "EUR",
                              "source_url": "", "source_label": "", "raw_data": None, "exchange_rate": 1544.43,
                              "exchange_rate_from": "20230603", "exchange_rate_to": "20260531",
                              "vat_rate": 0.1, "distribution_margin": 0.0869, "ingredient": "", **r})


def test_check_foreign_price_data_flags_concentration_as_unit(tmp_path, monkeypatch):
    from agents.quality_guard import deviations
    monkeypatch.setattr(deviations, "GUARD_DIR", tmp_path / "qg")          # 모듈 상수는 import 시점 — 직접 바꾼다
    monkeypatch.setattr(deviations, "DEVIATION_LOG", tmp_path / "qg" / "deviation_log.jsonl")
    from agents.quality_guard.checks import check_foreign_price_data
    db = tmp_path / "p.db"
    _seed(db, [
        {"country": "FR", "product_name": "KEYTRUDA 25 mg/ml sol diluer p perf", "dosage_strength": "25 mg",
         "dosage_form": "solution pour perfusion", "package_unit": "", "local_price": 2000.74,
         "factory_price_krw": 2379302, "adjusted_price_krw": 2844669, "pack_count": 1, "per_unit_local": 2000.74,
         "form_type": "injection", "unit_strength_mg": 25.0, "total_pkg_mg": 25.0, "daily_cost_krw": 1083683,
         "dose_norm_factor": 4.0, "adjusted_price_krw_normalized": 11378676},
        {"country": "DE", "product_name": "KEYTRUDA® 25 mg/ml Konzentrat zur Herstellung einer Infusionslösung",
         "dosage_strength": "25 mg/ml", "dosage_form": "", "package_unit": "", "local_price": 2456.22,
         "factory_price_krw": 2638351, "adjusted_price_krw": 3154386, "pack_count": 1, "per_unit_local": 2456.22,
         "form_type": "injection", "unit_strength_mg": 25.0, "dose_norm_factor": 4.0,
         "adjusted_price_krw_normalized": 12617544},
        {"country": "IT", "product_name": "KEYTRUDA", "package_unit": "1",
         "dosage_strength": "25 mg/ml - concentrato … flaconcino (vetro) - 4 ml” 1 flaconcino",
         "dosage_form": "", "local_price": 3798.34, "factory_price_krw": 5866270, "adjusted_price_krw": 7013654,
         "pack_count": 1, "per_unit_local": 3798.34, "form_type": "injection", "unit_strength_mg": 100.0,
         "total_pkg_mg": 100.0, "daily_cost_krw": 667967, "dose_norm_factor": 1.0,
         "adjusted_price_krw_normalized": 7013654},
        {"country": "XX", "product_name": "bad", "dosage_strength": "1 mg", "dosage_form": "", "package_unit": "",
         "local_price": 1.0, "factory_price_krw": 1, "adjusted_price_krw": 1, "pack_count": 1, "per_unit_local": 1.0,
         "form_type": "oral", "currency": "ZZZ"},
    ])
    findings = check_foreign_price_data(db)
    kinds = {(f["check"], f["country"]) for f in findings}
    assert ("injection_concentration_as_unit", "FR") in kinds
    assert ("injection_concentration_as_unit", "DE") in kinds
    assert ("injection_concentration_as_unit", "IT") not in kinds         # 100mg 복원 가능 → 정상
    assert ("invalid_country_code", "XX") in kinds and ("invalid_currency_code", "XX") in kinds
    fr = next(f for f in findings if f["check"] == "injection_concentration_as_unit" and f["country"] == "FR")
    # record_quality_review 가 이해하는 drift 형태 (rule/file/severity) + 사람이 찾을 row id
    assert fr["severity"] == "ERROR" and fr["rule"] and "foreign_drug_prices" in fr["file"]
    assert str(fr["row_id"]) in fr["file"]
    # deviation_log 에도 남는다
    log = (tmp_path / "qg" / "deviation_log.jsonl").read_text(encoding="utf-8")
    assert "injection_concentration_as_unit" in log


def test_cached_results_do_not_apply_stored_concentration_factor(tmp_path):
    """저장된 FR #1504 (unit 25, factor 4.0, normalized ₩11,378,676) 를 읽을 때 표시값은 raw 로 돌아가고 daily 는 None.
    저장 행 자체는 바꾸지 않는다 (read 경로 재계산만)."""
    from agents.db import DrugPriceDB
    from agents.exchange_rate import PriceCalculator
    db = tmp_path / "c.db"
    _seed(db, [{
        "country": "FR", "product_name": "KEYTRUDA 25 mg/ml sol diluer p perf", "dosage_strength": "25 mg",
        "dosage_form": "solution pour perfusion", "package_unit": "", "local_price": 2000.74,
        "factory_price_krw": 2379302, "adjusted_price_krw": 2844669, "pack_count": 1, "per_unit_local": 2000.74,
        "form_type": "injection", "unit_strength_mg": 25.0, "total_pkg_mg": 25.0, "daily_cost_krw": 1083683,
        "dose_norm_factor": 4.0, "adjusted_price_krw_normalized": 11378676, "formulation_key": "100mg|injection",
    }])
    a = A.__new__(A)
    a.db = DrugPriceDB(db)
    a.calculator = PriceCalculator()
    a._build_scraper = lambda country: type("S", (), {"SOURCE_TYPE": "vidal"})()
    row = a.get_cached_results("keytruda")["FR"][0]
    assert row["adjusted_price_krw"] == 2_844_669
    assert row["adjusted_price_krw_normalized"] == 2_844_669 and row["dose_norm_factor"] is None
    assert row["daily_cost_krw"] is None and row["unit_strength_mg"] is None
    assert row["daily_cost_note"] == "unit_mg_unresolved" and "복원 불가" in row["dose_norm_note"]
    stored = sqlite3.connect(db).execute(
        "SELECT dose_norm_factor, adjusted_price_krw_normalized, daily_cost_krw FROM foreign_drug_prices").fetchone()
    assert stored == (4.0, 11378676, 1083683)


def test_check_foreign_price_data_handles_missing_db(tmp_path):
    from agents.quality_guard.checks import check_foreign_price_data
    assert check_foreign_price_data(tmp_path / "none.db") == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
