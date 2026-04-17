"""PBAC (Australia) scraper.

진입점:
  https://www.pbs.gov.au/info/industry/listing/elements/pbac-meetings/psd/
      public-summary-documents-by-product
  → 약제명 기반 href 탐색 (`/psd/YYYY-MM/<drug>-PSD-<Month-YYYY>`)

각 landing page 에서:
  - 제목 (h1 또는 <title>)
  - 회의일 (URL YYYY-MM → YYYY-MM-01)
  - PDF 링크 (`/psd/.../*.pdf`)

권고 결정 본문은 PSD PDF 안에 있어 본 스크레이퍼는 'See PSD' 로 표기.
추후 PDF 파싱으로 확장 가능.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import HTABaseScraper, HTAResult

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

INDEX_URL = ("https://www.pbs.gov.au/info/industry/listing/elements/"
             "pbac-meetings/psd/public-summary-documents-by-product")


class AustraliaPBACScraper(HTABaseScraper):
    COUNTRY  = "AU"
    BODY     = "PBAC"
    BASE_URL = "https://www.pbs.gov.au"

    _index_cache_html: Optional[str] = None

    def _get(self, url: str) -> Optional[str]:
        try:
            r = requests.get(url, timeout=self.timeout, headers={"User-Agent": UA})
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[PBAC] GET 실패 %s: %s", url, e)
            return None

    def _index(self) -> str:
        if not AustraliaPBACScraper._index_cache_html:
            AustraliaPBACScraper._index_cache_html = self._get(INDEX_URL) or ""
        return AustraliaPBACScraper._index_cache_html

    MEETING_RE = re.compile(r"/psd/(\d{4})-(\d{2})/", re.I)

    def search(self, drug: str) -> list[HTAResult]:
        idx_html = self._index()
        if not idx_html:
            return []
        pat = re.compile(
            r'href="(/info/industry/listing/elements/pbac-meetings/psd/'
            r'\d{4}-\d{2}/[^"]*' + re.escape(drug) + r'[^"]*)"',
            re.I,
        )
        hrefs = sorted(set(pat.findall(idx_html)))
        return [r for r in (self._parse_landing(drug, h) for h in hrefs) if r]

    def _parse_landing(self, drug: str, href: str) -> Optional[HTAResult]:
        url = urljoin(self.BASE_URL, href)
        html = self._get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "lxml")
        title_el = soup.find("h1") or soup.find("title")
        title = title_el.get_text(strip=True) if title_el else drug

        # Meeting date YYYY-MM-01
        mm = self.MEETING_RE.search(href)
        decision_date = f"{mm.group(1)}-{mm.group(2)}-01" if mm else None

        # PDF link
        pdf_url = None
        for a in soup.select('a[href$=".pdf"]'):
            h = a.get("href", "")
            if "psd" in h.lower():
                pdf_url = urljoin(self.BASE_URL, h)
                break

        pdf_local = None
        if pdf_url:
            fname = pdf_url.rsplit("/", 1)[-1]
            p = self.download_pdf(pdf_url, fname)
            if p:
                pdf_local = str(p)

        return HTAResult(
            drug_query=drug,
            country=self.COUNTRY,
            body=self.BODY,
            title=title,
            indication="",  # PSD PDF 내부에 있음
            decision="See PSD",
            decision_date=decision_date,
            detail_url=url,
            pdf_url=pdf_url,
            pdf_local=pdf_local,
            extra={"meeting": f"{mm.group(1)}-{mm.group(2)}" if mm else None},
        )


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "belzutifan"
    rs = AustraliaPBACScraper().search(drug)
    print(json.dumps([r.to_dict() for r in rs], ensure_ascii=False, indent=2))
