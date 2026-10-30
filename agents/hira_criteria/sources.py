"""HIRA 급여기준 원문 '최신본' 탐색.

모든 함수는 화면에 보이는 목록에서 그때그때 최신 게시물을 찾는다(번호·URL 하드코딩 최소화).
찾지 못하면 예외(SourceError) — '없음' 으로 조용히 넘기지 않는다.
"""
from __future__ import annotations

import html as _html
import re
from typing import Optional

from .http import fetch, fetch_text, strip_html

ONCO_NOTICE_BOARD = "https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA030023010000"      # 공고(월별 개정)
ONCO_PRENOTICE_BOARD = "https://www.hira.or.kr/rc/drug/anticancer/antiCncrAnnceList.do?pgmid=HIRAA030023020000"
ONCO_FORM_BOARD = "https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA030023030000"        # 항암화학요법(통합본·허가초과)
EBOOK_LIST = "https://www.hira.or.kr/ra/ebook/list.do?pgmid=HIRAA030402000000"
INSU_LIST = "https://www.hira.or.kr/rc/insu/insuadtcrtr/InsuAdtCrtrList.do?pgmid=HIRAA030069000400"
INSU_POPUP = "https://www.hira.or.kr/rc/insu/insuadtcrtr/InsuAdtCrtrPopup.do"
INSU_BBS_VIEW = "https://www.hira.or.kr/rc/drug/insuadtcrtr/bbsView.do"
BBS_DOWNLOAD = "https://www.hira.or.kr/bbs/bbsCDownLoad.do"
SHARE_DOWNLOAD = "https://www.hira.or.kr/download.do"

INSU_TABS = {"고시": "01", "항암제공고": "19", "행정해석": "02", "심사지침": "09",
             "심의사례공개": "10", "심사사례지침": "17", "자료실": "99"}


class SourceError(RuntimeError):
    pass


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def bbs_download_url(apnd_no: str, brd_blt_no: str, brd_ty_no: str, blt_no: str) -> str:
    return (f"{BBS_DOWNLOAD}?apndNo={apnd_no}&apndBrdBltNo={brd_blt_no}"
            f"&apndBrdTyNo={brd_ty_no}&apndBltNo={blt_no}")


def share_download_url(src: str, fnm: str) -> str:
    from urllib.parse import quote
    return f"{SHARE_DOWNLOAD}?src={quote(src)}&fnm={quote(fnm)}"


def parse_bbs_attachments(page_html: str) -> list[dict]:
    """게시물 상세의 첨부 목록 → [{label, ext, url}]. label 은 화면 표시명(확장자 아이콘 포함)."""
    out = []
    for li in re.findall(r"<li[^>]*>([\s\S]*?)</li>", page_html):
        m = re.search(r"downLoadBbs\('(\d+)','(\d+)','(\d+)','(\d+)'\)", li)
        if not m:
            continue
        label = _clean(re.sub(r"첨부파일 다운로드", "", li))
        ext = (re.search(r'icon-file-(\w+)|title="(hwp|hwpx|pdf|xls|xlsx)"', li) or [None])
        ext_s = ""
        em = re.search(r'title="(hwpx|hwp|pdf|xlsx|xls|zip)"', li)
        if em:
            ext_s = em.group(1)
        out.append({"label": label, "ext": ext_s, "url": bbs_download_url(*m.groups())})
    if not out:  # li 구조가 다른 게시판 — onclick 만이라도
        for m in re.finditer(r"downLoadBbs\('(\d+)','(\d+)','(\d+)','(\d+)'\)", page_html):
            out.append({"label": "", "ext": "", "url": bbs_download_url(*m.groups())})
    return out


# ── 항암: 통합본(공고책자) · 허가초과 요법 ───────────────────────────────────────────────

