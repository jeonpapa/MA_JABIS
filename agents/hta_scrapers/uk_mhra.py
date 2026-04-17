"""UK MHRA scraper — EMC SmPC section 4.1 therapeutic indications.

Data source:
  https://www.medicines.org.uk/emc/product/{id}/smpc
  Section 4.1 lives inside a <details><summary>4.1 Therapeutic indications</summary>…</details> block.

The EMC (electronic Medicines Compendium) hosts UK-licensed SmPCs.
Post-Brexit these diverge from EMA SmPCs on occasion.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

EMC_BASE = "https://www.medicines.org.uk"
SEARCH_URL = EMC_BASE + "/emc/search"
SMPC_URL = EMC_BASE + "/emc/product/{product_id}/smpc"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

EMC_PRODUCT_IDS: dict[str, int] = {
    "keytruda":  2498,
    "welireg":   14126,
    "lynparza":  9204,
    "lenvima":   7881,
}


@dataclass
class MHRAIndication:
    code:  str
    label: str
    body:  str
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "body": self.body,
                "keywords": self.keywords}


@dataclass
class MHRARecord:
    drug:         str
    brand:        str
    product_id:   Optional[int]
    smpc_url:     Optional[str]
    indications:  list[MHRAIndication]
    raw_section:  str = ""

    def to_dict(self) -> dict:
        return {
            "drug": self.drug,
            "brand": self.brand,
            "product_id": self.product_id,
            "smpc_url": self.smpc_url,
            "indications": [i.to_dict() for i in self.indications],
            "raw_section": self.raw_section[:3000],
        }


# ─── Section 4.1 extraction ─────────────────────────────────────────────────

def _extract_section_41(html: str) -> str:
    """HTML 에서 <details> 안의 section 4.1 본문을 추출."""
    soup = BeautifulSoup(html, "html.parser")
    for summary in soup.find_all("summary"):
        text = summary.get_text(strip=True)
        if "4.1" in text and "indications" in text.lower():
            details = summary.parent
            full = details.get_text("\n", strip=True)
            lines = full.split("\n")
            return "\n".join(lines[1:])
    logger.warning("[MHRA] section 4.1 <details> 블록을 찾지 못함")
    return ""


_DISEASE_HEADER = re.compile(
    r"^(?:Melanoma|Non[‑\-\s]*small cell|Malignant|Classical|Urothelial|"
    r"Head and neck|Renal cell|Microsatellite|Colorectal|Oesophageal|"
    r"Triple[‑\-\s]*negative|Endometrial|Cervical|Gastric|Biliary|"
    r"Hepatocellular|Merkel|Cutaneous|Hodgkin|Small cell|"
    r"Non[‑\-\s]*colorectal|Breast|Ovarian|Pancreatic|Prostate|"
    r"von Hippel|Pheochromocytoma|Thyroid|"
    r"[A-Z][a-z]+ (?:cancer|carcinoma|lymphoma|tumou?r|mesothelioma))",
    re.IGNORECASE,
)


def _split_indications(section_text: str) -> list[MHRAIndication]:
    """Section 4.1 본문을 disease 헤더 기준 블록으로 분리."""
    if not section_text:
        return []

    lines = [l.strip() for l in section_text.split("\n") if l.strip()]

    blocks: list[tuple[str, list[str]]] = []
    current_disease = ""
    body_lines: list[str] = []

    def flush():
        if body_lines:
            blocks.append((current_disease, list(body_lines)))

    for line in lines:
        if _DISEASE_HEADER.match(line) and len(line) < 120:
            flush()
            body_lines = []
            current_disease = line
        else:
            body_lines.append(line)

    flush()

    if not blocks and lines:
        blocks.append(("", lines))

    result: list[MHRAIndication] = []
    idx = 0
    for disease, blines in blocks:
        paragraphs = _split_paragraphs(blines)
        if not paragraphs:
            paragraphs = [" ".join(blines)]
        for para in paragraphs:
            idx += 1
            label = disease or para[:80]
            result.append(MHRAIndication(
                code=f"mhra_{idx}",
                label=label[:200],
                body=para[:2000],
            ))

    return result


def _split_paragraphs(lines: list[str]) -> list[str]:
    """KEYTRUDA/drug-name 으로 시작하는 문장 단위로 분리."""
    paras: list[str] = []
    buf: list[str] = []
    for line in lines:
        if re.match(r"^(?:KEYTRUDA|WELIREG|Lynparza|Lenvatinib|[A-Z]{3,})\b", line) and buf:
            paras.append(" ".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        paras.append(" ".join(buf))
    return paras


# ─── Main scraper ────────────────────────────────────────────────────────────

class UKMHRAScraper:
    COUNTRY = "UK"
    BODY    = "MHRA"

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
                    logger.info("[MHRA] 재시도 %d/%d: %s", attempt + 2, self.retries, url)
                else:
                    logger.warning("[MHRA] %d회 시도 실패 (%s): %s", self.retries, url, e)
        return None

    def search(
        self,
        drug: str,
        product_id: Optional[int] = None,
    ) -> list[MHRARecord]:
        pid = product_id or EMC_PRODUCT_IDS.get(drug.lower())
        if not pid:
            pid = self._search_product_id(drug)
        if not pid:
            logger.warning("[MHRA] %s 의 EMC product ID 미확인. "
                           "EMC_PRODUCT_IDS 에 추가하거나 product_id 로 직접 지정.", drug)
            return []

        smpc_url = SMPC_URL.format(product_id=pid)
        r = self._get_with_retry(smpc_url)
        if r is None:
            return []

        html = r.text
        section = _extract_section_41(html)
        if not section:
            logger.warning("[MHRA] %s section 4.1 추출 실패", drug)
            return []

        indications = _split_indications(section)

        brand_match = re.search(r"<title>([^<]+)</title>", html)
        brand = drug.upper()
        if brand_match:
            raw_title = brand_match.group(1).strip()
            brand = raw_title.split("-")[0].split("|")[0].strip()

        return [MHRARecord(
            drug=drug,
            brand=brand,
            product_id=pid,
            smpc_url=smpc_url,
            indications=indications,
            raw_section=section,
        )]

    def _search_product_id(self, drug: str) -> Optional[int]:
        """EMC 검색으로 첫 번째 SmPC product ID 찾기."""
        r = self._get_with_retry(SEARCH_URL, params={"q": drug})
        if r is None:
            return None
        m = re.search(r'/emc/product/(\d+)/smpc', r.text)
        if m:
            return int(m.group(1))
        logger.warning("[MHRA] '%s' EMC 검색 결과에서 product ID 미발견", drug)
        return None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "keytruda"
    recs = UKMHRAScraper().search(drug)
    if not recs:
        print("결과 없음")
        sys.exit(1)
    rec = recs[0]
    print(f"brand: {rec.brand}  inds: {len(rec.indications)}")
    for i in rec.indications:
        print(f"\n[{i.code}] {i.label}")
        print(f"  {i.body[:200]}...")
