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

# cycle 패턴: "매 N주마다 M mg" 또는 "N주 간격"
_CYCLE_WEEK_RE = re.compile(
    r"매\s*(\d+)\s*주\s*마다\s*(\d+(?:\.\d+)?)\s*(?:mg|밀리그램|㎎)",
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


def parse_dose_schedule(usage_text: str, form: str = "") -> dict:
    """용법 텍스트에서 스케줄 정보 추출.

    반환 key: schedule / daily_dose_units / cycle_days / doses_per_cycle / daily_dose_mg
    실패 시 빈 dict (calling side 가 None 처리).
    """
    if not usage_text:
        return {}
    txt = usage_text[:3000]  # 긴 텍스트에서 앞부분 기준

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

    # 2) continuous — "1일 N회 ..."
    m = _DAILY_FREQ_RE.search(txt)
    if m:
        times = int(m.group(1))
        units = m.group(3)  # 정/캡슐 count
        out["schedule"] = "continuous"
        # units 있으면 "1일 2회 1정씩" → 2 × 1 = 2
        if units:
            out["daily_dose_units"] = float(times * int(units))
        else:
            out["daily_dose_units"] = float(times)
        # mg 값도 수집
        mg = m.group(2)
        if mg:
            out["daily_dose_mg"] = float(mg) * times
        return out

    # 3) mg 기반 폴백 — "하루 100mg"
    m = _DAILY_MG_RE.search(txt)
    if m:
        out.update({
            "schedule":        "continuous",
            "daily_dose_mg":   float(m.group(1)),
            "daily_dose_units": 1.0,
        })
        return out

    return {}
