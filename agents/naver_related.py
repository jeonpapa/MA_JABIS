"""Naver 연관검색어 확장 — Home 브랜드 목록(home_brand) 자동 후보 제안.

**신규 자격증명 불필요**: 기본 경로는 Naver 자동완성(autocomplete) 공개 엔드포인트
(`ac.search.naver.com`) 로, 로그인/API 키 없이 누구나 호출 가능한 JSONP-유사 응답을
반환한다. `config/.env` 에 `NAVER_SEARCHAD_API_KEY`/`NAVER_SEARCHAD_SECRET`/
`NAVER_SEARCHAD_CUSTOMER_ID` 3종이 모두 설정된 경우에만 네이버 검색광고
(SearchAd) `relKwdStat` API 로 업그레이드해 검색량 기반 연관키워드를 시도한다 —
없어도 autocomplete 경로만으로 정상 동작한다.

제안된 후보는 `agents.editable_factors.add_related_candidates()` 를 통해
`home_brand` 테이블에 `source='related', active=0` 으로 들어가며, admin 이
검토 후 활성화(active=1)해야 Home 브랜드 트래픽 집계에 반영된다.
"""
from __future__ import annotations

import hashlib
import hmac
import base64
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
_env_path = BASE_DIR / "config" / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=False)
    except ImportError:
        pass

logger = logging.getLogger(__name__)

AUTOCOMPLETE_URL = "https://ac.search.naver.com/nx/ac"
SEARCHAD_URL = "https://api.searchad.naver.com"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 연관검색어 필터링에서 배제할 일반 stopword (브랜드/약제명일 가능성 낮음)
_STOPWORDS = {
    "가격", "부작용", "효능", "복용법", "후기", "가격표", "보험", "적용",
    "급여", "비급여", "처방", "성분", "제조사", "회사", "주가", "뉴스",
}


def _urlopen(req, timeout: int = 10):
    """urllib.request.urlopen 얇은 래퍼 — 테스트에서 monkeypatch 하기 위한 진입점."""
    return urllib.request.urlopen(req, timeout=timeout)


def _fetch_autocomplete(seed: str, timeout: int = 10) -> list[str]:
    """Naver 자동완성 엔드포인트 호출 — 크레덴셜 불필요. 실패 시 [] 반환."""
    params = {
        "q": seed,
        "con": "1",
        "frm": "nv",
        "ans": "2",
        "r_format": "json",
        "st": "100",
    }
    url = f"{AUTOCOMPLETE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with _urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        data = json.loads(raw)
    except Exception as e:
        logger.warning("[naver_related] autocomplete fetch 실패(%s): %s", seed, e)
        return []

    suggestions: list[str] = []
    try:
        # 응답 형식: {"query": "...", "items": [[[term, ...], [term, ...], ...]]}
        groups = data.get("items") or []
        for group in groups:
            for entry in group:
                if not entry:
                    continue
                term = entry[0] if isinstance(entry, list) else entry
                if isinstance(term, str) and term.strip():
                    suggestions.append(term.strip())
    except Exception as e:
        logger.warning("[naver_related] autocomplete 응답 파싱 실패(%s): %s", seed, e)
        return []
    return suggestions


def _searchad_signature(timestamp: str, method: str, uri: str, secret: str) -> str:
    message = f"{timestamp}.{method}.{uri}"
    hashed = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(hashed.digest()).decode("utf-8")