def onco_form_files() -> dict:
    """항암화학요법 게시판의 '항암화학요법 등 공고내용 전문'(통합본)과 '허가초과 항암요법'(xlsx)."""
    h = fetch_text(ONCO_FORM_BOARD)
    found = {}
    for tr in re.findall(r"<tr>([\s\S]*?)</tr>", h):
        title = _clean(re.sub(r"<td[^>]*class=\"[^\"]*file[\s\S]*", "", tr))
        m = re.search(r"downLoadBbs\('(\d+)','(\d+)','(\d+)','(\d+)'\)", tr)
        if not m:
            continue
        url = bbs_download_url(*m.groups())
        if "공고내용 전문" in title:
            found["consolidated"] = {"title": "항암화학요법 등 공고내용 전문", "url": url}
        elif "허가초과" in title:
            found["offlabel"] = {"title": "허가초과 항암요법", "url": url}
    if "consolidated" not in found:
        raise SourceError("항암 통합본(공고내용 전문) 링크를 찾지 못함")
    return found


def date_from_filename(fname: str) -> Optional[str]:
    """'공고책자_20261101.pdf' → '2026-11-01'."""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", fname or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _ymd(s: str) -> Optional[str]:
    m = re.search(r"(20\d{2})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})", s or "")
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


# ── 항암: 월별 공고(개정) ────────────────────────────────────────────────────────────

def onco_notices(page: int = 1) -> list[dict]:
    """공고 게시판 목록 → [{seq, notice_no, effective_date, posted_date, blt_no, url}] (최신순)."""
    h = fetch_text(f"{ONCO_NOTICE_BOARD}&pageIndex={page}")
    rows = []
    body = h[h.find("<tbody"):]
    for tr in re.findall(r"<tr>([\s\S]*?)</tr>", body):
        m = re.search(r"brdBltNo=(\d+)", tr)
        if not m:
            continue
        cells = {c: _clean(v) for c, v in re.findall(r'<td class="col-([\w-]+)">([\s\S]*?)</td>', tr)}
        no = re.sub(r"\s+", "", cells.get("num2", ""))
        rows.append({
            "seq": cells.get("num"),
            "notice_no": no.replace("제", "공고 제", 1) if no.startswith("제") else no,
            "effective_date": _ymd(cells.get("depart", "")),
            "posted_date": _ymd(cells.get("date", "")),
            "blt_no": m.group(1),
            "url": f"{ONCO_NOTICE_BOARD}&brdScnBltNo=4&brdBltNo={m.group(1)}",
        })
    if not rows:
        raise SourceError("항암 공고 목록 파싱 실패")
    return rows


def onco_latest_notice() -> dict:
    """가장 최근 공고 + 첨부(공고전문/주요공고개정내역) + 본문 요약."""
    latest = onco_notices(1)[0]
    h = fetch_text(latest["url"])
    latest["attachments"] = parse_bbs_attachments(h)
    k = h.find("국민건강보험 요양급여의 기준에 관한 규칙")
    latest["summary"] = strip_html(h[k - 50:k + 6000]).split("이전글")[0].strip() if k > 0 else ""
    return latest


# ── 일반약제: 세부사항(약제) 책자 ─────────────────────────────────────────────────────

def drug_booklet_latest() -> dict:
    """HIRA 전자자료 목록에서 '요양급여의 적용기준 및 방법에 관한 세부사항(약제)' 최신판."""
    h = fetch_text(EBOOK_LIST)
    for li in re.findall(r"<li>([\s\S]*?)</li>", h):
        t = re.search(r'<p class="tit">([\s\S]*?)</p>', li)
        a = re.search(r'href="(/ebooksc/[^"]+\.pdf)"', li)
        if not (t and a):
            continue
        title = _clean(t.group(1))
        if "세부사항(약제)" in title.replace(" ", ""):
            spans = re.findall(r"<span>([\s\S]*?)</span>", li)
            edition = re.search(r"(20\d{2})년\s*(\d{1,2})월판", title)
            return {"title": title, "url": "https://www.hira.or.kr" + a.group(1),
                    "posted_date": _ymd(" ".join(spans)),
                    "edition": f"{edition.group(1)}-{int(edition.group(2)):02d}" if edition else None}
    raise SourceError("세부사항(약제) 책자를 전자자료 목록 1페이지에서 찾지 못함")


# ── 보험인정기준(고시·항암제공고 등) ──────────────────────────────────────────────────

