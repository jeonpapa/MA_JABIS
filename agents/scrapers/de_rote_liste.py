"""
독일 Rote Liste (rote-liste.de) 약가 스크레이퍼 — requests 기반 (Playwright 불필요)

로그인 흐름 (실제 HTML 확인됨):
  DocCheck OAuth — POST https://login.doccheck.com/
  실제 확인된 폼 필드:
    username       : 이메일 주소
    password       : 비밀번호
    login_id       : 2000000012529   (Rote Liste 클라이언트 ID)
    dc_client_id   : 2000000012529
    redirect_uri   : https://www.rote-liste.de/login
    strDcLanguage  : de
    intDcLanguageId: 148
    intLoginVersion: 3
    strDesignVersion: fullscreen_dc
    intLogoutSwitch: 0

로그인 성공 시:
  → 302 리다이렉트 to https://www.rote-liste.de/login  (토큰/코드 포함)
  → requests.Session이 쿠키 자동 관리

약제 검색:
  GET https://www.rote-liste.de/search?query={query}
  → /rle/detail/{id}/{slug} 링크 추출

설정 (.env):
  ROTE_LISTE_DE_USERNAME=your@doccheck.com
  ROTE_LISTE_DE_PASSWORD=yourpassword

주의:
  - DocCheck는 의료전문가용 인증 서비스. MSD 임직원 계정 필요.
  - 로그인 미설정 시 local_price=None (비급여 처리)
  - 가격 단위: AVP (EB)/FB = Erstattungsbetrag/Festbetrag (급여 기준가)
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from .base import BaseScraper

logger = logging.getLogger(__name__)

ROTE_LISTE_BASE  = "https://www.rote-liste.de"
DOCCHECK_LOGIN   = "https://login.doccheck.com/"
DOCCHECK_CLIENT  = "2000000012529"
REDIRECT_URI     = "https://www.rote-liste.de/login"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.rote-liste.de/",
}


class DeRoteListeScraper(BaseScraper):
    COUNTRY        = "DE"
    CURRENCY       = "EUR"
    SOURCE_LABEL   = "Rote Liste (AVP, DocCheck)"
    REQUIRES_LOGIN = True

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir  = cache_dir or Path("data/foreign/de")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only   = msd_only
        self._session   = requests.Session()
        self._session.headers.update(HEADERS)
        self._logged_in = False

    # ────────────────────────────────────────────────────────────────────────
    # 1) DocCheck 로그인 (requests — 실제 확인된 폼 구조)
    # ────────────────────────────────────────────────────────────────────────

    def _login_requests(self) -> bool:
        """
        DocCheck OAuth POST 로그인.
        성공: 302 → rote-liste.de/login 리다이렉트 + 세션 쿠키 설정
        실패: 302 → more.doccheck.com/errors/...
        """
        username = self.credentials.get("username", "")
        password = self.credentials.get("password", "")

        if not username or not password:
            logger.warning(
                "[DE] DocCheck 자격증명 없음 "
                "(ROTE_LISTE_DE_USERNAME/PASSWORD 미설정) — 비로그인 모드"
            )
            return False

        # DocCheck 로그인 페이지 먼저 방문 (PHPSESSID 쿠키 획득)
        try:
            init_url = (
                f"https://login.doccheck.com/code/"
                f"?dc_language=de"
                f"&dc_client_id={DOCCHECK_CLIENT}"
                f"&dc_template=fullscreen_dc"
                f"&redirect_uri={REDIRECT_URI}"
            )
            self._session.get(init_url, timeout=15)
        except Exception as e:
            logger.debug("[DE] DocCheck 초기 방문 실패: %s", e)

        # POST 로그인 (실제 폼 구조)
        payload = {
            "username":        username,
            "password":        password,
            "login_id":        DOCCHECK_CLIENT,
            "dc_client_id":    DOCCHECK_CLIENT,
            "redirect_uri":    REDIRECT_URI,
            "strDcLanguage":   "de",
            "intDcLanguageId": "148",
            "intLoginVersion": "3",
            "strDesignVersion":"fullscreen_dc",
            "intLogoutSwitch": "0",
        }

        try:
            resp = self._session.post(
                DOCCHECK_LOGIN,
                data=payload,
                allow_redirects=True,
                timeout=20,
                headers={**HEADERS, "Referer": init_url},
            )
            final_url = resp.url

            # 실패: more.doccheck.com/errors/... 로 리다이렉트
            if "errors" in final_url or "username-or-password" in final_url:
                logger.error("[DE] DocCheck 로그인 실패 — 자격증명 오류 (%s)", final_url)
                return False

            # 성공: rote-liste.de/login 으로 리다이렉트
            if "rote-liste.de" in final_url:
                logger.info("[DE] DocCheck 로그인 성공 (%s)", final_url)
                self._logged_in = True
                return True

            # 쿠키가 설정됐으면 낙관적 처리
            if self._session.cookies:
                logger.info("[DE] DocCheck 로그인 — 쿠키 설정됨 (final=%s)", final_url)
                self._logged_in = True
                return True

            logger.warning("[DE] DocCheck 로그인 결과 불확실 (final=%s)", final_url)
            self._logged_in = True
            return True

        except Exception as e:
            logger.error("[DE] DocCheck 로그인 오류: %s", e)
            return False

    async def login(self, page=None) -> None:
        """BaseScraper 호환 — run()이 직접 _login_requests() 호출"""
        pass

    async def logout(self, page=None) -> None:
        pass

    # ────────────────────────────────────────────────────────────────────────
    # 2) 검색 (requests)
    # ────────────────────────────────────────────────────────────────────────

    def _search(self, query: str) -> list[dict]:
        """
        Rote Liste 검색 → 약제 상세 URL 목록 반환.
        반환: [{"url": str, "prod_id": str, "slug": str}]
        """
        try:
            resp = self._session.get(
                ROTE_LISTE_BASE + "/search",
                params={"query": query},
                timeout=20,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("[DE] 검색 실패: %s", e)
            return []

        html = resp.text

        # /rle/detail/{prod_id}/{slug} 링크 추출
        links = re.findall(
            r'href=["\'](/rle/detail/(\d+)(?:-\d+)?/([^"\']+))["\']',
            html,
        )
        results = []
        seen_ids = set()
        for href, prod_id, slug in links:
            if prod_id in seen_ids:
                continue
            seen_ids.add(prod_id)
            results.append({
                "url": ROTE_LISTE_BASE + href,
                "prod_id": prod_id,
                "slug": slug,
            })

        logger.info("[DE] 검색 결과: %d건 (query=%s)", len(results), query)
        return results

    # ────────────────────────────────────────────────────────────────────────
    # 3) 상세 페이지 가격 추출
    # ────────────────────────────────────────────────────────────────────────

    def _extract_detail(self, url: str) -> Optional[dict]:
        """Rote Liste 약제 상세 페이지에서 AVP/가격 정보 추출."""
        try:
            resp = self._session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            logger.debug("[DE] 상세 페이지 실패 (%s): %s", url, e)
            return None

        html = resp.text
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)

        # ── 제품명 ─────────────────────────────────────────────────────────
        product_name = ""
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
        if m:
            product_name = re.sub(r"<[^>]+>", "", m.group(1)).strip()

        # ── 성분명 (Wirkstoff) ─────────────────────────────────────────────
        ingredient = ""
        for pat in [
            r"(?:Wirkstoff|INN|Substanz)[:\s]+([A-Za-z][^\n<\|]{3,60})",
            r"pembrolizumab",
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                ingredient = (m.group(0) if "pembrolizumab" in pat.lower()
                              else m.group(1).strip())
                break

        # ── 규격/함량 ──────────────────────────────────────────────────────
        dosage = ""
        m = re.search(r"(\d+\s*mg(?:/\s*(?:\d+\s*)?m[lL])?)", text, re.IGNORECASE)
        if m:
            dosage = m.group(1).strip()

        # ── 제조사 ─────────────────────────────────────────────────────────
        company = ""
        m = re.search(r"(?:Hersteller|Anbieter|Firma)[:\s]+([^\n<\|]{3,80})", text, re.IGNORECASE)
        if m:
            company = m.group(1).strip()

        # ── PZN ────────────────────────────────────────────────────────────
        pzn = ""
        m = re.search(r"PZN[:\s-]+(\d{8})", text)
        if m:
            pzn = m.group(1)

        # ── 로그인/DocCheck 벽 확인 ────────────────────────────────────────
        login_wall = "DocCheck" in text or "Fachkreise" in text or "einloggen" in text.lower()

        # ── 가격 추출 ─────────────────────────────────────────────────────
        # 로그인 후 PACKUNGSANGABEN 섹션에 테이블 형태로 가격이 표시됨
        # 패턴: "N1  12345678  1.234,56  5.678,90" 또는 "€ 1.234,56"
        # 독일 숫자: 천단위 "." + 소수점 ","  (예: 17.830,31)
        # negative lookbehind 로 앞자리 절단 방지
        DE_NUMBER = r"(?<![\d\.,])\d{1,3}(?:\.\d{3})*,\d{2}(?![\d\.])"

        price = None
        all_prices = []

        # 테이블 행 파싱: PZN (8자리) + 독일식 가격 패턴
        pack_rows = re.findall(
            rf"(\d+\s+[^\d\n]{{5,60}}?)\s+(N[123])?\s+(\d{{8}})\s+({DE_NUMBER})\s*({DE_NUMBER})?",
            text,
        )
        for row in pack_rows:
            dosage_raw, pack_size, pzn_raw, price1_raw, price2_raw = row
            # AVP (EB)/FB 우선, 없으면 AVP/UVP
            raw = price1_raw or price2_raw
            if not raw:
                continue
            try:
                p = float(raw.replace(".", "").replace(",", "."))
                if 0.01 <= p <= 2_000_000:
                    all_prices.append({
                        "dosage_strength": dosage_raw.strip(),
                        "pack_size":       pack_size,
                        "pzn":             pzn_raw,
                        "price":           p,
                    })
            except ValueError:
                continue

        if all_prices:
            price = all_prices[0]["price"]
            dosage = all_prices[0]["dosage_strength"] or dosage
            pzn    = all_prices[0]["pzn"] or pzn
        else:
            # 폴백: 단순 유로 가격 패턴 — 독일식 천단위 구분자 보존
            euro_prices = re.findall(rf"({DE_NUMBER})\s*€", text)
            for ep in euro_prices:
                try:
                    p = float(ep.replace(".", "").replace(",", "."))
                    if 0.01 <= p <= 2_000_000:
                        price = p
                        break
                except ValueError:
                    continue

        if login_wall and price is None:
            logger.info("[DE] DocCheck 로그인 필요 (가격 벽): %s", url)

        return {
            "product_name":    product_name,
            "ingredient":      ingredient,
            "dosage_strength": dosage,
            "dosage_form":     "",
            "package_unit":    pzn,
            "local_price":     price,
            "source_url":      url,
            "extra": {
                "company":      company,
                "all_prices":   all_prices,
                "login_wall":   login_wall,
                "source_type":  None,
            },
        }

    # ────────────────────────────────────────────────────────────────────────
    # 4) BaseScraper 인터페이스
    # ────────────────────────────────────────────────────────────────────────

    async def search(self, query: str, _page=None) -> list[dict]:
        """비동기 인터페이스."""
        links = self._search(query)
        if not links:
            return []

        q_lower = query.lower()
        # 쿼리명이 slug에 포함된 것 우선
        links.sort(key=lambda l: (0 if q_lower in l["slug"].lower() else 1))

        results = []
        seen_ids = set()
        for link in links[:3]:
            if link["prod_id"] in seen_ids:
                continue
            seen_ids.add(link["prod_id"])

            item = self._extract_detail(link["url"])
            if item:
                results.append(item)
                if item["local_price"] is not None:
                    break

        return results

    async def refresh(self, _page=None) -> None:
        logger.info("[DE] Rote Liste 실시간 조회 방식")

    async def run(self, query: str) -> list[dict]:
        """
        requests 기반으로 실행 (Playwright 불필요).
        1. DocCheck 로그인 → 2. 검색 → 3. 상세 페이지 파싱
        """
        logger.info("[DE] Rote Liste 검색 시작: '%s'", query)
        searched_at = datetime.now().isoformat()

        # DocCheck 로그인
        if not self._logged_in:
            self._login_requests()

        raw_results = await self.search(query)
        logger.info("[DE] 결과: %d건", len(raw_results))

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
