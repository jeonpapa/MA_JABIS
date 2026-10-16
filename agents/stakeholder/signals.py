"""정책 시그널 — 소스 repo `stakeholder_service.py` 의 PolicySignalStore 스키마 1:1 이식 + 근거 locator 대조 + API 모양.

`data/policy_signals.json` 은 외부 에이전트(Key/Hermes) 소유 산출물이다. 대쉬보드는 검증 후 읽기 전용으로 미러한다
(sync._import_signals). 분리 원칙(소스 README 규칙 6): 정책 방향성 예측은 `status='analyst_hypothesis'` 이며
공식 정책·직접 발언이 아니다. `confirmed_evidence`(확인된 근거) · `analyst_inference`(분석 추론) · `not_confirmed`
(미확인 범위)는 저장·응답 어디서도 섞지 않는다. confidence 두 값은 분석자 판단 [0,1] 이지 보정된 확률이 아니다.

근거 locator 판정(`locator_match`) — 정본 인용의 range|file|none|unchecked 어휘를 쓰되, claim 은 요약(의역)이라 원문 문자열
대조를 하지 않고 **locator 가 인용 출처의 추출 텍스트 안을 가리키는지**를 잰다. 텍스트는 절대 고치지 않는다.
  range     locator 경로 = 그 source 의 text_path, 볼륨 파일 있음, 모든 줄 범위가 파일 안이고 빈 줄만이 아님
  file      경로는 맞고 파일도 있으나 줄 범위가 없거나(파일 전체) 파일 길이를 벗어남/빈 줄뿐
  none      source_id 가 manifest(sv_source)에 없음 · locator 해석 불가 · 경로가 그 출처의 text_path 와 다름
  unchecked 경로는 맞으나 볼륨에서 파일을 읽을 수 없음(누락·허용목록 밖)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from . import paths

SIGNALS_REL = "data/policy_signals.json"


# ── 소스 stakeholder_service.py 와 1:1 (필드·타입·기본값·extra="forbid"·[0,1] 제약) ──────────────────

class SignalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignalEvidence(SignalModel):
    date: str
    person: str
    role_at_event: str
    evidence_grade: str
    evidence_role: str
    claim: str
    source_id: str
    locator: str
    policy_status: str | None = None
    scope_note: str | None = None


class SignalBenefit(SignalModel):
    benefit: str
    modality: str
    rationale: str
    basis_source_ids: list[str]


class SignalInference(SignalModel):
    likely_contract_architecture: str
    payer_benefits: list[SignalBenefit]
    most_likely_first_use: str
    selective_use: str
    composite_scope: dict[str, str]


class ConfidenceMethodology(SignalModel):
    scale: str
    method: str
    direction_basis: str
    implementation_basis: str
    limitation: str
    reviewed_by: str
    next_review_trigger: str


class PolicySignal(SignalModel):
    signal_id: str
    title: str
    institution: str
    signal_type: str
    status: str
    as_of: str
    direction_confidence: float = Field(ge=0, le=1)
    implementation_model_confidence: float = Field(ge=0, le=1)
    confidence_methodology: ConfidenceMethodology
    thesis: str
    confirmed_evidence: list[SignalEvidence]
    analyst_inference: SignalInference
    not_confirmed: str
    watch_triggers: list[str]


class PolicySignalStore(SignalModel):
    schema_version: str
    updated_at: str
    signals: list[PolicySignal]


class SignalSchemaError(ValueError):
    """policy_signals.json 이 스키마를 어김 — 메시지는 위치·이유 요약(최대 5건)."""


def validate_payload(data: Any) -> PolicySignalStore:
    """소스 GET /api/signals 와 같은 검증 + 저장 제약(signal_id 유일 — 테이블 키). 실패는 SignalSchemaError."""
    try:
        validated = PolicySignalStore.model_validate(data)
    except PydanticValidationError as exc:
        errs = exc.errors()
        parts = [f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg')}" for e in errs[:5]]
        more = f" 외 {len(errs) - 5}건" if len(errs) > 5 else ""
        raise SignalSchemaError(f"스키마 위반 {len(errs)}건 — " + "; ".join(parts) + more) from exc
    ids = [s.signal_id for s in validated.signals]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise SignalSchemaError(f"signal_id 중복: {dup}")
    return validated


def sort_signals(items: list, key=lambda s: s.as_of) -> list:
    """소스와 같은 정렬 — as_of(문자열) 내림차순, 같은 날은 파일 순서(안정 정렬)."""
    return sorted(items, key=key, reverse=True)


# ── locator 판정 ────────────────────────────────────────────────────────────────────

_RE_LOC = re.compile(r"^(?P<path>[^:\s]+)(?::(?P<spec>\s*\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*\s*))?$")


def parse_locator(locator: Any) -> tuple[str, list[tuple[int, int]]] | None:
    """`sources/<file>.txt[:a[-b][, c-d …]]` → (path, [(a,b)…]). 해석 불가는 None."""
    if not isinstance(locator, str):
        return None
    m = _RE_LOC.match(locator.strip())
    if not m:
        return None
    ranges: list[tuple[int, int]] = []
    for part in (m.group("spec") or "").split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        ranges.append((int(a), int(b) if b.strip() else int(a)))
    return m.group("path"), ranges


def locator_status(root: str | Path, source: dict | None, locator: Any) -> tuple[str, str | None]:
    """(locator_match, 사유|None). 위 모듈 docstring 의 정의. 파일·DB 를 고치지 않는다."""
    if source is None:
        return "none", "source_id 가 manifest(sv_source)에 없음"
    parsed = parse_locator(locator)
    if parsed is None:
        return "none", f"locator 형식 해석 불가: {locator!r}"
    rel, ranges = parsed
    text_path = source.get("text_path")
    if not text_path:
        return "none", "인용 출처에 text_path(추출 텍스트)가 없음"
    if rel != text_path:
        return "none", f"locator 경로({rel})가 인용 출처의 text_path({text_path})와 다름"
    if not paths.is_safe_rel_path(rel):
        return "none", f"안전하지 않은 경로: {rel!r}"
    if not paths.is_sync_allowed(rel):
        return "unchecked", "추출 텍스트가 sync 허용목록 밖(excluded)"
    try:
        f = paths.resolve_under(root, rel)
    except ValueError:
        return "none", f"안전하지 않은 경로: {rel!r}"
    if not f.is_file():
        return "unchecked", f"추출 텍스트 파일이 볼륨에 없음: {rel}"
    try:
        lines = f.read_bytes().decode("utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return "unchecked", f"추출 텍스트를 읽을 수 없음: {rel}"
    if not ranges:
        return "file", "줄 범위 없음 — 파일 전체를 가리킴"
    for a, b in ranges:
        if a < 1 or b < a or b > len(lines):
            return "file", f"줄 범위 {a}-{b} 가 파일({len(lines)}줄)을 벗어남"
        if not any(x.strip() for x in lines[a - 1:b]):
            return "file", f"줄 범위 {a}-{b} 가 빈 줄뿐"
    return "range", None


# ── 저장 행 ──────────────────────────────────────────────────────────────────────────

def build_rows(data: dict, validated: PolicySignalStore, root: str | Path,
               sources: dict[str, dict]) -> tuple[list[dict], list[dict], list[str]]:
    """(signal 행, evidence 행, 경고). 원문 객체(data['signals'][i])를 raw_json 으로 통째 보존하고, 승격 컬럼은
    검증된 값. 근거 텍스트(claim 등)는 파일 문자열 그대로."""
    import json

    def j(v: Any) -> str:
        return json.dumps(v, ensure_ascii=False, sort_keys=True)

    sig_rows: list[dict] = []
    ev_rows: list[dict] = []
    warnings: list[str] = []
    for pos, (raw, sig) in enumerate(zip(data["signals"], validated.signals)):
        sig_rows.append({
            "signal_id": sig.signal_id, "position": pos, "title": sig.title, "institution": sig.institution,
            "signal_type": sig.signal_type, "status": sig.status, "as_of": sig.as_of,
            "direction_confidence": sig.direction_confidence,
            "implementation_model_confidence": sig.implementation_model_confidence,
            "thesis": sig.thesis, "not_confirmed": sig.not_confirmed,
            "analyst_inference_json": j(raw["analyst_inference"]),
            "confidence_methodology_json": j(raw["confidence_methodology"]),
            "watch_triggers_json": j(raw["watch_triggers"]), "raw_json": j(raw)})
        for idx, (eraw, ev) in enumerate(zip(raw["confirmed_evidence"], sig.confirmed_evidence)):
            match, detail = locator_status(root, sources.get(ev.source_id), ev.locator)
            if ev.source_id not in sources:
                warnings.append(f"policy_signals {sig.signal_id} 근거[{idx}]: source_id {ev.source_id} 가 manifest 에 없음")
            ev_rows.append({
                "signal_id": sig.signal_id, "idx": idx, "date": ev.date, "person": ev.person,
                "role_at_event": ev.role_at_event, "evidence_grade": ev.evidence_grade,
                "evidence_role": ev.evidence_role, "claim": ev.claim, "source_id": ev.source_id,
                "locator": ev.locator, "policy_status": ev.policy_status, "scope_note": ev.scope_note,
                "locator_match": match, "locator_detail": detail, "raw_json": j(eraw)})
        for b in sig.analyst_inference.payer_benefits:
            for sid in b.basis_source_ids:
                if sid not in sources:
                    warnings.append(f"policy_signals {sig.signal_id} 추론 basis_source_ids: {sid} 가 manifest 에 없음")
    return sig_rows, ev_rows, warnings


# ── API 모양 (GET /api/stakeholder/signals) ─────────────────────────────────────────────

def api_payload(rows: list[dict], sources: dict[str, dict], meta: dict[str, Any]) -> dict:
    """소스 GET /api/signals 와 같은 모양(model_dump — 선택 필드는 null 로 존재) + 근거마다 locator_match·
    integrity_status·source{publisher_title,url,publication_date} + import_status. 정렬 as_of 내림차순."""
    out: list[tuple[str, dict]] = []
    for r in rows:                                          # position 순 → 안정 정렬로 동률은 파일 순서
        sig = PolicySignal.model_validate(r["raw"]).model_dump()
        ev_rows = {e["idx"]: e for e in r.get("evidence") or []}
        for i, ev in enumerate(sig["confirmed_evidence"]):
            row = ev_rows.get(i) or {}
            src = sources.get(ev["source_id"])
            ev["locator_match"] = row.get("locator_match") or "unchecked"
            ev["integrity_status"] = src.get("integrity_status") if src else None
            ev["source"] = ({"publisher_title": src.get("title"), "url": src.get("url"),
                             "publication_date": src.get("publication_date")} if src else None)
        out.append((sig["as_of"], sig))
    signals = [s for _, s in sort_signals(out, key=lambda t: t[0])]
    return {"schema_version": meta.get("schema_version"), "updated_at": meta.get("updated_at"),
            "signal_count": len(signals), "signals": signals,
            "import_status": {"state": meta.get("state") or "absent", "error": meta.get("error")}}
