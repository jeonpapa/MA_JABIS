"""Cross-Validator — 3개 LLM 의 JSON 응답을 필드 단위로 교차검증

파이프라인:
  1. 동일한 구조화 질의를 3 LLM 에 병렬 전송
  2. 각 응답에서 JSON 블록 추출
  3. 필드별로 값 비교 → 일치 / 불일치 / 고유 태깅
  4. 신뢰도 등급 + 사용자 리뷰 플래그 부여

사용:
    from agents.research.cross_validator import cross_validate
    result = cross_validate(
        prompt="...",
        system="...",
        expected_fields=["ta_number", "decision", "icer_value", ...],
    )
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .clients import ask_gemini_grounded, ask_openai, ask_perplexity


# LLM 소스 정의 — 이름과 콜러블
SOURCES: list[tuple[str, Callable[..., dict]]] = [
    ("gemini",     ask_gemini_grounded),
    ("perplexity", ask_perplexity),
    ("openai",     ask_openai),
]


def _extract_json(text: str) -> dict | None:
    """응답 본문에서 JSON 블록 추출. ```json ... ``` 또는 raw JSON 지원."""
    if not text:
        return None
    # 1) ```json 펜스
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 2) 첫 { ... } 블록
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _normalize(v) -> str:
    """값 비교용 정규화 — 공백·대소문자·마침표 제거."""
    if v is None:
        return "∅"
    s = str(v).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.rstrip(".")
    return s


def cross_validate(
    prompt: str,
    system: str | None = None,
    expected_fields: list[str] | None = None,
    narrative_fields: set[str] | None = None,
    sources: list[tuple[str, Callable]] | None = None,
    timeout: int = 180,
) -> dict:
    """병렬 LLM 질의 → 필드 단위 교차검증.

    Args:
        prompt:            사용자 질의 (JSON 응답을 요청하도록 작성)
        system:            시스템 프롬프트
        expected_fields:   비교 대상 필드명 목록. None 이면 모든 응답 필드 취합
        narrative_fields:  서술형 (paraphrase 허용) 필드. 충돌 판정 대신 집계
                           예: {"rationale", "indication_scope"}
        sources:           [(name, callable), ...] — 기본값: Gemini/Perplexity/OpenAI
        timeout:           각 LLM 호출 타임아웃 (초)

    Returns:
        {
          "responses":   {src: {text, citations, parsed, error}},
          "matrix":      {field: {src: value}},
          "consensus":   {field: {status, value, sources_agreeing}},
          "flags":       [{field, issue, ...}],
          "summary":     {agree_count, conflict_count, missing_count, single_source, narrative_count},
        }
    """
    narrative_fields = narrative_fields or set()
    if sources is None:
        sources = SOURCES

    # ── 1. 병렬 질의
    def _call(name_fn):
        name, fn = name_fn
        try:
            r = fn(prompt, system=system, timeout=timeout)
            return name, r
        except Exception as e:
            return name, {"source": name, "error": str(e)}

    with ThreadPoolExecutor(max_workers=len(sources)) as ex:
        futures = [ex.submit(_call, s) for s in sources]
        raw = dict(f.result() for f in futures)

    # ── 2. JSON 파싱
    responses: dict = {}
    for name, r in raw.items():
        if "error" in r:
            responses[name] = {"error": r["error"], "parsed": None}
            continue
        parsed = _extract_json(r.get("text", ""))
        responses[name] = {
            "text":      r.get("text", ""),
            "citations": r.get("citations", []),
            "parsed":    parsed,
            "source":    r.get("source", name),
        }

    # ── 3. 필드 수집
    all_fields: set[str] = set()
    for r in responses.values():
        if r.get("parsed"):
            all_fields.update(r["parsed"].keys())
    if expected_fields:
        all_fields |= set(expected_fields)

    # ── 4. 필드 단위 매트릭스
    matrix: dict[str, dict[str, object]] = {}
    for field in sorted(all_fields):
        row: dict[str, object] = {}
        for src in responses:
            parsed = responses[src].get("parsed")
            if parsed is None:
                row[src] = "⚠️ (응답 없음)"
            else:
                row[src] = parsed.get(field, "∅")
        matrix[field] = row

    # ── 5. 합의·충돌·단일 판정
    consensus: dict[str, dict] = {}
    flags: list[dict] = []
    agree_n = conflict_n = missing_n = single_n = narrative_n = 0

    for field, row in matrix.items():
        norms: dict[str, str] = {}
        for src, val in row.items():
            # 응답 없음 / 필드 누락 / null 값은 모두 '미제공' 으로 취급
            if val is None:
                continue
            if isinstance(val, str) and val.startswith("⚠️"):
                continue
            if val == "∅":
                continue
            norms[src] = _normalize(val)

        unique_vals = set(norms.values())
        n_sources = len(norms)

        # ── 서술형 필드: paraphrase 차이 를 충돌로 취급하지 않음
        if field in narrative_fields:
            if n_sources == 0:
                consensus[field] = {"status": "missing", "value": None, "sources": []}
                missing_n += 1
                flags.append({"field": field, "issue": "모든 소스가 값을 제공하지 않음"})
            else:
                # 소스별 원본 텍스트 를 모두 보존 (유저가 직접 통합)
                consensus[field] = {
                    "status": "narrative",
                    "values": {src: row[src] for src in norms.keys()},
                    "sources": sorted(norms.keys()),
                }
                narrative_n += 1
                if n_sources >= 2:
                    flags.append({
                        "field": field,
                        "issue": f"서술형 필드 — {n_sources}개 소스 표현 차이 (유저가 통합 필요)",
                        "severity": "info",
                    })
            continue

        if n_sources == 0:
            consensus[field] = {"status": "missing", "value": None, "sources": []}
            missing_n += 1
            flags.append({"field": field, "issue": "모든 소스가 값을 제공하지 않음"})
        elif len(unique_vals) == 1 and n_sources >= 2:
            src_list = sorted(norms.keys())
            agreed = next(iter(unique_vals))
            # 원본 값 복원 (첫 소스)
            orig = next(row[s] for s in src_list if _normalize(row[s]) == agreed)
            consensus[field] = {
                "status": "agree",
                "value": orig,
                "sources": src_list,
            }
            agree_n += 1
        elif n_sources == 1:
            only_src = next(iter(norms))
            consensus[field] = {
                "status": "single",
                "value": row[only_src],
                "sources": [only_src],
            }
            single_n += 1
            flags.append({
                "field": field,
                "issue": f"단일 소스 ({only_src}) 만 값 제공 — 교차검증 불가",
            })
        else:
            # 충돌
            by_val: dict[str, list[str]] = {}
            for src, norm in norms.items():
                by_val.setdefault(norm, []).append(src)
            consensus[field] = {
                "status": "conflict",
                "values": {v: row[s[0]] for v, s in by_val.items()},
                "grouped_sources": by_val,
            }
            conflict_n += 1
            flags.append({
                "field": field,
                "issue": "소스 간 값 충돌 — 사용자 리뷰 필요",
                "groups": by_val,
            })

    return {
        "responses": responses,
        "matrix": matrix,
        "consensus": consensus,
        "flags": flags,
        "summary": {
            "agree_count":     agree_n,
            "conflict_count":  conflict_n,
            "missing_count":   missing_n,
            "single_source":   single_n,
            "narrative_count": narrative_n,
            "total_fields":    len(all_fields),
            "sources_active":  sum(1 for r in responses.values() if r.get("parsed") is not None),
        },
    }
