"""Policy Intelligence Gmail/document ingest pipeline.

This module is intentionally usable in two modes:
1. production: create a Gmail service from Hermes Google OAuth token and download
   forwarded KRPIA/MA policy emails plus attachments into private storage;
2. tests: pass a fake Gmail service and deterministic message payloads.

Raw paths are kept in the private ingest manifest only. Dashboard/API exposure is
handled by agents.policy_intelligence, which sanitizes the payload.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Iterable

try:  # optional in minimal test environments
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except Exception:  # pragma: no cover - exercised only when Google deps missing
    Request = None  # type: ignore[assignment]
    Credentials = None  # type: ignore[assignment]
    build = None  # type: ignore[assignment]

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

DEFAULT_ROOT = Path(os.environ.get("POLICY_INTELLIGENCE_ROOT", "/opt/data/policy_intelligence"))
DEFAULT_QUERY = os.environ.get(
    "POLICY_INTELLIGENCE_GMAIL_QUERY",
    "(KRPIA OR 약가 OR 심평원 OR 복지부 OR HIRA OR MOHW OR NHIS) newer_than:30d",
)
TOKEN_PATHS = [
    Path(os.environ.get("GOOGLE_TOKEN_PATH", "/opt/data/google_token.json")),
    Path.home() / ".hermes" / "google_token.json",
]
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

TOPIC_KEYWORDS: list[tuple[str, list[str]]] = [
    ("기등재 약제 재평가·약가조정", ["기등재", "재평가", "약가조정", "특허만료", "오리지널"]),
    ("약가 유연계약제", ["유연계약", "유연 계약", "실제가", "도매", "요양기관포털"]),
    ("RWE·약제성과평가", ["RWE", "성과평가", "실사용", "Real World", "가이드라인"]),
    ("희귀질환 치료제 신속등재 / 100일 신속등재", ["희귀", "신속등재", "100일"]),
    ("사용량-약가 연동 협상", ["사용량-약가", "사용량 약가", "PVA", "연동 협상"]),
    ("급여기준 고시 개정 의견조회", ["급여기준", "고시", "요양급여", "적용기준"]),
    ("KRPIA 정책제안", ["정책제안", "정책 제안", "KRPIA 2026"]),
]
AGENCY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("KRPIA", ["KRPIA", "협회"]),
    ("복지부", ["MOHW", "복지부", "보건복지부"]),
    ("심평원", ["HIRA", "심평원", "건강보험심사평가원"]),
    ("공단", ["NHIS", "공단", "건강보험공단", "국민건강보험"]),
]


def b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def safe_name(value: str, max_len: int = 160) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return (cleaned[:max_len] or "untitled").strip(" ._")


def _find_token() -> Path:
    for path in TOKEN_PATHS:
        if path.exists():
            return path
    raise FileNotFoundError("google_token.json not found; run Google Workspace OAuth setup first")


def gmail_service():
    if Credentials is None or Request is None or build is None:
        raise RuntimeError("Google API client packages are not installed")
    token = _find_token()
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _walk_parts(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield payload
    for part in payload.get("parts") or []:
        yield from _walk_parts(part)


def _headers_dict(payload: dict[str, Any]) -> dict[str, str]:
    return {h.get("name", ""): h.get("value", "") for h in payload.get("headers") or []}


def _part_bytes(service: Any, msg_id: str, part: dict[str, Any]) -> bytes:
    body = part.get("body") or {}
    if body.get("data"):
        return b64url_decode(body["data"])
    attachment_id = body.get("attachmentId")
    if attachment_id:
        attachment = service.users().messages().attachments().get(
            userId="me", messageId=msg_id, id=attachment_id
        ).execute()
        return b64url_decode(attachment["data"])
    return b""


def ingest_gmail_messages(
    *,
    service: Any,
    query: str,
    max_results: int,
    out_dir: str | Path,
) -> dict[str, Any]:
    """Download Gmail messages and attachments into private storage."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    response = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = response.get("messages") or []
    items: list[dict[str, Any]] = []

    for message_ref in messages:
        msg_id = message_ref["id"]
        full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        raw_resp = service.users().messages().get(userId="me", id=msg_id, format="raw").execute()
        payload = full.get("payload") or {}
        headers = _headers_dict(payload)
        subject = headers.get("Subject") or full.get("snippet") or msg_id
        internal_ms = int(full.get("internalDate", "0") or 0)
        received = datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)
        folder = root / f"{received.strftime('%Y%m%d_%H%M%S')}_{msg_id}_{safe_name(subject, 90)}"
        folder.mkdir(parents=True, exist_ok=True)

        raw_bytes = b64url_decode(raw_resp["raw"])
        (folder / "message.eml").write_bytes(raw_bytes)
        (folder / "message_sha256.txt").write_text(hashlib.sha256(raw_bytes).hexdigest() + "\n", encoding="utf-8")

        text_parts: list[str] = []
        html_parts: list[str] = []
        attachments: list[dict[str, Any]] = []
        for part in _walk_parts(payload):
            mime_type = part.get("mimeType", "")
            filename = part.get("filename") or ""
            data = _part_bytes(service, msg_id, part)
            if not data:
                continue
            if filename:
                att_dir = folder / "attachments" / "original"
                att_dir.mkdir(parents=True, exist_ok=True)
                out_path = att_dir / safe_name(filename, 180)
                if out_path.exists():
                    out_path = out_path.with_name(f"{out_path.stem}_{hashlib.sha1(data).hexdigest()[:8]}{out_path.suffix}")
                out_path.write_bytes(data)
                attachments.append(
                    {
                        "filename": filename,
                        "saved_path": str(out_path),
                        "mime_type": mime_type,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            elif mime_type == "text/plain":
                text_parts.append(data.decode("utf-8", errors="replace"))
            elif mime_type == "text/html":
                html_parts.append(data.decode("utf-8", errors="replace"))

        (folder / "body.txt").write_text("\n\n--- PART ---\n\n".join(text_parts), encoding="utf-8")
        (folder / "body.html").write_text("\n\n<!-- PART -->\n\n".join(html_parts), encoding="utf-8")
        (folder / "attachments.json").write_text(json.dumps(attachments, ensure_ascii=False, indent=2), encoding="utf-8")
        metadata = {
            "gmail_id": msg_id,
            "thread_id": full.get("threadId"),
            "query": query,
            "subject": subject,
            "from": headers.get("From"),
            "to": headers.get("To"),
            "cc": headers.get("Cc"),
            "date_header": headers.get("Date"),
            "internal_date_utc": received.isoformat(),
            "label_ids": full.get("labelIds", []),
            "snippet": full.get("snippet"),
            "attachments": attachments,
            "folder": str(folder),
        }
        (folder / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        items.append(metadata)

    index_path = root / f"index_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    index_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"count": len(items), "index_path": str(index_path), "items": items}


def _extract_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    if fitz is None:
        return "", {"status": "failed", "method": "pymupdf", "error": "pymupdf not available"}
    doc = fitz.open(path)
    text = "".join(f"\n\n--- PAGE {i + 1} ---\n{page.get_text('text')}" for i, page in enumerate(doc)).strip()
    return text, {"status": "ok" if text else "empty", "method": "pymupdf", "pages": doc.page_count}


def _extract_hwpx(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        chunks: list[str] = []
        with zipfile.ZipFile(path) as archive:
            names = sorted(
                [n for n in archive.namelist() if n.lower().endswith(".xml")],
                key=lambda n: (0 if "contents/section" in n.lower() else 1, n),
            )
            for name in names:
                try:
                    root = ET.fromstring(archive.read(name))
                except Exception:
                    continue
                parts = [elem.text.strip() for elem in root.iter() if elem.text and elem.text.strip()]
                if parts:
                    chunks.append(f"\n\n--- {name} ---\n" + "\n".join(parts))
        text = "\n".join(chunks).strip()
        return text, {"status": "ok" if text else "empty", "method": "zip+xml", "xml_files": len(names)}
    except Exception as exc:
        return "", {"status": "failed", "method": "zip+xml", "error": str(exc)}


def _extract_hwp(path: Path) -> tuple[str, dict[str, Any]]:
    cmd = shutil.which("hwp5txt") or "/opt/data/home/.local/bin/hwp5txt"
    if not Path(cmd).exists():
        return "", {"status": "failed", "method": "hwp5txt", "error": "hwp5txt not installed"}
    try:
        result = subprocess.run([cmd, str(path)], capture_output=True, text=True, timeout=120)
        text = result.stdout.strip()
        return text, {
            "status": "ok" if result.returncode == 0 and text else "failed",
            "method": "hwp5txt",
            "returncode": result.returncode,
            "stderr": result.stderr[-1000:],
        }
    except Exception as exc:
        return "", {"status": "failed", "method": "hwp5txt", "error": str(exc)}


def extract_documents(index_items: list[dict[str, Any]], out_root: str | Path) -> dict[str, Any]:
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for item in index_items:
        folder = Path(item["folder"])
        out_dir = out_root / folder.name
        out_dir.mkdir(parents=True, exist_ok=True)
        body = folder / "body.txt"
        if body.exists():
            (out_dir / "body.txt").write_text(body.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        for attachment in item.get("attachments") or []:
            source = Path(attachment["saved_path"])
            ext = source.suffix.lower()
            if ext == ".pdf":
                text, meta = _extract_pdf(source)
            elif ext == ".hwpx":
                text, meta = _extract_hwpx(source)
            elif ext == ".hwp":
                text, meta = _extract_hwp(source)
            else:
                continue
            text_path = out_dir / f"{safe_name(source.stem, 150)}.txt"
            text_path.write_text(text, encoding="utf-8")
            manifest.append(
                {
                    "gmail_id": item["gmail_id"],
                    "subject": item.get("subject"),
                    "source_path": str(source),
                    "text_path": str(text_path),
                    "filename": attachment.get("filename"),
                    "sha256": attachment.get("sha256"),
                    "char_count": len(text),
                    **meta,
                }
            )
    manifest_path = out_root / "extraction_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    status_counts = Counter(item.get("status") for item in manifest)
    return {"count": len(manifest), "manifest": str(manifest_path), "status_counts": dict(status_counts), "items": manifest}


def classify_topic(text: str) -> str:
    upper_text = text.upper()
    for topic, keywords in TOPIC_KEYWORDS:
        if any(keyword.upper() in upper_text for keyword in keywords):
            return topic
    return "기타"


def detect_agencies(text: str) -> list[str]:
    upper_text = text.upper()
    agencies = [agency for agency, keywords in AGENCY_KEYWORDS if any(keyword.upper() in upper_text for keyword in keywords)]
    return agencies or ["KRPIA"]


def deadline_hint(subject: str) -> str | None:
    match = re.search(r"~\s*([0-9]{1,2}[./_-][0-9]{1,2}|[0-9]{1,2}\s*월\s*[0-9]{1,2}\s*일?)", subject)
    return match.group(1) if match else None


def build_dashboard_manifest(
    index_items: list[dict[str, Any]],
    *,
    extraction_manifest: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    extraction_by_gmail: dict[str, list[dict[str, Any]]] = {}
    for doc in extraction_manifest or []:
        extraction_by_gmail.setdefault(doc.get("gmail_id"), []).append(doc)

    events: list[dict[str, Any]] = []
    for item in index_items:
        subject = item.get("subject") or ""
        body_chars = 0
        body_path = Path(item.get("folder", "")) / "body.txt" if item.get("folder") else None
        if body_path and body_path.exists():
            body_chars = len(body_path.read_text(encoding="utf-8", errors="replace"))
        docs = []
        for doc in extraction_by_gmail.get(item.get("gmail_id"), []):
            docs.append(
                {
                    "filename": doc.get("filename"),
                    "status": doc.get("status"),
                    "chars": doc.get("char_count", 0),
                    "text_path": doc.get("text_path"),
                    "sha256": doc.get("sha256"),
                }
            )
        events.append(
            {
                "event_id": item.get("gmail_id"),
                "thread_id": item.get("thread_id"),
                "received_utc": item.get("internal_date_utc"),
                "subject": subject,
                "from": item.get("from"),
                "topic": classify_topic(subject + " " + (item.get("snippet") or "")),
                "agencies": detect_agencies(subject + " " + (item.get("from") or "")),
                "deadline_hint_from_subject": deadline_hint(subject),
                "email_body_chars": body_chars,
                "attachment_count_total": len(item.get("attachments") or []),
                "extractable_document_count": len(docs),
                "extracted_document_chars": sum(doc.get("chars", 0) for doc in docs),
                "raw_folder": item.get("folder"),
                "documents": docs,
            }
        )

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "events": sorted(events, key=lambda e: e.get("received_utc") or "", reverse=True),
    }


def _cumulative_events(root: Path, new_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """기존 모든 manifest(pilot_* + gmail_krpia_*)의 이벤트를 event_id 로 union.

    30일/max_results 윈도우로 새 배치가 과거 이벤트를 누락해도 유실 방지.
    같은 event_id 는 더 나중에 읽은 copy(=최신 manifest, 마지막에 새 배치)가 이긴다.
    raw_folder 는 볼륨에 잔존하므로 과거 이벤트의 원문/상세도 유효.
    """
    mdir = root / "manifests"
    by_id: dict[str, dict[str, Any]] = {}
    if mdir.exists():
        files = list(mdir.glob("pilot_*.json")) + list(mdir.glob("gmail_krpia_*.json"))
        for f in sorted(files, key=lambda p: p.stat().st_mtime):  # 오래된 → 최신
            try:
                prior = json.loads(f.read_text(encoding="utf-8")).get("events") or []
            except Exception:
                continue
            for event in prior:
                eid = event.get("event_id")
                if eid:
                    by_id[eid] = event
    for event in new_events:  # 새 배치 최우선
        eid = event.get("event_id")
        if eid:
            by_id[eid] = event
    return sorted(by_id.values(), key=lambda e: e.get("received_utc") or "", reverse=True)


def run_ingest(
    *,
    root: str | Path = DEFAULT_ROOT,
    query: str = DEFAULT_QUERY,
    max_results: int = 20,
    manifest_name: str | None = None,
    service: Any | None = None,
    cumulative: bool = True,
) -> dict[str, Any]:
    root = Path(root)
    service = service or gmail_service()
    ingest = ingest_gmail_messages(service=service, query=query, max_results=max_results, out_dir=root / "raw" / "gmail")
    extraction = extract_documents(ingest["items"], root / "extracted" / "text")
    manifest = build_dashboard_manifest(ingest["items"], extraction_manifest=extraction["items"])
    if cumulative:
        merged_events = _cumulative_events(root, manifest.get("events") or [])
        manifest = {"created_at": manifest.get("created_at"), "event_count": len(merged_events), "events": merged_events}
    if manifest_name is None:
        manifest_name = f"gmail_krpia_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    manifest_path = root / "manifests" / manifest_name
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    status = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "raw_count": ingest["count"],
        "extracted_count": extraction["count"],
        "manifest_path": str(manifest_path),
        "manifest_name": manifest_path.name,
        "status_counts": extraction["status_counts"],
    }
    status_path = root / "manifests" / "latest_ingest_status.json"
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": status, "ingest": ingest, "extraction": extraction, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest KRPIA/MA policy emails from Gmail")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--manifest-name")
    args = parser.parse_args()
    result = run_ingest(root=args.root, query=args.query, max_results=args.max, manifest_name=args.manifest_name)
    # Avoid dumping full raw paths/items to stdout in normal operations.
    print(json.dumps(result["status"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
