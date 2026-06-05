#!/usr/bin/env python3
"""Send or dry-run a HIRA Access Intelligence email.

This script renders the HIRA email draft context and sends it via SMTP when
SMTP credentials are configured. It never guesses credentials.

Credential sources:
  1. Environment variables
  2. config/.env in the repository root

Required SMTP variables:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_FROM
Optional:
  SMTP_TLS=true|false

Examples:
  python scripts/send_hira_email.py --to yo.seop.jeon@msd.com \
    --context docs/hira_access_intelligence/email_draft_example_yakpyungwi_6.json \
    --dry-run

  python scripts/send_hira_email.py --to yo.seop.jeon@msd.com \
    --context docs/hira_access_intelligence/email_draft_example_yakpyungwi_6.json \
    --attachment /path/report.pdf
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

# Import sibling renderer without requiring package install.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from render_hira_email_draft import render  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "SMTP_TLS")


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    env_path = REPO_ROOT / "config" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in ENV_KEYS:
                out[key] = value.strip().strip('"').strip("'")
    for key in ENV_KEYS:
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def configured(env: dict[str, str]) -> bool:
    required = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM")
    return all(env.get(k) for k in required)


def add_attachment(msg: EmailMessage, path: Path) -> None:
    data = path.read_bytes()
    ctype, _ = mimetypes.guess_type(str(path))
    if not ctype:
        ctype = "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)


def build_message(sender: str, recipients: Iterable[str], subject: str, body: str, attachments: list[Path]) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    for attachment in attachments:
        add_attachment(msg, attachment)
    return msg


def send_smtp(msg: EmailMessage, env: dict[str, str]) -> None:
    host = env["SMTP_HOST"]
    port = int(env.get("SMTP_PORT", "587"))
    use_tls = env.get("SMTP_TLS", "true").lower() != "false"
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as server:
            server.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            if use_tls:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            server.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
            server.send_message(msg)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send HIRA email via SMTP or dry-run")
    parser.add_argument("--to", action="append", required=True, help="Recipient email. Can be repeated.")
    parser.add_argument("--context", required=True, help="Email context JSON path")
    parser.add_argument("--attachment", action="append", default=[], help="Attachment path. Can be repeated.")
    parser.add_argument("--dry-run", action="store_true", help="Render and validate only; do not send")
    parser.add_argument("--out", help="Write rendered draft text to this path")
    args = parser.parse_args()

    context_path = Path(args.context)
    context = json.loads(context_path.read_text(encoding="utf-8"))
    rendered = render(context)
    recipients = [x.strip() for x in args.to if x.strip()]
    attachments = [Path(x) for x in args.attachment]

    missing_attachments = [str(x) for x in attachments if not x.exists()]
    if missing_attachments:
        print(json.dumps({"status": "error", "message": "missing attachments", "files": missing_attachments}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    draft_text = f"Subject: {rendered['subject']}\nTo: {', '.join(recipients)}\n\n{rendered['body_markdown']}"
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(draft_text, encoding="utf-8")

    if rendered["warnings"]:
        print(json.dumps({"status": "error", "message": "draft validation warnings", "warnings": rendered["warnings"]}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2

    env = load_env()
    result = {
        "subject": rendered["subject"],
        "recipients": recipients,
        "attachments": [str(x) for x in attachments],
        "out": args.out,
    }
    if args.dry_run:
        result.update({"status": "ok", "mode": "dry-run"})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not configured(env):
        result.update({
            "status": "error",
            "mode": "not-sent",
            "message": "SMTP not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, MAIL_FROM in config/.env or environment.",
        })
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 3

    msg = build_message(env["MAIL_FROM"], recipients, rendered["subject"], rendered["body_markdown"], attachments)
    send_smtp(msg, env)
    result.update({"status": "sent", "mode": "smtp", "from": env["MAIL_FROM"]})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