def insu_list(tab: str = "01", page: int = 1, per: int = 100, **extra) -> dict:
    """보험인정기준 목록 1페이지 → {total, rows:[{category, basis, title, posted_date, key}]}.
    key = (mtgHmeDd, sno, mtgMtrRegSno) — 상세 팝업 조회 키."""
    data = {"pageIndex": str(page), "tabGbn": tab, "recordCountPerPage": str(per),
            "decIteTpCd": tab, "seqListYn": "N"}
    data.update({k: str(v) for k, v in extra.items()})
    b, _ = fetch(INSU_LIST, data=data)
    h = b.decode("utf-8", "replace")
    total = re.search(r"전체\s*:\s*(?:<span[^>]*>)?([\d,]+)", h)
    rows = []
    for tr in re.findall(r"<tr>\s*(<td class=\"col-gubun\">[\s\S]*?)</tr>", h):
        on = re.search(r"viewInsuAdtCrtr\(\s*\d+\s*,\s*'(\d+)'\s*,\s*'(\d+)'\s*,\s*'(\d+)'", tr)
        bv = re.search(r"bbsView\('(\w+)','(\d+)'", tr)
        title_m = re.search(r'title="([^"]*?) 새창으로 열기"', tr) or re.search(r'<td class="col-tit">([\s\S]*?)</td>', tr)
        rows.append({
            "category": _clean((re.search(r'col-gubun">([\s\S]*?)</td>', tr) or [None, ""])[1]),
            "basis": _clean((re.search(r'col-num2">([\s\S]*?)</td>', tr) or [None, ""])[1]),
            "title": _clean(title_m.group(1)) if title_m else "",
            "posted_date": _ymd((re.search(r'col-date">([\s\S]*?)</td>', tr) or [None, ""])[1]),
            "key": on.groups() if on else None,
            "bbs_blt_no": bv.group(2) if bv else None,
        })
    return {"total": int(total.group(1).replace(",", "")) if total else None, "rows": rows}


def insu_detail(mtg_hme_dd: str, sno: str, mtg_mtr_reg_sno: str) -> dict:
    """보험인정기준 상세 팝업 → {title, category, basis, posted_date, body_text, sections, attachments, url}."""
    url = f"{INSU_POPUP}?mtgHmeDd={mtg_hme_dd}&sno={sno}&mtgMtrRegSno={mtg_mtr_reg_sno}"
    h = fetch_text(url)
    text = strip_html(h)
    atts = [{"fnm": fnm, "url": share_download_url(src, fnm)}
            for src, fnm in re.findall(r"goDown1\('([^']+)','([^']+)'\)", h)]
    sections = {}
    for m in re.finditer(r"■\s*([^\n■]+?)\n([\s\S]*?)(?=\n■|\n닫기|$)", text):
        sections[m.group(1).strip()] = m.group(2).strip()
    head = text.split("■", 1)[0]
    def _after(label):
        m = re.search(label + r"\s*\n?\s*([^\n]+)", head)
        return m.group(1).strip() if m else None
    title = re.search(r"상세내용\s*\n?\s*인쇄\s*\n?\s*([^\n]+)", text)
    return {"url": url, "title": title.group(1).strip() if title else None,
            "category": _after("분류"), "basis": _after("관련근거"),
            "posted_date": _ymd(_after("게시일") or ""), "body_text": text,
            "sections": sections, "attachments": atts}


# ── KCD: 심평원 상병마스터 ───────────────────────────────────────────────────────────

def kcd_master_latest(max_pages: int = 5) -> dict:
    """보험인정기준 > 자료실(분류 '청구관련기준자료') 에서 제목에 '상병마스터' 가 들어간 최신 게시물과 첨부."""
    for page in range(1, max_pages + 1):
        res = insu_list("99", page, 30, searchCondition3="청구관련기준자료")
        for r in res["rows"]:
            if "상병마스터" in r["title"] and r["bbs_blt_no"]:
                url = f"{INSU_BBS_VIEW}?brdBltNo={r['bbs_blt_no']}&brdScnBltNo=4&pgmid=HIRAA030069000400"
                h = fetch_text(url)
                return {**r, "url": url, "attachments": parse_bbs_attachments(h)}
    raise SourceError("상병마스터 게시물을 자료실에서 찾지 못함")
