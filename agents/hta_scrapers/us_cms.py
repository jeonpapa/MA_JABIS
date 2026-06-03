"""US CMS (Centers for Medicare & Medicaid Services) NCD/LCD scraper.

진입점:
  https://www.cms.gov/medicare-coverage-database/search.aspx?keyword=<inn>

Drug-specific NCD/LCD 는 적은 편 — 항암제는 대체로 NCCN compendium 기반 covered.
없으면 `decision_type='not_applicable'` row 로 명시 기록 (UI 가 "비대상" 안내 가능).

NCD 페이지 구조:
  - <h1>: NCD 제목 + 번호 (예: "NCD - PET Scans (220.6)")
  - 본문에 effective date + scope
  - PDF 직링크 (선택)
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

from .base import HTABaseScraper, ReimbursementResult

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class USCMSScraper(HTABaseScraper):
    COUNTRY  = "US"
    BODY     = "CMS"
    BASE_URL = "https://www.cms.gov"
    SEARCH_URL = "https://www.cms.gov/medicare-coverage-database/search.aspx"

    # 검색 결과 NCD 링크 패턴
    NCD_HREF_RE = re.compile(r'href="(/medicare-coverage-database/view/ncd[^"]+)"', re.I)
    NCD_ID_RE = re.compile(r"\(([\d.]+)\)")
    DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")

    def _get(self, url: str) -> Optional[str]:
        try:
            r = requests.get(url, timeout=self.timeout, headers={"User-Agent": UA})
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[CMS] GET 실패 %s: %s", url, e)
            return None

    def search(self, drug: str) -> list:
        """HTABaseScraper 호환 — 빈 list."""
        return []

    def search_reimbursement(self, drug: str) -> list[ReimbursementResult]:
        """
        CMS NCD 검색. drug-specific NCD 가 매칭되면 ReimbursementResult.
        매칭 0건이면 `not_applicable` 명시 row 1건 반환 (UI 가 "Medicare 일반 covered" 안내).
        """
        params = f"?keyword={quote(drug)}&searchType=ncd"
        url = self.SEARCH_URL + params
        html = self._get(url)
        if not html:
            return [self._make_not_applicable(drug, url)]

        ncd_paths = sorted({m.group(1) for m in self.NCD_HREF_RE.finditer(html)})
        if not ncd_paths:
            return [self._make_not_applicable(drug, url)]

        results: list[ReimbursementResult] = []
        for path in ncd_paths[:10]:
            ncd_url = urljoin(self.BASE_URL, path)
            r = self._parse_ncd(drug, ncd_url)
            if r:
                results.append(r)
        if not results:
            return [self._make_not_applicable(drug, url)]
        return results

    def _make_not_applicable(self, drug: str, source_url: str) -> ReimbursementResult:
        """NCD 미매칭 — Medicare 는 NCCN compendium 기반으로 대부분 covered. 명시 row."""
        return ReimbursementResult(
            drug_query=drug,
            indication_id=None,
            country="US",
            body="CMS",
            decision_type="not_applicable",
            decision_id=None,
            decision_date=None,
            effective_date=None,
            criteria_text=(
                "No drug-specific NCD found. Medicare Part B/D coverage typically "
                "follows NCCN compendium and Medicare Advantage/PDP plan formularies. "
                "Drug-specific reimbursement decisions are made at MAC (LCD) or plan level."
            ),
            source_url=source_url,
            currency="USD",
            raw_payload={"search_url": source_url, "ncd_match": False},
        )

    def _parse_ncd(self, drug: str, url: str) -> Optional[ReimbursementResult]:
        html = self._get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "lxml")
        title_el = soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else ""
        body_text = soup.get_text(" ", strip=True)

        # drug 가 NCD 본문에 있어야 의미있음
        if drug.lower() not in body_text.lower():
            return None

        # NCD 번호 (예: 220.6)
        ncd_id = None
        m = self.NCD_ID_RE.search(title)
        if m:
            ncd_id = "NCD-" + m.group(1)

        # 본문에 covered/non-covered 결정
        body_lower = body_text.lower()
        if "non-covered" in body_lower or "not covered" in body_lower:
            decision_type = "reject"
        elif "covered" in body_lower or "national coverage" in body_lower:
            decision_type = "recommend"
        else:
            decision_type = "not_applicable"

        # effective date (MM/DD/YYYY → ISO)
        date_iso = None
        dm = self.DATE_RE.search(body_text)
        if dm:
            try:
                date_iso = f"{dm.group(3)}-{int(dm.group(1)):02d}-{int(dm.group(2)):02d}"
            except (ValueError, TypeError):
                pass

        criteria = title + " | " + body_text[:1500]
        return ReimbursementResult(
            drug_query=drug,
            indication_id=None,
            country="US",
            body="CMS",
            decision_type=decision_type,
            decision_id=ncd_id,
            decision_date=date_iso,
            effective_date=date_iso,
            criteria_text=criteria[:2000],
            source_url=url,
            currency="USD",
            raw_payload={"title": title, "url": url, "ncd_id": ncd_id},
        )


if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--drug", required=True)
    args = ap.parse_args()
    s = USCMSScraper()
    rs = s.search_reimbursement(args.drug)
    print(f"수집: {len(rs)}건")
    for r in rs[:5]:
        d = r.to_dict()
        if d.get("criteria_text"):
            d["criteria_text"] = d["criteria_text"][:200]
        print(json.dumps(d, ensure_ascii=False, indent=2))
