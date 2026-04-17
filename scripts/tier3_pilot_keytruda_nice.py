"""Tier-3 Pilot — Keytruda × NICE TA 교차검증 데모

목적:
  3개 LLM (Gemini grounded / Perplexity sonar-pro / OpenAI GPT-5) 에게
  동일한 구조화 질의를 던지고, JSON 필드별로 교차검증 결과를 보여준다.

사용:
    python3 scripts/tier3_pilot_keytruda_nice.py

결과:
  data/design_panel/tier3_pilot_result.json  에 저장 + 콘솔 출력
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from agents.research.cross_validator import cross_validate

OUT_PATH = BASE_DIR / "data" / "design_panel" / "tier3_pilot_result.json"

SYSTEM = """당신은 제약 산업 HTA (Health Technology Assessment) 리서치 전문가입니다.
답변은 반드시 JSON 블록만 출력하세요. 설명/주석/마크다운 헤더는 포함하지 마세요.
확실하지 않은 필드는 null 로 표기하되, 절대 추측하지 마세요."""

PROMPT = """Find NICE Technology Appraisal guidance for pembrolizumab (Keytruda) as
first-line monotherapy in adults with previously untreated advanced/metastatic
non-small-cell lung cancer (NSCLC) with PD-L1 high expression.

Return a single JSON object with exactly these keys:
{
  "ta_number":          "e.g. TA531",
  "decision":           "Recommended | Not recommended | Optimised | Conditional",
  "decision_date":      "YYYY-MM",
  "indication_scope":   "브리핑용 1~2문장 요약 (PD-L1 임계값 등 제한조건 포함)",
  "icer_value_gbp":     "주요 ICER 수치 in £/QALY (없으면 null)",
  "pas_applied":        true | false | null,
  "rationale":          "NICE가 왜 이 결정을 내렸는지 100단어 이내로 요약",
  "source_url":         "NICE 공식 TA 페이지 URL"
}

Return ONLY the JSON — no prose before or after."""

EXPECTED_FIELDS = [
    "ta_number", "decision", "decision_date", "indication_scope",
    "icer_value_gbp", "pas_applied", "rationale", "source_url",
]

# 서술형 필드 — paraphrase 변형 을 충돌로 취급하지 않음
NARRATIVE_FIELDS = {"indication_scope", "rationale"}


def main():
    print("=" * 72)
    print("TIER-3 PILOT — Keytruda × NICE TA 교차검증")
    print("=" * 72)
    print("\n질의 대상: pembrolizumab 1L NSCLC monotherapy, PD-L1 high")
    print("소스: Gemini 2.5-pro (grounded) · Perplexity sonar-pro · OpenAI GPT-5\n")
    print("병렬 호출 중... (최대 180초)\n")

    result = cross_validate(
        prompt=PROMPT,
        system=SYSTEM,
        expected_fields=EXPECTED_FIELDS,
        narrative_fields=NARRATIVE_FIELDS,
        timeout=180,
    )

    # ── 소스별 상태
    print("── 소스별 응답 상태 ────────────────────────────────────────────────")
    for src, r in result["responses"].items():
        if "error" in r:
            print(f"  ❌ {src:10s} : {r['error'][:80]}")
        elif r.get("parsed") is None:
            print(f"  ⚠️  {src:10s} : JSON 파싱 실패 (응답 {len(r.get('text',''))}자)")
        else:
            n_cites = len(r.get("citations", []))
            print(f"  ✅ {src:10s} : {len(r['parsed'])} 필드 · citations {n_cites}")

    # ── 필드별 매트릭스
    print("\n── 필드별 교차검증 매트릭스 ────────────────────────────────────────")
    fmt_cell = lambda v, w: (str(v)[:w-1] + "…") if len(str(v)) > w else str(v).ljust(w)

    srcs = list(result["responses"].keys())
    # header
    print(f"  {'필드':<20s}" + "".join(f" | {s[:22]:<22s}" for s in srcs))
    print("  " + "-" * (20 + 25 * len(srcs)))
    for field in EXPECTED_FIELDS:
        row = result["matrix"].get(field, {})
        status = result["consensus"].get(field, {}).get("status", "?")
        icon = {
            "agree": "✅", "conflict": "❌", "single": "⚠️",
            "missing": "∅", "narrative": "📝",
        }.get(status, "?")
        cells = "".join(f" | {fmt_cell(row.get(s, '∅'), 22)}" for s in srcs)
        print(f"  {icon} {field:<18s}{cells}")

    # ── 플래그
    if result["flags"]:
        print("\n── 사용자 리뷰 필요 항목 ───────────────────────────────────────────")
        for f in result["flags"]:
            print(f"  ⚠️  {f['field']}: {f['issue']}")

    # ── 요약
    s = result["summary"]
    print("\n── 요약 ───────────────────────────────────────────────────────────")
    print(f"  활성 소스     : {s['sources_active']} / {len(srcs)}")
    print(f"  전체 필드     : {s['total_fields']}")
    print(f"  ✅ 합의       : {s['agree_count']}")
    print(f"  ❌ 충돌       : {s['conflict_count']}")
    print(f"  ⚠️  단일소스    : {s['single_source']}")
    print(f"  📝 서술형      : {s.get('narrative_count', 0)}")
    print(f"  ∅ 누락       : {s['missing_count']}")

    # ── 저장
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # raw 는 크기 커서 제외
    clean = {
        "responses": {
            k: {kk: vv for kk, vv in v.items() if kk != "raw"}
            for k, v in result["responses"].items()
        },
        "matrix":    result["matrix"],
        "consensus": result["consensus"],
        "flags":     result["flags"],
        "summary":   result["summary"],
    }
    OUT_PATH.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n💾 전체 결과: {OUT_PATH}")


if __name__ == "__main__":
    main()
