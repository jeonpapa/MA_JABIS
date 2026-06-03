"""Naver 뉴스 검색 — 한국 의약 기사 수집.

- sort=1 (최신순)
- URL 도메인 우선 매체 판별, Naver 표시 매체명 폴백
- published_at (YYYY.MM.DD) 추출 → window enforcement 용
"""
from __future__ import annotations

import html as _html
import logging
import re
from datetime import datetime

import requests

from .media import score_source


def _html_unescape(s: str) -> str:
    """`&quot;` 등 HTML entity → 원문 텍스트."""
    if not s:
        return ""
    return _html.unescape(s)

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

# Tier A 매체 중 쿼리 타게팅 상위 N개 (collect_news 에서 `{brand} 특허 <매체>` 쿼리용)
_TOP_TIER_A_FOR_PATENT_QUERY = 3

# Naver 검색결과 HTML (sds-comps-* 2026 UI) 파싱용 regex — 모듈 로드 시 1회 컴파일
_HEAD_RE = re.compile(
    r'<a[^>]+href="(?P<url>https?://[^"]+)"[^>]*>\s*'
    r'<span[^>]+sds-comps-text-type-headline[^"]*"[^>]*>(?P<title>.*?)</span>',
    re.DOTALL,
)
_DATE_RE = re.compile(
    r'<span class="[^"]*sds-comps-text-type-body2[^"]*"[^>]*>'
    r'(?:<div[^>]*>)?<span[^>]*>(\d{4}\.\d{2}\.\d{2})\.?</span>',
)
_SNIP_RE = re.compile(
    r'<span[^>]+sds-comps-text-type-body1[^"]*"[^>]*>(.*?)</span>',
    re.DOTALL,
)


