"""화면별 스모크 (P1.5 Task 4) — 화면 테이블 커버리지, 체크 단위 실패 보고, 토큰 부재 skipped, 하루 1건 과제.

네트워크 금지: HTTP 는 전부 가짜 opener 또는 Flask test_client 어댑터로 간다. 이 worktree 의 플랫폼 DB 는 빈 stub 이라
"데이터가 있다" 는 단정할 수 없다 — 검사기의 논리(집계·형식·권한 게이트·로그인 게이트)만 실제 라우트로 확인한다.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import smoke


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """실제 소켓 연결 금지 — 스모크가 치는 라우트 중 캐시가 비면 외부(NICE·Naver·LLM)로 나가는 것이 있어 가짜 서버/test_client
    바깥으로는 한 바이트도 못 나가게 한다(핫스팟에서 2분 걸리던 원인)."""
    def _blocked(self, *a, **kw):
        raise OSError("network blocked by test guard")
    import socket
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)


def _response(status: int, raw: bytes, url: str):
    if status >= 400:
        raise urllib.error.HTTPError(url, status, "err", {}, io.BytesIO(raw))
    resp = io.BytesIO(raw)          # BytesIO 는 이미 context manager — with 문에서 그대로 쓰인다
    resp.status = status
    return resp


def _fake_opener(routes: dict):
    """path → (status, body). 등록 안 된 경로는 404."""
    def opener(req, timeout=10):
        path = req.full_url.split("http://test", 1)[1]
        status, body = routes.get(path, (404, {"error": "nf"}))
        return _response(status, json.dumps(body).encode(), req.full_url)
    return opener


def _token_for(role):
    return f"{role}-token" if role else None


# ── 화면 테이블 ─────────────────────────────────────────────────────────────

def test_screen_table_covers_all_routes():
    names = {s.name for s in smoke.SCREENS}
    user = {"login", "home", "domestic-pricing", "regimen-cost", "international-pricing", "market-share",
            "competitor-trends", "daily-mailing", "reimbursement-status", "policy-intelligence", "analog-search", "my-requests"}
    admin = {"admin/market-share", "admin/msd-pipeline", "admin/competitor-trends", "admin/reimbursement",
             "admin/reimbursement-pipeline", "admin/daily-mailing-kanban", "admin/home-brands",
             "admin/service-requests", "admin/agent-tasks"}
    assert user <= names and admin <= names and "auth-gates" in names
    assert len(names) == len(smoke.SCREENS) == 22
    assert sum(len(s.checks) for s in smoke.SCREENS) >= 44           # 계획 완료 기준(T4): 기대 조건 44개 이상
    assert all(s.role in (None, "user", "admin") for s in smoke.SCREENS)


# ── run_smoke ───────────────────────────────────────────────────────────────

def _ok_routes() -> dict:
    routes = {c.path: (200, {"tasks": []} if "agent-tasks" in c.path else []) for s in smoke.SCREENS for c in s.checks}
    routes["/api/auth/me"] = (401, {"error": "missing token"})
    routes["/api/admin/agent-tasks"] = (200, {"tasks": []})
    return routes


_GATED_ANON = ("/api/auth/me", "/api/competitor-trends", "/api/admin/")     # 토큰 없이 → 401 (실서버 require_auth)


def _gated_opener(routes: dict, *, anon_gate: bool = True, admin_gate: bool = True):
    """실서버의 권한 게이트를 흉내내는 가짜 서버: 토큰 없이 보호 라우트 → 401, user 토큰으로 /api/admin/* → 403.
    게이트를 끄면(anon_gate/admin_gate=False) '인증이 뚫린 서버' 가 된다 — 스모크가 그것을 잡는지 본다."""
    def opener(req, timeout=10):
        path = req.full_url.split("http://test", 1)[1]
        auth = req.get_header("Authorization") or ""
        if anon_gate and not auth and path.startswith(_GATED_ANON):
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b'{"error":"missing token"}'))
        if admin_gate and path.startswith("/api/admin/") and auth == "Bearer user-token":
            raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, io.BytesIO(b'{"error":"admin required"}'))
        return _fake_opener(routes)(req, timeout)
    return opener


def test_run_smoke_all_green():
    res = smoke.run_smoke("http://test", _token_for, opener=_gated_opener(_ok_routes()))
    assert res["ok"] is True and res["counts"]["failed"] == 0 and res["errors"] == []
    assert res["counts"]["skipped"] == 0
    assert res["counts"]["passed"] == sum(len(s.checks) for s in smoke.SCREENS)
    assert res["counts"]["screens"] == len(smoke.SCREENS)


def test_run_smoke_reports_failures_per_check():
    broken = _ok_routes()
    broken["/api/policy-intelligence/overview"] = (500, {"error": "manifest not found"})
    res = smoke.run_smoke("http://test", _token_for, opener=_gated_opener(broken))
    assert res["ok"] is False and res["counts"]["failed"] == 1
    assert res["errors"] == ["policy-intelligence /api/policy-intelligence/overview → HTTP 500"]


def test_run_smoke_reports_missing_key_and_wrong_status():
    broken = _ok_routes()
    broken["/api/admin/agent-tasks"] = (200, {"items": []})          # admin 화면: 'tasks' 키 없음
    broken["/api/auth/me"] = (200, {"user": {}})                     # login 게이트: 토큰 없이 200 이면 인증이 뚫린 것
    res = smoke.run_smoke("http://test", _token_for, opener=_gated_opener(broken, anon_gate=False))
    assert res["ok"] is False
    assert "admin/agent-tasks /api/admin/agent-tasks → HTTP 200 / 'tasks' 없음" in res["errors"]
    assert "login /api/auth/me → HTTP 200 (기대 401)" in res["errors"]


def test_run_smoke_auth_gate_failure_when_user_token_passes_admin_route():
    """권한 경계가 무너지면(user 토큰으로 admin 200) auth-gates 가 잡는다 — 체크마다 1줄."""
    res = smoke.run_smoke("http://test", _token_for, opener=_gated_opener(_ok_routes(), admin_gate=False))
    assert res["ok"] is False
    assert res["errors"] == ["auth-gates /api/admin/agent-tasks → HTTP 200 (기대 403)",
                             "auth-gates /api/admin/users → HTTP 200 (기대 403)"]


def test_run_smoke_skips_when_token_unavailable():
    """토큰을 못 만든 역할의 화면은 skipped 로 세고 실패로 치지 않는다 — 토큰 없이 치는 화면만 돈다."""
    anon_routes = {c.path: (200, []) for s in smoke.SCREENS if s.role is None for c in s.checks}
    res = smoke.run_smoke("http://test", lambda role: None, opener=_gated_opener(anon_routes))
    assert res["counts"]["skipped"] > 0 and all("skipped" not in e for e in res["errors"])
    assert res["counts"]["skipped"] == sum(len(s.checks) for s in smoke.SCREENS if s.role)
    assert res["ok"] is True and res["counts"]["passed"] == sum(len(s.checks) for s in smoke.SCREENS if s.role is None)


def test_run_smoke_transport_error_is_a_check_failure_not_a_crash():
    def opener(req, timeout=10):
        raise ConnectionRefusedError("[Errno 61] Connection refused")
    res = smoke.run_smoke("http://test", lambda role: None, opener=opener)
    assert res["ok"] is False and all("ConnectionRefusedError" in e for e in res["errors"])


# ── 토큰 발급(계정 없이, 같은 프로세스의 JWT_SECRET) ─────────────────────────

def test_mint_token_factory_accepts_when_signature_is_shared():
    """/api/auth/me 가 404(서명 OK, smoke@internal 은 users DB 에 없음)면 토큰 사용. 역할별 1회 probe 후 캐시."""
    calls: list[str] = []

    def opener(req, timeout=10):
        calls.append(req.get_header("Authorization") or "")
        return _response(404, b'{"error":"user not found"}', req.full_url)
    token_for = smoke.mint_token_factory("http://test", opener=opener)
    t1, t2 = token_for("user"), token_for("user")
    assert t1 and t1 == t2 and len(calls) == 1 and calls[0].startswith("Bearer ")
    assert token_for(None) is None
    from api.auth import _decode_token
    payload = _decode_token(t1)
    assert payload["role"] == "user" and payload["sub"] == "smoke@internal"
    assert token_for("admin") and _decode_token(token_for("admin"))["role"] == "admin"


def test_mint_token_factory_returns_none_when_web_rejects_signature():
    """비밀이 공유되지 않으면(로컬에서 JWT_SECRET 없음 → 프로세스별 임시 비밀) 401 → None → 화면 skipped (오탐 아님)."""
    def opener(req, timeout=10):
        return _response(401, b'{"error":"invalid token"}', req.full_url)
    token_for = smoke.mint_token_factory("http://test", opener=opener)
    assert token_for("user") is None and token_for("admin") is None


# ── 실제 라우트: Flask test_client 어댑터 ────────────────────────────────────

def _flask_opener(app):
    client = app.test_client()

    def opener(req, timeout=10):
        path = req.full_url.split("http://test", 1)[1]
        resp = client.open(path, method=req.get_method(), headers=dict(req.header_items()))
        return _response(resp.status_code, resp.get_data(), req.full_url)
    return opener


def test_smoke_against_flask_test_client(monkeypatch, tmp_path):
    """실제 Flask 앱(빈 stub DB)으로 한 바퀴: 집계가 맞고, 로그인·권한 게이트가 통과하며, 같은 프로세스 토큰이 거부되지 않는다.
    데이터 의존 화면의 200/500 은 stub DB 의 사실이라 단정하지 않는다(프로덕션 첫 실행 예측은 계획 §T4)."""
    import api.server as s
    monkeypatch.setattr(s, "_AGENT_TASK_DB_PATH", tmp_path / "a.db")
    opener = _flask_opener(s.app)
    res = smoke.run_smoke("http://test", smoke.mint_token_factory("http://test", opener=opener), opener=opener)
    total = sum(len(sc.checks) for sc in smoke.SCREENS)
    assert res["counts"]["skipped"] == 0                               # 같은 프로세스 → 비밀 공유 → 토큰 발급됨
    assert res["counts"]["passed"] + res["counts"]["failed"] == total
    assert not [e for e in res["errors"] if e.startswith(("login ", "auth-gates ", "admin/agent-tasks "))]
    assert not [e for e in res["errors"] if "HTTP 401" in e or "HTTP 403" in e]   # 역할 매핑이 맞다
    assert all(" → " in e for e in res["errors"])
    print("\nSMOKE(stub DB):", json.dumps(res, ensure_ascii=False))


# ── 스케줄러 잡: 실패해도 하루 1건 ──────────────────────────────────────────

def test_platform_smoke_job_records_one_task_per_day(monkeypatch):
    import scheduler as sch
    from agents.agent_tasks.jobs import recent_job_runs
    from agents.agent_tasks.store import list_tasks
    db = Path(os.environ["AGENT_TASK_DB"])
    bad = {"ok": False, "counts": {"screens": 22, "passed": 43, "failed": 1, "skipped": 0},
           "errors": ["policy-intelligence /api/policy-intelligence/overview → HTTP 500"]}
    monkeypatch.setattr(smoke, "run_smoke", lambda base, token_for, **kw: bad)
    monkeypatch.setattr(smoke, "mint_token_factory", lambda base, **kw: (lambda role: None))
    monkeypatch.setenv("MA_SMOKE_BASE", "http://test")
    r1, r2 = sch.platform_smoke_job(), sch.platform_smoke_job()
    assert r1["ok"] is False and r1["errors"] == bad["errors"] and r2["ok"] is False
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"].startswith("platform_smoke:")
    assert "policy-intelligence /api/policy-intelligence/overview → HTTP 500" in tasks[0]["detail"]
    runs = [r for r in recent_job_runs(db_path=db) if r["job_id"] == "platform_smoke"]
    assert len(runs) == 1 and runs[0]["ok"] == 0
