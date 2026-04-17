"""KR MFDS (식약처) 변경이력 scraper — 효능·효과 버전별 파싱.

데이터 소스:
  https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemChangeHistList?itemSeq=<X>&docType=EE&page=1
    → 효능·효과 변경이력 목록 HTML (각 <tr> 에 data-docdata 로 DOC XML 임베드)

각 history 항목 구조 (DOC/SECTION/ARTICLE/PARAGRAPH):
  <DOC title="효능효과" type="EE">
    <SECTION title="">
      <ARTICLE title="흑색종">
        <PARAGRAPH>1. 수술이 불가능하거나 전이성인 흑색종 환자의 치료</PARAGRAPH>
        <PARAGRAPH>2. 완전 절제술을 받은 IIB기...수술 후 보조요법 치료</PARAGRAPH>
      </ARTICLE>
      <ARTICLE title="비소세포폐암">
        ...
      </ARTICLE>
    </SECTION>
  </DOC>

용도: 시간 순 정렬 후 인접 버전 PARAGRAPH diff → (변경일자, 신규 추가 indication) 추출.
이로써 MFDS 적응증별 실제 승인일을 'estimated' 가 아닌 'official' 로 확정한다.
"""
from __future__ import annotations

import html as _html
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

HIST_LIST_URL = "https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemChangeHistList"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "hta_cache" / "MFDS_HISTORY"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class MFDSHistArticle:
    title: str                 # disease header (예: "흑색종")
    paragraphs: list[str]      # 번호 포함 원문 indication 목록


@dataclass
class MFDSHistVersion:
    history_seq: str           # "113"
    ordinal: str               # "28"
    change_date: str           # "2025-10-02"
    articles: list[MFDSHistArticle]
    raw_xml: str               # unescape 된 원본 XML

    def article_by_title(self, title: str) -> Optional[MFDSHistArticle]:
        for a in self.articles:
            if a.title == title:
                return a
        return None


# ─── 목록 HTML fetch (+ 디스크 캐시) ─────────────────────────────────────────

