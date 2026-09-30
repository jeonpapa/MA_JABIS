# 에이전트 팀 P1 — 운영 감시자 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프로덕션이 스스로 만든 운영 신호를 `agent_task` 원장에 쌓고, VPS 디스패처가 그 과제를 집어 정해진 런북으로 복구하며, 관리자가 그 전부를 운영 원장 화면에서 본다.

**Architecture:** 원장·런북·API 는 **프로덕션(fly)** 안에 둔다(DB·스케줄러에 직접 닿아야 하므로). VPS 는 얇은 디스패처만 돌린다 — 로그인 → 열린 과제 조회 → 선점 → 런북 실행 요청 → 결과 회신. **P1 에는 LLM 이 없다**(런북은 결정적 파이썬 함수). 인증은 `users` 테이블의 `role='agent'` 서비스 계정 + 기존 JWT 로그인을 재사용한다.

**Tech Stack:** Python 3.12, sqlite3(WAL, 공용 `drug_prices.db`), Flask(`api/server.py`), APScheduler(`scheduler.py`), React+TS(Vite), pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-agent-team-full-service-design.md` (§3 아키텍처, §5 작업 버스, §7 관측, §8 안전장치, §9 P1).

---

## 작업 규칙

- Python: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python`. 테스트는 저장소 루트에서 `python -m pytest <경로> -q`.
- TDD: 실패 테스트 → 최소 구현 → 통과 → 커밋. 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- 실제 외부 호출 금지(네트워크·Gmail·LLM). `flyctl`·`git push`·배포 금지.
- 기존 패턴을 따른다: 저장소 = `agents/service_requests/store.py`, 상태 선점 = `agents/daily_mailing/run_store.py:claim_for_sending`, sqlite 연결 = `agents/daily_mailing/storage.py:connect`.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `agents/agent_tasks/store.py` (신규) | `agent_task`·`agent_task_event`·`agent_policy` 스키마와 CRUD·상태 전이·감사 로그 |
| `agents/agent_tasks/signals.py` (신규) | 운영 신호 → 과제 변환(중복 억제 포함) |
| `agents/agent_tasks/runbooks.py` (신규) | 결정적 복구 런북 레지스트리와 실행 |
| `agents/agent_tasks/policy.py` (신규) | 정지 스위치·일일 예산 판정(순수 함수 + 조회) |
| `api/auth.py` (수정) | `require_auth(role=...)` 를 임의 역할로 일반화(admin 은 상위 권한) |
| `api/server.py` (수정) | `/api/agent/*`(agent 역할) + `/api/admin/agent-tasks`(admin) 엔드포인트 |
| `scheduler.py` (수정) | 잡 실패·헬스 문제를 신호로 기록 |
| `scripts/agent_dispatcher.py` (신규) | VPS 디스패처(로그인·선점·런북 요청·회신, `--dry-run`) |
| `frontend/src/api/agentTasks.ts` (신규) | 운영 원장 API 클라이언트 |
| `frontend/src/pages/admin/agent-tasks/page.tsx` (신규) | 운영 원장 화면 |
| `docs/agent_team/VPS_SETUP.md` (신규) | VPS 크론·환경변수·계정 발급 절차 |

---

### Task 1: 원장 저장소 (`agent_task` · `agent_task_event` · `agent_policy`)

**Files:**
- Create: `agents/agent_tasks/__init__.py`, `agents/agent_tasks/store.py`
- Test: `tests/test_agent_task_store.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_agent_task_store.py
from __future__ import annotations

import pytest

from agents.agent_tasks.store import (
    OPEN_STATUSES,
    AgentTaskError,
    add_event,
    claim_task,
    create_task,
    get_task,
    list_events,
    list_tasks,
    update_task,
)


def _new(db, **kw):
    data = dict(kind="ops_incident", source="health_check", source_ref="dm:2026-09-30",
                title="Daily Mailing 미생성", detail="구독 14 오늘 run 없음", priority=2)
    data.update(kw)
    return create_task(db_path=db, **data)


def test_create_and_get(tmp_path):
    db = tmp_path / "a.db"
    tid = _new(db)
    t = get_task(tid, db_path=db)
    assert t["status"] == "new" and t["attempts"] == 0 and t["claimed_by"] is None
    assert [e["event"] for e in list_events(tid, db_path=db)] == ["created"]


def test_duplicate_source_ref_is_not_recreated_while_open(tmp_path):
    db = tmp_path / "a.db"
    first = _new(db)
    again = _new(db)
    assert again == first                     # 같은 (source, source_ref) 가 열려 있으면 같은 과제
    update_task(first, db_path=db, status="closed", actor="test", resolution="처리됨")
    third = _new(db)
    assert third != first                     # 닫힌 뒤에는 새 과제


def test_claim_is_exclusive_and_counts_attempts(tmp_path):
    db = tmp_path / "a.db"
    tid = _new(db)
    update_task(tid, db_path=db, status="triaged", actor="sentinel")
    assert claim_task(tid, actor="vps-1", db_path=db) is True
    assert claim_task(tid, actor="vps-2", db_path=db) is False
    t = get_task(tid, db_path=db)
    assert t["status"] == "in_progress" and t["claimed_by"] == "vps-1" and t["attempts"] == 1


def test_invalid_transition_rejected(tmp_path):
    db = tmp_path / "a.db"
    tid = _new(db)
    with pytest.raises(AgentTaskError):
        update_task(tid, db_path=db, status="deployed", actor="vps-1")


def test_list_tasks_filters(tmp_path):
    db = tmp_path / "a.db"
    a = _new(db)
    b = _new(db, source_ref="dm:2026-10-01", title="다른 신호")
    update_task(b, db_path=db, status="triaged", actor="sentinel")
    assert {t["id"] for t in list_tasks(db_path=db, statuses=OPEN_STATUSES)} == {a, b}
    assert [t["id"] for t in list_tasks(db_path=db, statuses=("triaged",))] == [b]


def test_events_are_append_only_audit(tmp_path):
    db = tmp_path / "a.db"
    tid = _new(db)
    update_task(tid, db_path=db, status="triaged", actor="sentinel")
    claim_task(tid, actor="vps-1", db_path=db)
    add_event(tid, actor="vps-1", event="runbook_started", detail="domestic_price_catchup", db_path=db)
    update_task(tid, db_path=db, status="closed", actor="vps-1", resolution="9월분 적재 완료")
    assert [e["event"] for e in list_events(tid, db_path=db)] == [
        "created", "status:triaged", "claimed", "runbook_started", "status:closed"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_agent_task_store.py -q`
Expected: FAIL — `ModuleNotFoundError: agents.agent_tasks.store`

- [ ] **Step 3: 구현**

```python
# agents/agent_tasks/__init__.py
"""에이전트 팀 운영 원장 (설계: docs/superpowers/specs/2026-09-30-agent-team-full-service-design.md)."""
```

```python
# agents/agent_tasks/store.py
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
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_agent_task_store.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add agents/agent_tasks/__init__.py agents/agent_tasks/store.py tests/test_agent_task_store.py
git commit -m "feat(agent-team): 작업 원장 저장소 — 과제·감사 로그·상태 전이·선점"
```

---

### Task 2: 정책(정지 스위치·일일 예산)

