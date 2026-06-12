"""HIRA 보도자료 게시판 공식 크롤러 — 약평위·암질심 심의결과 1차 소스.

게시판: https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA020041000100
requests 는 403 차단되나 Playwright(headless chromium) 는 HTTP 200 — 2026-06-12 검증.

조회 전용 모듈 (DB 적재는 agents/ingest/reimb_committee_import.py 가 담당).
- list_committee_posts(months): 목록 페이지를 거슬러 위원회 심의결과 게시물 수집
- fetch_post_body(page, post): 게시물 본문 텍스트
- audit_coverage(): 게시물 목록 ↔ amjilsim_sessions 대조 → 누락 차수 리포트

실행: python -m agents.scrapers.hira_press [개월수]
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

BOARD_URL = "https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA020041000100"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# 위원회 심의결과 게시물 제목 패턴 (공식 표기 변형 포함)
_COMMITTEE_RE = re.compile(
    r"약제급여평가위원회|중증\(암\)질환심의위원회|암질환심의위원회|중증질환심의위원회"
)
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_ORDINAL_RE = re.compile(r"제\s*(\d+)\s*차")


def _committee_type(title: str) -> str:
    return "YAKPYUNGWI" if "약제급여평가" in title else "AMJILSIM"


async def list_committee_posts(months: int = 12, max_pages: int = 30) -> list[dict]:
    """지난 N개월 약평위·암질심 심의결과 게시물 목록 (본문 미포함)."""
    cutoff = (datetime.now() - timedelta(days=months * 31)).strftime("%Y-%m-%d")
    posts: list[dict] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=_UA)
        page = await ctx.new_page()
        try:
            stop = False
            for pidx in range(1, max_pages + 1):
                url = f"{BOARD_URL}&pageIndex={pidx}"
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
                    await page.wait_for_timeout(800)
                except Exception as e:
                    logger.warning("[hira_press] page %d 로딩 실패: %s", pidx, e)
                    continue
                rows = page.locator("table tbody tr")
                n = await rows.count()
                if n == 0:
                    break
                for i in range(n):
                    row = rows.nth(i)
                    text = (await row.inner_text()).strip()
                    m_date = _DATE_RE.search(text)
                    post_date = "-".join(m_date.groups()) if m_date else None
                    if post_date and post_date < cutoff:
                        stop = True
                        break
                    link = row.locator("a").first
                    if await link.count() == 0:
                        continue
                    title = (await link.inner_text()).strip()
                    if not _COMMITTEE_RE.search(title):
                        continue
                    href = await link.get_attribute("href") or ""
                    m_no = re.search(r"brdBltNo=(\d+)", href)
                    m_ord = _ORDINAL_RE.search(title)
                    posts.append({
                        "title": title,
                        "date": post_date,
                        "brdBltNo": m_no.group(1) if m_no else None,
                        "href": href,
                        "committee_type": _committee_type(title),
                        "ordinal": int(m_ord.group(1)) if m_ord else None,
                    })
                if stop:
                    break
        finally:
            await browser.close()
    posts.sort(key=lambda p: p["date"] or "")
    logger.info("[hira_press] 위원회 게시물 %d건 (%d개월)", len(posts), months)
    return posts


async def fetch_post_body(post: dict) -> str:
    """단일 게시물 본문 텍스트 (목록의 href 기반)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(user_agent=_UA)
        page = await ctx.new_page()
        try:
            await page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=40_000)
            link = page.locator(f"a[href*='brdBltNo={post['brdBltNo']}']").first
            if await link.count() > 0:
                await link.click()
            else:  # 다른 페이지의 게시물 — 제목 검색으로 폴백
                await page.goto(f"{BOARD_URL}&{post['href'].lstrip('?')}",
                                wait_until="domcontentloaded", timeout=40_000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(1_500)
            return await page.inner_text("body")
        finally:
            await browser.close()


def audit_coverage(posts: list[dict],
                   db_path: Path = None) -> dict:
    """게시물 목록 ↔ amjilsim_sessions COMPLETED 차수 대조 → 누락 리포트."""
    import sqlite3
    db_path = db_path or Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sessions = conn.execute(
        "SELECT session_id, committee_type, session_date, status, "
        "COALESCE(ordinal_official, ordinal_assumed) AS ordinal "
        "FROM amjilsim_sessions").fetchall()
    conn.close()
    by_key = {(s["committee_type"], s["session_date"][:7], s["ordinal"]): s for s in sessions}

    missing, stale = [], []
    for p in posts:
        if not p["date"]:
            continue
        key = (p["committee_type"], p["date"][:7], p["ordinal"])
        s = by_key.get(key)
        # 게시일과 회의일이 다를 수 있어 (월·차수) 로 매칭, 차수 없으면 월만
        if s is None:
            s = next((x for k, x in by_key.items()
                      if k[0] == p["committee_type"] and k[1] == p["date"][:7]), None)
        if s is None:
            missing.append(p)
        elif s["status"] != "COMPLETED":
            stale.append({**p, "session_id": s["session_id"], "db_status": s["status"]})
    return {"posts": len(posts), "missing_sessions": missing, "stale_status": stale}


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    found = asyncio.run(list_committee_posts(months))
    for p in found:
        print(f"  {p['date']}  [{p['committee_type']}] {p['title'][:60]}  (brdBltNo={p['brdBltNo']})")
    report = audit_coverage(found)
    print(json.dumps({"posts": report["posts"],
                      "missing": [(m['date'], m['title'][:40]) for m in report["missing_sessions"]],
                      "stale": [(s['date'], s['title'][:40], s['db_status']) for s in report["stale_status"]]},
                     ensure_ascii=False, indent=2))
