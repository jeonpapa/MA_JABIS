from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.agent_tasks.policy import DEFAULT_POLICY, get_policy, set_policy, spend_check
from agents.agent_tasks.store import create_task, update_task

KST = timezone(timedelta(hours=9))
# 감사 이벤트는 실제 시각으로 기록되므로, 예산 판정 기준 시각도 '지금'이어야 한다
# (고정 날짜를 쓰면 그날이 지나는 순간 테스트가 깨진다).
NOW = datetime.now(KST)


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
