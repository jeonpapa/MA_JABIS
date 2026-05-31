"""
Markdown 리포트 → PDF 변환.

흐름
----
1. markdown 텍스트 읽기 → HTML 변환 (tables/fenced_code/toc extension)
2. 한국어 시스템 폰트(Apple SD Gothic Neo / Pretendard) CSS 적용
3. Playwright chromium headless로 HTML 렌더 → page.pdf() (A4, 18mm margin)

사용
----
    python -m agents.amjilsim_tracker.reporters.pdf_renderer <md_path> [pdf_path]

    # 기본: 같은 디렉토리에 .pdf 확장자로 저장
    python -m agents.amjilsim_tracker.reporters.pdf_renderer \\
        ~/심평원보고/reports/2026-05-28_session-4_d_plus_1.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright


CSS = """
@page { size: A4; margin: 18mm 16mm; }
body {
    font-family: 'Apple SD Gothic Neo', -apple-system, 'Pretendard',
                 'Helvetica Neue', sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    color: #1d1d1f;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}
h1 {
    font-size: 19pt;
    border-bottom: 2px solid #1d1d1f;
    padding-bottom: 6pt;
    margin: 0 0 16pt 0;
    page-break-after: avoid;
}
h2 {
    font-size: 13.5pt;
    border-bottom: 1px solid #cccccc;
    padding-bottom: 3pt;
    margin: 20pt 0 8pt 0;
    page-break-after: avoid;
}
h3 {
    font-size: 11.5pt;
    margin: 14pt 0 6pt 0;
    page-break-after: avoid;
}
p { margin: 6pt 0; }
table {
    border-collapse: collapse;
    width: 100%;
    margin: 8pt 0;
    font-size: 9.5pt;
    page-break-inside: avoid;
}
th, td {
    border: 1px solid #d0d0d0;
    padding: 4pt 6pt;
    text-align: left;
    vertical-align: top;
}
th { background: #f5f5f7; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
code {
    background: #f0f0f5;
    padding: 1pt 4pt;
    border-radius: 3pt;
    font-family: 'SF Mono', 'Menlo', Consolas, monospace;
    font-size: 9pt;
}
pre {
    background: #f5f5f7;
    padding: 8pt 10pt;
    border-radius: 4pt;
    overflow-x: auto;
    font-size: 9pt;
    page-break-inside: avoid;
}
pre code { background: transparent; padding: 0; }
blockquote {
    border-left: 3px solid #cccccc;
    padding: 4pt 0 4pt 12pt;
    color: #555555;
    margin: 10pt 0;
    font-size: 10pt;
}
hr { border: none; border-top: 1px solid #cccccc; margin: 16pt 0; }
ul, ol { padding-left: 22pt; }
li { margin: 3pt 0; }
strong { color: #1d1d1f; font-weight: 700; }
a { color: #0066cc; text-decoration: none; }
"""


def md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    if not md_path.exists():
        raise FileNotFoundError(md_path)

    md_text = md_path.read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        output_format="html5",
    )
    title = md_path.stem
    html = (
        f"<!doctype html><html lang='ko'><head>"
        f"<meta charset='utf-8'>"
        f"<title>{title}</title>"
        f"<style>{CSS}</style>"
        f"</head><body>{html_body}</body></html>"
    )

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            margin={"top": "18mm", "bottom": "18mm",
                    "left": "16mm", "right": "16mm"},
            print_background=True,
            prefer_css_page_size=True,
        )
        browser.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("md_path", type=Path, help="입력 markdown 파일")
    p.add_argument("pdf_path", type=Path, nargs="?", default=None,
                   help="출력 PDF (생략 시 .md와 같은 위치에 .pdf 확장자)")
    args = p.parse_args()

    out = args.pdf_path or args.md_path.with_suffix(".pdf")
    md_to_pdf(args.md_path, out)
    print(f"✅ {out}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
