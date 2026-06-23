"""해외약가 제형(formulation)별 그룹핑 + 제형 내 표시단위 보정.

2026-06-23 재설계: 기존 cross-strength 정규화(240↔480↔20 를 같은 것으로 ×배율)는
서로 다른 *제형*을 섞는 오류였다. 이제 **제형(강도×투여경로)별로 먼저 그룹핑**하고,
용량보정은 **같은 제형 내에서 표시단위가 다를 때만** 적용한다.

정책 (process_formulations):
  1) **미국(US)을 기준**으로 canonical 제형 집합 (route, strength_mg) 구성
     (제형이 가장 빨리 등재되는 국가). US 부재 시 제형 최다 국가 대용.
  2) 각 행을 canonical 제형에 매칭 — 1차 결정적(route 일치 + strength ±1%),
     2차 LLM(강도 불명/모호 행만, gpt-4o). canonical 에 없으면 foreign_only(별도 탭).
  3) **제형 내 표시단위 보정**: dose_norm_factor = canonical_strength / unit_strength
     (per-ml↔per-vial, 包↔錠 등 같은 제형의 표시단위 차이만). 다른 강도 그룹 간
     보정은 구조적으로 불가(canonical_strength_mg 동일 그룹 내로 제한).
  4) adjusted_price_krw_normalized = adjusted_price_krw × factor (제형 내 표시단위 통일).

산출 컬럼: formulation_key/formulation_label/canonical_strength_mg/route/
  formulation_source/is_us_listed + dose_norm_factor/adjusted_price_krw_normalized.
대시보드는 formulation_key 로 탭 그룹핑, 탭 내에서 국가별 normalized 비교.
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


# 런타임 정규화 모델 — tests/sim_dose_normalize_models.py 시뮬레이션으로 선정(3c).
# 결과는 tests/dose_norm_model_report.json 에 기록. 변경 시 시뮬레이션 재실행 권장.
NORM_MODEL = "gpt-4o"


def build_norm_prompt(drug: str, rows: list[dict]) -> tuple[str, str]:
    """정규화 LLM 프롬프트 (system, user). 시뮬레이션 하니스와 공유."""
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
    return sys_prompt, user_prompt


def _call_json(model: str, sys_prompt: str, user_prompt: str) -> dict | None:
    """모델명 → 프로바이더 디스패치. 파싱된 JSON dict 반환(실패 None). 검증 없음.

    지원: gpt-4o / gpt-4o-mini (OpenAI), gemini-2.5-flash (Gemini),
          sonar-pro / sonar (Perplexity).
    """
    _load_openai_key()  # config/.env 의 모든 키 로드
    try:
        m = model.lower()
        if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
            from openai import OpenAI
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": sys_prompt},
                          {"role": "user", "content": user_prompt}],
                temperature=0.0, max_tokens=900,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content.strip()
        elif m.startswith("gemini"):
            raw = _call_gemini(model, sys_prompt, user_prompt)
        elif m.startswith("sonar"):
            from openai import OpenAI
            client = OpenAI(api_key=os.environ.get("PERPLEXITY_API_KEY", ""),
                            base_url="https://api.perplexity.ai")
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": sys_prompt},
                          {"role": "user", "content": user_prompt}],
                temperature=0.0, max_tokens=900,
            )
            raw = resp.choices[0].message.content.strip()
        else:
            logger.warning("[DoseNorm] 미지원 모델: %s", model)
            return None
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.lstrip().lower().startswith("json"):
                raw = raw.lstrip()[4:]
        return json.loads(raw)
    except Exception as e:
        logger.warning("[DoseNorm] 모델 호출 실패(%s): %s", model, e)
        return None


def call_norm_model(model: str, sys_prompt: str, user_prompt: str) -> dict | None:
    """_call_json + 'countries' 키 검증 (시뮬레이션 하니스 호환)."""
    result = _call_json(model, sys_prompt, user_prompt)
    if result is None or not isinstance(result.get("countries"), dict):
        return None
    return result


def _call_gemini(model: str, sys_prompt: str, user_prompt: str) -> str:
    """Gemini REST generateContent — JSON 강제, thinkingBudget=0."""
    import requests
    key = os.environ.get("GOOGLE_GENAI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    body = {
        "systemInstruction": {"parts": [{"text": sys_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2048,
                             "responseMimeType": "application/json",
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    r = requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def llm_normalize(drug: str, items: list[dict], model: str | None = None) -> dict | None:
    """선정 모델(NORM_MODEL)로 기준용량 + 국가별 보정계수 판단. 실패 시 None.

    입력: 국가별 {country, product_name, dosage_strength, form_type}.
    출력: {"reference_strength_mg": float,
           "countries": {country: {"unit_strength_mg": float|null,
                                    "factor": float|null, "note": str}}}
    """
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
    sys_prompt, user_prompt = build_norm_prompt(drug, rows)
    return call_norm_model(model or NORM_MODEL, sys_prompt, user_prompt)


def _sane_factor(f) -> float | None:
    """factor 안전성 검증 — 양수 + 과도값(×0.001~×1000) 차단."""
    try:
        f = float(f)
    except (ValueError, TypeError):
        return None
    if f <= 0 or f < 0.001 or f > 1000:
        return None
    return f


def route_of(form_type: str | None) -> str:
    """form_type → route (oral/injection). unknown 은 oral 로 보수 처리."""
    ft = (form_type or "").lower()
    return "injection" if ("inject" in ft or "주사" in ft or "주" == ft) else "oral"


def _fmt_strength(mg: float):
    return int(mg) if abs(mg - round(mg)) < 1e-6 else round(mg, 2)


def _fkey(mg: float, route: str) -> str:
    return f"{_fmt_strength(mg)}mg|{route}"


def _flabel(mg: float, route: str) -> str:
    return f"{_fmt_strength(mg)}mg {'주사' if route == 'injection' else '경구'}"


def _strength_of(item: dict) -> float | None:
    """행의 per-unit 활성성분 mg — 에이전트가 form-aware 로 채운 unit_strength_mg 우선."""
    v = item.get("unit_strength_mg")
    if v:
        try:
            return float(v)
        except (ValueError, TypeError):
            pass
    return extract_strength_mg(item.get("dosage_strength"))


def _emit(it: dict, canon_mg: float, route: str, source: str,
          is_us: bool, updates: list) -> None:
    """제형 배정 + 제형 내 표시단위 보정 → 필드 산출(in-place + updates)."""
    adj = it.get("adjusted_price_krw")
    unit_mg = it.get("_strength")
    # 제형 내 표시단위 보정: 같은 제형(canon_mg)인데 행 표시강도가 다르면 보정.
    factor = 1.0
    if unit_mg and canon_mg and abs(unit_mg - canon_mg) / canon_mg > 0.01:
        sf = _sane_factor(canon_mg / unit_mg)
        if sf:
            factor = sf
    normalized = int(round(adj * factor)) if adj is not None else None
    note = ""
    if factor != 1.0:
        note = f"표시단위 보정 {_fmt_strength(unit_mg)}mg→{_fmt_strength(canon_mg)}mg ×{factor:.3g}"
    elif source == "foreign_only":
        note = "US 미등재 제형"
    fields = {
        "formulation_key": _fkey(canon_mg, route),
        "formulation_label": _flabel(canon_mg, route),
        "canonical_strength_mg": _fmt_strength(canon_mg),
        "route": route,
        "formulation_source": source,
        "is_us_listed": 1 if is_us else 0,
        "unit_strength_mg": unit_mg,
        "reference_strength_mg": _fmt_strength(canon_mg),
        "dose_norm_factor": round(factor, 6),
        "adjusted_price_krw_normalized": normalized,
        "dose_norm_note": note,
    }
    it.update(fields)
    if it.get("id"):
        updates.append({"id": it["id"], **fields})


def _emit_unmatched(it: dict, updates: list) -> None:
    """강도 불명 + canonical 매칭 실패 → unmatched(원본가 유지, 별도 탭)."""
    adj = it.get("adjusted_price_krw")
    route = it.get("_route") or route_of(it.get("form_type"))
    label = (it.get("dosage_strength") or "기타")[:30]
    fields = {
        "formulation_key": f"unknown|{route}|{label}",
        "formulation_label": f"{label} ({'주사' if route == 'injection' else '경구'})",
        "canonical_strength_mg": None, "route": route,
        "formulation_source": "unmatched", "is_us_listed": 0,
        "unit_strength_mg": it.get("_strength"), "reference_strength_mg": None,
        "dose_norm_factor": 1.0,
        "adjusted_price_krw_normalized": int(adj) if adj is not None else None,
        "dose_norm_note": "제형 강도 불명 — 매칭 보류(원본가)",
    }
    it.update(fields)
    if it.get("id"):
        updates.append({"id": it["id"], **fields})


def _build_formulation_prompt(drug: str, canonical: dict, rows: list[dict]) -> tuple[str, str]:
    canon_list = [{"route": r, "strength_mg": _fmt_strength(s)} for (r, _), s in canonical.items()]
    sys = (
        "당신은 글로벌 약가 분석가입니다. 각 국가별 의약품 표기를 '미국 기준 제형'에 매칭하세요.\n"
        "제형 = (투여경로 route: oral/injection) × (최소단위당 활성성분 강도 mg).\n"
        "규칙: 1) 주사는 농도×부피의 per-vial 총 mg(예: 20mg/mL × 12mL = 240). "
        "2) 미국 canonical 목록 중 route 일치 + 강도 동일(±2%)이면 그 제형에 매칭. "
        "3) 매칭되는 canonical 이 없으면 is_us_listed=false 로 자체 강도 반환. "
        "4) 복합제·강도 불명이면 canonical_strength_mg=null. JSON 만 응답."
    )
    user = (
        f"약제: {drug}\n미국 canonical 제형: {json.dumps(canon_list, ensure_ascii=False)}\n"
        f"매칭 대상 행:\n{json.dumps(rows, ensure_ascii=False, indent=2)}\n\n"
        '출력: {"items":[{"idx":<int>,"route":"oral|injection",'
        '"canonical_strength_mg":<number|null>,"is_us_listed":<bool>,"note":"<짧게>"}]}'
    )
    return sys, user


def _llm_match(drug: str, pending: list[dict], canonical: dict,
               us_based: bool, updates: list) -> None:
    """강도 불명/모호 행만 LLM 으로 canonical 제형 매칭."""
    rows = [{
        "idx": i, "country": it.get("country"),
        "product_name": it.get("product_name") or "",
        "dosage_strength": it.get("dosage_strength") or "",
        "package_unit": it.get("package_unit") or "",
        "form_type": it.get("form_type") or "",
    } for i, it in enumerate(pending)]
    sys, user = _build_formulation_prompt(drug, canonical, rows)
    res = _call_json(NORM_MODEL, sys, user)
    by_idx = {}
    if res and isinstance(res.get("items"), list):
        for o in res["items"]:
            if isinstance(o, dict) and "idx" in o:
                by_idx[o["idx"]] = o
    canon_strengths = {(r, round(s, 2)): s for (r, _), s in canonical.items()}
    for i, it in enumerate(pending):
        o = by_idx.get(i)
        cmg = None
        if o:
            try:
                cmg = float(o["canonical_strength_mg"]) if o.get("canonical_strength_mg") else None
            except (ValueError, TypeError):
                cmg = None
        if not cmg:
            _emit_unmatched(it, updates)
            continue
        route = (o.get("route") or it.get("_route") or "oral").lower()
        # canonical 에 실제 존재하면 us_canonical, 아니면 foreign_only
        is_canon = (route, round(cmg, 2)) in canon_strengths
        src = ("us_canonical" if us_based else "foreign_ref") if is_canon else "foreign_only"
        _emit(it, cmg, route, src, us_based and is_canon, updates)


def process_formulations(drug: str, items: list[dict]) -> list[dict]:
    """제형(formulation)별 매칭 + 제형 내 표시단위 보정. in-place 필드 추가 + updates 반환.

    각 priced item 은 에이전트가 form-aware unit_strength_mg 를 채워 전달하는 것을 권장
    (없으면 dosage_strength regex fallback). 미국 canonical 기준, cross-strength 금지.
    """
    # 가격 유무와 무관하게 강도 있는 모든 행에 제형 배정(미가격 행도 같은 탭에 '가격 미공개').
    targets = [it for it in items
               if it.get("adjusted_price_krw") is not None or it.get("dosage_strength")
               or it.get("unit_strength_mg")]
    if not targets:
        return []
    for it in targets:
        it["_strength"] = _strength_of(it)
        it["_route"] = route_of(it.get("form_type"))

    # canonical 기준 국가: US 우선(강도 있는 행, 가격 무관), 없으면 제형 최다 국가
    us_rows = [it for it in targets if (it.get("country") or "").upper() == "US" and it["_strength"]]
    us_based = bool(us_rows)
    base_rows = us_rows
    if not base_rows:
        from collections import defaultdict
        by_c = defaultdict(set)
        for it in targets:
            if it["_strength"]:
                by_c[it["country"]].add((it["_route"], round(it["_strength"], 2)))
        if by_c:
            cc = max(by_c, key=lambda c: len(by_c[c]))
            base_rows = [it for it in targets if it.get("country") == cc and it["_strength"]]

    canonical = {}  # (route, round(strength,2)) -> strength
    for it in base_rows:
        canonical[(it["_route"], round(it["_strength"], 2))] = it["_strength"]

    updates: list[dict] = []
    pending = []
    for it in targets:
        s, r = it["_strength"], it["_route"]
        if s is None:
            pending.append(it)
            continue
        # canonical 매칭 (정확 → ±1%)
        matched = canonical.get((r, round(s, 2)))
        is_canon = matched is not None
        if matched is None:
            for (cr, cs), cval in canonical.items():
                if cr == r and cs and abs(s - cs) / cs <= 0.01:
                    matched, is_canon = cval, True
                    break
        if matched is None:
            matched, is_canon = s, False   # foreign_only
        src = ("us_canonical" if us_based else "foreign_ref") if is_canon else "foreign_only"
        _emit(it, matched, r, src, us_based and is_canon, updates)

    if pending:
        if canonical:
            _llm_match(drug, pending, canonical, us_based, updates)
        else:
            for it in pending:
                _emit_unmatched(it, updates)
    return updates
