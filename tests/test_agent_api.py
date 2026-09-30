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
                  json={"status": "blocked"}).status_code == 404          # /runbook 과 같은 NOT_FOUND


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
    assert t["resolution"] == f"재시도 한도({store.MAX_ATTEMPTS}회) 초과 — 사람 확인 필요"
    events = [e["event"] for e in store.list_events(tid, db_path=db)]
    assert events[-1] == "status:blocked"
    # 이미 blocked 인 과제에 또 claim 해도 409, 상태는 그대로, 이벤트도 더 늘지 않는다
    again = c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent"))
    assert again.status_code == 409 and "reason" not in again.get_json()
    assert store.get_task(tid, db_path=db)["status"] == "blocked"
    assert [e["event"] for e in store.list_events(tid, db_path=db)].count("status:blocked") == 1


def test_concurrent_claims_on_exhausted_task_emit_one_blocked_event(monkeypatch, tmp_path):
    """스레드 재현(API): 소진된 과제에 두 디스패처가 동시에 claim — 둘 다 409, reason=attempts_exhausted 는
    전이를 수행한 하나에만, status:blocked 이벤트는 정확히 1개."""
    import threading

    db = _patch_db(monkeypatch, tmp_path)
    tid = _triaged_task(db)
    c = s.app.test_client()
    for _ in range(store.MAX_ATTEMPTS):
        assert c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent")).status_code == 200
        assert c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("agent"),
                      json={"status": "triaged"}).status_code == 200
    real_claim = store.claim_task
    barrier = threading.Barrier(2, timeout=10)

    def gated_claim(task_id, **kw):        # 두 실행이 모두 선점 직전까지 온 뒤에야 store 로 들어간다
        barrier.wait()
        return real_claim(task_id, **kw)

    monkeypatch.setattr(s._agent_store, "claim_task", gated_claim)
    results: dict[str, tuple[int, str | None]] = {}

    def worker(actor: str):
        r = s.app.test_client().post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent", actor))
        results[actor] = (r.status_code, r.get_json().get("reason"))

    threads = [threading.Thread(target=worker, args=(a,)) for a in ("vps-0@x", "vps-1@x")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(v[0] for v in results.values()) == [409, 409], results
    assert sorted(v[1] or "" for v in results.values()) == ["", "attempts_exhausted"], results
    events = [e["event"] for e in store.list_events(tid, db_path=db)]
    assert events.count("status:blocked") == 1, events
    assert store.get_task(tid, db_path=db)["status"] == "blocked"


# ── 권한 최소화: agent 토큰은 role='agent' 를 명시한 라우트(/api/agent/*)만 통과한다 ────────

def test_agent_token_is_confined_to_agent_routes(monkeypatch, tmp_path):
    """require_auth()(role 미지정) 도 agent 를 거부해야 한다 — VPS 자격증명으로 일반 사용자 API 접근 금지."""
    _patch_db(monkeypatch, tmp_path)
    c = s.app.test_client()
    assert c.get("/api/mail-subscriptions", headers=_h("agent")).status_code == 403
    assert c.get("/api/msd/pipeline", headers=_h("agent")).status_code == 403
    assert c.get("/api/auth/me", headers=_h("agent")).status_code == 403
    assert c.get("/api/agent/tasks", headers=_h("agent")).status_code == 200
    assert c.get("/api/agent/tasks", headers=_h("user")).status_code == 403
    assert c.get("/api/mail-subscriptions", headers=_h("user", "me@msd.com")).status_code == 200
    assert c.get("/api/mail-subscriptions", headers=_h("admin", "admin@msd.com")).status_code == 200
    assert c.get("/api/agent/tasks", headers=_h("admin", "admin@msd.com")).status_code == 200


# ── 운영 원장 정렬: 과제가 200건을 넘어도 최신 사고가 첫 페이지에 보여야 한다 ────────────

def test_admin_ledger_shows_newest_task_when_over_page_limit(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    ids = [store.create_task(kind="ops_incident", source="x", source_ref=f"old:{i}", title=f"old {i}",
                             priority=1, db_path=db) for i in range(200)]
    newest = store.create_task(kind="ops_incident", source="x", source_ref="new:incident",
                               title="방금 난 사고", priority=3, db_path=db)
    c = s.app.test_client()
    data = c.get("/api/admin/agent-tasks", headers=_h("admin", "admin@msd.com")).get_json()
    listed = [t["id"] for t in data["tasks"]]
    assert len(listed) == 200
    assert listed[0] == newest and newest in listed
    assert ids[0] not in listed                      # 가장 오래된 것이 밀려난다
    # 디스패처용 목록은 우선순위 순 유지 (priority 1 인 old 가 먼저)
    agent = c.get("/api/agent/tasks?limit=5", headers=_h("agent")).get_json()["tasks"]
    assert [t["id"] for t in agent] == ids[:5]


# ── /api/agent/tasks?kind= : 디스패처가 처리할 수 있는 종류만 받는다 ─────────────────────

def test_agent_tasks_list_filters_by_kind(monkeypatch, tmp_path):
    db = _patch_db(monkeypatch, tmp_path)
    ops = store.create_task(kind="ops_incident", source="x", source_ref="o", title="사고", db_path=db)
    req = store.create_task(kind="user_request", source="service_request", source_ref="r", title="요청", db_path=db)
    c = s.app.test_client()
    both = c.get("/api/agent/tasks", headers=_h("agent")).get_json()["tasks"]
    assert {t["id"] for t in both} == {ops, req}
    only = c.get("/api/agent/tasks?kind=ops_incident", headers=_h("agent")).get_json()["tasks"]
    assert [t["id"] for t in only] == [ops]
    multi = c.get("/api/agent/tasks?kind=ops_incident,user_request", headers=_h("agent")).get_json()["tasks"]
    assert {t["id"] for t in multi} == {ops, req}
    bad = c.get("/api/agent/tasks?kind=nope", headers=_h("agent"))
    assert bad.status_code == 400 and bad.get_json()["code"] == "INVALID"
    # 반복 파라미터도 합친다 (?kind=a&kind=b) — 첫 값만 쓰면 조용히 절반이 빠진다
    repeated = c.get("/api/agent/tasks?kind=ops_incident&kind=user_request", headers=_h("agent")).get_json()["tasks"]
    assert {t["id"] for t in repeated} == {ops, req}


def test_agent_tasks_list_validates_status_like_kind(monkeypatch, tmp_path):
    """status 오타가 200 빈 목록이면 디스패처가 조용히 아무 일도 안 한다 — kind 와 같은 규칙으로 400."""
    db = _patch_db(monkeypatch, tmp_path)
    a = store.create_task(kind="ops_incident", source="x", source_ref="a", title="a", db_path=db)
    b = _triaged_task(db, source_ref="b")
    c = s.app.test_client()
    bad = c.get("/api/agent/tasks?status=triage", headers=_h("agent"))
    assert bad.status_code == 400 and bad.get_json()["code"] == "INVALID" and "triage" in bad.get_json()["error"]
    admin_bad = c.get("/api/admin/agent-tasks?status=nope", headers=_h("admin", "admin@msd.com"))
    assert admin_bad.status_code == 400 and admin_bad.get_json()["code"] == "INVALID"
    # 콤마·반복 파라미터 모두 합친다, 빈 문자열은 "필터 없음"
    assert {t["id"] for t in c.get("/api/agent/tasks?status=new&status=triaged", headers=_h("agent")).get_json()["tasks"]} == {a, b}
    assert {t["id"] for t in c.get("/api/agent/tasks?status=new,triaged", headers=_h("agent")).get_json()["tasks"]} == {a, b}
    assert [t["id"] for t in c.get("/api/agent/tasks?status=triaged", headers=_h("agent")).get_json()["tasks"]] == [b]
    assert {t["id"] for t in c.get("/api/agent/tasks?status=&kind=", headers=_h("agent")).get_json()["tasks"]} == {a, b}


# ── 소유권: 선점자가 아닌 호출자의 런북 실행·회신은 409 STALE_CLAIM ─────────────────────

def test_runbook_and_resolve_reject_stale_claim(monkeypatch, tmp_path, _fake_runbooks):
    """A 선점 → 운영자가 triaged 로 되돌림 → B 선점. 이제 A 의 /runbook·/resolve 는 STALE_CLAIM,
    B 는 정상 진행. (되돌리기가 claimed_by 를 비운 뒤 status 만 보면 A 가 B 의 과제를 닫는다.)"""
    db = _patch_db(monkeypatch, tmp_path)
    tid = _triaged_task(db, detail="런북: daily_mailing_dispatch")
    c = s.app.test_client()
    A, B = _h("agent", "vps-a@x"), _h("agent", "vps-b@x")
    assert c.post(f"/api/agent/tasks/{tid}/claim", headers=A).status_code == 200
    assert c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("admin", "admin@msd.com"),
                  json={"status": "triaged"}).status_code == 200            # 운영 개입(admin)
    assert c.post(f"/api/agent/tasks/{tid}/claim", headers=B).status_code == 200

    stale_run = c.post(f"/api/agent/tasks/{tid}/runbook", headers=A, json={"name": "daily_mailing_dispatch"})
    assert stale_run.status_code == 409 and stale_run.get_json()["code"] == "STALE_CLAIM"
    stale_close = c.post(f"/api/agent/tasks/{tid}/resolve", headers=A, json={"status": "closed"})
    assert stale_close.status_code == 409 and stale_close.get_json()["code"] == "STALE_CLAIM"
    assert _fake_runbooks == []                                              # A 의 런북은 돌지 않았다
    assert store.get_task(tid, db_path=db)["status"] == "in_progress"

    assert c.post(f"/api/agent/tasks/{tid}/runbook", headers=B, json={"name": "daily_mailing_dispatch"}).status_code == 200
    assert c.post(f"/api/agent/tasks/{tid}/resolve", headers=B,
                  json={"status": "closed", "resolution": "B 복구"}).status_code == 200
    t = store.get_task(tid, db_path=db)
    assert t["status"] == "closed" and t["resolution"] == "B 복구" and t["claimed_by"] == "vps-b@x"


def test_in_flight_runbook_cannot_close_task_reclaimed_by_other(monkeypatch, tmp_path):
    """스레드 재현: A 의 런북이 도는 **도중**에 운영자가 triaged 로 되돌리고 B 가 선점한다.
    A 의 런북 요청은 끝나지만(200, 이미 실행됐다) A 의 closed 회신은 STALE_CLAIM 이어야 하고,
    B 의 결과만 원장에 남아야 한다."""
    import threading

    db = _patch_db(monkeypatch, tmp_path)
    tid = _triaged_task(db, detail="런북: daily_mailing_dispatch")
    started, released = threading.Event(), threading.Event()

    def slow_run(name: str) -> dict:
        if not released.is_set():          # 첫 호출(A)만 붙잡는다 — B 는 바로 통과
            started.set()
            assert released.wait(10)
        return {"ok": True, "summary": f"{name} 복구 완료", "detail": {}}

    monkeypatch.setattr(s._agent_runbooks, "run", slow_run)
    A, B = _h("agent", "vps-a@x"), _h("agent", "vps-b@x")
    c = s.app.test_client()
    assert c.post(f"/api/agent/tasks/{tid}/claim", headers=A).status_code == 200
    a_result: dict = {}

    def a_worker():
        cc = s.app.test_client()
        a_result["runbook"] = cc.post(f"/api/agent/tasks/{tid}/runbook", headers=A,
                                      json={"name": "daily_mailing_dispatch"}).status_code
        r = cc.post(f"/api/agent/tasks/{tid}/resolve", headers=A, json={"status": "closed", "resolution": "A"})
        a_result["resolve"] = (r.status_code, r.get_json().get("code"))

    t = threading.Thread(target=a_worker)
    t.start()
    assert started.wait(10)                                                   # A 의 런북이 도는 중
    assert c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("admin", "admin@msd.com"),
                  json={"status": "triaged"}).status_code == 200
    assert c.post(f"/api/agent/tasks/{tid}/claim", headers=B).status_code == 200
    released.set()
    t.join(timeout=30)

    assert a_result["runbook"] == 200 and a_result["resolve"] == (409, "STALE_CLAIM"), a_result
    assert c.post(f"/api/agent/tasks/{tid}/runbook", headers=B, json={"name": "daily_mailing_dispatch"}).status_code == 200
    assert c.post(f"/api/agent/tasks/{tid}/resolve", headers=B, json={"status": "closed", "resolution": "B"}).status_code == 200
    task = store.get_task(tid, db_path=db)
    assert task["status"] == "closed" and task["resolution"] == "B" and task["claimed_by"] == "vps-b@x"


def test_admin_token_may_resolve_task_claimed_by_agent(monkeypatch, tmp_path):
    """admin 은 운영 개입용 예외: 남의 선점을 blocked/triaged 로 되돌릴 수 있다. agent 는 못 한다."""
    db = _patch_db(monkeypatch, tmp_path)
    tid = _triaged_task(db)
    c = s.app.test_client()
    assert c.post(f"/api/agent/tasks/{tid}/claim", headers=_h("agent", "vps-a@x")).status_code == 200
    other = c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("agent", "vps-b@x"), json={"status": "blocked"})
    assert other.status_code == 409 and other.get_json()["code"] == "STALE_CLAIM"
    assert c.post(f"/api/agent/tasks/{tid}/resolve", headers=_h("admin", "admin@msd.com"),
                  json={"status": "blocked", "resolution": "운영 개입"}).status_code == 200
    assert store.get_task(tid, db_path=db)["status"] == "blocked"
