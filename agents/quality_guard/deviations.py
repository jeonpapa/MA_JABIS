"""편차 로그 I/O — JSONL append + dedup + 경로 상수."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# BASE_DIR = repo root (agents/quality_guard/deviations.py → parents[2])
BASE_DIR = Path(__file__).resolve().parents[2]
GUARD_DIR = BASE_DIR / "quality_guard"
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
