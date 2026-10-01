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
# 설계 §5: 재시도 2회까지(최초 1 + 재시도 2) — attempts 가 3 인 triaged 과제는 claim_task 가 선점 대신
# blocked 로 넘긴다(store 안의 조건부 UPDATE 1회 — 서버 핸들러가 아니다).
MAX_ATTEMPTS = 3
EXHAUSTED_RESOLUTION = f"재시도 한도({MAX_ATTEMPTS}회) 초과 — 사람 확인 필요"

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
                dedup_statuses: tuple[str, ...] = OPEN_STATUSES,
                db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """과제 생성. 같은 (source, source_ref) 가 dedup_statuses 중 하나로 남아 있으면 그 과제 id 를 그대로 돌려준다.

    기본은 열린 상태만. 신호(signals.py)는 blocked 도 포함한다 — '런북 없음 → blocked' 로 끝난 과제가 매일 다시
    생기지 않게. 닫히거나(closed) 반려(rejected)되면 다시 만든다.
    """
    if kind not in KINDS:
        raise AgentTaskError(f"알 수 없는 kind: {kind}")
    conn = connect(db_path)
    try:
        if source_ref:
            placeholders = ",".join("?" for _ in dedup_statuses)
            row = conn.execute(
                f"SELECT id FROM agent_task WHERE source=? AND source_ref=? AND status IN ({placeholders})"
                " ORDER BY id LIMIT 1", (source, source_ref, *dedup_statuses)).fetchone()
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


def find_task(source: str, source_ref: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    """상태와 무관하게 같은 (source, source_ref) 의 최신 과제. 한 번만 만들 과제(알려진 부채)의 존재 확인용."""
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agent_task WHERE source=? AND source_ref=? ORDER BY id DESC LIMIT 1",
                           (source, source_ref)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# list_tasks 정렬: 디스패처는 우선순위(priority, id), 운영 원장 화면은 최근 갱신순(updated_at DESC, id DESC)
# — 화면은 limit 에 잘려도 최신 사고가 첫 페이지에 남아야 한다.
ORDERS = {"priority": "priority, id", "recent": "updated_at DESC, id DESC"}


def list_tasks(*, db_path: str | Path = DEFAULT_DB_PATH, statuses: tuple[str, ...] = OPEN_STATUSES,
               kinds: tuple[str, ...] | None = None, limit: int = 100,
               order: str = "priority") -> list[dict]:
    if order not in ORDERS:
        raise AgentTaskError(f"알 수 없는 order: {order}")
    conn = connect(db_path)
    try:
        sql = f"SELECT * FROM agent_task WHERE status IN ({','.join('?' for _ in statuses)})"
        params: list = list(statuses)
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params += list(kinds)
        sql += f" ORDER BY {ORDERS[order]} LIMIT ?"
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
        if new_status == "triaged":
            # 재시도 경로(blocked/in_progress → triaged): 선점 표식을 비워 다음 실행이 다시 집을 수 있게 한다.
            # attempts 는 보존 — claim_task 의 MAX_ATTEMPTS 한도가 여기에 걸린다.
            fields["claimed_by"] = None
            fields["claimed_at"] = None
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


def triage_task(task_id: int, *, actor: str, detail: str | None = None,
                db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    """new 과제를 자동 분류(new→triaged). 단일 트랜잭션의 조건부 UPDATE 1회.

    update_task 는 SELECT 후 무조건 UPDATE 라 동시 claim 에서 승자의 in_progress 를 되돌릴 수 있다.
    여기서는 `WHERE status='new'` 가 붙은 UPDATE 의 rowcount 가 1 일 때만 이벤트(auto_triaged,
    status:triaged)를 남기므로, 몇 개의 실행이 동시에 와도 정확히 하나만 분류하고 이벤트도 한 번만 남는다.
    """
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE agent_task SET status='triaged', updated_at=? WHERE id=? AND status='new'",
            (_now(), task_id))
        if cur.rowcount != 1:
            conn.commit()
            return False
        add_event(task_id, actor=actor, event="auto_triaged", detail=detail, conn=conn)
        add_event(task_id, actor=actor, event="status:triaged", conn=conn)
        conn.commit()
        return True
    finally:
        conn.close()


def claim_task(task_id: int, *, actor: str, db_path: str | Path = DEFAULT_DB_PATH) -> str:
    """triaged 과제를 선점한다. 결과는 세 가지 — 각각 단일 트랜잭션 안의 조건부 UPDATE 1회로 정해진다.

    - "claimed":       in_progress + claimed_by + attempts+1 (+ claimed 이벤트)
    - "exhausted":     attempts 가 MAX_ATTEMPTS 이상인 triaged 과제 — 이 호출이 blocked 로 넘겼다
                       (+ status:blocked 이벤트). 동시에 여러 실행이 와도 rowcount 로 정확히 하나만.
    - "not_claimable": 이미 선점됐거나(다른 실행), triaged 가 아니거나(blocked 포함), 없는 과제.

    update_task 는 SELECT 후 무조건 UPDATE 라 동시 실행에서 상태를 되돌릴 수 있다 — 여기서는 쓰지 않는다
    (triage_task 와 같은 이유).
    """
    conn = connect(db_path)
    try:
        now = _now()
        cur = conn.execute(
            "UPDATE agent_task SET status='in_progress', claimed_by=?, claimed_at=?, "
            "attempts=attempts+1, updated_at=? WHERE id=? AND status='triaged' AND claimed_by IS NULL"
            " AND attempts < ?",
            (actor, now, now, task_id, MAX_ATTEMPTS))
        if cur.rowcount == 1:
            add_event(task_id, actor=actor, event="claimed", conn=conn)
            conn.commit()
            return "claimed"
        cur = conn.execute(
            "UPDATE agent_task SET status='blocked', resolution=?, updated_at=? "
            "WHERE id=? AND status='triaged' AND claimed_by IS NULL AND attempts >= ?",
            (EXHAUSTED_RESOLUTION, now, task_id, MAX_ATTEMPTS))
        if cur.rowcount == 1:
            add_event(task_id, actor=actor, event="status:blocked", detail=EXHAUSTED_RESOLUTION, conn=conn)
            conn.commit()
            return "exhausted"
        conn.commit()
        return "not_claimable"
    finally:
        conn.close()
