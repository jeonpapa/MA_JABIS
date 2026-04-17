"""Scotland SMC (Scottish Medicines Consortium) scraper.

검색 엔드포인트:
  https://www.scottishmedicines.org.uk/search/?keywords=<drug>

상세 페이지 패턴:
  /medicines-advice/<drug-slug>-smcNNNN/

상세 페이지에서 추출:
  - SMC ID
  - 결정(Accepted / Not recommended / Non-submission)
  - 적응증 (Indication 섹션)
  - 평가 PDF 링크 (/media/NNNN/...for-website.pdf)
  - 결정일
"""
from __future__ import annotations

import re
from urllib.parse import urljoin
import logging

import requests
from bs4 import BeautifulSoup

from .base import HTABaseScraper, HTAResult

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class ScotlandSMCScraper(HTABaseScraper):
    COUNTRY  = "SCT"
    BODY     = "SMC"
    BASE_URL = "https://www.scottishmedicines.org.uk"

    def _get(self, url: str) -> Optional[str]:  # type: ignore[name-defined]
        try:
            r = requests.get(url, timeout=self.timeout, headers={"User-Agent": UA})
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("[SMC] GET 실패 %s: %s", url, e)
            return None

    def search(self, drug: str) -> list[HTAResult]:
        html = self._get(f"{self.BASE_URL}/search/?keywords={drug}")
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        # 검색 결과 a[href*=/medicines-advice/]
        seen = set()
        urls: list[str] = []
        for a in soup.select('a[href*="/medicines-advice/"]'):
            href = a.get("href", "")
            if not href or "/medicines-advice/" not in href or href.endswith("/medicines-advice/"):
                continue
            full = urljoin(self.BASE_URL, href)
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)

        results: list[HTAResult] = []
        for url in urls:
            r = self._parse_detail(drug, url)
            if r:
                results.append(r)
        # 같은 적응증/결정에 대해 최신 것만 유지
        return self._keep_latest(results)

    # ──────────────────────────────────────────────
    SMC_ID_RE = re.compile(r"smc(\d+)", re.I)
    DATE_RE   = re.compile(
        r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(\d{4})", re.I,
    )

    DECISION_KEYWORDS = [
        ("Accepted for restricted use",      "Accepted (restricted)"),
        ("Accepted for use",                 "Accepted"),
        ("Not recommended",                  "Not recommended"),
        ("Not recommended for use",          "Not recommended"),
        ("non-submission",                   "Non-submission"),
        ("Non-submission",                   "Non-submission"),
    ]

    def _parse_detail(self, drug: str, url: str) -> Optional[HTAResult]:
        html = self._get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "lxml")

        title_el = soup.find(["h1", "h2"])
        title = title_el.get_text(strip=True) if title_el else url.rsplit("/", 2)[-2]

        text = soup.get_text("\n", strip=True)

        # SMC ID
        m = self.SMC_ID_RE.search(url) or self.SMC_ID_RE.search(text)
        smc_id = f"SMC{m.group(1)}" if m else ""

        # Decision
        decision = ""
        for needle, label in self.DECISION_KEYWORDS:
            if needle.lower() in text.lower():
                decision = label
                break

        # Indication: "indication" 키워드 다음 문단
        indication = ""
        idx = text.lower().find("indication")
        if idx >= 0:
            window = text[idx: idx + 800]
            # 첫 줄(헤더) 이후 첫 의미있는 문단 추출
            lines = [ln.strip() for ln in window.split("\n") if ln.strip()]
            if len(lines) >= 2:
                indication = lines[1][:600]

        # Decision date
        date_iso = None
        dm = self.DATE_RE.search(text)
        if dm:
            from datetime import datetime
            try:
                dt = datetime.strptime(f"{dm.group(1)} {dm.group(2)} {dm.group(3)}", "%d %B %Y")
                date_iso = dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        # PDF link
        pdf_url = None
        for a in soup.select('a[href*="/media/"][href$=".pdf"]'):
            pdf_url = urljoin(self.BASE_URL, a["href"])
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
            indication=indication,
            decision=decision or "Unknown",
            decision_date=date_iso,
            detail_url=url,
            pdf_url=pdf_url,
            pdf_local=pdf_local,
            extra={"smc_id": smc_id},
        )


# CLI 테스트
if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "belzutifan"
    rs = ScotlandSMCScraper().search(drug)
    print(json.dumps([r.to_dict() for r in rs], ensure_ascii=False, indent=2))
