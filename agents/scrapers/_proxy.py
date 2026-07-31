"""해외 스크레이퍼 공용 residential-proxy 헬퍼.

프로덕션 fly 데이터센터 IP 는 일부 소스(mhlw.go.jp xlsx, compendium.ch)에서
간헐적으로 차단/스로틀된다 → 국가 라우팅 residential proxy(dataimpulse `__cr.jp`/`__cr.ch`)
로 우회. UK MIMS(`MIMS_UK_PROXY`)·국내 HIRA(`HIRA_PROXY`)와 동일 컨벤션.

env 값 예: "http://<user>__cr.jp:<pass>@gw.dataimpulse.com:823"
- 국가 라우팅(`__cr.<cc>`)은 env 문자열에 이미 포함 → 여기선 sticky sessid 만 덧붙인다.
- sticky: 요청마다 IP 로테이션을 막아 세션 동안 같은 exit IP 유지. 재시도 시엔
  새 sessid 로 다른 exit IP 를 받아 차단 IP 를 회피한다.
"""
from __future__ import annotations

import logging
import os
import uuid
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _sticky(proxy_url: str | None) -> str | None:
    """로테이팅 residential proxy 를 sticky 로 — username 에 `__sessid.<rand>` 주입.

    dataimpulse 실측 포맷: `username__sessid.<id>`. 이미 sessid 있으면 그대로 둔다.
    """
    if not proxy_url:
        return proxy_url
    u = urlparse(proxy_url)
    user = u.username or ""
    host = u.hostname or ""
    if not user or "sessid" in user.lower():
        return proxy_url
    if "dataimpulse" in host:
        user = f"{user}__sessid.{uuid.uuid4().hex[:12]}"
        auth = f"{user}:{u.password}@" if u.password else f"{user}@"
        return f"{u.scheme or 'http'}://{auth}{host}:{u.port}"
    return proxy_url


def proxy_from_env(*env_vars: str) -> str | None:
    """첫 번째로 값이 있는 env 의 proxy URL(sticky 적용) 반환. 없으면 None.

    런타임에 읽는다(.env 가 import 시점엔 아직 로드 안 됐을 수 있음).
    """
    for ev in env_vars:
        raw = os.environ.get(ev, "").strip()
        if raw:
            return _sticky(raw)
    return None


def requests_proxies(proxy_url: str | None) -> dict | None:
    """requests `proxies=` 인자용 dict. None 이면 None."""
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def playwright_proxy(proxy_url: str | None) -> dict | None:
    """requests proxy URL → Playwright `new_context(proxy=)` dict.

    {server, username, password} — Playwright 는 server 에 인증정보를 넣지 않는다.
    """
    if not proxy_url:
        return None
    u = urlparse(proxy_url)
    server = f"{u.scheme or 'http'}://{u.hostname}:{u.port}"
    d: dict = {"server": server}
    if u.username:
        d["username"] = u.username
    if u.password:
        d["password"] = u.password
    return d
