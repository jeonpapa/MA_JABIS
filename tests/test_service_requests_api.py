"""서비스 보완/개선 요청 API 테스트 — Flask test client + 실 JWT.

api.server import 가 무거워(전체 앱) Flask/jwt 미탑재 환경에서는 스킵.
store DB 는 DEFAULT_DB_PATH monkeypatch 로 tmp sqlite 로 격리 (실 DB 미접촉).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("flask", reason="Flask 미탑재 — API 테스트 스킵 (store 테스트는 별도)")
pytest.importorskip("jwt", reason="PyJWT 미탑재 — API 테스트 스킵 (store 테스트는 별도)")

from agents.service_requests import store as srs  # noqa: E402
from api.auth import _issue_token  # noqa: E402

USER_A = "usera@test.com"
USER_B = "userb@test.com"
ADMIN = "admin@test.com"

FULL_CHECKLIST = {k: True for k in srs.CHECKLIST_KEYS}


def _headers(email: str, role: str = "user") -> dict:
    return {"Authorization": f"Bearer {_issue_token(email, role)}"}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    # 함수들이 db_path 를 호출 시점에 resolve 하므로 monkeypatch 로 실 DB 격리
    monkeypatch.setattr(srs, "DEFAULT_DB_PATH", tmp_path / "sr_api_test.db")
    import api.server as s
    assert s._service_requests_store is not None, "service_requests store import 실패"
    return s.app.test_client()


def _create(client, email=USER_A, **overrides):
    payload = {
        "title": "해외약가 탭 로딩 개선",
        "body": "탭 전환 시 3초 이상 소요",
        "expected_outcome": "1초 내 로딩",
        "request_type": "improvement",
        "priority": "high",
        "page_path": "/foreign",
        "page_label": "해외 약가",
        "context": {"route": "/foreign", "authToken": "should-be-masked"},
    }
    payload.update(overrides)
    return client.post("/api/service-requests", json=payload, headers=_headers(email))


def test_create_requires_auth(client):
    r = client.post("/api/service-requests", json={"title": "x"})
    assert r.status_code == 401


def test_create_and_redaction(client):
    r = _create(client)
    assert r.status_code == 201
    item = r.get_json()["item"]
    assert item["owner_email"] == USER_A
    assert item["status"] == "open"
    assert item["context"]["authToken"] == "[REDACTED]"
    assert item["context"]["route"] == "/foreign"
    # title 누락 → 400
    r2 = client.post("/api/service-requests", json={"title": " "}, headers=_headers(USER_A))
    assert r2.status_code == 400 and r2.get_json()["code"] == "INVALID"


def test_mine_owner_scoped(client):
    _create(client, email=USER_A, title="A 요청")
    _create(client, email=USER_B, title="B 요청")
    r = client.get("/api/service-requests/mine", headers=_headers(USER_A))
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert [i["title"] for i in items] == ["A 요청"]


def test_detail_owner_or_admin_only(client):
    rid = _create(client, email=USER_A).get_json()["item"]["id"]
    # 소유자 OK (+events)
    r = client.get(f"/api/service-requests/{rid}", headers=_headers(USER_A))
    assert r.status_code == 200
    body = r.get_json()
    assert body["item"]["id"] == rid
    assert [e["event_type"] for e in body["events"]] == ["create"]
    # 타 사용자 403
    r2 = client.get(f"/api/service-requests/{rid}", headers=_headers(USER_B))
    assert r2.status_code == 403 and r2.get_json()["code"] == "AUTH_FORBIDDEN"
    # admin OK
    r3 = client.get(f"/api/service-requests/{rid}", headers=_headers(ADMIN, role="admin"))
    assert r3.status_code == 200
    # 미존재 404
    r4 = client.get("/api/service-requests/99999", headers=_headers(USER_A))
    assert r4.status_code == 404


def test_admin_list_requires_admin(client):
    _create(client, email=USER_A)
    r = client.get("/api/admin/service-requests", headers=_headers(USER_A))
    assert r.status_code == 403
    r2 = client.get("/api/admin/service-requests", headers=_headers(ADMIN, role="admin"))
    assert r2.status_code == 200
    assert len(r2.get_json()["items"]) == 1
    # 필터
    r3 = client.get("/api/admin/service-requests?status=sent", headers=_headers(ADMIN, role="admin"))
    assert r3.get_json()["items"] == []


def test_admin_patch(client):
    rid = _create(client).get_json()["item"]["id"]
    admin_h = _headers(ADMIN, role="admin")
    r = client.patch(f"/api/admin/service-requests/{rid}",
                     json={"status": "in_review", "admin_note": "triage"}, headers=admin_h)
    assert r.status_code == 200
    item = r.get_json()["item"]
    assert item["status"] == "in_review" and item["admin_note"] == "triage"
    assert client.patch(f"/api/admin/service-requests/{rid}",
                        json={"status": "bogus"}, headers=admin_h).status_code == 400
    assert client.patch("/api/admin/service-requests/99999",
                        json={"status": "done"}, headers=admin_h).status_code == 404
    # 일반 사용자 403
    assert client.patch(f"/api/admin/service-requests/{rid}",
                        json={"status": "done"}, headers=_headers(USER_A)).status_code == 403


def test_package_confirm_send_flow(client):
    rid = _create(client).get_json()["item"]["id"]
    admin_h = _headers(ADMIN, role="admin")
    # send before confirm → 409
    r = client.post(f"/api/admin/service-requests/{rid}/send-to-claude", headers=admin_h)
    assert r.status_code == 409 and r.get_json()["code"] == "NOT_CONFIRMED"
    # package generate
    r2 = client.post(f"/api/admin/service-requests/{rid}/claude-package",
                     json={"mode": "generate"}, headers=admin_h)
    assert r2.status_code == 200
    md = r2.get_json()["markdown"]
    assert srs.NO_DEPLOY_LINE in md and "SAFETY / REDACTION CHECKLIST" in md
    assert r2.get_json()["item"]["status"] == "packaged"
    # confirm incomplete → 400
    r3 = client.post(f"/api/admin/service-requests/{rid}/confirm",
                     json={"checklist": {"scope_clear": True}}, headers=admin_h)
    assert r3.status_code == 400 and r3.get_json()["code"] == "CHECKLIST_INCOMPLETE"
    # confirm complete → 200
    r4 = client.post(f"/api/admin/service-requests/{rid}/confirm",
                     json={"checklist": FULL_CHECKLIST}, headers=admin_h)
    assert r4.status_code == 200 and r4.get_json()["item"]["status"] == "confirmed"
    # send after confirm → 200 sent
    r5 = client.post(f"/api/admin/service-requests/{rid}/send-to-claude", headers=admin_h)
    assert r5.status_code == 200
    body = r5.get_json()
    assert body["item"]["status"] == "sent" and body["item"]["sent_at"]
    assert body["markdown"] == md
    # 감사 이벤트 전체 확인
    events = client.get(f"/api/service-requests/{rid}",
                        headers=admin_h).get_json()["events"]
    assert [e["event_type"] for e in events] == ["create", "package", "confirm", "send"]


def test_admin_endpoints_blocked_for_user(client):
    rid = _create(client).get_json()["item"]["id"]
    user_h = _headers(USER_A)
    for method, url, payload in (
        ("post", f"/api/admin/service-requests/{rid}/claude-package", {"mode": "generate"}),
        ("post", f"/api/admin/service-requests/{rid}/confirm", {"checklist": FULL_CHECKLIST}),
        ("post", f"/api/admin/service-requests/{rid}/send-to-claude", None),
        ("get", "/api/admin/service-requests/outbox", None),
        ("post", f"/api/admin/service-requests/{rid}/claim", None),
        ("post", f"/api/admin/service-requests/{rid}/resolve",
         {"status": "done", "resolution_note": "x"}),
    ):
        r = getattr(client, method)(url, json=payload, headers=user_h)
        assert r.status_code == 403, url


# ── 위임 루프 (outbox → claim → resolve) ──────────────────────────────────────

def _sent(client) -> int:
    """생성→패키지→확인→sent 까지 진행, request id 반환."""
    rid = _create(client).get_json()["item"]["id"]
    admin_h = _headers(ADMIN, role="admin")
    client.post(f"/api/admin/service-requests/{rid}/claude-package",
                json={"mode": "generate"}, headers=admin_h)
    client.post(f"/api/admin/service-requests/{rid}/confirm",
                json={"checklist": FULL_CHECKLIST}, headers=admin_h)
    r = client.post(f"/api/admin/service-requests/{rid}/send-to-claude", headers=admin_h)
    assert r.status_code == 200
    return rid


def test_outbox_claim_resolve_flow(client):
    admin_h = _headers(ADMIN, role="admin")
    _create(client, title="아직 open")  # outbox 미포함
    rid = _sent(client)
    # outbox 는 sent 만
    r = client.get("/api/admin/service-requests/outbox", headers=admin_h)
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert [i["id"] for i in items] == [rid]
    assert srs.NO_DEPLOY_LINE in items[0]["package_markdown"]
    # claim → in_progress
    r2 = client.post(f"/api/admin/service-requests/{rid}/claim", headers=admin_h)
    assert r2.status_code == 200
    item = r2.get_json()["item"]
    assert item["status"] == "in_progress" and item["claimed_by"] == ADMIN
    # 이중 claim → 409
    r3 = client.post(f"/api/admin/service-requests/{rid}/claim", headers=admin_h)
    assert r3.status_code == 409 and r3.get_json()["code"] == "NOT_CLAIMABLE"
    # outbox 에서 사라짐
    assert client.get("/api/admin/service-requests/outbox",
                      headers=admin_h).get_json()["items"] == []
    # resolve → done + 필드
    r4 = client.post(f"/api/admin/service-requests/{rid}/resolve",
                     json={"status": "done", "resolution_note": "정렬 수정",
                           "commit_ref": "abc1234"}, headers=admin_h)
    assert r4.status_code == 200
    item = r4.get_json()["item"]
    assert item["status"] == "done"
    assert item["resolution_note"] == "정렬 수정"
    assert item["commit_ref"] == "abc1234"
    assert item["resolved_by"] == ADMIN and item["resolved_at"]
    # 감사 이벤트
    events = client.get(f"/api/service-requests/{rid}", headers=admin_h).get_json()["events"]
    assert [e["event_type"] for e in events] == [
        "create", "package", "confirm", "send", "claim", "resolve"]


def test_resolve_validation(client):
    admin_h = _headers(ADMIN, role="admin")
    rid = _sent(client)
    # bad status → 400 INVALID
    r = client.post(f"/api/admin/service-requests/{rid}/resolve",
                    json={"status": "rejected", "resolution_note": "x"}, headers=admin_h)
    assert r.status_code == 400 and r.get_json()["code"] == "INVALID"
    # 빈 note → 400 INVALID (CLI/UI 제출 게이트와 동일 계약)
    r_note = client.post(f"/api/admin/service-requests/{rid}/resolve",
                         json={"status": "done", "resolution_note": "  "}, headers=admin_h)
    assert r_note.status_code == 400 and r_note.get_json()["code"] == "INVALID"
    # 미존재 → 404
    r2 = client.post("/api/admin/service-requests/99999/resolve",
                     json={"status": "done", "resolution_note": "x"}, headers=admin_h)
    assert r2.status_code == 404
    # sent 에서 직접 resolve (claim 생략) OK — wont_fix
    r3 = client.post(f"/api/admin/service-requests/{rid}/resolve",
                     json={"status": "wont_fix", "resolution_note": "정책 충돌"}, headers=admin_h)
    assert r3.status_code == 200 and r3.get_json()["item"]["status"] == "wont_fix"
    # 종결 후 재-resolve → 409
    r4 = client.post(f"/api/admin/service-requests/{rid}/resolve",
                     json={"status": "done", "resolution_note": "again"}, headers=admin_h)
    assert r4.status_code == 409 and r4.get_json()["code"] == "NOT_RESOLVABLE"
