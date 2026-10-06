"""
프랑스 VIDAL (vidal.fr) 약가 스크레이퍼 — requests 기반 (Playwright 불필요)

로그인 흐름 (실제 확인됨):
  1) POST https://www.vidal.fr/login/?client_id=vidal_2017&redirect=...
     Fields: login[email], login[password], login[remember]=1, login[submit]=Je me connecte
  2) OAuth2 리다이렉트 → oauth2.vidal.fr → vidal.fr/auth-redirection.html
  3) requests.Session이 쿠키를 자동 관리 → 이후 모든 요청에 인증 적용

약제 검색 흐름:
  1) GET https://www.vidal.fr/recherche.html?query={query}
     → data-cbo 속성에 base64 인코딩된 약제 URL 포함
  2) 약제 상세 페이지에서 가격·성분·급여 정보 추출

설정 (.env):
  VIDAL_FR_USERNAME=your@email.com
  VIDAL_FR_PASSWORD=yourpassword

주의:
  - 로그인 미설정 시 local_price=None (비급여 처리)
  - 병원 전용 항암제(Keytruda 등)는 로그인 후에도 가격이 다를 수 있음
  - Vidal Professional 구독 필요 (MSD 사내 구독 확인)
"""

import base64
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urljoin

import requests

from .base import BaseScraper

logger = logging.getLogger(__name__)

VIDAL_BASE   = "https://www.vidal.fr"
OAUTH2_BASE  = "https://oauth2.vidal.fr"

# ── 큐레이션 약제 URL 맵 (brand/INN lower → Vidal 상세 URL) ──────────────────
# Vidal 검색→약제URL 해소가 JS/안티스크래핑으로 불안정해, 추적 대상 약제는 검증된
# 상세 URL 을 직접 매핑한다(health.kr DRUG_CD_MAP 패턴). 등록 약제는 검색 없이 안정 조회,
# 미등록은 search fallback(best-effort). 신규 약제 추가 시 여기에 등록.
VIDAL_URL_MAP: dict[str, str] = {
    "keytruda":      VIDAL_BASE + "/medicaments/keytruda-25-mg-ml-sol-diluer-p-perf-173141.html",
    "pembrolizumab": VIDAL_BASE + "/medicaments/keytruda-25-mg-ml-sol-diluer-p-perf-173141.html",
}

