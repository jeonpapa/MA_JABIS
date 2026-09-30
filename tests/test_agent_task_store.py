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
    # 설계 §5 상태 기계: new 에서 closed 로 바로 갈 수 없다 — 정상 경로로 닫는다
    update_task(first, db_path=db, status="triaged", actor="sentinel")
    update_task(first, db_path=db, status="in_progress", actor="vps-1")
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
