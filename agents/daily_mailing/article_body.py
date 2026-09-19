"""기사 본문 추출 — Daily Mailing 요약·인용 검증의 근거.

설계 §5 ④. 네이버 뉴스(n.news.naver.com)는 `#dic_area` 구조가 일정하므로 우선 시도하고,
언론사 원문은 알려진 본문 선택자 → `<p>` 밀도 폴백 순으로 추출한다.
결과 status: ok(≥MIN_BODY_CHARS) / too_short / failed.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

MIN_BODY_CHARS = 200
MAX_BODY_CHARS = 8000
MAX_BODY_BYTES = 3_000_000          # 원문 응답 읽기 상한 (스트리밍, 초과분은 버림)
ALLOWED_SCHEMES = ("http", "https")
_CHUNK_BYTES = 65536
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


def check_url_allowed(url: str) -> str:
    """원문 fetch 허용 검사. 허용이면 '' , 아니면 사유. DNS 조회는 하지 않는다 (IP 리터럴만 검사).

    - http/https 만
    - localhost, 사설·루프백·링크로컬·예약·멀티캐스트·미지정 IP 리터럴 차단 (SSRF 방지)
    """
    try:
        parsed = urlparse((url or "").strip())
    except ValueError as exc:
        return f"invalid url: {exc}"
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        return f"scheme not allowed: {scheme or '(none)'}"
    try:
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        return f"invalid host: {exc}"
    if not host:
        return "missing host"
    if host == "localhost" or host.endswith(".localhost"):
        return "host not allowed: localhost"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return ""   # 일반 호스트명
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        return f"host not allowed: {host}"
    return ""


_http_get = requests.get   # 테스트에서 교체 가능한 HTTP GET 진입점


def _detect_encoding(data: bytes) -> str:
    """헤더에 charset 이 없거나 iso-8859-1(requests 기본값)일 때 본문 바이트로 추정 (apparent_encoding 과 동일 기전).
    스트리밍으로 읽었으므로 Response.apparent_encoding 대신 우리가 읽은 바이트에 직접 적용한다."""
    detector = getattr(requests.compat, "chardet", None)
    if detector is not None:
        try:
            guess = (detector.detect(data) or {}).get("encoding")
            if guess:
                return str(guess)
        except Exception:
            pass
    return "utf-8"


def _default_fetch(url: str, timeout: float) -> str:
    reason = check_url_allowed(url)
    if reason:
        raise ValueError(f"unsafe url: {reason}")
    resp = _http_get(url, headers={"User-Agent": _UA}, timeout=timeout, stream=True)
    try:
        resp.raise_for_status()
        length = str(resp.headers.get("Content-Length") or "").strip()
        if length.isdigit() and int(length) > MAX_BODY_BYTES:
            raise ValueError(f"content too large: {length} bytes > {MAX_BODY_BYTES}")
        content_type = str(resp.headers.get("Content-Type") or "").lower()
        if content_type and "html" not in content_type:
            raise ValueError(f"unsupported content-type: {content_type}")
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=_CHUNK_BYTES):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_BODY_BYTES:
                break
        data = b"".join(chunks)[:MAX_BODY_BYTES]
    finally:
        resp.close()
    encoding = resp.encoding
    if not encoding or str(encoding).lower() == "iso-8859-1":
        encoding = _detect_encoding(data)
    try:
        return data.decode(encoding, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


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
        reason = check_url_allowed(url)
        if reason:   # 주입된 fetch 라도 비허용 URL 은 호출하지 않는다
            last_error = f"unsafe url: {reason}"[:200]
            continue
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