# 로그인 POST 액션 URL (페이지에서 실제 확인됨)
LOGIN_PATH   = "/login/"
LOGIN_PARAMS = {
    "client_id": "vidal_2017",
    "redirect": (
        "https://oauth2.vidal.fr/oauth/auth/"
        "?client_id=vidal_2017"
        "&response_type=code"
        "&scope=basic+userdata+licenses+logout+campus"
        "&redirect_uri=https%3A%2F%2Fwww.vidal.fr%2Fauth-redirection.html%3Fno-redirect%3D1"
    ),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


class FrVidalScraper(BaseScraper):
    COUNTRY        = "FR"
    CURRENCY       = "EUR"
    SOURCE_LABEL   = "VIDAL.fr"
    SOURCE_TYPE    = "vidal"      # PriceCalculator 오버라이드 없음 → FR 기본 ratio 0.77 (2026-10: 0.65 는 발효된 적 없음)
    REQUIRES_LOGIN = True
    LOGIN_OPTIONAL = True         # 자격증명 없으면 비로그인 모드(_login_requests → False) — 큐레이션 UCD 페이지는 공개

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/fr")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only  = msd_only
        self._session  = requests.Session()
        self._session.headers.update(HEADERS)
        self._logged_in = False

    # ────────────────────────────────────────────────────────────────────────
    # 1) 로그인 (requests 기반 — OAuth2 흐름)
    # ────────────────────────────────────────────────────────────────────────

    def _login_requests(self) -> bool:
        """
        Vidal.fr 로그인 (requests).
        POST /login/?client_id=vidal_2017&redirect=...
        OAuth2 코드 교환 → 세션 쿠키 설정.
        반환: 성공 여부
        """
        username = self.credentials.get("username", "")
        password = self.credentials.get("password", "")

        if not username or not password:
            logger.warning("[FR] Vidal 자격증명 없음 (VIDAL_FR_USERNAME/PASSWORD 미설정) — 비로그인 모드")
            return False

        # 로그인 페이지 먼저 방문 (쿠키/세션 초기화)
        try:
            self._session.get(VIDAL_BASE + "/login.html", timeout=15)
        except Exception as e:
            logger.debug("[FR] 로그인 페이지 사전 방문 실패: %s", e)

        # POST 로그인
        login_url = VIDAL_BASE + LOGIN_PATH + "?" + urlencode(LOGIN_PARAMS)
        payload = {
            "login[email]":    username,
            "login[password]": password,
            "login[remember]": "1",
            "login[submit]":   "Je me connecte",
        }

        try:
            resp = self._session.post(
                login_url, data=payload,
                allow_redirects=True, timeout=20,
            )
            # 로그인 성공 확인: 최종 URL이 vidal.fr 도메인, 에러 키워드 없음
            final_url = resp.url
            page_text = resp.text

            if "auth-redirection" in final_url or "no-redirect" in final_url:
                logger.info("[FR] Vidal 로그인 성공 (OAuth2 리다이렉트 완료)")
                self._logged_in = True
                return True
            if "erreur" in page_text.lower() or "incorrect" in page_text.lower():
                logger.error("[FR] Vidal 로그인 실패 — 자격증명 오류")
                return False
            # 메인 페이지로 리다이렉트되면 성공으로 간주
            if final_url.rstrip("/") in (VIDAL_BASE, VIDAL_BASE + "/"):
                logger.info("[FR] Vidal 로그인 성공 (메인 페이지)")
                self._logged_in = True
                return True

            # 쿠키가 설정됐으면 성공
            if self._session.cookies:
                logger.info("[FR] Vidal 로그인 성공 (쿠키 확인)")
                self._logged_in = True
                return True

            logger.warning("[FR] Vidal 로그인 결과 불확실 (final_url=%s)", final_url)
            self._logged_in = True   # 낙관적 처리
            return True

        except Exception as e:
            logger.error("[FR] Vidal 로그인 오류: %s", e)
            return False

    async def login(self, page=None) -> None:
        """BaseScraper 호환 — run()이 직접 _login_requests() 호출"""
        pass

    async def logout(self, page=None) -> None:
        pass

    # ────────────────────────────────────────────────────────────────────────
    # 2) 검색
    # ────────────────────────────────────────────────────────────────────────

    def _search(self, query: str) -> list[dict]:
        """
        Vidal 검색 → 약제 상세 URL 목록 반환.
        반환: [{"url": str, "text": str}]
        큐레이션 맵(VIDAL_URL_MAP)에 등록된 약제는 검색 생략하고 매핑 URL 직접 사용.
        """
        mapped = VIDAL_URL_MAP.get((query or "").strip().lower())
        if mapped:
            logger.info("[FR] Vidal URL 맵 적중: %s", query)
            return [{"url": mapped, "text": query}]
        try:
            resp = self._session.get(
                VIDAL_BASE + "/recherche.html",
                params={"query": query},
                timeout=20,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("[FR] 검색 실패: %s", e)
            return []

        html = resp.text

        # data-cbo 속성 (base64 인코딩된 URL) 디코딩
        results = []
        seen_urls = set()
        q_lower = query.lower()

        cbo_items = re.findall(
            r'data-cbo=["\']([^"\']+)["\'][^>]*>([^<]*)</(?:a|span|div)',
            html, re.IGNORECASE
        )
        for cbo_val, text in cbo_items:
            try:
                padding = 4 - len(cbo_val) % 4
                decoded = base64.b64decode(cbo_val + "=" * padding).decode("utf-8", errors="ignore")
            except Exception:
                continue

            # medicaments/ 로 시작하는 URL만 (navLinks, gammes 등 제외)
            if not decoded.startswith("medicaments/"):
                continue
            if "/substances/" in decoded or "/liste" in decoded:
                continue

            url = VIDAL_BASE + "/" + decoded
            if url in seen_urls:
                continue
            seen_urls.add(url)

            results.append({"url": url, "text": text.strip()})

        # 직접 href 링크도 추가
        direct_links = re.findall(
            r'href=["\'](?:https://www\.vidal\.fr)?(/medicaments/[^"\']+\.html)["\']',
            html, re.IGNORECASE
        )
        # bare 약제 상세 URL (href/data-cbo 아닌 JS/data 문자열로 있는 경우) — 약제 상세는
        # '-<숫자ID>.html' 로 끝난다(분류/nav 페이지 제외). INN 검색 시 이쪽에만 잡힘.
        bare_links = re.findall(r'/medicaments/[a-z0-9\-]+-\d+\.html', html, re.IGNORECASE)
        for href in [*direct_links, *bare_links]:
            if "/substances/" in href or "/classification/" in href:
                continue
            url = VIDAL_BASE + href if not href.startswith("http") else href
            if url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({"url": url, "text": ""})

        logger.info("[FR] 검색 결과 링크: %d개 (query=%s)", len(results), query)
        return results

    # ────────────────────────────────────────────────────────────────────────
    # 3) 상세 페이지 가격 추출
    # ────────────────────────────────────────────────────────────────────────

    def _extract_detail(self, url: str) -> Optional[dict]:
        """약제 상세 페이지에서 가격·성분·포장 정보 추출."""
        try:
            resp = self._session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            logger.debug("[FR] 상세 페이지 실패 (%s): %s", url, e)
            return None

        html = resp.text
        # script/style 블록 먼저 제거 — 태그만 벗기면 JS 코드가 본문 텍스트로 남아
        # 성분명 등 정규식에 오매칭됨(예: ingredient 에 querySelector 코드 유입).
        no_code = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html,
                         flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", no_code)
        text = re.sub(r"\s+", " ", text)

        # ── 제품명 ─────────────────────────────────────────────────────────
        product_name = ""
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
        if m:
            product_name = re.sub(r"<[^>]+>", "", m.group(1)).strip()

        # ── 성분명 (DCI) ────────────────────────────────────────────────────
        ingredient = ""
        ing_patterns = [
            r"(?:Substance active|DCI|Principe actif)\s*[:\s]+([A-Za-z][^\n<]{3,60})",
            r"(?:pembrolizumab)",
        ]
        for pat in ing_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                cand = m.group(0 if "pembrolizumab" in pat.lower() else 1).strip()
                # JS/코드 오매칭 배제 — 성분명(DCI)은 문자·공백·하이픈·쉼표만.
                # 인라인 속성 JS 가 태그제거 후 텍스트로 남아 캡처되는 경우 방어.
                if re.search(r"""[\"'(){}<>;=./]|querySelector|textContent""", cand):
                    continue
                ingredient = cand
                break

        # ── 규격 ────────────────────────────────────────────────────────────
        dosage = ""
        m = re.search(r"(\d+\s*mg(?:/\s*\d+\s*m[lL])?)", text, re.IGNORECASE)
        if m:
            dosage = m.group(1).strip()

        # ── 제형 ────────────────────────────────────────────────────────────
        dosage_form = ""
        for form in ["solution pour perfusion", "solution injectable", "comprimé", "gélule", "poudre"]:
            if form.lower() in text.lower():
                dosage_form = form
                break

        # ── 급여 분류 ───────────────────────────────────────────────────────
        remboursement = ""
        m = re.search(r"(Non remboursé|Liste [I12]+|Remboursé à \d+\s*%)", text, re.IGNORECASE)
        if m:
            remboursement = m.group(1)

        # ── 제조사 ─────────────────────────────────────────────────────────
        company = ""
        m = re.search(r"(?:Fabricant|Laboratoire|Titulaire)\s*[:\s]+([^\n<]{3,60})", text, re.IGNORECASE)
        if m:
            company = m.group(1).strip()

        # ── 로그인 필요 여부 확인 ───────────────────────────────────────────
        login_required = "connectez-vous" in text.lower() or "se connecter" in text.lower()

        # ── 가격 추출 ────────────────────────────────────────────────────────
        # 우선순위 1: **UCD 병원가** "... par UCD : N euros" (HT, ex-VAT). 병원용 주사제
        #   (keytruda 등)는 BDPM 공시가 없고 이 UCD 가격이 정답 — 공개, 3자리 소수 표기
        #   (예 "2000,740 euros" = €2000.74). HT 가에 FR 기본 ratio 0.77 적용(실효값, 2026-10).
        # 우선순위 2~: 일반 "X.XXX,XX €" / "Prix : ...".
        price = None
        price_basis = ""
        price_patterns = [
            # UCD 병원가 — "par UCD ... N,NNN euros". 사이 UCD 13자리 코드는 comma/euros 가
            # 없어 자동 배제됨(euros 접미사 앵커). 예 "2000,740 euros" → 2000.74.
            # UCD(unité commune de dispensation) 는 정의상 최소단위(바이알 1개) 단가 → pack_count=1 명시.
            ("ucd", r"par\s*UCD.{0,150}?(\d{1,5}(?:[\s\.]\d{3})*,\d+)\s*euros"),
            ("ucd", r"responsabilit.{0,180}?(\d{1,5}(?:[\s\.]\d{3})*,\d+)\s*euros"),
            ("", r"(\d{1,5}(?:[\s\.]\d{3})*,\d+)\s*euros"),
            ("", r"[Pp]rix\s*(?:HT|TTC)?\s*[:\s]+(\d{1,5}(?:\.\d{3})*,\d{2})\s*€"),
            ("", r"(\d{1,5}(?:\.\d{3})*,\d{2})\s*€(?:\s*(?:TTC|HT))?"),
            ("", r"€\s*(\d{1,5}(?:\.\d{3})*,\d{2})"),
            ("", r"[Pp]rix\s*[:\s]+(\d+[,\.]\d{2})"),
        ]
        for basis, pat in price_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                raw = m.group(1).replace(" ", "").replace(".", "").replace(",", ".")
                try:
                    candidate = float(raw)
                    if 0.01 <= candidate <= 500_000:
                        price = candidate
                        price_basis = basis
                        break
                except ValueError:
                    continue

        # Non remboursé → 비급여 처리
        if "Non remboursé" in (remboursement or "") and price is None:
            logger.info("[FR] Non remboursé — 가격 없음: %s", product_name)

        rec = {
            "product_name":    product_name,
            "ingredient":      ingredient,
            "dosage_strength": dosage,
            "dosage_form":     dosage_form,
            "package_unit":    "",
            "local_price":     price,
            "source_url":      url,
            "extra": {
                "remboursement":   remboursement or "확인 불가",
                "company":         company,
                "login_required":  login_required,
                "source_type":     self.SOURCE_TYPE,
                "price_basis":     price_basis,
            },
        }
        if price is not None and price_basis == "ucd":
            rec["pack_count"] = 1        # UCD 단가 = 최소단위 1개 (에이전트 pack_count_source=scraper)
        return rec

    # ────────────────────────────────────────────────────────────────────────
    # 4) BaseScraper 인터페이스
    # ────────────────────────────────────────────────────────────────────────

    async def search(self, query: str, page=None) -> list[dict]:
        """비동기 인터페이스."""
        links = self._search(query)
        if not links:
            return []

        results = []
        q_lower = query.lower()

        # 쿼리 관련 링크 우선 정렬
        def relevance(link):
            u = link["url"].lower()
            t = link["text"].lower()
            return (0 if q_lower in u or q_lower in t else 1)

        links.sort(key=relevance)

        for link in links[:5]:
            item = self._extract_detail(link["url"])
            if item:
                if not item["product_name"]:
                    item["product_name"] = link["text"]
                results.append(item)
                if item["local_price"] is not None:
                    break

        return results

    async def refresh(self, _page=None) -> None:
        logger.info("[FR] Vidal 실시간 조회 방식")

    async def run(self, query: str) -> list[dict]:
        """
        requests 기반으로 실행 (Playwright 불필요).
        1. 로그인 → 2. 검색 → 3. 상세 페이지 파싱
        """
        logger.info("[FR] Vidal 검색 시작: '%s'", query)
        searched_at = datetime.now().isoformat()

        # 로그인
        if not self._logged_in:
            self._login_requests()

        raw_results = await self.search(query)
        logger.info("[FR] 결과: %d건", len(raw_results))

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
