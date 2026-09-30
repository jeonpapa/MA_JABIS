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
    assert claim_task(tid, actor="vps-1", db_path=db) == "claimed"
    assert claim_task(tid, actor="vps-2", db_path=db) == "not_claimable"
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


def test_triage_task_is_conditional_and_records_events_once(tmp_path):
    from agents.agent_tasks.store import triage_task

    db = tmp_path / "a.db"
    tid = _new(db)
    assert triage_task(tid, actor="vps-1", detail="자동 분류", db_path=db) is True
    assert triage_task(tid, actor="vps-2", detail="자동 분류", db_path=db) is False   # 이미 triaged
    assert get_task(tid, db_path=db)["status"] == "triaged"
    events = [(e["event"], e["actor"]) for e in list_events(tid, db_path=db)]
    assert events == [("created", "system"), ("auto_triaged", "vps-1"), ("status:triaged", "vps-1")]
    assert triage_task(999, actor="vps-1", db_path=db) is False                       # 없는 과제


# ── 재시도 경로 (설계 §5: 재시도 2회까지, 3번째 실패는 blocked + 사람) ──────────────

def test_returning_to_triaged_clears_claim_so_retry_can_claim(tmp_path):
    """blocked → triaged 되돌리기 후 재선점. claimed_by 가 안 비워지면 claim_task 가 영원히 False."""
    db = tmp_path / "a.db"
    tid = _new(db)
    update_task(tid, db_path=db, status="triaged", actor="sentinel")
    assert claim_task(tid, actor="vps-1", db_path=db) == "claimed"
    update_task(tid, db_path=db, status="blocked", actor="vps-1", resolution="런북 실패")
    update_task(tid, db_path=db, status="triaged", actor="admin")
    t = get_task(tid, db_path=db)
    assert t["claimed_by"] is None and t["claimed_at"] is None and t["attempts"] == 1   # attempts 보존
    assert claim_task(tid, actor="vps-2", db_path=db) == "claimed"
    t = get_task(tid, db_path=db)
    assert t["claimed_by"] == "vps-2" and t["attempts"] == 2


def _exhaust(db, tid, max_attempts):
    for n in range(1, max_attempts + 1):
        assert claim_task(tid, actor=f"vps-{n}", db_path=db) == "claimed"
        assert get_task(tid, db_path=db)["attempts"] == n
        update_task(tid, db_path=db, status="triaged", actor=f"vps-{n}")      # in_progress → triaged


def test_claim_refused_after_max_attempts_and_store_blocks_it_once(tmp_path):
    """설계 §5 의 재시도 한도(최초 1 + 재시도 2 = 3)를 고정하는 유일한 단언. 한도를 다 쓴 triaged 과제는
    store 가 스스로 blocked 로 넘기고(status:blocked 이벤트 1개) 'exhausted' 를 돌려준다. 그 뒤의 claim 은
    'not_claimable'(이미 blocked) 이고 이벤트는 더 늘지 않는다."""
    from agents.agent_tasks.store import MAX_ATTEMPTS

    assert MAX_ATTEMPTS == 3
    db = tmp_path / "a.db"
    tid = _new(db)
    update_task(tid, db_path=db, status="triaged", actor="sentinel")
    _exhaust(db, tid, MAX_ATTEMPTS)
    assert claim_task(tid, actor="vps-9", db_path=db) == "exhausted"          # 한도 초과 → store 가 blocked
    t = get_task(tid, db_path=db)
    assert t["status"] == "blocked" and t["attempts"] == MAX_ATTEMPTS and t["claimed_by"] is None
    assert t["resolution"] == f"재시도 한도({MAX_ATTEMPTS}회) 초과 — 사람 확인 필요"
    events = list_events(tid, db_path=db)
    assert [e["event"] for e in events].count("status:blocked") == 1
    assert events[-1]["event"] == "status:blocked" and events[-1]["actor"] == "vps-9"
    assert claim_task(tid, actor="vps-10", db_path=db) == "not_claimable"
    assert [e["event"] for e in list_events(tid, db_path=db)].count("status:blocked") == 1


def test_concurrent_claims_on_exhausted_task_block_it_exactly_once(tmp_path):
    """스레드 재현: 소진된 triaged 과제를 두 claim 이 동시에 친다 — status:blocked 이벤트는 정확히 1개,
    결과는 exhausted 1 + not_claimable 1. (조건부 UPDATE 1회 — 전이표에 기댄 우연한 안전이 아니다.)"""
    import threading
    from agents.agent_tasks.store import MAX_ATTEMPTS

    db = tmp_path / "a.db"
    tid = _new(db)
    update_task(tid, db_path=db, status="triaged", actor="sentinel")
    _exhaust(db, tid, MAX_ATTEMPTS)
    barrier = threading.Barrier(2, timeout=10)
    results: dict[str, str] = {}

    def worker(actor: str):
        barrier.wait()
        results[actor] = claim_task(tid, actor=actor, db_path=db)

    threads = [threading.Thread(target=worker, args=(a,)) for a in ("vps-a", "vps-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results.values()) == ["exhausted", "not_claimable"], results
    events = [e["event"] for e in list_events(tid, db_path=db)]
    assert events.count("status:blocked") == 1, events
    assert get_task(tid, db_path=db)["status"] == "blocked"


def test_list_tasks_order_recent_puts_latest_update_first(tmp_path):
    db = tmp_path / "a.db"
    a = _new(db)
    b = _new(db, source_ref="dm:2026-10-01", title="둘째", priority=1)
    c_ = _new(db, source_ref="dm:2026-10-02", title="셋째")
    update_task(a, db_path=db, detail="가장 최근에 손댐", actor="test")     # updated_at 갱신
    assert [t["id"] for t in list_tasks(db_path=db)] == [b, a, c_]                    # 기본: priority, id
    assert [t["id"] for t in list_tasks(db_path=db, order="recent")] == [a, c_, b]    # updated_at DESC, id DESC
    with pytest.raises(AgentTaskError):
        list_tasks(db_path=db, order="whatever")
