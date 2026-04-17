"""AU TGA scraper — Product Information (PI) section 4.1 therapeutic indications.

Data source:
  https://www.ebs.tga.gov.au/ebs/picmi/picmirepository.nsf/pdf?OpenAgent&id=<PI_ID>

Requires Playwright (headless Chromium) to accept the TGA licence agreement
before downloading the PI PDF. Section 4.1 is then extracted via pdfplumber.
"""
from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber

logger = logging.getLogger(__name__)

TGA_BASE = "https://www.ebs.tga.gov.au/ebs/picmi/picmirepository.nsf"
SEARCH_URL = TGA_BASE + "/PICMI?OpenForm&t=pi&q={query}"
PDF_URL = TGA_BASE + "/pdf?OpenAgent&id={pi_id}"

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "hta_cache" / "TGA"

TGA_PI_IDS: dict[str, str] = {
    "keytruda":  "CP-2023-PI-01512-1",
    "welireg":   "CP-2023-PI-01168-1",
    "lynparza":  "CP-2018-PI-01771-1",
    "lenvima":   "CP-2016-PI-01212-1",
}


@dataclass
class TGAIndication:
    code:  str
    label: str
    body:  str
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "body": self.body,
                "keywords": self.keywords}


@dataclass
class TGARecord:
    drug:          str
    brand:         str
    pi_id:         Optional[str]
    pi_pdf_local:  Optional[str]
    indications:   list[TGAIndication]
    raw_section:   str = ""

    def to_dict(self) -> dict:
        return {
            "drug": self.drug,
            "brand": self.brand,
            "pi_id": self.pi_id,
            "pi_pdf_local": self.pi_pdf_local,
            "indications": [i.to_dict() for i in self.indications],
            "raw_section": self.raw_section[:3000],
        }


# ─── PDF download (Playwright) ──────────────────────────────────────────────

def _download_pi_pdf(pi_id: str, dest: Path) -> bool:
    """Playwright 로 TGA 라이선스 동의 후 PDF 다운로드."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[TGA] playwright 미설치. pip install playwright && playwright install chromium")
        return False

    url = PDF_URL.format(pi_id=pi_id)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(accept_downloads=True)
            page = ctx.new_page()
            page.goto(url, timeout=30000)
            with page.expect_download(timeout=30000) as dl_info:
                page.evaluate("IAccept()")
            download = dl_info.value
            dest.parent.mkdir(parents=True, exist_ok=True)
            download.save_as(str(dest))
            browser.close()
        if dest.stat().st_size < 1000:
            logger.warning("[TGA] 다운로드 파일이 너무 작음 (%d bytes)", dest.stat().st_size)
            return False
        return True
    except Exception as e:
        logger.warning("[TGA] PDF 다운로드 실패 (%s): %s", pi_id, e)
        return False


# ─── Section 4.1 extraction ─────────────────────────────────────────────────

def _extract_section_41(pdf_path: Path) -> str:
    """PI PDF 에서 section 4.1 THERAPEUTIC INDICATIONS 본문 추출."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        text_all = ""
        for i in range(min(25, len(pdf.pages))):
            text_all += (pdf.pages[i].extract_text() or "") + "\n"

    m41 = re.search(r"4\.1\s+THERAPEUTIC INDICATIONS?", text_all, re.IGNORECASE)
    if not m41:
        logger.warning("[TGA] section 4.1 헤더를 찾지 못함")
        return ""

    after = text_all[m41.end():]
    m42 = re.search(r"4\.2\s+", after)
    if m42:
        after = after[:m42.start()]

    return after.strip()


_DISEASE_HEADER = re.compile(
    r"^(?:Melanoma|Non[‑\-\s]*small cell|Malignant|Classical|Urothelial|"
    r"Head and neck|Renal cell|Microsatellite|Colorectal|Oesophageal|"
    r"Triple[‑\-\s]*negative|Endometrial|Cervical|Gastric|Biliary|"
    r"Hepatocellular|Merkel|Cutaneous|Hodgkin|Small cell|"
    r"Breast|Ovarian|Pancreatic|Prostate|"
    r"von Hippel|Pheochromocytoma|Thyroid|"
    r"[A-Z][a-z]+ (?:cancer|carcinoma|lymphoma|tumou?r|mesothelioma))",
    re.IGNORECASE,
)


def _split_indications(section_text: str, brand: str = "KEYTRUDA") -> list[TGAIndication]:
    """Section 4.1 텍스트를 disease 헤더 + BRAND 문장 단위로 분리."""
    if not section_text:
        return []

    lines = [l.strip() for l in section_text.split("\n") if l.strip()]
    # Remove page numbers (standalone digits)
    lines = [l for l in lines if not re.match(r"^\d{1,3}$", l)]

    blocks: list[tuple[str, list[str]]] = []
    current_disease = ""
    body_lines: list[str] = []

    def flush():
        if body_lines:
            blocks.append((current_disease, list(body_lines)))

    brand_upper = brand.split()[0].upper().rstrip("®")

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

    result: list[TGAIndication] = []
    idx = 0
    for disease, blines in blocks:
        paragraphs = _split_by_brand(blines, brand_upper)
        if not paragraphs:
            paragraphs = [" ".join(blines)]
        for para in paragraphs:
            idx += 1
            label = disease or para[:80]
            result.append(TGAIndication(
                code=f"tga_{idx}",
                label=label[:200],
                body=para[:2000],
            ))

    return result


def _split_by_brand(lines: list[str], brand: str) -> list[str]:
    paras: list[str] = []
    buf: list[str] = []
    for line in lines:
        if line.upper().startswith(brand) and buf:
            paras.append(" ".join(buf))
            buf = [line]
        else:
            buf.append(line)
    if buf:
        paras.append(" ".join(buf))
    return paras


# ─── Main scraper ────────────────────────────────────────────────────────────

class AUTGAScraper:
    COUNTRY = "AU"
    BODY    = "TGA"

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or CACHE_DIR

    def search(
        self,
        drug: str,
        pi_id: Optional[str] = None,
    ) -> list[TGARecord]:
        pid = pi_id or TGA_PI_IDS.get(drug.lower())
        if not pid:
            logger.warning("[TGA] %s 의 PI ID 미확인. TGA_PI_IDS 에 추가하거나 pi_id 로 직접 ���정.", drug)
            return []

        pdf_path = self.cache_dir / f"{drug.lower()}_pi.pdf"
        if not pdf_path.exists() or pdf_path.stat().st_size < 1000:
            logger.info("[TGA] %s PI PDF 다운로드 중...", drug)
            if not _download_pi_pdf(pid, pdf_path):
                return []

        section = _extract_section_41(pdf_path)
        if not section:
            logger.warning("[TGA] %s section 4.1 추출 실패", drug)
            return []

        brand = drug.upper()
        indications = _split_indications(section, brand=brand)

        return [TGARecord(
            drug=drug,
            brand=brand,
            pi_id=pid,
            pi_pdf_local=str(pdf_path),
            indications=indications,
            raw_section=section,
        )]


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "keytruda"
    recs = AUTGAScraper().search(drug)
    if not recs:
        print("결과 없음")
        sys.exit(1)
    rec = recs[0]
    print(f"brand: {rec.brand}  pi_id: {rec.pi_id}  inds: {len(rec.indications)}")
    for i in rec.indications:
        print(f"\n[{i.code}] {i.label}")
        print(f"  {i.body[:200]}...")
