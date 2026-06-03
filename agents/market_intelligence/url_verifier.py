"""URL 기반 published_at 검증 — Perplexity hallucination 방지.

문제 (2026-04-25 발견):
  Perplexity 가 "자누비아 9.4% 약가 인하" 키워드로 2010 년 메디칼타임즈 ID=95928,
  메디포뉴스 no=67723 기사를 가져왔으나 published_at 을 사용자 query 에 맞춰
  "2024.09.30" 으로 위조. enforce_rules 의 윈도우 필터가 이를 통과시켜
  잘못된 mechanism 분류로 이어짐.

방어:
  1) URL ID 휴리스틱 — 매체별 ID 임계값으로 suspicious reference 식별 (cheap)
  2) URL fetch 검증 — 의심·top-N refs 의 실제 게재일 추출, 불일치 시 교체
"""
from __future__ import annotations

import logging
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# URL ID 추출 — 매체별 패턴
# ──────────────────────────────────────────────────────────────────────────
_URL_ID_PATTERNS: dict[str, re.Pattern] = {
    "medicaltimes.com": re.compile(r"NewsView\.html\?ID=(\d+)"),
    "medifonews.com":   re.compile(r"article\.html\?no=(\d+)"),
    "dailypharm.com":   re.compile(r"NewsView\.html\?ID=(\d+)|user/news/(\d+)"),
    "hitnews.co.kr":    re.compile(r"articleView\.html\?idxno=(\d+)"),
    "medipana.com":     re.compile(r"articleView\.html\?idxno=(\d+)"),
    "yakup.com":        re.compile(r"\?nid=(\d+)|/(\d+)\.html"),
    "pharmnews.com":    re.compile(r"articleView\.html\?idxno=(\d+)"),
    "medisobizanews.com": re.compile(r"articleView\.html\?idxno=(\d+)"),
    "newsthevoice.com": re.compile(r"articleView\.html\?idxno=(\d+)"),
    "kpanews.co.kr":    re.compile(r"articleView\.html\?idxno=(\d+)|idx=(\d+)"),
}

# 매체별 (claimed_year, min_id) — claimed_year 이상 기사이려면 ID >= min_id 여야 함.
# 임계값은 각 매체의 실제 ID 진행 추이에서 calibration (보수적으로 낮게 설정).
# 신규 매체 추가 시 ID 발행 추이 1~2건 sampling 후 보정.
_ID_YEAR_THRESHOLD: dict[str, list[tuple[int, int]]] = {
    "medicaltimes.com":  [(2020, 700_000),  (2024, 1_000_000), (2026, 1_150_000)],
    "medifonews.com":    [(2020, 100_000),  (2024, 130_000),   (2026, 145_000)],
    "hitnews.co.kr":     [(2020, 30_000),   (2024, 50_000),    (2026, 60_000)],
    "medipana.com":      [(2020, 200_000),  (2024, 230_000),   (2026, 400_000)],
    "dailypharm.com":    [(2020, 250_000),  (2024, 300_000),   (2026, 330_000)],
    "yakup.com":         [(2020, 250_000),  (2024, 290_000),   (2026, 310_000)],
    "pharmnews.com":     [(2020, 200_000),  (2024, 230_000),   (2026, 245_000)],
}


def _extract_url_id(url: str) -> tuple[str, int] | None:
    """URL → (domain, article_id) 또는 None."""
    if not url:
        return None
    url_lower = url.lower()
    for domain, pattern in _URL_ID_PATTERNS.items():
        if domain in url_lower:
            m = pattern.search(url)
            if not m:
                continue
            for g in m.groups():
                if g:
                    try:
                        return domain, int(g)
                    except ValueError:
                        return None
    return None


def url_id_suspicious(url: str, claimed_year: int) -> tuple[bool, str | None]:
    """URL ID 가 claimed_year 와 일관성 있는지 판정.

    Returns: (suspicious, reason_str)
    """
    parsed = _extract_url_id(url)
    if not parsed:
        return False, None
    domain, art_id = parsed
    thresholds = _ID_YEAR_THRESHOLD.get(domain) or []
    for min_year, min_id in thresholds:
        if claimed_year >= min_year and art_id < min_id:
            return True, (
                f"{domain} ID={art_id} 는 {min_year}+ 기사 임계값 (>= {min_id}) 미달. "
                f"claimed year {claimed_year} 와 불일치 (오래된 기사를 신규로 위조 가능성)"
            )
    return False, None


# ──────────────────────────────────────────────────────────────────────────
# URL fetch — 실제 published_at 추출
# ──────────────────────────────────────────────────────────────────────────
_DATE_META_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']'),
    re.compile(r'<meta[^>]+name=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']'),
    re.compile(r'<meta[^>]+name=["\']pub[Dd]ate["\'][^>]+content=["\']([^"\']+)["\']'),
    re.compile(r'<meta[^>]+name=["\']date["\'][^>]+content=["\']([^"\']+)["\']'),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']'),
]
# JSON 임베딩 패턴 — medicaltimes 등은 본문 내 JS object 에 "Publish_date":"YYYY-MM-DD HH:MM:SS"
_DATE_JSON_PATTERNS = [
    re.compile(r'["\']Publish_date["\']\s*:\s*["\'](\d{4}-\d{2}-\d{2})'),
    re.compile(r'["\']publishedAt["\']\s*:\s*["\'](\d{4}-\d{2}-\d{2})'),
    re.compile(r'["\']write_date["\']\s*:\s*["\'](\d{4}-\d{2}-\d{2})', re.IGNORECASE),
    re.compile(r'["\']Write_Date["\']\s*:\s*["\'](\d{4}-\d{2}-\d{2})'),
]
_DATE_BODY_PATTERNS = [
    # K4M CMS 공통: "<em class='info'>...2010-09-30 10:59</em>" 등
    re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)'),
    # YYYY.MM.DD HH:MM
    re.compile(r'(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2})'),
    # 단순 YYYY-MM-DD or YYYY.MM.DD (마지막 fallback)
    re.compile(r'(\d{4})[-./](\d{2})[-./](\d{2})'),
]


