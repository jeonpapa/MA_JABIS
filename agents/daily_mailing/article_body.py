"""기사 본문 추출 — Daily Mailing 요약·인용 검증의 근거.

설계 §5 ④. 네이버 뉴스(n.news.naver.com)는 `#dic_area` 구조가 일정하므로 우선 시도하고,
언론사 원문은 알려진 본문 선택자 → `<p>` 밀도 폴백 순으로 추출한다.
결과 status: ok(≥MIN_BODY_CHARS) / too_short / failed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import requests
from bs4 import BeautifulSoup

MIN_BODY_CHARS = 200
MAX_BODY_CHARS = 8000
BODY_SELECTORS = (
    "#dic_area",                   # 네이버 뉴스
    "#article-view-content-div",   # NDSoft CMS (다수 의약 전문지)
    "#articleBody",
    "#article_body",
    "#articleBodyContents",
    "#news_body_area",
    "div.article_body",
    "div.article-body",
    "div.news_body",
    "div.view_con",
    "#newsEndContents",
    "article",
)
STRIP_TAGS = ("script", "style", "noscript", "iframe", "header", "footer", "nav",
              "aside", "form", "figure", "figcaption", "button")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


@dataclass(frozen=True)
class ArticleBody:
    status: str        # ok | too_short | failed
    text: str
    source_url: str
    error: str = ""


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_body_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(list(STRIP_TAGS)):
        tag.decompose()
    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if node is not None:
            text = _normalize_ws(node.get_text(" "))
            if len(text) >= MIN_BODY_CHARS:
                return text[:MAX_BODY_CHARS]
    # 폴백: 직계 <p> 텍스트가 가장 긴 부모 블록
    best = ""
    seen: set[int] = set()
    for p in soup.find_all("p"):
        parent = p.parent
        if parent is None or id(parent) in seen:
            continue
        seen.add(id(parent))
        text = _normalize_ws(" ".join(c.get_text(" ") for c in parent.find_all("p", recursive=False)))
        if len(text) > len(best):
            best = text
    if best:
        return best[:MAX_BODY_CHARS]
    # 마지막 폴백: 선택자에 걸렸지만 짧은 본문
    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if node is not None:
            return _normalize_ws(node.get_text(" "))[:MAX_BODY_CHARS]
    return ""


def _default_fetch(url: str, timeout: float) -> str:
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return resp.text


def candidate_urls(publisher_url: str, naver_url: str = "") -> list[str]:
    ordered: list[str] = []
    if naver_url and "n.news.naver.com" in naver_url:
        ordered.append(naver_url)
    for url in (publisher_url, naver_url):
        if url and url not in ordered:
            ordered.append(url)
    return ordered


def fetch_article_body(
    publisher_url: str,
    naver_url: str = "",
    *,
    fetch: Callable[[str, float], str] | None = None,
    timeout: float = 10.0,
) -> ArticleBody:
    fetch = fetch or _default_fetch
    last_error = ""
    best_text, best_url = "", ""
    for url in candidate_urls(publisher_url, naver_url):
        try:
            text = extract_body_text(fetch(url, timeout))
        except Exception as exc:  # 네트워크/파싱 실패는 다음 URL 로
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            continue
        if len(text) >= MIN_BODY_CHARS:
            return ArticleBody(status="ok", text=text, source_url=url)
        if len(text) > len(best_text):
            best_text, best_url = text, url
    if best_text:
        return ArticleBody(status="too_short", text=best_text, source_url=best_url)
    return ArticleBody(status="failed", text="", source_url="", error=last_error)
