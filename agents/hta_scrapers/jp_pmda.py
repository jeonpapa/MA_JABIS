"""JP PMDA scraper — 添付文書 (Package Insert) section 4 「効能又は効果」.

데이터 소스:
  https://www.info.pmda.go.jp/go/pack/<YJ-code>/   ← 원본 PDF 서버
  https://www.pmda.go.jp/PmdaSearch/iyakuSearch/   ← 검색 UI (JS 기반)

添付文書 구조 (2019 이후 신 template):
  1. 警告
  2. 禁忌
  3. 組成・性状
  4. 効能又は効果          ← 여기가 적응증
     4.1 / 4.2 / 4.3 ... (번호 또는 ○ 로 분리)
  5. 効能又は効果に関連する注意
  6. 用法及び用量
  ...

적응증 분리 전략:
  - 섹션 4 텍스트 추출
  - "4.1", "4.2" 등 번호가 있으면 번호 기준 분할
  - 번호 없이 "〇" 또는 "・" 불릿이면 불릿 기준 분할
  - 번호/불릿 둘 다 없으면 단일 적응증
  - 본문 일본어 원문 그대로 LLM 에 넘기면 Gemini 가 다국어 추출

PMDA 검색은 Playwright 없이 POST form 으로 안 먹는 구조 (JS 렌더링) 이므로
현재 구현은 **직접 PDF URL 입력** 방식. 제품별 URL 은 `PMDA_PI_URLS` dict 에
매핑. URL 이 갱신되면 dict 를 업데이트하거나 `search(drug, pi_url=...)` 로 주입.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber
import requests

logger = logging.getLogger(__name__)

BASE = "https://www.pmda.go.jp"
GENERAL_LIST_URL = BASE + "/PmdaSearch/iyakuDetail/GeneralList/{yj}"
UA = "MA-AI-Dossier/1.0"

# MSD Japan 제품 → PMDA YJ 코드 (일본표준商品分類番号 + 枝番).
# iyakuDetail/GeneralList/<YJ> 페이지에서 최신 添付文書 PDF 링크를 동적 추출하므로
# revision 업데이트 시 자동으로 최신 판 사용.
# 신규 제품 추가: PMDA 검색 (https://www.pmda.go.jp/PmdaSearch/iyakuSearch/) 에서
# 販売名 검색 → 결과 행의 YJ코드 컬럼 값 확인 후 아래 dict 에 추가.
PMDA_YJ_CODES: dict[str, str] = {
    "keytruda":  "4291435A2025",   # キイトルーダ点滴静注 100mg
    "welireg":   "4291094F1020",   # ウェリレグ錠 40mg
    "lynparza":  "4291052F1027",   # リムパーザ錠 100mg
    "lenvima":   "4291039M1020",   # レンビマカプセル 4mg
    "januvia":   "3969010F2030",   # ジャヌビア錠 50mg
}


@dataclass
class PMDAIndication:
    code:  str           # "pmda_1", "pmda_2", ... (합성; 添付文書 번호 4.1/4.2 있으면 그 번호)
    label: str           # 적응증 첫 줄 (예: "悪性黒色腫")
    body:  str           # 일본어 본문 전체
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "label": self.label, "body": self.body, "keywords": self.keywords}


@dataclass
class PMDARecord:
    drug:           str
    brand:          str
    pi_pdf_url:     Optional[str]
    pi_pdf_local:   Optional[str]
    approval_date:  Optional[str]     # YYYY-MM-DD (제조판매承認日 — PDF 에서 탐지 가능하면)
    indications:    list[PMDAIndication]
    raw_section:    str = ""

    def to_dict(self) -> dict:
        return {
            "drug": self.drug,
            "brand": self.brand,
            "pi_pdf_url": self.pi_pdf_url,
            "pi_pdf_local": self.pi_pdf_local,
            "approval_date": self.approval_date,
            "indications": [i.to_dict() for i in self.indications],
            "raw_section": self.raw_section[:3000],
        }


# ─── 섹션 4 추출 ──────────────────────────────────────────────────────────────
# 添付文書 는 2컬럼 레이아웃 이라 pdfplumber 의 linear 추출이 좌/우 컬럼을
# interleave 해 section 4 header 가 본문 중간에 끼인다. page.crop 으로 좌/우를
# 분리 후 컬럼 단위로 읽어 내려가야 section 4 와 section 5 가 혼재하지 않는다.

# section 4 본문에서 제거해야 할 noise (페이지 상단 metadata, section 5 진입 등)
NOISE_CUT = re.compile(
    r"(?:5[\s．\.]*効能"
    r"|日本標準商品分類番号"
    r"|承認番号"
    r"|販売開始"
    r"|最適使用推進"
    r"|モノクローナル抗体"
    r"|性腫瘍剤"
    r"|貯\s*法"
    r"|有効期間"
    r")"
)
SECTION5_HEAD = re.compile(r"5[\s．\.]*効能[・又]+効果に関連する注意")


def _extract_section_4(pdf_path: Path) -> str:
    """添付文書 PDF 에서 section 4 「効能又は効果」 본문을 ○/〇-bullet 기준으로 추출.

    전략:
      1. 처음 6 페이지를 좌/우 컬럼으로 crop 후 순회
      2. 각 컬럼에서 section 5 헤더 만나면 그 앞까지만 채택 (전체 loop 중단)
      3. "4.効能又は効果" 이후부터 "○" 를 구분자로 split → 각 조각이 적응증
      4. 각 bullet 에서 noise marker (section 5 잔해, metadata 등) 만나면 truncate
    """
    with pdfplumber.open(str(pdf_path)) as pdf:
        texts: list[str] = []
        stop = False
        for i in range(min(6, len(pdf.pages))):
            if stop:
                break
            page = pdf.pages[i]
            w, h = page.width, page.height
            for col_box in [(0, 0, w * 0.5, h), (w * 0.5, 0, w, h)]:
                if stop:
                    break
                col_text = page.crop(col_box).extract_text() or ""
                m = SECTION5_HEAD.search(col_text)
                if m:
                    col_text = col_text[:m.start()]
                    stop = True
                texts.append(col_text)

    combined = "\n".join(texts)
    m = re.search(r"4[\s．\.]*効能[・又]+効果", combined)
    if m:
        combined = combined[m.end():]

    end_m = re.search(
        r"(?:5[\s．\.]*効能[・又]+効果に関連"
        r"|5\.1\s"
        r"|6[\s．\.]*用法[・及]+用量"
        r"|7[\s．\.]*用法)",
        combined,
    )
    if end_m:
        combined = combined[:end_m.start()]

    raw_chunks = re.split(r"[〇○]", combined)
    bullets: list[str] = []
    for c in raw_chunks[1:]:   # 첫 조각은 ○ 이전 (header 직후 공백)
        merged = c.replace("\n", "").strip()
        merged = re.sub(r"^[＊\*\s]+", "", merged)
        # noise marker 만나면 그 앞까지만
        nm = NOISE_CUT.search(merged)
        if nm:
            merged = merged[:nm.start()]
        merged = merged.strip("＊*─- \t")
        if merged:
            bullets.append(merged)

    if not bullets:
        bullets = _fallback_section4_parse(combined)
    if not bullets:
        logger.warning("[PMDA] section 4 추출 실패 (○-bullet 및 fallback 모두)")
    return "\n".join(bullets)


def _fallback_section4_parse(text: str) -> list[str]:
    """○ bullet 이 없는 添付文書 (예: Lenvima) 용 fallback.

    패턴: 〈ブランド名〉 이후 적응증을 읽점(、) 으로 나열.
    또는 단순 줄 나열.
    """
    text = text.strip()
    if not text:
        return []

    chunks = re.split(r"〈[^〉]+〉", text)
    if len(chunks) > 1:
        seen: set[str] = set()
        results: list[str] = []
        for chunk in chunks[1:]:
            cleaned = chunk.replace("\n", "").strip().rstrip("-–─")
            nm = NOISE_CUT.search(cleaned)
            if nm:
                cleaned = cleaned[:nm.start()]
            cleaned = cleaned.strip("＊*─- \t、,")
            if not cleaned:
                continue
            items = re.split(r"、(?=[^\s]{2})", cleaned)
            for item in items:
                item = re.sub(r"-\d+$", "", item).strip("、, \t")
                if item and item not in seen:
                    seen.add(item)
                    results.append(item)
        return results

    lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 3]
    return lines[:30]


def _split_indications(section_text: str) -> list[PMDAIndication]:
    """_extract_section_4 결과는 이미 한 줄당 한 적응증. 줄 단위로 PMDAIndication 화."""
    if not section_text:
        return []
    lines = [l.strip() for l in section_text.split("\n") if l.strip()]
    out: list[PMDAIndication] = []
    for idx, body in enumerate(lines, 1):
        label = body
        for sep in ("。", "、", "：", ":", "（"):
            p = label.find(sep)
            if 0 < p < 60:
                label = label[:p]
                break
        out.append(PMDAIndication(
            code=f"pmda_{idx}",
            label=label[:120],
            body=body[:2000],
            keywords=[],   # 일본어 키워드는 LLM 단계에서 영어로 정규화
        ))
    return out


# ─── 메인 스크레이퍼 ──────────────────────────────────────────────────────────
class JPPMDAScraper:
    COUNTRY = "JP"
    BODY    = "PMDA"

    def __init__(self, cache_dir: Optional[Path] = None, timeout: int = 60):
        self.timeout = timeout
        self.cache_dir = cache_dir or (
            Path(__file__).parent.parent.parent / "data" / "hta_cache" / "PMDA"
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def search(
        self,
        drug: str,
        yj_code: Optional[str] = None,
        pi_url: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> list[PMDARecord]:
        """drug(product_slug) → PMDARecord list.

        URL 해결 우선순위:
          1. pi_url 파라미터 (직접 지정)
          2. yj_code → iyakuDetail/GeneralList/<yj> 페이지에서 최신 PDF 추출
          3. PMDA_YJ_CODES dict (drug slug 기본값)

        Args:
            drug:    product slug (예: "keytruda")
            yj_code: PMDA YJ 코드 (예: "4291436A1023"). 없으면 dict 기본값.
            pi_url:  添付文書 PDF URL 직접 지정.
            brand:   일본어 販売名. 없으면 drug 을 그대로 사용.
        """
        url = pi_url
        yj = yj_code or PMDA_YJ_CODES.get(drug.lower())
        if not url and yj:
            url = self._resolve_pdf_url(yj)
        if not url:
            logger.warning("[PMDA] %s 의 添付文書 URL 해결 실패. PMDA_YJ_CODES 에 YJ 코드를 "
                           "추가하거나 pi_url 로 직접 지정.", drug)
            return []

        pdf_local = self._download_pdf(url, f"{drug.lower()}_pi.pdf")
        if not pdf_local:
            return []

        section = _extract_section_4(pdf_local)
        if not section:
            logger.warning("[PMDA] %s 섹션 4 추출 실패 — PDF 구조 변경 가능성", drug)
            return []

        indications = _split_indications(section)
        approval_date = self._extract_approval_date(pdf_local)

        return [PMDARecord(
            drug=drug,
            brand=brand or drug.title(),
            pi_pdf_url=url,
            pi_pdf_local=str(pdf_local),
            approval_date=approval_date,
            indications=indications,
            raw_section=section,
        )]

    def _resolve_pdf_url(self, yj_code: str) -> Optional[str]:
        """YJ 코드 → 최신 添付文書 PDF URL.

        GeneralList HTML 에서 첫 번째 ResultDataSetPDF/... 링크 추출.
        여러 formulation (주사/정/과립 등) 이 하나 YJ 에 걸릴 수 있으나 적응증은 공유.
        """
        list_url = GENERAL_LIST_URL.format(yj=yj_code)
        try:
            r = requests.get(list_url, headers={"User-Agent": UA}, timeout=self.timeout)
            r.raise_for_status()
        except Exception as e:
            logger.warning("[PMDA] GeneralList 로드 실패 %s: %s", list_url, e)
            return None
        m = re.search(r"ResultDataSetPDF/([A-Za-z0-9_]+)", r.text)
        if not m:
            logger.warning("[PMDA] GeneralList 에 PDF 링크 없음: %s", list_url)
            return None
        return f"{BASE}/PmdaSearch/iyakuDetail/ResultDataSetPDF/{m.group(1)}"

    def _download_pdf(self, url: str, filename: str) -> Optional[Path]:
        out = self.cache_dir / filename
        if out.exists() and out.stat().st_size > 1000:
            return out
        try:
            r = requests.get(url, headers={"User-Agent": UA},
                             timeout=self.timeout, allow_redirects=True)
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                logger.warning("[PMDA] 응답이 PDF 가 아님: %s (head: %r)", url, r.content[:30])
                return None
            out.write_bytes(r.content)
            logger.info("[PMDA] 添付文書 다운로드: %s (%d bytes)", filename, len(r.content))
            return out
        except Exception as e:
            logger.warning("[PMDA] 添付文書 다운로드 실패 %s: %s", url, e)
            return None

    @staticmethod
    def _extract_approval_date(pdf_path: Path) -> Optional[str]:
        """添付文書 내 '承認年月日' 또는 '販売開始' 날짜 추출. 和暦/西暦 혼재 대응."""
        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                text = "\n".join((p.extract_text() or "") for p in pdf.pages[:20])
        except Exception:
            return None

        # 西暦 YYYY年MM月 형태
        m = re.search(r"承認[年月日]*[\s：:]*(\d{4})年[\s　]*(\d{1,2})月[\s　]*(\d{1,2})?", text)
        if m:
            y, mth, d = m.groups()
            return f"{y}-{int(mth):02d}-{int(d or 1):02d}"

        # 和暦 (令和/平成)
        wareki = {"令和": 2018, "平成": 1988}
        m = re.search(r"承認[年月日]*[\s：:]*(令和|平成)[\s　]*(\d{1,2})年[\s　]*(\d{1,2})月", text)
        if m:
            era, era_y, mth = m.groups()
            base = wareki.get(era, 0)
            y = base + int(era_y)
            return f"{y}-{int(mth):02d}-01"

        return None


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    drug = sys.argv[1] if len(sys.argv) > 1 else "keytruda"
    recs = JPPMDAScraper().search(drug)
    if not recs:
        print("결과 없음")
        sys.exit(1)
    rec = recs[0]
    print(f"brand: {rec.brand}  approval: {rec.approval_date}  inds: {len(rec.indications)}")
    for i in rec.indications:
        print(f"\n[{i.code}] {i.label}")
        print(f"  {i.body[:200]}...")
