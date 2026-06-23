"""허가사항(MFDS) 용법용량 → 구조화 dosing Resolver (하이브리드).

투약비용비교 치료비 계산용. 우선순위:
  1) DB 캐시(dosing_resolved, TTL 30일) — 캐시-DB-first
  2) Tier1 정규식 (health_kr_dose_parser) — 저렴·결정론, 단순 용법
  3) Tier2 gpt-4o (dosing_resolver_rules.md) — 복잡/다적응증·체중BSA
  4) Tier3 2모델 동의(gemini-2.5-flash) — LLM confidence='low' 시 다수결로 승급/유지

출력 dict: {schedule, daily_dose_units, daily_dose_mg, cycle_days, doses_per_cycle,
            per_kg_mg, per_m2_mg, representative_indication, alternatives,
            confidence, source, model, notes}
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from agents.scrapers.health_kr_dose_parser import parse_dose_schedule, parse_weight_bsa

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
RULES_PATH = BASE_DIR / "agents" / "rules" / "dosing_resolver_rules.md"

_ADULT_WEIGHT_KG = 60.0
_ADULT_BSA_M2 = 1.7
# 단독 mg (농도 /mL, mg/kg, mg/m² 제외) — cycle 1회 투여량 추정용
_PLAIN_MG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mg|밀리그램|㎎)(?!\s*/\s*(?:kg|m|mL|㎖))", re.IGNORECASE)


def _find_dose_mg(txt: str) -> float | None:
    m = _PLAIN_MG_RE.search(txt or "")
    return float(m.group(1)) if m else None


def _canonical_daily_mg(d: dict, usage_text: str) -> float | None:
    """구조화 dosing → 1일 평균 mg(강도독립). 불가 시 None."""
    sched = d.get("schedule")
    cyc = d.get("cycle_days")
    dpc = d.get("doses_per_cycle") or 1.0
    if sched == "continuous" and d.get("daily_dose_mg") is not None:
        return float(d["daily_dose_mg"])
    if sched == "cycle" and cyc:
        if d.get("per_kg_mg") is not None:
            return d["per_kg_mg"] * _ADULT_WEIGHT_KG * dpc / cyc
        if d.get("per_m2_mg") is not None:
            return d["per_m2_mg"] * _ADULT_BSA_M2 * dpc / cyc
        admin_mg = d.get("daily_dose_mg") or _find_dose_mg(usage_text)
        if admin_mg:
            return float(admin_mg) * dpc / cyc
    return None

_DOSE_FIELDS = ("schedule", "daily_dose_units", "daily_dose_mg", "cycle_days",
                "doses_per_cycle", "per_kg_mg", "per_m2_mg")


def _has_usable_dose(d: dict) -> bool:
    if not d or not d.get("schedule"):
        return False
    return any(d.get(k) is not None for k in
               ("daily_dose_units", "daily_dose_mg", "doses_per_cycle", "per_kg_mg", "per_m2_mg"))


def _regex_resolve(usage_text: str) -> dict | None:
    """Tier1 정규식. schedule + 사용가능 dose 확보 시 dict, 아니면 None."""
    sched = parse_dose_schedule(usage_text) or {}
    wb = parse_weight_bsa(usage_text) or {}
    out: dict = {}
    # 체중/BSA(cycle) 가 있으면 우선(항암제), 없으면 schedule 결과
    if wb.get("per_kg_mg") is not None or wb.get("per_m2_mg") is not None:
        out.update({
            "schedule": "cycle",
            "cycle_days": wb.get("interval_days"),
            "doses_per_cycle": 1.0 if wb.get("interval_days") else None,
            "per_kg_mg": wb.get("per_kg_mg"),
            "per_m2_mg": wb.get("per_m2_mg"),
        })
    if sched:
        for k in ("schedule", "daily_dose_units", "daily_dose_mg", "cycle_days", "doses_per_cycle"):
            out.setdefault(k, sched.get(k))
        if not out.get("schedule"):
            out["schedule"] = sched.get("schedule")
    if not _has_usable_dose(out):
        return None
    cm = _canonical_daily_mg(out, usage_text)
    if cm is not None:
        out["daily_dose_mg"] = round(cm, 4)
    return out


def _build_prompts(usage_text: str, name: str) -> tuple[str, str]:
    rules = RULES_PATH.read_text(encoding="utf-8") if RULES_PATH.exists() else ""
    sys_prompt = (
        "당신은 한국 MA 약제 용법용량 분석 전문가다. 아래 규칙을 글자 그대로 준수하고 "
        "지정된 JSON 만 출력한다.\n\n=== dosing_resolver_rules.md ===\n" + rules
    )
    user_prompt = f"약제: {name or '(미상)'}\n허가사항 용법용량 원문:\n{(usage_text or '')[:2500]}"
    return sys_prompt, user_prompt


def _llm_resolve(usage_text: str, name: str, model: str) -> dict | None:
    from agents.foreign_dose_normalize import _call_json
    sys_p, user_p = _build_prompts(usage_text, name)
    raw = _call_json(model, sys_p, user_p)
    if not isinstance(raw, dict) or not raw.get("schedule"):
        return None
    return raw


def _empty(usage_text: str, reason: str) -> dict:
    return {
        "schedule": None, "daily_dose_units": None, "daily_dose_mg": None,
        "cycle_days": None, "doses_per_cycle": None, "per_kg_mg": None, "per_m2_mg": None,
        "representative_indication": None, "alternatives": [],
        "confidence": "low", "source": "none", "model": None,
        "notes": reason, "usage_text": usage_text,
    }


def _agree(a: dict, b: dict) -> bool:
    """두 LLM 결과가 핵심(schedule + 대표 dose)에서 일치하는지(±5%)."""
    if a.get("schedule") != b.get("schedule"):
        return False
    for k in ("daily_dose_units", "daily_dose_mg", "cycle_days", "doses_per_cycle",
              "per_kg_mg", "per_m2_mg"):
        va, vb = a.get(k), b.get(k)
        if va is None and vb is None:
            continue
        if va is None or vb is None:
            return False
        try:
            if abs(float(va) - float(vb)) > max(1e-6, 0.05 * abs(float(va))):
                return False
        except (TypeError, ValueError):
            return False
    return True


def resolve_dosing(usage_text: str, *, cache_key: str, name: str = "",
                   db=None, force: bool = False, use_llm: bool = True) -> dict:
    """허가사항 usage_text → 구조화 dosing (캐시 우선). cache_key 로 영구 저장."""
    if db is not None and cache_key and not force:
        cached = db.get_dosing(cache_key)
        if cached:
            return cached

    result: dict | None = None

    # Tier1 정규식
    rx = _regex_resolve(usage_text) if usage_text else None
    if rx:
        # cycle 은 1회 투여 단위/mg 가 정규식으로 불확실 → medium(검토 유도). continuous 는 high.
        rx_conf = "medium" if rx.get("schedule") == "cycle" else "high"
        result = {**rx, "representative_indication": None, "alternatives": [],
                  "confidence": rx_conf, "source": "regex", "model": None, "notes": ""}

    # Tier2 LLM (정규식 실패 시)
    if result is None and use_llm and usage_text:
        primary = _llm_resolve(usage_text, name, "gpt-4o")
        if primary:
            conf = (primary.get("confidence") or "medium").lower()
            source, model = "llm", "gpt-4o"
            # Tier3: low 면 2모델 동의로 승급
            if conf == "low":
                second = _llm_resolve(usage_text, name, "gemini-2.5-flash")
                if second and _agree(primary, second):
                    conf, source = "medium", "review"
            merged = {k: primary.get(k) for k in _DOSE_FIELDS}
            cm = _canonical_daily_mg(merged, usage_text)
            if cm is not None:
                merged["daily_dose_mg"] = round(cm, 4)
            result = {
                **merged,
                "representative_indication": primary.get("representative_indication"),
                "alternatives": primary.get("alternatives") or [],
                "confidence": conf, "source": source, "model": model,
                "notes": primary.get("notes") or "",
            }

    if result is None:
        result = _empty(usage_text, "정규식·LLM 모두 용법 추출 실패")

    result["cache_key"] = cache_key
    result["usage_text"] = usage_text
    if db is not None and cache_key:
        try:
            db.save_dosing(result)
        except Exception as e:
            logger.warning("[dosing] 캐시 저장 실패(%s): %s", cache_key, e)
    return result
