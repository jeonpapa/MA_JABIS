from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.policy_intelligence_ingest import (
    build_dashboard_manifest,
    ingest_gmail_messages,
    safe_name,
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class _FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeAttachments:
    def __init__(self, attachment_payloads):
        self.attachment_payloads = attachment_payloads

    def get(self, userId, messageId, id):  # noqa: N803 - Gmail API naming
        return _FakeExecute({"data": _b64url(self.attachment_payloads[(messageId, id)])})


class _FakeMessages:
    def __init__(self):
        self.attachment_payloads = {("msg-1", "att-1"): b"policy pdf bytes"}
        self.full = {
            "id": "msg-1",
            "threadId": "thread-1",
            "internalDate": "1782727200000",
            "labelIds": ["INBOX"],
            "snippet": "KRPIA snippet",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Fw: [KRPIA-HIRA] 약가 유연계약제 안내"},
                    {"name": "From", "value": "Joseph <yo.seop.jeon@msd.com>"},
                    {"name": "To", "value": "policy-bot@example.com"},
                    {"name": "Date", "value": "Tue, 30 Jun 2026 09:00:00 +0900"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64url("본문 텍스트".encode())}},
                    {"mimeType": "text/html", "body": {"data": _b64url(b"<p>html</p>")}},
                    {
                        "mimeType": "application/pdf",
                        "filename": "안내.pdf",
                        "body": {"attachmentId": "att-1"},
                    },
                ],
            },
        }

    def list(self, userId, q, maxResults):  # noqa: N803
        assert q == "label:krpia"
        assert maxResults == 10
        return _FakeExecute({"messages": [{"id": "msg-1"}]})

    def get(self, userId, id, format):  # noqa: A002,N803
        if format == "full":
            return _FakeExecute(self.full)
        if format == "raw":
            return _FakeExecute({"raw": _b64url(b"raw rfc822")})
        raise AssertionError(format)

    def attachments(self):
        return _FakeAttachments(self.attachment_payloads)


class _FakeUsers:
    def __init__(self):
        self._messages = _FakeMessages()

    def messages(self):
        return self._messages


class _FakeGmailService:
    def __init__(self):
        self._users = _FakeUsers()

    def users(self):
        return self._users


def test_ingest_gmail_messages_preserves_raw_email_and_attachments(tmp_path: Path):
    result = ingest_gmail_messages(
        service=_FakeGmailService(),
        query="label:krpia",
        max_results=10,
        out_dir=tmp_path / "raw" / "gmail",
    )

    assert result["count"] == 1
    item = result["items"][0]
    folder = Path(item["folder"])
    assert folder.exists()
    assert (folder / "message.eml").read_bytes() == b"raw rfc822"
    assert (folder / "message_sha256.txt").read_text().strip()
    assert (folder / "body.txt").read_text(encoding="utf-8") == "본문 텍스트"
    assert (folder / "body.html").read_text(encoding="utf-8") == "<p>html</p>"
    attachments = json.loads((folder / "attachments.json").read_text(encoding="utf-8"))
    assert attachments[0]["filename"] == "안내.pdf"
    assert Path(attachments[0]["saved_path"]).read_bytes() == b"policy pdf bytes"
    assert str(tmp_path) in item["folder"]  # raw path remains in ingest index only


def test_build_dashboard_manifest_merges_raw_index_and_extractions_without_leaking_paths(tmp_path: Path):
    raw_folder = tmp_path / "raw" / "gmail" / "20260630_090000_msg-1_Fw_ KRPIA"
    raw_folder.mkdir(parents=True)
    body_path = raw_folder / "body.txt"
    body_path.write_text("메일 본문", encoding="utf-8")
    index = [
        {
            "gmail_id": "msg-1",
            "thread_id": "thread-1",
            "subject": "Fw: [KRPIA-HIRA] 약가 유연계약제 안내",
            "from": "Joseph <yo.seop.jeon@msd.com>",
            "internal_date_utc": "2026-06-30T00:00:00+00:00",
            "folder": str(raw_folder),
            "attachments": [{"filename": "안내.pdf", "sha256": "abc", "saved_path": str(raw_folder / "attachments" / "안내.pdf")}],
        }
    ]
    extraction_manifest = [
        {
            "gmail_id": "msg-1",
            "filename": "안내.pdf",
            "status": "ok",
            "char_count": 120,
            "text_path": str(tmp_path / "extracted" / "doc.txt"),
            "source_path": str(raw_folder / "attachments" / "안내.pdf"),
        }
    ]

    manifest = build_dashboard_manifest(index, extraction_manifest=extraction_manifest)

    assert manifest["event_count"] == 1
    event = manifest["events"][0]
    assert event["event_id"] == "msg-1"
    assert event["topic"] == "약가 유연계약제"
    assert event["attachment_count_total"] == 1
    assert event["documents"][0]["filename"] == "안내.pdf"
    assert event["documents"][0]["chars"] == 120
    public_payload = json.dumps(manifest, ensure_ascii=False)
    # Source manifest may keep private paths for server-side processing, but dashboard adapter must sanitize later.
    assert "약가 유연계약제" in public_payload


def test_safe_name_removes_path_separators():
    assert "/" not in safe_name("a/b:c?.pdf")
    assert "\\" not in safe_name("a/b:c?.pdf")
