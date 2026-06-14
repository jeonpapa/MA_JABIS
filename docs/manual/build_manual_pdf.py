"""기능 소개 매뉴얼 HTML → 가로(A4 landscape) PDF 렌더링.

프로젝트에 이미 설치된 Playwright chromium 으로 print-to-pdf.
실행: .venv/bin/python docs/manual/build_manual_pdf.py
출력: MA_AI_Dossier_기능소개_매뉴얼.pdf (프로젝트 루트)
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = Path(__file__).resolve().parents[2]
HTML = BASE / "docs" / "manual" / "feature_manual.html"
OUT = BASE / "MA_AI_Dossier_기능소개_매뉴얼.pdf"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(HTML.as_uri(), wait_until="networkidle")
        await page.pdf(
            path=str(OUT),
            landscape=True,
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        await browser.close()
    print(f"PDF 생성 완료: {OUT} ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    asyncio.run(main())
