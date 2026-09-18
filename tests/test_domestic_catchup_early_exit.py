"""국내 약가 월별 catch-up — 첫 주(1~7일) 일일 재시도 + 이미 적재 시 조기 종료 +
느린 프록시 board 로딩 robustness (2026-09-01/09-18 프로덕션 실패 회귀).

- 스케줄: config domestic_agent.schedule.day="1-7" 이 CronTrigger 로 1~7일 09:00 발화
- 조기 종료: DB 최신 적용일 >= 당월 1일이면 브라우저/프록시 기동 없이 skip (유료 프록시 절약)
- force=True(수동 실행) 는 조기 종료 무시
- board 로딩: 시도마다 새 page + 재시도, 전 페이지 실패 시 errors 에 기록 (silent no-op 금지)
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.domestic_catchup as dc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ── 스케줄 설정 ──────────────────────────────────────────────────────────────

def test_settings_schedule_fires_daily_on_first_week():
    cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    sched = cfg["domestic_agent"]["schedule"]
    trig = CronTrigger(day=sched["day"], hour=sched["hour"], minute=sched["minute"],
                       timezone="Asia/Seoul")
    tz = ZoneInfo("Asia/Seoul")
    # 9/2 10:00 이후 → 9/3 09:00 (첫 주 매일 재시도)
    nxt = trig.get_next_fire_time(None, datetime(2026, 9, 2, 10, 0, tzinfo=tz))
    assert (nxt.month, nxt.day, nxt.hour, nxt.minute) == (9, 3, 9, 0)
    # 9/8 이후 → 다음달 1일 09:00
    nxt = trig.get_next_fire_time(None, datetime(2026, 9, 8, 0, 0, tzinfo=tz))
    assert (nxt.month, nxt.day, nxt.hour) == (10, 1, 9)


def test_scheduler_banner_accepts_string_day():
    import scheduler as sched
    banner = sched._schedule_banner({"day": "1-7", "hour": 9, "minute": 0})
    assert "매월 1-7일 09:00" in banner


# ── 조기 종료 ────────────────────────────────────────────────────────────────

class _Launched(Exception):
    """async_playwright() 호출 감지용 sentinel."""


class _FakeDB:
    def __init__(self, dates):
        self._dates = dates

    def get_available_dates(self):
        return list(self._dates)


class _FakeBackfill:
    def __init__(self, config, base_dir):
        self.db = _FakeDB(config["_dates"])


def _sentinel_browser(monkeypatch):
    def boom():
        raise _Launched()
    monkeypatch.setattr(dc, "async_playwright", boom)


def test_skip_when_current_month_already_loaded(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(dc, "BackfillAgent", _FakeBackfill)
    monkeypatch.setattr(dc, "_month_first", lambda: "2026.09.01")
    _sentinel_browser(monkeypatch)
    caplog.set_level("INFO", logger="agents.domestic_catchup")

    res = asyncio.run(dc.catch_up({"_dates": ["2026.08.01", "2026.09.01"]}, tmp_path))

    assert res["latest_db_before"] == "2026.09.01"
    assert res["ingested"] == [] and res["errors"] == []
    assert res["skipped"] == "already_loaded"
    assert any("이미 적재" in r.getMessage() for r in caplog.records)


def test_skip_when_db_has_mid_month_newer_date(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "BackfillAgent", _FakeBackfill)
    monkeypatch.setattr(dc, "_month_first", lambda: "2026.09.01")
    _sentinel_browser(monkeypatch)
    res = asyncio.run(dc.catch_up({"_dates": ["2026.09.15"]}, tmp_path))
    assert res["skipped"] == "already_loaded"


def test_runs_when_latest_is_previous_month(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "BackfillAgent", _FakeBackfill)
    monkeypatch.setattr(dc, "_month_first", lambda: "2026.09.01")
    _sentinel_browser(monkeypatch)
    with pytest.raises(_Launched):
        asyncio.run(dc.catch_up({"_dates": ["2026.07.01", "2026.08.01"]}, tmp_path))


def test_runs_when_db_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "BackfillAgent", _FakeBackfill)
    monkeypatch.setattr(dc, "_month_first", lambda: "2026.09.01")
    _sentinel_browser(monkeypatch)
    with pytest.raises(_Launched):
        asyncio.run(dc.catch_up({"_dates": []}, tmp_path))


def test_force_bypasses_early_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "BackfillAgent", _FakeBackfill)
    monkeypatch.setattr(dc, "_month_first", lambda: "2026.09.01")
    _sentinel_browser(monkeypatch)
    with pytest.raises(_Launched):
        asyncio.run(dc.catch_up({"_dates": ["2026.09.01"]}, tmp_path, force=True))


def test_month_first_format():
    assert dc._month_first(datetime(2026, 9, 18).date()) == "2026.09.01"
    assert dc._month_first(datetime(2026, 1, 1).date()) == "2026.01.01"


# ── board 로딩 실패 → errors 기록 (G) ───────────────────────────────────────

class _FakeTimeoutPage:
    def __init__(self):
        self.closed = False

    async def goto(self, url, **kw):
        raise TimeoutError(f"Page.goto: Timeout 60000ms exceeded ({url})")

    async def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self):
        self.pages: list[_FakeTimeoutPage] = []

    async def new_page(self):
        p = _FakeTimeoutPage()
        self.pages.append(p)
        return p


class _FakeBrowser:
    def __init__(self):
        self.context = _FakeContext()
        self.closed = False

    async def new_context(self, **kw):
        return self.context

    async def close(self):
        self.closed = True


class _FakePW:
    def __init__(self, browser):
        self._browser = browser

        class _Chromium:
            async def launch(_self, **kw):
                return browser
        self.chromium = _Chromium()


class _FakeAsyncPlaywright:
    def __init__(self, browser):
        self._browser = browser

    async def __aenter__(self):
        return _FakePW(self._browser)

    async def __aexit__(self, *exc):
        return False


def test_total_board_load_failure_recorded_in_errors(monkeypatch, tmp_path, caplog):
    """모든 board 페이지 goto 타임아웃 → 적재 0건이 '오류 []' 로 성공 위장되면 안 된다."""
    monkeypatch.setattr(dc, "BackfillAgent", _FakeBackfill)
    monkeypatch.setattr(dc, "_month_first", lambda: "2026.09.01")
    monkeypatch.setattr(dc, "_RETRY_BACKOFF_SEC", 0)
    browser = _FakeBrowser()
    monkeypatch.setattr(dc, "async_playwright", lambda: _FakeAsyncPlaywright(browser))
    caplog.set_level("INFO", logger="agents.domestic_catchup")

    res = asyncio.run(dc.catch_up({"_dates": ["2026.08.01"]}, tmp_path, max_pages=2))

    assert res["ingested"] == []
    assert len(res["errors"]) == 1
    assert "HIRA 게시판 로딩 실패" in res["errors"][0]
    assert "page 1..2" in res["errors"][0]
    # 시도마다 새 page (타임아웃된 navigation 이 다음 goto 를 interrupt 하지 못하게) + 전부 close
    assert len(browser.context.pages) == 2 * dc._BOARD_RETRIES
    assert all(p.closed for p in browser.context.pages)
    assert browser.closed


# ── board 로딩 재시도 (F) — 실제 chromium, page.route 로 HIRA URL fulfill ────

def _board_html(rows: list[tuple[int, str]]) -> str:
    trs = "".join(
        f"<tr><td>{i}</td><td><a href=\"?pgmid=HIRAA030014050000&brdBltNo={brd}\">{title}</a></td>"
        f"<td>보험약제과</td><td>2026.08.28</td></tr>"
        for i, (brd, title) in enumerate(rows, 1)
    )
    return f"<html><body><table><thead><tr><th>번호</th></tr></thead><tbody>{trs}</tbody></table></body></html>"


def test_board_page_retry_with_fresh_page_after_timeout(monkeypatch):
    """page 1 첫 시도는 응답 없음(타임아웃) → 새 page 로 재시도 성공, page 2 정상."""
    monkeypatch.setattr(dc, "_BOARD_GOTO_TIMEOUT_MS", 1500)
    monkeypatch.setattr(dc, "_BOARD_ROWS_TIMEOUT_MS", 1500)
    monkeypatch.setattr(dc, "_RETRY_BACKOFF_SEC", 0)
    hits = {"1": 0, "2": 0}

    async def handler(route):
        url = route.request.url
        pidx = "1" if "pageIndex=1" in url else "2"
        hits[pidx] += 1
        if pidx == "1" and hits["1"] == 1:
            return  # 첫 시도: 영원히 응답 안 함 → goto 타임아웃
        rows = ([(1709, "약제급여목록및급여상한금액표(2026.9.1.적용)")] if pidx == "1"
                else [(1700, "약제급여목록및급여상한금액표(2026.8.1.적용)"),
                      (1699, "기타 공지사항 2026.7.20")])
        await route.fulfill(body=_board_html(rows), content_type="text/html; charset=utf-8")

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context()
            await ctx.route("**/bbsDummyKR.do*", handler)
            try:
                return await dc._collect_recent_posts(ctx, max_pages=2)
            finally:
                await browser.close()

    posts, failed_pages = asyncio.run(run())
    assert failed_pages == []
    assert hits["1"] == 2 and hits["2"] == 1
    assert [p["apply_date"] for p in posts] == ["2026.09.01", "2026.08.01"]  # 키워드 필터
    assert posts[0]["brd_blt_no"] == 1709
