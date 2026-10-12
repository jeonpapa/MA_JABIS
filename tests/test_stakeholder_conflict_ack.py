"""충돌별 확인 후 승인 (T2b, 사용자 결정 2026-10-12) + 직책 반영 실패 시 대기 해제.

- 감사 제안의 conflicts 는 대부분 검토 메모다(예: "pre-run context script 부재로 감사 상태 degraded"). 소스 규칙(비어 있지 않으면
  무조건 422)으로는 실제 6건 중 어느 것도 승인할 수 없었다 → admin 이 **충돌마다** 사유와 함께 확인(acknowledge)해야 승인 가능.
  확인 후에도 근거 불변식(E2/E3·공식 출처·도메인·중복·대기 직책 등)은 그대로.
- 확인 행은 (proposal_id, conflict_index) 당 1행 + 확인 당시 conflict_text 스냅숏. 지금 목록의 그 index 문구와 다르면 무효.
- apply_scheduled 가 role_change 를 application_failed 로 표시하면 그 직책 이력 행도 failed 로 — 이후 같은 인물 직책 변경이
  PENDING_ROLE_CHANGE 409 로 영구히 막히지 않게.
실제 DB 는 쓰지 않는다 — 전부 tmp_path.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from agents.stakeholder import approval as ap
from agents.stakeholder import store

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "stakeholder_src_mini"

E = lambda **kw: {"url": "https://www.hira.or.kr/x", "publisher": "심평원", "source_class": "official",  # noqa: E731
                  "evidence_level": "E3", "locator": "본문 1문단", **kw}
NEW = {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회", "role": "위원"}
C3 = ["pre-run context script 부재로 감사 상태 degraded", "두 매체는 같은 사건을 취재", "게시일은 창 시작 전"]


def _p(ptype="new_person", change=None, evidence=None, conflicts=(), status="needs_review", pid="AUD-T"):
    return {"proposal_id": pid, "proposal_type": ptype, "proposed_change": dict(NEW) if change is None else change,
            "evidence": [E()] if evidence is None else evidence, "conflicts": list(conflicts), "status": status}


def _ack(i, text, by="admin@x"):
    return {"proposal_id": "AUD-T", "conflict_index": i, "conflict_text": text, "reason": "확인함",
            "acknowledged_by": by, "acknowledged_at": "2026-10-12T09:00:00+09:00"}


class _Facts:
    def domain_exists(self, domain):
        return False

    def get_person(self, person_id):
        return None

    def has_pending_role_change(self, person_id):
        return False

    def person_exists(self, name, organization):
        return False


# ── 순수: conflicts_status / unacknowledged_conflicts / plan_approval ───────────────

def test_conflicts_status_matches_snapshot_exactly():
    p = _p(conflicts=C3)
    acks = [_ack(0, C3[0]), _ack(1, C3[1] + " (수정됨)"), _ack(7, "없는 index")]
    st = ap.conflicts_status(p, acks)
    assert [s["index"] for s in st] == [0, 1, 2] and [s["text"] for s in st] == C3
    assert st[0] == {"index": 0, "text": C3[0], "acknowledged": True, "stale": False, "by": "admin@x",
                     "at": "2026-10-12T09:00:00+09:00", "reason": "확인함"}
    # 문구가 바뀐 확인은 무효(stale) — 확인자 정보는 싣지 않는다
    assert st[1] == {"index": 1, "text": C3[1], "acknowledged": False, "stale": True, "by": None, "at": None,
                     "reason": None}
    assert st[2]["acknowledged"] is False and st[2]["stale"] is False
    assert ap.unacknowledged_conflicts(p, acks) == [1, 2]
    assert ap.conflicts_status(_p(), []) == [] and ap.unacknowledged_conflicts(_p(), None) == []


def test_plan_approval_requires_every_conflict_acknowledged():
    with pytest.raises(ap.ApprovalError) as ei:
        ap.plan_approval(_p(conflicts=C3), _Facts(), effective_from="d", ts="t", reviewer="r")
    e = ei.value
    assert e.status == 422 and e.code == "CONFLICTS_UNACKNOWLEDGED"
    assert "#0, #1, #2" in e.message and "충돌" in e.message
    assert e.details == {"unacknowledged": [0, 1, 2]}
    with pytest.raises(ap.ApprovalError) as ei:                                          # 일부만 확인
        ap.plan_approval(_p(conflicts=C3), _Facts(), effective_from="d", ts="t", reviewer="r",
                         acks=[_ack(0, C3[0]), _ack(2, C3[2])])
    assert ei.value.code == "CONFLICTS_UNACKNOWLEDGED" and ei.value.details == {"unacknowledged": [1]}
    assert "#1" in ei.value.message and "#0" not in ei.value.message and "#2" not in ei.value.message
    with pytest.raises(ap.ApprovalError) as ei:                                          # 스냅숏 불일치 = 미확인
        ap.plan_approval(_p(conflicts=C3), _Facts(), effective_from="d", ts="t", reviewer="r",
                         acks=[_ack(0, C3[0]), _ack(1, "바꿔치기"), _ack(2, C3[2])])
    assert ei.value.details == {"unacknowledged": [1]} and "무효" in ei.value.message
    plan = ap.plan_approval(_p(conflicts=C3), _Facts(), effective_from="d", ts="t", reviewer="r",
                            acks=[_ack(i, t) for i, t in enumerate(C3)])
    assert plan["kind"] == "person"


def test_acknowledged_conflicts_do_not_bypass_evidence_rules():
    """확인은 충돌 게이트만 연다 — 근거 불변식은 그대로 (E1 만 있는 신규 인물 → 422 EVIDENCE)."""
    with pytest.raises(ap.ApprovalError) as ei:
        ap.plan_approval(_p(evidence=[E(evidence_level="E1")], conflicts=C3[:1]), _Facts(), effective_from="d",
                         ts="t", reviewer="r", acks=[_ack(0, C3[0])])
    assert ei.value.status == 422 and ei.value.code == "EVIDENCE"


def test_state_checked_before_conflicts():
    with pytest.raises(ap.ApprovalError) as ei:
        ap.plan_approval(_p(conflicts=C3, status="scheduled"), _Facts(), effective_from="d", ts="t", reviewer="r")
    assert ei.value.status == 409 and ei.value.code == "STATE"


# ── DB ────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    p = tmp_path / "a.db"
    store.insert_person_if_absent({"person_id": "P-HIRA-KIM", "name": "김국희", "organization": "건강보험심사평가원",
        "organization_code": "HIRA", "department": "희귀·중증질환성과평가실", "role": "실장 후보", "status": "active",
        "verification_status": "candidate", "effective_from": "2026-10-09", "aliases": [], "topic_tags": [],
        "photo_url": None, "photo_path": None, "created_at": "t", "updated_at": "t"}, origin="import", db_path=p)
    return p


def _proposal(db, ptype="new_person", change=None, evidence=None, conflicts=C3, pid="AUD-1", status="needs_review"):
    store.insert_proposal_if_absent({"proposal_id": pid, "proposal_type": ptype, "dedupe_key": f"{ptype}:{pid}",
        "priority": "high", "summary": "s", "rationale": "r",
        "proposed_change": dict(NEW) if change is None else change, "evidence": [E()] if evidence is None else evidence,
        "conflicts": list(conflicts), "audit_run_id": "coverage-audit-2026-10-10", "created_by": "auditor",
        "status": status, "created_at": "t", "updated_at": "t"}, origin="import", db_path=db)


def _set_conflicts(db, pid, conflicts):
    with store.transaction(db) as conn:
        conn.execute("UPDATE sv_audit_proposal SET conflicts_json=? WHERE proposal_id=?",
                     (json.dumps(conflicts, ensure_ascii=False), pid))


def test_schema_has_ack_table_and_counts(db):
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "sv_audit_conflict_ack" in names and "sv_audit_conflict_ack" in store.TABLES
    assert store.counts(db_path=db)["sv_audit_conflict_ack"] == 0
    store._ENSURED.clear()
    store.ensure_tables(db)                                                              # 멱등
    assert store.counts(db_path=db)["sv_audit_conflict_ack"] == 0


def test_acknowledge_records_row_event_and_view(db):
    _proposal(db)
    out = ap.acknowledge_conflict("AUD-1", 1, reviewer=" admin@x ", reason="  매체 전사 수준으로 취급 확인  ", db_path=db)
    assert out["proposal_id"] == "AUD-1" and out["status"] == "needs_review"
    assert [s["acknowledged"] for s in out["conflicts_status"]] == [False, True, False]
    assert out["conflicts_unacknowledged"] == [0, 2]
    s1 = out["conflicts_status"][1]
    assert (s1["by"], s1["reason"], s1["text"]) == ("admin@x", "매체 전사 수준으로 취급 확인", C3[1]) and s1["at"]
    acks = ap.list_conflict_acks("AUD-1", db_path=db)
    assert len(acks) == 1 and acks[0]["conflict_index"] == 1 and acks[0]["conflict_text"] == C3[1]
    assert acks[0]["acknowledged_by"] == "admin@x" and acks[0]["reason"] == "매체 전사 수준으로 취급 확인"
    ev = store.list_proposal_events("AUD-1", db_path=db)
    assert [(e["event"], e["actor"]) for e in ev] == [("conflict_acknowledged", "admin@x")]
    assert "#1" in ev[0]["detail"] and "매체 전사 수준으로 취급 확인" in ev[0]["detail"]
    view = ap.proposal_view("AUD-1", db_path=db)
    assert view["conflicts_status"] == out["conflicts_status"] and ap.proposal_view("AUD-NOPE", db_path=db) is None
    listed = ap.with_conflicts_status(store.list_proposals(db_path=db), db_path=db)
    assert listed[0]["conflicts_unacknowledged"] == [0, 2]


@pytest.mark.parametrize("index", [-1, 3, True, "0", None, 1.0])
def test_acknowledge_rejects_bad_index(db, index):
    _proposal(db)
    with pytest.raises(ap.ApprovalError) as ei:
        ap.acknowledge_conflict("AUD-1", index, reviewer="a", reason="사유", db_path=db)
    assert ei.value.status == 422 and ei.value.code == "BAD_CONFLICT_INDEX"
    assert ap.list_conflict_acks("AUD-1", db_path=db) == []


def test_acknowledge_without_conflicts_is_422(db):
    _proposal(db, conflicts=())
    with pytest.raises(ap.ApprovalError) as ei:
        ap.acknowledge_conflict("AUD-1", 0, reviewer="a", reason="사유", db_path=db)
    assert ei.value.status == 422 and ei.value.code == "BAD_CONFLICT_INDEX"


@pytest.mark.parametrize("reviewer,reason,code", [("a", "", "BAD_REASON"), ("a", " x ", "BAD_REASON"),
                                                  ("a", "x" * 1001, "BAD_REASON"), ("a", None, "BAD_REASON"),
                                                  ("", "사유", "BAD_REVIEWER"), ("x" * 81, "사유", "BAD_REVIEWER")])
def test_acknowledge_validates_reason_and_reviewer(db, reviewer, reason, code):
    _proposal(db)
    with pytest.raises(ap.ApprovalError) as ei:
        ap.acknowledge_conflict("AUD-1", 0, reviewer=reviewer, reason=reason, db_path=db)
    assert ei.value.status == 422 and ei.value.code == code
    assert ap.list_conflict_acks("AUD-1", db_path=db) == [] and store.list_proposal_events("AUD-1", db_path=db) == []


def test_acknowledge_reason_bounds_inclusive(db):
    _proposal(db)
    ap.acknowledge_conflict("AUD-1", 0, reviewer="a", reason=" 확인 ", db_path=db)          # strip 후 2자
    ap.acknowledge_conflict("AUD-1", 1, reviewer="a", reason="가" * 1000, db_path=db)
    assert len(ap.list_conflict_acks("AUD-1", db_path=db)) == 2


def test_acknowledge_404_and_state_409(db):
    with pytest.raises(ap.ApprovalError) as ei:
        ap.acknowledge_conflict("AUD-NOPE", 0, reviewer="a", reason="사유", db_path=db)
    assert ei.value.status == 404 and ei.value.code == "NOT_FOUND"
    for i, st in enumerate(("scheduled", "applied", "rejected", "superseded", "application_failed", "approved")):
        _proposal(db, pid=f"AUD-S{i}", status=st)
        with pytest.raises(ap.ApprovalError) as ei:
            ap.acknowledge_conflict(f"AUD-S{i}", 0, reviewer="a", reason="사유", db_path=db)
        assert ei.value.status == 409 and ei.value.code == "STATE"
        assert ei.value.message == "현재 상태에서는 충돌을 확인할 수 없습니다."
    _proposal(db, pid="AUD-NE", status="needs_evidence")
    ap.acknowledge_conflict("AUD-NE", 0, reviewer="a", reason="사유", db_path=db)        # needs_evidence 도 열린 상태


def test_reacknowledging_a_valid_ack_is_409_and_keeps_original(db):
    _proposal(db)
    ap.acknowledge_conflict("AUD-1", 0, reviewer="first@x", reason="첫 확인", db_path=db)
    with pytest.raises(ap.ApprovalError) as ei:
        ap.acknowledge_conflict("AUD-1", 0, reviewer="second@x", reason="덮어쓰기 시도", db_path=db)
    assert ei.value.status == 409 and ei.value.code == "ALREADY_ACKNOWLEDGED"
    acks = ap.list_conflict_acks("AUD-1", db_path=db)
    assert [(a["acknowledged_by"], a["reason"]) for a in acks] == [("first@x", "첫 확인")]
    assert [e["event"] for e in store.list_proposal_events("AUD-1", db_path=db)] == ["conflict_acknowledged"]


def test_approve_requires_all_acks_then_schedules(db):
    _proposal(db)
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-1", reviewer="a", reason="승인", db_path=db)
    assert ei.value.status == 422 and ei.value.code == "CONFLICTS_UNACKNOWLEDGED" and "#0, #1, #2" in ei.value.message
    ap.acknowledge_conflict("AUD-1", 0, reviewer="a", reason="검토 메모", db_path=db)
    ap.acknowledge_conflict("AUD-1", 2, reviewer="a", reason="검토 메모", db_path=db)
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-1", reviewer="a", reason="승인", db_path=db)
    assert ei.value.details == {"unacknowledged": [1]}
    assert not [p for p in store.list_people(db_path=db) if p["name"] == "이소희"]           # 부수 행 없음
    ap.acknowledge_conflict("AUD-1", 1, reviewer="b", reason="매체 전사 수준", db_path=db)
    out = ap.approve("AUD-1", reviewer="a", reason="승인", db_path=db)
    assert out["status"] == "scheduled" and out["effective_from"] == "2026-10-13"
    assert all(s["acknowledged"] for s in out["conflicts_status"]) and out["conflicts_unacknowledged"] == []
    assert [e["event"] for e in store.list_proposal_events("AUD-1", db_path=db)] == [
        "conflict_acknowledged", "conflict_acknowledged", "conflict_acknowledged", "approved"]
    with pytest.raises(ap.ApprovalError) as ei:                                          # 승인 뒤 확인 불가
        ap.acknowledge_conflict("AUD-1", 0, reviewer="a", reason="늦은 확인", db_path=db)
    assert ei.value.status == 409 and ei.value.code == "STATE"


def test_approve_without_conflicts_unchanged(db):
    _proposal(db, conflicts=())
    out = ap.approve("AUD-1", reviewer="a", reason="승인", db_path=db)
    assert out["status"] == "scheduled" and out["conflicts_status"] == []


def test_swapped_conflict_text_voids_ack(db):
    """확인 아래에서 충돌 문구를 바꿔치기할 수 없다 — 스냅숏 불일치 → 무효 → 승인 422, 재확인은 허용(행 교체 + 이벤트 추가)."""
    _proposal(db)
    for i in range(3):
        ap.acknowledge_conflict("AUD-1", i, reviewer="a", reason="확인", db_path=db)
    _set_conflicts(db, "AUD-1", [C3[0], "공식 원문과 직책이 다름", C3[2]])
    view = ap.proposal_view("AUD-1", db_path=db)
    assert view["conflicts_unacknowledged"] == [1] and view["conflicts_status"][1]["stale"] is True
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-1", reviewer="a", reason="승인", db_path=db)
    assert ei.value.code == "CONFLICTS_UNACKNOWLEDGED" and ei.value.details == {"unacknowledged": [1]}
    ap.acknowledge_conflict("AUD-1", 1, reviewer="b@x", reason="새 충돌 확인", db_path=db)
    ack1 = [a for a in ap.list_conflict_acks("AUD-1", db_path=db) if a["conflict_index"] == 1][0]
    assert (ack1["conflict_text"], ack1["acknowledged_by"]) == ("공식 원문과 직책이 다름", "b@x")
    assert len(ap.list_conflict_acks("AUD-1", db_path=db)) == 3
    assert len(store.list_proposal_events("AUD-1", db_path=db)) == 4                     # 이력은 append-only
    # 충돌이 하나 늘면 새 index 는 미확인
    _set_conflicts(db, "AUD-1", [C3[0], "공식 원문과 직책이 다름", C3[2], "추가 충돌"])
    with pytest.raises(ap.ApprovalError) as ei:
        ap.approve("AUD-1", reviewer="a", reason="승인", db_path=db)
    assert ei.value.details == {"unacknowledged": [3]}
    ap.acknowledge_conflict("AUD-1", 3, reviewer="a", reason="추가 확인", db_path=db)
    assert ap.approve("AUD-1", reviewer="a", reason="승인", db_path=db)["status"] == "scheduled"


def test_reject_ignores_acks(db):
    _proposal(db)
    assert ap.reject("AUD-1", reviewer="a", reason="충돌 미해결", db_path=db)["status"] == "rejected"
    with pytest.raises(ap.ApprovalError) as ei:
        ap.acknowledge_conflict("AUD-1", 0, reviewer="a", reason="사유", db_path=db)
    assert ei.value.status == 409


def test_ack_failure_rolls_back_row_and_event(db, monkeypatch):
    _proposal(db)

    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(store, "add_proposal_event_row", boom)
    with pytest.raises(RuntimeError):
        ap.acknowledge_conflict("AUD-1", 0, reviewer="a", reason="사유", db_path=db)
    monkeypatch.undo()
    assert ap.list_conflict_acks("AUD-1", db_path=db) == []


# ── 동시성: 확인과 승인이 경합해도 미확인 충돌로 승인되지 않는다 ─────────────────────────

def test_approve_holding_write_lock_blocks_ack_and_fails_on_missing_ack(db, monkeypatch):
    """승인이 BEGIN IMMEDIATE 안에서 확인 목록을 읽은 뒤 → 동시 확인은 잠금 대기 → 승인은 그 시점 기준 422, 확인은 이후 기록."""
    _proposal(db)
    ap.acknowledge_conflict("AUD-1", 0, reviewer="a", reason="확인", db_path=db)
    ap.acknowledge_conflict("AUD-1", 1, reviewer="a", reason="확인", db_path=db)
    inside, release = threading.Event(), threading.Event()
    real = ap.plan_approval

    def slow_plan(*a, **kw):
        inside.set()
        release.wait(3)
        return real(*a, **kw)
    monkeypatch.setattr(ap, "plan_approval", slow_plan)
    results: dict = {}

    def do_approve():
        try:
            results["approve"] = ap.approve("AUD-1", reviewer="a", reason="승인", db_path=db)["status"]
        except ap.ApprovalError as exc:
            results["approve"] = exc.code

    def do_ack():
        results["ack_started"] = time.monotonic()
        ap.acknowledge_conflict("AUD-1", 2, reviewer="b", reason="마지막 확인", db_path=db)
        results["ack_done"] = time.monotonic()

    t1 = threading.Thread(target=do_approve)
    t1.start()
    assert inside.wait(5)
    t2 = threading.Thread(target=do_ack)
    t2.start()
    time.sleep(0.3)
    assert "ack_done" not in results                                                     # 승인 트랜잭션이 잠금을 쥐고 있다
    release.set()
    t1.join(10)
    t2.join(10)
    assert results["approve"] == "CONFLICTS_UNACKNOWLEDGED"
    assert store.get_proposal("AUD-1", db_path=db)["status"] == "needs_review"
    assert len(ap.list_conflict_acks("AUD-1", db_path=db)) == 3


def test_concurrent_ack_and_approve_never_approves_with_missing_ack(db):
    for n in range(8):
        pid = f"AUD-R{n}"
        _proposal(db, pid=pid, change={**NEW, "name": f"인물{n}"})
        ap.acknowledge_conflict(pid, 0, reviewer="a", reason="확인", db_path=db)
        ap.acknowledge_conflict(pid, 1, reviewer="a", reason="확인", db_path=db)
        barrier = threading.Barrier(2)
        out: dict = {}

        def approve():
            barrier.wait()
            try:
                out["approve"] = ap.approve(pid, reviewer="a", reason="승인", db_path=db)["status"]
            except ap.ApprovalError as exc:
                out["approve"] = exc.code

        def ack():
            barrier.wait()
            try:
                ap.acknowledge_conflict(pid, 2, reviewer="b", reason="확인", db_path=db)
                out["ack"] = "ok"
            except ap.ApprovalError as exc:
                out["ack"] = exc.code

        ts = [threading.Thread(target=approve), threading.Thread(target=ack)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(20)
        events = [e["event"] for e in store.list_proposal_events(pid, db_path=db)]
        if out["approve"] == "scheduled":
            assert out["ack"] == "ok" and events.index("approved") > max(
                i for i, e in enumerate(events) if e == "conflict_acknowledged")
            assert len(ap.list_conflict_acks(pid, db_path=db)) == 3
        else:
            assert out["approve"] == "CONFLICTS_UNACKNOWLEDGED" and out["ack"] == "ok"
            assert "approved" not in events and store.get_proposal(pid, db_path=db)["status"] == "needs_review"


# ── 직책 반영 실패 → 대기 해제 ────────────────────────────────────────────────────────

def test_failed_role_application_releases_pending_block(db, monkeypatch):
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, conflicts=(), pid="AUD-RC1")
    ap.approve("AUD-RC1", reviewer="a", reason="공식 인사자료", db_path=db)

    def broken(conn, proposal, ts):
        raise ap.ApprovalError(409, "반영 중 오류", "APPLY_TEST")
    monkeypatch.setattr(ap, "_apply_role_change", broken)
    res = ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)
    monkeypatch.undo()
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 13))
    assert res["failed_ids"] == ["AUD-RC1"]
    assert store.get_proposal("AUD-RC1", db_path=db)["status"] == "application_failed"
    hist = store.list_role_history("P-HIRA-KIM", db_path=db)
    assert [(h["status"], h["proposal_id"]) for h in hist] == [("failed", "AUD-RC1")]
    ev = store.list_proposal_events("AUD-RC1", db_path=db)
    assert ev[-1]["event"] == "application_failed" and "APPLY_TEST" in ev[-1]["detail"]
    assert "failed" in ev[-1]["detail"]                                                  # 해제 사실도 기록
    # 이후 같은 인물의 직책 변경은 PENDING_ROLE_CHANGE 로 막히지 않는다
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, conflicts=(), pid="AUD-RC2")
    assert ap.approve("AUD-RC2", reviewer="a", reason="재승인", db_path=db)["status"] == "scheduled"
    assert ap.apply_scheduled(today=date(2026, 10, 14), db_path=db)["applied_ids"] == ["AUD-RC2"]
    statuses = [h["status"] for h in store.list_role_history("P-HIRA-KIM", db_path=db)]
    assert statuses.count("failed") == 1 and statuses.count("applied") == 1
    assert store.get_person("P-HIRA-KIM", db_path=db)["role"] == "실장"


def test_role_history_accepts_failed_status(db):
    with store.transaction(db) as conn:
        store.insert_role_history_row(conn, {"person_id": "P-HIRA-KIM", "role": "x", "status": "failed"})
    assert store.list_role_history("P-HIRA-KIM", status="failed", db_path=db)[0]["role"] == "x"


_OLD_ROLE_HIST_DDL = """
CREATE TABLE sv_person_role_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id      TEXT NOT NULL,
    organization   TEXT, department TEXT, role TEXT,
    effective_from TEXT, effective_to TEXT,
    status         TEXT NOT NULL CHECK (status IN ('scheduled','applied','superseded')),
    proposal_id    TEXT, evidence_json TEXT, approved_at TEXT, applied_at TEXT
);
CREATE INDEX idx_sv_role_hist_person ON sv_person_role_history(person_id, status);
INSERT INTO sv_person_role_history (person_id, role, status, proposal_id) VALUES ('P-1', '국장', 'scheduled', 'AUD-X');
INSERT INTO sv_person_role_history (person_id, role, status) VALUES ('P-1', '과장', 'superseded');
"""


def test_ensure_tables_migrates_old_role_history_check(tmp_path):
    """T1 스키마로 이미 만들어진 DB: CHECK 에 'failed' 가 없으면 행·id 를 보존한 채 테이블을 재구성한다(멱등)."""
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.executescript(_OLD_ROLE_HIST_DDL)
    c.close()
    store.ensure_tables(db)
    rows = store.list_role_history("P-1", db_path=db)
    assert [(r["id"], r["role"], r["status"], r["proposal_id"]) for r in rows] == [
        (1, "국장", "scheduled", "AUD-X"), (2, "과장", "superseded", None)]
    with store.transaction(db) as conn:
        conn.execute("UPDATE sv_person_role_history SET status='failed' WHERE id=1")
        new_id = store.insert_role_history_row(conn, {"person_id": "P-1", "role": "r", "status": "applied"})
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE sv_person_role_history SET status='bogus' WHERE id=2")
    assert new_id == 3                                                                   # AUTOINCREMENT 이어짐
    idx = {r[0] for r in sqlite3.connect(db).execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sv_person_role_history'")}
    assert "idx_sv_role_hist_person" in idx
    store._ENSURED.clear()
    store.ensure_tables(db)                                                              # 두 번째는 no-op
    assert len(store.list_role_history("P-1", db_path=db)) == 3


# ── 픽스처·실데이터 ───────────────────────────────────────────────────────────────────

def _load_registry(db, data: Path):
    for p in json.loads((data / "people_registry.json").read_text(encoding="utf-8"))["people"]:
        store.insert_person_if_absent(p, origin="import", db_path=db)
    for s in json.loads((data / "source_registry.json").read_text(encoding="utf-8"))["sources"]:
        store.insert_source_domain_if_absent(s, origin="import", db_path=db)
    props = json.loads((data / "audit_proposals.json").read_text(encoding="utf-8"))["proposals"]
    for p in props:
        store.insert_proposal_if_absent(p, origin="import", db_path=db)
    return props


def _ack_all_then_approve(db, props):
    results = {}
    for p in props:
        for i in range(len(p["conflicts"])):
            ap.acknowledge_conflict(p["proposal_id"], i, reviewer="admin@x", reason="검토 메모로 확인", db_path=db)
        try:
            results[p["proposal_id"]] = ap.approve(p["proposal_id"], reviewer="admin@x", reason="충돌 확인 후 승인",
                                                   db_path=db)["status"]
        except ap.ApprovalError as exc:
            results[p["proposal_id"]] = (exc.status, exc.code)
    return results


def test_fixture_proposals_after_acks(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    db = tmp_path / "a.db"
    props = _load_registry(db, FIXTURE / "data")
    res = _ack_all_then_approve(db, props)
    by_type = {p["proposal_type"]: res[p["proposal_id"]] for p in props}
    assert by_type == {"new_person": "scheduled", "role_change": "scheduled", "source_expansion": (422, "EVIDENCE")}


_REAL_SRC = os.environ.get("STAKEHOLDER_SRC_DIR")


@pytest.mark.skipif(not _REAL_SRC or not (Path(_REAL_SRC or ".") / "data" / "audit_proposals.json").exists(),
                    reason="STAKEHOLDER_SRC_DIR(소스 repo 체크아웃) 가 없으면 실데이터 확인을 건너뛴다")
def test_real_six_proposals_after_acks(tmp_path, monkeypatch):
    """실데이터 6건: 확인 전에는 전부 CONFLICTS_UNACKNOWLEDGED, 전부 확인 후 new_person 4 + role_change 1 승인,
    source_expansion 은 E1 ×3 이라 근거 422."""
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    db = tmp_path / "real.db"
    props = _load_registry(db, Path(_REAL_SRC) / "data")
    assert len(props) == 6 and all(p["conflicts"] for p in props)
    for p in props:
        with pytest.raises(ap.ApprovalError) as ei:
            ap.approve(p["proposal_id"], reviewer="admin@x", reason="확인 전", db_path=db)
        assert ei.value.code == "CONFLICTS_UNACKNOWLEDGED"
    res = _ack_all_then_approve(db, props)
    kinds = {}
    for p in props:
        kinds.setdefault(p["proposal_type"], []).append(res[p["proposal_id"]])
    assert kinds == {"new_person": ["scheduled"] * 4, "role_change": ["scheduled"],
                     "source_expansion": [(422, "EVIDENCE")]}
    n_acks = sum(len(p["conflicts"]) for p in props)
    assert len(store.list_conflict_acks(db_path=db)) == n_acks
