"""
Micromedex 개발용 세션 유지 도구.

목적:
  반복적인 login/logout 이 Micromedex 측에 과부하 / 크롤링 경고로 보일 수
  있으므로, 한 번만 로그인한 뒤 창을 열어둔 채 search/parse 로직을 반복
  검증하고 마지막에만 logout + close 한다.

사용법:
  python scripts/dev_micromedex_session.py
  > help
  > search Keytruda
  > dump landing
  > eval document.querySelectorAll('form').length
  > shot current
  > quit      # ← 반드시 이걸로 종료 (Ctrl+C 도 로그아웃 시도)

명령:
  search <q>   WordWheel autocomplete + form.submit 으로 쿼리 실행
  dump <lbl>   현재 페이지 HTML 을 data/foreign/us/dev_<lbl>.html 로 저장
  shot <lbl>   스크린샷 저장
  eval <js>    페이지에서 JS 실행, 결과 출력
  goto <url>   페이지 이동
  reset        REDBOOK 홈으로 복귀
  status       현재 URL / title
  quit         로그아웃 + 브라우저 종료 (필수)
"""
import asyncio
import logging
import shlex
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from agents.scrapers.base import load_credentials  # noqa: E402
from agents.scrapers.us_micromedex import (        # noqa: E402
    UsMicromedexScraper,
    REDBOOK_URL,
    SEL_RB_INPUT,
)
from playwright.async_api import async_playwright  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dev_micromedex")

CACHE_DIR = BASE / "data" / "foreign" / "us"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


async def do_search(scraper: UsMicromedexScraper, page, query: str) -> None:
    """scraper.search 의 로직 그대로 사용."""
    logger.info("→ search: %s", query)
    results = await scraper.search(query, page)
    logger.info("← 결과 %d건. URL=%s", len(results), page.url)
    for i, r in enumerate(results[:5]):
        print(f"  [{i}] {r.get('product_name')!r} ${r.get('local_price')} "
              f"| {r.get('dosage_strength','')} | {r.get('package_unit','')}")


async def do_dump(page, label: str) -> None:
    path = CACHE_DIR / f"dev_{label}.html"
    path.write_text(await page.content(), encoding="utf-8")
    logger.info("HTML dump → %s (%d bytes)", path, path.stat().st_size)


async def do_shot(page, label: str) -> None:
    path = CACHE_DIR / f"dev_{label}.png"
    await page.screenshot(path=str(path), full_page=True)
    logger.info("screenshot → %s", path)


async def do_eval(page, js: str) -> None:
    try:
        result = await page.evaluate(f"() => ({js})")
        print("→", result)
    except Exception as e:
        print("!! eval error:", e)


async def repl(scraper: UsMicromedexScraper, page) -> None:
    print("\n=== Micromedex dev session (logged in) ===")
    print("명령: search|dump|shot|eval|goto|reset|status|quit   (help)\n")
    loop = asyncio.get_running_loop()
    while True:
        try:
            # stdin 읽기를 스레드에 offload
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split(None, 1)
        if not parts:
            continue
        cmd, *args = parts
        cmd = cmd.lower()

        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd == "help":
                print(__doc__)
            elif cmd == "status":
                print("url:", page.url, "| title:", await page.title())
            elif cmd == "reset":
                await page.goto(REDBOOK_URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(2_000)
                print("reset →", page.url)
            elif cmd == "search":
                if not args:
                    print("usage: search <query>")
                    continue
                await do_search(scraper, page, " ".join(args))
            elif cmd == "dump":
                await do_dump(page, args[0] if args else "snapshot")
            elif cmd == "shot":
                await do_shot(page, args[0] if args else "snapshot")
            elif cmd == "eval":
                if not args:
                    print("usage: eval <js-expression>")
                    continue
                await do_eval(page, " ".join(args))
            elif cmd == "goto":
                if not args:
                    print("usage: goto <url>")
                    continue
                await page.goto(args[0], wait_until="domcontentloaded", timeout=30_000)
                print("goto →", page.url)
            else:
                print("unknown cmd:", cmd, "— try: help")
        except Exception as e:
            logger.error("명령 '%s' 실행 실패: %s", cmd, e)


async def main() -> None:
    creds = load_credentials(BASE / "config" / "foreign_credentials.json", "US")
    if not creds.get("username"):
        print("[dev] MICROMEDEX_US_USERNAME/PASSWORD 가 설정되지 않음", file=sys.stderr)
        sys.exit(1)

    scraper = UsMicromedexScraper(
        credentials=creds,
        cache_dir=CACHE_DIR,
        headless=False,
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False, args=UsMicromedexScraper.PLAYWRIGHT_ARGS
        )
        ctx_kwargs = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        if scraper.storage_state_path and scraper.storage_state_path.exists():
            ctx_kwargs["storage_state"] = str(scraper.storage_state_path)
            logger.info("storage_state 재사용: %s", scraper.storage_state_path)

        ctx = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()
        scraper._ctx = ctx
        scraper._browser = browser
        scraper._page = page

        try:
            await scraper.login(page)
            try:
                await ctx.storage_state(path=str(scraper.storage_state_path))
            except Exception as e:
                logger.debug("storage_state 저장 실패: %s", e)

            # 로그인 후 Red Book 홈으로
            await page.goto(REDBOOK_URL, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3_000)
            logger.info("Red Book 홈 도착: %s", page.url)
            if await page.locator(SEL_RB_INPUT).count() == 0:
                logger.warning("Red Book 검색 입력창 없음 — 페이지 확인 필요 (dump landing 권장)")

            await repl(scraper, page)
        finally:
            logger.info("=== 종료 시퀀스: logout → close ===")
            try:
                await scraper.logout(page)
            except Exception as e:
                logger.error("logout 실패: %s", e)
            try:
                await ctx.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
            logger.info("세션 종료 완료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[dev] Ctrl+C 수신 — 종료 시퀀스가 실행됩니다")
