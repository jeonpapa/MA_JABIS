"""용량 정규화 LLM 모델 시뮬레이션 (3c) — 4모델을 골든셋으로 채점해 최적 1개 선정.

후보: gpt-4o, gpt-4o-mini (OpenAI) / gemini-2.5-flash (Gemini) / sonar-pro (Perplexity).
채점: reference_mg 정확 + 국가별 factor(상대오차≤1%) + 복합제 null 정확 + 결정성(2회 동일).
실행: .venv/bin/python -m tests.sim_dose_normalize_models
결과: tests/dose_norm_model_report.json (선정 모델 + 모델별 점수표).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from agents.foreign_dose_normalize import build_norm_prompt, call_norm_model

logger = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent
GOLDEN = BASE / "golden_dose_normalize.json"
REPORT = BASE / "dose_norm_model_report.json"

CANDIDATES = ["gpt-4o", "gpt-4o-mini", "gemini-2.5-flash", "sonar-pro"]
FACTOR_TOL = 0.01   # 상대오차 1%


def _factor_ok(expected, got) -> bool:
    if expected is None:
        return got is None  # 복합제: null 이어야 정답
    if got is None:
        return False
    try:
        return abs(float(got) - float(expected)) / float(expected) <= FACTOR_TOL
    except (ValueError, TypeError, ZeroDivisionError):
        return False


def _ref_ok(expected, got) -> bool:
    if expected is None:
        return got is None or got == 0  # 복합제는 ref 무관(관대)
    if got is None:
        return False
    try:
        return abs(float(got) - float(expected)) / float(expected) <= FACTOR_TOL
    except (ValueError, TypeError, ZeroDivisionError):
        return False


def _score_case(result: dict | None, expect: dict) -> dict:
    """단일 케이스 채점 → {ref_ok, factor_pass, factor_total, ok}."""
    if not result:
        total = len(expect.get("factors", {}))
        return {"ref_ok": False, "factor_pass": 0, "factor_total": total, "ok": False}
    countries = result.get("countries") or {}
    ref_ok = _ref_ok(expect.get("reference_strength_mg"), result.get("reference_strength_mg"))
    fpass, ftotal = 0, 0
    for c, exp_factor in expect.get("factors", {}).items():
        ftotal += 1
        got = (countries.get(c) or {}).get("factor")
        if _factor_ok(exp_factor, got):
            fpass += 1
    ok = ref_ok and fpass == ftotal
    return {"ref_ok": ref_ok, "factor_pass": fpass, "factor_total": ftotal, "ok": ok}


def _same(a: dict | None, b: dict | None) -> bool:
    """결정성 — 두 응답의 (ref + 국가별 factor) 동일 여부."""
    if not a or not b:
        return a == b
    if a.get("reference_strength_mg") != b.get("reference_strength_mg"):
        return False
    ca, cb = a.get("countries") or {}, b.get("countries") or {}
    if set(ca) != set(cb):
        return False
    return all((ca[c] or {}).get("factor") == (cb[c] or {}).get("factor") for c in ca)


def run() -> dict:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = golden["cases"]
    report = {"candidates": CANDIDATES, "models": {}}

    for model in CANDIDATES:
        per_case, cases_ok, factor_pass, factor_total, det_ok = [], 0, 0, 0, 0
        for case in cases:
            rows = case["items"]
            sysp, usrp = build_norm_prompt(case["drug"], rows)
            r1 = call_norm_model(model, sysp, usrp)
            r2 = call_norm_model(model, sysp, usrp)
            sc = _score_case(r1, case["expect"])
            deterministic = _same(r1, r2)
            per_case.append({"id": case["id"], **sc, "deterministic": deterministic})
            cases_ok += int(sc["ok"])
            factor_pass += sc["factor_pass"]
            factor_total += sc["factor_total"]
            det_ok += int(deterministic)
            logger.info("[%s] %s ok=%s factor=%d/%d det=%s",
                        model, case["id"], sc["ok"], sc["factor_pass"],
                        sc["factor_total"], deterministic)
        report["models"][model] = {
            "cases_ok": cases_ok, "cases_total": len(cases),
            "factor_pass": factor_pass, "factor_total": factor_total,
            "factor_accuracy": round(factor_pass / factor_total, 4) if factor_total else 0,
            "determinism": round(det_ok / len(cases), 4),
            "per_case": per_case,
        }

    # 선정: factor_accuracy 주 → cases_ok → determinism 순
    def key(m):
        s = report["models"][m]
        return (s["factor_accuracy"], s["cases_ok"], s["determinism"])
    ranked = sorted(CANDIDATES, key=key, reverse=True)
    report["selected"] = ranked[0]
    report["ranking"] = ranked
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rep = run()
    print("\n===== 모델별 점수 =====")
    for m in rep["ranking"]:
        s = rep["models"][m]
        print(f"  {m:18} factor={s['factor_accuracy']*100:5.1f}% "
              f"cases={s['cases_ok']}/{s['cases_total']} det={s['determinism']*100:4.0f}%")
    print(f"\n선정 모델: {rep['selected']}")
