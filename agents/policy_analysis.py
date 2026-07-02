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
    if not isinstance(analysis, dict):
        return ["analysis is not a valid object"]
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
    real_quote_count = 0
    for entry in quotes:
        if not isinstance(entry, dict):
            warnings.append("invalid evidence_quote entry (expected object)")
            continue
        quote = (entry.get("quote") or "").strip()
        if not quote:
            warnings.append("empty evidence_quote entry")
            continue
        real_quote_count += 1
        if quote.casefold() not in texts:
            warnings.append(f"evidence quote not found in source: {quote[:40]}")
    if real_quote_count == 0 and not analysis.get("data_gaps"):
        warnings.append("no evidence_quotes and no data_gaps (grounding required)")

    haystack = json.dumps(analysis, ensure_ascii=False)
    for token in BANNED_TOKENS:
        if token in haystack:
            warnings.append(f"banned token found: {token}")
    if "조건부 통과" in haystack:
        warnings.append("avoid phrase '조건부 통과'; use official HIRA term")
    return warnings


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
    """manifests/latest 를 읽어 policy 이벤트 반환(news lane 제외는 리더 규칙 재사용)."""
    from agents.policy_intelligence import _resolve_manifest_file, _is_policy_intelligence_event  # 지연 import
    manifest = json.loads(_resolve_manifest_file(root).read_text(encoding="utf-8"))
    return [e for e in (manifest.get("events") or []) if _is_policy_intelligence_event(e)]


def list_pending(root: str | Path, since: str | None = None) -> list[dict[str, Any]]:
    """분석 없음/stale 이벤트 목록 + 기대 fingerprint.

    since (YYYY-MM-DD) 지정 시 received_utc 가 그 날짜 이후(포함)인 이벤트만 반환 →
    기존 과거분을 헤르메스 큐레이션 대상에서 원천 제외(하드 가드). ISO 날짜라 문자열 비교로 충분.
    """
    root = Path(root)
    since_key = (since or "")[:10]
    out = []
    for event in _iter_policy_events(root):
        if since_key and (event.get("received_utc") or "")[:10] < since_key:
            continue
        eid = event.get("event_id", "")
        analysis = load_analysis(eid, root)
        if not (analysis and analysis_valid(analysis, event, root)):
            out.append({"event_id": eid, "subject": event.get("subject"),
                        "topic": event.get("topic"),
                        "received_utc": event.get("received_utc"),
                        "expected_fingerprint": content_fingerprint(event, root)})
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="헤르메스 KRPIA 큐레이션 도구")
    parser.add_argument("command", choices=["list-pending", "validate"])
    parser.add_argument("--event-id")
    parser.add_argument("--file", help="validate 대상 analysis json")
    parser.add_argument("--root", default=None)
    parser.add_argument("--since", help="YYYY-MM-DD 이후(포함) 이벤트만 (과거분 큐레이션 방지)")
    args = parser.parse_args()
    root = Path(args.root) if args.root else default_root()

    if args.command == "list-pending":
        print(json.dumps(list_pending(root, since=args.since), ensure_ascii=False, indent=2))
    else:
        if not args.file:
            print(json.dumps({"ok": False, "error": "--file is required for validate"}, ensure_ascii=False))
            raise SystemExit(1)
        try:
            analysis = json.loads(Path(args.file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": f"cannot read --file: {exc}"}, ensure_ascii=False))
            raise SystemExit(1)
        events = {e.get("event_id"): e for e in _iter_policy_events(root)}
        event = events.get(analysis.get("event_id") or args.event_id)
        if event is None:
            print(json.dumps({"ok": False, "error": "event not found"}, ensure_ascii=False))
        else:
            warnings = validate_analysis(analysis, event, root)
            print(json.dumps({"ok": not warnings, "warnings": warnings}, ensure_ascii=False, indent=2))
