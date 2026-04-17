"""EU EMA scraper — EPAR Product Information (SmPC) section 4.1.

데이터 소스:
  https://www.ema.europa.eu/en/medicines/human/EPAR/<brand-slug>
  └─ Product Information PDF (SmPC) section 4.1 "Therapeutic indications"

SmPC 4.1 구조:
  <Disease header>          (예: "Melanoma", "Non-small cell lung carcinoma (NSCLC)")
  KEYTRUDA ... is indicated ...
  KEYTRUDA ... is indicated ...
  <다음 Disease header>
  KEYTRUDA ... is indicated ...

FDA 와 달리 1.x 번호 없음 — 합성 code("ema_1", "ema_2" ...) 를 부여하고,
label 에는 질환 헤더를 사용한다. 매칭 anchor 는 structure_indication 이
본문에서 추출하는 6-anchor (disease / stage / LoT / population / biomarker_class /
pivotal_trial) 로 수행.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE = "https://www.ema.europa.eu"
EPAR_URL = BASE + "/en/medicines/human/EPAR/{slug}"
UA = "MA-AI-Dossier/1.0"


@dataclass
class EMAIndication:
    code:  str           # "ema_1", "ema_2", ... (합성)
    label: str           # 질환 헤더 ("Melanoma", "NSCLC", ...)
    body:  str           # "KEYTRUDA ... is indicated ..." 단락
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "body": self.body, "keywords": self.keywords}


@dataclass
class EMARecord:
    drug:           str
    brand:          str
    epar_url:       Optional[str]
    pi_pdf_url:     Optional[str]
    pi_pdf_local:   Optional[str]
    authorization_date: Optional[str]     # YYYY-MM-DD — 초기 허가일
    indications:    list[EMAIndication]
    raw_section:    str = ""              # section 4.1 원문 (디버그·label_excerpt 용)

    def to_dict(self) -> dict:
        return {
            "drug": self.drug,
            "brand": self.brand,
            "epar_url": self.epar_url,
            "pi_pdf_url": self.pi_pdf_url,
            "pi_pdf_local": self.pi_pdf_local,
            "authorization_date": self.authorization_date,
            "indications": [i.to_dict() for i in self.indications],
            "raw_section": self.raw_section[:3000],
        }


# ─── SmPC 섹션 4.1 추출 + 적응증 분리 ────────────────────────────────────────
SECTION_START_RE = re.compile(r"(?mi)^\s*4\.1\s+Therapeutic\s+indications?\s*$")
SECTION_END_RE   = re.compile(r"(?mi)^\s*4\.2\s+Posology")


def _extract_section_4_1(pdf_path: Path) -> str:
    """SmPC PDF 에서 section 4.1 본문만 추출. 처음 30페이지 내에서 탐색."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages[:30])

    m1 = SECTION_START_RE.search(text)
    if not m1:
        logger.warning("[EMA] section 4.1 헤더를 찾지 못함")
        return ""
    start = m1.end()
    m2 = SECTION_END_RE.search(text, start)
    end = m2.start() if m2 else start + 15000
    return text[start:end].strip()


def _looks_like_disease_header(line: str) -> bool:
    """한 줄이 질환 헤더인지 판정.

    특징:
      - 짧음 (<=120자)
      - 마침표/쉼표로 끝나지 않음
      - 소문자로 시작하지 않음
      - 약어 괄호가 있거나 대부분 단어가 대문자 시작
    """
    if not line or len(line) > 120:
        return False
    if line[0].islower():
        return False
    if line.endswith((".", ",", ";", ":")):
        return False
    if re.search(r"\([A-Za-z]{2,8}\)", line):   # (NSCLC), (cHL), (HNSCC)
        return True
    words = line.split()
    if not words or len(words) > 12:
        return False
    cap_ratio = sum(1 for w in words if w and (w[0].isupper() or not w[0].isalpha())) / len(words)
    return cap_ratio >= 0.4


KW_RE = re.compile(r"\b([A-Z]{2,}|[A-Z][a-z]+(?:-[A-Z][a-z]+)*)\b")
STOPWORDS = {
    "EMA", "EU", "KEYTRUDA", "KEYNOTE",
    "FOR", "IN", "OF", "AND", "THE", "TO", "OR", "WHO", "WITH",
    "INDICATIONS", "USAGE", "INDICATION", "THERAPEUTIC",
}


def _extract_keywords(text: str) -> list[str]:
    seen: list[str] = []
    for m in KW_RE.finditer(text):
        kw = m.group(1)
        if kw.upper() in STOPWORDS or len(kw) < 2:
            continue
        if kw not in seen:
            seen.append(kw)
        if len(seen) >= 12:
            break
    return seen


