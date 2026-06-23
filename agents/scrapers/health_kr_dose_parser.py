"""health.kr `usage_text` 자연어 → 정량화 (daily_dose_units / schedule / cycle).

DrugEnrichmentAgent 가 일일/월간/연간 치료비를 계산하려면 다음 수치가 필요:
- `daily_dose_units`: 하루 몇 정/바이알/mL
- `dose_schedule`: 'continuous' (매일 복용) / 'cycle' (주기 반복) / 'as_needed'
- `cycle_days` + `doses_per_cycle`: cycle 스케줄일 때

health.kr `dosage` 필드 패턴 예시:
  - "1일 1회 100 mg을 투여"              → continuous, daily_dose_units=1 (100mg tablet)
  - "성인 1일 1회 1정 투여"                → continuous, daily_dose_units=1
  - "200 mg을 매 3주마다 투여"            → cycle, cycle_days=21, doses_per_cycle=1
  - "1일 2회 1정씩 투여"                   → continuous, daily_dose_units=2
  - "24주 동안 주 1회 피하 투여"            → cycle, cycle_days=7, doses_per_cycle=1
"""
from __future__ import annotations

import re
from typing import Optional

# continuous daily 패턴: "1일 N회 M mg" 또는 "1일 N회 M정"
_DAILY_FREQ_RE = re.compile(
    r"(?:성인\s+)?1\s*일\s*(\d+)\s*회\s*(?:(\d+(?:\.\d+)?)\s*(?:mg|밀리그램|㎎|g|그램|㎍))?\s*(?:(\d+)\s*(?:정|캡슐|포))?",
    re.IGNORECASE,
)

# cycle 패턴: "매 N주마다" (mg 선택 — mg 가 앞/뒤 어디든 가능하므로 필수 아님)
_CYCLE_WEEK_RE = re.compile(
    r"매\s*(\d+)\s*주\s*마다",
    re.IGNORECASE,
)
_CYCLE_INTERVAL_RE = re.compile(
    r"(\d+)\s*주\s*(?:간격|간\s*격)",
    re.IGNORECASE,
)

# daily mg 패턴: "하루 N mg" / "1일 N mg"
_DAILY_MG_RE = re.compile(
    r"(?:하루|1일)\s*(?:최대\s*)?(?:용량\s*)?(\d+(?:\.\d+)?)\s*(?:mg|밀리그램|㎎)",
    re.IGNORECASE,
)

# per-dose-우선 어순: "1회 M mg|K정[씩/을] ... 1일 N회"
#   예) "1회 100mg을 1일 2회", "1회 1정씩 1일 1회 투여"
_PER_DOSE_FREQ_RE = re.compile(
    r"1\s*회\s*(?:(\d+(?:\.\d+)?)\s*(?:mg|밀리그램|㎎|g|그램|㎍))?\s*(?:(\d+)\s*(?:정|캡슐|포|환|매))?\s*씩?\s*"
    r"(?:을|를)?\s*1\s*일\s*(\d+)\s*회",
    re.IGNORECASE,
)

# 격일/주N회: "격일", "주 N회", "1주 N회", "주N회"
_EVERY_OTHER_DAY_RE = re.compile(r"격일|2일\s*마다|이틀\s*마다", re.IGNORECASE)
_PER_WEEK_FREQ_RE = re.compile(r"(?:1?\s*주)\s*(\d+)\s*회", re.IGNORECASE)

# 체중(mg/kg) · BSA(mg/m²) 기반 dosing — 항암제/생물학적제제
_DOSE_PER_KG_RE = re.compile(
    r"(?:체중\s*1?\s*kg\s*당|/\s*kg|kg\s*당)\s*(\d+(?:\.\d+)?)\s*mg|"
    r"(\d+(?:\.\d+)?)\s*mg\s*/\s*kg",
    re.IGNORECASE,
)
_DOSE_PER_M2_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*mg\s*/\s*m\s*[²2\^]|"
    r"체표면적\s*m[²2\^]?\s*당\s*(\d+(?:\.\d+)?)\s*mg",
    re.IGNORECASE,
)
_INTERVAL_WEEK_RE = re.compile(r"매?\s*(\d+)\s*주\s*(?:마다|간격|에)|q\s*(\d+)\s*w", re.IGNORECASE)
_INTERVAL_DAY_RE = re.compile(r"매?\s*(\d+)\s*일\s*(?:마다|간격|에)", re.IGNORECASE)


def _pick_maintenance(matches: list) -> float | None:
    """mg/kg·mg/m² 매치 리스트에서 유지(maintenance) dose 추정.

    '이후/유지/maintenance' 키워드 근접 매치 우선, 없으면 최소값(loading 제외).
    """
    if not matches:
        return None
    keys = ("이후", "유지", "maintenance", "지속", "장기")
    for m in matches:
        window = m.string[max(0, m.start() - 50):m.start()]
        if any(k in window for k in keys):
            return next(float(g) for g in m.groups() if g)
    vals = [next(float(g) for g in m.groups() if g) for m in matches]
    return min(vals) if vals else None


