from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.daily_mailing.schedule import MONDAY_LOOKBACK_HOURS, is_due, lookback_hours_for, parse_hhmm, scheduled_today

KST = timezone(timedelta(hours=9))
FRI_0759 = datetime(2026, 9, 18, 7, 59, tzinfo=KST)
FRI_0800 = datetime(2026, 9, 18, 8, 0, tzinfo=KST)
SAT_0800 = datetime(2026, 9, 19, 8, 0, tzinfo=KST)
SUN_0800 = datetime(2026, 9, 20, 8, 0, tzinfo=KST)
MON_0800 = datetime(2026, 9, 21, 8, 0, tzinfo=KST)


def test_daily():
    sub = {"active": True, "schedule": "Daily", "time": "08:00"}
    assert is_due(sub, FRI_0759) is False
    assert is_due(sub, FRI_0800) is True


def test_weekly_day_match():
    sub = {"active": True, "schedule": "Weekly", "time": "07:00", "weekDay": "Friday"}
    assert is_due(sub, FRI_0800) is True
    assert is_due({**sub, "weekDay": "Monday"}, FRI_0800) is False
    assert scheduled_today({**sub, "weekDay": None, "week_day": "Friday"}, FRI_0800) is True


def test_inactive_or_bad_time():
    assert is_due({"active": False, "schedule": "Daily", "time": "08:00"}, FRI_0800) is False
    assert is_due({"active": True, "schedule": "Daily", "time": "25:99"}, FRI_0800) is False
    assert parse_hhmm("7:5").hour == 7
    assert parse_hhmm("") is None


def test_daily_means_weekdays_only():
    """제품 결정: 'Daily' = 평일(월–금). 토·일은 예정 아님."""
    sub = {"active": True, "schedule": "Daily", "time": "08:00"}
    assert scheduled_today(sub, SAT_0800) is False and is_due(sub, SAT_0800) is False
    assert scheduled_today(sub, SUN_0800) is False and is_due(sub, SUN_0800) is False
    assert is_due(sub, MON_0800) is True and is_due(sub, FRI_0800) is True


def test_monday_widens_lookback_to_cover_weekend():
    """월요일 run 은 금요일 오후~월요일 아침 뉴스를 놓치지 않도록 72시간, 다른 평일은 구독값(기본 24)."""
    sub = {"active": True, "schedule": "Daily", "time": "08:00"}
    assert MONDAY_LOOKBACK_HOURS == 72
    assert lookback_hours_for(sub, MON_0800) == 72
    assert lookback_hours_for(sub, FRI_0800) == 24
    assert lookback_hours_for({**sub, "lookback_hours": 36}, FRI_0800) == 36
    assert lookback_hours_for({**sub, "lookback_hours": 96}, MON_0800) == 96      # 구독값이 더 넓으면 유지
    weekly = {"active": True, "schedule": "Weekly", "time": "08:00", "weekDay": "Monday"}
    assert lookback_hours_for(weekly, MON_0800) == 72                            # 월요일 위클리도 주말을 덮는다
