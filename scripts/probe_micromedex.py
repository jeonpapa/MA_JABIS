"""
Micromedex 로그인/검색 구조 1회 탐색 스크립트.
- .env 자격증명으로 로그인 시도
- Keytruda 검색 → Red Book (pricing) 페이지 URL / DOM 구조 파악
"""
import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
from agents.scrapers.base import load_credentials

DEBUG_DIR = BASE / "data" / "debug" / "us_micromedex"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)


async def probe():
    creds = load_credentials(BASE / "config" / "foreign_credentials.json", "US")
    print(f"[probe] credentials loaded: user={creds.get('username','')!r}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        print("[probe] goto https://www.micromedexsolutions.com/")
        await page.goto(
            "https://www.micromedexsolutions.com/", wait_until="domcontentloaded", timeout=30_000
        )
        await page.wait_for_timeout(3_000)

        url = page.url
        title = await page.title()
        print(f"[probe] after initial load: url={url} title={title!r}")
        await page.screenshot(path=str(DEBUG_DIR / "01_initial.png"), full_page=True)
        html = await page.content()
        (DEBUG_DIR / "01_initial.html").write_text(html, encoding="utf-8")

        # 로그인 필드 탐색
        has_user = await page.locator("input[name='username'], input[type='text'][id*='user' i]").count()
        has_pass = await page.locator("input[type='password']").count()
        print(f"[probe] username fields={has_user}, password fields={has_pass}")

        if has_pass > 0 and creds.get("username") and creds.get("password"):
            print("[probe] attempting login...")
            try:
                # 일반적인 Micromedex 로그인 폼
                if has_user:
                    await page.locator(
                        "input[name='username'], input[type='text'][id*='user' i]"
                    ).first.fill(creds["username"])
                await page.locator("input[type='password']").first.fill(creds["password"])
                # 로그인 버튼
                btn = page.locator(
                    "button[type='submit'], input[type='submit'], button:has-text('Login'), button:has-text('Sign in')"
                ).first
                if await btn.count() > 0:
                    await btn.click()
                    await page.wait_for_timeout(5_000)
                else:
                    await page.locator("input[type='password']").first.press("Enter")
                    await page.wait_for_timeout(5_000)
            except Exception as e:
                print(f"[probe] login click failed: {e}")

            print(f"[probe] after login: url={page.url} title={await page.title()!r}")
            await page.screenshot(path=str(DEBUG_DIR / "02_after_login.png"), full_page=True)
            (DEBUG_DIR / "02_after_login.html").write_text(
                await page.content(), encoding="utf-8"
            )

        # 검색창 탐색 + Keytruda 조회
        search_selectors = [
            "input[id*='search' i]",
            "input[name*='search' i]",
            "input[placeholder*='search' i]",
            "input[type='search']",
        ]
        search_el = None
        for sel in search_selectors:
            if await page.locator(sel).count() > 0:
                search_el = page.locator(sel).first
                print(f"[probe] search input matched selector: {sel}")
                break

        if search_el:
            try:
                await search_el.fill("Keytruda")
                await search_el.press("Enter")
                await page.wait_for_timeout(6_000)
                print(f"[probe] after search: url={page.url} title={await page.title()!r}")
                await page.screenshot(path=str(DEBUG_DIR / "03_search_keytruda.png"), full_page=True)
                (DEBUG_DIR / "03_search_keytruda.html").write_text(
                    await page.content(), encoding="utf-8"
                )

                # Red Book 링크 탐색
                rb = page.locator("a:has-text('Red Book'), a:has-text('RED BOOK'), a:has-text('Pricing')")
                print(f"[probe] Red Book/Pricing links: {await rb.count()}")
                for i in range(min(5, await rb.count())):
                    href = await rb.nth(i).get_attribute("href")
                    text = (await rb.nth(i).inner_text()).strip()[:80]
                    print(f"  [{i}] text={text!r} href={href}")
            except Exception as e:
                print(f"[probe] search failed: {e}")

        print(f"[probe] done — artifacts at: {DEBUG_DIR}")
        await ctx.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(probe())
