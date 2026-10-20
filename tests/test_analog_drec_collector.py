"""HIRA 약평위 게시판 수집기 — 실제 게시물(brdBltNo=47098, 2026-08-11) HTML 구조를 그대로 쓴 픽스처로 검증. 네트워크 호출 없음."""
from agents.analog.drec_collector import BOARD_URL, discover, download, parse_post, safe_filename

POST_HTML = """<script>var x=1;</script>
<div class="viewCont">
<div class="title">brivaracetam   10mg, 25mg, 50mg, 75mg, 100mg </div>
<ul class="writer">
<li></li>
<li>2026-08-11</li>
<li>726</li>
</ul>
<div class="fileBox">
<ul>
<li>
<i class="icon-file-pdf" title="pdf" aria-label="pdf"></i>
회의자료_브리베타정50밀리그램 등 29품목((주)종근당 등 7개사)
<a href="#none" onclick="downLoadBbs('1','47098','17','58');" class="btn_file">첨부파일 다운로드</a>
</li>
<li>
<i class="icon-file-pdf" title="pdf" aria-label="pdf"></i>
평가결과_브리베타정50밀리그램 등 29품목((주)종근당 등 7개사)
<a href="#none" onclick="downLoadBbs('2','47098','17','58');" class="btn_file">첨부파일 다운로드</a>
</li>
<li>
<i class="icon-file-hwp" title="hwp" aria-label="hwp"></i>
평가결과_브리베타정50밀리그램 등 29품목((주)종근당 등 7개사)
<a href="#none" onclick="downLoadBbs('4','47098','17','58');" class="btn_file">첨부파일 다운로드</a>
</li>
</ul>
</div>
</div>"""
EMPTY_HTML = "<html><body><div>게시물이 존재하지 않습니다.</div></body></html>"


def _fake(pages: dict):
    def get(url: str) -> bytes:
        blt = int(url.split("brdBltNo=")[1].split("&")[0])
        v = pages.get(blt, EMPTY_HTML)
        if isinstance(v, Exception):
            raise v
        return v.encode()
    return get


def test_parse_post_title_date_and_result_pdf_only():
    p = parse_post(POST_HTML, 47098)
    assert p.title == "brivaracetam 10mg, 25mg, 50mg, 75mg, 100mg" and p.post_date == "2026-08-11"
    assert [a.ext for a in p.attachments] == ["pdf", "pdf", "hwp"]
    r = p.result_pdf
    assert r.name.startswith("평가결과_브리베타정50밀리그램") and r.no == "2"                 # 회의자료·hwp 는 받지 않는다
    assert r.url == "https://www.hira.or.kr/bbs/bbsCDownLoad.do?apndNo=2&apndBrdBltNo=47098&apndBrdTyNo=17&apndBltNo=58"


def test_parse_post_without_attachments_is_none():
    assert parse_post(EMPTY_HTML, 47105) is None


def test_discover_finds_new_posts_and_stops_after_gap():
    pages = {47095: POST_HTML, 47096: POST_HTML, 47098: POST_HTML}       # 47097 은 결번
    posts, cursor, failed = discover(47094, get=_fake(pages), max_gap=3)
    assert [p.blt for p in posts] == [47095, 47096, 47098] and cursor == 47098 and failed == []


def test_discover_does_not_advance_past_a_failed_fetch():
    """접속 실패를 '게시물 없음' 으로 취급하면 그 번호의 게시물을 영영 놓친다 — cursor 는 첫 실패 직전에 머문다."""
    pages = {47095: POST_HTML, 47096: TimeoutError("boom"), 47097: POST_HTML}
    posts, cursor, failed = discover(47094, get=_fake(pages), max_gap=3)
    assert [p.blt for p in posts] == [47095, 47097] and failed[0] == 47096 and cursor == 47095


def test_discover_no_new_posts_keeps_cursor():
    posts, cursor, failed = discover(47104, get=lambda u: EMPTY_HTML.encode(), max_gap=4)
    assert posts == [] and cursor == 47104 and failed == []


def test_download_rejects_non_pdf_and_safe_filename():
    import pytest
    att = parse_post(POST_HTML, 47098).result_pdf
    assert download(att, get=lambda u: b"%PDF-1.4 ...") .startswith(b"%PDF")
    with pytest.raises(ValueError):
        download(att, get=lambda u: b"<html>error</html>")
    assert safe_filename('평가결과_가/나:다?.x') == "평가결과_가_나_다_.x.pdf"
    assert safe_filename("평가결과_A.pdf") == "평가결과_A.pdf"
    assert BOARD_URL.format(blt=1).endswith("brdBltNo=1&pageIndex=1")
