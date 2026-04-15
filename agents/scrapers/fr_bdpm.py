"""
프랑스 BDPM / 공개 약가 스크레이퍼

대상:
  1) https://base-donnees-publique.medicaments.gouv.fr/ — ANSM 공개 약품 DB (BDPM)
  2) https://www.vidal.fr/ — Vidal 공개 페이지 (로그인 불필요한 정보)

배경:
  프랑스 처방약 가격 체계:
  - PFHT (Prix Fabricant Hors Taxe): 제조사 출하가 (VAT 제외)
    → CEPS(경제위원회)와 제약사 간 비밀 협상. 항암제 등은 비공개.
  - PPH  (Prix Public Hospitalier): 병원 공급가
  - PPC  (Prix Public Communautaire, 소비자가): PFHT + 유통마진 + TVA(5.5% or 2.1%)
  - 리스트 I / 리스트 II / Non remboursé: 급여 분류

  항암제(병원 전용) 등 LPP/T2A 대상 약제는 PFHT가 공개되지 않음.
  외래 처방약(보조치료제 등)은 JO(Journal Officiel)에 고시.

조회 흐름:
  1) BDPM 검색 → CIS 코드, 성분, 급여율 추출
  2) 가격 시도: Vidal 공개 페이지 → 약국 비교 사이트 (ameli.fr 데이터)
  3) 병원 전용 항암제: local_price=None + 메모 기록 ("PFHT 비공개")

HIRA 조정가:
  source_type="vidal" → factory_ratio = 0.65 (가격이 있는 경우에만 계산)
"""

import logging
import re
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import requests

from .base import BaseScraper

logger = logging.getLogger(__name__)

BDPM_BASE      = "https://base-donnees-publique.medicaments.gouv.fr"
BDPM_SEARCH    = BDPM_BASE + "/index.php"
BDPM_DETAIL    = BDPM_BASE + "/extrait.php"

VIDAL_BASE     = "https://www.vidal.fr"
VIDAL_SEARCH   = VIDAL_BASE + "/recherche.html"

REQUESTS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


