"""
독일 Gelbe Liste 약가 스크레이퍼

대상: https://www.gelbe-liste.de/
로그인: 불필요 (공개 데이터)
법적 근거: 독일 AMPreisV(약가규정)에 따라 모든 처방약의 AVP(Apothekenverkaufspreis)는
           법정 고시가이며 공개 의무가 있음.

조회 흐름:
  1) https://www.gelbe-liste.de/products?name={query} 검색 (requests, JSON)
  2) 결과 목록에서 PZN / 제품명 / AVP 추출
  3) AVP = Apothekenverkaufspreis (처방약 소비자가) — HIRA 조정가 계산에 사용

가격 종류:
  - AVP (UVP):     Apothekenverkaufspreis (처방약 공식 소비자가)
  - Festbetrag:    급여 기준가 (일부 약제)
  AVP ≒ 제조사가 + 물류마진 + 약국마진 + VAT 19%

HIRA 조정가:
  source_type=None → 기본 ratio 적용 (국가별 factory_ratio)
  독일 처방약 AVP는 소비자가 기준이므로 HIRA 공식 환산 시 deduct VAT + margin
"""

import logging
import re
import json
from pathlib import Path
from typing import Optional
from datetime import datetime

import requests

from .base import BaseScraper

logger = logging.getLogger(__name__)

GELBE_LISTE_BASE   = "https://www.gelbe-liste.de"
GELBE_SEARCH_URL   = GELBE_LISTE_BASE + "/products"
GELBE_API_SEARCH   = GELBE_LISTE_BASE + "/search/auto"

REQUESTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Referer": "https://www.gelbe-liste.de/",
}

