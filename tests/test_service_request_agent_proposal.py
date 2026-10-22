"""개선 요청 처리 루틴(request_agent) — 조회 + 처리 계획 제출만 가능한 최소 권한 역할."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.service_requests import store as srs  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "sr.db"
    srs.ensure_service_request_tables(p)
    return p


def _new(db, title="스위스 약가 조회 추가"):
    return srs.create_request("u@x.com", title=title, body="본문", expected_outcome="기대", db_path=db)


def test_pending_lists_only_open_without_proposal_oldest_first(db):
    a, b = _new(db, "A"), _new(db, "B")
    assert [r["id"] for r in srs.list_pending_proposals(db_path=db)] == [a["id"], b["id"]]
    res = srs.add_proposal(a["id"], "agent@x.com", summary="요약", plan="1) 확인 2) 수정", db_path=db)
    assert res["status"] == "in_review"
    assert [r["id"] for r in srs.list_pending_proposals(db_path=db)] == [b["id"]]


def test_proposal_once_only_and_open_only_and_event_is_audited(db):
    r = _new(db)
    assert srs.add_proposal(r["id"], "agent@x.com", summary="s", plan="p", needs_data_change=True, db_path=db)["status"] == "in_review"
    assert srs.add_proposal(r["id"], "agent@x.com", summary="s2", plan="p2", db_path=db) == (None, "not open")
    ev = [e for e in srs.list_events(r["id"], db_path=db) if e["event_type"] == srs.PROPOSAL_EVENT]
    assert len(ev) == 1 and ev[0]["payload"]["needs_data_change"] is True and ev[0]["from_status"] == "open" and ev[0]["to_status"] == "in_review"
    assert srs.add_proposal(9999, "a@x.com", summary="s", plan="p", db_path=db) == (None, "not found")


def test_proposal_validation_rejects_empty_and_credentials_and_truncates(db):
    r = _new(db)
    assert srs.add_proposal(r["id"], "a@x.com", summary="", plan="p", db_path=db) == (None, "summary and plan required")
    assert srs.add_proposal(r["id"], "a@x.com", summary="s", plan="use password=abc123", db_path=db) == (None, "credential-like text")
    assert srs.add_proposal(r["id"], "a@x.com", summary="s", plan="x" * 9000, db_path=db)["status"] == "in_review"
    ev = [e for e in srs.list_events(r["id"], db_path=db) if e["event_type"] == srs.PROPOSAL_EVENT][0]
    assert len(ev["payload"]["plan"]) == srs.PROPOSAL_FIELD_LIMITS["plan"]


# ── API: 역할 격리 ───────────────────────────────────────────────────────────
flask = pytest.importorskip("flask", reason="Flask 미탑재")
pytest.importorskip("jwt", reason="PyJWT 미탑재")
from api.auth import _issue_token  # noqa: E402


def _h(email, role):
    return {"Authorization": f"Bearer {_issue_token(email, role)}"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(srs, "DEFAULT_DB_PATH", tmp_path / "api.db")
    import api.server as s
    return s.app.test_client()


def test_request_agent_can_list_and_propose_but_nothing_else(client):
    rid = client.post("/api/service-requests", json={"title": "새 요청", "body": "b"}, headers=_h("u@x.com", "user")).get_json()["item"]["id"]
    ag = _h("req-agent@x.com", "request_agent")
    pend = client.get("/api/agent/service-requests/pending", headers=ag)
    assert pend.status_code == 200 and [r["id"] for r in pend.get_json()["requests"]] == [rid]
    assert "owner_email" in pend.get_json()["requests"][0] and "admin_note" not in pend.get_json()["requests"][0]
    ok = client.post(f"/api/agent/service-requests/{rid}/proposal", json={"summary": "s", "plan": "p"}, headers=ag)
    assert ok.status_code == 200 and ok.get_json()["status"] == "in_review"
    assert client.post(f"/api/agent/service-requests/{rid}/proposal", json={"summary": "s", "plan": "p"}, headers=ag).status_code == 409
    assert client.post("/api/agent/service-requests/999/proposal", json={"summary": "s", "plan": "p"}, headers=ag).status_code == 404
    assert client.post(f"/api/agent/service-requests/{rid}/proposal", json={"summary": "", "plan": ""}, headers=ag).status_code in (400, 409)
    # 그 밖의 경로는 전부 막힌다(서비스 역할은 자기 라우트 밖 403)
    for method, path in [("get", "/api/admin/service-requests"), ("get", "/api/admin/service-requests/outbox"),
                         ("post", f"/api/admin/service-requests/{rid}/claim"), ("post", f"/api/admin/service-requests/{rid}/resolve"),
                         ("get", "/api/service-requests/mine"), ("get", "/api/analog/search"), ("get", "/api/agent/tasks")]:
        assert getattr(client, method)(path, headers=ag).status_code == 403, path


def test_other_roles_cannot_use_the_agent_routes(client):
    for role in ("user", "price_agent", "agent"):
        assert client.get("/api/agent/service-requests/pending", headers=_h("x@x.com", role)).status_code == 403, role
    assert client.get("/api/agent/service-requests/pending", headers=_h("a@x.com", "admin")).status_code == 200
    assert client.get("/api/agent/service-requests/pending").status_code == 401
