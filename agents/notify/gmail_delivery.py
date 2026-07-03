"""Gmail API delivery adapter for Daily Mailing.

Default operation is draft creation. Live send requires an explicit caller-side
approval gate and should not be reached from preview/test-send endpoints.
"""
from __future__ import annotations

import base64
import json
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable, Protocol

TOKEN_PATH = Path(os.environ.get("GOOGLE_TOKEN_PATH", "/opt/data/google_token.json"))
REQUIRED_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class GmailServiceProtocol(Protocol):
    def users(self) -> Any: ...


def gmail_configured(token_path: str | Path = TOKEN_PATH) -> bool:
    path = Path(token_path)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    scopes = data.get("scopes") or data.get("scope") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    return REQUIRED_SEND_SCOPE in set(scopes)


def build_email_message(
    *,
    recipients: Iterable[str],
    subject: str,
    body_html: str,
    body_text: str | None = None,
    sender: str | None = None,
) -> EmailMessage:
    to_list = [r.strip() for r in recipients if r and r.strip()]
    if not to_list:
        raise ValueError("at least one recipient is required")
    msg = EmailMessage()
    msg["To"] = ", ".join(to_list)
    if sender:
        msg["From"] = sender
    msg["Subject"] = subject
    msg.set_content(body_text or "이 메일은 HTML 본문입니다. HTML 지원 메일 클라이언트에서 확인하세요.")
    msg.add_alternative(body_html, subtype="html")
    return msg


def encode_raw_message(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _build_service(token_path: str | Path = TOKEN_PATH):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path), scopes=[REQUIRED_SEND_SCOPE])
    return build("gmail", "v1", credentials=creds)


def create_gmail_draft(
    *,
    recipients: Iterable[str],
    subject: str,
    body_html: str,
    body_text: str | None = None,
    sender: str | None = None,
    token_path: str | Path = TOKEN_PATH,
    service: GmailServiceProtocol | None = None,
) -> dict:
    """Create a Gmail draft and return Gmail IDs. Does not send."""
    if service is None:
        if not gmail_configured(token_path):
            return {"ok": False, "mode": "gmail_draft", "draft_created": False, "message": "Gmail OAuth token missing gmail.send scope"}
        service = _build_service(token_path)
    assert service is not None
    msg = build_email_message(
        recipients=recipients,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        sender=sender,
    )
    raw = encode_raw_message(msg)
    created = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return {
        "ok": True,
        "mode": "gmail_draft",
        "draft_created": True,
        "gmail_draft_id": created.get("id"),
        "gmail_message_id": (created.get("message") or {}).get("id"),
        "thread_id": (created.get("message") or {}).get("threadId"),
        "recipients": [r.strip() for r in recipients if r and r.strip()],
        "subject": subject,
    }
