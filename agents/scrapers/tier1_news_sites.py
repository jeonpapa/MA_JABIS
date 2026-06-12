"""Tier 1 전문지 사이트 직접 검색 (하이브리드 수집의 site 축).

Naver News API 가 인덱싱하지 못한 전문지 기사를 1차 소스(전문지 자체 검색)에서 직접 확보.
대상은 newsmaster CMS 계열(동일 HTML 구조) 의 핵심 T1 전문지 — 제너릭 파서 1개로 커버.

배포·완전자동화 고려:
  - 모든 네트워크 호출 try/except + timeout, 사이트별 실패 격리(한 곳 죽어도 나머지 진행).
  - 정중한 rate (요청 간 SITE_DELAY_S) + 표준 UA. robots: 검색결과 list 페이지만, 저빈도(주 1회).
  - 외부 상태/대화 의존 없음 → 헤드리스 스케줄러에서 그대로 실행 가능.
  - 신규 사이트 추가 = NEWSMASTER_SITES 에 도메인 1줄. 구조 다른 사이트는 별도 parser 필요(현재 제외).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 12
SITE_DELAY_S = 0.8          # 사이트 내 페이지 간 정중한 간격
MAX_PAGES = 5               # 사이트당 최대 페이지 (페이지당 ~20건)
_DATE_FULL = re.compile(r"(20\d\d)[-./](\d{1,2})[-./](\d{1,2})")
# 연도 없는 list 표기 (예: '06.12 06:47', '06-12 05:56') — 뒤에 HH:MM 동반 시만 (오탐 방지)
_DATE_MD = re.compile(r"(?<!\d)(\d{1,2})[-./](\d{1,2})\s+\d{1,2}:\d{2}")

# newsmaster CMS 계열 T1 전문지 — GET 검색이 **실제 쿼리 필터로 작동**하는 곳만 등록.
#   (2026-06 조사: docdocdoc/monews/medipana/bosa/kpanews/mdtoday/pharmnews 등은 GET 검색이
#    무시되고 최신목록만 반환 → 직접검색 부적합. 단 이들 매체는 Naver 가 충분히 인덱싱하므로
#    Naver 축으로 커버됨. 뉴스더보이스는 Naver 인덱싱이 약해 직접검색이 핵심 갭필러.)
#   신규 추가 시: 옵디보 vs 무의미어 결과가 달라야(검색 작동) + 파서가 날짜 추출 가능해야 함.
NEWSMASTER_SITES: dict[str, str] = {
    "newsthevoice.com": "뉴스더보이스",
    "hitnews.co.kr":    "히트뉴스",
    "medipana.com":     "메디파나뉴스",   # GET 검색 무시 → POST 검색만 작동 (POST_SITES)
}

# GET articleList 검색이 비활성이고 POST 로만 필터되는 사이트
POST_SITES: set[str] = {"medipana.com"}


@dataclass
class SiteNewsItem:
    title: str
    url: str            # 절대 URL (publisher 원문)
    description: str
    pub_date: datetime
    source_name: str
    source_domain: str

    @property
    def date_str(self) -> str:
        return self.pub_date.strftime("%Y-%m-%d")


def _abs_url(domain: str, href: str) -> str:
    if href.startswith("http"):
        return href
    return f"https://www.{domain}{href}" if href.startswith("/") else f"https://www.{domain}/{href}"


def _parse_date(text: str) -> Optional[datetime]:
    text = text or ""
    m = _DATE_FULL.search(text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # 연도 없는 MM.DD HH:MM → 연도 추론 (미래면 작년)
    m = _DATE_MD.search(text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        today = datetime.now()
        for yr in (today.year, today.year - 1):
            try:
                cand = datetime(yr, mo, d)
            except ValueError:
                return None
            if cand <= today + timedelta(days=1):
                return cand
    return None


# newsmaster 변형별 item 컨테이너 (우선순위) — 셋 다 probe 로 확인
_CONTAINER_SELECTORS = [
    "div.article-list section.article-list-content div.list-block",   # 모던 (뉴스더보이스)
    "section#section-list ul.type1 > li, section#section-list ul.type2 > li",  # 클래식 (청년의사·히트뉴스)
    "article.altlist-body ul.altlist-webzine > li, ul.altlist-webzine > li",   # webzine 변형 (메디파나)
    "section.contents .auto-article .item, .auto-article .item",       # 변형 (라포르시안)
    "div.list-block",
]
_TITLE_SELECTORS = ("h4.titles a, div.list-titles a, .list-titles a, .titles a, "
                    "strong.titles a, h2.altlist-subject a, .altlist-subject a")
_SUMMARY_SELECTORS = ("p.list-summary, .list-summary, p.lead, .lead, .desc, "
                      ".auto-summary, .altlist-summary, p.altlist-summary")


def _parse_newsmaster(html: str, domain: str, source_name: str) -> list[SiteNewsItem]:
    """newsmaster 검색결과 list 파싱 — 3개 변형 컨테이너 대응."""
    soup = BeautifulSoup(html, "lxml")
    blocks = []
    for sel in _CONTAINER_SELECTORS:
        blocks = soup.select(sel)
        if blocks:
            break
    out: list[SiteNewsItem] = []
    seen_idx: set[str] = set()
    for b in blocks:
        a = b.select_one(_TITLE_SELECTORS)
        if not a or not a.get("href"):
            # fallback: 컨테이너 내 첫 articleView 링크(의미있는 텍스트)
            a = next((x for x in b.find_all("a", href=True)
                      if "articleView" in x["href"] and len(x.get_text(strip=True)) > 8), None)
        if not a or "articleView" not in a.get("href", ""):
            continue
        href = a["href"]
        title = a.get_text(strip=True)
        if not title or len(title) < 6:
            continue
        # idxno 기준 컨테이너 중복 제거 (한 블록에 title+image 링크 중복)
        m = re.search(r"idxno=(\d+)", href)
        idx = m.group(1) if m else href
        if idx in seen_idx:
            continue
        seen_idx.add(idx)
        summary_el = b.select_one(_SUMMARY_SELECTORS)
        # 날짜: 전용 element → block 전체 → 부모 wrapper 순 (변형 대응)
        dated_el = b.select_one(".list-dated, .byline, .dated, .date, .auto-byline")
        dt = (_parse_date(dated_el.get_text(" ", strip=True) if dated_el else "")
              or _parse_date(b.get_text(" ", strip=True))
              or (_parse_date(b.parent.get_text(" ", strip=True)) if b.parent else None))
        if dt is None:
            continue  # 날짜 없으면 보존기간 계산 불가 → skip (날조 금지)
        out.append(SiteNewsItem(
            title=title,
            url=_abs_url(domain, href),
            description=(summary_el.get_text(strip=True) if summary_el else ""),
            pub_date=dt,
            source_name=source_name,
            source_domain=domain,
        ))
    return out


def search_site(domain: str, query: str, lookback_days: int,
                max_pages: int = MAX_PAGES) -> list[SiteNewsItem]:
    """단일 newsmaster 사이트 검색. 실패 시 빈 리스트 (격리)."""
    source_name = NEWSMASTER_SITES.get(domain, domain)
    cutoff = datetime.now() - timedelta(days=lookback_days)
    is_post = domain in POST_SITES
    items: list[SiteNewsItem] = []
    for page in range(1, max_pages + 1):
        action = f"https://www.{domain}/news/articleList.html"
        try:
            if is_post:
                r = requests.post(action, headers={"User-Agent": UA}, timeout=TIMEOUT,
                                  data={"sc_area": "A", "view_type": "sm",
                                        "news_search_type": "", "sc_word": query, "page": page})
            else:
                r = requests.get(action, headers={"User-Agent": UA}, timeout=TIMEOUT,
                                 params={"sc_word": query, "sc_area": "A",
                                         "view_type": "sm", "page": page})
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
        except requests.RequestException as e:
            logger.warning("[tier1_site] %s p%d 실패: %s", domain, page, e)
            break
        page_items = _parse_newsmaster(r.text, domain, source_name)
        if not page_items:
            break
        items.extend(page_items)
        # 마지막 항목이 cutoff 이전이면 중단
        if page_items[-1].pub_date < cutoff:
            break
        time.sleep(SITE_DELAY_S)
    return [it for it in items if it.pub_date >= cutoff]


def search_all_sites(query: str, lookback_days: int) -> list[SiteNewsItem]:
    """전체 등록 사이트에서 검색 (사이트별 실패 격리)."""
    results: list[SiteNewsItem] = []
    for domain in NEWSMASTER_SITES:
        try:
            found = search_site(domain, query, lookback_days)
            results.extend(found)
        except Exception as e:
            logger.warning("[tier1_site] %s 전체 실패: %s", domain, e)
        time.sleep(SITE_DELAY_S)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    qq = sys.argv[1] if len(sys.argv) > 1 else "옵디보"
    for dom in NEWSMASTER_SITES:
        res = search_site(dom, qq, 183)
        print(f"{dom:20} {len(res):3}건  최신: {res[0].date_str if res else '-'} | {res[0].title[:40] if res else ''}")
