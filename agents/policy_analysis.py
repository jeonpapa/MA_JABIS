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
