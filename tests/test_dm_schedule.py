from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.daily_mailing.schedule import is_due, parse_hhmm, scheduled_today

KST = timezone(timedelta(hours=9))
FRI_0759 = datetime(2026, 9, 18, 7, 59, tzinfo=KST)
FRI_0800 = datetime(2026, 9, 18, 8, 0, tzinfo=KST)


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
