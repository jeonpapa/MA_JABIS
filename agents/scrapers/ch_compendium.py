"""
스위스 Compendium (compendium.ch) 약가 스크레이퍼

대상: https://www.compendium.ch/search/de
로그인: 불필요 (제품 상세 페이지 공개 접근 가능)

조회 흐름:
  1) 자동완성 API로 MNR(제품번호) 탐색
     GET /search/autocomplete?q={query}&type=brand → JSON
  2) 제품 상세 페이지에서 CHF 가격 파싱
     GET /product/{mnr}/{slug}

가격 종류:
  - Publikumspreis (공개가, 약국 판매가)
  - SL 상태 (Spezialitätenliste 수재 여부)

HIRA 조정가 공식:
  source_type="compendium" → factory_ratio = 0.65
  (PriceCalculator.calculate_factory_price 참조)
"""

import logging
import re
from pathlib import Path
from typing import Optional

import requests
from playwright.async_api import Page

from .base import BaseScraper

logger = logging.getLogger(__name__)

COMPENDIUM_BASE     = "https://www.compendium.ch"
AUTOCOMPLETE_URL    = COMPENDIUM_BASE + "/search/autocomplete"
PRODUCT_URL_TMPL    = COMPENDIUM_BASE + "/product/{mnr}/{slug}"
SEARCH_PAGE_URL     = COMPENDIUM_BASE + "/search/de"

REQUESTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
    "Referer": SEARCH_PAGE_URL,
}


