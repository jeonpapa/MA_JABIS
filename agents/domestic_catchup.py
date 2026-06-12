"""국내 약가 월별 자동 캐치업 — 누락 자동복구형 루틴.

HIRA 약가고시현황 board 최근 게시물 중 **DB 최신 적용일보다 새로운 달을 전부** 다운로드·적재.
스케줄러 미가동/실패로 빠진 달(예: 5·6월)이 있어도 다음 실행에서 자동 복구한다.

- 소스: HIRA board (config domestic_agent.hira_board_url) 의 '약제급여목록및급여상한금액표' 게시물.
- 다운로드/파싱/적재는 검증된 BackfillAgent(download_post_excel·process_excel, ZIP·구형 포맷 대응) 재사용.
- 멱등: 이미 적재된(브dBltNo processed) 게시물 skip. 적용일은 게시물 제목의 'YYYY.MM.DD' 사용.
- 배포 안전: headless chromium, 외부 상태 의존 없음. 스케줄러(매월 1일) 가 호출.

실행: agents/domestic_catchup.run_sync(config, base_dir)  또는  python -m agents.domestic_catchup
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from playwright.async_api import async_playwright

from agents.backfill_agent import BackfillAgent, HIRA_BOARD_URL

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_DATE_RE = re.compile(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})")
_LIST_KEYWORD = "약제급여목록"  # 약가 상한금액표 게시물만 (타 공지 제외)


async def catch_up(config: dict, base_dir: Path, max_pages: int = 2) -> dict:
    """누락 월 자동 적재. 반환: {latest_db_before, ingested:[{apply_date,rows}], errors:[...]}"""
    bf = BackfillAgent(config, base_dir)
    dates = bf.db.get_available_dates() or []
    latest_db = max(dates) if dates else None
    logger.info("[catchup] DB 최신 적용일: %s (총 %d개 고시일)", latest_db, len(dates))

    ingested: list[dict] = []
    errors: list[str] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=True, user_agent=_UA)
        page = await context.new_page()
        try:
            # ── 최근 게시물 수집 (page 1~max_pages) ──
            posts: list[dict] = []
            seen_brd: set[int] = set()
            for pidx in range(1, max_pages + 1):
                try:
                    await page.goto(f"{HIRA_BOARD_URL}&pageIndex={pidx}",
                                    wait_until="networkidle", timeout=30000)
                except Exception as e:
                    logger.warning("[catchup] page %d 로딩 실패: %s", pidx, e)
                    continue
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
                    path, _detail_date = await bf.download_post_excel(page, p)
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


def run_sync(config: dict, base_dir: Path, max_pages: int = 2) -> dict:
    """동기 래퍼 — 스케줄러/CLI 에서 호출."""
    return asyncio.run(catch_up(config, base_dir, max_pages=max_pages))


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = json.loads((Path(__file__).resolve().parents[1] / "config" / "settings.json")
                     .read_text(encoding="utf-8"))
    out = run_sync(cfg, Path(__file__).resolve().parents[1], max_pages=3)
    print(json.dumps(out, ensure_ascii=False, indent=2))
