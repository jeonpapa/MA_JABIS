"""에이전트 팀 API — 역할 인증(agent/admin) + 작업 원장 엔드포인트 (계획 Task 6).

런북은 실제 스케줄러·Gmail·LLM 에 닿으므로(DispatchDeps.production) 테스트는 항상 가짜 실행기를 주입한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


@pytest.fixture(autouse=True)
def _fake_runbooks(monkeypatch):
    """네트워크·Gmail·LLM 금지 — 런북 실행기를 결정적 가짜로 바꾼다 (레지스트리는 진짜)."""
    calls: list[str] = []

    def fake_run(name: str) -> dict:
        calls.append(name)
        return {"ok": True, "summary": f"{name} 복구 완료", "detail": {}}

    monkeypatch.setattr(s._agent_runbooks, "run", fake_run)
    return calls


def _triaged_task(db, **kw) -> int:
    kw.setdefault("kind", "ops_incident")
    kw.setdefault("source", "x")
    kw.setdefault("source_ref", "y")
    kw.setdefault("title", "t")
    tid = store.create_task(db_path=db, **kw)
    store.update_task(tid, db_path=db, status="triaged", actor="test")
    return tid


# ── 역할 인증 ────────────────────────────────────────────────────────────────

def test_agent_role_required(monkeypatch, tmp_path):
    _patch_db(monkeypatch, tmp_path)
    c = s.app.test_client()
    assert c.get("/api/agent/tasks").status_code == 401
    assert c.get("/api/agent/tasks", headers=_h("user")).status_code == 403
    assert c.get("/api/agent/tasks", headers=_h("agent")).status_code == 200
    assert c.get("/api/agent/tasks", headers=_h("admin")).status_code == 200   # admin 은 상위 권한


def test_admin_routes_still_reject_agent_and_user(monkeypatch, tmp_path):
    """역할 일반화가 기존 admin 전용 라우트를 느슨하게 만들지 않는다."""
    _patch_db(monkeypatch, tmp_path)
    c = s.app.test_client()
    assert c.get("/api/admin/agent-tasks", headers=_h("agent")).status_code == 403
    assert c.get("/api/admin/agent-tasks", headers=_h("user")).status_code == 403
    assert c.get("/api/admin/users", headers=_h("agent")).status_code == 403


# ── 선점 → 런북 → 회신 ────────────────────────────────────────────────────────

def test_claim_and_resolve_flow(monkeypatch, tmp_path, _fake_runbooks):
    db = _patch_db(monkeypatch, tmp_path)
    tid = store.create_task(kind="ops_incident", source="scheduler_job", source_ref="j:1",
                            title="잡 실패", detail="런북: daily_mailing_dispatch", db_path=db)
    store.update_task(tid, db_path=db, status="triaged", actor="test")
    c = s.app.test_client()
    listed = c.get("/api/agent/tasks?status=triaged", headers=_h("agent")).get_json()
    assert [t["id"] for t in listed["tasks"]] == [tid]
    assert listed["policy"]["enabled"] is True

    ok = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent")).get_json()
    assert ok["claimed"] is True and ok["task"]["status"] == "in_progress"
    again = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    assert again.status_code == 409

    ran = c.post(f"/api/agent/tasks/{tid}/runbook", headers=_h("agent"),
                 json={"name": "daily_mailing_dispatch"})
    assert ran.status_code == 200 and "result" in ran.get_json()
    assert _fake_runbooks == ["daily_mailing_dispatch"]

    done = c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("agent"),
                  json={"status": "closed", "resolution": "재실행으로 복구"})
    assert done.status_code == 200
    task = store.get_task(tid, db_path=db)
    assert task["status"] == "closed" and task["resolution"] == "재실행으로 복구"
    assert [e["event"] for e in store.list_events(tid, db_path=db)] == [
        "created", "status:triaged", "claimed", "runbook_started", "runbook_ok", "status:closed"]


def test_runbook_failure_is_recorded_as_event(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    monkeypatch.setattr(s._agent_runbooks, "run",
                        lambda name: {"ok": False, "summary": "여전히 실패", "detail": {}})
    tid = _triaged_task(db)
    c = s.app.test_client()
    c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    r = c.post(f"/api/agent/tasks/{tid}/runbook", headers=_h("agent"), json={"name": "domestic_price_catchup"})
    assert r.status_code == 200 and r.get_json()["result"]["ok"] is False
    events = store.list_events(tid, db_path=db)
    assert events[-1]["event"] == "runbook_failed" and events[-1]["detail"] == "여전히 실패"


def test_runbook_unknown_name_is_400(monkeypatch, tmp_path, _fake_runbooks):
    db = _patch_db(monkeypatch, tmp_path)
    tid = _triaged_task(db)
    c = s.app.test_client()
    c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    r = c.post(f"/api/agent/tasks/{tid}/runbook", headers=_h("agent"), json={"name": "nope"})
    assert r.status_code == 400 and "error" in r.get_json()
    assert _fake_runbooks == []


def test_runbook_requires_claimed_task(monkeypatch, tmp_path, _fake_runbooks):
    """선점(in_progress) 없이 런북을 돌리면 예산 판정을 우회한다 — 막는다."""
    db = _patch_db(monkeypatch, tmp_path)
    tid = _triaged_task(db)
    c = s.app.test_client()
    r = c.post(f"/api/agent/tasks/{tid}/runbook", headers=_h("agent"), json={"name": "domestic_price_catchup"})
    assert r.status_code == 409 and r.get_json()["code"] == "NOT_CLAIMED"
    assert _fake_runbooks == []
    assert c.post("/api/agent/tasks/999/runbook", headers=_h("agent"),
                  json={"name": "domestic_price_catchup"}).status_code == 404


def test_resolve_validates_status_and_transition(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    tid = _triaged_task(db)
    c = s.app.test_client()
    bad = c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("agent"), json={"status": "deployed"})
    assert bad.status_code == 400
    # triaged → closed 는 상태 기계상 불가(선점 먼저)
    r = c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("agent"), json={"status": "closed"})
    assert r.status_code == 409 and r.get_json()["code"] == "INVALID_STATE"
    assert c.post("/api/agent/tasks/999/resolve", headers=_h("agent"),
                  json={"status": "blocked"}).status_code == 409


# ── 자동 분류: 신호가 만든 new 과제를 디스패처가 집을 수 있어야 한다 ─────────────

def test_claim_auto_triages_new_ops_incident(monkeypatch, tmp_path):
    """signals.py 는 new 로 만들고 아무도 triaged 로 옮기지 않는다 — ops_incident 는 선점 시 자동 분류."""
    db = _patch_db(monkeypatch, tmp_path)
    tid = store.create_task(kind="ops_incident", source="scheduler_job", source_ref="j:2",
                            title="잡 실패", detail="런북: domestic_price_catchup", db_path=db)
    c = s.app.test_client()
    listed = c.get("/api/agent/tasks?status=new,triaged", headers=_h("agent")).get_json()
    assert [t["id"] for t in listed["tasks"]] == [tid]
    r = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    assert r.status_code == 200 and r.get_json()["claimed"] is True
    assert [e["event"] for e in store.list_events(tid, db_path=db)] == [
        "created", "auto_triaged", "status:triaged", "claimed"]


def test_claim_does_not_auto_triage_other_kinds(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    tid = store.create_task(kind="user_request", source="service_request", source_ref="sr:1",
                            title="요청", db_path=db)
    c = s.app.test_client()
    r = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    assert r.status_code == 409 and r.get_json()["code"] == "NOT_CLAIMABLE"
    assert store.get_task(tid, db_path=db)["status"] == "new"


# ── 정책 ────────────────────────────────────────────────────────────────────

def test_policy_blocks_claim(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    from agents.agent_tasks.policy import set_policy
    set_policy({"enabled": False}, actor="admin@msd.com", db_path=db)
    tid = _triaged_task(db)
    c = s.app.test_client()
    r = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    assert r.status_code == 423 and r.get_json()["reason"] == "disabled"
    assert store.get_task(tid, db_path=db)["status"] == "triaged"


def test_admin_policy_endpoint_updates_and_returns_policy(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    c = s.app.test_client()
    r = c.post("/api/admin/agent-policy", headers=_h("admin", "admin@msd.com"),
               json={"enabled": False, "max_tasks_per_day": 5})
    assert r.status_code == 200
    policy = r.get_json()["policy"]
    assert policy["enabled"] is False and policy["max_tasks_per_day"] == 5
    assert c.post("/api/admin/agent-policy", headers=_h("agent"), json={"enabled": True}).status_code == 403
    assert c.get("/api/admin/agent-tasks", headers=_h("admin", "admin@msd.com")).get_json()["policy"]["enabled"] is False


# ── 운영 원장(admin) ──────────────────────────────────────────────────────────

def test_admin_ledger_endpoint(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    tid = store.create_task(kind="ops_incident", source="x", source_ref="y", title="t", db_path=db)
    c = s.app.test_client()
    assert c.get("/api/admin/agent-tasks", headers=_h("user")).status_code == 403
    data = c.get("/api/admin/agent-tasks", headers=_h("admin", "admin@msd.com")).get_json()
    assert data["policy"]["enabled"] is True
    assert [t["id"] for t in data["tasks"]] == [tid]
    assert data["runbooks"] == ["daily_mailing_dispatch", "daily_mailing_top_news", "domestic_price_catchup"]
    detail = c.get(f"/api/admin/agent-tasks/{tid}", headers=_h("admin", "admin@msd.com")).get_json()
    assert [e["event"] for e in detail["events"]] == ["created"]
    assert detail["task"]["id"] == tid
    missing = c.get("/api/admin/agent-tasks/999", headers=_h("admin", "admin@msd.com"))
    assert missing.status_code == 404 and "error" in missing.get_json()


def test_admin_ledger_includes_blocked_tasks(monkeypatch, tmp_path):
    """blocked 는 '사람 확인 필요' — 운영 원장 화면(HUMAN_STATUSES)이 반드시 봐야 한다."""
    db = _patch_db(monkeypatch, tmp_path)
    tid = store.create_task(kind="ops_incident", source="x", source_ref="y", title="t", db_path=db)
    store.update_task(tid, db_path=db, status="blocked", actor="test", resolution="런북 없음")
    c = s.app.test_client()
    data = c.get("/api/admin/agent-tasks", headers=_h("admin", "admin@msd.com")).get_json()
    assert [t["status"] for t in data["tasks"]] == ["blocked"]
    only_new = c.get("/api/admin/agent-tasks?status=new", headers=_h("admin", "admin@msd.com")).get_json()
    assert only_new["tasks"] == []


def test_agent_service_account_can_be_created(tmp_path):
    """VPS 디스패처용 role='agent' 계정 생성 — 없으면 P1 운영 자체가 시작되지 않는다."""
    from agents.db.users import UsersDB

    db = UsersDB(str(tmp_path / "users.db"))
    user = db.create_user("agent@msd.com", "strong-passphrase", role="agent")
    assert user["role"] == "agent"
    assert db.verify_password("agent@msd.com", "strong-passphrase")["role"] == "agent"


# ── 동시 claim: 자동 분류는 단일 트랜잭션이어야 한다 ─────────────────────────────

def test_concurrent_claim_of_new_task_is_atomic(monkeypatch, tmp_path):
    """두 디스패처가 같은 new 과제를 동시에 claim — 하나만 200, 최종 행은 승자의 in_progress,
    auto_triaged 이벤트는 정확히 1개. (update_task 는 상태 가드가 없어 승자의 in_progress 를 되돌렸다.)"""
    import threading

    db = _patch_db(monkeypatch, tmp_path)
    tid = store.create_task(kind="ops_incident", source="scheduler_job", source_ref="j:race",
                            title="잡 실패", detail="런북: domestic_price_catchup", db_path=db)
    real_get_task = store.get_task
    barrier = threading.Barrier(2, timeout=10)

    def gated_get_task(task_id, **kw):
        task = real_get_task(task_id, **kw)
        if task and task["status"] == "new":   # 두 실행 모두 'new' 를 읽은 뒤에야 쓰기로 넘어간다
            barrier.wait()
        return task

    monkeypatch.setattr(s._agent_store, "get_task", gated_get_task)
    results: dict[str, int] = {}

    def worker(actor: str):
        r = s.app.test_client().post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent", actor))
        results[actor] = r.status_code

    threads = [threading.Thread(target=worker, args=(a,)) for a in ("vps-0@x", "vps-1@x")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results.values()) == [200, 409], results
    winner = next(a for a, code in results.items() if code == 200)
    task = real_get_task(tid, db_path=db)
    assert task["status"] == "in_progress" and task["claimed_by"] == winner
    events = [e["event"] for e in store.list_events(tid, db_path=db)]
    assert events.count("auto_triaged") == 1, events
    assert events == ["created", "auto_triaged", "status:triaged", "claimed"]


# ── 재시도 경로: 되돌리기 후 재선점 + 한도 초과 시 자동 blocked ──────────────────────

def test_resolve_triaged_then_reclaim(monkeypatch, tmp_path):
    """blocked 과제를 triaged 로 되돌리면 다음 실행이 다시 집을 수 있어야 한다 (VPS_SETUP §6)."""
    db = _patch_db(monkeypatch, tmp_path)
    tid = _triaged_task(db)
    c = s.app.test_client()
    assert c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent")).status_code == 200
    assert c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("agent"),
                  json={"status": "blocked", "resolution": "디스패처 오류"}).status_code == 200
    assert c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("agent"),
                  json={"status": "triaged"}).status_code == 200
    t = store.get_task(tid, db_path=db)
    assert t["status"] == "triaged" and t["claimed_by"] is None and t["attempts"] == 1
    r = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent", "vps-2@x"))
    assert r.status_code == 200 and r.get_json()["task"]["attempts"] == 2
    assert r.get_json()["task"]["claimed_by"] == "vps-2@x"


def test_claim_exhausted_attempts_blocks_task(monkeypatch, tmp_path):
    """설계 §5: 재시도 2회까지. attempts 가 3 이면 선점 불가 + 서버가 blocked(사람 확인) 로 바꾼다."""
    db = _patch_db(monkeypatch, tmp_path)
    tid = _triaged_task(db)
    c = s.app.test_client()
    for _ in range(store.MAX_ATTEMPTS):
        assert c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent")).status_code == 200
        assert c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("agent"),
                      json={"status": "triaged"}).status_code == 200
    r = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    assert r.status_code == 409 and r.get_json()["claimed"] is False
    assert r.get_json()["reason"] == "attempts_exhausted"
    t = store.get_task(tid, db_path=db)
    assert t["status"] == "blocked" and t["attempts"] == store.MAX_ATTEMPTS
    assert t["resolution"] == "재시도 한도(3회) 초과 — 사람 확인 필요"
    events = [e["event"] for e in store.list_events(tid, db_path=db)]
    assert events[-1] == "status:blocked"
    # 이미 blocked 인 과제에 또 claim 해도 같은 409, 상태는 그대로
    assert c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent")).status_code == 409
    assert store.get_task(tid, db_path=db)["status"] == "blocked"
