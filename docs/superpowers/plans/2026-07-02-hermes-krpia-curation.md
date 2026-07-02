# 헤르메스 KRPIA 큐레이션 파이프라인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 신규 KRPIA 메일이 유입돼도 대쉬보드 품질이 유지되도록, GPT-5.5 기반 외부 에이전트 헤르메스가 메일별 내용 기반 분석(요약·MSD시사점·severity·근거)을 생성해 비공개 Git 채널로 발행하고, 리더가 sha256 게이트로 검증해 반영(없으면 기존 규칙 폴백)한다.

**Architecture:** 원본 결정론 인제스트는 불변. 헤르메스는 `analysis/<event_id>.json` 사이드카만 얹는다. `content_fingerprint`(원본 메일 sha + 문서 sha)로 stale 자동 감지 → 유효하면 LLM 값, 아니면 `TOPIC_RULES` 상수 폴백. 발행은 reimb와 동일한 AccessRoutineAnalystic 비공개 repo + prod sha256 멱등 sync.

**Tech Stack:** Python 3.12, pytest, Flask(api/server.py), APScheduler(scheduler.py), React/TypeScript(Vite). 신규 모듈 `agents/policy_analysis.py`, 기준 문서 `agents/rules/*.md` + `agents/ingest/*README.md`.

**설계 스펙:** `docs/superpowers/specs/2026-07-02-hermes-krpia-curation-design.md`

---

## File Structure

| 파일 | 책임 | 상태 |
|---|---|---|
| `agents/policy_analysis.py` | fingerprint·사이드카 load/valid·validate(grounding+용어가드)·resolve_curation·경로 유틸(default_root/remap). **repo 의존 없음, 단방향 소스** | Create |
| `agents/policy_intelligence.py` | 리더. policy_analysis import 후 판단필드에 curation 적용 + 폴백 + curation_source + overview 카운트 | Modify |
| `agents/ingest/policy_analysis_sync.py` | AccessRoutineAnalystic git raw → `<root>/analysis/` 멱등 sync | Create |
| `scheduler.py` | `policy_intel_analysis_sync` 잡 등록(02:10 + boot) | Modify |
| `agents/rules/policy_intelligence_curation_rules.md` | 헤르메스 기준 권위 문서(가드레일·루브릭·용어표 임베드) | Create |
| `agents/ingest/POLICY_INTEL_CURATION_README.md` | 헤르메스 작업 런북 | Create |
| `CLAUDE.md` | 규칙 맵에 신규 rules 링크 | Modify |
| `agents/quality_guard/checks.py` 또는 rule_compliance checks | grounding·pending 커버리지 체크 등록 | Modify |
| `frontend/src/api/policyIntelligence.ts` | 신규 필드 타입 | Modify |
| `frontend/src/pages/policy-intelligence/page.tsx` + `components/policy/EventModal.tsx` | curation_source 뱃지 + evidence 표시 | Modify |
| `tests/test_policy_analysis.py` | fingerprint·gate·grounding·용어가드·폴백 | Create |
| `tests/test_policy_intelligence.py` | curation 적용 + 폴백 + overview 카운트 | Modify |

---

## Task 1: `agents/policy_analysis.py` — 경로 유틸 + fingerprint + 사이드카 load/valid

**Files:**
- Create: `agents/policy_analysis.py`
- Test: `tests/test_policy_analysis.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_policy_analysis.py
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import policy_analysis as pa


def _make_event(root: Path) -> dict:
    folder = root / "raw" / "gmail" / "evt1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "body.txt").write_text("약가 유연계약제 시행 안내 본문", encoding="utf-8")
    raw = b"raw-eml-bytes-evt1"
    (folder / "message_sha256.txt").write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="utf-8")
    text_path = root / "extracted" / "evt1" / "doc.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("첨부 본문: 유연계약 후보 품목", encoding="utf-8")
    return {
        "event_id": "evt1",
        "subject": "Fw: [KRPIA] 약가 유연계약제 안내",
        "raw_folder": str(folder),
        "documents": [{"filename": "a.pdf", "sha256": "docsha1", "text_path": str(text_path)}],
    }


def test_fingerprint_is_deterministic_and_content_sensitive(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    event = _make_event(root)
    fp1 = pa.content_fingerprint(event, root)
    fp2 = pa.content_fingerprint(event, root)
    assert fp1 == fp2 and len(fp1) == 64
    # 문서 sha 변경 → fingerprint 변경
    event2 = json.loads(json.dumps(event))
    event2["documents"][0]["sha256"] = "docsha2"
    assert pa.content_fingerprint(event2, root) != fp1


def test_load_and_valid_gate(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    event = _make_event(root)
    fp = pa.content_fingerprint(event, root)
    good = {
        "event_id": "evt1", "content_fingerprint": fp,
        "summary": "유연계약제 접수 안내", "severity": "high",
        "msd_implication": {"rationale": "r", "next_action": "n"},
    }
    pa.analysis_path("evt1", root).parent.mkdir(parents=True, exist_ok=True)
    pa.analysis_path("evt1", root).write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    loaded = pa.load_analysis("evt1", root)
    assert pa.analysis_valid(loaded, event, root) is True
    # fingerprint 불일치 → invalid
    stale = dict(good, content_fingerprint="deadbeef")
    assert pa.analysis_valid(stale, event, root) is False
    # 없음 → None + invalid
    assert pa.load_analysis("nope", root) is None
    assert pa.analysis_valid(None, event, root) is False
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_analysis.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.policy_analysis'`

- [ ] **Step 3: 최소 구현**

