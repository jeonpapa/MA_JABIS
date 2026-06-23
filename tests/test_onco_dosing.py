"""항암 용량 계산 검증 — onco-regimen-dosing 스킬 + 엑셀 계산기 값 대조.

표준환자(키165·체중62·나이60·M·SCr0.9): BSA Mosteller 1.686, GFR(cap125) 76.54.
실행: PYTHONPATH=. .venv/bin/python tests/test_onco_dosing.py
"""
from __future__ import annotations

from agents.onco_dosing import (
    patient_metrics, absolute_dose, compute_drug_dose, compute_regimen_doses, PATIENT_DEFAULT,
)


def _close(a, b, tol=0.5):
    return a is not None and abs(a - b) <= tol


def test_patient_metrics_default():
    m = patient_metrics()
    assert _close(m["bsa"], 1.6857, 0.001), m["bsa"]      # 엑셀 계산기 1.68572
    assert _close(m["bsa_dubois"], 1.6819, 0.001)
    assert _close(m["crcl"], 76.54, 0.1) and _close(m["gfr"], 76.54, 0.1)


def test_units():
    m = patient_metrics()
    # cisplatin 75 mg/m² → 75×1.686 = 126.4 (엑셀 126.43)
    assert _close(absolute_dose("mg/m2", 75, m["bsa"], m["weight"], m["gfr"]), 126.43, 0.2)
    # etoposide 100 mg/m² → 168.57 (엑셀)
    assert _close(absolute_dose("mg/m²", 100, m["bsa"], m["weight"], m["gfr"]), 168.57, 0.2)
    # carboplatin AUC5 → 5×(76.54+25) = 507.7
    assert _close(absolute_dose("AUC", 5, m["bsa"], m["weight"], m["gfr"]), 507.7, 0.3)
    # trastuzumab 6 mg/kg → 6×62 = 372
    assert _close(absolute_dose("mg/kg", 6, m["bsa"], m["weight"], m["gfr"]), 372.0)
    # pembrolizumab flat 200
    assert absolute_dose("mg", 200, m["bsa"], m["weight"], m["gfr"]) == 200
    # 미지원 단위 → None
    assert absolute_dose("MBq", 1, m["bsa"], m["weight"], m["gfr"]) is None


def test_cycle_total():
    m = patient_metrics()
    # etoposide 100 mg/m² D1-3 (per_cycle 3) → 1회 168.57, 주기 505.7 (엑셀)
    d = compute_drug_dose({"unit": "mg/m2", "dose_value": 100, "per_cycle": 3}, m)
    assert _close(d["one_dose_mg"], 168.57, 0.2) and _close(d["cycle_total_mg"], 505.7, 0.6)


def test_patient_dependency():
    """체중 변경 시 mg/kg·mg/m² 변동, flat 불변."""
    big = compute_regimen_doses(
        [{"ingredient": "Trastuzumab", "unit": "mg/kg", "dose_value": 6, "per_cycle": 1},
         {"ingredient": "Pembrolizumab", "unit": "mg", "dose_value": 200, "per_cycle": 1}],
        patient={**PATIENT_DEFAULT, "weight": 80},
    )
    tz = next(d for d in big["drugs"] if d["ingredient"] == "Trastuzumab")
    pb = next(d for d in big["drugs"] if d["ingredient"] == "Pembrolizumab")
    assert _close(tz["one_dose_mg"], 480.0)   # 6×80
    assert pb["one_dose_mg"] == 200           # flat 불변


if __name__ == "__main__":
    test_patient_metrics_default()
    test_units()
    test_cycle_total()
    test_patient_dependency()
    print("OK — onco dosing tests passed")