def naver_search(
    query: str,
    max_results: int = 8,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list:
    """Naver 뉴스 HTML 검색 → [{title, url, snippet, media_name, weight, tier, published_at}].

    2026 년 Naver 신규 UI (`sds-comps-*` 클래스 기반) 대응:
    - headline: `sds-comps-text-type-headline` 내 text + 상위 <a href>
    - published: `sds-comps-text-type-body2` 내 `YYYY.MM.DD` 패턴
    - snippet: headline 다음 형제 `sds-comps-text-type-body1`

    Args:
        date_from/to: "YYYY.MM.DD" — 지정 시 Naver `nso` 날짜 필터 적용 (윈도우 검색).
                     최신순 sort 로는 2023 이전 기사를 찾기 어려우므로 변동일 윈도우 검색 필수.
    """
    from urllib.parse import urlencode
    params = {"where": "news", "query": query, "sort": "1"}
    if date_from and date_to:
        # ex. 2023.09.01 → 20230901
        ds_compact = date_from.replace(".", "").replace("-", "")
        de_compact = date_to.replace(".", "").replace("-", "")
        params.update({
            "pd":  "3",
            "ds":  date_from,
            "de":  date_to,
            "nso": f"so:r,p:from{ds_compact}to{de_compact},a:all",
        })
    search_url = "https://search.naver.com/search.naver?" + urlencode(params)

    try:
        resp = requests.get(search_url, headers=_NAVER_HEADERS, timeout=12)
        if resp.status_code != 200:
            logger.warning("Naver 검색 HTTP %d (쿼리: %s)", resp.status_code, query[:40])
            return []

        html = resp.text

        heads = list(_HEAD_RE.finditer(html))
        dates = _DATE_RE.findall(html)
        snippets_raw = [re.sub(r"<[^>]+>", "", m.group(1)).strip()[:300]
                        for m in _SNIP_RE.finditer(html)]

        results: list = []
        seen: set = set()

        for i, m in enumerate(heads):
            url = m.group("url")
            title = _html_unescape(re.sub(r"<[^>]+>", "", m.group("title")).strip())
            if not title or url in seen:
                continue
            # sports/shopping 등 비뉴스 링크 제외
            if "news.naver" in url and "article" not in url:
                continue
            seen.add(url)

            score = score_source(url)
            # Naver 내부 링크(n.news 등) 는 도메인으로 판별 불가 → press_name 기반 폴백 생략
            # (2026 신규 UI 에서 press_name 노출이 선택적이라 정확도 저하)

            results.append({
                "title":         title,
                "url":           url,
                "snippet":       snippets_raw[i] if i < len(snippets_raw) else "",
                "media_name":    score["media_name"],
                "weight":        score["weight"],
                "tier":          score["tier"],
                "published_at":  dates[i] if i < len(dates) else "",
            })
            if len(results) >= max_results:
                break

        logger.info("Naver 뉴스: %d건 수집 (쿼리: %s)", len(results), query[:50])
        return results

    except Exception as e:
        logger.warning("Naver 검색 실패 (%s): %s", query[:40], e)
        return []


def collect_news(drug_ko: str, ingredient_ko: str, change_date: datetime) -> list:
    """약제명/성분명 + 약가 키워드 + **전문지 매체명 타게팅** 으로 다각도 검색.

    변동일 ±6개월 윈도우에 집중:
    - Naver nso 파라미터로 날짜 범위 엄격 제한
    - 쿼리 풀: 일반(4대 기전) + 전문지명 타게팅 (데일리팜·메디파나·히트뉴스 등)
    - 수집 후 published_at 기반 윈도우 외 기사 제거 + weight 내림차순 정렬
    """
    from .media import MEDIA_DB

    short_ing  = (ingredient_ko or drug_ko).split(",")[0].strip()
    # 브랜드 추출: "자누비아정100밀리그램" → "자누비아" (숫자·함량·제형 suffix 전부 제거).
    brand_base = re.sub(
        r"(정|주|캡슐|액|주사|시럽|서방정|필름코팅정)?"
        r"\s*\d[\d./]*\s*(mg|밀리그램|㎎|g|그램|㎍|ug|mcg|mL|밀리리터)?.*$",
        "",
        drug_ko,
    ).strip() or drug_ko
    brand_base = re.sub(r"(주|정|캡슐|액|주사|시럽)$", "", brand_base).strip() or brand_base

    # 변동일 중심 ±6개월 윈도우
    from datetime import timedelta
    wf = change_date - timedelta(days=180)
    wt = change_date + timedelta(days=180)
    ds = wf.strftime("%Y.%m.%d")
    de = wt.strftime("%Y.%m.%d")

    # Tier A·B 전문지 이름 추출 (매체 쿼리 타게팅용)
    trade_press_names = [
        name for name, info in MEDIA_DB.items()
        if info["tier"] in ("A", "B")
    ]

    # 쿼리 풀:
    #   1) 일반 기전 쿼리 — 전체 매체 대상
    #   2) 전문지명 명시 쿼리 — Tier A/B 매체별 타게팅 (Naver 가 매체명 포함 시 해당 매체 가중치 상승)
    # Naver 날짜 필터(ds/de) 로 ±6개월 엄격 제한. 2026년 최신순 기사 제외.
    general_queries = [
        f"{brand_base} 특허 만료",            # 특허 기전
        f"{brand_base} 제네릭 등재",          # 특허/제네릭
        f"{short_ing} 특허 만료",             # 성분 기준
        f"{brand_base} 약가 인하",            # 일반
        f"{brand_base} 사용량 연동 협상",     # 사용량-약가
        f"{brand_base} 적응증 확대",          # 적응증
        f"{brand_base} 약제급여평가위원회",   # 평가 고시
        f"{brand_base} 실거래가",             # 실거래가 연동
        f"{short_ing} 급여 확대",
    ]
    # 핵심 Tier A 매체별 직접 타게팅 쿼리 (데일리팜·약업신문·메디파나·히트뉴스)
    tier_a_names = [n for n in trade_press_names if MEDIA_DB[n]["tier"] == "A"]
    targeted_queries = [
        f"{brand_base} 약가 {media_name}"
        for media_name in tier_a_names
    ] + [
        f"{brand_base} 특허 {media_name}"
        for media_name in tier_a_names[:_TOP_TIER_A_FOR_PATENT_QUERY]
    ]
    queries = general_queries + targeted_queries

    # 쿼리 병렬 실행 — 17 쿼리 직렬 ≈ 24s → ~3s. max_workers=3 은 Naver rate-limit 403 회피 타협점
    # (4 이상: 30% 403 / 3: 10% 미만).
    from concurrent.futures import ThreadPoolExecutor
    articles: list = []
    seen_urls: set = set()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = [ex.submit(naver_search, q, 6, ds, de) for q in queries]
        for f in futures:
            for r in f.result():
                if r["url"] not in seen_urls:
                    seen_urls.add(r["url"])
                    articles.append(r)

    # published_at 기준 윈도우 외 기사 제거 (± 6개월)
    filtered = []
    dropped = 0
    for a in articles:
        pub = (a.get("published_at") or "").strip()
        if not pub:
            filtered.append(a)  # 날짜 모르면 일단 유지 (enforce_rules 단계에서 date_unknown 표시)
            continue
        try:
            pd = datetime.strptime(pub[:10].replace("-", "."), "%Y.%m.%d")
        except Exception:
            filtered.append(a)
            continue
        if wf <= pd <= wt:
            filtered.append(a)
        else:
            dropped += 1

    # weight 내림차순 정렬 — 전문지(Tier A·B) 기사를 상위로
    filtered.sort(key=lambda a: -(a.get("weight") or 0))

    # 매체 분포 집계 로그
    tier_counts: dict = {}
    for a in filtered:
        t = a.get("tier", "other")
        tier_counts[t] = tier_counts.get(t, 0) + 1
    logger.info(
        "[MI Agent] 수집 기사: %d건 (윈도우 %s~%s, 외부 %d건 제외) · tier A=%d B=%d C=%d other=%d",
        len(filtered),
        wf.strftime("%Y-%m-%d"), wt.strftime("%Y-%m-%d"), dropped,
        tier_counts.get("A", 0), tier_counts.get("B", 0),
        tier_counts.get("C", 0), tier_counts.get("other", 0),
    )
    return filtered
