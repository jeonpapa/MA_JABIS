"""BackfillAgent.download_post_excel 첨부 링크 선택 robustness (2026-09-01 프로덕션 실패 회귀).

프로덕션(HIRA_PROXY 느린 residential proxy) 에서 관측된 실패:
- `a.btn_file` 이 아직 안 붙은 시점에 count()==0 → 폴백이 **숨은 SNS 공유 링크**
  (`<a href="#none" onclick="Navi.naverBlog(); return false;">`) 를 골라 click 30s 타임아웃.

여기서는 네트워크 미접촉 — page.route 로 HIRA 상세 URL 을 로컬 HTML 로 fulfill.
- (a) a.btn_file 이 JS 로 늦게 주입되는 페이지 → 붙을 때까지 대기 후 정상 다운로드
- (b) 숨은 SNS 공유 링크만 있는 페이지 → 클릭 금지, (None, apply_date)
- (c) 구형 페이지(a[href='#none'] + downLoadBbs onclick, 가시) → 폴백 다운로드 (SNS 배제)
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.backfill_agent import (  # noqa: E402
    BackfillAgent,
    _is_download_onclick,
    _is_share_link,
)

ROOT = Path(__file__).resolve().parent.parent

# 실제 HIRA 상세 페이지의 SNS 공유 블록 (숨김) — 폴백이 절대 고르면 안 되는 대상
_SHARE_BLOCK = """
<ul class="utilshare-list" style="display:none">
  <li><a href="#none" title="새창열림" onclick="Navi.naverBlog(); return false;">네이버 블로그</a></li>
  <li><a href="#none" title="새창열림" onclick="Navi.facebook(); return false;">페이스북</a></li>
  <li><a href="#none" onclick="Navi.kakao(); return false;">카카오</a></li>
</ul>
"""

# 실제 페이지의 downLoadBbs 를 blob 다운로드로 대체 (클릭 시 Download 이벤트 발생)
_JS = """
<script>
window.__navi_clicked = 0;
window.Navi = {
  naverBlog: function(){ window.__navi_clicked += 1; },
  facebook:  function(){ window.__navi_clicked += 1; },
  kakao:     function(){ window.__navi_clicked += 1; }
};
function downLoadBbs(a, b, c, d) {
  var blob = new Blob(["dummy-excel"], {type: "application/octet-stream"});
  var link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "list_" + b + ".xlsx";
  document.body.appendChild(link);
  link.click();
}
</script>
"""

_BTN_FILE = ("<a class=\"btn_file\" href=\"#none\" "
             "onclick=\"downLoadBbs('1','1709','1','59');\">첨부파일 다운로드</a>")


def _page_delayed_btn_file(delay_ms: int) -> str:
    """9/1 게시물 형태 — a.btn_file 이 goto(networkidle/domcontentloaded) 이후 늦게 붙는다."""
    return f"""<html><body>
<h3>약제급여목록및급여상한금액표(2026.09.01.적용)</h3>
{_SHARE_BLOCK}
<div id="file"></div>
<script>
setTimeout(function() {{
  document.getElementById("file").innerHTML = {json.dumps(_BTN_FILE)};
}}, {delay_ms});
</script>
{_JS}
</body></html>"""


_PAGE_ONLY_SHARE = f"""<html><body>
<h3>약제급여목록및급여상한금액표(2026.09.01.적용)</h3>
{_SHARE_BLOCK}
<a href="#none" style="display:none" onclick="Navi.naverBlog(); return false;">숨은 공유</a>
{_JS}
</body></html>"""


_PAGE_LEGACY = f"""<html><body>
<h3>약제급여목록및급여상한금액표(2019.03.01.적용)</h3>
{_SHARE_BLOCK}
<div class="file">
  <a href="#none" onclick="downLoadBbs('1','1234','1','59');">첨부파일</a>
</div>
{_JS}
</body></html>"""


def _agent(tmp_path: Path) -> BackfillAgent:
    cfg = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    (tmp_path / "data" / "db").mkdir(parents=True)
    (tmp_path / "data" / "raw").mkdir(parents=True)
    return BackfillAgent(cfg, tmp_path)


async def _download(html: str, tmp_path: Path, **kw):
    agent = _agent(tmp_path)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(accept_downloads=True)
        page = await ctx.new_page()
        await page.route(
            "**/bbsDummyKR.do*",
            lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"),
        )
        post = {"href": "?pgmid=HIRAA030014050000&brdBltNo=99999", "post_number": 0}
        try:
            res = await agent.download_post_excel(page, post, **kw)
            navi_clicked = await page.evaluate("window.__navi_clicked")
            return res, navi_clicked
        finally:
            await browser.close()


# ── 순수 헬퍼 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("onclick, expected", [
    ("downLoadBbs('1','1709','1','59');", True),
    ("fn_egov_downFile('FILE_000001','0')", True),
    ("javascript:download('x')", True),
    ("Navi.naverBlog(); return false;", False),
    ("Navi.facebook(); return false;", False),
    ("kakaoShare(); return false;", False),
    ("", False),
    (None, False),
])
def test_is_download_onclick(onclick, expected):
    assert _is_download_onclick(onclick) is expected


@pytest.mark.parametrize("onclick, href, expected", [
    ("Navi.naverBlog(); return false;", "#none", True),
    ("Navi.facebook(); return false;", "#none", True),
    ("shareTwitter()", "#none", True),
    ("kakaoLink()", "#none", True),
    ("", "https://www.facebook.com/sharer", True),
    ("downLoadBbs('1','1709','1','59');", "#none", False),
])
def test_is_share_link(onclick, href, expected):
    assert _is_share_link(onclick, href) is expected


# ── Playwright (로컬 HTML, 네트워크 미접촉) ───────────────────────────────────

def test_waits_for_late_btn_file_then_downloads(tmp_path):
    """(a) 느린 프록시 시뮬레이션 — a.btn_file 이 1.5s 뒤 주입돼도 대기 후 다운로드."""
    (path, apply_date), navi_clicked = asyncio.run(
        _download(_page_delayed_btn_file(1500), tmp_path, attach_timeout_ms=6000)
    )
    assert path is not None and Path(path).is_file()
    assert Path(path).name.endswith(".xlsx") and "20260901" in Path(path).name
    assert apply_date == "2026.09.01"
    assert navi_clicked == 0


def test_hidden_share_link_never_clicked(tmp_path):
    """(b) a.btn_file 없음 + 숨은 SNS 공유 링크만 → 클릭 없이 (None, apply_date) 즉시 반환."""
    t0 = time.monotonic()
    (path, apply_date), navi_clicked = asyncio.run(
        _download(_PAGE_ONLY_SHARE, tmp_path, attach_timeout_ms=800)
    )
    elapsed = time.monotonic() - t0
    assert path is None
    assert apply_date == "2026.09.01"
    assert navi_clicked == 0
    assert elapsed < 20, f"숨은 링크 click 대기(30s)로 빠진 듯: {elapsed:.1f}s"


def test_legacy_page_fallback_downloads_visible_download_anchor(tmp_path):
    """(c) 구형 페이지 호환 — 가시 a[href='#none'] + downLoadBbs onclick 은 폴백으로 다운로드."""
    (path, apply_date), navi_clicked = asyncio.run(
        _download(_PAGE_LEGACY, tmp_path, attach_timeout_ms=800)
    )
    assert path is not None and Path(path).is_file()
    assert Path(path).name.endswith("list_1234.xlsx")
    assert apply_date == "2019.03.01"
    assert navi_clicked == 0
