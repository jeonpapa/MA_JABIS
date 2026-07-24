"""
영국 MIMS 스크래퍼 — Scrapling StealthySession 기반 (2026-07-24 재구현)

기존 DuckDuckGo + Playwright(공개접근) 방식은 MIMS 가 Cloudflare 봇차단("Just a moment")
+ 검색/가격 로그인벽을 강화하며 특히 프로덕션(데이터센터 IP)에서 상시 실패했다.
Scrapling `StealthySession(solve_cloudflare=True)`(camoufox 스텔스)로 Cloudflare Turnstile 을
자동 해결하고, MIMS 계정으로 로그인해 인증 검색한다. MIMS 는 심평원 인정 신뢰 소스라 유지.

흐름:
  1. StealthySession → Cloudflare Turnstile 자동 해결
  2. /login 에서 쿠키 동의 + 이메일/비번(#btnSignIn) 로그인
  3. /search/drugs?keywords={query} → 정확 매칭 시 302 로 약제 딥페이지 리다이렉트,
     아니면 결과목록에서 후보 URL 추출
  4. Price 섹션에서 용량별 £ 가격 추출 (기존 파싱 로직 재사용)

자격증명: config/.env `MIMS_UK_USERNAME` / `MIMS_UK_PASSWORD` (base.ENV_KEY_MAP).
의존성: scrapling[fetchers] + camoufox (scripts: `scrapling install`).
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .base import BaseScraper

logger = logging.getLogger(__name__)

MIMS_BASE  = "https://www.mims.co.uk"
LOGIN_URL  = f"{MIMS_BASE}/login"
SEARCH_URL = f"{MIMS_BASE}/search/drugs"


class UkMimsScraper(BaseScraper):
    COUNTRY        = "UK"
    CURRENCY       = "GBP"
    SOURCE_LABEL   = "MIMS online (UK, 로그인)"
    REQUIRES_LOGIN = True

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/uk")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only

    # ──────────────────────────────────────────────────────────────────────
    # Scrapling 동기 스크레이프 (asyncio.to_thread 로 실행 — StealthySession 은 동기)
    # ──────────────────────────────────────────────────────────────────────

    def _scrape_sync(self, query: str) -> list[dict]:
        user = (self.credentials or {}).get("username")
        pw   = (self.credentials or {}).get("password")
        if not user or not pw:
            logger.warning("[UK] MIMS 자격증명 없음 (MIMS_UK_USERNAME/PASSWORD) — 스킵")
            return []
        try:
            from scrapling.fetchers import StealthySession
        except Exception as e:
            logger.error("[UK] scrapling 미설치 — pip install 'scrapling[fetchers]' + scrapling install: %s", e)
            return []

        def login_action(page):
            page.wait_for_timeout(1500)
            try:
                page.click("#onetrust-accept-btn-handler", timeout=4000)  # 쿠키 동의
            except Exception:
                pass
            page.fill("input[data-cy='loginEmail']", user, timeout=8000)
            page.fill("input[data-cy='loginPassword']", pw, timeout=8000)
            try:
                page.click("#btnSignIn", timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(5000)

        results: list[dict] = []
        with StealthySession(headless=self.headless, solve_cloudflare=True) as s:
            lp = s.fetch(LOGIN_URL, page_action=login_action,
                         network_idle=False, timeout=120000)
            if "log in or register" in (lp.html_content or "").lower():
                logger.warning("[UK] MIMS 로그인 실패 (로그인벽 잔존)")
                return []

            sr = s.fetch(f"{SEARCH_URL}?keywords={quote(query)}",
                         network_idle=True, timeout=120000)
            html = sr.html_content or ""
            landed = (getattr(sr, "url", "") or "")

            # 정확 매칭 시 검색은 약제 딥페이지로 302 리다이렉트된다 → 그 페이지가 결과.
            # 아니면(결과 목록) 후보 딥링크를 추려 각각 방문.
            if self._is_drug_page(landed):
                pages = [(landed, html)]
            else:
                pages = []
                for cu in self._pick_result_urls(html, query)[:3]:
                    dp = s.fetch(cu, network_idle=True, timeout=120000)
                    pages.append((cu, dp.html_content or ""))

            for page_url, page_html in pages:
                results.extend(self._parse_price_page(page_html, page_url))
        return results

    @staticmethod
    def _is_drug_page(url: str) -> bool:
        """/drugs/{...}/{name} 형태의 약제 상세 페이지인지 (검색/인덱스 페이지 제외)."""
        if "/drugs/" not in url:
            return False
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        return tail not in ("drugs", "search") and "search" not in url

    @staticmethod
    def _pick_result_urls(html: str, query: str) -> list[str]:
        """검색결과 목록에서 /drugs/ 딥링크 추출 (약제명 토큰 매칭 우선)."""
        links = list(dict.fromkeys(re.findall(r"/drugs/[a-z0-9\-/]+", html)))
        # 인덱스/브라우즈 링크(끝이 카테고리) 배제하고 query 토큰이 마지막 세그먼트에 있는 것 우선
        q = query.lower().replace(" ", "-")
        exact = [l for l in links if q in l.rsplit("/", 1)[-1]]
        chosen = exact or links
        return [MIMS_BASE + l for l in chosen]

    # ──────────────────────────────────────────────────────────────────────
    # 가격 페이지 파싱 (HTML → text → Price 섹션 용량별 £ 가격)
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _html_to_text(html: str) -> str:
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|section)>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = (text.replace("&pound;", "£").replace("&amp;", "&")
                    .replace("&nbsp;", " ").replace("&#163;", "£"))
        text = re.sub(r"[ \t]+", " ", text)
        return text

    @classmethod
    def _parse_price_page(cls, html: str, url: str) -> list[dict]:
        text = cls._html_to_text(html)

        m = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
        product_name = m.group(1).strip() if m else ""

        ingredient = ""
        ing = re.search(r"How Supplied:\s*([A-Za-z][A-Za-z ]+?)\s*(?:\n|Price:)", text, re.I)
        if ing:
            ingredient = ing.group(1).strip()

        company = ""
        mfr = re.search(r"Manufacturer:\s*([^\n]+)", text, re.I)
        if mfr:
            company = mfr.group(1).strip()

        # 로그인 상태에서 £ 가 아예 없으면 비급여/미표시
        ps = re.search(
            r"Price:(.*?)(?:Indications:|Legal Class:|How Supplied:|Manufacturer:|Drug Class:|$)",
            text, re.S | re.I,
        )
        if not ps:
            logger.info("[UK] Price 섹션 미발견(비급여?): %s", url)
            return []

        section = ps.group(1)
        # "40mg blue oval f-c tab marked 177, 90=£11936.70." → (용량설명, 가격)
        price_lines = re.findall(r"([^\n=£]+?)\s*=\s*£\s*([\d,\.]+)", section)
        if not price_lines:
            simple = re.findall(r"£\s*([\d,\.]+)", section)
            if simple:
                price_lines = [("", simple[0])]

        rows = []
        for dosage_raw, price_raw in price_lines:
            try:
                price = float(price_raw.rstrip(".").replace(",", ""))
            except ValueError:
                continue
            rows.append({
                "product_name":    product_name,
                "ingredient":      ingredient,
                "dosage_strength": dosage_raw.strip(),
                "dosage_form":     "",
                "package_unit":    "",
                "local_price":     price,
                "source_url":      url,
                "extra": {"company": company, "source_type": None},
            })
        logger.info("[UK] %s — 용량별 가격 %d건", product_name or url, len(rows))
        return rows

    # ──────────────────────────────────────────────────────────────────────
    # BaseScraper 인터페이스
    # ──────────────────────────────────────────────────────────────────────

    async def search(self, query: str, page=None) -> list[dict]:
        """run() 이 Scrapling 로 전체 처리하므로 직접 사용 안 함 (계약 충족용)."""
        return []

    async def refresh(self, _page=None) -> None:
        logger.info("[UK] MIMS 공개가(로그인) — 캐시 없음")

    async def run(self, query: str) -> list[dict]:
        """Playwright 대신 Scrapling(StealthySession) 으로 로그인→검색→가격."""
        logger.info("[UK] MIMS(Scrapling) 검색 시작: '%s'", query)
        searched_at = datetime.now().isoformat()
        try:
            raw = await asyncio.to_thread(self._scrape_sync, query)
        except Exception as e:
            logger.error("[UK] MIMS 스크레이프 실패: %s", e, exc_info=True)
            return []

        results = []
        for item in raw:
            form_type = self._resolve_form_type(item)
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
                "form_type":           form_type,
            })
        logger.info("[UK] 결과: %d건", len(results))
        return results
