"""KR MFDS (식약처) scraper — 의약품안전나라 허가사항 효능·효과.

데이터 소스:
  https://nedrug.mfds.go.kr/searchDrug              ← 검색 (GET, itemName 파라미터)
  https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq=<seq>  ← 상세

효능·효과 구조:
  <disease header>          (예: "흑색종", "비소세포폐암")
  1. indication 1
  2. indication 2
  <다음 disease header>
  indication (번호 없이 단일)

FDA 1.x 또는 EMA SmPC 4.1 과 동일한 구조이나 한국어.
LLM 이 한국어 본문에서 anchor 를 추출 (Gemini 다국어).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

NEDRUG_BASE = "https://nedrug.mfds.go.kr"
SEARCH_URL = NEDRUG_BASE + "/searchDrug"
DETAIL_URL = NEDRUG_BASE + "/pbp/CCBBB01/getItemDetail"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"

# product_slug → itemSeq 기본 매핑 (검증된 단일제)
MFDS_ITEM_SEQ: dict[str, str] = {
    "keytruda":  "201501487",   # 키트루다주(펨브롤리주맙,유전자재조합)
    "welireg":   "202301643",   # 웰리렉정(벨주티판)
    "lynparza":  "201907537",   # 린파자정100밀리그램(올라파립)
    "lenvima":   "201507057",   # 렌비마캡슐10밀리그램(렌바티닙메실산염)
}

# 런타임 자동 조회 결과 캐시. 신규 product 는 여기에 누적되어 재조회 비용 없이 재사용.
_CACHE_FILE = Path(__file__).resolve().parents[2] / "data" / "db" / "mfds_item_seq_cache.json"


def _load_itemseq_cache() -> dict[str, str]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[MFDS] item_seq cache read 실패: %s", e)
        return {}


def _save_itemseq_cache(cache: dict[str, str]) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def resolve_item_seq(
    product_slug: str,
    candidates: Optional[list[str]] = None,
    scraper: Optional["KRMFDSScraper"] = None,
) -> Optional[str]:
    """product_slug → itemSeq 자동 조회 체인.

    우선순위: MFDS_ITEM_SEQ (하드코딩) → 런타임 캐시 → nedrug 검색.
    candidates 는 검색에 사용할 대안 문자열 (예: 한국어 brand 명, 성분명).
    성공 시 캐시에 저장한다.
    """
    slug = (product_slug or "").strip().lower()
    if not slug:
        return None

    seq = MFDS_ITEM_SEQ.get(slug)
    if seq:
        return seq

    cache = _load_itemseq_cache()
    seq = cache.get(slug)
    if seq:
        return seq

    sc = scraper or KRMFDSScraper()
    queries: list[str] = []
    for q in (candidates or []):
        if q and q not in queries:
            queries.append(q)
    if product_slug not in queries:
        queries.append(product_slug)

    for q in queries:
        found = sc._search_item_seq(q)
        if found:
            cache[slug] = found
            _save_itemseq_cache(cache)
            logger.info("[MFDS] %s → itemSeq=%s (검색어='%s', 캐시 저장)",
                        slug, found, q)
            return found

    logger.warning("[MFDS] %s itemSeq 자동조회 실패 (시도 검색어: %s)",
                   slug, queries)
    return None


@dataclass
class MFDSIndication:
    code:  str           # "mfds_1", "mfds_2", ... (합성)
    label: str           # disease header (예: "흑색종", "비소세포폐암")
    body:  str           # 한국어 본문
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "body": self.body,
                "keywords": self.keywords}


@dataclass
class MFDSRecord:
    drug:            str
    brand:           str
    item_seq:        Optional[str]
    detail_url:      Optional[str]
    permit_date:     Optional[str]     # YYYY-MM-DD
    indications:     list[MFDSIndication]
    raw_section:     str = ""

    def to_dict(self) -> dict:
        return {
            "drug": self.drug,
            "brand": self.brand,
            "item_seq": self.item_seq,
            "detail_url": self.detail_url,
            "permit_date": self.permit_date,
            "indications": [i.to_dict() for i in self.indications],
            "raw_section": self.raw_section[:3000],
        }


# ─── 효능·효과 추출 + 적응증 분리 ────────────────────────────────────────────

def _extract_ee_doc(html: str) -> str:
    """상세 페이지 HTML 에서 효능·효과(_ee_doc div) 텍스트 추출."""
    soup = BeautifulSoup(html, "html.parser")
    ee = soup.find(id="_ee_doc")
    if not ee:
        logger.warning("[MFDS] _ee_doc 요소를 찾지 못함")
        return ""
    return ee.get_text("\n", strip=True)


def _looks_like_disease_header(line: str) -> bool:
    """한 줄이 질환 헤더인지 판정.

    특징:
      - 짧음 (<=80자)
      - 숫자로 시작하지 않음 (numbered sub-indication 아님)
      - 마침표/쉼표로 끝나지 않음
      - 한글 질환명 키워드 포함
    """
    if not line or len(line) > 80:
        return False
    if re.match(r"^\d+\.", line):
        return False
    if line.endswith((".", "。", ",")):
        return False
    if re.match(r"^⦁", line):
        return False
    words = line.strip()
    if len(words) < 2:
        return False
    disease_kws = (
        "암", "종", "림프종", "백혈병", "골수종", "중피종",
        "MSI-H", "TMB-H", "고빈도", "삼중음성",
    )
    return any(kw in words for kw in disease_kws) and len(words) <= 60


def _split_indications(ee_text: str) -> list[MFDSIndication]:
    """효능·효과 텍스트를 (disease, body) 블록으로 분리."""
    if not ee_text:
        return []

    lines = [l.strip() for l in ee_text.split("\n") if l.strip()]

    blocks: list[tuple[str, str]] = []
    current_disease = ""
    body_lines: list[str] = []

    def flush():
        if body_lines:
            body = " ".join(body_lines)
            if body:
                blocks.append((current_disease, body))

    for line in lines:
        if _looks_like_disease_header(line):
            flush()
            body_lines = []
            current_disease = line
        elif re.match(r"^\d+\.", line):
            flush()
            body_lines = [line]
        elif body_lines:
            body_lines.append(line)
        else:
            body_lines = [line]

    flush()

    result: list[MFDSIndication] = []
    for idx, (disease, body) in enumerate(blocks, 1):
        label = disease or body[:60]
        result.append(MFDSIndication(
            code=f"mfds_{idx}",
            label=label[:200],
            body=body[:2000],
            keywords=[],
        ))
    return result


def _extract_permit_date(html: str) -> Optional[str]:
    """상세 페이지에서 허가일(itemPermitDate) 추출."""
    m = re.search(r'"itemPermitDate"\s*:\s*"(\d{8})"', html)
    if m:
        d = m.group(1)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    soup = BeautifulSoup(html, "html.parser")
    for th in soup.find_all("th"):
        if "허가일" in (th.get_text() or ""):
            td = th.find_next_sibling("td")
            if td:
                raw = td.get_text(strip=True)
                m2 = re.search(r"(\d{4})[-.](\d{2})[-.](\d{2})", raw)
                if m2:
                    return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"
    return None


# ─── 메인 스크레이퍼 ──────────────────────────────────────────────────────────
class KRMFDSScraper:
    COUNTRY = "KR"
    BODY    = "MFDS"

    def __init__(self, timeout: int = 30, retries: int = 3):
        self.timeout = timeout
        self.retries = retries

    def _get_with_retry(self, url: str, params: dict | None = None):
        for attempt in range(self.retries):
            try:
                r = requests.get(url, params=params,
                                 headers={"User-Agent": UA},
                                 timeout=self.timeout)
                r.raise_for_status()
                return r
            except Exception as e:
                if attempt < self.retries - 1:
                    time.sleep(2 * (attempt + 1))
                    logger.info("[MFDS] 재시도 %d/%d: %s", attempt + 2, self.retries, url)
                else:
                    logger.warning("[MFDS] %d회 시도 실패 (%s): %s", self.retries, url, e)
        return None

    def search(
        self,
        drug: str,
        item_seq: Optional[str] = None,
        item_name: Optional[str] = None,
    ) -> list[MFDSRecord]:
        """drug(product_slug) → MFDSRecord list.

        item_seq 직접 지정 시 바로 상세 페이지 fetch.
        미지정 시 resolve_item_seq 체인 (하드코딩 → 캐시 → 검색) 사용.
        """
        seq = item_seq or resolve_item_seq(
            drug,
            candidates=[item_name] if item_name else None,
            scraper=self,
        )
        if not seq:
            logger.warning("[MFDS] %s 의 itemSeq 미확인. MFDS_ITEM_SEQ/캐시 에 추가하거나 "
                           "item_seq/item_name 으로 직접 지정.", drug)
            return []

        detail_url = f"{DETAIL_URL}?itemSeq={seq}"
        r = self._get_with_retry(detail_url)
        if r is None:
            return []

        html = r.text
        ee_text = _extract_ee_doc(html)
        if not ee_text:
            logger.warning("[MFDS] %s 효능·효과 추출 실패", drug)
            return []

        indications = _split_indications(ee_text)
        permit_date = _extract_permit_date(html)

        brand_match = re.search(r'"itemName"\s*:\s*"([^"]+)"', html)
        brand = brand_match.group(1) if brand_match else drug.title()

        return [MFDSRecord(
            drug=drug,
            brand=brand,
            item_seq=seq,
            detail_url=detail_url,
            permit_date=permit_date,
            indications=indications,
            raw_section=ee_text,
        )]

    def _search_item_seq(self, item_name: str) -> Optional[str]:
        """nedrug 검색으로 itemSeq 찾기. JS 렌더링 없는 GET 검색."""
        params = {"searchYn": "true", "itemName": item_name, "page": "1"}
        r = self._get_with_retry(SEARCH_URL, params=params)
        if r is None:
            return None
        m = re.search(r'getItemDetail\?itemSeq=(\d+)', r.text)
        if m:
            return m.group(1)
        logger.warning("[MFDS] '%s' 검색 결과에서 itemSeq 미발견", item_name)
        return None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "keytruda"
    recs = KRMFDSScraper().search(drug)
    if not recs:
        print("결과 없음")
        sys.exit(1)
    rec = recs[0]
    print(f"brand: {rec.brand}  permit: {rec.permit_date}  "
          f"inds: {len(rec.indications)}")
    for i in rec.indications:
        print(f"\n[{i.code}] {i.label}")
        print(f"  {i.body[:200]}...")