```python
# agents/policy_analysis.py
"""헤르메스 KRPIA 큐레이션 사이드카 — fingerprint·검증·경로 유틸.

repo 내 다른 모듈에 의존하지 않는 단방향 소스. policy_intelligence.py(리더)와
헤르메스 CLI 양쪽이 이 모듈의 fingerprint/검증을 공유해 두 환경에서 동일 판정한다.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ANALYSIS_SUBDIR = "analysis"
REQUIRED_FIELDS = ("event_id", "content_fingerprint", "summary", "severity", "msd_implication")
VALID_SEVERITY = {"high", "medium", "low", "High", "Medium", "Low",
                  "Very High", "Medium-High"}
# HIRA 이메일 트랙(scripts/render_hira_email_draft.py)에서 이식한 금지 토큰
BANNED_TOKENS = ["brdBltNo", "idxno", "PR-", "Precision", "Recall", "F1"]


def default_root() -> Path:
    configured = os.environ.get("POLICY_INTELLIGENCE_ROOT")
    if configured:
        return Path(configured)
    if Path("/app/data").exists():
        return Path("/app/data/policy_intelligence")
    return Path("/opt/data/policy_intelligence")


def remap_private_path(stored: str | None, root: Path) -> Path | None:
    """매니페스트 저장 절대경로를 현재 root 하위로 재매핑 + traversal 가드."""
    if not stored:
        return None
    norm = str(stored).replace("\\", "/")
    marker = "policy_intelligence/"
    idx = norm.find(marker)
    rel = norm[idx + len(marker):] if idx != -1 else norm.lstrip("/")
    root_res = Path(root).resolve()
    candidate = (root_res / rel).resolve()
    try:
        candidate.relative_to(root_res)
    except ValueError:
        return None
    return candidate if candidate.exists() else None


def content_fingerprint(event: dict[str, Any], root: str | Path) -> str:
    """원본 메일 해시(message_sha256, 없으면 body.txt sha) + 정렬된 문서 sha 결합."""
    root = Path(root)
    base = ""
    folder = remap_private_path(event.get("raw_folder"), root)
    if folder is not None:
        msg_sha = folder / "message_sha256.txt"
        body = folder / "body.txt"
        if msg_sha.exists():
            base = msg_sha.read_text(encoding="utf-8").strip()
        elif body.exists():
            base = hashlib.sha256(body.read_bytes()).hexdigest()
    doc_shas = sorted((d.get("sha256") or "") for d in (event.get("documents") or []))
    payload = "|".join([base] + doc_shas)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def analysis_path(event_id: str, root: str | Path) -> Path:
    return Path(root) / ANALYSIS_SUBDIR / f"{event_id}.json"


def load_analysis(event_id: str, root: str | Path) -> dict[str, Any] | None:
    path = analysis_path(event_id, root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def analysis_valid(analysis: dict[str, Any] | None, event: dict[str, Any], root: str | Path) -> bool:
    if not analysis:
        return False
    if any(not analysis.get(f) for f in REQUIRED_FIELDS):
        return False
    return analysis.get("content_fingerprint") == content_fingerprint(event, root)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_analysis.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: 커밋**

```bash
git add agents/policy_analysis.py tests/test_policy_analysis.py
git commit -m "feat(policy): 큐레이션 사이드카 fingerprint + valid 게이트"
```

---

## Task 2: `validate_analysis` — grounding + 용어가드

**Files:**
- Modify: `agents/policy_analysis.py`
- Test: `tests/test_policy_analysis.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# tests/test_policy_analysis.py 에 추가
def test_validate_grounding_and_terms(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    event = _make_event(root)
    fp = pa.content_fingerprint(event, root)
    base = {
        "event_id": "evt1", "content_fingerprint": fp,
        "summary": "유연계약제 접수 안내", "severity": "high",
        "msd_implication": {"rationale": "r", "next_action": "n"},
    }
    # 근거 없음 + data_gaps 없음 → 경고
    assert any("grounding" in w for w in pa.validate_analysis(base, event, root))
    # 소스에 없는 인용 → 경고
    bad_q = dict(base, evidence_quotes=[{"quote": "존재하지 않는 문장", "source": "body"}])
    assert any("not found in source" in w for w in pa.validate_analysis(bad_q, event, root))
    # 소스에 실재하는 인용 → 통과(경고 0)
    ok_q = dict(base, evidence_quotes=[{"quote": "유연계약 후보 품목", "source": "a.pdf"}])
    assert pa.validate_analysis(ok_q, event, root) == []
    # 금지 토큰 → 경고
    banned = dict(ok_q, summary="Precision 개선 안내")
    assert any("banned token" in w for w in pa.validate_analysis(banned, event, root))
    # '조건부 통과' → 경고
    cond = dict(ok_q, summary="조건부 통과 예상")
    assert any("조건부 통과" in w for w in pa.validate_analysis(cond, event, root))
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_analysis.py::test_validate_grounding_and_terms -q`
Expected: FAIL — `AttributeError: module 'agents.policy_analysis' has no attribute 'validate_analysis'`

- [ ] **Step 3: 구현 추가**

```python
# agents/policy_analysis.py 하단에 추가
def _event_texts(event: dict[str, Any], root: str | Path) -> str:
    root = Path(root)
    parts: list[str] = []
    folder = remap_private_path(event.get("raw_folder"), root)
    if folder is not None:
        body = folder / "body.txt"
        if body.exists():
            parts.append(body.read_text(encoding="utf-8", errors="replace"))
    for doc in event.get("documents") or []:
        tp = remap_private_path(doc.get("text_path"), root)
        if tp is not None:
            parts.append(tp.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts).casefold()


def validate_analysis(analysis: dict[str, Any], event: dict[str, Any], root: str | Path) -> list[str]:
    """헤르메스 초안 검증 → 경고 리스트(빈 리스트면 통과). CLI/리뷰 게이트가 사용."""
    warnings: list[str] = []
    for field in REQUIRED_FIELDS:
        if not analysis.get(field):
            warnings.append(f"missing required field: {field}")
    fp = content_fingerprint(event, root)
    if analysis.get("content_fingerprint") != fp:
        warnings.append(f"content_fingerprint mismatch (expected {fp})")
    if analysis.get("severity") not in VALID_SEVERITY:
        warnings.append(f"invalid severity: {analysis.get('severity')}")

    quotes = analysis.get("evidence_quotes") or []
    texts = _event_texts(event, root)
    for entry in quotes:
        quote = (entry.get("quote") or "").strip()
        if quote and quote.casefold() not in texts:
            warnings.append(f"evidence quote not found in source: {quote[:40]}")
    if not quotes and not (analysis.get("data_gaps")):
        warnings.append("no evidence_quotes and no data_gaps (grounding required)")

    haystack = json.dumps(analysis, ensure_ascii=False)
    for token in BANNED_TOKENS:
        if token in haystack:
            warnings.append(f"banned token found: {token}")
    if "조건부 통과" in haystack:
        warnings.append("avoid phrase '조건부 통과'; use official HIRA term")
    return warnings
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_analysis.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add agents/policy_analysis.py tests/test_policy_analysis.py
git commit -m "feat(policy): 큐레이션 검증 — grounding + 용어가드"
```

---

## Task 3: `resolve_curation` + 헤르메스 CLI(list-pending / validate)

**Files:**
- Modify: `agents/policy_analysis.py`
- Test: `tests/test_policy_analysis.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# tests/test_policy_analysis.py 에 추가
def test_resolve_curation_source(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    event = _make_event(root)
    # 분석 없음 → rule_fallback
    assert pa.resolve_curation(event, root)["curation_source"] == "rule_fallback"
    # 유효 분석 → hermes + 값 전달
    fp = pa.content_fingerprint(event, root)
    a = {"event_id": "evt1", "content_fingerprint": fp, "summary": "요약X",
         "severity": "high", "status": "진행중",
         "msd_implication": {"rationale": "r", "next_action": "n"}}
    p = pa.analysis_path("evt1", root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    got = pa.resolve_curation(event, root)
    assert got["curation_source"] == "hermes"
    assert got["summary"] == "요약X" and got["severity"] == "high"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_analysis.py::test_resolve_curation_source -q`
Expected: FAIL — `AttributeError: ... 'resolve_curation'`

- [ ] **Step 3: 구현 + CLI 추가**

```python
# agents/policy_analysis.py 하단에 추가
def resolve_curation(event: dict[str, Any], root: str | Path) -> dict[str, Any]:
    """유효 사이드카가 있으면 hermes 값, 아니면 rule_fallback 마커."""
    analysis = load_analysis(event.get("event_id", ""), root)
    if analysis and analysis_valid(analysis, event, root):
        return {
            "curation_source": "hermes",
            "summary": analysis.get("summary"),
            "severity": analysis.get("severity"),
            "status": analysis.get("status"),
            "msd_implication": analysis.get("msd_implication") or {},
            "evidence_quotes": analysis.get("evidence_quotes") or [],
            "confidence": analysis.get("confidence"),
        }
    return {"curation_source": "rule_fallback"}


def _iter_policy_events(root: Path) -> list[dict[str, Any]]:
    """manifests/latest 를 읽어 policy 이벤트 반환(리더 규칙과 동일: news lane 제외는 리더에 위임)."""
    from agents.policy_intelligence import _resolve_manifest_file, _is_policy_intelligence_event  # 지연 import
    manifest = json.loads(_resolve_manifest_file(root).read_text(encoding="utf-8"))
    return [e for e in (manifest.get("events") or []) if _is_policy_intelligence_event(e)]


def list_pending(root: str | Path) -> list[dict[str, Any]]:
    """분석 없음/stale 이벤트 목록 + 기대 fingerprint."""
    root = Path(root)
    out = []
    for event in _iter_policy_events(root):
        eid = event.get("event_id", "")
        analysis = load_analysis(eid, root)
        if not (analysis and analysis_valid(analysis, event, root)):
            out.append({"event_id": eid, "subject": event.get("subject"),
                        "topic": event.get("topic"),
                        "expected_fingerprint": content_fingerprint(event, root)})
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="헤르메스 KRPIA 큐레이션 도구")
    parser.add_argument("command", choices=["list-pending", "validate"])
    parser.add_argument("--event-id")
    parser.add_argument("--file", help="validate 대상 analysis json")
    parser.add_argument("--root", default=None)
    args = parser.parse_args()
    root = Path(args.root) if args.root else default_root()

    if args.command == "list-pending":
        print(json.dumps(list_pending(root), ensure_ascii=False, indent=2))
    else:
        events = {e.get("event_id"): e for e in _iter_policy_events(root)}
        analysis = json.loads(Path(args.file).read_text(encoding="utf-8"))
        event = events.get(analysis.get("event_id") or args.event_id)
        if event is None:
            print(json.dumps({"ok": False, "error": "event not found"}, ensure_ascii=False))
        else:
            warnings = validate_analysis(analysis, event, root)
            print(json.dumps({"ok": not warnings, "warnings": warnings}, ensure_ascii=False, indent=2))
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_analysis.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add agents/policy_analysis.py tests/test_policy_analysis.py
git commit -m "feat(policy): resolve_curation + 헤르메스 CLI(list-pending/validate)"
```

---

## Task 4: 리더 통합 — `policy_intelligence.py` curation 적용 + 폴백 + overview 카운트

**Files:**
- Modify: `agents/policy_intelligence.py:17-27`(경로 유틸 import 교체), `:238-254`(`_public_event`), `:257-405`(`load_policy_intelligence`)
- Test: `tests/test_policy_intelligence.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# tests/test_policy_intelligence.py 에 추가
import hashlib
from agents import policy_analysis as pa

def test_curation_overrides_rule_with_fallback(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    folder = root / "raw" / "gmail" / "evt1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "body.txt").write_text("유연계약제 접수 본문", encoding="utf-8")
    (folder / "message_sha256.txt").write_text(hashlib.sha256(b"eml").hexdigest() + "\n", encoding="utf-8")
    manifest = {"created_at": "2026-07-01T00:00:00+00:00", "event_count": 1, "events": [{
        "event_id": "evt1", "received_utc": "2026-07-01T00:00:00+00:00",
        "subject": "Fw: [KRPIA] 약가 유연계약제 안내", "topic": "약가 유연계약제",
        "agencies": ["KRPIA"], "email_body_chars": 10, "attachment_count_total": 0,
        "raw_folder": str(folder), "documents": []}]}
    mdir = root / "manifests"; mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "gmail_krpia_20260701_000000.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    event = manifest["events"][0]
    # 폴백: 분석 없음 → 규칙 severity(High) + curation_source rule_fallback
    data = load_policy_intelligence(root=root)
    ev = data["events"][0]
    assert ev["curation_source"] == "rule_fallback"
    assert ev["severity"] == "High"
    assert data["overview"]["pending_analysis_count"] == 1
    assert data["overview"]["curated_event_count"] == 0

    # 헤르메스 분석 얹기 → 값 override + curation_source hermes
    a = {"event_id": "evt1", "content_fingerprint": pa.content_fingerprint(event, root),
         "summary": "유연계약제 접수 시작(사용자 확인 요망)", "severity": "medium", "status": "진행중",
         "msd_implication": {"rationale": "실제가 노출 리스크", "next_action": "SOP 점검"}}
    p = pa.analysis_path("evt1", root); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    data2 = load_policy_intelligence(root=root)
    ev2 = data2["events"][0]
    assert ev2["curation_source"] == "hermes"
    assert ev2["severity"] == "medium"
    assert ev2["summary"].startswith("유연계약제 접수 시작")
    assert data2["overview"]["curated_event_count"] == 1
    assert data2["overview"]["pending_analysis_count"] == 0
    # 토픽 원장·시사점도 헤르메스 값 반영
    ledger = data2["topic_ledgers"][0]
    assert ledger["msd_implication_latest"]["rationale"] == "실제가 노출 리스크"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_intelligence.py::test_curation_overrides_rule_with_fallback -q`
Expected: FAIL — `KeyError: 'curation_source'`

- [ ] **Step 3: 경로 유틸 import 교체** — `policy_intelligence.py:17-27` 의 `_default_root` 정의를 지우고 policy_analysis 재사용

`agents/policy_intelligence.py` 상단 import 블록(줄 7-14) 바로 아래에 추가하고, 기존 `_default_root`(17-24) 함수를 삭제:

```python
from agents.policy_analysis import (
    default_root as _default_root,
    remap_private_path as _remap_private_path,
    resolve_curation as _resolve_curation,
)
```

그리고 파일 하단 `_remap_private_path`(408-423) 정의를 **삭제**(이제 policy_analysis 에서 import). `DEFAULT_ROOT = _default_root()`(27) 는 그대로 둔다.

- [ ] **Step 4: `_public_event` 에 curation 적용** — `policy_intelligence.py:238-254` 교체

```python
def _public_event(event: dict[str, Any], root: Path) -> dict[str, Any]:
    topic = event.get("topic") or "기타"
    rule = _rule(topic)
    cur = _resolve_curation(event, root)
    is_hermes = cur["curation_source"] == "hermes"
    return {
        "id": event.get("event_id"),
        "date": event.get("received_utc"),
        "subject": event.get("subject"),
        "summary": cur["summary"] if is_hermes and cur.get("summary") else _event_summary(event.get("subject") or ""),
        "topic": topic,
        "agencies": event.get("agencies") or [],
        "deadline": event.get("deadline_hint_from_subject"),
        "status": cur["status"] if is_hermes and cur.get("status") else rule["status"],
        "severity": cur["severity"] if is_hermes and cur.get("severity") else rule["severity"],
        "curation_source": cur["curation_source"],
        "msd_implication": cur.get("msd_implication") or {"rationale": rule["rationale"], "next_action": rule["next_action"]},
        "evidence_quotes": cur.get("evidence_quotes") or [],
        "email_body_chars": event.get("email_body_chars", 0),
        "attachment_count": event.get("attachment_count_total", len(event.get("documents") or [])),
        "document_count": len(event.get("documents") or []),
    }
```

- [ ] **Step 5: `load_policy_intelligence` 반영** — `policy_intelligence.py:274`(`events = ...`), 원장/시사점, overview 수정

`:274` 를 `events = [_public_event(event, root) for event in raw_events]` 로 변경.

`change_records`·`topic_ledgers` 블록(305-357)에서 규칙 대신 이벤트의 curation 값을 우선 사용하도록, 루프 상단에 이벤트별 원장 override 를 계산. 구체적으로 `topic_ledgers.append({...})`(337-357) 의 아래 필드를 교체:

```python
        latest_ev = latest  # 이미 _public_event 산출물 (curation_source·msd_implication 포함)
        topic_ledgers.append(
            {
                "topic_id": _topic_id(topic),
                "topic_name": topic,
                "first_seen_at": first.get("date"),
                "latest_seen_at": latest.get("date"),
                "current_status": latest_ev.get("status") or rule["status"],
                "current_summary": latest.get("summary"),
                "latest_change": latest_change,
                "severity": latest_ev.get("severity") or rule["severity"],
                "curation_source": latest_ev.get("curation_source", "rule_fallback"),
                "msd_implication_latest": latest_ev.get("msd_implication") or {
                    "rationale": rule["rationale"], "next_action": rule["next_action"],
                },
                "events": [event.get("id") for event in chronological_events],
                "impact_assessment_ready": rule["priority"] == 1,
                "data_gaps": [
                    "MSD 내부 품목·가격·매출·계약/인하 이력 필요"
                ] if rule["priority"] <= 2 else [],
            }
        )
```

change_records 의 `evidence_quotes`/`why_it_matters`(318-319): 해당 이벤트가 hermes 면 실제 근거·rationale 사용. 루프 내 `after = ...` 다음에:

```python
            ev_cur = next((e for e in events if e.get("id") == event.get("id")), {})
            hermes_quotes = [q.get("quote") for q in (ev_cur.get("evidence_quotes") or []) if q.get("quote")]
```
그리고 change_record 의 `"evidence_quotes": hermes_quotes or ([after] if after else [])`, `"why_it_matters": (ev_cur.get("msd_implication") or {}).get("rationale") or rule["rationale"]` 로 교체.

overview(381-393) 에 카운트 추가:

```python
    curated_event_count = sum(1 for e in events if e.get("curation_source") == "hermes")
    overview = {
        ...  # 기존 필드 유지
        "curated_event_count": curated_event_count,
        "pending_analysis_count": len(events) - curated_event_count,
    }
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_intelligence.py -q`
Expected: PASS (기존 + 신규 통과). 기존 테스트가 severity/summary 규칙값을 단언한다면 폴백 경로라 그대로 통과.

- [ ] **Step 7: 커밋**

```bash
git add agents/policy_intelligence.py tests/test_policy_intelligence.py
git commit -m "feat(policy): 리더에 헤르메스 큐레이션 적용 + 규칙 폴백 + 커버리지 카운트"
```

---

## Task 5: 이벤트 상세 + 위원회 워크스페이스에 curation 노출

**Files:**
- Modify: `agents/policy_intelligence.py:455-497`(`load_event_detail`), `:643-671`(committee `base`/monthly)
- Test: `tests/test_policy_intelligence.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
# tests/test_policy_intelligence.py 에 추가
from agents.policy_intelligence import load_event_detail

def test_event_detail_includes_curation(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    folder = root / "raw" / "gmail" / "evt1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "body.txt").write_text("유연계약제 접수 본문", encoding="utf-8")
    (folder / "message_sha256.txt").write_text(hashlib.sha256(b"eml").hexdigest() + "\n", encoding="utf-8")
    manifest = {"created_at": "t", "events": [{
        "event_id": "evt1", "received_utc": "2026-07-01T00:00:00+00:00",
        "subject": "Fw: [KRPIA] 약가 유연계약제 안내", "topic": "약가 유연계약제",
        "agencies": ["KRPIA"], "raw_folder": str(folder), "documents": []}]}
    mdir = root / "manifests"; mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "gmail_krpia_20260701_000000.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    a = {"event_id": "evt1", "content_fingerprint": pa.content_fingerprint(manifest["events"][0], root),
         "summary": "S", "severity": "low", "status": "모니터링",
         "msd_implication": {"rationale": "R", "next_action": "N"},
         "evidence_quotes": [{"quote": "유연계약제 접수 본문", "source": "body"}]}
    p = pa.analysis_path("evt1", root); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    detail = load_event_detail("evt1", root=root)
    assert detail["curation_source"] == "hermes"
    assert detail["msd_implication"]["rationale"] == "R"
    assert detail["evidence_quotes"][0]["quote"] == "유연계약제 접수 본문"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_intelligence.py::test_event_detail_includes_curation -q`
Expected: FAIL — `KeyError: 'curation_source'`

- [ ] **Step 3: `load_event_detail` 반환에 curation 추가** — `policy_intelligence.py:483-497`

`rule = _rule(...)` 다음에 `cur = _resolve_curation(raw, root)` 추가하고 반환 dict 에:

```python
    cur = _resolve_curation(raw, root)
    is_hermes = cur["curation_source"] == "hermes"
    return {
        "id": event_id,
        "subject": raw.get("subject"),
        "date": raw.get("received_utc"),
        "from": raw.get("from"),
        "topic": raw.get("topic") or "기타",
        "agencies": raw.get("agencies") or [],
        "severity": cur["severity"] if is_hermes and cur.get("severity") else rule["severity"],
        "status": cur["status"] if is_hermes and cur.get("status") else rule["status"],
        "curation_source": cur["curation_source"],
        "summary": cur.get("summary") if is_hermes else None,
        "msd_implication": cur.get("msd_implication") or {"rationale": rule["rationale"], "next_action": rule["next_action"]},
        "evidence_quotes": cur.get("evidence_quotes") or [],
        "deadline": raw.get("deadline_hint_from_subject"),
        "email_body": email_body,
        "email_body_chars": raw.get("email_body_chars", 0),
        "documents": documents,
    }
```

- [ ] **Step 4: 위원회 `base` 에 curation 추가** — `policy_intelligence.py:648-654`

`base = {...}` dict 에 `"curation": _resolve_curation(event, root)` 한 줄 추가(월별/ TF 카드가 실제 시사점 노출 가능).

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_intelligence.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add agents/policy_intelligence.py tests/test_policy_intelligence.py
git commit -m "feat(policy): 이벤트 상세·위원회 카드에 큐레이션 노출"
```

---

## Task 6: 헤르메스 기준 권위 문서 `policy_intelligence_curation_rules.md`

**Files:**
- Create: `agents/rules/policy_intelligence_curation_rules.md`
- Modify: `CLAUDE.md`(규칙 맵 표에 링크 1줄)

- [ ] **Step 1: 권위 문서 작성**

`agents/rules/policy_intelligence_curation_rules.md` 에 아래 구조로 작성(전체를 실제 문장으로 채운다 — 헤르메스가 파일만 읽고 재현):

```markdown
# Policy Intelligence 큐레이션 규칙 (헤르메스 기준)

대상: KRPIA/정부 메일 → 대쉬보드 판단 콘텐츠(요약·MSD시사점·severity·근거).
집행: `agents/policy_analysis.py`(fingerprint·검증). 산출: `analysis/<event_id>.json`.

## 1. 라우팅은 규칙, 재판단 금지
topic·committee 레인은 manifest 값을 복사한다. LLM이 재분류하지 않는다.

## 2. 근거 강제 (grounding)
모든 summary·msd_implication 은 evidence_quotes(본문/첨부에서 **실제 발췌**, source+loc)를
동반한다. 근거 없는 주장 금지. 근거 부족은 data_gaps 에 기록하고 지어내지 않는다.
(검증기가 인용을 소스 텍스트에서 substring 으로 확인 — 실재하지 않으면 실패.)

## 3. MSD 시사점 루브릭 (5대 severe violation 금지)
- ① LOE 도래 자산 미래시제 분석 금지
- ② 단독품목 면제 자산에 generic 인하 자동적용 금지
- ③ 인하 여력 소진 자산에 추가 인하 가능성 제시 금지
- ④ 기체결 RSA 자산에 RSA 재조정을 가벼운 옵션으로 제시 금지
- ⑤ KB 사실 무시한 일반론 금지
- 미공개 RSA 수치·가격 카드 추정 금지. payer(HIRA/MOHW/NHIS) 관점 우선.
- 2026 개편안 인용 시 "고시 개정 진행 중 — 최종 확정 아님" 명시.
- 가능하면 KR-RULE 번호 인용(kr_rules_cited). (요지 발췌는 부록 A.)

## 4. severity 루브릭
- high: MSD 핵심자산 급여/약가에 직접·단기 영향, 또는 법·고시 확정
- medium: 간접·중기 영향, 또는 초안/의견수렴 단계
- low: 정보성·모니터링

## 5. 용어 화이트리스트 + 금지 토큰
- 공식 결과 용어는 화이트리스트만: 급여 적정성 있음 / 평가금액 이하 수용 시 적정 /
  위험분담 확대 적정 / 재심의 / 급여기준 설정 / 급여기준 미설정
- 금지 토큰: brdBltNo, idxno, PR-, Precision, Recall, F1
- "조건부 통과" 금지 → 공식 HIRA 용어 사용

## 6. 출력 계약 (스키마)
`agents/policy_analysis.py` REQUIRED_FIELDS 준수: event_id, content_fingerprint,
summary, severity, msd_implication{rationale,next_action}. 권장: status, evidence_quotes[],
kr_rules_cited[], data_gaps[], confidence, analyst="hermes", model="gpt-5.5", analyzed_at.
content_fingerprint 는 `python -m agents.policy_analysis list-pending` 이 알려주는 값 사용.

## 부록 A. KR-RULE 요지 (헤르메스용 발췌)
(korea-drug-pricing-system 스킬 원천. 헤르메스는 스킬 접근 불가하므로 아래 요지를 사용.)
- KR-RULE-028 약가 유연계약제: 표시가 인상 가능한 유일 기전, 실제가 비공개.
- (신규 topic 등장 시 이 부록에 요지 추가.)
```

- [ ] **Step 2: CLAUDE.md 규칙 맵에 링크 추가**

`CLAUDE.md` 의 "규칙 맵" 표에서 Market Intelligence 행 근처에 1줄 추가:

```markdown
| Policy Intelligence 큐레이션 (헤르메스 기준) | `agents/rules/policy_intelligence_curation_rules.md` |
```

- [ ] **Step 3: 커밋**

```bash
git add agents/rules/policy_intelligence_curation_rules.md CLAUDE.md
git commit -m "docs(policy): 헤르메스 큐레이션 기준 권위 문서 + 규칙맵 링크"
```

---

## Task 7: 헤르메스 작업 런북 README

**Files:**
- Create: `agents/ingest/POLICY_INTEL_CURATION_README.md`

- [ ] **Step 1: 런북 작성** (`REIMB_DATA_README.md` 형식)

```markdown
# Policy Intelligence 큐레이션 채널 — 헤르메스 작업 가이드

헤르메스(GPT-5.5)는 전달받은 KRPIA 메일을 **분석해 사이드카를 커밋**한다.
기준 전문: `agents/rules/policy_intelligence_curation_rules.md`.

## 절차
1. 결정론 ingest 실행 → manifest 생성(라우팅은 규칙, 변경 금지).
2. 분석 대상 확인: `python -m agents.policy_analysis list-pending`
   → 각 event_id + expected_fingerprint.
3. 각 대상 이벤트의 본문(raw_folder/body.txt) + 첨부 추출텍스트(text_path)를 읽고,
   기준 문서(§2~§6)에 따라 `analysis/<event_id>.json` 작성.
   content_fingerprint 는 2번이 알려준 expected_fingerprint 를 그대로 넣는다.
4. 검증: `python -m agents.policy_analysis validate --file analysis/<event_id>.json`
   → `"ok": true` 여야 커밋. 경고가 있으면 수정.
5. (선택) ReviewAgent 다수결 게이트.
6. **AccessRoutineAnalystic `main` 에 커밋**: `policy_intelligence/analysis/<event_id>.json`
   + `policy_intelligence/analysis_manifest.json`(event_id→{fingerprint,analyzed_at,criteria_version}).

## 규칙
- 사이드카는 **비공개 repo(AccessRoutineAnalystic)에만** 커밋. evidence_quotes 에 메일
  본문 발췌가 있으므로 MA_JABIS(메인 repo) 커밋 절대 금지.
- 멱등: 같은 fingerprint 면 재작성 불필요. prod 가 02:10 + 부팅 시 자동 sync.
- 7개 topic 에 안 맞으면 대쉬보드 topic 을 임의 생성 금지 → data_gaps 에
  "new_topic_candidate: <제안명>" 만 기록.
```

- [ ] **Step 2: 커밋**

```bash
git add agents/ingest/POLICY_INTEL_CURATION_README.md
git commit -m "docs(policy): 헤르메스 큐레이션 작업 런북"
```

---

## Task 8: prod sync — `policy_analysis_sync.py` + 스케줄러 잡

**Files:**
- Create: `agents/ingest/policy_analysis_sync.py`
- Modify: `scheduler.py:857-866` 근처(잡 등록)
- Test: `tests/test_policy_analysis_sync.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_policy_analysis_sync.py
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.ingest import policy_analysis_sync as sync


def test_sync_from_local_dir_is_idempotent(tmp_path: Path):
    # 소스(헤르메스 커밋 모사) → 대상(prod root/analysis)
    src = tmp_path / "src" / "policy_intelligence" / "analysis"
    src.mkdir(parents=True, exist_ok=True)
    (src / "evt1.json").write_text(json.dumps({"event_id": "evt1", "content_fingerprint": "fp"}), encoding="utf-8")
    (src / "analysis_manifest.json").write_text(json.dumps({"evt1": {"fingerprint": "fp"}}), encoding="utf-8")
    dst_root = tmp_path / "policy_intelligence"

    r1 = sync.sync_analysis(source_dir=src, root=dst_root)
    assert r1["copied"] == 2
    assert (dst_root / "analysis" / "evt1.json").exists()
    # 재실행 — 변경 없으면 skip(멱등)
    r2 = sync.sync_analysis(source_dir=src, root=dst_root)
    assert r2["copied"] == 0 and r2["skipped"] == 2
    # 내용 변경 → 재복사
    (src / "evt1.json").write_text(json.dumps({"event_id": "evt1", "content_fingerprint": "fp2"}), encoding="utf-8")
    r3 = sync.sync_analysis(source_dir=src, root=dst_root)
    assert r3["copied"] == 1
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_analysis_sync.py -q`
Expected: FAIL — `ModuleNotFoundError: ... policy_analysis_sync`

- [ ] **Step 3: 구현**

```python
# agents/ingest/policy_analysis_sync.py
"""AccessRoutineAnalystic(비공개) → prod <root>/analysis/ 멱등 sync.

소스 우선순위: source_dir(테스트) > POLICY_ANALYSIS_URL(git raw manifest) > 로컬 폴백.
sha256 비교로 변경분만 복사. reimb_reports.sync_reports 패턴과 동일 사상.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from agents.policy_analysis import default_root, ANALYSIS_SUBDIR

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/jeonpapa/AccessRoutineAnalystic/main/policy_intelligence/analysis"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_source_files_local(source_dir: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(source_dir.glob("*.json")):
        out[p.name] = p.read_bytes()
    return out


def _iter_source_files_url(base_url: str) -> dict[str, bytes]:
    manifest_bytes = urllib.request.urlopen(f"{base_url}/analysis_manifest.json", timeout=30).read()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    out: dict[str, bytes] = {"analysis_manifest.json": manifest_bytes}
    for event_id in manifest:
        name = f"{event_id}.json"
        out[name] = urllib.request.urlopen(f"{base_url}/{name}", timeout=30).read()
    return out


def sync_analysis(source_dir: str | Path | None = None, root: str | Path | None = None) -> dict[str, Any]:
    root = Path(root) if root is not None else default_root()
    dest = root / ANALYSIS_SUBDIR
    dest.mkdir(parents=True, exist_ok=True)

    if source_dir is not None:
        files = _iter_source_files_local(Path(source_dir))
    else:
        base = os.environ.get("POLICY_ANALYSIS_URL") or GITHUB_RAW_BASE
        try:
            files = _iter_source_files_url(base)
        except Exception as exc:
            return {"copied": 0, "skipped": 0, "error": f"source fetch failed: {exc}"}

    copied, skipped = 0, 0
    for name, data in files.items():
        target = dest / name
        if target.exists() and _sha(target.read_bytes()) == _sha(data):
            skipped += 1
            continue
        target.write_bytes(data)
        copied += 1
    return {"copied": copied, "skipped": skipped, "dest": str(dest)}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_policy_analysis_sync.py -q`
Expected: PASS

- [ ] **Step 5: 스케줄러 잡 등록** — `scheduler.py`

상단 import 부(다른 ingest import 근처)에 `from agents.ingest.policy_analysis_sync import sync_analysis as _sync_policy_analysis` 추가. 부팅 sync 블록(`reimb_data_sync_job()` 호출 근처 `:695`)에 `try: _sync_policy_analysis()` 추가. `reimb_data_sync` 잡 등록(860-866) 바로 아래에:

```python
    # 매일 02:10 — Policy Intelligence 큐레이션 사이드카 git sync (헤르메스 커밋 자동 반영)
    def _policy_analysis_sync_job():
        try:
            res = _sync_policy_analysis()
            logger.info("policy analysis sync: %s", res)
        except Exception as e:
            logger.warning("policy analysis sync 실패: %s", e)

    scheduler.add_job(
        _policy_analysis_sync_job,
        trigger=CronTrigger(hour=2, minute=10, timezone="Asia/Seoul"),
        id="policy_intel_analysis_sync",
        name="Policy Intelligence 큐레이션 사이드카 매일 02:10 git sync",
        replace_existing=True,
    )
```

- [ ] **Step 6: 스모크 + 커밋**

Run: `.venv/bin/python -c "import scheduler"` (import 에러 없음 확인)
Expected: 출력 없음(정상)

```bash
git add agents/ingest/policy_analysis_sync.py tests/test_policy_analysis_sync.py scheduler.py
git commit -m "feat(policy): 큐레이션 사이드카 prod 멱등 sync + 02:10 잡"
```

---

## Task 9: RuleCompliance 체크 등록 (grounding + pending 커버리지)

**Files:**
- Modify: rule compliance 체크 모듈 (먼저 위치 확인: `grep -rn "def check_" agents/ | grep -i compliance` 또는 `agents/rule_compliance/checks.py`)
- Test: 해당 체크 모듈의 테스트 규약을 따름(없으면 스모크만)

- [ ] **Step 1: 체크 함수 위치 확인**

Run: `grep -rn "SKIP\|def check_" agents/*compliance* agents/**/checks.py 2>/dev/null | head`
Expected: 체크 등록 패턴(함수/딕셔너리) 확인. 그 규약에 맞춰 아래 로직을 추가.

- [ ] **Step 2: 체크 추가** — 큐레이션 무결성 검증 함수

```python
# rule compliance 체크 모듈에 추가 (등록 규약은 Step1 결과에 맞춤)
def check_policy_curation_grounding():
    """큐레이션 사이드카의 evidence_quotes 가 소스에 실재하고, pending 커버리지를 보고."""
    from agents.policy_analysis import default_root, validate_analysis, load_analysis, list_pending, _iter_policy_events
    root = default_root()
    if not (root / "manifests").exists():
        return {"status": "SKIP", "detail": "policy_intelligence manifests 없음"}
    problems = []
    events = {e.get("event_id"): e for e in _iter_policy_events(root)}
    for eid, event in events.items():
        a = load_analysis(eid, root)
        if not a:
            continue
        warns = validate_analysis(a, event, root)
        if warns:
            problems.append({"event_id": eid, "warnings": warns})
    pending = list_pending(root)
    status = "FAIL" if problems else "PASS"
    return {"status": status, "detail": {
        "grounding_problems": problems,
        "pending_analysis_count": len(pending),
        "new_topic_candidates": [
            g for eid, a in ((e, load_analysis(e, root)) for e in events)
            if a for g in (a.get("data_gaps") or []) if str(g).startswith("new_topic_candidate")
        ],
    }}
```

- [ ] **Step 3: 스모크**

Run: `.venv/bin/python -c "from agents.policy_analysis import list_pending, default_root; print(len(list_pending(default_root())))"`
Expected: 정수 출력(로컬 데이터 기준 pending 수).

- [ ] **Step 4: 커밋**

```bash
git add -A
git commit -m "feat(compliance): 큐레이션 grounding + pending 커버리지 체크"
```

---

## Task 10: 프론트 타입 — `policyIntelligence.ts`

**Files:**
- Modify: `frontend/src/api/policyIntelligence.ts`

- [ ] **Step 1: 타입 확장**

`PolicyOverview` 에 추가: `curated_event_count?: number; pending_analysis_count?: number;`

`PolicyEvent` 에 추가: `curation_source?: 'hermes' | 'rule_fallback'; msd_implication?: { rationale: string; next_action: string }; evidence_quotes?: { quote: string; source?: string; loc?: string }[];`

`PolicyTopicLedger` 에 추가: `curation_source?: 'hermes' | 'rule_fallback';`

`PolicyEventDetail` 에 추가: `curation_source?: 'hermes' | 'rule_fallback'; summary?: string | null; msd_implication?: { rationale: string; next_action: string }; evidence_quotes?: { quote: string; source?: string; loc?: string }[];`

- [ ] **Step 2: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 3: 커밋**

```bash
git add frontend/src/api/policyIntelligence.ts
git commit -m "feat(policy-ui): 큐레이션 응답 타입"
```

---

## Task 11: 프론트 UI — curation 뱃지 + evidence 표시

**Files:**
- Modify: `frontend/src/components/policy/EventModal.tsx`, `frontend/src/pages/policy-intelligence/page.tsx`

- [ ] **Step 1: EventModal 에 evidence + 뱃지**

`PolicyEventModal` 에서 detail 로드 후, 헤더에 `curation_source` 뱃지("AI 큐레이션"=emerald / "규칙 기본값"=slate)를 표시하고, `msd_implication.rationale`/`next_action` 을 별도 블록으로, `evidence_quotes` 를 `“{quote}” — {source} {loc}` 리스트로 렌더:

```tsx
{detail.curation_source && (
  <span className={`text-xs px-2 py-0.5 rounded ${detail.curation_source === 'hermes' ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
    {detail.curation_source === 'hermes' ? 'AI 큐레이션' : '규칙 기본값'}
  </span>
)}
{detail.msd_implication?.rationale && (
  <div className="mt-3 text-sm">
    <div className="font-medium">MSD 시사점</div>
    <p>{detail.msd_implication.rationale}</p>
    <p className="text-slate-500">→ {detail.msd_implication.next_action}</p>
  </div>
)}
{(detail.evidence_quotes?.length ?? 0) > 0 && (
  <ul className="mt-2 text-xs text-slate-600 space-y-1">
    {detail.evidence_quotes!.map((q, i) => (
      <li key={i}>“{q.quote}” <span className="text-slate-400">— {q.source}{q.loc ? ` ${q.loc}` : ''}</span></li>
    ))}
  </ul>
)}
```

- [ ] **Step 2: TopicCard/TopicDetail + overview 스탯에 뱃지·카운트**

`page.tsx` overview 스탯 라인에 "큐레이션 {curated_event_count} / 미처리 {pending_analysis_count}" 추가. TopicDetail 의 각 이벤트/원장에 `curation_source==='hermes'` 면 emerald 점 표시.

- [ ] **Step 3: 빌드**

Run: `cd frontend && npm run build`
Expected: `✓ built`

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/components/policy/EventModal.tsx frontend/src/pages/policy-intelligence/page.tsx
git commit -m "feat(policy-ui): 큐레이션 뱃지 + MSD시사점·근거 표시"
```

---

## Task 12: 엔드투엔드 검증 (실데이터 픽스처)

**Files:** (검증 전용, 코드 변경 없음)

- [ ] **Step 1: 로컬 pending 확인**

Run: `POLICY_INTELLIGENCE_ROOT=data/policy_intelligence .venv/bin/python -m agents.policy_analysis list-pending`
Expected: 로컬 manifest(20 events)의 미처리 목록 + expected_fingerprint 출력.

- [ ] **Step 2: 실제 이벤트 1건 분석 사이드카 수기 작성 → 검증**

목록의 첫 event_id 로 `data/policy_intelligence/analysis/<event_id>.json` 작성(expected_fingerprint 사용, 실제 본문에서 evidence quote 발췌), 그 후:

Run: `POLICY_INTELLIGENCE_ROOT=data/policy_intelligence .venv/bin/python -m agents.policy_analysis validate --file data/policy_intelligence/analysis/<event_id>.json`
Expected: `"ok": true`

- [ ] **Step 3: 리더 반영 확인**

Run:
```bash
POLICY_INTELLIGENCE_ROOT=data/policy_intelligence .venv/bin/python -c "
from agents.policy_intelligence import load_policy_intelligence
d=load_policy_intelligence(root='data/policy_intelligence')
print('curated', d['overview']['curated_event_count'], 'pending', d['overview']['pending_analysis_count'])
print([e['curation_source'] for e in d['events']][:5])
"
```
Expected: curated 1, 해당 이벤트 `hermes`, 나머지 `rule_fallback`.

- [ ] **Step 4: 폴백 확인**

사이드카 삭제 후 Step 3 재실행 → curated 0, 대쉬보드 정상(에러 없음).

- [ ] **Step 5: 전체 테스트 + 빌드**

Run: `.venv/bin/python -m pytest tests/test_policy_analysis.py tests/test_policy_intelligence.py tests/test_policy_analysis_sync.py -q && cd frontend && npm run build`
Expected: 전부 PASS + `✓ built`

- [ ] **Step 6: 픽스처 사이드카 제거(로컬 데이터 오염 방지) + 최종 확인**

```bash
rm -f data/policy_intelligence/analysis/<event_id>.json
git status  # data/policy_intelligence/ 는 gitignore 라 커밋 대상 아님 확인
```

---

## 배포 (별도 — 사용자 확인 필수)

이 계획은 **로컬 구현·검증까지**다. 프로덕션 반영은 [[feedback_deploy_confirm_gate]] 에 따라 별도. 배포 시:
1. 코드(`agents/`, `scheduler.py`, `frontend/out`)는 `flyctl deploy`(사용자 실행).
2. 헤르메스가 AccessRoutineAnalystic 에 `policy_intelligence/analysis/*.json` 커밋 → prod `policy_intel_analysis_sync`(02:10/부팅) 자동 취득.
3. `POLICY_ANALYSIS_URL`(git raw base) 환경변수 prod 설정 필요(미설정 시 기본 GITHUB_RAW_BASE 사용).