def _fetch_searchad_related(seed: str, timeout: int = 10) -> list[str]:
    """네이버 검색광고 relKwdStat API — 3개 크레덴셜 모두 설정된 경우에만 시도.
    실패/미설정 시 [] (autocomplete 경로로 폴백하는 쪽은 호출자 책임)."""
    api_key = os.getenv("NAVER_SEARCHAD_API_KEY")
    secret = os.getenv("NAVER_SEARCHAD_SECRET")
    customer_id = os.getenv("NAVER_SEARCHAD_CUSTOMER_ID")
    if not (api_key and secret and customer_id):
        return []

    uri = "/keywordstool"
    method = "GET"
    timestamp = str(int(time.time() * 1000))
    try:
        signature = _searchad_signature(timestamp, method, uri, secret)
        params = {"hintKeywords": seed, "showDetail": "1"}
        url = f"{SEARCHAD_URL}{uri}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "X-Timestamp": timestamp,
                "X-API-KEY": api_key,
                "X-Customer": str(customer_id),
                "X-Signature": signature,
                "User-Agent": _USER_AGENT,
            },
        )
        with _urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        data = json.loads(raw)
        rows = data.get("keywordList") or []
        return [r["relKeyword"] for r in rows if r.get("relKeyword")]
    except Exception as e:
        logger.warning("[naver_related] SearchAd relKwdStat 실패(%s): %s", seed, e)
        return []


def related_keywords(seed: str, limit: int = 10) -> list[str]:
    """`seed` 브랜드에 대한 연관검색어 목록 (최대 `limit`개).

    SearchAd 크레덴셜 3종이 모두 있으면 우선 시도하고, 없거나 실패하면 항상
    autocomplete 경로로 폴백한다. 두 경로 모두 실패하면 [] (예외를 던지지 않음).
    """
    if not seed or not seed.strip():
        return []

    results = _fetch_searchad_related(seed, timeout=10)
    if not results:
        results = _fetch_autocomplete(seed, timeout=10)

    # dedupe, seed 자기 자신 제외, 순서 보존
    seen = set()
    deduped = []
    for term in results:
        if term == seed or term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped[:limit]


def _looks_like_brand(term: str, seed: str) -> bool:
    """연관검색어 → 약제/브랜드명일 가능성 휴리스틱 필터."""
    if not term or term == seed:
        return False
    if not (2 <= len(term) <= 20):
        return False
    if term in _STOPWORDS:
        return False
    # "브랜드 + 접미어" 형태(예: "키트루다 부작용")는 브랜드명이 아니라 검색 질의이므로 배제
    for stop in _STOPWORDS:
        if term.startswith(f"{seed} ") and stop in term:
            return False
    return True


def expand_home_brands(
    db_path=None,
    seeds: list[str] | None = None,
    per_seed: int = 8,
) -> dict:
    """활성 home_brand(또는 지정된 seeds) 각각에 대해 연관검색어를 조회, 브랜드일법한
    후보만 필터링해 home_brand 에 source='related', active=0 **대기 큐** 로 추가 제안.
    (승인 시 독립 브랜드가 아니라 시드의 related_terms_json 보조 검색어로 편입된다.)

    Returns: {"seeds_processed": int, "candidates_added": int}
    """
    from agents.editable_factors import (
        add_related_candidates,
        get_home_brand_groups,
        get_home_brands,
    )

    if seeds is None:
        seeds = get_home_brands(db_path)

    # 시드 브랜드 + 이미 승인된 보조 검색어(related_terms) 모두 제외 — 승인된 검색어가
    # 후보로 무한 재제안되는 것을 방지.
    existing = {b.lower() for b in get_home_brands(db_path)}
    for g in get_home_brand_groups(db_path):
        existing.update(t.lower() for t in (g.get("terms") or []))
    candidates: list[dict] = []
    seen_this_run: set[str] = set()

    for seed in seeds:
        try:
            terms = related_keywords(seed, limit=per_seed)
        except Exception as e:
            logger.warning("[naver_related] expand_home_brands seed=%s 실패: %s", seed, e)
            continue
        for term in terms:
            if not _looks_like_brand(term, seed):
                continue
            key = term.lower()
            if key in existing or key in seen_this_run:
                continue
            seen_this_run.add(key)
            candidates.append({"brand": term, "related_from": seed})

    added = add_related_candidates(db_path, candidates) if candidates else 0
    return {"seeds_processed": len(seeds), "candidates_added": added}
