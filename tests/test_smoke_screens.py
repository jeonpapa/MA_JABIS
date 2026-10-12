"""화면별 스모크 (P1.5 Task 4) — 화면 테이블 커버리지, 체크 단위 실패 보고, 토큰 부재 skipped, 하루 1건 과제.

네트워크 금지: HTTP 는 전부 가짜 opener 또는 Flask test_client 어댑터로 간다. 플랫폼 DB 의 내용에 "데이터가 있다" 는
단정하지 않는다 — 검사기의 논리(집계·형식·권한 게이트·로그인 게이트)만 실제 라우트로 확인한다.
실제 DB 쓰기 금지(P1.5 fix D): Flask 한 바퀴는 server.db 를 임시 스키마 DB 로 바꾸고 스모크 헤더로 부수효과를 끈다 —
conftest 의 읽기 전용 가드가 그 밖의 쓰기를 막는다.
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
    user |= {"stakeholder-signals", "stakeholder-signals/workbench"}                    # T8 Stakeholder Signals
    admin |= {"admin/stakeholder-people", "admin/stakeholder-audit"}
    assert user <= names and admin <= names and "auth-gates" in names
    assert len(names) == len(smoke.SCREENS) == 26
    assert sum(len(s.checks) for s in smoke.SCREENS) >= 44           # 계획 완료 기준(T4): 기대 조건 44개 이상
    assert all(s.role in (None, "user", "admin") for s in smoke.SCREENS)


# ── run_smoke ───────────────────────────────────────────────────────────────

PRICED_CACHED = {"query": "keytruda", "results": {"JP": [{"local_price": 1, "adjusted_price_krw": 123}], "US": []}}
# P1.6 불변식을 만족하는 정상 응답 — 3분할 합계, 지난 예상일=overdue, not_applicable 에 날짜 없음, 지난 회차 없음, 미디어 집계 >0
MSD_SUMMARY_OK = {"reimbursed_product_count": 1, "keytruda": {
    "total_indications": 66, "mfds_approved": 60, "reimbursed_indications": 10, "not_reimbursed_indications": 5,
    "unverified_reimbursement": 51, "checklist_covered": 15, "pending_reimbursement": 56, "items": []}}
PIPELINE_OK = {"stages": [{"id": "cancer", "count": 1, "drugs": [{"name": "카보메틱스", "timeline": [
    {"phase": "급여 신청(접수)", "date": None, "status": "upcoming"},
    {"phase": "암질심 통과", "date": None, "expectedDate": "2026-07-02", "expectedStatus": "overdue", "status": "expected"},
    {"phase": "약평위 통과", "date": None, "expectedDate": "2099-11-05", "expectedStatus": "forecast", "status": "expected"},
    {"phase": "건보공단 협상", "date": None, "expectedDate": None, "status": "upcoming"}]}]},
    {"id": "evaluation", "count": 1, "drugs": [{"name": "마운자로", "timeline": [
        {"phase": "암질심 통과", "date": None, "expectedDate": None, "expectedStatus": None, "status": "not_applicable"}]}]},
    {"id": "nhis", "count": 0, "drugs": []}], "completedExcluded": 17}
MEETINGS_OK = {"items": [{"id": 15, "date": "2026-06-04", "status": "COMPLETED"}, {"id": 20, "date": "2099-11-05", "status": "SCHEDULED"}]}
MEDIA_OK = {"updated_at": "x", "days": 14, "brands": [{"brand": "키트루다", "total_count": 12}, {"brand": "옵디보", "total_count": 0}]}


def _ok_routes() -> dict:
    routes = {c.path: (200, {"tasks": []} if "agent-tasks" in c.path else []) for s in smoke.SCREENS for c in s.checks}
    routes["/api/auth/me"] = (401, {"error": "missing token"})
    routes["/api/admin/agent-tasks"] = (200, {"tasks": []})
    routes["/api/foreign/cached?q=keytruda"] = (200, PRICED_CACHED)                 # P1.6: 조정가 있는 국가 1개 이상
    routes["/api/foreign/country-overview?query=keytruda"] = (200, {"product": "keytruda", "countries": []})
    routes["/api/msd/summary"] = (200, MSD_SUMMARY_OK)
    routes["/api/reimbursement/pipeline"] = (200, PIPELINE_OK)
    routes["/api/reimbursement/meetings"] = (200, MEETINGS_OK)
    routes["/api/home/media-intelligence"] = (200, MEDIA_OK)
    routes["/api/admin/daily-mailing/kanban"] = (200, {"runs": [], "articles": []})
    # T8 Stakeholder: 키 존재 체크의 정상 본문 (빈 저장소 모양 — 데이터 나이는 신선도가 본다)
    routes["/api/stakeholder/overview"] = (200, {"kpi": {}, "integrity": {}, "topics": []})
    for p in ("/api/stakeholder/statements?limit=1", "/api/stakeholder/statements?topic=price_reform&grade=A&limit=1",
              "/api/stakeholder/events", "/api/stakeholder/people", "/api/admin/stakeholder/source-domains",
              "/api/stakeholder/runs?kind=audit&limit=1", "/api/stakeholder/sources",
              "/api/stakeholder/runs?kind=monitoring&limit=1"):
        routes[p] = (200, {"items": []})
    routes["/api/admin/stakeholder/proposals"] = (200, {"proposals": []})
    routes["/api/admin/stakeholder/integrity"] = (200, {"summary": {}})
    return routes


_GATED_ANON = ("/api/auth/me", "/api/competitor-trends", "/api/admin/")     # 토큰 없이 → 401 (실서버 require_auth)


def _gated_opener(routes: dict, *, anon_gate: bool = True, admin_gate: bool = True):
    """실서버의 권한 게이트를 흉내내는 가짜 서버: 토큰 없이 보호 라우트 → 401, user 토큰으로 /api/admin/*·/api/agent/* → 403.
    게이트를 끄면(anon_gate/admin_gate=False) '인증이 뚫린 서버' 가 된다 — 스모크가 그것을 잡는지 본다."""
    def opener(req, timeout=10):
        path = req.full_url.split("http://test", 1)[1]
        auth = req.get_header("Authorization") or ""
        if anon_gate and not auth and path.startswith(_GATED_ANON):
            raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, io.BytesIO(b'{"error":"missing token"}'))
        if admin_gate and path.startswith(("/api/admin/", "/api/agent/")) and auth == "Bearer user-token":
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
                             "auth-gates /api/admin/users → HTTP 200 (기대 403)",
                             "auth-gates /api/admin/stakeholder/proposals → HTTP 200 (기대 403)",
                             "auth-gates /api/agent/stakeholder/registry → HTTP 200 (기대 403)"]


def test_run_smoke_skips_when_token_unavailable():
    """토큰을 못 만든 역할의 화면은 skipped 로 세고 실패로 치지 않는다 — 토큰 없이 치는 화면만 돈다."""
    anon_paths = {c.path for s in smoke.SCREENS if s.role is None for c in s.checks}
    anon_routes = {p: v for p, v in _ok_routes().items() if p in anon_paths}      # 토큰 없이 치는 화면의 정상 응답만
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
    """실제 Flask 앱(임시 스키마 DB)으로 한 바퀴: 집계가 맞고, 로그인·권한 게이트가 통과하며, 같은 프로세스 토큰이 거부되지 않는다.
    데이터 의존 화면의 200/500 은 임시 DB 의 사실이라 단정하지 않는다(프로덕션 첫 실행 예측은 계획 §T4).
    부수효과 0: 스모크 헤더 덕에 임시 DB 의 search_log 도 비어 있어야 한다(실제 DB 에 'keytruda' 행을 남기던 사고)."""
    import sqlite3
    import api.server as s
    import agents.gov_agency_clouds as _clouds
    import agents.government_keyword_summary as _gov
    from agents.daily_mailing import storage as _dm_storage
    from agents.db import DrugPriceDB
    api_db = tmp_path / "api.db"
    monkeypatch.setattr(s, "db", DrugPriceDB(api_db))         # 스키마만 있는 임시 DB — 실제 플랫폼 DB 에 쓰지 않는다
    monkeypatch.setattr(s, "_AGENT_TASK_DB_PATH", tmp_path / "a.db")
    monkeypatch.setattr(s, "_daily_mailing_admin_kanban",     # 기본 db_path 가 정의 시점에 실제 DB 로 묶여 있다
                        lambda limit_runs=20, purge=True: _dm_storage.load_admin_kanban(tmp_path / "dm.db", limit_runs=limit_runs, purge=purge))
    monkeypatch.setattr(_gov, "CACHE_DIR", tmp_path)        # 두 Home 라우트의 일자 캐시는 git 추적 디렉터리 — 작업트리에 쓰지 않는다
    monkeypatch.setattr(_clouds, "CACHE_DIR", tmp_path)
    # Stakeholder Blueprint 는 import 시점에 기본 DB 경로(실제 drug_prices.db)를 잡는다 — store.connect 를 임시 DB 로 돌려
    # 실제 DB 에 sv_* DDL 을 시도하지 않게 한다(conftest 의 읽기 전용 가드면 500 이 되고, 가드가 없으면 쓰기다).
    from agents.stakeholder import store as _sv_store
    _real_sv_connect, _sv_db = _sv_store.connect, tmp_path / "sv.db"
    monkeypatch.setattr(_sv_store, "connect", lambda db_path=None: _real_sv_connect(_sv_db))
    monkeypatch.setattr(_sv_store, "_ENSURED", set())
    opener = _flask_opener(s.app)
    res = smoke.run_smoke("http://test", smoke.mint_token_factory("http://test", opener=opener), opener=opener)
    assert sqlite3.connect(api_db).execute("SELECT COUNT(*) FROM search_log").fetchone()[0] == 0
    total = sum(len(sc.checks) for sc in smoke.SCREENS)
    assert res["counts"]["skipped"] == 0                               # 같은 프로세스 → 비밀 공유 → 토큰 발급됨
    assert res["counts"]["passed"] + res["counts"]["failed"] == total
    assert not [e for e in res["errors"] if e.startswith(("login ", "auth-gates ", "admin/agent-tasks "))]
    assert not [e for e in res["errors"] if "stakeholder" in e], res["errors"]   # 빈 sv_* 저장소도 화면 계약(키)은 지킨다
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


# ── P1.6: 해외약가 화면은 '200 + JSON' 으로 통과하지 않는다 — 조정가 있는 국가가 하나는 있어야 한다 ──────

def test_international_pricing_fails_when_cached_has_no_priced_country():
    """감사 상태(US 0행·가격 동결)에서 빈 results 는 200 이라 스모크가 통과했다. 이제 조정가 null 뿐이면 실패."""
    for body in ({"query": "keytruda", "results": {}},
                 {"query": "keytruda", "results": {"JP": [{"local_price": 1, "adjusted_price_krw": None}], "US": []}},
                 {"query": "keytruda", "results": {"JP": [{"local_price": 1}]}}):
        broken = _ok_routes()
        broken["/api/foreign/cached?q=keytruda"] = (200, body)
        res = smoke.run_smoke("http://test", _token_for, opener=_gated_opener(broken))
        assert res["ok"] is False and res["counts"]["failed"] == 1
        assert res["errors"][0].startswith("international-pricing /api/foreign/cached?q=keytruda → ") and "조정가" in res["errors"][0]


def test_international_pricing_checks_country_overview_with_query_param():
    paths = {c.path for sc in smoke.SCREENS if sc.name == "international-pricing" for c in sc.checks}
    assert "/api/foreign/country-overview?query=keytruda" in paths          # 라우트의 실제 파라미터 이름은 query (q 아님)
    broken = _ok_routes()
    broken["/api/foreign/country-overview?query=keytruda"] = (200, {"product": "keytruda"})      # countries 없음
    res = smoke.run_smoke("http://test", _token_for, opener=_gated_opener(broken))
    assert res["errors"] == ["international-pricing /api/foreign/country-overview?query=keytruda → HTTP 200 / 'countries' 없음"]


def test_priced_country_expectation_accepts_one_priced_country():
    assert smoke._priced_country(200, PRICED_CACHED) is None
    assert smoke._priced_country(500, {"error": "x"}) == "HTTP 500"
    assert "조정가" in smoke._priced_country(200, {"results": {"US": []}})


# ── P1.6 불변식: 합쳐진 플랫폼 수정이 되돌아오면 스모크가 잡는다 ─────────────────────────────────────

def _only_error(routes: dict) -> list[str]:
    return smoke.run_smoke("http://test", _token_for, opener=_gated_opener(routes))["errors"]


def test_msd_summary_tristate_must_exist_and_sum_to_total():
    import copy
    broken = _ok_routes()
    bad = copy.deepcopy(MSD_SUMMARY_OK)
    bad["keytruda"]["unverified_reimbursement"] = 0                      # 10+5+0 ≠ 66 — '미확인' 을 다시 삼킨 회귀
    broken["/api/msd/summary"] = (200, bad)
    assert _only_error(broken) == ["home /api/msd/summary → 급여10+미급여5+미확인0=15 ≠ 전체 66"]
    bad2 = copy.deepcopy(MSD_SUMMARY_OK)
    del bad2["keytruda"]["unverified_reimbursement"]
    del bad2["keytruda"]["checklist_covered"]
    broken["/api/msd/summary"] = (200, bad2)
    assert _only_error(broken) == ["home /api/msd/summary → 급여 3분할 키 없음 ['unverified_reimbursement', 'checklist_covered']"]
    bad3 = copy.deepcopy(MSD_SUMMARY_OK)
    bad3["keytruda"]["checklist_covered"] = 66
    broken["/api/msd/summary"] = (200, bad3)
    assert _only_error(broken) == ["home /api/msd/summary → checklist_covered 66 ≠ 급여10+미급여5"]
    assert smoke._msd_summary_tristate(200, {"keytruda": None}) == "HTTP 200 / 'keytruda' 없음"
    assert smoke._msd_summary_tristate(500, {"error": "x"}) == "HTTP 500"


def test_pipeline_past_expected_date_must_be_overdue_and_not_applicable_has_no_date():
    import copy
    broken = _ok_routes()
    bad = copy.deepcopy(PIPELINE_OK)
    bad["stages"][0]["drugs"][0]["timeline"][1]["expectedStatus"] = "forecast"     # 2026-07-02 를 예보처럼
    bad["stages"][1]["drugs"][0]["timeline"][0]["expectedDate"] = "2026-11-11"     # 비항암에 암질심 예상일
    broken["/api/reimbursement/pipeline"] = (200, bad)
    assert _only_error(broken) == ["reimbursement-status /api/reimbursement/pipeline → "
                                   "지난 expectedDate 인데 expectedStatus≠overdue 1건 ['암질심 통과']; not_applicable 단계에 날짜 1건 ['암질심 통과']"]
    # 어제 날짜는 서버 date.today() 와의 하루 어긋남을 허용 — 오탐 없음
    from datetime import date, timedelta
    tol = copy.deepcopy(PIPELINE_OK)
    tol["stages"][0]["drugs"][0]["timeline"][1].update(expectedDate=(date.today() - timedelta(days=1)).isoformat(), expectedStatus="forecast")
    assert smoke._pipeline_expectations(200, tol) is None
    assert smoke._pipeline_expectations(200, {"completedExcluded": 0}) == "HTTP 200 / 'stages' 없음"


def test_meetings_past_scheduled_fails_with_count_and_yields_one_platform_smoke_task(tmp_path):
    """오늘 실제 DB 로는 실패하는 것이 맞다(지난 SCHEDULED 7건). 문구는 건수+최초 날짜뿐이라 세션이 늘어도 과제 1건."""
    from agents.agent_tasks.signals import record_job_failure
    from agents.agent_tasks.store import list_tasks
    broken = _ok_routes()
    broken["/api/reimbursement/meetings"] = (200, {"items": [
        {"id": 16, "date": "2026-07-02", "status": "SCHEDULED"}, {"id": 5, "date": "2026-07-08", "status": "SCHEDULED"},
        {"id": 17, "date": "2026-08-06", "status": "SCHEDULED"}, {"id": 15, "date": "2026-06-04", "status": "COMPLETED"},
        {"id": 20, "date": "2099-11-05", "status": "SCHEDULED"}]})
    errs = _only_error(broken)
    assert errs == ["reimbursement-status /api/reimbursement/meetings → 14일 넘게 지난 SCHEDULED 세션 3건 — 결과 미반영(최초 2026-07-02)"]
    db = tmp_path / "a.db"
    a = record_job_failure("platform_smoke", "; ".join(errs), errors=errs, db_path=db)
    more = ["reimbursement-status /api/reimbursement/meetings → 14일 넘게 지난 SCHEDULED 세션 7건 — 결과 미반영(최초 2026-07-02)"]
    b = record_job_failure("platform_smoke", "; ".join(more), errors=more, db_path=db)
    assert a == b and len(list_tasks(db_path=db)) == 1                     # 건수만 바뀌면 같은 과제
    assert smoke._meetings_results_ingested(200, {"items": []}) is None
    assert smoke._meetings_results_ingested(200, {"meetings": []}) == "HTTP 200 / 'items' 없음"


def test_media_intelligence_requires_error_free_nonempty_aggregate():
    broken = _ok_routes()
    broken["/api/home/media-intelligence"] = (200, {"brands": [{"brand": "키트루다", "total_count": 3}], "error": "네이버 수집 실패 3/12 브랜드"})
    assert _only_error(broken) == ["home /api/home/media-intelligence → error 키 있음(네이버 수집 실패 — 캐시 미기록·미서빙 규약대로 실패가 노출됨)"]
    broken["/api/home/media-intelligence"] = (200, {"brands": [{"brand": "키트루다", "total_count": 0}, {"brand": "옵디보", "total_count": 0}]})
    assert _only_error(broken) == ["home /api/home/media-intelligence → 전 브랜드 기사 0건(2개 브랜드) — 오염/빈 집계(looks_poisoned 모양)"]
    broken["/api/home/media-intelligence"] = (200, {"brands": []})
    assert _only_error(broken) == ["home /api/home/media-intelligence → 브랜드 0개(모니터링 목록 비어 있음)"]
    from agents.media_intelligence import looks_poisoned
    assert looks_poisoned({"brands": [{"brand": "a", "total_count": 0}]}) and smoke._media_intelligence_live(200, MEDIA_OK) is None


def test_kanban_requires_runs_key():
    broken = _ok_routes()
    broken["/api/admin/daily-mailing/kanban"] = (200, {"items": []})
    assert _only_error(broken) == ["admin/daily-mailing-kanban /api/admin/daily-mailing/kanban → HTTP 200 / 'runs' 없음"]
    broken["/api/admin/daily-mailing/kanban"] = (503, {"error": "모듈 미탑재", "runs": []})
    assert _only_error(broken) == ["admin/daily-mailing-kanban /api/admin/daily-mailing/kanban → HTTP 503 / 'runs' 없음"]


def test_smoke_source_no_longer_claims_kanban_purge_is_skipped_for_smoke():
    """칸반 GET 은 헤더와 무관하게 purge 하지 않는다 — 문서·주석이 '만료 purge 를 건너뛴다' 고 말하면 안 된다."""
    src = Path(smoke.__file__).read_text(encoding="utf-8")
    assert "INSERT·만료 purge" not in src and "만료 purge·" not in src
    assert "daily_mailing_health_job" in src                               # 어디로 갔는지 적혀 있다