class ChCompendiumScraper(BaseScraper):
    COUNTRY       = "CH"
    CURRENCY      = "CHF"
    SOURCE_LABEL  = "compendium.ch (Publikumspreis, SL)"
    SOURCE_TYPE   = "compendium"    # PriceCalculator에 전달 → ratio 0.65
    REQUIRES_LOGIN = False

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/ch")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._session = requests.Session()
        self._session.headers.update(REQUESTS_HEADERS)

    # ────────────────────────────────────────────────────────────────────────
    # 1) 자동완성 API → MNR 목록
    # ────────────────────────────────────────────────────────────────────────

    def _autocomplete(self, query: str) -> list[dict]:
        """
        GET /search/autocomplete?q={query}&type=brand
        반환: [{"mnr": "1346803", "description": "KEYTRUDA Inf Konz 100 mg/4ml", ...}, ...]
        """
        resp = self._session.get(
            AUTOCOMPLETE_URL,
            params={"q": query, "type": "brand"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        products = []
        # 실제 응답 구조:
        # {"brands": [{"description": "Keytruda", "products": [{"productNumber": 1346803, "description": "KEYTRUDA Inf Konz ..."}]}]}
        brand_list = data.get("brands", []) if isinstance(data, dict) else data
        for brand in brand_list:
            for product in brand.get("products", [brand]):
                mnr = product.get("productNumber") or product.get("mnr")
                desc = product.get("description", brand.get("description", ""))
                if mnr:
                    products.append({"mnr": str(mnr), "description": desc})

        logger.info("[CH] 자동완성 결과: %d개 (query=%s)", len(products), query)
        return products

    # ────────────────────────────────────────────────────────────────────────
    # 2) 제품 상세 페이지 가져오기 + 파싱
    # ────────────────────────────────────────────────────────────────────────

    def _fetch_product_page(self, mnr: str, slug: str) -> Optional[str]:
        """GET /product/{mnr}/{slug} → HTML."""
        url = PRODUCT_URL_TMPL.format(mnr=mnr, slug=slug)
        try:
            resp = self._session.get(url, timeout=20)
            if resp.status_code == 200:
                logger.info("[CH] 제품 페이지 로드: %s", url)
                return resp.text
            logger.warning("[CH] 제품 페이지 오류 %d: %s", resp.status_code, url)
        except Exception as e:
            logger.warning("[CH] 제품 페이지 요청 실패: %s", e)
        return None

    def _parse_product_html(self, html: str, mnr: str, description: str) -> Optional[dict]:
        """
        제품 상세 HTML에서 가격 및 메타 정보 추출.
        CHF 가격은 "CHF X,XXX.XX" 또는 "X'XXX.XX" 형식.
        """
        # ── 가격 추출 ──────────────────────────────────────────────────
        # 패턴 1: "CHF 4,294.10" 또는 "CHF 4'294.10"
        price_match = re.search(
            r"CHF\s+([\d\s',\.]+)",
            html,
            re.IGNORECASE,
        )

        if not price_match:
            # 패턴 2: "CHF" 없이 "1'234.50" 또는 "1,234.50" 형식 (보험 약가)
            price_match = re.search(
                r"(?:Publikumspreis|VK-Preis|Preis)[:\s]*([0-9\s',\.]+)",
                html,
                re.IGNORECASE,
            )

        if not price_match:
            logger.warning("[CH] MNR %s: 가격 미발견", mnr)
            return None

        raw_price_str = price_match.group(1).strip()
        raw_price = raw_price_str.replace(" ", "").replace("'", "").replace(",", "")

        try:
            price = float(raw_price)
        except ValueError:
            logger.warning("[CH] 가격 파싱 실패: %s", raw_price_str)
            return None

        # ── 성분명 추출 (Wirkstoff) ─────────────────────────────────────
        ingredient_match = re.search(
            r"(?:Wirkstoff|Wirkstoffe|substance\s+active)[:\s]*([A-Za-z][^\n<]{3,60})",
            html,
            re.IGNORECASE,
        )
        ingredient = ingredient_match.group(1).strip() if ingredient_match else ""

        # ── 포장 (Packung) ─────────────────────────────────────────────
        # 설명 문자열에서 추출: "KEYTRUDA Inf Konz 100 mg/4ml"
        dosage_strength = description

        # ── 제조사 ─────────────────────────────────────────────────────
        company_match = re.search(
            r"(?:Zulassungsinhaberin|Hersteller|titulaire)[:\s]*([A-Za-z][^\n<]{3,80})",
            html,
            re.IGNORECASE,
        )
        company = company_match.group(1).strip() if company_match else "MSD"

        # ── SL 상태 (급여 여부) ────────────────────────────────────────
        sl_match = re.search(r"SL[:\s]*([^\n<]{3,80})", html)
        sl_status = sl_match.group(1).strip() if sl_match else ""

        # ── 제품명 (description에서) ────────────────────────────────────
        product_name_match = re.search(
            r"<h1[^>]*>([^<]+)</h1>",
            html,
        )
        product_name = product_name_match.group(1).strip() if product_name_match else description

        return {
            "product_name":    product_name or description,
            "ingredient":      ingredient,
            "dosage_strength": dosage_strength,
            "dosage_form":     "",
            "package_unit":    "",
            "local_price":     price,
            "source_url":      PRODUCT_URL_TMPL.format(mnr=mnr, slug="keytruda"),
            "extra": {
                "mnr":        mnr,
                "company":    company,
                "sl_status":  sl_status,
                "source_type": self.SOURCE_TYPE,
            },
        }

    # ────────────────────────────────────────────────────────────────────────
    # 3) MSD 필터
    # ────────────────────────────────────────────────────────────────────────

    def _is_msd_product(self, html: str, description: str) -> bool:
        """MSD 관련 텍스트가 제품 페이지에 있는지 확인."""
        MSD_PATTERNS = ["MSD", "Merck Sharp", "MERCK SHARP"]
        combined = html + " " + description
        return any(pat.upper() in combined.upper() for pat in MSD_PATTERNS)

    # ────────────────────────────────────────────────────────────────────────
    # 4) BaseScraper 인터페이스
    # ────────────────────────────────────────────────────────────────────────

    async def search(self, query: str, _page: Page) -> list[dict]:
        """
        스위스 Compendium에서 약제명으로 검색.
        1. 자동완성 API로 MNR 탐색
        2. 각 MNR의 제품 페이지 파싱
        3. msd_only=True이면 MSD 제품만 반환
        """
        candidates = self._autocomplete(query)
        if not candidates:
            logger.info("[CH] '%s' 자동완성 결과 없음 → 비급여 처리", query)
            return []

        results = []
        slug = query.lower().replace(" ", "-")

        for item in candidates:
            mnr = item["mnr"]
            description = item["description"]

            html = self._fetch_product_page(mnr, slug)
            if not html:
                # slug 없이도 시도
                html = self._fetch_product_page(mnr, "product")

            if not html:
                continue

            if self.msd_only and not self._is_msd_product(html, description):
                logger.debug("[CH] MSD 아님, 건너뜀: %s", description)
                continue

            parsed = self._parse_product_html(html, mnr, description)
            if parsed:
                results.append(parsed)

        if not results:
            logger.info("[CH] '%s' 결과 없음 (MSD 필터 적용)", query)
        return results

    async def refresh(self, _page: Page) -> None:
        """Compendium은 매번 실시간 조회 — 캐시 없음."""
        logger.info("[CH] Compendium은 실시간 조회 방식 (캐시 불필요)")
