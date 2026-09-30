"""Daily Mailing 발송 시각 판정 (KST) — 순수 함수.

구독 schedule: 'Daily' **평일(월–금)** / 'Weekly' + week_day('Monday'..). time 'HH:MM'.
10분 주기 dispatch 가 '오늘 예정 & 시각 도래 & 오늘 run 없음' 인 구독을 생성한다(재시작 따라잡기 포함).
월요일 run 은 금요일 오후~월요일 아침 뉴스를 놓치지 않도록 lookback 을 MONDAY_LOOKBACK_HOURS 로 넓힌다.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DEFAULT_LOOKBACK_HOURS = 24
MONDAY_LOOKBACK_HOURS = 72   # 금 08:00 → 월 08:00 (2026-09 제품 결정: Daily = 평일)


def parse_hhmm(value: str | None) -> time | None:
    try:
        hh, mm = str(value or "").strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        return None


def is_weekday(now_kst: datetime) -> bool:
    return now_kst.weekday() < 5


def scheduled_today(sub: dict, now_kst: datetime) -> bool:
    if not sub.get("active"):
        return False
    schedule = sub.get("schedule")
    if schedule == "Daily":
        return is_weekday(now_kst)
    if schedule == "Weekly":
        return (sub.get("weekDay") or sub.get("week_day")) == WEEKDAY_NAMES[now_kst.weekday()]
    return False


def is_due(sub: dict, now_kst: datetime) -> bool:
    at = parse_hhmm(sub.get("time"))
    return at is not None and scheduled_today(sub, now_kst) and now_kst.time() >= at


def lookback_hours_for(sub: dict, now_kst: datetime) -> int:
    """이번 run 의 수집 창(시간). 월요일은 주말을 덮도록 최소 MONDAY_LOOKBACK_HOURS, 그 외는 구독값(기본 24).

    디스패처가 스콥에 넣어 엔진으로 전달한다 — 엔진 코드는 scope.lookback_hours 만 본다.
    """
    base = int(sub.get("lookback_hours") or DEFAULT_LOOKBACK_HOURS)
    if now_kst.weekday() == 0:
        return max(base, MONDAY_LOOKBACK_HOURS)
    return base
