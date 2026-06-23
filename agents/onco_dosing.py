"""항암 레지멘 용량 계산 — onco-regimen-dosing 스킬 룰 코드화.

환자 파라미터(키·체중·나이·성별·SCr) → BSA(Mosteller/DuBois)·CrCl(Cockcroft-Gault)·
Calvert GFR(cap125) → 약제 단위(mg/m2·mg/kg·AUC·flat)별 절대용량(mg) → 주기총량(mg).

출처: data/onco-regimen-dosing.skill (scripts/dose_calc.py, references/calculation_methods.md).
기본 환자값은 엑셀 '계산기' 시트값(키165·체중62·나이60·M·SCr0.9 → BSA 1.686, GFR 76.5).
"""
from __future__ import annotations

import math

PATIENT_DEFAULT = {"height": 165.0, "weight": 62.0, "age": 60.0, "sex": "M", "scr": 0.9}
GFR_CAP = 125.0


def bsa_mosteller(h_cm: float, w_kg: float) -> float:
    return math.sqrt(h_cm * w_kg / 3600.0)


def bsa_dubois(h_cm: float, w_kg: float) -> float:
    return 0.007184 * (h_cm ** 0.725) * (w_kg ** 0.425)


def crcl_cockcroft_gault(age: float, w_kg: float, scr: float, sex: str) -> float:
    k = 0.85 if str(sex).upper() == "F" else 1.0
    return ((140 - age) * w_kg * k) / (72.0 * scr)


def patient_metrics(patient: dict | None = None, bsa_method: str = "mosteller") -> dict:
    """환자값 → {bsa, bsa_mosteller, bsa_dubois, crcl, gfr}. 결측은 기본값 보충."""
    p = {**PATIENT_DEFAULT, **(patient or {})}
    h, w, age, scr, sex = (float(p["height"]), float(p["weight"]), float(p["age"]),
                           float(p["scr"]), p["sex"])
    bm, bd = bsa_mosteller(h, w), bsa_dubois(h, w)
    crcl = crcl_cockcroft_gault(age, w, scr, sex)
    return {
        "height": h, "weight": w, "age": age, "sex": str(sex).upper(), "scr": scr,
        "bsa_mosteller": round(bm, 4), "bsa_dubois": round(bd, 4),
        "bsa": round(bd if bsa_method == "dubois" else bm, 4),
        "crcl": round(crcl, 2), "gfr": round(min(crcl, GFR_CAP), 2),
    }


def _norm_unit(unit: str | None) -> str:
    return (unit or "").strip().lower().replace("²", "2").replace("㎡", "m2")


def absolute_dose(unit: str | None, value, bsa: float, weight: float, gfr: float) -> float | None:
    """단위별 1회 절대용량(mg). 미지원/결측 단위는 None(계산 불가 → 비용 None)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    u = _norm_unit(unit)
    if u in ("mg/m2", "mg/m2/day", "g/m2"):
        mg = v * bsa
        return mg * 1000 if u == "g/m2" else mg
    if u in ("mg/kg", "mg/kg/day"):
        return v * weight
    if u == "auc":
        return v * (gfr + 25.0)          # Calvert
    if u in ("mg", "flat"):
        return v
    return None                          # unit/mcg/MBq 등 — 환자 비종속이 아니거나 약가 부적합


def compute_drug_dose(drug: dict, metrics: dict) -> dict:
    """약제 dosing dict + 환자 metrics → 1회용량mg·주기총량mg. drug 키:
    dose_value, unit, per_cycle (회수/주기). 반환에 one_dose_mg/cycle_total_mg/dose_basis."""
    one = absolute_dose(drug.get("unit"), drug.get("dose_value"),
                        metrics["bsa"], metrics["weight"], metrics["gfr"])
    per = drug.get("per_cycle") or 1
    try:
        per = float(per)
    except (TypeError, ValueError):
        per = 1.0
    cycle_total = round(one * per, 2) if one is not None else None
    basis = ""
    if one is not None:
        u = _norm_unit(drug.get("unit"))
        if u in ("mg/m2", "mg/m2/day"):
            basis = f"{drug.get('dose_value')}×BSA {metrics['bsa']}"
        elif u in ("mg/kg", "mg/kg/day"):
            basis = f"{drug.get('dose_value')}×체중 {metrics['weight']}kg"
        elif u == "auc":
            basis = f"Calvert {drug.get('dose_value')}×(GFR {metrics['gfr']}+25)"
        elif u in ("mg", "flat"):
            basis = "고정용량"
    return {
        "one_dose_mg": round(one, 2) if one is not None else None,
        "cycle_total_mg": cycle_total,
        "per_cycle": per,
        "dose_basis": basis,
    }


def compute_regimen_doses(drugs: list[dict], patient: dict | None = None,
                          bsa_method: str = "mosteller") -> dict:
    """레지멘 약제들 → 각 약제 mg + 환자 metrics. 반환 {metrics, drugs:[{...drug, dose}]}"""
    metrics = patient_metrics(patient, bsa_method)
    out = []
    for d in drugs:
        out.append({**d, **compute_drug_dose(d, metrics)})
    return {"metrics": metrics, "drugs": out}