class FrBdpmScraper(BaseScraper):
    COUNTRY        = "FR"
    CURRENCY       = "EUR"
    SOURCE_LABEL   = "BDPM/ANSM (프랑스 공개 약품 DB)"
    SOURCE_TYPE    = "vidal"    # HIRA factory_ratio = 0.65 (가격 있을 때)
    REQUIRES_LOGIN = False

    def __init__(self, cache_dir: Path = None, msd_only: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.cache_dir = cache_dir or Path("data/foreign/fr")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.msd_only = msd_only
        self._session = requests.Session()
        self._session.headers.update(REQUESTS_HEADERS)

    # ────────────────────────────────────────────────────────────────────────
    # 로그인 불필요
    # ────────────────────────────────────────────────────────────────────────

    async def login(self, page=None) -> None:
        pass

    async def logout(self, page=None) -> None:
        pass

    # ────────────────────────────────────────────────────────────────────────
    # 1) BDPM 검색
    # ────────────────────────────────────────────────────────────────────────

    def _search_bdpm(self, query: str) -> list[dict]:
        """
        BDPM 약제 검색. CIS 코드 및 기본 정보 반환.
        반환: [{"cis": str, "name": str, "url": str}]
        """
        try:
            r = self._session.get(
                BDPM_SEARCH,
                params={
                    "page": "searchMedic",
                    "search": query,
                    "type_rec": "all",
                },
                timeout=20,
            )
            r.raise_for_status()
        except Exception as e:
            logger.warning("[FR] BDPM 검색 실패: %s", e)
            return []

        html = r.text

        # 약제 링크 추출: /extrait.php?specid=XXXXXXXX
        links = re.findall(r'href="[^"]*extrait\.php\?specid=(\d+)"[^>]*>([^<]+)', html)
        if not links:
            # 대안: CIS 코드 패턴
            links = re.findall(r'specid=(\d+)[^>]*>([^<]+)</a>', html)

        results = []
        seen = set()
        q_lower = query.lower()

        for cis, name in links:
            name = name.strip()
            if cis in seen:
                continue
            if q_lower not in name.lower() and q_lower not in cis:
                continue
            seen.add(cis)
            results.append({
                "cis": cis,
                "name": name,
                "url": f"{BDPM_DETAIL}?specid={cis}",
            })
            if len(results) >= 5:
                break

        logger.info("[FR] BDPM 검색 결과: %d건 (query=%s)", len(results), query)
        return results

    # ────────────────────────────────────────────────────────────────────────
    # 2) BDPM 상세 페이지 파싱
    # ────────────────────────────────────────────────────────────────────────

    def _parse_bdpm_detail(self, url: str) -> Optional[dict]:
        """BDPM 약제 상세 페이지에서 성분·급여율·기본 정보 추출."""
        try:
            r = self._session.get(url, timeout=20)
            r.raise_for_status()
        except Exception as e:
            logger.debug("[FR] BDPM 상세 페이지 실패: %s", e)
            return None

        html = r.text
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)

        # ── 제품명 ─────────────────────────────────────────────────────────
        product_name = ""
        m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
        if m:
            product_name = re.sub(r"<[^>]+>", "", m.group(1)).strip()

        # ── 성분명 (DCI = Dénomination Commune Internationale) ─────────────
        ingredient = ""
        for pat in [
            r"(?:Substance\s+active|DCI|Principe\s+actif)[:\s]+([A-Za-zé]+(?:\s+[A-Za-zé]+)?)",
            r"Pembrolizumab",   # 직접 감지
        ]:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                ingredient = m.group(0) if pat == "Pembrolizumab" else m.group(1).strip()
                break

        # ── 규격/함량 ──────────────────────────────────────────────────────
        dosage = ""
        m = re.search(r"(\d+\s*mg(?:/\s*(?:\d+\s*)?m[lL])?)", text, re.IGNORECASE)
        if m:
            dosage = m.group(1).strip()

        # ── 제형 ────────────────────────────────────────────────────────────
        dosage_form = ""
        for form in ["solution pour perfusion", "poudre pour solution", "comprimé", "gélule", "injectable"]:
            if form.lower() in text.lower():
                dosage_form = form
                break

        # ── 급여율 ─────────────────────────────────────────────────────────
        remboursement = ""
        m = re.search(r"(Non\s+remboursé|[\d]{2,3}\s*%\s*(?:de\s+remboursement)?)", text, re.IGNORECASE)
        if m:
            remboursement = m.group(1).strip()

        # ── 마케팅 현황 ────────────────────────────────────────────────────
        commercialise = "Commercialisée" in text or "COMMERCIALISEE" in text.upper()

        # ── 제조사 ─────────────────────────────────────────────────────────
        company = ""
        m = re.search(r"(?:Laboratoire|Titulaire)[:\s]+([A-Z][^\n<]{3,60})", text, re.IGNORECASE)
        if m:
            company = m.group(1).strip()

        # ── 가격: BDPM에는 직접 가격 없음 — Vidal 공개 페이지 시도 ──────────
        price = None
        price_note = ""

        # 항암제 / 병원 전용 여부 확인
        is_hospital_only = any(kw in text.lower() for kw in [
            "réservé à l'usage hospitalier", "usage hospitalier",
            "liste rétrocession", "atc: l01",   # L01 = 항암제 ATC 코드
        ])

        if is_hospital_only or (not remboursement or "Non remboursé" in remboursement):
            price_note = "PFHT 비공개 (병원 전용 항암제 또는 비급여)"
            logger.info("[FR] %s — 가격 비공개 (%s)", product_name, price_note)
        else:
            # 외래 급여약: Vidal 공개 페이지에서 가격 시도
            price, price_note = self._try_vidal_price(product_name or "")
            if price is None:
                price_note = "공개 약가 없음 (전문가 DB 필요)"

        return {
            "product_name":    product_name,
            "ingredient":      ingredient,
            "dosage_strength": dosage,
            "dosage_form":     dosage_form,
            "package_unit":    "",
            "local_price":     price,
            "source_url":      url,
            "extra": {
                "company":          company,
                "remboursement":    remboursement or "확인 불가",
                "commercialise":    commercialise,
                "price_note":       price_note,
                "source_type":      self.SOURCE_TYPE,
                "is_hospital_only": is_hospital_only,
            },
        }

    # ────────────────────────────────────────────────────────────────────────
    # 3) Vidal 공개 페이지 가격 추출 시도
    # ────────────────────────────────────────────────────────────────────────

    def _try_vidal_price(self, product_name: str) -> tuple[Optional[float], str]:
        """
        Vidal 공개 검색에서 가격 정보 추출 시도.
        로그인 없이 공개된 정보만 파싱.
        반환: (price_or_None, note)
        """
        if not product_name:
            return None, "제품명 없음"
        try:
            r = self._session.get(
                VIDAL_SEARCH,
                params={"query": product_name},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            logger.debug("[FR] Vidal 검색 실패: %s", e)
            return None, f"Vidal 접근 오류: {e}"

        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)

        price_patterns = [
            r"Prix[:\s]+(\d{1,4}(?:[,\.]\d{2})?)\s*€",
            r"(\d{1,4}(?:[,\.]\d{2})?)\s*€(?:\s+TTC)?",
            r"€\s*(\d{1,4}(?:[,\.]\d{2})?)",
        ]
        for pat in price_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                raw = m.group(1).replace(",", ".")
                try:
                    price = float(raw)
                    if 0.01 <= price <= 100_000:
                        return price, "Vidal 공개 페이지"
                except ValueError:
                    continue

        return None, "가격 정보 로그인 필요 (Vidal 전문가 전용)"

    # ────────────────────────────────────────────────────────────────────────
    # 4) BaseScraper 인터페이스
    # ────────────────────────────────────────────────────────────────────────

    async def search(self, query: str, page=None) -> list[dict]:
        """비동기 인터페이스 — 내부적으로 동기 requests 사용."""
        search_results = self._search_bdpm(query)
        if not search_results:
            logger.info("[FR] '%s' BDPM 검색 결과 없음", query)
            return []

        results = []
        for item in search_results[:3]:
            detail = self._parse_bdpm_detail(item["url"])
            if detail:
                if not detail.get("product_name"):
                    detail["product_name"] = item["name"]
                results.append(detail)
                # 가격이 있으면 첫 번째로 충분
                if detail.get("local_price") is not None:
                    break

        return results

    async def refresh(self, _page=None) -> None:
        logger.info("[FR] BDPM 실시간 조회 방식")

    async def run(self, query: str) -> list[dict]:
        """
        Playwright 없이 requests만으로 실행.
        BaseScraper.run()을 오버라이드.
        """
        logger.info("[FR] BDPM 검색 시작: '%s'", query)
        searched_at = datetime.now().isoformat()

        raw_results = await self.search(query)
        logger.info("[FR] 결과: %d건", len(raw_results))

        if not raw_results:
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
                "local_price":         item.get("local_price"),  # None if hospital drug
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
