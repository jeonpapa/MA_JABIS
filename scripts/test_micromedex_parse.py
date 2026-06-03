"""
저장된 Red Book 결과 HTML 을 file:// 로 로드해 _parse_results 만 검증.
Micromedex 세션을 소비하지 않고 파서 정확도만 확인.
"""
import asyncio
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from playwright.async_api import async_playwright  # noqa: E402
from agents.scrapers.us_micromedex import UsMicromedexScraper  # noqa: E402


async def main():
    html_file = BASE / "data" / "foreign" / "us" / "dev_03_after_search.html"
    if not html_file.exists():
        print(f"[err] {html_file} 없음", file=sys.stderr)
        sys.exit(1)

    scraper = UsMicromedexScraper(credentials={"username": "x", "password": "x"})

    html_str = html_file.read_text(encoding="utf-8")
    # Micromedex 페이지는 cookie-check 스크립트가 file:// 로드 시 redirect 를
    # 걸어서 DOM 이 비게 됨. JS 를 끄고 set_content 로 심는다.
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(java_script_enabled=False)
        page = await ctx.new_page()
        await page.set_content(html_str, wait_until="domcontentloaded")
        results = await scraper._parse_results(page, "Keytruda")
        await browser.close()

    print(f"\n=== 파싱 결과: {len(results)}건 ===\n")
    for i, r in enumerate(results):
        ex = r.get("extra", {})
        print(f"[{i}] {r['product_name']} | {r['ingredient']}")
        print(f"    NDC={ex.get('ndc')} form={r['dosage_form']} strength={r['dosage_strength']} pkg={r['package_unit']}")
        print(f"    ${r['local_price']} ({ex.get('price_basis')})  WAC=${ex.get('wac_package')}  AWP/unit=${ex.get('awp_unit')}")
        print(f"    mfr={ex.get('manufacturer')}\n")


if __name__ == "__main__":
    asyncio.run(main())
