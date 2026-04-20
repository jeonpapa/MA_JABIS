"""SMTP 메일 발송 — Daily Mailing 파이프라인 공용.

config/.env 에서 SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / MAIL_FROM 로드.
누락 시 dry-run (STDOUT 로그) 으로 동작해 UI 테스트 가능.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_ENV_KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "SMTP_TLS")


def _load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    env_path = Path(__file__).resolve().parents[2] / "config" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in _ENV_KEYS:
                out[k] = v.strip()
    for k in _ENV_KEYS:
        if k not in out and os.environ.get(k):
            out[k] = os.environ[k]
    return out


def smtp_configured() -> bool:
    env = _load_env()
    return bool(env.get("SMTP_HOST") and env.get("SMTP_USER") and env.get("SMTP_PASSWORD") and env.get("MAIL_FROM"))


def send_email(
    recipients: Iterable[str],
    subject: str,
    body_html: str,
    body_text: str | None = None,
) -> dict:
    """메일 발송. 설정 누락 시 dry-run.

    반환: {"ok": bool, "mode": "smtp"|"dry-run", "recipients": [...], "message"?: str}
    """
    to_list = [r.strip() for r in recipients if r and r.strip()]
    if not to_list:
        return {"ok": False, "mode": "none", "recipients": [], "message": "no recipients"}

    env = _load_env()
    if not smtp_configured():
        logger.warning("[mailer] SMTP 설정 누락 — dry-run 모드. 수신자: %s", to_list)
        logger.info("[mailer dry-run] subject=%s", subject)
        return {
            "ok": True,
            "mode": "dry-run",
            "recipients": to_list,
            "message": "SMTP 미설정: config/.env 에 SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/MAIL_FROM 추가 후 재시도",
        }

    msg = EmailMessage()
    msg["From"] = env["MAIL_FROM"]
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    if body_text:
        msg.set_content(body_text)
        msg.add_alternative(body_html, subtype="html")
    else:
        msg.set_content("이 메일은 HTML 본문입니다. HTML 지원 메일 클라이언트에서 확인하세요.")
        msg.add_alternative(body_html, subtype="html")

    host = env["SMTP_HOST"]
    port = int(env.get("SMTP_PORT", "587"))
    use_tls = env.get("SMTP_TLS", "true").lower() != "false"

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
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
        logger.info("[mailer] 메일 전송 완료: %s → %s", subject, to_list)
        return {"ok": True, "mode": "smtp", "recipients": to_list}
    except Exception as e:
        logger.error("[mailer] 메일 전송 실패: %s", e, exc_info=True)
        return {"ok": False, "mode": "smtp", "recipients": to_list, "message": str(e)}


def render_subscription_digest(
    name: str,
    keywords: list[str],
    media: list[str],
    schedule: str,
    scheduled_time: str,
) -> tuple[str, str]:
    """구독 설정 → (subject, body_html). 실제 뉴스 수집은 Phase 5 의 크론잡에서 채움."""
    subject = f"[Daily Mailing] {name} — {schedule} {scheduled_time}"
    kw_html = "".join(f"<li>{k}</li>" for k in keywords) or "<li>(키워드 없음)</li>"
    media_html = ", ".join(media) if media else "(미디어 없음)"
    body_html = f"""
    <html><body style="font-family:Helvetica,sans-serif;background:#0D1117;color:#C9D1D9;padding:24px;">
        <h2 style="color:#00E5CC;">{name}</h2>
        <p style="color:#8B9BB4;">스케줄: {schedule} {scheduled_time}</p>
        <h3 style="color:#F59E0B;">모니터링 키워드</h3>
        <ul>{kw_html}</ul>
        <h3 style="color:#F59E0B;">모니터링 미디어</h3>
        <p>{media_html}</p>
        <hr style="border-color:#1E2530;" />
        <p style="font-size:12px;color:#4A5568;">본 메일은 MA AI Dossier 대쉬보드의 Daily Mailing 설정에 따라 자동 발송됩니다.</p>
    </body></html>
    """
    return subject, body_html
