"""국내 약가 월별 자동 캐치업 — 누락 자동복구형 루틴.

HIRA 약가고시현황 board 최근 게시물 중 **DB 최신 적용일보다 새로운 달을 전부** 다운로드·적재.
스케줄러 미가동/실패로 빠진 달(예: 5·6월)이 있어도 다음 실행에서 자동 복구한다.

- 소스: HIRA board (config domestic_agent.hira_board_url) 의 '약제급여목록및급여상한금액표' 게시물.
- 다운로드/파싱/적재는 검증된 BackfillAgent(download_post_excel·process_excel, ZIP·구형 포맷 대응) 재사용.
- 멱등: 이미 적재된(brdBltNo processed) 게시물 skip. 적용일은 게시물 제목의 'YYYY.MM.DD' 사용.
- 스케줄: 매월 1~7일 09:00 매일(첫 주 재시도). **당월 1일 이미 적재면 브라우저/프록시 기동
  없이 즉시 종료** (유료 KR proxy 대역폭 절약). 수동 실행(force=True)은 이 조기 종료를 무시.
- board 로딩(2026-09-18 프로덕션 실패 반영): domcontentloaded + 행(table tbody tr) 명시 대기,
  **시도마다 새 page** (타임아웃된 navigation 이 다음 goto 를 'interrupted by another
  navigation' 으로 깨뜨리지 않게) + 재시도. 전 페이지 실패는 errors 에 기록 (silent no-op 금지).
- 배포 안전: headless chromium, 외부 상태 의존 없음.

실행: agents/domestic_catchup.run_sync(config, base_dir)  또는  python -m agents.domestic_catchup
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from agents.backfill_agent import BackfillAgent, HIRA_BOARD_URL

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_DATE_RE = re.compile(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})")
_LIST_KEYWORD = "약제급여목록"  # 약가 상한금액표 게시물만 (타 공지 제외)

# board 로딩 — 느린 residential proxy 기준 (2026-09-18: networkidle 30s 타임아웃 → 0건 수집)
_BOARD_GOTO_TIMEOUT_MS = 60000
_BOARD_ROWS_TIMEOUT_MS = 20000   # table tbody tr 명시 대기
_BOARD_RETRIES = 3
_RETRY_BACKOFF_SEC = 3.0         # attempt × backoff
_DETAIL_ATTEMPTS = 2             # 상세 페이지 다운로드 시도 횟수 (시도마다 새 page)


def _playwright_proxy():
    """`HIRA_PROXY`(http://user:pass@host:port) → Playwright context proxy dict. 없으면 None.

    HIRA(www.hira.or.kr) 는 fly 등 **비한국/데이터센터 IP 를 400 Bad Request 로 차단**한다.
    한국 residential proxy(예: dataimpulse `__cr.kr`) 를 `config/.env HIRA_PROXY` 로 주면
    그 KR IP 로 우회해 프로덕션에서도 수집 가능. 미설정 시 None(직접 접근 — 한국 IP 환경용).
    """
    raw = os.environ.get("HIRA_PROXY", "").strip()
    if not raw:
        return None
    u = urlparse(raw)
    if not u.hostname or not u.port:
        return None
    proxy = {"server": f"{u.scheme or 'http'}://{u.hostname}:{u.port}"}
    if u.username:
        proxy["username"] = u.username
    if u.password:
        proxy["password"] = u.password
    return proxy


def _month_first(today: date | None = None) -> str:
    """당월 1일 'YYYY.MM.01' — DB 최신 적용일(같은 포맷)과 문자열 비교용."""
    today = today or date.today()
    return today.strftime("%Y.%m.01")


def _fmt_pages(pages: list[int]) -> str:
    """[1,2,3] → '1..3', [2] → '2', [1,3] → '1,3'."""
    if len(pages) > 1 and pages == list(range(pages[0], pages[-1] + 1)):
        return f"{pages[0]}..{pages[-1]}"
    return ",".join(map(str, pages))


async def _open_board_page(context, pidx: int):
    """board pageIndex 를 **새 page** 에서 연다. 성공 (page, None) / 실패 (None, 마지막 예외).

    - domcontentloaded + 행 명시 대기: HIRA 는 백그라운드 요청이 잦아 networkidle 이 프록시에서 불안정.
    - 시도마다 새 page: 타임아웃된 이전 navigation 이 살아있는 page 로 다시 goto 하면
      'Navigation ... is interrupted by another navigation' 으로 연쇄 실패 (2026-09-18 page 2·3).
    호출부가 page.close() 책임.
    """
    url = f"{HIRA_BOARD_URL}&pageIndex={pidx}"
    last_err: Exception | None = None
    for attempt in range(1, _BOARD_RETRIES + 1):
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=_BOARD_GOTO_TIMEOUT_MS)
            await page.locator("table tbody tr").first.wait_for(
                state="attached", timeout=_BOARD_ROWS_TIMEOUT_MS)
            return page, None
        except Exception as e:
            last_err = e
            logger.warning("[catchup] page %d 로딩 실패 (%d/%d): %s",
                           pidx, attempt, _BOARD_RETRIES, e)
            try:
                await page.close()
            except Exception:
                pass
            if attempt < _BOARD_RETRIES:
                await asyncio.sleep(_RETRY_BACKOFF_SEC * attempt)
    return None, last_err


async def _collect_recent_posts(context, max_pages: int) -> tuple[list[dict], list[int]]:
    """board page 1~max_pages 의 '약제급여목록' 게시물 수집. 반환 (posts, 로딩 실패 page 목록)."""
    posts: list[dict] = []
    seen_brd: set[int] = set()
    failed_pages: list[int] = []
    for pidx in range(1, max_pages + 1):
        page, _err = await _open_board_page(context, pidx)
        if page is None:
            failed_pages.append(pidx)
            continue
        try:
            rows = page.locator("table tbody tr")
            for i in range(await rows.count()):
                row = rows.nth(i)
                link = row.locator("a").first
                if await link.count() == 0:
                    continue
                title = (await link.inner_text() or "").strip()
                href = await link.get_attribute("href") or ""
                m_brd = re.search(r"brdBltNo=(\d+)", href)
                m_date = _DATE_RE.search(title)
                if not (m_brd and m_date and _LIST_KEYWORD in title):
                    continue
                brd = int(m_brd.group(1))
                if brd in seen_brd:
                    continue
                seen_brd.add(brd)
                apply_date = (f"{m_date.group(1)}.{int(m_date.group(2)):02d}."
                              f"{int(m_date.group(3)):02d}")
                posts.append({"apply_date": apply_date, "brd_blt_no": brd,
                              "href": href, "title": title, "post_number": 0})
        finally:
            await page.close()
    return posts, failed_pages


async def _download_with_retry(bf: BackfillAgent, context, post: dict):
    """상세 페이지 다운로드 — 시도마다 새 page (타임아웃 navigation 잔존 방지). 성공 path 또는 None."""
    ad = post["apply_date"]
    for attempt in range(1, _DETAIL_ATTEMPTS + 1):
        page = await context.new_page()
        try:
            path, _detail_date = await bf.download_post_excel(page, post)
        finally:
            await page.close()
        if path is not None:
            return path
        if attempt < _DETAIL_ATTEMPTS:
            logger.warning("[catchup] %s 다운로드 실패 (%d/%d) — 새 page 로 재시도",
                           ad, attempt, _DETAIL_ATTEMPTS)
            await asyncio.sleep(_RETRY_BACKOFF_SEC)
    return None


async def catch_up(config: dict, base_dir: Path, max_pages: int = 2, *,
                   force: bool = False) -> dict:
    """누락 월 자동 적재. 반환: {latest_db_before, ingested:[{apply_date,rows}], errors:[...], skipped?}

    force=False(스케줄): DB 최신 적용일 >= 당월 1일이면 브라우저/프록시 기동 없이 skip.
    force=True(수동 CLI): 항상 board 를 확인.
    """
    bf = BackfillAgent(config, base_dir)
    dates = bf.db.get_available_dates() or []
    latest_db = max(dates) if dates else None
    logger.info("[catchup] DB 최신 적용일: %s (총 %d개 고시일)", latest_db, len(dates))

    ingested: list[dict] = []
    errors: list[str] = []

    # ── 조기 종료: 당월 이미 적재 → 1~7일 일일 재시도 잡이 유료 프록시를 쓰지 않게 ──
    month_first = _month_first()
    if not force and latest_db and latest_db >= month_first:
        logger.info("[catchup] 당월 이미 적재 (DB 최신 %s >= %s) — 브라우저/프록시 미기동, skip",
                    latest_db, month_first)
        return {"latest_db_before": latest_db, "ingested": ingested, "errors": errors,
                "skipped": "already_loaded"}

    proxy = _playwright_proxy()
    if proxy:
        logger.info("[catchup] HIRA_PROXY 사용 (KR IP 우회) — %s", proxy.get("server"))
    else:
        logger.info("[catchup] HIRA_PROXY 미설정 — 직접 접근(한국 IP 환경 전용, fly 는 400 차단)")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            accept_downloads=True, user_agent=_UA, proxy=proxy)
        try:
            # ── 최근 게시물 수집 (page 1~max_pages, 페이지별 재시도) ──
            posts, failed_pages = await _collect_recent_posts(context, max_pages)
            if failed_pages and not posts:
                # 전 페이지 로딩 실패 — '적재 대상 0건 · 오류 []' 로 성공 위장하지 않는다
                msg = (f"HIRA 게시판 로딩 실패 (page {_fmt_pages(failed_pages)}, "
                       f"{_BOARD_RETRIES}회 재시도 소진)")
                logger.error("[catchup] %s", msg)
                errors.append(msg)
            elif failed_pages:
                logger.warning("[catchup] board page %s 로딩 실패 — 수집된 %d건으로 진행",
                               failed_pages, len(posts))

            # ── DB 최신일보다 새롭고 미처리인 것만, 적용일 오름차순 ──
            todo = [p for p in posts
                    if (latest_db is None or p["apply_date"] > latest_db)
                    and not bf.db.is_processed(p["brd_blt_no"])]
            todo.sort(key=lambda p: p["apply_date"])
            logger.info("[catchup] 적재 대상 %d건: %s",
                        len(todo), [p["apply_date"] for p in todo])

            for p in todo:
                ad = p["apply_date"]
                try:
                    path = await _download_with_retry(bf, context, p)
                    if path is None:
                        errors.append(f"{ad} 다운로드 실패(첨부 없음)")
                        continue
                    # 적용일은 제목값(ad) 사용 — detail body 정규식보다 정확
                    count = bf.process_excel(Path(path), ad)
                    if count <= 0:
                        errors.append(f"{ad} 파싱 0행")
                        continue
                    bf.db.log_download(brd_blt_no=p["brd_blt_no"], post_number=0,
                                       apply_date=ad, filename=Path(path).name,
                                       file_path=str(path), status="success")
                    ingested.append({"apply_date": ad, "rows": count})
                    logger.info("[catchup] 적재 완료 %s: %d행", ad, count)
                except Exception as e:
                    logger.error("[catchup] %s 적재 실패: %s", ad, e, exc_info=True)
                    errors.append(f"{ad}: {e}")
        finally:
            await browser.close()

    return {"latest_db_before": latest_db, "ingested": ingested, "errors": errors}


def run_sync(config: dict, base_dir: Path, max_pages: int = 2, *, force: bool = False) -> dict:
    """동기 래퍼 — 스케줄러/CLI 에서 호출."""
    return asyncio.run(catch_up(config, base_dir, max_pages=max_pages, force=force))


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config" / "settings.json")
                     .read_text(encoding="utf-8"))
    # 수동 실행 — 당월 적재 여부와 무관하게 board 확인
    out = run_sync(cfg, Path(__file__).resolve().parents[1], max_pages=3, force=True)
    print(json.dumps(out, ensure_ascii=False, indent=2))