def _split_indications(section_text: str, brand: str = "KEYTRUDA") -> list[EMAIndication]:
    """section 4.1 본문에서 (disease, body) 블록을 분리해 EMAIndication 리스트로."""
    if not section_text:
        return []

    # 페이지 번호 (단독 숫자 줄) 제거
    raw_lines = section_text.split("\n")
    lines = [l for l in raw_lines if not re.match(r"^\s*\d{1,3}\s*$", l)]

    blocks: list[tuple[str, str]] = []
    current_disease = ""
    body_lines: list[str] = []
    brand_l = brand.lower()

    def flush():
        if body_lines:
            body = " ".join(l.strip() for l in body_lines if l.strip())
            if body:
                blocks.append((current_disease, body))

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.lower().startswith(brand_l):
            # 새 적응증 시작
            flush()
            body_lines = [stripped]
        elif body_lines:
            # 바디 수집 중 — 질환 헤더면 flush, 아니면 연속 줄
            if _looks_like_disease_header(stripped):
                flush()
                body_lines = []
                current_disease = stripped
            else:
                body_lines.append(stripped)
        else:
            # 바디 없음 → 질환 헤더 후보
            current_disease = stripped

    flush()

    # 합성 code 부여
    result: list[EMAIndication] = []
    for idx, (disease, body) in enumerate(blocks, 1):
        if brand_l not in body.lower():
            continue
        label = disease or body[:80]
        result.append(EMAIndication(
            code=f"ema_{idx}",
            label=label[:200],
            body=body[:2000],
            keywords=_extract_keywords(label + " " + body[:400]),
        ))
    return result


# ─── 메인 스크레이퍼 ──────────────────────────────────────────────────────────
class EUEMAScraper:
    COUNTRY = "EU"
    BODY    = "EMA"

    def __init__(self, cache_dir: Optional[Path] = None, timeout: int = 60):
        self.timeout = timeout
        self.cache_dir = cache_dir or (
            Path(__file__).parent.parent.parent / "data" / "hta_cache" / "EMA"
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def search(self, drug: str, brand_slug: Optional[str] = None) -> list[EMARecord]:
        """약제명(generic 또는 브랜드) → EMARecord list.

        brand_slug 지정 시 EPAR URL 직접 조회 (`/EPAR/<slug>`).
        미지정 시 drug 를 lower-case·공백제거 해 slug 추정.
        """
        slug = (brand_slug or drug).lower().strip().replace(" ", "-")
        epar_url = EPAR_URL.format(slug=slug)
        try:
            r = requests.get(epar_url, headers={"User-Agent": UA}, timeout=self.timeout)
            if r.status_code == 404:
                logger.warning("[EMA] EPAR 페이지 없음: %s", epar_url)
                return []
            r.raise_for_status()
        except Exception as e:
            logger.warning("[EMA] EPAR 페이지 실패 (%s): %s", epar_url, e)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        pi_href = None
        for a in soup.find_all("a"):
            href = a.get("href") or ""
            if "/en/documents/product-information/" in href and href.endswith("_en.pdf"):
                pi_href = href
                break
        if not pi_href:
            logger.warning("[EMA] Product Information PDF 링크 없음: %s", epar_url)
            return []
        pi_url = pi_href if pi_href.startswith("http") else BASE + pi_href

        pdf_local = self._download_pdf(pi_url, f"{slug}_pi.pdf")
        if not pdf_local:
            return []

        section = _extract_section_4_1(pdf_local)
        brand_upper = slug.upper().replace("-", " ").strip()
        indications = _split_indications(section, brand=brand_upper)

        auth_date = self._extract_auth_date(soup)

        return [EMARecord(
            drug=drug,
            brand=slug.title(),
            epar_url=epar_url,
            pi_pdf_url=pi_url,
            pi_pdf_local=str(pdf_local),
            authorization_date=auth_date,
            indications=indications,
            raw_section=section,
        )]

    def _download_pdf(self, url: str, filename: str) -> Optional[Path]:
        out = self.cache_dir / filename
        if out.exists() and out.stat().st_size > 1000:
            return out
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=self.timeout)
            r.raise_for_status()
            out.write_bytes(r.content)
            logger.info("[EMA] SmPC 다운로드: %s (%d bytes)", filename, len(r.content))
            return out
        except Exception as e:
            logger.warning("[EMA] SmPC 다운로드 실패 %s: %s", url, e)
            return None

    @staticmethod
    def _extract_auth_date(soup: BeautifulSoup) -> Optional[str]:
        """EPAR 페이지에서 'Date of authorisation' 을 추출. YYYY-MM-DD 로 정규화."""
        txt = soup.get_text(" ", strip=True)
        m = re.search(r"[Dd]ate of authorisation[:\s]+([0-3]?\d[/ ][01]?\d[/ ]\d{4}|\d{1,2}\s+\w+\s+\d{4})", txt)
        if not m:
            return None
        raw = m.group(1)
        # dd/mm/yyyy
        m2 = re.match(r"(\d{1,2})[/ ](\d{1,2})[/ ](\d{4})", raw)
        if m2:
            d, mth, y = m2.groups()
            return f"{y}-{int(mth):02d}-{int(d):02d}"
        # "17 July 2015"
        months = {
            "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
            "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
        }
        m3 = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
        if m3:
            d, mth_name, y = m3.groups()
            mth = months.get(mth_name.lower())
            if mth:
                return f"{y}-{mth:02d}-{int(d):02d}"
        return None


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "keytruda"
    recs = EUEMAScraper().search(drug)
    if not recs:
        print("결과 없음")
        sys.exit(1)
    rec = recs[0]
    print(f"brand: {rec.brand}  auth: {rec.authorization_date}  inds: {len(rec.indications)}")
    for i in rec.indications:
        print(f"\n[{i.code}] {i.label}")
        print(f"  {i.body[:200]}...")
