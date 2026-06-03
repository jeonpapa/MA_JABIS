"""Australia PBS (Pharmaceutical Benefits Scheme) scraper.

검색 진입점:
  https://www.pbs.gov.au/medicine/item/search?term=<drug>

응답 페이지에 PBS item 코드 + listing 상태 + brand_name + indication 노출.
PBAC 권고 결정은 별도 페이지 (/info/industry/listing/elements/pbac-meetings/...) 에 있음.

본 스크레이퍼는 **PBS item 등재 여부** + **listing date** 를 우선 수집.
PBAC 결과는 기존 `australia_pbac.py` 에서 별도 처리 (HTAResult 형태).
이 모듈은 ReimbursementResult (DB 영구 저장 대상) 생성에 집중.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import HTABaseScraper, ReimbursementResult

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class AUPBSScraper(HTABaseScraper):
    COUNTRY  = "AU"
    BODY     = "PBAC"  # 등재 결정은 PBAC, 코드는 PBS schedule
    BASE_URL = "https://www.pbs.gov.au"

    # PBS schedule item 페이지 패턴 — /medicine/item/<code>
    ITEM_HREF_RE = re.compile(r"/medicine/item/([A-Z0-9]+)", re.I)
    DATE_RE = re.compile(r"(\d{1,2})\s+(January|February|March|April|May|June|July|"
                         r"August|September|October|November|December)\s+(\d{4})", re.I)

    def _get(self, url: str) -> Optional[str]:
        try:
            r = requests.get(url, timeout=self.timeout, headers={"User-Agent": UA})
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[PBS] GET 실패 %s: %s", url, e)
            return None

    def search(self, drug: str) -> list:
        """HTABaseScraper 호환 search — 빈 list 반환 (HTAResult 는 PBAC 모듈 담당)."""
        return []

    def search_reimbursement(self, drug: str) -> list[ReimbursementResult]:
        """drug 검색어로 PBS schedule 매칭 + ReimbursementResult 리스트 반환."""
        # PBS 검색은 기본 medicine listings 페이지에 brand search
        url = f"{self.BASE_URL}/medicine/item/search?term={drug}"
        html = self._get(url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        seen_codes: set[str] = set()
        results: list[ReimbursementResult] = []

        # 검색 결과 row 들에서 item code + brand + 분류 추출
        for row in soup.select("a[href*='/medicine/item/']"):
            href = row.get("href", "")
            m = self.ITEM_HREF_RE.search(href)
            if not m:
                continue
            code = m.group(1).upper()
            if code in seen_codes:
                continue
            seen_codes.add(code)

            item_url = urljoin(self.BASE_URL, href)
            r = self._parse_item(drug, item_url, code)
            if r:
                results.append(r)
            if len(results) >= 20:
                break

        return results

    def _parse_item(self, drug: str, url: str, code: str) -> Optional[ReimbursementResult]:
        html = self._get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "lxml")
        title_el = soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else ""

        # drug name 검증 — title 또는 본문에 포함되어야 함
        text_full = soup.get_text(" ", strip=True)
        text_lower = text_full.lower()
        if drug.lower() not in text_lower:
            return None

        # listing date / authority required / restricted benefit 등 status 키워드
        decision_type = "recommend"
        if "restricted benefit" in text_lower or "authority required" in text_lower:
            decision_type = "restrict"
        if "not listed" in text_lower or "delisted" in text_lower:
            decision_type = "not_listed"

        # listing 일자 (출시일 또는 effective_date)
        date_iso = None
        dm = self.DATE_RE.search(text_full)
        if dm:
            from datetime import datetime
            try:
                dt = datetime.strptime(f"{dm.group(1)} {dm.group(2)} {dm.group(3)}", "%d %B %Y")
                date_iso = dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # 적응증 / 조건 텍스트 (~2000자)
        criteria_text = title
        # PBS 페이지에는 "Treatment Phase" / "Clinical criteria" 섹션이 있음
        for h in soup.find_all(["h2", "h3"]):
            ht = h.get_text(strip=True).lower()
            if "criteria" in ht or "indication" in ht or "treatment phase" in ht:
                # 다음 형제 본문 추출
                nxt = h.find_next_sibling()
                if nxt:
                    criteria_text = (criteria_text + " | " + nxt.get_text(" ", strip=True))[:2000]
                    break

        return ReimbursementResult(
            drug_query=drug,
            indication_id=None,
            country="AU",
            body="PBAC",
            decision_type=decision_type,
            decision_id=code,
            decision_date=date_iso,
            effective_date=date_iso,
            criteria_text=criteria_text,
            pbs_code=code,
            source_url=url,
            currency="AUD",
            raw_payload={"title": title, "url": url, "pbs_code": code},
        )


if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--drug", required=True)
    args = ap.parse_args()
    s = AUPBSScraper()
    rs = s.search_reimbursement(args.drug)
    print(f"수집: {len(rs)}건")
    for r in rs[:5]:
        d = r.to_dict()
        # 긴 필드 truncate
        if d.get("criteria_text"):
            d["criteria_text"] = d["criteria_text"][:200]
        print(json.dumps(d, ensure_ascii=False, indent=2))
