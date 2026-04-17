"""Naver 뉴스 검색 — 한국 의약 기사 수집.

- sort=1 (최신순)
- URL 도메인 우선 매체 판별, Naver 표시 매체명 폴백
- published_at (YYYY.MM.DD) 추출 → window enforcement 용
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import requests

from .media import MEDIA_DB, score_source

logger = logging.getLogger(__name__)


_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://search.naver.com/",
}

_PRESS_NAME_MAP = {
    "데일리팜":           "데일리팜",
    "약업신문":           "약업신문",
    "메디파나뉴스":       "메디파나뉴스",
    "히트뉴스":           "히트뉴스",
    "청년의사":           "청년의사",
    "메디칼타임즈":       "메디칼타임즈",
    "뉴스더보이스":       "뉴스더보이스",
    "팜뉴스":             "팜뉴스",
    "메디게이트뉴스":     "메디게이트뉴스",
    "보건복지부":         "보건복지부",
    "건강보험심사평가원": "건강보험심사평가원",
    "국민건강보험공단":   "국민건강보험공단",
    "헬스조선":           "헬스조선",
    "헬스경향":           "헬스경향",
    "메디컬월드뉴스":     "메디컬월드뉴스",
}


def naver_search(query: str, max_results: int = 8) -> list:
    """Naver 뉴스 HTML 검색 → [{title, url, snippet, media_name, weight, tier, published_at}]."""
    from urllib.parse import urlencode
    params = {"where": "news", "query": query, "sort": "1"}
    search_url = "https://search.naver.com/search.naver?" + urlencode(params)

    try:
        resp = requests.get(search_url, headers=_NAVER_HEADERS, timeout=12)
        if resp.status_code != 200:
            logger.warning("Naver 검색 HTTP %d (쿼리: %s)", resp.status_code, query[:40])
            return []

        html = resp.text
        results: list = []
        seen: set = set()

        tit_re = re.compile(
            r'<a[^>]+class="[^"]*news_tit[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        press_re = re.compile(
            r'<a[^>]+class="[^"]*info press[^"]*"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snip_re = re.compile(
            r'<div[^>]+class="[^"]*dsc_txt[^"]*"[^>]*>(.*?)</div>',
            re.DOTALL,
        )
        # 상대 표현 ("3일 전") 무시 — window enforcement 에 부적합
        date_re = re.compile(
            r'<span[^>]+class="[^"]*\binfo\b(?![^"]*press)[^"]*"[^>]*>\s*'
            r'(\d{4}\.\d{2}\.\d{2})\.?\s*</span>'
        )
        pub_dates = [m.group(1) for m in date_re.finditer(html)]

        titles_urls = [
            (m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip())
            for m in tit_re.finditer(html)
        ]
        press_names = [
            re.sub(r"<[^>]+>", "", m.group(1)).strip()
            for m in press_re.finditer(html)
        ]
        snippets = [
            re.sub(r"<[^>]+>", "", m.group(1)).strip()[:250]
            for m in snip_re.finditer(html)
        ]

        for i, (url, title) in enumerate(titles_urls):
            if not title or url in seen:
                continue
            seen.add(url)

            score = score_source(url)
            if score["tier"] == "other" and i < len(press_names):
                media_key = _PRESS_NAME_MAP.get(press_names[i])
                if media_key and media_key in MEDIA_DB:
                    m_info = MEDIA_DB[media_key]
                    score = {
                        "media_name": media_key,
                        "weight": m_info["weight"],
                        "tier": m_info["tier"],
                        "desc": m_info["desc"],
                    }

            results.append({
                "title":         title,
                "url":           url,
                "snippet":       snippets[i] if i < len(snippets) else "",
                "media_name":    score["media_name"],
                "weight":        score["weight"],
                "tier":          score["tier"],
                "published_at":  pub_dates[i] if i < len(pub_dates) else "",
            })
            if len(results) >= max_results:
                break

        logger.info("Naver 뉴스: %d건 수집 (쿼리: %s)", len(results), query[:50])
        return results

    except Exception as e:
        logger.warning("Naver 검색 실패 (%s): %s", query[:40], e)
        return []


def collect_news(drug_ko: str, ingredient_ko: str, change_date: datetime) -> list:
    """약제명/성분명 + 약가 키워드 조합으로 다각도 검색."""
    short_ing  = (ingredient_ko or drug_ko).split(",")[0].strip()
    brand_base = re.sub(r"(주|정|캡슐|액|주사|시럽)$", "", drug_ko).strip()
    year       = change_date.year

    queries = [
        f"{brand_base} 약가 인하 {year}",
        f"{brand_base} 보험급여 {year}",
        f"{short_ing} 약가 {year}",
        f"{brand_base} 적응증 확대 {year}",
        f"{brand_base} 특허 만료 {year}",
        f"{brand_base} 사용량 연동 {year}",
        f"{short_ing} 급여 확대 {year}",
        f"{brand_base} 약가 협상 {year}",
    ]

    articles: list = []
    seen_urls: set = set()

    for q in queries:
        for r in naver_search(q, max_results=6):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                articles.append(r)
        time.sleep(0.5)

    logger.info("[MI Agent] 수집 기사: %d건 (고유 URL)", len(articles))
    return articles
