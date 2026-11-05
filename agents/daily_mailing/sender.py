"""Daily Mailing 생성·발송 주체 스위치 (2026-10-27 사용자 결정: 헤르메스가 큐레이션·작성·발송).

- ``hermes``: 헤르메스(외부 VPS)가 스콥을 pull → 큐레이션·작성·자체 최종승인 → Gmail 발송 → run 번들 커밋.
  대시보드는 스콥 발행(``daily_mailing_scope_export``)·run 번들 동기화(``daily_mailing_runs_sync``)·헬스만 한다.
  서버 자체 생성·발송 잡(``daily_mailing_dispatch``·``daily_mailing_top_news``)은 no-op.
- ``server``: 2026-09-18 설계 — 프로덕션 서버가 직접 생성·발송. 헤르메스 버스 잡은 no-op.

잡 id 는 두 모드에서 동일하게 등록된다(tests/test_scheduler_job_specs.py 의 JOB_RUNBOOK 대조 유지).
전환은 이 값 하나로 한다 — 롤백 = ``sender: "server"`` (또는 fly secret ``DAILY_MAILING_SENDER=server``).

우선순위: env ``DAILY_MAILING_SENDER`` > ``config/settings.json`` 의 ``daily_mailing.sender`` > ``server``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

VALID_SENDERS = ("hermes", "server")
DEFAULT_SENDER = "server"
_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.json"


def mailing_sender(settings_path: str | Path | None = None) -> str:
    env = (os.environ.get("DAILY_MAILING_SENDER") or "").strip().lower()
    if env in VALID_SENDERS:
        return env
    try:
        cfg = json.loads(Path(settings_path or _SETTINGS_PATH).read_text(encoding="utf-8"))
        value = str((cfg.get("daily_mailing") or {}).get("sender") or "").strip().lower()
    except Exception:  # noqa: BLE001 — 설정 파손 시 기존 동작(server) 유지
        value = ""
    return value if value in VALID_SENDERS else DEFAULT_SENDER


def hermes_sends() -> bool:
    return mailing_sender() == "hermes"
