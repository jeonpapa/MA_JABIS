"""
영국 MIMS (mims.co.uk) 약가 스크레이퍼

대상: https://www.mims.co.uk/
로그인: 불필요 (구글 Referer 경유 시 공개 가격 표시)

조회 흐름:
  1) DuckDuckGo HTML 검색: "site:mims.co.uk {query} drugs" → MIMS URL 추출
  2) Playwright로 Google Referer 헤더 설정 후 MIMS 약제 페이지 접근
  3) "Price:" 섹션에서 용량별 GBP 가격 파싱
     예: "100mg/4ml conc for soln for inf in vial, 2=£5260.00."
  4) 모든 용량·포장 규격별 가격 추출 (list[dict])

가격 종류:
  - Price: {dosage}={£ 금액} 형식으로 복수 포장 존재 가능
  - 용량별 개별 결과로 반환

HIRA 조정가:
  source_type 없음 → 기본 ratio 적용 (PriceCalculator 참조)
  (MIMS 가격은 NHS 공개가 / Manufacturer 리스트가)
"""

import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import requests
from playwright.async_api import Page

from .base import BaseScraper

logger = logging.getLogger(__name__)

MIMS_BASE       = "https://www.mims.co.uk"
DDG_HTML_URL    = "https://html.duckduckgo.com/html/"

REQUESTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}


