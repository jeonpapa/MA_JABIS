"""
캐나다 Ontario Exceptional Access Program (EAP) 약가 스크레이퍼

대상: https://www.ontario.ca/page/exceptional-access-program-product-prices
로그인: 불필요 (공개 HTML 테이블)

배경:
  - Ontario Ministry of Health 의 EAP 목록 — 제약사와 협약한 DBP (Drug Benefit Price)
  - 대부분이 항암제·희귀약 등 특수 급여 대상 약제 (온타리오주 한정 공개가)
  - 캐나다 연방 단위의 공식 약가 공개 시스템은 없음 (각 주별 상이) — Ontario 가 가장
    접근성 높고 A8 비교에 상대적으로 대표성 있음

컬럼 구조 (HTML 테이블):
  - DIN (Drug Identification Number)
  - Trade name
  - Strength
  - Dosage form          (예: "Tab", "Inj Sol-240 mL Vial Pk", ...)
  - DBP (Drug Benefit Price)   통화 CAD

HIRA 조정가:
  source_type=None → 기본 factory_ratio 사용
  (DBP 는 제약사-정부 협약가 — 공장도가 성격에 가까우나 보수적으로 기본 공식 적용)
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import BaseScraper

logger = logging.getLogger(__name__)

EAP_URL = "https://www.ontario.ca/page/exceptional-access-program-product-prices"

CACHE_TTL_HOURS = 24

REQUESTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
}


class CaOntarioScraper(BaseScraper):
    COUNTRY        = "CA"
    CURRENCY       = "CAD"
    SOURCE_LABEL   = "Ontario EAP (Drug Benefit Price)"
    SOURCE_TYPE    = None
    REQUIRES_LOGIN = False

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/ca")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._rows_cache: Optional[list[dict]] = None
        self._session = requests.Session()
        self._session.headers.update(REQUESTS_HEADERS)

    async def login(self, page=None) -> None:
        pass

    async def logout(self, page=None) -> None:
        pass

    # ────────────────────────────────────────────────────────────────────────
    # 1) 페이지 캐시
    # ────────────────────────────────────────────────────────────────────────

    def _cache_path(self) -> Path:
        return self.cache_dir / "eap_prices.html"

    def _ensure_cached(self) -> Optional[Path]:
        path = self._cache_path()
        if path.exists():
            age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
            if age < timedelta(hours=CACHE_TTL_HOURS):
                return path
        try:
            r = self._session.get(EAP_URL, timeout=30)
            r.raise_for_status()
            path.write_text(r.text, encoding="utf-8")
            logger.info("[CA] EAP 페이지 캐시 갱신 (%d bytes)", len(r.text))
            return path
        except Exception as e:
            logger.warning("[CA] EAP 페이지 다운로드 실패: %s", e)
            return path if path.exists() else None

    # ────────────────────────────────────────────────────────────────────────
    # 2) HTML 테이블 파싱
    # ────────────────────────────────────────────────────────────────────────

    def _parse_table(self, html: str) -> list[dict]:
        """
        EAP 페이지의 모든 연도별 테이블을 파싱해 단일 행 목록 반환.
        각 행: {din, product_name, strength, dosage_form, price, year}
        최신 연도 우선 (중복 DIN 은 가장 최근 연도만 유지).
        """
        soup = BeautifulSoup(html, "html.parser")
        rows_by_din: dict[str, dict] = {}

        # 페이지 내 h2 태그 = 연도 (2026, 2025, ...)
        current_year = ""
        for tag in soup.find_all(["h2", "table"]):
            if tag.name == "h2":
                m = re.search(r"(20\d{2})", tag.get_text(" ", strip=True))
                current_year = m.group(1) if m else ""
                continue

            headers = [
                th.get_text(" ", strip=True).lower()
                for th in tag.find_all("th")
            ]
            if not any("trade name" in h or "din" in h for h in headers):
                continue   # EAP 테이블 아님

            for tr in tag.find_all("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) < 5:
                    continue
                din, trade, strength, dform, price_raw = cells[:5]
                if not din or not trade:
                    continue
                price = self._parse_price(price_raw)
                key = din.strip()
                # 신선도: 최신 연도가 기존을 덮어씀
                existing = rows_by_din.get(key)
                if existing and existing["year"] >= current_year:
                    continue
                rows_by_din[key] = {
                    "din":           din.strip(),
                    "product_name":  trade.strip(),
                    "strength":      strength.strip(),
                    "dosage_form":   dform.strip(),
                    "price":         price,
                    "year":          current_year,
                }
        return list(rows_by_din.values())

    def _parse_price(self, raw: str) -> Optional[float]:
        """'$238.7160' / '$1,234.56' / '—' → float or None."""
        if not raw:
            return None
        s = raw.strip().replace("$", "").replace(",", "").replace("\xa0", "")
        if not s or s in {"-", "—", "N/A"}:
            return None
        try:
            return float(s)
        except ValueError:
            return None

    # ────────────────────────────────────────────────────────────────────────
    # 3) 검색
    # ────────────────────────────────────────────────────────────────────────

    def _load_rows(self) -> list[dict]:
        if self._rows_cache is not None:
            return self._rows_cache
        path = self._ensure_cached()
        if not path:
            return []
        html = path.read_text(encoding="utf-8", errors="ignore")
        self._rows_cache = self._parse_table(html)
        logger.info("[CA] EAP 파싱 결과: %d DIN", len(self._rows_cache))
        return self._rows_cache

    async def search(self, query: str, page=None) -> list[dict]:
        rows = self._load_rows()
        if not rows:
            logger.info("[CA] EAP 데이터 없음")
            return []

        q_lower = query.lower().strip()
        matches = [r for r in rows if q_lower in r["product_name"].lower()]
        logger.info("[CA] '%s' EAP 매칭: %d건", query, len(matches))

        results = []
        for r in matches:
            results.append({
                "product_name":    r["product_name"],
                "ingredient":      "",
                "dosage_strength": r["strength"],
                "dosage_form":     r["dosage_form"],
                "package_unit":    "",
                "local_price":     r["price"],
                "source_url":      EAP_URL,
                "extra": {
                    "din":      r["din"],
                    "year":     r["year"],
                    "price_kind": "DBP (Drug Benefit Price)",
                    "source_type": None,
                },
            })
        return results

    async def refresh(self, _page=None) -> None:
        logger.info("[CA] Ontario EAP 실시간 조회 방식")

    async def run(self, query: str) -> list[dict]:
        """Playwright 없이 requests + BeautifulSoup 로 실행."""
        logger.info("[CA] EAP 검색 시작: '%s'", query)
        searched_at = datetime.now().isoformat()

        raw_results = await self.search(query)
        logger.info("[CA] 결과: %d건", len(raw_results))
        if not raw_results:
            return []

        results = []
        for item in raw_results:
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

        return results