**Files:**
- Create: `agents/agent_tasks/policy.py`
- Test: `tests/test_agent_policy.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_agent_policy.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.agent_tasks.policy import DEFAULT_POLICY, get_policy, set_policy, spend_check
from agents.agent_tasks.store import create_task, update_task

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=KST)


def test_default_policy_and_override(tmp_path):
    db = tmp_path / "a.db"
    p = get_policy(db_path=db)
    assert p == DEFAULT_POLICY
    set_policy({"enabled": False, "max_tasks_per_day": 5}, actor="admin@msd.com", db_path=db)
    p2 = get_policy(db_path=db)
    assert p2["enabled"] is False and p2["max_tasks_per_day"] == 5
    assert p2["max_cost_usd_per_day"] == DEFAULT_POLICY["max_cost_usd_per_day"]


def test_spend_check_blocks_when_disabled(tmp_path):
    db = tmp_path / "a.db"
    set_policy({"enabled": False}, actor="admin@msd.com", db_path=db)
    ok, reason = spend_check(now=NOW, db_path=db)
    assert ok is False and reason == "disabled"


def test_spend_check_counts_today_only(tmp_path):
    db = tmp_path / "a.db"
    set_policy({"max_tasks_per_day": 2}, actor="admin@msd.com", db_path=db)
    for i in range(2):
        tid = create_task(kind="ops_incident", source="health_check", source_ref=f"x{i}",
                          title="t", db_path=db)
        update_task(tid, db_path=db, status="triaged", actor="sentinel")
        update_task(tid, db_path=db, status="in_progress", actor="vps-1")
    ok, reason = spend_check(now=NOW, db_path=db)
    assert ok is False and reason == "task_budget"
    ok2, _ = spend_check(now=NOW + timedelta(days=1), db_path=db)
    assert ok2 is True


def test_spend_check_cost_budget(tmp_path):
    db = tmp_path / "a.db"
    set_policy({"max_cost_usd_per_day": 1.0}, actor="admin@msd.com", db_path=db)
    tid = create_task(kind="improvement", source="quality_guard", source_ref="q1", title="t", db_path=db)
    update_task(tid, db_path=db, status="triaged", actor="sentinel")
    update_task(tid, db_path=db, status="in_progress", actor="vps-1")
    update_task(tid, db_path=db, actor="vps-1", cost_usd=1.5)
    ok, reason = spend_check(now=NOW, db_path=db)
    assert ok is False and reason == "cost_budget"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_agent_policy.py -q` → FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: 구현**

```python
# agents/agent_tasks/policy.py
"""에이전트 팀 정책 — 정지 스위치와 일일 예산 (설계 §8).

정책은 agent_policy 테이블(키-값)에 저장하고, 없으면 DEFAULT_POLICY 를 쓴다.
선점 직전에 spend_check 로 확인한다: 꺼져 있거나 오늘 예산을 넘겼으면 새 작업을 잡지 않는다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import DEFAULT_DB_PATH, connect

KST = timezone(timedelta(hours=9))
DEFAULT_POLICY: dict = {
    "enabled": True,
    "max_tasks_per_day": 20,
    "max_cost_usd_per_day": 20.0,
    "max_deploys_per_day": 3,
}


def get_policy(*, db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    conn = connect(db_path)
    try:
        rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM agent_policy")}
    finally:
        conn.close()
    policy = dict(DEFAULT_POLICY)
    for key, raw in rows.items():
        if key in policy:
            try:
                policy[key] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    return policy


def set_policy(values: dict, *, actor: str, db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(db_path)
    try:
        for key, value in values.items():
            if key not in DEFAULT_POLICY:
                continue
            conn.execute("INSERT OR REPLACE INTO agent_policy (key, value, updated_at) VALUES (?, ?, ?)",
                         (key, json.dumps(value), now))
        conn.execute("INSERT INTO agent_task_event (task_id, actor, event, detail, created_at) "
                     "VALUES (0, ?, 'policy_changed', ?, ?)", (actor, json.dumps(values, ensure_ascii=False), now))
        conn.commit()
    finally:
        conn.close()
    return get_policy(db_path=db_path)


def _day_bounds(now: datetime) -> tuple[str, str]:
    kst = now.astimezone(KST)
    start = kst.replace(hour=0, minute=0, second=0, microsecond=0)
    return (start.astimezone(timezone.utc).isoformat(),
            (start + timedelta(days=1)).astimezone(timezone.utc).isoformat())


def spend_check(*, now: datetime | None = None, db_path: str | Path = DEFAULT_DB_PATH) -> tuple[bool, str]:
    """(선점 가능 여부, 사유). 사유: ok | disabled | task_budget | cost_budget | deploy_budget."""
    policy = get_policy(db_path=db_path)
    if not policy["enabled"]:
        return False, "disabled"
    start, end = _day_bounds(now or datetime.now(KST))
    conn = connect(db_path)
    try:
        claimed = conn.execute(
            "SELECT COUNT(*) FROM agent_task_event WHERE event='claimed' AND created_at >= ? AND created_at < ?",
            (start, end)).fetchone()[0]
        cost = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM agent_task WHERE updated_at >= ? AND updated_at < ?",
            (start, end)).fetchone()[0]
        deploys = conn.execute(
            "SELECT COUNT(*) FROM agent_task_event WHERE event='status:deployed' AND created_at >= ? AND created_at < ?",
            (start, end)).fetchone()[0]
    finally:
        conn.close()
    if claimed >= policy["max_tasks_per_day"]:
        return False, "task_budget"
    if cost >= policy["max_cost_usd_per_day"]:
        return False, "cost_budget"
    if deploys >= policy["max_deploys_per_day"]:
        return False, "deploy_budget"
    return True, "ok"
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_agent_policy.py -q` → 4 passed
- [ ] **Step 5: 커밋**

```bash
git add agents/agent_tasks/policy.py tests/test_agent_policy.py
git commit -m "feat(agent-team): 정지 스위치·일일 예산 정책"
```

---

### Task 3: 복구 런북 레지스트리

**Files:**
- Create: `agents/agent_tasks/runbooks.py`
- Test: `tests/test_agent_runbooks.py`

런북은 **프로덕션 안에서** 실행되는 결정적 함수다. P1 에는 세 개만 둔다.

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_agent_runbooks.py
from __future__ import annotations

import pytest

from agents.agent_tasks import runbooks


def test_registry_lists_known_runbooks():
    assert set(runbooks.list_runbooks()) == {
        "domestic_price_catchup", "daily_mailing_dispatch", "daily_mailing_top_news"}


def test_unknown_runbook_raises():
    with pytest.raises(runbooks.RunbookError):
        runbooks.run("does_not_exist")


def test_domestic_catchup_reports_ingested(monkeypatch):
    calls = {}

    def fake_run_sync(config, base_dir, max_pages=3, force=False):
        calls["force"] = force
        return {"ingested": [{"apply_date": "2026.09.01", "rows": 21980}], "errors": []}

    monkeypatch.setattr(runbooks, "_domestic_run_sync", fake_run_sync)
    out = runbooks.run("domestic_price_catchup")
    assert out["ok"] is True and calls["force"] is True
    assert "2026.09.01" in out["summary"]