def _normalize_date(raw: str) -> str | None:
    """다양한 표기 → YYYY-MM-DD."""
    if not raw:
        return None
    m = re.match(r"(\d{4})[-./](\d{2})[-./](\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def fetch_published_at(url: str, timeout: int = 8) -> str | None:
    """URL fetch → published_at 추출. 실패 시 None.

    1) <meta property=article:published_time> 우선 (대부분 매체 보유)
    2) <time datetime=> 보조
    3) 본문 'YYYY-MM-DD HH:MM' 패턴 fallback
    """
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # medicaltimes 등은 본문 200KB+ 위치에 JSON 임베딩 (Publish_date) 가 있음 → 500KB read.
            html = r.read(500_000).decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("[URL verify] fetch 실패 %s: %s", url[:60], e)
        return None

    # 1) meta 태그 우선
    for pat in _DATE_META_PATTERNS:
        m = pat.search(html)
        if m:
            iso = _normalize_date(m.group(1))
            if iso:
                return iso

    # 2) JSON 임베딩 (medicaltimes Publish_date 등)
    for pat in _DATE_JSON_PATTERNS:
        m = pat.search(html)
        if m:
            iso = _normalize_date(m.group(1))
            if iso:
                return iso

    # 3) 본문 패턴 (HTML body 부분만 — head 의 schema.org JSON 제외)
    body_start = html.find("<body")
    body_html = html[body_start:] if body_start >= 0 else html
    for pat in _DATE_BODY_PATTERNS:
        m = pat.search(body_html)
        if not m:
            continue
        if len(m.groups()) == 3:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        raw = m.group(1)
        iso = _normalize_date(raw)
        if iso:
            return iso
    return None


# ──────────────────────────────────────────────────────────────────────────
# 통합 진입점
# ──────────────────────────────────────────────────────────────────────────
def verify_references(refs: list[dict], top_n: int = 6, max_fetch: int = 10) -> dict:
    """references 의 published_at 검증.

    Strategy:
      1) ID 휴리스틱 — claimed year 와 URL ID 임계값 비교, 의심 마킹
      2) URL fetch — suspicious + top weight refs 의 실제 게재일 추출
      3) 불일치 시 published_at 교체 + `date_corrected=True` 마킹
      4) ID 의심 했는데 fetch 실패 → 보수적으로 reference 제거 표시 (`drop=True`)

    Returns: {"corrected": N, "suspicious": N, "dropped": N, "log": [...]}
    """
    if not refs:
        return {"corrected": 0, "suspicious": 0, "dropped": 0, "log": []}

    log: list[str] = []
    suspicious_idx: list[int] = []

    # 1) ID 휴리스틱
    for i, r in enumerate(refs):
        pub = (r.get("published_at") or "").strip()
        url = r.get("url") or ""
        if not pub or not url:
            continue
        try:
            year = int(pub[:4])
        except ValueError:
            continue
        suspicious, reason = url_id_suspicious(url, year)
        if suspicious:
            r["id_suspicious"] = True
            r["id_suspicious_reason"] = reason
            suspicious_idx.append(i)
            log.append(f"id-heuristic suspicious: ref[{i}] — {reason[:80]}")

    # 2) Top-N (weight 기준) + suspicious 합쳐서 fetch, max_fetch 로 캡
    sorted_idx = sorted(range(len(refs)), key=lambda i: -(refs[i].get("weight") or 0))
    top_set = sorted_idx[:top_n]
    indices_to_fetch = list(dict.fromkeys(suspicious_idx + top_set))[:max_fetch]

    def _verify_one(i: int) -> tuple[int, str | None]:
        url = refs[i].get("url") or ""
        return i, fetch_published_at(url)

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(_verify_one, indices_to_fetch))

    corrected = 0
    dropped = 0
    for i, real_pub in results:
        r = refs[i]
        claimed = (r.get("published_at") or "").strip()
        if real_pub is None:
            # fetch 실패 — suspicious 였으면 안전상 drop 표시 (호출자가 실제 제거 결정)
            if r.get("id_suspicious"):
                r["drop"] = True
                r["drop_reason"] = "id_suspicious + fetch 실패 — 검증 불가"
                dropped += 1
                log.append(f"drop: ref[{i}] {r.get('url','')[:60]} — id 의심 + fetch 실패")
            else:
                r["fetch_verified"] = False
            continue

        r["fetch_verified"] = True

        # 정규화 비교 (YYYY-MM-DD)
        claimed_iso = _normalize_date(claimed[:10].replace(".", "-"))
        real_iso = real_pub[:10]

        if claimed_iso and claimed_iso != real_iso:
            r["published_at_claimed"] = claimed
            r["published_at"] = real_iso
            r["date_corrected"] = True
            corrected += 1
            log.append(f"corrected: ref[{i}] {claimed_iso} → {real_iso}")
        elif not claimed_iso:
            # claimed 가 빈/이상한 형식 — real_pub 으로 채움
            r["published_at"] = real_iso
            r["date_corrected"] = True
            corrected += 1

    return {
        "corrected": corrected,
        "suspicious": len(suspicious_idx),
        "dropped": dropped,
        "log": log,
    }
