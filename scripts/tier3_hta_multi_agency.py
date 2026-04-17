"""Tier-3 Multi-HTA — 4개 HTA 기관 × 3 LLM 교차검증

대상 기관: NICE (UK) · PBAC (AU) · HAS (FR) · G-BA (DE)
파일럿 약제: pembrolizumab 1L NSCLC PD-L1 high (Keytruda)

결과:
  data/design_panel/tier3_multi_hta_<product>.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from agents.research.cross_validator import cross_validate
from agents.research.hta_registry import (
    COMMON_SYSTEM,
    HTA_REGISTRY,
    build_prompt,
    get_spec,
)

# ── 파일럿 대상
PRODUCT    = "pembrolizumab (Keytruda)"
INDICATION = "first-line monotherapy for advanced/metastatic non-small-cell lung cancer (NSCLC) with PD-L1 high expression (TPS ≥50%), EGFR/ALK wild-type"

# 환경변수로 특정 기관만 선택 가능 (예: HTA_AGENCIES=nice,pbac)
import os
AGENCIES = os.environ.get("HTA_AGENCIES", "").split(",") if os.environ.get("HTA_AGENCIES") else list(HTA_REGISTRY.keys())
AGENCIES = [a.strip() for a in AGENCIES if a.strip() in HTA_REGISTRY]


def run_agency(code: str) -> dict:
    spec = get_spec(code)
    prompt = build_prompt(code, product=PRODUCT, indication=INDICATION)
    print(f"\n{'─' * 72}")
    print(f"▶ {spec['name']} ({spec['country']}) — {spec['description']}")
    print(f"{'─' * 72}")
    print(f"  필드: {', '.join(spec['fields'])}")
    print(f"  병렬 호출 중... (timeout 180s)")

    result = cross_validate(
        prompt=prompt,
        system=COMMON_SYSTEM,
        expected_fields=spec["fields"],
        narrative_fields=spec["narrative_fields"],
        timeout=180,
    )

    # 요약 출력
    s = result["summary"]
    print(f"\n  활성 소스 {s['sources_active']}/3 · "
          f"✅{s['agree_count']} ❌{s['conflict_count']} "
          f"⚠️ {s['single_source']} 📝{s.get('narrative_count',0)} ∅{s['missing_count']}")

    # 필드별 한 줄 요약
    fmt = lambda v, w=26: (str(v)[:w-1] + "…") if len(str(v)) > w else str(v).ljust(w)
    icon_map = {"agree": "✅", "conflict": "❌", "single": "⚠️", "missing": "∅", "narrative": "📝"}
    srcs = list(result["responses"].keys())
    print(f"  {'필드':<22s}" + "".join(f" | {s[:8]:<8s}" for s in srcs))
    for field in spec["fields"]:
        row = result["matrix"].get(field, {})
        status = result["consensus"].get(field, {}).get("status", "?")
        icon = icon_map.get(status, "?")
        cells = "".join(f" | {fmt(row.get(s, '∅'), 8)}" for s in srcs)
        print(f"  {icon} {field:<20s}{cells}")

    return result


def main():
    print("=" * 72)
    print(f"TIER-3 MULTI-HTA 파일럿 — {PRODUCT}")
    print(f"대상 기관: {', '.join(get_spec(a)['name'] for a in AGENCIES)}")
    print("=" * 72)

    all_results = {}
    for code in AGENCIES:
        try:
            all_results[code] = run_agency(code)
        except Exception as e:
            print(f"\n❌ {code} 실행 실패: {e}")
            all_results[code] = {"error": str(e)}

    # ── 전체 요약
    print("\n" + "=" * 72)
    print("SUMMARY — 기관별 교차검증 통계")
    print("=" * 72)
    print(f"  {'기관':<10s} | {'소스':>4s} | {'✅합의':>6s} | {'❌충돌':>6s} | {'📝서술':>6s} | {'⚠️단일':>6s} | {'∅누락':>6s}")
    print(f"  {'-' * 10} | {'-' * 4} | {'-' * 6} | {'-' * 6} | {'-' * 6} | {'-' * 6} | {'-' * 6}")
    for code, r in all_results.items():
        if "error" in r:
            continue
        s = r["summary"]
        name = get_spec(code)["name"]
        print(f"  {name:<10s} | {s['sources_active']:>4d} | {s['agree_count']:>6d} | "
              f"{s['conflict_count']:>6d} | {s.get('narrative_count',0):>6d} | "
              f"{s['single_source']:>6d} | {s['missing_count']:>6d}")

    # ── 저장
    out_path = BASE_DIR / "data" / "design_panel" / "tier3_multi_hta_keytruda.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clean: dict = {}
    for code, r in all_results.items():
        if "error" in r:
            clean[code] = r
            continue
        clean[code] = {
            "agency":    get_spec(code),
            "responses": {
                k: {kk: vv for kk, vv in v.items() if kk != "raw"}
                for k, v in r["responses"].items()
            },
            "matrix":    r["matrix"],
            "consensus": r["consensus"],
            "flags":     r["flags"],
            "summary":   r["summary"],
        }
    # HTASpec 은 set 을 포함 → 직렬화용 변환
    for code in clean:
        if "agency" in clean[code]:
            clean[code]["agency"] = {
                k: (sorted(v) if isinstance(v, set) else v)
                for k, v in clean[code]["agency"].items()
            }

    out_path.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n💾 전체 결과: {out_path}")


if __name__ == "__main__":
    main()
