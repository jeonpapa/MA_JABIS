"""에이전트 작업 원장 — 과제·감사 로그·정책.

패턴: agents/service_requests/store.py(스키마+감사 테이블), agents/daily_mailing/run_store.py(조건부
UPDATE 선점). 모든 상태 전이는 agent_task_event 에 append-only 로 남는다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"
BUSY_TIMEOUT_SECONDS = 30.0

KINDS = ("ops_incident", "user_request", "improvement", "release")
STATUSES = ("new", "triaged", "in_progress", "verified", "awaiting_approval",
            "deploying", "deployed", "closed", "blocked", "rejected")
OPEN_STATUSES = ("new", "triaged", "in_progress", "verified", "awaiting_approval", "deploying")
TERMINAL_STATUSES = ("closed", "rejected")
# 허용된 전이만 통과시킨다 (설계 §5 상태 기계)
ALLOWED_TRANSITIONS = {
    "new": ("triaged", "rejected", "blocked"),
    "triaged": ("in_progress", "rejected", "blocked"),
    "in_progress": ("verified", "closed", "blocked", "rejected", "triaged"),
    "verified": ("awaiting_approval", "deploying", "closed", "blocked"),
    "awaiting_approval": ("deploying", "rejected", "blocked"),
    "deploying": ("deployed", "blocked"),
    "deployed": ("closed", "blocked"),
    "blocked": ("triaged", "rejected", "closed"),
    "closed": (),
    "rejected": (),
}
UPDATABLE = frozenset({"status", "priority", "risk", "branch", "commit_ref",
                       "deploy_version", "resolution", "cost_usd", "detail"})

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agent_task (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    source       TEXT NOT NULL,
    source_ref   TEXT,
    title        TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '',
    priority     INTEGER NOT NULL DEFAULT 3,
    risk         TEXT,
    status       TEXT NOT NULL DEFAULT 'new',
    claimed_by   TEXT,
    claimed_at   TEXT,
    branch       TEXT,
    commit_ref   TEXT,
    deploy_version TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    cost_usd     REAL NOT NULL DEFAULT 0,
    resolution   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_task_status ON agent_task(status, priority);
CREATE INDEX IF NOT EXISTS idx_agent_task_source ON agent_task(source, source_ref);

CREATE TABLE IF NOT EXISTS agent_task_event (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    actor      TEXT NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_task_event_task ON agent_task_event(task_id, id);

CREATE TABLE IF NOT EXISTS agent_policy (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class AgentTaskError(RuntimeError):
    """허용되지 않은 전이나 없는 과제."""


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_event(task_id: int, *, actor: str, event: str, detail: str | None = None,
              db_path: str | Path = DEFAULT_DB_PATH, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        conn.execute("INSERT INTO agent_task_event (task_id, actor, event, detail, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (task_id, actor, event, detail, _now()))
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()


def create_task(*, kind: str, source: str, title: str, detail: str = "", priority: int = 3,
                source_ref: str | None = None, actor: str = "system",
                db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """과제 생성. 같은 (source, source_ref) 가 아직 열려 있으면 그 과제 id 를 그대로 돌려준다."""
    if kind not in KINDS:
        raise AgentTaskError(f"알 수 없는 kind: {kind}")
    conn = connect(db_path)
    try:
        if source_ref:
            placeholders = ",".join("?" for _ in OPEN_STATUSES)
            row = conn.execute(
                f"SELECT id FROM agent_task WHERE source=? AND source_ref=? AND status IN ({placeholders})"
                " ORDER BY id LIMIT 1", (source, source_ref, *OPEN_STATUSES)).fetchone()
            if row:
                return int(row["id"])
        now = _now()
        cur = conn.execute(
            "INSERT INTO agent_task (kind, source, source_ref, title, detail, priority, status,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?)",
            (kind, source, source_ref, title, detail, int(priority), now, now))
        task_id = int(cur.lastrowid)
        add_event(task_id, actor=actor, event="created", detail=title, conn=conn)
        conn.commit()
        return task_id
    finally:
        conn.close()


def get_task(task_id: int, *, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agent_task WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_tasks(*, db_path: str | Path = DEFAULT_DB_PATH, statuses: tuple[str, ...] = OPEN_STATUSES,
               kinds: tuple[str, ...] | None = None, limit: int = 100) -> list[dict]:
    conn = connect(db_path)
    try:
        sql = f"SELECT * FROM agent_task WHERE status IN ({','.join('?' for _ in statuses)})"
        params: list = list(statuses)
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params += list(kinds)
        sql += " ORDER BY priority, id LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def list_events(task_id: int, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM agent_task_event WHERE task_id=? ORDER BY id", (task_id,)).fetchall()]
    finally:
        conn.close()


def update_task(task_id: int, *, actor: str, db_path: str | Path = DEFAULT_DB_PATH, **fields) -> dict:
    unknown = set(fields) - UPDATABLE
    if unknown:
        raise AgentTaskError(f"수정할 수 없는 필드: {sorted(unknown)}")
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agent_task WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise AgentTaskError(f"없는 과제: {task_id}")
        new_status = fields.get("status")
        if new_status is not None:
            if new_status not in STATUSES:
                raise AgentTaskError(f"알 수 없는 status: {new_status}")
            if new_status not in ALLOWED_TRANSITIONS[row["status"]]:
                raise AgentTaskError(f"허용되지 않은 전이: {row['status']} → {new_status}")
        fields["updated_at"] = _now()
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE agent_task SET {sets} WHERE id=?", (*fields.values(), task_id))
        if new_status is not None:
            add_event(task_id, actor=actor, event=f"status:{new_status}",
                      detail=fields.get("resolution"), conn=conn)
        conn.commit()
        return dict(conn.execute("SELECT * FROM agent_task WHERE id=?", (task_id,)).fetchone())
    finally:
        conn.close()


def claim_task(task_id: int, *, actor: str, db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    """triaged 과제를 선점(조건부 UPDATE 1회). 성공 시 in_progress + attempts +1."""
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE agent_task SET status='in_progress', claimed_by=?, claimed_at=?, "
            "attempts=attempts+1, updated_at=? WHERE id=? AND status='triaged' AND claimed_by IS NULL",
            (actor, _now(), _now(), task_id))
        if cur.rowcount != 1:
            conn.commit()
            return False
        add_event(task_id, actor=actor, event="claimed", conn=conn)
        conn.commit()
        return True
    finally:
        conn.close()
