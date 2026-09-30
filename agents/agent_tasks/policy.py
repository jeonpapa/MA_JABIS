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
        # 오늘 착수한 작업 수 — 디스패처 선점(claimed)과 관리자/API 의 in_progress 전이 모두 한 번씩 센다
        claimed = conn.execute(
            "SELECT COUNT(*) FROM agent_task_event WHERE event IN ('claimed', 'status:in_progress')"
            " AND created_at >= ? AND created_at < ?",
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
