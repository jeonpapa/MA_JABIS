"""스케줄러 생존 신호 (P1.5 Task 4).

스케줄러 프로세스가 60초마다 파일을 atomic(tmp → os.replace) 으로 갱신하고, 웹 프로세스의 /api/health 가 파일 mtime 으로
나이를 잰다. 파일인 이유: sqlite 잠금과 무관하게 남고(DB 가 잠겨도 '살아 있다' 는 보인다), 볼륨(data/)에 있어 재배포 뒤에도
직전 값이 남는다.

판정 규칙(오탐 억제):
  - 부팅 유예(BOOT_GRACE_S) 안에는 파일이 없어도·오래됐어도 stale 이 아니다 — 배포 직후 볼륨에 남은 전 머신의 옛 heartbeat,
    또는 첫 볼륨(파일 없음)으로 Fly HTTP check 가 새 머신을 죽이는 일을 막는다. 스케줄러는 기동 후 60초 안에 덮어쓴다.
  - 유예가 지난 뒤 파일이 없거나 STALE_AFTER_S 를 넘으면 stale — 60초 잡이 15번 연속 못 돈 것이라 스케줄러 정지/hang 으로 본다.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HEARTBEAT_PATH = REPO_ROOT / "data" / "scheduler_heartbeat.json"
STALE_AFTER_S = 900     # 15분: 60초 잡이 15번 연속 못 돌면 스케줄러가 멈춘 것
BOOT_GRACE_S = 300      # 웹 프로세스 기동 5분 안에는 파일이 없거나 옛것이어도 정상(첫 볼륨·배포 직후)


def write_heartbeat(*, pid: int, path: Path | None = None) -> None:
    path = path or HEARTBEAT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": time.time(), "pid": pid}), encoding="utf-8")
    os.replace(tmp, path)


def heartbeat_status(*, now: float, process_started_at: float, path: Path | None = None) -> dict:
    """{'age_s': float|None, 'stale': bool}. 파일 없음·오래됨 모두 부팅 유예 뒤에만 stale."""
    path = path or HEARTBEAT_PATH
    try:
        age = now - path.stat().st_mtime
    except OSError:          # 없음(FileNotFoundError)·볼륨 미마운트 등 — 유예 뒤에는 stale 로 본다
        age = None
    uptime = now - process_started_at
    in_boot_grace = uptime <= BOOT_GRACE_S
    stale = (not in_boot_grace) and (age is None or age > STALE_AFTER_S)
    return {"age_s": None if age is None else round(age, 1), "stale": stale}
