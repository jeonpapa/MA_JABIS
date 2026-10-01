"""스케줄러 잡 결과 표준화 + 신호 경계 (P1.5 Task 1).

규약: 잡 함수는 **dict 를 돌려주거나 예외를 던진다**. dict 에서 해석하는 키:
  ok(bool, 기본 True) · counts(dict) · errors(list) · error(str) · failed(list[dict|str]) · error_class("transient"|"config")
`guarded_job(job_id)` 는 (1) 예외 → 실패 신호 + exception 로그, (2) errors/error/failed 가 비어 있지 않으면 실패 신호,
(3) scheduler_job_run 에 실행 1행. 신호·기록 실패는 여기서 삼킨다 — 잡을 죽이지 않는다 (P1 `_agent_record_*` 관례).
error_class="config" 는 자격증명·토큰 부재처럼 재실행으로 안 풀리는 실패 — source_ref 에 날짜 대신 'config' 를 써서
사람이 닫을 때까지 과제 1건만 유지한다.
"""
from __future__ import annotations

import functools
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .store import DEFAULT_DB_PATH, connect

logger = logging.getLogger(__name__)

RETENTION_DAYS = 14
JOB_RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler_job_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    counts      TEXT NOT NULL DEFAULT '{}',
    errors      TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_job_run_job ON scheduler_job_run(job_id, id);
"""


def resolve_db_path() -> Path:
    """원장 DB 경로. 테스트는 AGENT_TASK_DB 로 임시 파일을 지정한다(tests/conftest.py)."""
    return Path(os.environ.get("AGENT_TASK_DB") or DEFAULT_DB_PATH)


def _fmt_failed(item) -> str:
    if isinstance(item, dict):
        who = item.get("slug") or item.get("id") or item.get("subscription_id") or "?"
        why = item.get("reason") or item.get("error") or item.get("detail") or ""
        return f"{who}: {why}".rstrip(": ")
    return str(item)


def normalize_result(raw) -> dict:
    """잡 반환값 → {ok, counts, errors, error_class}. None 은 성공. dict 가 아니면 성공(문자열만 남김)."""
    if raw is None:
        return {"ok": True, "counts": {}, "errors": [], "error_class": "transient"}
    if not isinstance(raw, dict):
        return {"ok": True, "counts": {"result": str(raw)[:200]}, "errors": [], "error_class": "transient"}
    errors = [str(e)[:300] for e in (raw.get("errors") or [])]
    if raw.get("error"):
        errors.append(str(raw["error"])[:300])
    errors += [_fmt_failed(f)[:300] for f in (raw.get("failed") or [])]
    ok = bool(raw.get("ok", True)) and not errors
    counts = raw.get("counts")
    if not isinstance(counts, dict):
        counts = {k: v for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {"ok": ok, "counts": counts, "errors": errors,
            "error_class": raw.get("error_class") or "transient"}


def record_job_run(job_id: str, started: datetime, res: dict, *, db_path: str | Path | None = None) -> None:
    path = Path(db_path) if db_path else resolve_db_path()
    conn = connect(path)
    try:
        conn.executescript(JOB_RUN_SCHEMA)
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO scheduler_job_run (job_id, started_at, finished_at, ok, counts, errors) VALUES (?,?,?,?,?,?)",
            (job_id, started.isoformat(), now.isoformat(), 1 if res["ok"] else 0,
             json.dumps(res["counts"], ensure_ascii=False, default=str),
             json.dumps(res["errors"], ensure_ascii=False)))
        conn.execute("DELETE FROM scheduler_job_run WHERE started_at < ?",
                     ((now - timedelta(days=RETENTION_DAYS)).isoformat(),))
        conn.commit()
    finally:
        conn.close()


def recent_job_runs(*, hours: int = 24, db_path: str | Path | None = None) -> list[dict]:
    """job_id 별 마지막 실행(창 안). /api/health 의 jobs_failed_24h 와 신선도 판정기가 쓴다."""
    path = Path(db_path) if db_path else resolve_db_path()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = connect(path)
    try:
        conn.executescript(JOB_RUN_SCHEMA)
        rows = conn.execute(
            "SELECT r.job_id, r.started_at, r.finished_at, r.ok, r.counts, r.errors FROM scheduler_job_run r "
            "JOIN (SELECT job_id, MAX(id) AS mid FROM scheduler_job_run WHERE started_at >= ? GROUP BY job_id) m "
            "ON r.id = m.mid ORDER BY r.job_id", (since,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _record(job_id: str, started: datetime, res: dict, *, record_runs: bool) -> None:
    path = resolve_db_path()
    if record_runs:
        try:
            record_job_run(job_id, started, res, db_path=path)
        except Exception as e:
            logger.warning("[job:%s] 실행 기록 실패: %s", job_id, e)
    if res["ok"]:
        return
    logger.error("[job:%s] 실패 — %s", job_id, "; ".join(res["errors"])[:500])
    try:
        from .signals import record_job_failure
        record_job_failure(job_id, "; ".join(res["errors"])[:300], error_class=res["error_class"],
                           errors=list(res["errors"]), db_path=path)
    except Exception as e:
        logger.warning("[job:%s] 실패 신호 기록 실패: %s", job_id, e)


def guarded_job(job_id: str, *, record_runs: bool = True):
    """스케줄러 잡 데코레이터. 반환값을 표준화하고 실패를 원장에 남긴다. 예외는 밖으로 나가지 않는다.

    record_runs=False 는 heartbeat 처럼 분 단위로 도는 잡용 — 실행 기록 테이블을 채우지 않는다.
    """
    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            started = datetime.now(timezone.utc)
            try:
                res = normalize_result(fn(*args, **kwargs))
            except Exception as exc:
                logger.exception("[job:%s] 예외로 실패", job_id)
                res = {"ok": False, "counts": {}, "errors": [f"{type(exc).__name__}: {exc}"[:300]],
                       "error_class": "transient"}
            _record(job_id, started, res, record_runs=record_runs)
            return res
        wrapper.__job_id__ = job_id  # type: ignore[attr-defined]
        return wrapper
    return deco
