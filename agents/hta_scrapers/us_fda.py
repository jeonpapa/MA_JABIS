"""US FDA scraper — openFDA Drug Label API.

엔드포인트:
  https://api.fda.gov/drug/label.json?search=openfda.{generic|brand}_name:<drug>

핵심 추출:
  - openfda.brand_name / generic_name / manufacturer_name
  - indications_and_usage  → 1.1, 1.2, ... 번호별 적응증 분리
  - effective_time         → YYYYMMDD (라벨 발효일)

본 스크레이퍼는 'HTA 평가' 가 아닌 'FDA 허가' 를 다루며,
다른 기관(PBAC/CADTH/NICE/SMC) 결정은 FDA 적응증과 매칭되어
indication-matrix 형태로 표시된다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

API = "https://api.fda.gov/drug/label.json"
UA  = "MA-AI-Dossier/1.0"


@dataclass
class FDAIndication:
    code:  str          # "1.1", "1.2", ...
    label: str          # "von Hippel-Lindau (VHL) disease"
    body:  str          # 본문
    keywords: list[str] = field(default_factory=list)  # 매칭용 핵심 키워드

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "body": self.body, "keywords": self.keywords}


@dataclass
class FDARecord:
    drug:           str
    brand_names:    list[str]
    generic_names:  list[str]
    manufacturer:   str
    effective_time: Optional[str]              # YYYY-MM-DD
    label_url:      Optional[str]
    raw_indication: str
    indications:    list[FDAIndication]

    def to_dict(self) -> dict:
        return {
            "drug": self.drug,
            "brand_names":    self.brand_names,
            "generic_names":  self.generic_names,
            "manufacturer":   self.manufacturer,
            "effective_time": self.effective_time,
            "label_url":      self.label_url,
            "raw_indication": self.raw_indication,
            "indications":    [i.to_dict() for i in self.indications],
        }


# ─── 적응증 본문 분리 ──────────────────────────────────────────────────────────
# FDA 라벨 본문 구조:
#   "1 INDICATIONS AND USAGE <SUMMARY> ... ( 1.1 ) ... ( 1.3 )
#    1.1 <Title> <BRAND> is indicated for ...
#    1.2 <Title> <BRAND> is indicated for ..."
# 첫 등장은 요약(타이틀 누락 가능), 두 번째 등장이 상세 — 상세 블록만 사용한다.
SECTION_RE = re.compile(r"(?m)(?<!\()\b(\d+\.\d+)\s+(?!\))(.+?)(?=\s+\d+\.\d+\s|\s*$)", re.DOTALL)

KW_RE = re.compile(r"\b([A-Z]{2,}|[A-Z][a-z]+(?:-[A-Z][a-z]+)*)\b")

STOPWORDS = {
    "FDA", "USA", "RX", "ONLY", "USE", "WITH",
    "FOR", "IN", "OF", "AND", "THE", "TO", "OR", "WHO",
    "INDICATIONS", "USAGE", "INDICATION",
}


def _split_indications(text: str, brand: str = "") -> list[FDAIndication]:
    """본문에서 1.x 적응증 블록을 분리.

    label = '1.x' 헤더 직후부터 '<BRAND> is indicated' 직전까지.
    body  = '<BRAND> is indicated ...' 부터 다음 1.x 헤더 전까지.
    """
    if not text:
        return []

    # 상세 블록만 캡처: 첫 등장(요약)과 두 번째 등장(상세) 둘 다 잡힌다.
    # 같은 code 가 여러 번 나오면 본문이 더 긴 것을 채택.
    # 3-level 코드 우선 매칭: 1.1.2 같은 것을 1.1 로 잘라먹지 않도록.
    matches = list(re.finditer(r"(?<!\()\b(\d+(?:\.\d+){1,2})\s+(?!\))", text))
    if not matches:
        return [FDAIndication(code="1", label="Indication", body=text.strip()[:1500],
                              keywords=_extract_keywords(text))]

    by_code: dict[str, tuple[str, str]] = {}  # code -> (label, body)
    # brand 와 "is indicated" 사이에 병용약 문구 (", in combination with ...") 가 낄 수 있다.
    # 마침표 없는 최대 250자 허용.
    brand_re = (
        re.compile(rf"\b{re.escape(brand)}[^.]{{0,250}}?is\s+indicated", re.IGNORECASE)
        if brand else None
    )

    for i, m in enumerate(matches):
        code  = m.group(1)
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()

        if brand_re:
            mb = brand_re.search(chunk)
            if mb:
                label = chunk[:mb.start()].strip().rstrip(",.;:")
                body  = chunk[mb.start():].strip()
            else:
                # BRAND 미매칭 = 요약 블록 — 라벨만 있고 본문 없음
                label, body = chunk, ""
        else:
            # 첫 마침표 이전을 라벨로 가정
            dot = chunk.find(". ")
            if dot > 0 and dot < 200:
                label, body = chunk[:dot].strip(), chunk[dot+2:].strip()
            else:
                label, body = chunk[:120].strip(), chunk

        # 같은 code 중복 시 본문이 더 긴 쪽
        prev = by_code.get(code)
        if prev is None or len(body) > len(prev[1]):
            by_code[code] = (label, body)

    out: list[FDAIndication] = []
    for code in sorted(by_code.keys(), key=lambda c: tuple(int(x) for x in c.split("."))):
        label, body = by_code[code]
        sub_bodies = _split_subindications(body, brand)
        if len(sub_bodies) <= 1:
            out.append(FDAIndication(
                code=code, label=label[:200], body=body[:1500],
                keywords=_extract_keywords(label + " " + body[:300]),
            ))
        else:
            for idx, sb in enumerate(sub_bodies):
                sub_code = f"{code}_{chr(ord('a') + idx)}"
                out.append(FDAIndication(
                    code=sub_code, label=label[:200], body=sb[:1500],
                    keywords=_extract_keywords(label + " " + sb[:300]),
                ))
    return out


def _split_subindications(body: str, brand: str) -> list[str]:
    """1.x body 를 'BRAND ... is indicated for ...' 단위로 분리.

    같은 1.x 섹션이 여러 sub-indication 을 한 단락에 묶어 두는 경우 (예: NSCLC,
    HNSCC, RCC, EC) 가 흔하다. sentence boundary 직후의 BRAND 만 indication
    start 로 인식하고, 'followed by KEYTRUDA' 같은 referent 는 무시한다.
    """
    if not brand or not body:
        return [body] if body else []
    pat = re.compile(rf"(?:^|(?<=[.!?]\s))\b{re.escape(brand)}\b", re.IGNORECASE)
    starts = [m.start() for m in pat.finditer(body)]
    if len(starts) <= 1:
        return [body.strip()] if body.strip() else []
    chunks: list[str] = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(body)
        ck = body[s:e].strip()
        if "is indicated" in ck.lower():
            chunks.append(ck)
    return chunks if chunks else [body.strip()]


def _extract_keywords(text: str) -> list[str]:
    """매칭용 핵심 키워드 (대문자 약어 + 질환명 등)."""
    seen = []
    for m in KW_RE.finditer(text):
        kw = m.group(1)
        if kw.upper() in STOPWORDS or len(kw) < 2:
            continue
        if kw not in seen:
            seen.append(kw)
        if len(seen) >= 12:
            break
    return seen


# ─── 메인 스크레이퍼 ──────────────────────────────────────────────────────────
class USFDAScraper:
    COUNTRY = "US"
    BODY    = "FDA"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def search(self, drug: str) -> list[FDARecord]:
        """약제명(generic|brand) → FDA 라벨 레코드 리스트.

        같은 약물에 대해 여러 NDA/라벨이 있을 수 있어 list 반환.
        """
        # generic_name 우선, 결과 없으면 brand_name
        for field in ("generic_name", "brand_name"):
            try:
                r = requests.get(
                    API,
                    params={
                        "search": f"openfda.{field}:{drug}",
                        "limit": 5,
                    },
                    headers={"User-Agent": UA},
                    timeout=self.timeout,
                )
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                data = r.json()
                results = data.get("results") or []
                if results:
                    return [self._parse(drug, rec) for rec in results]
            except Exception as e:
                logger.warning("[FDA] %s 검색 실패 (%s): %s", drug, field, e)
        return []

    def _parse(self, query: str, rec: dict) -> FDARecord:
        of = rec.get("openfda", {}) or {}
        ind_text = (rec.get("indications_and_usage") or [""])[0]
        eff = rec.get("effective_time", "")
        eff_iso = f"{eff[:4]}-{eff[4:6]}-{eff[6:8]}" if len(eff) == 8 else None

        label_url = (
            f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={rec['set_id']}"
            if rec.get("set_id") else None
        )

        brand_names = of.get("brand_name") or []
        generic_names = of.get("generic_name") or []
        primary_brand = brand_names[0] if brand_names else ""
        indications = _split_indications(ind_text, brand=primary_brand)
        # 브랜드/성분명은 매칭에 도움이 안 되므로 키워드에서 제거
        noise = {n.upper() for n in brand_names + generic_names}
        for ind in indications:
            ind.keywords = [k for k in ind.keywords if k.upper() not in noise]
        return FDARecord(
            drug=query,
            brand_names=brand_names,
            generic_names=generic_names,
            manufacturer=(of.get("manufacturer_name") or [""])[0],
            effective_time=eff_iso,
            label_url=label_url,
            raw_indication=ind_text[:5000],
            indications=indications,
        )


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "belzutifan"
    recs = USFDAScraper().search(drug)
    print(json.dumps([r.to_dict() for r in recs], ensure_ascii=False, indent=2))
