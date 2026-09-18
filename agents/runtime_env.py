"""런타임 환경 판별 — 프로덕션(Fly.io 단일 머신) vs 개발 Mac.

Fly 는 머신에 `FLY_APP_NAME` 을 주입한다 (`scripts/start_production.sh` 로 scheduler + web 구동).
로컬 전용 잡(git 백업·Claude 메모리 감사)의 skip 과 원격 발행(daily mailing scope prune)의
허용 여부를 이 한 곳의 기준으로 판단한다 — 호출부마다 env 를 따로 읽지 않는다.
"""
from __future__ import annotations

import os

PRODUCTION_ENV = "FLY_APP_NAME"


def is_production() -> bool:
    """프로덕션(Fly 머신) 여부 — `FLY_APP_NAME` 이 비어있지 않으면 True."""
    return bool(os.environ.get(PRODUCTION_ENV, "").strip())
