"""모니터링 텔레그램 알림 — 서버가 전용 봇(@MAMONSSU4FUEM_bot)으로 직접 보낸다(노트북이 꺼져 있어도 도착).

비밀값: TELEGRAM_MONITOR_BOT_TOKEN, TELEGRAM_MONITOR_CHAT_ID (Fly secrets / config/.env). 값은 로그에 남기지 않는다.
없으면 보내지 않고 {"ok": False, "reason": "not_configured"} — 회차 진행은 막지 않는다.
"""
from __future__ import annotations

import json
import os
import urllib.request


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_MONITOR_BOT_TOKEN") and os.environ.get("TELEGRAM_MONITOR_CHAT_ID"))


def send(text: str, *, opener=None) -> dict:
    token, chat = os.environ.get("TELEGRAM_MONITOR_BOT_TOKEN"), os.environ.get("TELEGRAM_MONITOR_CHAT_ID")
    if not token or not chat:
        return {"ok": False, "reason": "not_configured"}
    body = json.dumps({"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with (opener or urllib.request.urlopen)(req, timeout=20) as r:
            data = json.loads(r.read())
        return {"ok": bool(data.get("ok")), "message_id": (data.get("result") or {}).get("message_id")}
    except Exception as e:   # 토큰이 들어간 URL 은 남기지 않는다
        return {"ok": False, "reason": type(e).__name__}
