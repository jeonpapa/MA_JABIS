"""모니터링 주기 계산 — 모든 시각은 KST 로 정하고 UTC ISO 로 저장한다.

schedule JSON:
  {"type": "weekly",  "weekdays": [1..7], "hour": 9}      # ISO 요일(1=월 … 7=일)
  {"type": "monthly", "days": [1..31],    "hour": 9}      # 그 달에 없는 날(31일 등)은 말일로
  {"type": "dates",   "dates": ["2026-11-20", …], "hour": 9}
"""
from __future__ import annotations

import calendar
import os
from datetime import date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
A8 = ("JP", "IT", "FR", "CH", "UK", "DE", "CA", "US")

# 선행 시작 — 실측(2026-10~11 운영 7건): 1개국 요청 ≤0.1h, A8 8개국 0.9~16h(로그인·차단 재시도 포함).
# 약제 1개 · A8 전체 기준 평균 3시간으로 잡고, 여유 12시간을 더한다. 하루 전 텔레그램 안내 전에 반드시 시작한다.
HOURS_PER_ITEM_A8 = float(os.environ.get("MONITOR_HOURS_PER_ITEM_A8", "3"))
LEAD_BUFFER_HOURS = 12
MIN_LEAD_HOURS = 30          # 하루 전 안내(24h) 시점에는 이미 수집 중이어야 한다
MAX_LEAD_HOURS = 14 * 24


def validate_schedule(s: dict) -> dict:
    if not isinstance(s, dict):
        raise ValueError("schedule must be an object")
    kind = s.get("type")
    hour = s.get("hour", 9)
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("schedule.hour must be 0..23 (KST)")
    if kind == "weekly":
        days = s.get("weekdays")
        if not isinstance(days, list) or not days or any(not isinstance(d, int) or not 1 <= d <= 7 for d in days):
            raise ValueError("weekly schedule needs weekdays 1..7")
        return {"type": "weekly", "weekdays": sorted(set(days)), "hour": hour}
    if kind == "monthly":
        days = s.get("days")
        if not isinstance(days, list) or not days or any(not isinstance(d, int) or not 1 <= d <= 31 for d in days):
            raise ValueError("monthly schedule needs days 1..31")
        return {"type": "monthly", "days": sorted(set(days)), "hour": hour}
    if kind == "dates":
        raw = s.get("dates")
        if not isinstance(raw, list) or not raw or len(raw) > 366:
            raise ValueError("dates schedule needs 1..366 dates")
        out = sorted({date.fromisoformat(str(d)).isoformat() for d in raw})
        return {"type": "dates", "dates": out, "hour": hour}
    raise ValueError("schedule.type must be weekly, monthly or dates")


def _at(d: date, hour: int) -> datetime:
    return datetime.combine(d, time(hour=hour), tzinfo=KST)


def next_due(s: dict, after: datetime) -> datetime | None:
    """after(포함하지 않음) 이후 첫 발송 시각(KST aware). 더 없으면 None(특정 날짜가 모두 지남)."""
    after_kst = after.astimezone(KST)
    hour = s.get("hour", 9)
    if s["type"] == "dates":
        for d in s["dates"]:
            t = _at(date.fromisoformat(d), hour)
            if t > after_kst:
                return t
        return None
    d = after_kst.date()
    for _ in range(0, 400):
        if s["type"] == "weekly":
            ok = d.isoweekday() in s["weekdays"]
        else:
            last = calendar.monthrange(d.year, d.month)[1]
            ok = d.day in {min(x, last) for x in s["days"]}
        if ok:
            t = _at(d, hour)
            if t > after_kst:
                return t
        d += timedelta(days=1)
    return None


def lead_hours(n_items: int, countries: list[str]) -> float:
    """수집에 걸릴 시간 추정 + 여유. 국가 수에 비례(A8 8개국 = 1.0)."""
    share = max(len(countries), 1) / len(A8)
    est = HOURS_PER_ITEM_A8 * max(n_items, 1) * share
    return min(max(est + LEAD_BUFFER_HOURS, MIN_LEAD_HOURS), MAX_LEAD_HOURS)


def describe(s: dict) -> str:
    names = "월화수목금토일"
    h = f"{s.get('hour', 9):02d}:00"
    if s["type"] == "weekly":
        return "매주 " + "·".join(names[d - 1] for d in s["weekdays"]) + "요일 " + h
    if s["type"] == "monthly":
        return "매월 " + "·".join(f"{d}일" for d in s["days"]) + " " + h + " (없는 날은 말일)"
    return "지정일 " + ", ".join(s["dates"][:6]) + (" 외" if len(s["dates"]) > 6 else "") + " " + h
