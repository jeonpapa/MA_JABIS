"""Japan 中央社会保険医療協議会 (Chuikyo / Central Social Insurance Medical Council) scraper.

후생노동성이 中医協 답신을 통해 의약품 보험수재를 결정. 수재 의약품은 약가기준
(yakka kijun) 에 등재되어 보험수가가 부여됨.

진입점:
  - 약가기준 검색: https://shinryohoshu.mhlw.go.jp/shinryohoshu/searchMenu/yakkaSearch
  - 후생노동성 中医協 의사록: https://www.mhlw.go.jp/stf/shingi2/0000128153.html

수재 약가기준은 PMDA 와 연동 — 본 스크레이퍼는 약가기준 등재 여부 + 약가
(NHI price) + 효력일을 ReimbursementResult 로 변환.

PMDA YJ 코드를 brand → INN 매핑에 활용. PMDA 시 jp_pmda.py 의 download 패턴 재사용.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .base import HTABaseScraper, ReimbursementResult

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class JPChuikyoScraper(HTABaseScraper):
    COUNTRY = "JP"
    BODY    = "CHUIKYO"
    BASE_URL = "https://shinryohoshu.mhlw.go.jp"
    SEARCH_URL = BASE_URL + "/shinryohoshu/searchMenu/yakkaSearch"

    # YJ 코드 패턴
    YJ_RE = re.compile(r"YJ\d{12}")
    PRICE_RE = re.compile(r"([\d,]+\.\d+)\s*円")
    DATE_RE = re.compile(r"(\d{4})[年./-](\d{1,2})[月./-](\d{1,2})")

    def _get(self, url: str, params: dict | None = None) -> Optional[str]:
        try:
            r = requests.get(url, params=params, timeout=self.timeout,
                             headers={"User-Agent": UA})
            r.raise_for_status()
            r.encoding = r.apparent_encoding
            return r.text
        except Exception as e:
            logger.warning("[CHUIKYO] GET 실패 %s: %s", url, e)
            return None

    def search(self, drug: str) -> list:
        """HTABaseScraper 호환 — 빈 list."""
        return []

    def search_reimbursement(self, drug: str) -> list[ReimbursementResult]:
        """
        약가기준 검색 → 매칭 의약품의 NHI price + 등재일 → ReimbursementResult.
        drug 는 INN 또는 brand (kana 변환은 product_alias_map 의 agency_brand_overrides
        에서 PMDA 키 활용). 미매칭 시 not_listed row.
        """
        # 약가기준 검색 페이지는 form POST 기반이라 단순 GET 검색이 일부 제한적.
        # 1차: PMDA 의약품정보 DB 의 brand 검색을 통해 YJ 코드 확보 후
        # 2차: 약가기준 페이지에서 YJ 매칭 row 확인.
        # 본 구현은 NHI price 가 있는 경우 recommend, 없으면 not_listed.
        url = f"{self.SEARCH_URL}?keyword={quote(drug)}"
        html = self._get(url)
        if not html:
            return [self._make_not_listed(drug, url)]

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        # YJ 코드 추출 + NHI price 매칭
        yj_codes = self.YJ_RE.findall(text)
        prices = self.PRICE_RE.findall(text)

        if not yj_codes:
            return [self._make_not_listed(drug, url)]

        # 등재일 검출
        date_iso = None
        dm = self.DATE_RE.search(text)
        if dm:
            try:
                date_iso = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
            except (ValueError, TypeError):
                pass

        # NHI price (¥) → JPY
        nhi_price = None
        if prices:
            try:
                nhi_price = float(prices[0].replace(",", ""))
            except ValueError:
                pass

        # 첫 YJ 코드 기준 1건 row (YJ 별 분리는 PMDA 와 연동 필요 — 현재는 brand=대표 1건)
        primary_yj = yj_codes[0]
        criteria = (f"NHI 약가기준 등재 (YJ코드 {primary_yj}). "
                    + (f"NHI price ¥{nhi_price:,.2f}. " if nhi_price else "")
                    + (f"등재일 {date_iso}. " if date_iso else ""))

        return [ReimbursementResult(
            drug_query=drug,
            indication_id=None,
            country="JP",
            body="CHUIKYO",
            decision_type="recommend",
            decision_id=primary_yj,
            decision_date=date_iso,
            effective_date=date_iso,
            criteria_text=criteria[:2000],
            nhs_list_price=nhi_price,
            currency="JPY",
            source_url=url,
            raw_payload={"yj_codes": yj_codes[:5], "search_url": url},
        )]

    def _make_not_listed(self, drug: str, source_url: str) -> ReimbursementResult:
        return ReimbursementResult(
            drug_query=drug,
            indication_id=None,
            country="JP",
            body="CHUIKYO",
            decision_type="not_listed",
            decision_id=None,
            decision_date=None,
            effective_date=None,
            criteria_text=(
                "약가기준 검색에 매칭 없음. PMDA 승인 후 中医協 답신을 거쳐 등재 절차 "
                "진행 중일 수 있음. PMDA 의약품정보 DB 와 교차 확인 권장."
            ),
            currency="JPY",
            source_url=source_url,
            raw_payload={"search_url": source_url, "yj_match": False},
        )


if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--drug", required=True)
    args = ap.parse_args()
    s = JPChuikyoScraper()
    rs = s.search_reimbursement(args.drug)
    print(f"수집: {len(rs)}건")
    for r in rs[:5]:
        d = r.to_dict()
        if d.get("criteria_text"):
            d["criteria_text"] = d["criteria_text"][:200]
        print(json.dumps(d, ensure_ascii=False, indent=2))
