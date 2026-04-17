"""국가별 SKU 용량 표기를 파싱해 mg-equivalent 단위로 정규화.

A8 비교는 국가마다 pack·strength 가 다르면 raw local_price 비교가 무의미하다.
(예: DE Keytruda 25mg/ml 단일 농도 467 EUR vs CH 100mg/4ml vial 4294 CHF.)

파서는 product_name + strength + pack 텍스트에서
    (mg_total, ml_total, form, units_per_pack) 를 추출한다.
실패 시 REFERENCE_SKU 로 폴백한다 (UK 는 브랜드명만 나오는 경우가 많음).

reference_mg 는 보통 국내 Korean SKU 의 mg 수 (100mg, 40mg 등).
equivalent_price = local_price × (reference_mg / mg_pack_total) 로 환산.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass


# ── 약제별 표준 SKU (파싱 실패 시 폴백) ────────────────────────────────
REFERENCE_SKU: dict[str, dict] = {
    "keytruda":      {"mg_per_unit": 100, "ml_per_unit": 4,  "form": "vial"},
    "pembrolizumab": {"mg_per_unit": 100, "ml_per_unit": 4,  "form": "vial"},
    "welireg":       {"mg_per_unit": 40,  "ml_per_unit": None, "form": "tablet"},
    "belzutifan":    {"mg_per_unit": 40,  "ml_per_unit": None, "form": "tablet"},
    "opdivo":        {"mg_per_unit": 100, "ml_per_unit": 10, "form": "vial"},
    "nivolumab":     {"mg_per_unit": 100, "ml_per_unit": 10, "form": "vial"},
    "lynparza":      {"mg_per_unit": 100, "ml_per_unit": None, "form": "tablet"},
    "olaparib":      {"mg_per_unit": 100, "ml_per_unit": None, "form": "tablet"},
    "lenvima":       {"mg_per_unit": 10,  "ml_per_unit": None, "form": "capsule"},
    "lenvatinib":    {"mg_per_unit": 10,  "ml_per_unit": None, "form": "capsule"},
}

# "240 mg/80 mg" 같은 고정용량복합 → 비교 불가
_COMBO_RE = re.compile(r"\d+\s*mg\s*/\s*\d+\s*mg", re.I)

# "100 mg/4ml" = 100 mg in 4 ml (CH 관습)
_MG_SLASH_ML = re.compile(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*(\d+(?:\.\d+)?)\s*ml", re.I)

# "25 mg/ml" = concentration
_MG_PER_ML   = re.compile(r"(\d+(?:\.\d+)?)\s*mg\s*/\s*ml(?![a-z])", re.I)

# "4 ml" = volume (독립)
_VOL_ML      = re.compile(r"(?<![\d/])(\d+(?:\.\d+)?)\s*ml(?![a-z/])", re.I)

# "40 mg" = mg total (mg/ml 이 아닌 독립) — lookbehind 에 \d 포함해 "80"의 "0" 부분매칭 방지
_MG_TOTAL    = re.compile(r"(?<![\d/])(\d+(?:\.\d+)?)\s*mg(?!\s*/\s*(?:ml|\d))", re.I)

# pack count: "1 flaconcino", "30 tablet"
_PACK_COUNT  = re.compile(
    r"(\d+)\s*(?:vial|flaconcino|flacone|tablet|tabs?|cps|compresse|capsule|caps?|kapsel|錠|バイアル)",
    re.I,
)

_FORM_PATTERNS = [
    ("vial",    re.compile(r"vial|flacon|konz|konzentrat|inj[ée]|perf|점적|バイアル|注射", re.I)),
    ("tablet",  re.compile(r"tablet|tab(?:letten?)?|compresse|cp\s*pellic|pellic|錠|정|film", re.I)),
    ("capsule", re.compile(r"caps?|cáps|capsule|kapsel|カプセル|캡슐", re.I)),
]


@dataclass
class DoseParsed:
    mg_per_unit:    float | None  # 1 unit(vial/tab/cap) 당 mg
    ml_per_unit:    float | None  # vial 일 때 ml
    units_per_pack: int           # pack 당 unit 수 (보통 1)
    mg_pack_total:  float | None  # mg_per_unit × units_per_pack
    form:           str | None    # vial/tablet/capsule
    confidence:     str           # parsed / reference / combo / unknown
    source_text:    str           # 디버깅용 원문

    def to_dict(self) -> dict:
        return asdict(self)


def _detect_form(text: str) -> str | None:
    for name, pat in _FORM_PATTERNS:
        if pat.search(text):
            return name
    return None


def _normalize_text(s: str) -> str:
    """일본어 full-width 숫자·mg·ml → ASCII 변환 + 공백 정리."""
    if not s:
        return ""
    # fullwidth → halfwidth (NFKC 가 ｍ→m, ｇ→g, ｌ→l, 전각 숫자 등 처리)
    n = unicodedata.normalize("NFKC", s)
    # 한중일 단위 → ASCII
    n = n.replace("ｍｇ", "mg").replace("ｍｌ", "ml")
    # 한글 단위 "밀리그람" 등은 드물어 무시
    return re.sub(r"\s+", " ", n).strip()


def parse(
    product_name: str | None,
    strength: str | None = None,
    pack: str | None = None,
    form_hint: str | None = None,
    product_slug: str | None = None,
) -> DoseParsed:
    """용량 문자열 파싱. 실패 시 REFERENCE_SKU[product_slug] 로 폴백."""
    parts = [p for p in (product_name, strength, pack, form_hint) if p]
    raw = " | ".join(parts)
    text = _normalize_text(" ".join(parts))

    # 복합제 탐지 — "240 mg/80 mg" (비교 불가, 조기 반환)
    if _COMBO_RE.search(text):
        return DoseParsed(
            mg_per_unit=None, ml_per_unit=None, units_per_pack=1,
            mg_pack_total=None, form=_detect_form(text),
            confidence="combo", source_text=raw[:200],
        )

    mg_per_unit: float | None = None
    ml_per_unit: float | None = None

    # 1) "X mg/Y ml" — mg total + volume
    m = _MG_SLASH_ML.search(text)
    if m:
        mg_per_unit = float(m.group(1))
        ml_per_unit = float(m.group(2))
    else:
        # 2) concentration + 독립 volume
        mc = _MG_PER_ML.search(text)
        mv = _VOL_ML.search(text)
        if mc and mv:
            mg_per_unit = float(mc.group(1)) * float(mv.group(1))
            ml_per_unit = float(mv.group(1))
        elif mc and not mv:
            # concentration 만 있고 volume 모름 — reference 에서 ml 복구
            pass  # 아래 reference 폴백 단계에서 처리
        else:
            # 3) mg total (ml 정보 없음 — 주로 tablet)
            mt = _MG_TOTAL.search(text)
            if mt:
                mg_per_unit = float(mt.group(1))

    # pack count
    units = 1
    mp = _PACK_COUNT.search(text)
    if mp:
        units = int(mp.group(1))

    # form
    form: str | None = None
    for name, pat in _FORM_PATTERNS:
        if pat.search(text):
            form = name
            break

    # confidence 결정
    confidence = "parsed" if mg_per_unit else "unknown"

    # reference 폴백 — mg 이거나 volume 이 비어있으면 보강
    ref_used = False
    if product_slug:
        ref = REFERENCE_SKU.get(product_slug.lower())
        if ref:
            if mg_per_unit is None:
                mg_per_unit = ref.get("mg_per_unit")
                ref_used = True
            # 농도만 알고 volume 모르는 경우
            if ml_per_unit is None and ref.get("ml_per_unit"):
                # concentration 만 파싱된 경우엔 ref_ml 을 곱해 총 mg 재계산
                mc = _MG_PER_ML.search(text)
                if mc and not _MG_SLASH_ML.search(text) and not _VOL_ML.search(text):
                    ml_per_unit = ref["ml_per_unit"]
                    mg_per_unit = float(mc.group(1)) * ml_per_unit
                    ref_used = True
                elif mg_per_unit == ref.get("mg_per_unit"):
                    ml_per_unit = ref.get("ml_per_unit")
            if form is None:
                form = ref.get("form")

    if mg_per_unit and ref_used:
        confidence = "reference"
    elif mg_per_unit:
        confidence = "parsed"
    else:
        confidence = "unknown"

    mg_pack_total = mg_per_unit * units if mg_per_unit else None

    return DoseParsed(
        mg_per_unit=mg_per_unit,
        ml_per_unit=ml_per_unit,
        units_per_pack=units,
        mg_pack_total=mg_pack_total,
        form=form,
        confidence=confidence,
        source_text=raw[:200],
    )


def parse_reference_mg(sku_text: str | None, product_slug: str | None = None) -> float | None:
    """국내 Korean SKU 문자열에서 기준 mg 추출.
    예: '100mg/4mL' → 100, '40mg 30정' → 40.
    실패 시 REFERENCE_SKU fallback.
    """
    if sku_text:
        d = parse(sku_text, product_slug=product_slug)
        if d.mg_per_unit:
            return float(d.mg_per_unit)
    if product_slug:
        ref = REFERENCE_SKU.get(product_slug.lower())
        if ref and ref.get("mg_per_unit"):
            return float(ref["mg_per_unit"])
    return None


def normalize_prices(
    prices: dict[str, float],
    rows_meta: dict[str, dict] | None,
    product_slug: str | None,
    reference_mg: float | None,
) -> dict[str, dict]:
    """국가별 local_price → dose-equivalent 가격으로 환산.

    Args:
        prices: {country: local_price}
        rows_meta: {country: {product_name, strength, pack, form}}
        product_slug: 'keytruda' 등 (REFERENCE_SKU 폴백용)
        reference_mg: 동등 비교 기준 mg (보통 국내 SKU mg).
            None 이면 가장 흔한 mg_pack_total 을 기준으로 자동 설정.

    Returns:
        {
          country: {
            local_price, mg_per_unit, ml_per_unit, units_per_pack,
            mg_pack_total, price_per_mg, equivalent_price (reference_mg 기준),
            form, confidence, source_text
          }
        }
        + 추가키 '_reference_mg' (실제 사용된 기준값, 자동결정 시 확인용)
    """
    rows_meta = rows_meta or {}
    parsed_by_country: dict[str, DoseParsed] = {}
    for c in prices:
        meta = rows_meta.get(c, {}) or {}
        parsed_by_country[c] = parse(
            meta.get("product_name"),
            meta.get("strength"),
            meta.get("pack"),
            meta.get("form"),
            product_slug=product_slug,
        )

    # reference_mg 자동결정 — 가장 흔한 mg_pack_total
    if reference_mg is None:
        from collections import Counter
        cand = [p.mg_pack_total for p in parsed_by_country.values() if p.mg_pack_total]
        if cand:
            reference_mg = Counter(cand).most_common(1)[0][0]

    out: dict[str, dict] = {}
    for c, local in prices.items():
        d = parsed_by_country[c]
        price_per_mg = (
            local / d.mg_pack_total if (local and d.mg_pack_total) else None
        )
        equiv = (
            price_per_mg * reference_mg
            if (price_per_mg is not None and reference_mg is not None)
            else None
        )
        out[c] = {
            "local_price":    local,
            "mg_per_unit":    d.mg_per_unit,
            "ml_per_unit":    d.ml_per_unit,
            "units_per_pack": d.units_per_pack,
            "mg_pack_total":  d.mg_pack_total,
            "price_per_mg":   price_per_mg,
            "equivalent_price": equiv,
            "form":           d.form,
            "confidence":     d.confidence,
            "source_text":    d.source_text,
        }
    out["_reference_mg"] = reference_mg
    return out


__all__ = [
    "DoseParsed",
    "REFERENCE_SKU",
    "parse",
    "parse_reference_mg",
    "normalize_prices",
]
