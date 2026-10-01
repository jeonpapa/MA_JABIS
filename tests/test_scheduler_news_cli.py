"""`scheduler.py --news-daily-now` / `--news-weekly-now` (P1.5 Task 5) — 분리 런북 news_weekly_crawl 이 이 플래그로
자식 프로세스를 띄우고, 수동 재실행도 같은 guarded 잡 함수를 지나간다. 실제 크롤은 하지 않는다(잡 함수를 기록기로)."""
from __future__ import annotations

import sys

import pytest

import scheduler as sch


@pytest.fixture
def quiet_main(monkeypatch):
    """main() 의 부수효과 차단: 로깅 핸들러 설치 안 함, 스케줄러가 뜨면 실패."""
    monkeypatch.setattr(sch, "setup_logging", lambda config: None)

    class NoScheduler:
        def __init__(self, *a, **kw):
            raise AssertionError("즉시 실행 플래그는 스케줄러를 띄우면 안 된다")
    monkeypatch.setattr(sch, "BlockingScheduler", NoScheduler)
    calls: list[str] = []
    monkeypatch.setattr(sch, "competitor_news_daily_job", lambda: calls.append("daily") or {"ok": True, "counts": {}, "errors": []})
    monkeypatch.setattr(sch, "competitor_news_weekly_job", lambda: calls.append("weekly") or {"ok": True, "counts": {}, "errors": []})
    return calls


def test_news_daily_now_runs_daily_job_only(quiet_main, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["scheduler.py", "--news-daily-now"])
    sch.main()
    assert quiet_main == ["daily"]


def test_news_weekly_now_runs_weekly_job_only(quiet_main, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["scheduler.py", "--news-weekly-now"])
    sch.main()
    assert quiet_main == ["weekly"]