JSON_HEADERS = {
    **REQUESTS_HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


class DeGelbeListeScraper(BaseScraper):
    COUNTRY        = "DE"
    CURRENCY       = "EUR"
    SOURCE_LABEL   = "Gelbe Liste (AVP, öffentlich)"
    SOURCE_TYPE    = None   # 기본 factory_ratio 사용
    REQUIRES_LOGIN = False

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/de")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._session = requests.Session()
        self._session.headers.update(REQUESTS_HEADERS)

    # ────────────────────────────────────────────────────────────────────────
    # 로그인 불필요 — 빈 구현
    # ────────────────────────────────────────────────────────────────────────

    async def login(self, page=None) -> None:
        pass

    async def logout(self, page=None) -> None:
        pass

    # ────────────────────────────────────────────────────────────────────────
    # 1) Gelbe Liste 검색 (requests)
    # ────────────────────────────────────────────────────────────────────────

    def _search_gelbe_liste(self, query: str) -> list[dict]:
        """
        Gelbe Liste 약제 검색.
        JSON API 우선 → HTML 폴백.
        반환: [{"product_name", "ingredient", "dosage_strength", "local_price", ...}]
        """
        results = self._try_json_api(query)
        if not results:
            results = self._try_html_search(query)
        return results

    def _try_json_api(self, query: str) -> list[dict]:
        """Gelbe Liste JSON 자동완성 API 시도."""
        try:
            r = self._session.get(
                GELBE_API_SEARCH,
                params={"term": query, "lang": "de"},
                headers=JSON_HEADERS,
                timeout=15,
            )
            if r.status_code != 200:
                return []
            data = r.json()
            # 응답 구조: [{"label": "...", "value": "...", ...}] 또는 다른 구조
            if isinstance(data, list):
                return self._parse_autocomplete(data, query)
            return []
        except Exception as e:
            logger.debug("[DE] JSON API 실패: %s", e)
            return []

    def _parse_autocomplete(self, data: list, query: str) -> list[dict]:
        """자동완성 결과 파싱 — 슬러그 추출 후 상세 페이지 조회."""
        results = []
        seen = set()
        q_lower = query.lower()

        for item in data[:5]:
            label = item.get("label", "") or item.get("value", "") or str(item)
            url   = item.get("url", "") or item.get("href", "")

            if not label:
                continue
            if q_lower not in label.lower():
                continue
            if label in seen:
                continue
            seen.add(label)

            # 상세 조회
            if url:
                detail = self._fetch_detail(url if url.startswith("http") else GELBE_LISTE_BASE + url)
                if detail:
                    results.append(detail)

        return results

    def _try_html_search(self, query: str) -> list[dict]:
        """HTML 검색 결과 페이지 파싱."""
        try:
            r = self._session.get(
                GELBE_SEARCH_URL,
                params={"name": query, "lang": "de"},
                timeout=20,
            )
            r.raise_for_status()
        except Exception as e:
            logger.warning("[DE] HTML 검색 실패: %s", e)
            return []

        return self._parse_search_html(r.text, query)

    def _parse_search_html(self, html: str, query: str) -> list[dict]:
        """
        Gelbe Liste 검색 결과 HTML 파싱.
        제품 카드: <article class="product-item"> 또는 유사 구조.
        """
        results = []
        q_lower = query.lower()

        # 제품 링크 추출
        hrefs = re.findall(r'href="(/produkte/[^"]+)"', html)
        if not hrefs:
            # 다른 패턴 시도
            hrefs = re.findall(r'href="(/products/[^"]+)"', html)
        if not hrefs:
            hrefs = re.findall(r'href="(/medikament/[^"]+)"', html)

        seen = set()
        for href in hrefs:
            if href in seen:
                continue
            if q_lower not in href.lower():
                continue
            seen.add(href)
            if len(results) >= 5:
                break

            detail = self._fetch_detail(GELBE_LISTE_BASE + href)
            if detail:
                results.append(detail)

        return results

    # ────────────────────────────────────────────────────────────────────────
    # 2) 상세 페이지 가격 추출
    # ────────────────────────────────────────────────────────────────────────

    def _fetch_detail(self, url: str) -> Optional[dict]:
        """Gelbe Liste 약제 상세 페이지에서 AVP 가격 추출."""
        try:
            r = self._session.get(url, timeout=20)
            r.raise_for_status()
        except Exception as e:
            logger.debug("[DE] 상세 페이지 접근 실패 (%s): %s", url, e)
            return None

        html = r.text
        text = re.sub(r"<[^>]+>", " ", html)   # strip HTML tags

        # ── 제품명 ─────────────────────────────────────────────────────────
        product_name = ""
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
        if m:
            product_name = m.group(1).strip()
        if not product_name:
            m = re.search(r'<title>([^<|–]+)', html, re.IGNORECASE)
            if m:
                product_name = m.group(1).strip()

        # ── 성분명 ─────────────────────────────────────────────────────────
        ingredient = ""
        ing_patterns = [
            r"Wirkstoff[:\s]+([A-Za-z][^\n<]{3,60})",
            r"Wirkstoffe[:\s]+([A-Za-z][^\n<]{3,60})",
            r"Substanz[:\s]+([A-Za-z][^\n<]{3,60})",
            r"INN[:\s]+([A-Za-z][^\n<]{3,60})",
            r"Pembrolizumab",   # 직접 감지
        ]
        for pat in ing_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                ingredient = m.group(0 if pat == "Pembrolizumab" else 1).strip()
                break

        # ── 규격/함량 ──────────────────────────────────────────────────────
        dosage = ""
        m = re.search(r"(\d+\s*mg(?:/\s*\d+\s*m[lL])?)", text, re.IGNORECASE)
        if m:
            dosage = m.group(1).strip()

        # ── 제조사 ─────────────────────────────────────────────────────────
        company = ""
        m = re.search(r"(?:Hersteller|Anbieter|Firma)[:\s]+([A-Z][^\n<]{3,60})", text, re.IGNORECASE)
        if m:
            company = m.group(1).strip()

        # ── PZN ────────────────────────────────────────────────────────────
        pzn = ""
        m = re.search(r"PZN[:\s-]+(\d{8})", text)
        if m:
            pzn = m.group(1)

        # ── AVP 가격 추출 ──────────────────────────────────────────────────
        # 패턴: "AVP: 1.234,56 €" 또는 "1.234,56 €" 또는 "€ 1.234,56"
        price = None
        avp_patterns = [
            r"AVP[^€\d]*(\d{1,5}(?:\.\d{3})*,\d{2})\s*€",
            r"UVP[^€\d]*(\d{1,5}(?:\.\d{3})*,\d{2})\s*€",
            r"Preis[^€\d]*(\d{1,5}(?:\.\d{3})*,\d{2})\s*€",
            r"(\d{1,5}(?:\.\d{3})*,\d{2})\s*€",
            r"€\s*(\d{1,5}(?:\.\d{3})*,\d{2})",
        ]
        for pat in avp_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                raw = m.group(1).replace(".", "").replace(",", ".")
                try:
                    price = float(raw)
                    # 유효 범위 검증 (1센트 ~ 100만유로)
                    if 0.01 <= price <= 1_000_000:
                        break
                    else:
                        price = None
                except ValueError:
                    continue

        return {
            "product_name":    product_name,
            "ingredient":      ingredient,
            "dosage_strength": dosage,
            "dosage_form":     "",
            "package_unit":    pzn,
            "local_price":     price,
            "source_url":      url,
            "extra": {
                "company":     company,
                "pzn":         pzn,
                "source_type": None,
            },
        }

    # ────────────────────────────────────────────────────────────────────────
    # 3) BaseScraper 인터페이스 — run() 동기 오버라이드
    # ────────────────────────────────────────────────────────────────────────

    async def search(self, query: str, page=None) -> list[dict]:
        """비동기 인터페이스 — 내부적으로 동기 requests 사용."""
        return self._search_gelbe_liste(query)

    async def refresh(self, _page=None) -> None:
        logger.info("[DE] Gelbe Liste 실시간 조회 방식")

    async def run(self, query: str) -> list[dict]:
        """
        Playwright 없이 requests만으로 실행.
        BaseScraper.run()을 오버라이드하여 불필요한 브라우저 실행을 방지.
        """
        logger.info("[DE] Gelbe Liste 검색 시작: '%s'", query)
        searched_at = datetime.now().isoformat()

        raw_results = self._search_gelbe_liste(query)
        logger.info("[DE] 검색 결과: %d건", len(raw_results))

        if not raw_results:
            logger.info("[DE] '%s' 검색 결과 없음", query)
            return []

        results = []
        for item in raw_results:
            results.append({
                "searched_at":         searched_at,
                "query_name":          query,
                "country":             self.COUNTRY,
                "product_name":        item.get("product_name", ""),
                "ingredient":          item.get("ingredient", ""),
                "dosage_strength":     item.get("dosage_strength", ""),
                "dosage_form":         item.get("dosage_form", ""),
                "package_unit":        item.get("package_unit", ""),
                "local_price":         item.get("local_price"),
                "currency":            self.CURRENCY,
                "exchange_rate":       None,
                "exchange_rate_from":  None,
                "exchange_rate_to":    None,
                "factory_price_krw":   None,
                "vat_rate":            None,
                "distribution_margin": None,
                "adjusted_price_krw":  None,
                "source_url":          item.get("source_url", ""),
                "source_label":        self.SOURCE_LABEL,
                "raw_data":            json.dumps(item.get("extra", {}), ensure_ascii=False),
            })

        return results
