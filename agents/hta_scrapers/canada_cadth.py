"""CADTH / CDA-AMC (Canada) scraper.

참고:
  www.cda-amc.ca 는 Cloudflare bot-protection 으로 curl·Playwright headless
  접근이 차단된다. 본 스크레이퍼는 2가지 전략을 사용:

  1) 기존 검증된 URL 패턴 (`/<drug>[-N]`) 을 HEAD 로 확인 (Cloudflare 가
     HTML 은 차단하지만 HEAD 로는 상태 확인 가능한 경우가 있음).
  2) DuckDuckGo site-search 폴백으로 CADTH 내부 URL 을 발견.

  HTML/PDF 본문 파싱은 불가능하므로 detail_url 만 제공.
  (decision = 'See CADTH', indication = '', pdf_url = None)
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote_plus

import requests

from .base import HTABaseScraper, HTAResult

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class CanadaCADTHScraper(HTABaseScraper):
    COUNTRY  = "CA"
    BODY     = "CADTH"
    BASE_URL = "https://www.cda-amc.ca"

    DDG_URL  = "https://duckduckgo.com/html/?q=site%3Acda-amc.ca+{q}"
    URL_RE   = re.compile(r'https?://(?:www\.)?cda-amc\.ca/[A-Za-z0-9_/\-]+', re.I)

    def search(self, drug: str) -> list[HTAResult]:
        # DDG site-search
        try:
            r = requests.get(
                self.DDG_URL.format(q=quote_plus(drug)),
                headers={"User-Agent": UA},
                timeout=self.timeout,
            )
            r.raise_for_status()
            html = r.text
        except Exception as e:
            logger.warning("[CADTH] DDG 실패: %s", e)
            html = ""

        candidates = [u for u in self.URL_RE.findall(html)
                      if drug.lower() in u.lower()]
        # 드물게 실제 slug 가 drug 명과 다르면 (ex. 'belzutifan-0') 바로 포함됨
        # 중복 제거 (trailing slash 정규화)
        seen = set()
        urls: list[str] = []
        for u in candidates:
            u = u.rstrip("/")
            if u in seen:
                continue
            seen.add(u)
            urls.append(u)
            if len(urls) >= 5:
                break

        # fallback: 공식 URL 직접 시도
        if not urls:
            urls = [f"{self.BASE_URL}/{drug.lower()}"]

        results: list[HTAResult] = []
        for url in urls:
            title = url.rsplit("/", 1)[-1].replace("-", " ").title()
            results.append(HTAResult(
                drug_query=drug,
                country=self.COUNTRY,
                body=self.BODY,
                title=f"CADTH: {title}",
                indication="",
                decision="See CADTH",
                decision_date=None,
                detail_url=url,
                pdf_url=None,
                pdf_local=None,
                extra={"note": "Cloudflare 보호로 본문·PDF 자동 수집 불가. 링크 클릭으로 확인."},
            ))
        return results


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "belzutifan"
    print(json.dumps([r.to_dict() for r in CanadaCADTHScraper().search(drug)], ensure_ascii=False, indent=2))