def test_domestic_catchup_failure_is_not_ok(monkeypatch):
    monkeypatch.setattr(runbooks, "_domestic_run_sync",
                        lambda *a, **k: {"ingested": [], "errors": ["HIRA 게시판 로딩 실패"]})
    out = runbooks.run("domestic_price_catchup")
    assert out["ok"] is False and "HIRA" in out["summary"]


def test_daily_mailing_dispatch_runbook(monkeypatch):
    monkeypatch.setattr(runbooks, "_dispatch_due",
                        lambda deps: {"generated": [{"subscription_id": 14}], "errors": [], "retried": 1})
    monkeypatch.setattr(runbooks, "_dispatch_deps", lambda: object())
    out = runbooks.run("daily_mailing_dispatch")
    assert out["ok"] is True and "1건 생성" in out["summary"]


def test_top_news_runbook(monkeypatch):
    monkeypatch.setattr(runbooks, "_dispatch_deps", lambda: type("D", (), {
        "load_top_ma_news": staticmethod(lambda run_date, retry_failed=False: [1, 2])})())
    out = runbooks.run("daily_mailing_top_news")
    assert out["ok"] is True and "2건" in out["summary"]
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_agent_runbooks.py -q` → FAIL
- [ ] **Step 3: 구현**

```python
# agents/agent_tasks/runbooks.py
"""복구 런북 — 운영 감시자가 실행하는 결정적 절차 (LLM 없음).

프로덕션 안에서 실행된다(스케줄러·DB 접근 필요). 각 런북은 {'ok', 'summary', 'detail'} 를 돌려주고,
예외를 밖으로 던지지 않는다 — 실패도 결과값으로 표현해 과제에 기록한다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]


class RunbookError(RuntimeError):
    """등록되지 않은 런북."""


def _load_config() -> dict:
    return json.loads((REPO_ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


def _domestic_run_sync(config: dict, base_dir: Path, max_pages: int = 3, force: bool = False) -> dict:
    from agents.domestic_catchup import run_sync
    return run_sync(config, base_dir, max_pages=max_pages, force=force)


def _dispatch_deps():
    from agents.daily_mailing.dispatch import DispatchDeps
    return DispatchDeps.production()


def _dispatch_due(deps):
    from agents.daily_mailing.dispatch import dispatch_due_subscriptions
    return dispatch_due_subscriptions(deps)


def _runbook_domestic_price_catchup() -> dict:
    out = _domestic_run_sync(_load_config(), REPO_ROOT, max_pages=3, force=True)
    ingested = out.get("ingested") or []
    errors = out.get("errors") or []
    if errors and not ingested:
        return {"ok": False, "summary": f"국내 약가 캐치업 실패: {errors[0]}", "detail": out}
    dates = ", ".join(str(i.get("apply_date")) for i in ingested) or "신규 없음"
    return {"ok": True, "summary": f"국내 약가 캐치업 완료 — {dates}", "detail": out}


def _runbook_daily_mailing_dispatch() -> dict:
    res = _dispatch_due(_dispatch_deps())
    generated = res.get("generated") or []
    errors = res.get("errors") or []
    ok = not errors
    return {"ok": ok,
            "summary": f"Daily Mailing 판정 — {len(generated)}건 생성, 재시도 {res.get('retried', 0)}, 오류 {len(errors)}",
            "detail": res}


def _runbook_daily_mailing_top_news() -> dict:
    from agents.daily_mailing.schedule import KST
    run_date = datetime.now(KST).date().isoformat()
    items = _dispatch_deps().load_top_ma_news(run_date, retry_failed=True)
    return {"ok": bool(items), "summary": f"주요 MA 뉴스 {len(items)}건", "detail": {"run_date": run_date}}


RUNBOOKS: dict[str, Callable[[], dict]] = {
    "domestic_price_catchup": _runbook_domestic_price_catchup,
    "daily_mailing_dispatch": _runbook_daily_mailing_dispatch,
    "daily_mailing_top_news": _runbook_daily_mailing_top_news,
}


def list_runbooks() -> list[str]:
    return sorted(RUNBOOKS)


def run(name: str) -> dict:
    fn = RUNBOOKS.get(name)
    if fn is None:
        raise RunbookError(f"등록되지 않은 런북: {name}")
    try:
        result = fn()
    except Exception as exc:  # 런북 실패는 결과값으로 표현한다
        logger.exception("[agent-team] 런북 실패: %s", name)
        return {"ok": False, "summary": f"{name} 실행 중 예외: {type(exc).__name__}: {exc}"[:300], "detail": {}}
    result.setdefault("detail", {})
    return result
```

- [ ] **Step 4: 통과 확인** — 6 passed
- [ ] **Step 5: 커밋**

```bash
git add agents/agent_tasks/runbooks.py tests/test_agent_runbooks.py
git commit -m "feat(agent-team): 복구 런북 3종 (약가 캐치업·발송 판정·주요 뉴스)"
```

---

### Task 4: 운영 신호 → 과제 변환

**Files:**
- Create: `agents/agent_tasks/signals.py`
- Test: `tests/test_agent_signals.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_agent_signals.py
from __future__ import annotations

from agents.agent_tasks.signals import record_health_problems, record_job_failure
from agents.agent_tasks.store import get_task, list_tasks


def test_job_failure_creates_task_with_runbook_hint(tmp_path):
    db = tmp_path / "a.db"
    tid = record_job_failure("domestic_price_pipeline", "HIRA 게시판 로딩 실패", db_path=db)
    t = get_task(tid, db_path=db)
    assert t["kind"] == "ops_incident" and t["source"] == "scheduler_job"
    assert t["source_ref"].startswith("domestic_price_pipeline:")
    assert "domestic_price_catchup" in t["detail"]      # 런북 힌트
    assert t["priority"] == 2


def test_repeated_failure_same_day_does_not_duplicate(tmp_path):
    db = tmp_path / "a.db"
    a = record_job_failure("domestic_price_pipeline", "오류1", db_path=db)
    b = record_job_failure("domestic_price_pipeline", "오류2", db_path=db)
    assert a == b
    assert len(list_tasks(db_path=db)) == 1


def test_health_problems_create_one_task_per_subscription(tmp_path):
    db = tmp_path / "a.db"
    report = {"run_date": "2026-09-30", "ok": False, "problems": [
        {"subscription_id": 14, "name": "약가 정책 모니터링", "problem": "not_generated"},
        {"subscription_id": 15, "name": "새 메일링 설정", "problem": "held", "detail": "llm_unavailable"},
        {"subscription_id": None, "name": "-", "problem": "gmail_token_invalid"}]}
    ids = record_health_problems(report, db_path=db)
    assert len(ids) == 3
    tasks = {t["source_ref"]: t for t in list_tasks(db_path=db)}
    assert "daily_mailing:2026-09-30:14:not_generated" in tasks
    assert tasks["daily_mailing:2026-09-30:14:not_generated"]["detail"].count("daily_mailing_dispatch") == 1
    gmail = tasks["daily_mailing:2026-09-30:gmail_token_invalid"]
    assert gmail["priority"] == 1 and "런북 없음" in gmail["detail"]


def test_healthy_report_creates_nothing(tmp_path):
    db = tmp_path / "a.db"
    assert record_health_problems({"run_date": "2026-09-30", "ok": True, "problems": []}, db_path=db) == []
    assert list_tasks(db_path=db) == []
```

- [ ] **Step 2: 실패 확인** → FAIL
- [ ] **Step 3: 구현**

```python
# agents/agent_tasks/signals.py
"""운영 신호 → 작업 원장 과제 (설계 §5).

