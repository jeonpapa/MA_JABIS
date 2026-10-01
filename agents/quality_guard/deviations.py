"""편차 로그 I/O — JSONL append + dedup + 경로 상수."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# BASE_DIR = repo root (agents/quality_guard/deviations.py → parents[2])
BASE_DIR = Path(__file__).resolve().parents[2]


def guard_dir() -> Path:
    """QG/RC 산출물 경로. 기본 data/quality_guard (Fly 볼륨 — 재배포에 살아남는다). QUALITY_GUARD_DIR 로 오버라이드.

    구 경로 <repo>/quality_guard 는 git 추적 역사(74 파일)로 남긴다 — 컨테이너에서는 재배포마다 지워졌고, 로컬에서는
    git_backup_job 이 매일 보고서를 커밋하던 원인. 새 산출물은 .gitignore(data/quality_guard/).
    """
    override = os.environ.get("QUALITY_GUARD_DIR")
    return Path(override) if override else BASE_DIR / "data" / "quality_guard"


GUARD_DIR = guard_dir()
DEVIATION_LOG = GUARD_DIR / "deviation_log.jsonl"


def _dedup_key(entry: dict) -> tuple:
    """동일 편차 판별용 키 — (agent, file, deviation_type, description)."""
    return (
        entry.get("agent", ""),
        entry.get("file", ""),
        entry.get("deviation_type", ""),
        entry.get("description", ""),
    )


def _write_deviation(entry: dict) -> None:
    """편차를 JSONL 파일에 추가 기록. 동일 미해결 편차가 이미 있으면 skip.

    기존에 resolved=False 로 동일 (agent, file, deviation_type, description) 편차가
    존재하면 중복 append 를 막는다. 매 review 마다 같은 경고가 쌓여 로그가 폭주하는
    것을 방지.
    """
    GUARD_DIR.mkdir(parents=True, exist_ok=True)
    entry.setdefault("timestamp", datetime.now().isoformat())
    entry.setdefault("resolved", False)

    if DEVIATION_LOG.exists():
        new_key = _dedup_key(entry)
        for line in DEVIATION_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not existing.get("resolved") and _dedup_key(existing) == new_key:
                return

    with open(DEVIATION_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    level = entry.get("severity", "INFO")
    if level == "ERROR":
        logger.error("[QualityGuard] %s", entry.get("description", ""))
    elif level == "WARNING":
        logger.warning("[QualityGuard] %s", entry.get("description", ""))
    else:
        logger.info("[QualityGuard] %s", entry.get("description", ""))
