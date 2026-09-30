"""Daily Mailing 발송 시각 판정 (KST) — 순수 함수.

구독 schedule: 'Daily' 매일 / 'Weekly' + week_day('Monday'..). time 'HH:MM'.
10분 주기 dispatch 가 '오늘 예정 & 시각 도래 & 오늘 run 없음' 인 구독을 생성한다(재시작 따라잡기 포함).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def parse_hhmm(value: str | None) -> time | None:
    try:
        hh, mm = str(value or "").strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def scheduled_today(sub: dict, now_kst: datetime) -> bool:
    if not sub.get("active"):
        return False
    schedule = sub.get("schedule")
    if schedule == "Daily":
        return True
    if schedule == "Weekly":
        return (sub.get("weekDay") or sub.get("week_day")) == WEEKDAY_NAMES[now_kst.weekday()]
    return False


def is_due(sub: dict, now_kst: datetime) -> bool:
    at = parse_hhmm(sub.get("time"))
    return at is not None and scheduled_today(sub, now_kst) and now_kst.time() >= at