class UkMimsScraper(BaseScraper):
    COUNTRY       = "UK"
    CURRENCY      = "GBP"
    SOURCE_LABEL  = "MIMS online (UK public price)"
    REQUIRES_LOGIN = False          # 구글 Referer로 공개 접근

    # Playwright 실행 옵션 (샌드박스 비활성화 필요)
    PLAYWRIGHT_ARGS = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
    ]

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/uk")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._session = requests.Session()
        self._session.headers.update(REQUESTS_HEADERS)

    # ────────────────────────────────────────────────────────────────────────
    # 1) DuckDuckGo → MIMS URL 탐색
    # ────────────────────────────────────────────────────────────────────────

    def _find_mims_urls(self, query: str) -> list[str]:
        """
        DuckDuckGo HTML 검색으로 MIMS 약제 페이지 URL 목록 반환.
        site:mims.co.uk {query} 검색 → /drugs/ 경로 URL 추출
        DDG 실패 시 직접 URL 구성으로 폴백.
        """
        try:
            r = self._session.get(
                DDG_HTML_URL,
                params={"q": f"site:mims.co.uk {query}"},
                timeout=20,
            )
            r.raise_for_status()
        except Exception as e:
            logger.warning("[UK] DuckDuckGo 검색 실패: %s", e)
            return []

        uddg_links = re.findall(r"uddg=([^&\"\s]+)", r.text)
        urls = list(dict.fromkeys([
            unquote(u)
            for u in uddg_links
            if "mims.co.uk/drugs" in unquote(u)
        ]))

        # DDG 검색 결과가 없으면 직접 URL 구성으로 폴백
        if not urls:
            slug = query.lower().replace(" ", "-")
            fallback_url = f"{MIMS_BASE}/drugs/{slug}/"
            logger.info("[UK] DDG 검색 결과 없음, 직접 URL 시도: %s", fallback_url)
            urls = [fallback_url]

        logger.info("[UK] DDG 검색 결과 MIMS URL: %d개 (query=%s)", len(urls), query)
        return urls

    # ────────────────────────────────────────────────────────────────────────
    # 2) MIMS 약제 페이지에서 가격 추출
    # ────────────────────────────────────────────────────────────────────────

    async def _extract_prices_from_page(self, url: str, page: Page) -> list[dict]:
        """
        MIMS 약제 페이지에서 용량별 GBP 가격 목록 추출.

        Price 섹션 예시:
          Pembrolizumab
          Price:
          100mg/4ml conc for soln for inf in vial, 2=£5260.00.
          395mg/2.4ml soln for inj in vial, 1=£5260.00.
          790mg/4.8ml soln for inj in vial, 1=£10520.00.

        반환: [{"dosage_strength": "100mg/4ml conc for soln for inf in vial, 2",
                "local_price": 5260.0, ...}, ...]
        """
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2500)

        text = await page.inner_text("body")

        # ── 제품명 ───────────────────────────────────────────────────────
        product_name = ""
        try:
            product_name = await page.inner_text("h1", timeout=3_000)
            product_name = product_name.strip()
        except Exception:
            pass

        # ── 성분명 (How Supplied 아래 첫 줄 = INN) ───────────────────────
        ingredient = ""
        ing_match = re.search(
            r"How Supplied:\s*([A-Za-z][a-z]+(?:\s+[A-Za-z]+)?)\s*\nPrice:",
            text,
            re.IGNORECASE,
        )
        if ing_match:
            ingredient = ing_match.group(1).strip()

        # ── 제조사 ────────────────────────────────────────────────────────
        company = ""
        mfr_match = re.search(r"Manufacturer:\s*([^\n]+)", text, re.IGNORECASE)
        if mfr_match:
            company = mfr_match.group(1).strip()

        # ── 로그인 요구 감지 (가격 없으면 비급여 처리) ──────────────────
        login_required = (
            "log in or register" in text.lower()
            and not re.search(r"£\s*[\d,\.]+", text)
        )
        if login_required:
            logger.info("[UK] 로그인 필요 페이지 — 공개 가격 없음: %s", url)
            return []

        # ── Price 섹션 추출 ────────────────────────────────────────────────
        # "Price:" ~ "Indications:" / "Legal Class:" 등 다음 섹션까지
        price_section_match = re.search(
            r"Price:(.*?)(?:Indications:|Legal Class:|How Supplied:|Manufacturer:|Drug Class:|$)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if not price_section_match:
            logger.warning("[UK] Price 섹션 미발견: %s", url)
            return []

        price_section = price_section_match.group(1)
        # 용량=가격 패턴: "100mg/4ml conc for soln for inf in vial, 2=£5260.00."
        # 또는 단순 "£5260.00"
        price_lines = re.findall(
            r"([^\n=£]+?)\s*=\s*£\s*([\d,\.]+)",
            price_section,
        )

        if not price_lines:
            # 단순 £ 가격만 있는 경우
            simple_prices = re.findall(r"£\s*([\d,\.]+)", price_section)
            if simple_prices:
                raw = simple_prices[0].replace(",", "")
                try:
                    price_lines = [("", float(raw))]
                except ValueError:
                    pass

        results = []
        for dosage_raw, price_raw in price_lines:
            price_raw_clean = price_raw.rstrip(".").replace(",", "")
            try:
                price = float(price_raw_clean)
            except ValueError:
                continue

            dosage_strength = dosage_raw.strip()
            results.append({
                "product_name":    product_name,
                "ingredient":      ingredient,
                "dosage_strength": dosage_strength,
                "dosage_form":     "",
                "package_unit":    "",
                "local_price":     price,
                "source_url":      url,
                "extra": {
                    "company":     company,
                    "source_type": None,
                },
            })

        logger.info("[UK] %s — 용량별 가격 %d건", product_name or url, len(results))
        return results

    # ────────────────────────────────────────────────────────────────────────
    # 3) BaseScraper 인터페이스 (run()에서 Playwright args 오버라이드 필요)
    # ────────────────────────────────────────────────────────────────────────

    async def search(self, query: str, page: Page) -> list[dict]:
        """
        MIMS에서 약제명으로 검색 후 용량별 GBP 가격 반환.
        1. DuckDuckGo로 mims.co.uk URL 탐색
        2. Google Referer 설정 후 MIMS 페이지 접근
        3. Price 섹션에서 용량별 가격 추출
        """
        urls = self._find_mims_urls(query)
        if not urls:
            logger.info("[UK] '%s' MIMS URL 탐색 결과 없음 → 비급여", query)
            return []

        # 쿼리명이 URL 경로에 포함된 것 우선 정렬
        q_slug = query.lower().replace(" ", "-")
        urls_sorted = (
            [u for u in urls if q_slug in u.lower()]
            + [u for u in urls if q_slug not in u.lower()]
        )

        all_results = []
        seen_urls = set()

        for url in urls_sorted[:3]:
            if url in seen_urls:
                continue
            seen_urls.add(url)

            items = await self._extract_prices_from_page(url, page)
            all_results.extend(items)

            if all_results:
                break   # 첫 번째 유효 결과에서 중단

        if not all_results:
            logger.info("[UK] '%s' 가격 없음 → 비급여", query)
        return all_results

    async def refresh(self, _page: Page) -> None:
        """MIMS는 매번 실시간 조회."""
        logger.info("[UK] MIMS 실시간 조회 방식")

    async def run(self, query: str) -> list[dict]:
        """
        BaseScraper.run() 오버라이드 — Playwright에 --no-sandbox 인수 추가.
        (MIMS는 로그인 불필요, Google Referer만 설정)
        """
        import json
        from datetime import datetime
        from playwright.async_api import async_playwright

        logger.info("[UK] 검색 시작: '%s'", query)
        results = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                args=self.PLAYWRIGHT_ARGS,
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={"Referer": "https://www.google.com/"},
            )
            page = await context.new_page()

            try:
                raw_results = await self.search(query, page)
                logger.info("[UK] 검색 결과: %d건", len(raw_results))

                searched_at = datetime.now().isoformat()
                for item in raw_results:
                    form_type = self._resolve_form_type(item)
                    results.append({
                        "searched_at":         searched_at,
                        "query_name":          query,
                        "country":             self.COUNTRY,
                        "product_name":        item.get("product_name"),
                        "ingredient":          item.get("ingredient"),
                        "dosage_strength":     item.get("dosage_strength"),
                        "dosage_form":         item.get("dosage_form"),
                        "package_unit":        item.get("package_unit"),
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
                        "raw_data":            json.dumps(
                            item.get("extra", {}), ensure_ascii=False
                        ),
                        "form_type":           form_type,
                    })

            except Exception as e:
                logger.error("[UK] 검색 오류: %s", e, exc_info=True)
                raise
            finally:
                await context.close()
                await browser.close()

        return results