같은 신호가 하루에 여러 번 와도 과제는 하나다(source_ref 에 날짜를 넣고 store 가 중복을 막는다).
어떤 런북으로 고칠 수 있는지 detail 에 적어 둔다 — 디스패처가 이 힌트로 런북을 고른다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import DEFAULT_DB_PATH, create_task

KST = timezone(timedelta(hours=9))

# 신호 → 런북 매핑 (없으면 사람이 봐야 하는 과제)
JOB_RUNBOOK = {
    "domestic_price_pipeline": "domestic_price_catchup",
    "daily_mailing_dispatch": "daily_mailing_dispatch",
    "daily_mailing_top_news": "daily_mailing_top_news",
}
HEALTH_RUNBOOK = {
    "not_generated": "daily_mailing_dispatch",
    "send_failed": "daily_mailing_dispatch",
    "ready_to_send": "daily_mailing_dispatch",
}
HIGH_PRIORITY_PROBLEMS = {"gmail_token_invalid", "error"}


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def _runbook_line(name: str | None) -> str:
    return f"런북: {name}" if name else "런북 없음 — 사람 확인 필요"


def record_job_failure(job_id: str, error: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    runbook = JOB_RUNBOOK.get(job_id)
    return create_task(
        kind="ops_incident", source="scheduler_job", source_ref=f"{job_id}:{_today()}",
        title=f"스케줄러 잡 실패 — {job_id}",
        detail=f"{error}\n{_runbook_line(runbook)}", priority=2,
        actor="scheduler", db_path=db_path)


def record_health_problems(report: dict, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    run_date = report.get("run_date") or _today()
    ids: list[int] = []
    for problem in report.get("problems") or []:
        code = str(problem.get("problem") or "unknown")
        sub_id = problem.get("subscription_id")
        ref_tail = f"{sub_id}:{code}" if sub_id is not None else code
        runbook = HEALTH_RUNBOOK.get(code)
        detail = [f"구독: {problem.get('name') or '-'} ({sub_id})", f"문제: {code}"]
        if problem.get("detail"):
            detail.append(f"상세: {problem['detail']}")
        detail.append(_runbook_line(runbook))
        ids.append(create_task(
            kind="ops_incident", source="daily_mailing_health",
            source_ref=f"daily_mailing:{run_date}:{ref_tail}",
            title=f"Daily Mailing {code} — 구독 {sub_id if sub_id is not None else '-'}",
            detail="\n".join(detail),
            priority=1 if code in HIGH_PRIORITY_PROBLEMS else 2,
            actor="daily_mailing_health", db_path=db_path))
    return ids
```

- [ ] **Step 4: 통과 확인** — 4 passed
- [ ] **Step 5: 커밋**

```bash
git add agents/agent_tasks/signals.py tests/test_agent_signals.py
git commit -m "feat(agent-team): 운영 신호를 과제로 전환 (잡 실패·헬스 문제, 중복 억제)"
```

---

### Task 5: 스케줄러가 신호를 남기게 한다

**Files:**
- Modify: `scheduler.py` (`daily_mailing_health_job`, `job` 의 국내 약가 실패 분기)
- Test: `tests/test_agent_signal_wiring.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_agent_signal_wiring.py
from __future__ import annotations

import scheduler as sch


def test_health_job_records_problems(monkeypatch, tmp_path):
    recorded = {}
    monkeypatch.setattr(sch, "_agent_record_health_problems",
                        lambda report: recorded.setdefault("report", report))

    class FakeDeps:
        @classmethod
        def production(cls):
            return cls()

    monkeypatch.setattr("agents.daily_mailing.dispatch.DispatchDeps", FakeDeps)
    monkeypatch.setattr("agents.daily_mailing.dispatch.health_check",
                        lambda deps: {"ok": False, "run_date": "2026-09-30",
                                      "problems": [{"subscription_id": 14, "name": "n", "problem": "held"}]})
    sch.daily_mailing_health_job()
    assert recorded["report"]["problems"][0]["problem"] == "held"


def test_health_job_ok_records_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(sch, "_agent_record_health_problems", lambda report: called.append(report))

    class FakeDeps:
        @classmethod
        def production(cls):
            return cls()

    monkeypatch.setattr("agents.daily_mailing.dispatch.DispatchDeps", FakeDeps)
    monkeypatch.setattr("agents.daily_mailing.dispatch.health_check",
                        lambda deps: {"ok": True, "run_date": "2026-09-30", "problems": []})
    sch.daily_mailing_health_job()
    assert called == []


def test_signal_failure_does_not_break_the_job(monkeypatch):
    def boom(report):
        raise RuntimeError("DB 잠김")

    monkeypatch.setattr(sch, "_agent_record_health_problems", boom)

    class FakeDeps:
        @classmethod
        def production(cls):
            return cls()

    monkeypatch.setattr("agents.daily_mailing.dispatch.DispatchDeps", FakeDeps)
    monkeypatch.setattr("agents.daily_mailing.dispatch.health_check",
                        lambda deps: {"ok": False, "run_date": "2026-09-30",
                                      "problems": [{"subscription_id": 14, "name": "n", "problem": "held"}]})
    sch.daily_mailing_health_job()          # 예외가 밖으로 나오면 실패
```

- [ ] **Step 2: 실패 확인** → FAIL (`AttributeError: module 'scheduler' has no attribute '_agent_record_health_problems'`)
- [ ] **Step 3: 구현**

`scheduler.py` 에 헬퍼를 추가한다(파일 상단 import 블록 아래, 잡 함수들보다 위):

```python
def _agent_record_health_problems(report: dict) -> None:
    """헬스 문제를 에이전트 작업 원장에 남긴다 (실패해도 잡을 막지 않는다)."""
    from agents.agent_tasks.signals import record_health_problems
    record_health_problems(report)


def _agent_record_job_failure(job_id: str, error: str) -> None:
    from agents.agent_tasks.signals import record_job_failure
    record_job_failure(job_id, error)
```

`daily_mailing_health_job` 의 `else:` 분기(문제가 있을 때)에 다음을 추가한다:

```python
            try:
                _agent_record_health_problems(res)
            except Exception as signal_err:
                logger.warning("daily mailing health 신호 기록 실패: %s", signal_err)
```

국내 약가 파이프라인의 실패 분기(`logger.error("국내 약가 catch-up 실패 …")` 바로 아래)에 추가한다:

```python
            try:
                _agent_record_job_failure("domestic_price_pipeline", "; ".join(result["errors"])[:300])
            except Exception as signal_err:
                logger.warning("국내 약가 실패 신호 기록 실패: %s", signal_err)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_agent_signal_wiring.py tests/test_dm_scheduler_jobs.py -q`
Expected: 모두 통과

- [ ] **Step 5: 커밋**

```bash
git add scheduler.py tests/test_agent_signal_wiring.py
git commit -m "feat(agent-team): 스케줄러 실패·헬스 문제를 작업 원장 신호로 기록"
```

---

### Task 6: 역할 인증 일반화 + 에이전트 API

**Files:**
- Modify: `api/auth.py` (`require_auth`)
- Modify: `api/server.py` (에이전트·관리자 엔드포인트)
- Test: `tests/test_agent_api.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_agent_api.py
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s
from agents.agent_tasks import store
from api.auth import _issue_token


def _h(role: str, email: str = "agent@msd.com"):
    return {"Authorization": f"Bearer {_issue_token(email, role)}"}


def _patch_db(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setattr(s, "_AGENT_TASK_DB_PATH", db)
    return db


def test_agent_role_required(monkeypatch, tmp_path):
    _patch_db(monkeypatch, tmp_path)
    c = s.app.test_client()
    assert c.get("/api/agent/tasks").status_code == 401
    assert c.get("/api/agent/tasks", headers=_h("user")).status_code == 403
    assert c.get("/api/agent/tasks", headers=_h("agent")).status_code == 200
    assert c.get("/api/agent/tasks", headers=_h("admin")).status_code == 200   # admin 은 상위 권한


def test_claim_and_resolve_flow(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    tid = store.create_task(kind="ops_incident", source="scheduler_job", source_ref="j:1",
                            title="잡 실패", detail="런북: daily_mailing_dispatch", db_path=db)
    store.update_task(tid, db_path=db, status="triaged", actor="test")
    c = s.app.test_client()
    listed = c.get("/api/agent/tasks?status=triaged", headers=_h("agent")).get_json()
    assert [t["id"] for t in listed["tasks"]] == [tid]

    ok = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent")).get_json()
    assert ok["claimed"] is True
    again = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    assert again.status_code == 409

    ran = c.post(f"/api/agent/tasks/{tid}/runbook", headers=_h("agent"),
                 json={"name": "daily_mailing_dispatch"})
    assert ran.status_code == 200 and "result" in ran.get_json()

    done = c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("agent"),
                  json={"status": "closed", "resolution": "재실행으로 복구"})
    assert done.status_code == 200
    assert store.get_task(tid, db_path=db)["status"] == "closed"


def test_runbook_unknown_name_is_400(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    tid = store.create_task(kind="ops_incident", source="x", source_ref="y", title="t", db_path=db)
    store.update_task(tid, db_path=db, status="triaged", actor="t")
    c = s.app.test_client()
    c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    r = c.post(f"/api/agent/tasks/{tid}/runbook", headers=_h("agent"), json={"name": "nope"})
    assert r.status_code == 400


def test_policy_blocks_claim(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    from agents.agent_tasks.policy import set_policy
    set_policy({"enabled": False}, actor="admin@msd.com", db_path=db)
    tid = store.create_task(kind="ops_incident", source="x", source_ref="y", title="t", db_path=db)
    store.update_task(tid, db_path=db, status="triaged", actor="t")
    c = s.app.test_client()
    r = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    assert r.status_code == 423 and r.get_json()["reason"] == "disabled"


def test_admin_ledger_endpoint(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    tid = store.create_task(kind="ops_incident", source="x", source_ref="y", title="t", db_path=db)
    c = s.app.test_client()
    assert c.get("/api/admin/agent-tasks", headers=_h("user")).status_code == 403
    data = c.get("/api/admin/agent-tasks", headers=_h("admin", "admin@msd.com")).get_json()
    assert data["policy"]["enabled"] is True
    assert [t["id"] for t in data["tasks"]] == [tid]
    detail = c.get(f"/api/admin/agent-tasks/{tid}", headers=_h("admin", "admin@msd.com")).get_json()
    assert [e["event"] for e in detail["events"]] == ["created"]
```

- [ ] **Step 2: 실패 확인** → FAIL (404/AttributeError)
- [ ] **Step 3: 구현 — 역할 일반화**

`api/auth.py` 의 `require_auth` 안에서 admin 전용 검사를 바꾼다:

```python
            if role and payload.get("role") not in (role, "admin"):
                return jsonify({"error": f"{role} required", "code": "AUTH_FORBIDDEN"}), 403
```

- [ ] **Step 4: 구현 — 엔드포인트**

`api/server.py` 의 Daily Mailing 관리자 엔드포인트 아래에 추가한다:

```python
# ──────────────────────────────────────────────────────────────────────────────
# 에이전트 팀 — 작업 원장 (설계: docs/superpowers/specs/2026-09-30-agent-team-full-service-design.md)
#   /api/agent/*  : VPS 디스패처(role='agent') 전용. 원장은 API 로만 수정한다.
#   /api/admin/agent-tasks : 운영 원장 화면(admin).
# ──────────────────────────────────────────────────────────────────────────────
from agents.agent_tasks import policy as _agent_policy  # noqa: E402
from agents.agent_tasks import runbooks as _agent_runbooks  # noqa: E402
from agents.agent_tasks import store as _agent_store  # noqa: E402

_AGENT_TASK_DB_PATH = _agent_store.DEFAULT_DB_PATH


@app.get("/api/agent/tasks")
@require_auth(role="agent")
def agent_tasks_list():
    statuses = tuple(s for s in (request.args.get("status") or "").split(",") if s) or _agent_store.OPEN_STATUSES
    try:
        tasks = _agent_store.list_tasks(db_path=_AGENT_TASK_DB_PATH, statuses=statuses,
                                        limit=min(int(request.args.get("limit", 50)), 200))
        return jsonify({"tasks": tasks, "policy": _agent_policy.get_policy(db_path=_AGENT_TASK_DB_PATH)})
    except Exception as e:
        logger.error("agent_tasks_list 실패: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.post("/api/agent/tasks/<int:task_id>/claim")
@require_auth(role="agent")
def agent_task_claim(task_id: int):
    actor = request.user["sub"]  # type: ignore[attr-defined]
    allowed, reason = _agent_policy.spend_check(db_path=_AGENT_TASK_DB_PATH)
    if not allowed:
        return jsonify({"claimed": False, "reason": reason, "code": "POLICY_BLOCKED"}), 423
    if not _agent_store.claim_task(task_id, actor=actor, db_path=_AGENT_TASK_DB_PATH):
        return jsonify({"claimed": False, "code": "NOT_CLAIMABLE"}), 409
    return jsonify({"claimed": True, "task": _agent_store.get_task(task_id, db_path=_AGENT_TASK_DB_PATH)})


@app.post("/api/agent/tasks/<int:task_id>/runbook")
@require_auth(role="agent")
def agent_task_runbook(task_id: int):
    actor = request.user["sub"]  # type: ignore[attr-defined]
    name = (request.get_json(silent=True) or {}).get("name") or ""
    task = _agent_store.get_task(task_id, db_path=_AGENT_TASK_DB_PATH)
    if task is None:
        return jsonify({"error": "not found", "code": "NOT_FOUND"}), 404
    if name not in _agent_runbooks.list_runbooks():
        return jsonify({"error": f"알 수 없는 런북: {name}", "code": "INVALID"}), 400
    _agent_store.add_event(task_id, actor=actor, event="runbook_started", detail=name,
                           db_path=_AGENT_TASK_DB_PATH)
    result = _agent_runbooks.run(name)
    _agent_store.add_event(task_id, actor=actor,
                           event="runbook_ok" if result["ok"] else "runbook_failed",
                           detail=result["summary"], db_path=_AGENT_TASK_DB_PATH)
    return jsonify({"result": result})


@app.post("/api/agent/tasks/<int:task_id>/resolve")
@require_auth(role="agent")
def agent_task_resolve(task_id: int):
    actor = request.user["sub"]  # type: ignore[attr-defined]
    body = request.get_json(silent=True) or {}
    status = body.get("status") or "closed"
    if status not in ("closed", "blocked", "rejected", "triaged"):
        return jsonify({"error": "status must be closed|blocked|rejected|triaged", "code": "INVALID"}), 400
    try:
        task = _agent_store.update_task(task_id, actor=actor, db_path=_AGENT_TASK_DB_PATH,
                                        status=status, resolution=str(body.get("resolution") or "")[:2000])
        return jsonify({"task": task})
    except _agent_store.AgentTaskError as e:
        return jsonify({"error": str(e), "code": "INVALID_STATE"}), 409


@app.get("/api/admin/agent-tasks")
@require_auth(role="admin")
def admin_agent_tasks():
    statuses = tuple(s for s in (request.args.get("status") or "").split(",") if s) or \
        _agent_store.OPEN_STATUSES + ("closed", "rejected")
    return jsonify({
        "tasks": _agent_store.list_tasks(db_path=_AGENT_TASK_DB_PATH, statuses=statuses, limit=200),
        "policy": _agent_policy.get_policy(db_path=_AGENT_TASK_DB_PATH),
        "runbooks": _agent_runbooks.list_runbooks(),
    })


@app.get("/api/admin/agent-tasks/<int:task_id>")
@require_auth(role="admin")
def admin_agent_task_detail(task_id: int):
    task = _agent_store.get_task(task_id, db_path=_AGENT_TASK_DB_PATH)
    if task is None:
        return jsonify({"error": "not found", "code": "NOT_FOUND"}), 404
    return jsonify({"task": task, "events": _agent_store.list_events(task_id, db_path=_AGENT_TASK_DB_PATH)})


@app.post("/api/admin/agent-policy")
@require_auth(role="admin")
def admin_agent_policy():
    body = request.get_json(silent=True) or {}
    actor = request.user["sub"]  # type: ignore[attr-defined]
    return jsonify({"policy": _agent_policy.set_policy(body, actor=actor, db_path=_AGENT_TASK_DB_PATH)})
```

> `_AGENT_TASK_DB_PATH` 를 모듈 변수로 둔 이유: 테스트가 임시 DB 로 바꿔치기할 수 있어야 한다.
> 위 엔드포인트들은 `db_path=_AGENT_TASK_DB_PATH` 를 **호출 시점에** 읽으므로 monkeypatch 가 먹는다.

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_agent_api.py tests/test_dm_api.py -q`
Expected: 모두 통과

- [ ] **Step 6: 커밋**

```bash
git add api/auth.py api/server.py tests/test_agent_api.py
git commit -m "feat(agent-team): 에이전트 역할 인증 + 작업 원장 API(조회·선점·런북·회신·정책)"
```

---

### Task 7: VPS 디스패처

**Files:**
- Create: `scripts/agent_dispatcher.py`
- Test: `tests/test_agent_dispatcher.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_agent_dispatcher.py
from __future__ import annotations

import json

from scripts.agent_dispatcher import Dispatcher, pick_runbook


class FakeClient:
    """API 호출을 기록하는 가짜 클라이언트."""

    def __init__(self, tasks, runbook_result=None, claim_ok=True):
        self.tasks = tasks
        self.runbook_result = runbook_result or {"ok": True, "summary": "복구 완료"}
        self.claim_ok = claim_ok
        self.calls = []

    def list_tasks(self, status):
        self.calls.append(("list", status))
        return self.tasks

    def claim(self, task_id):
        self.calls.append(("claim", task_id))
        return self.claim_ok

    def run_runbook(self, task_id, name):
        self.calls.append(("runbook", task_id, name))
        return self.runbook_result

    def resolve(self, task_id, status, resolution):
        self.calls.append(("resolve", task_id, status, resolution))


def _task(tid=1, detail="런북: daily_mailing_dispatch", priority=2):
    return {"id": tid, "title": "Daily Mailing not_generated", "detail": detail,
            "priority": priority, "status": "triaged", "kind": "ops_incident"}


def test_pick_runbook_reads_hint():
    assert pick_runbook(_task()) == "daily_mailing_dispatch"
    assert pick_runbook(_task(detail="런북 없음 — 사람 확인 필요")) is None


def test_runs_runbook_and_closes(tmp_path):
    client = FakeClient([_task()])
    out = Dispatcher(client).run_once()
    assert client.calls == [("list", "triaged"), ("claim", 1),
                            ("runbook", 1, "daily_mailing_dispatch"),
                            ("resolve", 1, "closed", "복구 완료")]
    assert out["handled"] == 1


def test_runbook_failure_blocks_task():
    client = FakeClient([_task()], runbook_result={"ok": False, "summary": "여전히 실패"})
    Dispatcher(client).run_once()
    assert client.calls[-1] == ("resolve", 1, "blocked", "여전히 실패")


def test_task_without_runbook_is_blocked_for_human():
    client = FakeClient([_task(detail="런북 없음 — 사람 확인 필요")])
    Dispatcher(client).run_once()
    assert ("runbook", 1, None) not in client.calls
    assert client.calls[-1][0:2] == ("resolve", 1) and client.calls[-1][2] == "blocked"


def test_lost_claim_is_skipped():
    client = FakeClient([_task()], claim_ok=False)
    out = Dispatcher(client).run_once()
    assert out["handled"] == 0 and ("runbook", 1, "daily_mailing_dispatch") not in client.calls


def test_dry_run_does_not_mutate():
    client = FakeClient([_task()])
    out = Dispatcher(client, dry_run=True).run_once()
    assert client.calls == [("list", "triaged")]
    assert out["planned"] == [{"task_id": 1, "runbook": "daily_mailing_dispatch"}]


def test_priority_order_and_limit():
    client = FakeClient([_task(1, priority=3), _task(2, priority=1)])
    Dispatcher(client, max_tasks=1).run_once()
    assert ("claim", 2) in client.calls and ("claim", 1) not in client.calls
```

- [ ] **Step 2: 실패 확인** → FAIL (`ModuleNotFoundError: scripts.agent_dispatcher`)
- [ ] **Step 3: 구현**

```python
# scripts/agent_dispatcher.py
"""VPS 디스패처 — 열린 과제를 집어 프로덕션 런북으로 복구한다 (P1: LLM 없음).

크론 예: */10 * * * * cd /srv/ma-dossier && ./venv/bin/python scripts/agent_dispatcher.py >> logs/agent.log 2>&1

환경변수: MA_API_BASE(예: https://ma-ai-dossier.fly.dev), MA_AGENT_EMAIL, MA_AGENT_PASSWORD.
로그인은 매 실행마다 한다(7일 JWT 를 보관하지 않는다).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("agent_dispatcher")
RUNBOOK_HINT = re.compile(r"런북:\s*([a-z_]+)")
DEFAULT_MAX_TASKS = 3


def pick_runbook(task: dict) -> str | None:
    """과제 detail 의 '런북: <이름>' 힌트를 읽는다. 없으면 None(사람 확인)."""
    match = RUNBOOK_HINT.search(str(task.get("detail") or ""))
    return match.group(1) if match else None


class ApiClient:
    def __init__(self, base: str, email: str, password: str, *, opener=urllib.request.urlopen):
        self.base = base.rstrip("/")
        self._opener = opener
        self._token = self._login(email, password)

    def _request(self, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token or getattr(self, "_token", None):
            req.add_header("Authorization", f"Bearer {token or self._token}")
        with self._opener(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    def _login(self, email: str, password: str) -> str:
        return self._request("POST", "/api/auth/login", {"email": email, "password": password})["token"]

    def list_tasks(self, status: str) -> list[dict]:
        return self._request("GET", f"/api/agent/tasks?status={status}")["tasks"]

    def claim(self, task_id: int) -> bool:
        try:
            return bool(self._request("POST", f"/api/agent/tasks/{task_id}/claim")["claimed"])
        except urllib.error.HTTPError as exc:
            if exc.code in (409, 423):     # 이미 선점됐거나 정책이 막음
                return False
            raise

    def run_runbook(self, task_id: int, name: str) -> dict:
        return self._request("POST", f"/api/agent/tasks/{task_id}/runbook", {"name": name})["result"]

    def resolve(self, task_id: int, status: str, resolution: str) -> None:
        self._request("POST", f"/api/agent/tasks/{task_id}/resolve",
                      {"status": status, "resolution": resolution})


class Dispatcher:
    def __init__(self, client, *, max_tasks: int = DEFAULT_MAX_TASKS, dry_run: bool = False):
        self.client = client
        self.max_tasks = max_tasks
        self.dry_run = dry_run

    def run_once(self) -> dict:
        tasks = sorted(self.client.list_tasks("triaged"), key=lambda t: (t.get("priority", 3), t["id"]))
        planned: list[dict] = []
        handled = 0
        for task in tasks[: self.max_tasks]:
            runbook = pick_runbook(task)
            planned.append({"task_id": task["id"], "runbook": runbook})
            if self.dry_run:
                continue
            if not self.client.claim(task["id"]):
                logger.info("선점 실패(다른 실행이 가져감): %s", task["id"])
                continue
            if runbook is None:
                self.client.resolve(task["id"], "blocked", "자동 복구 런북 없음 — 사람 확인 필요")
                continue
            result = self.client.run_runbook(task["id"], runbook)
            self.client.resolve(task["id"], "closed" if result["ok"] else "blocked", result["summary"])
            handled += 1
        return {"planned": planned, "handled": handled, "candidates": len(tasks)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="에이전트 팀 디스패처 (P1 운영 감시자)")
    parser.add_argument("--dry-run", action="store_true", help="선점/실행 없이 계획만 출력")
    parser.add_argument("--max-tasks", type=int, default=DEFAULT_MAX_TASKS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    base = os.environ.get("MA_API_BASE", "").strip()
    email = os.environ.get("MA_AGENT_EMAIL", "").strip()
    password = os.environ.get("MA_AGENT_PASSWORD", "").strip()
    if not (base and email and password):
        logger.error("MA_API_BASE·MA_AGENT_EMAIL·MA_AGENT_PASSWORD 환경변수가 필요합니다")
        return 2
    out = Dispatcher(ApiClient(base, email, password), max_tasks=args.max_tasks, dry_run=args.dry_run).run_once()
    logger.info("dispatch: %s", json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_agent_dispatcher.py -q` → 7 passed
- [ ] **Step 5: 커밋**

```bash
git add scripts/agent_dispatcher.py tests/test_agent_dispatcher.py
git commit -m "feat(agent-team): VPS 디스패처 — 선점·런북 실행·회신, dry-run"
```

---

### Task 8: 운영 원장 화면

**Files:**
- Create: `frontend/src/api/agentTasks.ts`, `frontend/src/pages/admin/agent-tasks/page.tsx`
- Modify: 라우터(`frontend/src/App.tsx` 의 admin 라우트 목록), `frontend/src/components/feature/Sidebar.tsx` 의 `adminNavItems`

- [ ] **Step 1: API 클라이언트**

```ts
// frontend/src/api/agentTasks.ts
import { api } from './client';

export interface AgentTask {
  id: number;
  kind: string;
  source: string;
  source_ref: string | null;
  title: string;
  detail: string;
  priority: number;
  risk: string | null;
  status: string;
  claimed_by: string | null;
  claimed_at: string | null;
  branch: string | null;
  commit_ref: string | null;
  deploy_version: string | null;
  attempts: number;
  cost_usd: number;
  resolution: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentPolicy {
  enabled: boolean;
  max_tasks_per_day: number;
  max_cost_usd_per_day: number;
  max_deploys_per_day: number;
}

export interface AgentTaskEvent {
  id: number;
  task_id: number;
  actor: string;
  event: string;
  detail: string | null;
  created_at: string;
}

export async function fetchAgentTasks(): Promise<{ tasks: AgentTask[]; policy: AgentPolicy; runbooks: string[] }> {
  return api.get('/api/admin/agent-tasks');
}

export async function fetchAgentTaskDetail(id: number): Promise<{ task: AgentTask; events: AgentTaskEvent[] }> {
  return api.get(`/api/admin/agent-tasks/${id}`);
}

export async function updateAgentPolicy(patch: Partial<AgentPolicy>): Promise<{ policy: AgentPolicy }> {
  return api.post('/api/admin/agent-policy', patch);
}
```

- [ ] **Step 2: 화면**

`frontend/src/pages/admin/agent-tasks/page.tsx` 를 `frontend/src/pages/admin/daily-mailing-kanban/page.tsx` 와 같은 골격으로 만든다(관리자 가드 `fetchMe().role === 'admin'` → 아니면 `/` 로 이동, 로딩·에러·새로고침 버튼 동일). 화면 구성:

1. 상단 정책 카드: `enabled` 토글(끄면 빨간 "에이전트 팀 정지 중" 배지), 일일 상한 3종 표시, 토글 시 `updateAgentPolicy({ enabled: !policy.enabled })` 후 재조회.
2. 과제 테이블: 우선순위·종류·제목·상태·담당·시도·비용·근거(`source`/`source_ref`)·최근 갱신. 상태별 색은 `daily-mailing-kanban` 의 `badgeTone` 규칙을 그대로 쓴다(`closed`=teal, `blocked`/`rejected`=red, `in_progress`=amber, 그 외 blue).
3. 행 클릭 시 오른쪽 패널에 `fetchAgentTaskDetail` 결과 — `detail` 전문과 감사 로그(시각·행위자·이벤트·상세)를 시간순으로.

- [ ] **Step 3: 라우팅·메뉴 연결**

`App.tsx` 의 admin 라우트 목록에 `{ path: '/admin/agent-tasks', element: <AgentTasksPage /> }` 를 추가하고, `Sidebar.tsx` 의 `adminNavItems` 에 `{ label: '운영 원장', path: '/admin/agent-tasks', icon: 'ri-robot-line' }` 를 추가한다(기존 항목 형식 그대로).

- [ ] **Step 4: 검증**

```bash
cd frontend && npm run type-check && npm run build
```
Expected: 둘 다 성공, 새 경고 없음.

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/api/agentTasks.ts frontend/src/pages/admin/agent-tasks frontend/src/App.tsx frontend/src/components/feature/Sidebar.tsx
git commit -m "feat(agent-team): 운영 원장 화면 — 과제·감사 로그·정책 토글"
```

---

### Task 9: VPS 운영 문서와 계정 발급

**Files:**
- Create: `docs/agent_team/VPS_SETUP.md`

- [ ] **Step 1: 문서 작성**

다음 내용을 담는다(명령은 그대로 복사해 쓸 수 있게).

1. **에이전트 계정 발급**: 관리자로 로그인해 `POST /api/admin/users` 로 `{"email": "agent@msd.com", "password": "<강한 난수>", "role": "agent"}` 생성. 비밀번호는 VPS `.env` 에만 둔다.
2. **VPS 준비**: 저장소 클론, `python -m venv venv`, `pip install -r requirements.txt`(디스패처는 표준 라이브러리만 쓰므로 최소 설치로 충분), `.env` 에 `MA_API_BASE`·`MA_AGENT_EMAIL`·`MA_AGENT_PASSWORD`.
3. **크론**: `*/10 * * * * cd /srv/ma-dossier && set -a && . ./.env && set +a && ./venv/bin/python scripts/agent_dispatcher.py >> logs/agent.log 2>&1`
4. **첫 실행은 `--dry-run`** 으로 계획만 확인한다.
5. **정지 방법**: 운영 원장 화면의 정책 토글(`enabled=false`) 또는 크론 주석 처리. 정지해도 프로덕션 스케줄러(수집·발송)는 계속 돈다.
6. **문제 시 확인 순서**: 운영 원장 화면 → `logs/agent.log` → 프로덕션 `flyctl logs`.

- [ ] **Step 2: 커밋**

```bash
git add docs/agent_team/VPS_SETUP.md
git commit -m "docs(agent-team): VPS 디스패처 설치·운영 절차"
```

---

### Task 10: 회귀

- [ ] **Step 1: 전체 테스트**

```bash
.venv/bin/python -m pytest tests/ -q
```
Expected: 기존 통과 수 + 이번 신규(약 27개). `tests/test_access_insight_link.py::test_classify_gov_statement_via_kind_and_agency_keyword` 는 이 저장소에서 이미 실패하는 환경 의존 테스트다(로컬 DB 의 분류 사전 시딩 때문). 그 외 실패는 이번 작업의 회귀다.

- [ ] **Step 2: 임포트 스모크**

```bash
.venv/bin/python -c "import scheduler, api.server, scripts.agent_dispatcher; print('ok')"
```

- [ ] **Step 3: 통합 스모크(선택)**

임시 DB로 신호 → 과제 → 선점 → 런북(가짜) → 종료를 한 번 흘려보고, 운영 원장 API 가 그 과제를 돌려주는지 확인한다.

---

## P2~P4 계획 (작업 수준)

각 단계는 P1 의 원장·API·디스패처 위에 얹는다. 상세 계획은 직전 단계가 끝난 뒤 이 문서와 같은 형식으로 쓴다.

### P2 — 요청 처리자 (사용자 요청 자동 구현)

1. `service_request`(status=sent) → `agent_task(kind='user_request')` 동기화 모듈 + 역방향 상태 반영(`in_progress`/`done`/`wont_fix`).
2. 디스패처에 **LLM 실행 경로** 추가: 과제를 프롬프트로 만들어 `claude -p --allowedTools ... --permission-mode ...` 를 작업 저장소 사본에서 실행, 산출물은 브랜치 커밋.
3. **검증 에이전트 실행**(작성자와 다른 프로세스): 설계 준수 → 코드 품질 2단계, 결과를 `agent_task_event` 에 기록. 통과 시 `verified`.
4. 요청자 회신: `service_request.resolution_note`/`commit_ref` 갱신 → `/my-requests` 반영.
5. 안전: 요청 처리 중 금지 경로 변경 시 자동 `blocked`.

#### P1 리뷰에서 P2 로 넘김

- **운영 원장 화면의 상태 변경 버튼**(종료·`triaged` 되돌리기·`blocked`): P1 화면은 정책 토글만 있고, 상태 변경은 admin 토큰 + `POST /api/agent/tasks/<id>/resolve` 로만 한다(`docs/agent_team/VPS_SETUP.md` §6 운영 개입 절차).
- **런북 실행을 job 분리 + `202` 폴링으로**: P1 의 `POST /runbook` 은 동기(수 분)이고 디스패처가 600초를 기다리지만 프록시/워커가 그보다 먼저 끊지 않는다는 근거가 없다(`api/server.py` 의 "수 분짜리 동기 요청은 프록시 타임아웃에 끊긴다" 원칙). 끊기면 서버엔 `runbook_ok`, 과제는 디스패처 오류로 `blocked`.

### P3 — 개선자 (스스로 과제 만들기)

1. 수집기: QualityGuard 리포트·RuleCompliance 결과·Daily Mailing 통계(시사점 제외율·본문 확보율·클러스터 수)·발송 실패율을 매일 05:00 에 읽어 임계치 초과 시 과제 생성.
2. 개선 과제 실행은 P2 의 LLM 경로 재사용.
3. **주간 회고**(월 06:00): 지난주 과제 결과를 집계해 오탐·재발을 새 과제로.
4. 매일 아침 요약 메일 1통(Daily Mailing 발송 경로 재사용).

### P4 — 릴리스 관리자 (저위험 자동 배포)

1. `agents/release/risk.py` — git diff 기반 위험 분류기(설계 §6 규칙, 순수 함수 + 단위 테스트).
2. 배포 실행기: VPS 가 `flyctl deploy` 수행, 결과·버전을 과제에 기록.
3. 배포 후 검증(5분): 헬스·스케줄러 생존·에러 증가·오늘 run 상태 → 실패 시 직전 릴리스로 롤백.
4. 승인 대기 UI: 운영 원장 화면에서 `awaiting_approval` 과제 승인/반려.
5. 일일 배포 상한(`max_deploys_per_day`)과 정지 스위치 연동.

---

## 자체 점검

- **스펙 커버리지**: §3 아키텍처 → Task 6·7, §5 원장·상태기계·선점·감사 → Task 1, 예산·정지 → Task 2, 런북 → Task 3, 신호 수집 → Task 4·5, §7 운영 원장 화면 → Task 8, §8 안전장치 → Task 2(+금지 행위는 P2 LLM 경로에서 강제), §9 P1 완료 기준 → Task 10, VPS 절차 → Task 9.
- **플레이스홀더 없음**: 모든 단계에 실제 코드·명령·기대 결과를 넣었다.
- **이름 일관성**: `create_task/get_task/list_tasks/update_task/claim_task/add_event/list_events`(store), `record_job_failure/record_health_problems`(signals), `list_runbooks/run`(runbooks), `get_policy/set_policy/spend_check`(policy), `Dispatcher/ApiClient/pick_runbook`(dispatcher) — 계획 전체에서 동일하게 쓰였다.
