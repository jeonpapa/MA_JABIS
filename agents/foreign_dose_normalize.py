"""국가간 약제 용량(strength) 정규화.

해외약가 A8 비교 시 국가별로 보고 단위 강도가 다르면 per-unit 조정가 비교가
불공정해진다. 예: Prevymis(letermovir) — 일본은 20mg 정 단가, 타국은 240mg 정 단가.
일본 가격을 240mg 등가로 보정해야 동일 선상 비교가 된다.

정책:
  1) regex 로 국가별 per-unit strength(mg) 를 먼저 추출 → priced 국가들 사이에
     strength 가 1종이면(=일치) LLM 호출 없이 factor=1 (보정 불필요).
  2) **불일치 감지 시에만** LLM(GPT-4o)이 기준용량(reference_strength_mg)과
     국가별 보정계수(factor = reference/unit)를 판단. 동일 제형·동일 활성성분만 보정,
     복합제·다른 제형은 factor=null(보정 제외) + note.
  3) adjusted_price_krw_normalized = adjusted_price_krw × factor (per-unit 등가).

비교(min/avg/max·그래프·카드)는 normalized 를 기본값으로 사용(없으면 raw fallback).
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

# strength 추출: "240 mg", "0.5g(50mg/mL)", "20mg" 등에서 per-unit mg 후보.
_MG = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|밀리그램|㎎|g|그램|mcg|㎍|ug)", re.IGNORECASE)


def _to_mg(value: float, unit: str) -> float:
    u = unit.lower()
    if u in ("g", "그램"):
        return value * 1000.0
    if u in ("mcg", "㎍", "ug"):
        return value / 1000.0
    return value  # mg / 밀리그램 / ㎎


def extract_strength_mg(dosage_strength: str | None) -> float | None:
    """dosage_strength 문자열에서 per-unit 활성성분 강도(mg) 추정.

    가장 단순한 휴리스틱 — 첫 mg/g 매치를 per-unit 강도로 본다(주사 농도/부피 표기는
    LLM 단계에서 교정). 불확실하면 None.
    """
    if not dosage_strength:
        return None
    m = _MG.search(dosage_strength)
    if not m:
        return None
    try:
        return _to_mg(float(m.group(1)), m.group(2))
    except (ValueError, TypeError):
        return None


def detect_strength_mismatch(items: list[dict]) -> dict:
    """priced 국가들의 per-unit strength 가 서로 다른지 regex 로 1차 판정.

    items: search_all 이 모은 [{country, dosage_strength, adjusted_price_krw, ...}].
    반환: {"mismatch": bool, "strengths": {country: mg}, "distinct": [mg,...]}.
    """
    strengths: dict[str, float] = {}
    for it in items:
        if it.get("adjusted_price_krw") is None:
            continue
        mg = extract_strength_mg(it.get("dosage_strength"))
        if mg:
            strengths[it.get("country")] = mg
    distinct = sorted({round(v, 4) for v in strengths.values()})
    return {
        "mismatch": len(distinct) >= 2,
        "strengths": strengths,
        "distinct": distinct,
    }


def _load_openai_key() -> None:
    env_path = BASE_DIR / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass


def llm_normalize(drug: str, items: list[dict]) -> dict | None:
    """GPT-4o 로 기준용량 + 국가별 보정계수 판단. 실패 시 None.

    입력: 국가별 {country, product_name, dosage_strength, form_type}.
    출력: {"reference_strength_mg": float,
           "countries": {country: {"unit_strength_mg": float|null,
                                    "factor": float|null, "note": str}}}
    """
    try:
        _load_openai_key()
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    except Exception as e:
        logger.warning("[DoseNorm] OpenAI 초기화 실패: %s", e)
        return None

    rows = [
        {
            "country": it.get("country"),
            "product_name": it.get("product_name") or "",
            "dosage_strength": it.get("dosage_strength") or "",
            "form_type": it.get("form_type") or "",
        }
        for it in items
        if it.get("adjusted_price_krw") is not None
    ]
    if len(rows) < 2:
        return None

    sys_prompt = (
        "당신은 글로벌 약가 비교 분석가입니다. 국가별로 보고된 의약품의 '최소단위당 "
        "활성성분 강도(mg)'를 판단하고, 국가간 per-unit 약가 비교가 공정하도록 "
        "기준용량(reference)으로 보정계수를 산출하세요.\n"
        "규칙:\n"
        "1) unit_strength_mg = 최소단위(정/캡슐/바이알) 1개당 활성성분 총 mg. 주사는 "
        "농도×부피의 총 mg(예: 240mg/1.2mL vial → 240), 경구는 정당 mg.\n"
        "2) reference_strength_mg = 다수 국가가 보고한 표준 단위강도(가장 흔한 값 우선, "
        "임상 표준용량 고려).\n"
        "3) factor = reference_strength_mg / unit_strength_mg (해당국 가격을 기준용량 "
        "등가로 환산하는 배수).\n"
        "4) **동일 제형·동일 단일 활성성분만 보정**. 복합제·다른 제형·강도 불명이면 "
        "factor=null + note 로 사유. 강도가 같으면 factor=1.\n"
        "5) JSON 만 응답."
    )
    user_prompt = (
        f"약제: {drug}\n"
        f"국가별 표기:\n{json.dumps(rows, ensure_ascii=False, indent=2)}\n\n"
        "출력 JSON 형식:\n"
        '{"reference_strength_mg": <number>, '
        '"reference_basis": "<기준 선정 사유 1문장>", '
        '"countries": {"<country>": {"unit_strength_mg": <number|null>, '
        '"factor": <number|null>, "note": "<짧은 설명>"}}}'
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        result = json.loads(raw)
        if not isinstance(result.get("countries"), dict):
            return None
        return result
    except Exception as e:
        logger.warning("[DoseNorm] LLM 정규화 실패: %s", e)
        return None


def _sane_factor(f) -> float | None:
    """factor 안전성 검증 — 양수 + 과도값(×0.001~×1000) 차단."""
    try:
        f = float(f)
    except (ValueError, TypeError):
        return None
    if f <= 0 or f < 0.001 or f > 1000:
        return None
    return f


def normalize_country_items(drug: str, items: list[dict]) -> list[dict]:
    """국가간 용량 정규화 후처리. items 각 dict 에 정규화 필드를 in-place 추가.

    반환: 정규화 필드가 채워진 행들의 [{id, unit_strength_mg, reference_strength_mg,
          dose_norm_factor, adjusted_price_krw_normalized, dose_norm_note}] (DB 업데이트용).
    """
    priced = [it for it in items if it.get("adjusted_price_krw") is not None]
    if len(priced) < 2:
        return []

    detection = detect_strength_mismatch(items)
    updates: list[dict] = []

    if not detection["mismatch"]:
        # 강도 일치 — 보정 불필요(factor=1). normalized = raw 로 채워 비교 일관성 유지.
        ref = detection["distinct"][0] if detection["distinct"] else None
        for it in priced:
            adj = it.get("adjusted_price_krw")
            fields = {
                "unit_strength_mg": detection["strengths"].get(it.get("country")),
                "reference_strength_mg": ref,
                "dose_norm_factor": 1.0,
                "adjusted_price_krw_normalized": int(adj),
                "dose_norm_note": "강도 일치 — 보정 불필요" if ref else None,
            }
            it.update(fields)
            if it.get("id"):
                updates.append({"id": it["id"], **fields})
        return updates

    logger.info("[DoseNorm] %s 국가간 strength 불일치 %s → LLM 판단",
                drug, detection["distinct"])
    llm = llm_normalize(drug, items)
    ref_mg = None
    country_map = {}
    if llm:
        try:
            ref_mg = float(llm.get("reference_strength_mg")) if llm.get("reference_strength_mg") else None
        except (ValueError, TypeError):
            ref_mg = None
        country_map = llm.get("countries") or {}

    for it in priced:
        country = it.get("country")
        adj = it.get("adjusted_price_krw")
        cm = country_map.get(country, {}) if isinstance(country_map, dict) else {}
        factor = _sane_factor(cm.get("factor"))
        unit_mg = cm.get("unit_strength_mg")
        note = cm.get("note") or ""
        # LLM 미응답 국가 → regex strength 로 fallback factor 계산
        if factor is None and ref_mg:
            rmg = extract_strength_mg(it.get("dosage_strength"))
            if rmg and rmg > 0:
                factor = _sane_factor(ref_mg / rmg)
                unit_mg = unit_mg or rmg
                if factor and abs(factor - 1.0) > 1e-6:
                    note = note or f"regex 보정 {rmg}mg→{ref_mg}mg"
        if factor:
            normalized = int(round(adj * factor))
            fields = {
                "unit_strength_mg": unit_mg,
                "reference_strength_mg": ref_mg,
                "dose_norm_factor": round(factor, 6),
                "adjusted_price_krw_normalized": normalized,
                "dose_norm_note": (note or (f"{unit_mg}mg→{ref_mg}mg ×{factor:.3g}"
                                            if unit_mg and ref_mg else "용량 보정")),
            }
        else:
            # 보정 불가(복합제·강도 불명) → normalized=raw, factor 미기록 + 사유
            fields = {
                "unit_strength_mg": unit_mg,
                "reference_strength_mg": ref_mg,
                "dose_norm_factor": None,
                "adjusted_price_krw_normalized": int(adj),
                "dose_norm_note": note or "용량 보정 불가(강도 불명/복합제) — 원본가 사용",
            }
        it.update(fields)
        if it.get("id"):
            updates.append({"id": it["id"], **fields})
    return updates
