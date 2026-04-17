"""NICE (England & Wales) scraper.

진입점:
  https://www.nice.org.uk/search?q=<drug>

검색 결과에서 `/guidance/TA\d+` 링크를 추출하고 각 TA 페이지를 파싱.

TA 페이지 구조:
  - <h1>: 제목
  - "/resources/...pdf": 가이던스 PDF
  - "published: DD Month YYYY" 또는 meta 에 발행일
  - /chapter/1-recommendations 에 권고 본문 (간단 파싱은 landing 의 summary 로 대체)
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


class UKNICEScraper(HTABaseScraper):
    COUNTRY  = "UK"
    BODY     = "NICE"
    BASE_URL = "https://www.nice.org.uk"

    TA_HREF_RE = re.compile(r'href="(/guidance/(TA|HST)\d+)"', re.I)
    DATE_RE = re.compile(
        r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{4})", re.I,
    )

    def _get(self, url: str) -> Optional[str]:
        try:
            r = requests.get(url, timeout=self.timeout, headers={"User-Agent": UA})
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[NICE] GET 실패 %s: %s", url, e)
            return None

    def search(self, drug: str) -> list[HTAResult]:
        html = self._get(f"{self.BASE_URL}/search?q={drug}")
        if not html:
            return []
        tas = sorted({m.group(1) for m in self.TA_HREF_RE.finditer(html)})
        out: list[HTAResult] = []
        for ta in tas:
            r = self._parse_ta(drug, urljoin(self.BASE_URL, ta))
            if r:
                out.append(r)
        return self._keep_latest(out)

    # Recommendation keyword heuristic (랜딩/권고 페이지 텍스트 기반)
    RECO_MAP = [
        ("terminated appraisal",                   "Terminated"),
        ("not recommended",                        "Not recommended"),
        ("recommended within the cancer drugs fund", "Recommended (Cancer Drugs Fund)"),
        ("recommended with managed access",        "Recommended (managed access)"),
        ("recommended, with managed access",       "Recommended (managed access)"),
        ("recommended as an option",               "Recommended"),
        ("is recommended",                         "Recommended"),
    ]

    def _parse_ta(self, drug: str, url: str) -> Optional[HTAResult]:
        html = self._get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "lxml")

        title_el = soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else url.rsplit("/", 1)[-1]

        # drug 가 제목에 들어있지 않으면 스킵 (검색이 가이드라인 리스트에 섞여 들어올 수 있음)
        if drug.lower() not in title.lower() and drug.lower() not in html.lower()[:5000]:
            return None

        # 권고 페이지 텍스트 + landing 텍스트 합쳐서 결정 분류
        reco_html = self._get(url + "/chapter/1-recommendations") or ""
        combined = BeautifulSoup(html + "\n" + reco_html, "lxml").get_text(" ", strip=True).lower()

        decision = "Unknown"
        for needle, label in self.RECO_MAP:
            if needle.lower() in combined:
                decision = label
                break

        # 지시문 (adult ... treatment ... 문장 추출)
        indication = ""
        m_ind = re.search(
            r"(?:treating|treatment of|for treating|for the treatment of)\s+[^.]{20,300}\.",
            combined, re.I,
        )
        if m_ind:
            indication = m_ind.group(0)[:500].strip()

        # 발행일
        date_iso = None
        dm = self.DATE_RE.search(BeautifulSoup(html, "lxml").get_text(" ", strip=True))
        if dm:
            from datetime import datetime
            try:
                dt = datetime.strptime(f"{dm.group(1)} {dm.group(2)} {dm.group(3)}", "%d %B %Y")
                date_iso = dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # PDF 링크
        pdf_url = None
        for a in soup.select('a[href*="/resources/"]'):
            h = a.get("href", "")
            if h.endswith(".pdf") or "-pdf-" in h:
                pdf_url = urljoin(self.BASE_URL, h)
                break

        pdf_local = None
        if pdf_url:
            fname = pdf_url.rsplit("/", 1)[-1].split("?")[0]
            if not fname.endswith(".pdf"):
                fname = fname + ".pdf"
            p = self.download_pdf(pdf_url, fname)
            if p:
                pdf_local = str(p)

        ta_id = url.rsplit("/", 1)[-1]
        return HTAResult(
            drug_query=drug,
            country=self.COUNTRY,
            body=self.BODY,
            title=title,
            indication=indication,
            decision=decision,
            decision_date=date_iso,
            detail_url=url,
            pdf_url=pdf_url,
            pdf_local=pdf_local,
            extra={"nice_id": ta_id},
        )


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "belzutifan"
    print(json.dumps([r.to_dict() for r in UKNICEScraper().search(drug)], ensure_ascii=False, indent=2))