def _fetch_hist_list_html(item_seq: str, doc_type: str = "EE", use_cache: bool = True) -> str:
    cache_path = CACHE_DIR / f"{item_seq}_{doc_type}.html"
    if use_cache and cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    params = {"itemSeq": item_seq, "docType": doc_type, "page": 1}
    headers = {
        "User-Agent": UA,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemChangeHistInfo?itemSeq={item_seq}&docType={doc_type}",
    }
    r = requests.get(HIST_LIST_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    html = r.text
    cache_path.write_text(html, encoding="utf-8")
    logger.info("MFDS history cached: %s (%d bytes)", cache_path.name, len(html))
    time.sleep(0.5)
    return html


# ─── HTML → 버전 리스트 파싱 ─────────────────────────────────────────────────

# onclick="detailHist('113', '2025-10-02', this); return false;" (HTML-encoded 시 &#39;)
_RE_DETAIL = re.compile(
    r"detailHist\(\s*(?:&#39;|')(?P<seq>\d+)(?:&#39;|')\s*,\s*"
    r"(?:&#39;|')(?P<date>\d{4}-\d{2}-\d{2})(?:&#39;|')"
)
# data-docdata="<escaped DOC XML>"
_RE_DOCDATA = re.compile(r'data-docdata="(?P<xml>[^"]*)"')
# 순번 span (2번째 <span>)
_RE_ORDINAL = re.compile(r'<span class="s-th">순번</span>\s*<span>(?P<ord>\d+)</span>')
# <tr>...</tr> 블록 (tbody 내부)
_RE_TR = re.compile(r"<tr[^>]*>(?P<body>.*?)</tr>", re.DOTALL)


def _parse_doc_xml(xml_text: str) -> list[MFDSHistArticle]:
    """DOC/SECTION/ARTICLE/PARAGRAPH → [MFDSHistArticle(...)]."""
    if not xml_text.strip():
        return []
    # 일부 버전은 DOC 외부에 공백 / 인코딩 이슈 존재 가능 — 방어적 파싱
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("MFDS DOC XML parse error: %s", exc)
        return []

    out: list[MFDSHistArticle] = []
    # root 가 DOC 자체일 수도 있고 그 상위일 수도 있으므로 모두 순회
    articles = root.iter("ARTICLE")
    for art in articles:
        title = (art.get("title") or "").strip()
        paras: list[str] = []
        for p in art.iter("PARAGRAPH"):
            # CDATA 는 .text 로 나옴
            txt = (p.text or "").strip()
            if txt:
                paras.append(txt)
        if title or paras:
            out.append(MFDSHistArticle(title=title, paragraphs=paras))
    return out


def parse_history_list(html: str) -> list[MFDSHistVersion]:
    """목록 HTML → MFDSHistVersion 리스트 (change_date 오름차순 정렬)."""
    versions: list[MFDSHistVersion] = []
    for m in _RE_TR.finditer(html):
        body = m.group("body")
        mdetail = _RE_DETAIL.search(body)
        mdoc = _RE_DOCDATA.search(body)
        if not mdetail or not mdoc:
            continue
        mord = _RE_ORDINAL.search(body)
        hist_seq = mdetail.group("seq")
        change_date = mdetail.group("date")
        ordinal = mord.group("ord") if mord else ""
        # data-docdata 는 HTML-encoded: unescape → 실제 XML
        raw_xml = _html.unescape(mdoc.group("xml"))
        articles = _parse_doc_xml(raw_xml)
        versions.append(MFDSHistVersion(
            history_seq=hist_seq,
            ordinal=ordinal,
            change_date=change_date,
            articles=articles,
            raw_xml=raw_xml,
        ))
    # 시간 순 (과거 → 현재) 정렬
    versions.sort(key=lambda v: v.change_date)
    return versions


# ─── Diff: 인접 버전 간 신규 PARAGRAPH 추출 ─────────────────────────────────

@dataclass
class MFDSHistDiff:
    change_date: str             # 신규 PARAGRAPH 가 처음 등장한 변경일
    article_title: str           # disease header
    new_paragraph: str           # 본문
    prev_date: Optional[str]     # 직전 버전 변경일 (없으면 최초)


def _norm_para(text: str) -> str:
    """정렬 번호/공백 변동을 허용한 정규화 키.

    '1. 수술이 ...' vs '2. 수술이 ...' 는 실제로는 동일 indication 이지만 번호 부여가
    바뀌어 재-등장한 것일 수 있음 → 리딩 번호 제거 후 비교.
    """
    s = text.strip()
    s = re.sub(r"^\d+[\.\)]\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def diff_versions(versions: list[MFDSHistVersion]) -> list[MFDSHistDiff]:
    """인접 버전 비교 후 (해당 ARTICLE 에) 새로 등장한 PARAGRAPH 를 반환.

    규칙:
      - 이전 버전에 동일 ARTICLE 이 없었으면 → 그 ARTICLE 의 모든 PARAGRAPH 가 신규
      - 이전 버전에 있었으나 PARAGRAPH 가 신규인 경우 → 해당 PARAGRAPH 만 신규
    """
    if not versions:
        return []

    diffs: list[MFDSHistDiff] = []

    # 첫 버전: 모든 ARTICLE/PARAGRAPH 를 '신규' 로 기록 (baseline = 최초 허가 시점 적응증)
    first = versions[0]
    for art in first.articles:
        for p in art.paragraphs:
            diffs.append(MFDSHistDiff(
                change_date=first.change_date,
                article_title=art.title,
                new_paragraph=p,
                prev_date=None,
            ))

    # 이후 버전: 직전 버전과 비교
    for i in range(1, len(versions)):
        prev = versions[i - 1]
        curr = versions[i]
        prev_by_title: dict[str, set[str]] = {
            a.title: {_norm_para(p) for p in a.paragraphs} for a in prev.articles
        }
        for art in curr.articles:
            prev_paras = prev_by_title.get(art.title, set())
            for p in art.paragraphs:
                if _norm_para(p) not in prev_paras:
                    diffs.append(MFDSHistDiff(
                        change_date=curr.change_date,
                        article_title=art.title,
                        new_paragraph=p,
                        prev_date=prev.change_date,
                    ))
    return diffs


# ─── 공개 API ────────────────────────────────────────────────────────────────

def fetch_versions(item_seq: str, doc_type: str = "EE",
                   use_cache: bool = True) -> list[MFDSHistVersion]:
    html = _fetch_hist_list_html(item_seq, doc_type=doc_type, use_cache=use_cache)
    return parse_history_list(html)


def fetch_diffs(item_seq: str, doc_type: str = "EE",
                use_cache: bool = True) -> tuple[list[MFDSHistVersion], list[MFDSHistDiff]]:
    versions = fetch_versions(item_seq, doc_type=doc_type, use_cache=use_cache)
    return versions, diff_versions(versions)


if __name__ == "__main__":
    # CLI 검증용: 키트루다
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    versions, diffs = fetch_diffs("201501487")
    print(f"versions: {len(versions)}")
    for v in versions[:3] + versions[-1:]:
        print(f"  [{v.change_date}] seq={v.history_seq} ord={v.ordinal} "
              f"articles={len(v.articles)} paras={sum(len(a.paragraphs) for a in v.articles)}")
    print(f"\ndiffs (first-appearance paragraphs): {len(diffs)}")
    seen_dates: dict[str, int] = {}
    for d in diffs:
        seen_dates[d.change_date] = seen_dates.get(d.change_date, 0) + 1
    for dt, n in sorted(seen_dates.items()):
        print(f"  {dt}: +{n} paragraph(s)")