def parse_weight_bsa(usage_text: str) -> dict:
    """체중/BSA 기반 용법 → {per_kg_mg|per_m2_mg, interval_days}. 실패 시 {}."""
    if not usage_text:
        return {}
    txt = re.sub(r"(?<=\d),(?=\d)", "", usage_text[:3000])  # 천단위 콤마 제거
    per_kg = _pick_maintenance(list(_DOSE_PER_KG_RE.finditer(txt)))
    per_m2 = _pick_maintenance(list(_DOSE_PER_M2_RE.finditer(txt)))
    if per_kg is None and per_m2 is None:
        return {}
    wk = _INTERVAL_WEEK_RE.search(txt)
    dy = _INTERVAL_DAY_RE.search(txt)
    if wk:
        g = next((x for x in wk.groups() if x), None)
        interval_days = int(g) * 7 if g else None
    elif dy:
        interval_days = int(dy.group(1))
    elif re.search(r"매주|주\s*1\s*회|1\s*주\s*마다", txt):
        interval_days = 7   # 숫자 없는 '매주/주1회'
    else:
        interval_days = None
    out: dict = {"schedule": "cycle", "interval_days": interval_days}
    if per_kg is not None:
        out["per_kg_mg"] = per_kg
    if per_m2 is not None:
        out["per_m2_mg"] = per_m2
    return out


def parse_dose_schedule(usage_text: str, form: str = "") -> dict:
    """용법 텍스트에서 스케줄 정보 추출.

    반환 key: schedule / daily_dose_units / cycle_days / doses_per_cycle / daily_dose_mg
    실패 시 빈 dict (calling side 가 None 처리).
    """
    if not usage_text:
        return {}
    txt = re.sub(r"(?<=\d),(?=\d)", "", usage_text[:3000])  # 천단위 콤마 제거

    out: dict = {}

    # 1) cycle 검출 우선 (항암제 등) — "매 3주마다"
    m = _CYCLE_WEEK_RE.search(txt)
    if m:
        weeks = int(m.group(1))
        out.update({
            "schedule":         "cycle",
            "cycle_days":       weeks * 7,
            "doses_per_cycle":  1.0,
            "daily_dose_mg":    None,
        })
        return out

    m = _CYCLE_INTERVAL_RE.search(txt)
    if m and "매일" not in txt[:200]:
        weeks = int(m.group(1))
        out.update({
            "schedule":        "cycle",
            "cycle_days":      weeks * 7,
            "doses_per_cycle": 1.0,
        })
        return out

    # 2) 격일 — "격일 1회" → cycle_days=2
    if _EVERY_OTHER_DAY_RE.search(txt) and "매일" not in txt[:200]:
        out.update({"schedule": "cycle", "cycle_days": 2, "doses_per_cycle": 1.0})
        m = _DAILY_MG_RE.search(txt)
        if m:
            out["daily_dose_mg"] = float(m.group(1))
        return out

    # 3) 주 N회 — "주 2회" → cycle_days=7, doses_per_cycle=N (매일 패턴 우선이면 skip)
    m = _PER_WEEK_FREQ_RE.search(txt)
    if m and "1일" not in txt[:120] and "매일" not in txt[:120]:
        out.update({"schedule": "cycle", "cycle_days": 7, "doses_per_cycle": float(int(m.group(1)))})
        return out

    # 4) per-dose 우선 어순 — "1회 Mmg|K정[씩] ... 1일 N회" (daily 보다 먼저: mg 보존)
    m = _PER_DOSE_FREQ_RE.search(txt)
    if m:
        mg, units, times = m.group(1), m.group(2), int(m.group(3))
        out["schedule"] = "continuous"
        out["daily_dose_units"] = float(times * int(units)) if units else float(times)
        if mg:
            out["daily_dose_mg"] = float(mg) * times
        return out

    # 5) continuous — "1일 N회 [Mmg] [K정]"
    m = _DAILY_FREQ_RE.search(txt)
    if m:
        times = int(m.group(1))
        units = m.group(3)  # 정/캡슐 count
        out["schedule"] = "continuous"
        out["daily_dose_units"] = float(times * int(units)) if units else float(times)
        mg = m.group(2)
        if mg:
            out["daily_dose_mg"] = float(mg) * times
        return out

    # 6) mg 기반 폴백 — "하루 100mg"
    m = _DAILY_MG_RE.search(txt)
    if m:
        out.update({
            "schedule":        "continuous",
            "daily_dose_mg":   float(m.group(1)),
            "daily_dose_units": 1.0,
        })
        return out

    return {}
