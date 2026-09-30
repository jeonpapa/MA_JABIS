from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s
from agents.daily_mailing import dispatch
from agents.daily_mailing.storage import persist_daily_mailing_run
from api.auth import _issue_token


def _admin():
    return {"Authorization": f"Bearer {_issue_token('admin@msd.com', 'admin')}"}


def _user():
    return {"Authorization": f"Bearer {_issue_token('me@msd.com', 'user')}"}


def test_coerce_delivery_fields():
    out = s._coerce_mail_sub_input({"deliveryMode": "auto", "includeTopMaNews": False})
    assert out == {"delivery_mode": "auto", "include_top_ma_news": 0}
    bad = s._coerce_mail_sub_input({"deliveryMode": "sometimes"})
    assert isinstance(bad, tuple)


def test_row_dict_has_delivery_fields():
    row = (1, "n", "[]", "[]", "Daily", "08:00", None, '["r@msd.com"]', 1, "c", "u", None,
           "[]", "[]", "[]", "[]", "[]", None, "auto", 0)
    item = s._mail_sub_row_to_dict(row)
    assert item["deliveryMode"] == "auto" and item["includeTopMaNews"] is False


def test_scope_for_validation_merges_existing():
    existing = {"name": "파드셉", "keywords": ["약가 인하"], "brands": [], "companies": [],
                "policy_topics": [], "disease_areas": []}
    merged = s._mail_scope_for_validation({"brands": ["파드셉"]}, existing)
    assert merged["keywords"] == ["약가 인하"] and merged["brands"] == ["파드셉"] and merged["name"] == "파드셉"


def _pending_run(db):
    persist_daily_mailing_run({"run_id": "dm_19_x", "generated_at": "2026-09-18T08:00:00+09:00", "keywords": [],
                               "subscription_id": 19, "status": "ready", "run_date": "2026-09-18",
                               "is_test": False, "email_subject": "[MA Daily] 파드셉", "email_html": "<p>brief</p>",
                               "recipients": ["r@msd.com"], "delivery_status": "pending_approval",
                               "approval_status": "pending"}, articles=[], db_path=db)


def _patch_deps(monkeypatch, db, sent):
    from datetime import datetime, timedelta, timezone
    from agents.daily_mailing import storage
    now = datetime(2026, 9, 18, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    deps = dispatch.DispatchDeps(db_path=db, send=lambda **kw: (sent.append(kw), {"ok": True, "sent": True, "gmail_message_id": "m"})[1],
                                 now=lambda: now, gmail_ready=lambda: True)
    monkeypatch.setattr(dispatch.DispatchDeps, "production", classmethod(lambda cls, db_path=None: deps))
    monkeypatch.setattr(storage, "DEFAULT_DB_PATH", db)   # 미리보기는 storage 기본 DB 경로를 쓴다


def _raise(exc):
    raise exc


def test_preview_uses_storage_db_path_not_production_deps(monkeypatch, tmp_path):
    db = tmp_path / "d.db"
    _pending_run(db)
    from agents.daily_mailing import run_store, storage
    monkeypatch.setattr(storage, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(dispatch.DispatchDeps, "production",
                        classmethod(lambda cls, db_path=None: _raise(AssertionError("preview must not build production deps"))))
    c = s.app.test_client()
    r = c.get("/api/admin/daily-mailing/runs/dm_19_x/preview", headers=_admin())
    assert r.status_code == 200 and r.get_json()["html"] == "<p>brief</p>"
    assert c.get("/api/admin/daily-mailing/runs/nope/preview", headers=_admin()).status_code == 404
    monkeypatch.setattr(run_store, "get_run", lambda *a, **k: _raise(RuntimeError("db down")))
    r = c.get("/api/admin/daily-mailing/runs/dm_19_x/preview", headers=_admin())
    assert r.status_code == 500 and "db down" in r.get_json()["error"]


def test_admin_preview_approve_reject_and_health(monkeypatch, tmp_path):
    db = tmp_path / "d.db"
    _pending_run(db)
    sent = []
    _patch_deps(monkeypatch, db, sent)
    c = s.app.test_client()
    assert c.post("/api/admin/daily-mailing/runs/dm_19_x/approve", headers=_user()).status_code == 403
    preview = c.get("/api/admin/daily-mailing/runs/dm_19_x/preview", headers=_admin()).get_json()
    assert preview["html"] == "<p>brief</p>" and preview["subject"] == "[MA Daily] 파드셉"
    r = c.post("/api/admin/daily-mailing/runs/dm_19_x/approve", headers=_admin())
    assert r.status_code == 200 and r.get_json()["run"]["delivery_status"] == "sent"
    assert sent[0]["recipients"] == ["r@msd.com"]
    again = c.post("/api/admin/daily-mailing/runs/dm_19_x/approve", headers=_admin())
    assert again.status_code == 409
    assert c.post("/api/admin/daily-mailing/runs/nope/reject", headers=_admin()).status_code == 404
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE mail_subscription (id INTEGER PRIMARY KEY, owner_email TEXT, name TEXT,
            keywords_json TEXT, media_json TEXT, schedule TEXT, time TEXT, week_day TEXT, emails_json TEXT,
            active INTEGER, companies_json TEXT, brands_json TEXT, policy_topics_json TEXT,
            disease_areas_json TEXT, custom_sources_json TEXT, created_at TEXT, updated_at TEXT, last_sent_at TEXT)""")
    health = c.get("/api/admin/daily-mailing/health", headers=_admin()).get_json()
    assert health["ok"] is True and health["gmail_ready"] is True


_SUB_DDL = """CREATE TABLE mail_subscription (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner_email TEXT NOT NULL, name TEXT NOT NULL,
    keywords_json TEXT NOT NULL, media_json TEXT NOT NULL, schedule TEXT NOT NULL, time TEXT NOT NULL,
    week_day TEXT, emails_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    companies_json TEXT NOT NULL DEFAULT '[]', brands_json TEXT NOT NULL DEFAULT '[]',
    policy_topics_json TEXT NOT NULL DEFAULT '[]', disease_areas_json TEXT NOT NULL DEFAULT '[]',
    custom_sources_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    last_sent_at TEXT, test_request_json TEXT,
    delivery_mode TEXT NOT NULL DEFAULT 'approval', include_top_ma_news INTEGER NOT NULL DEFAULT 1)"""


def test_subscription_crud_no_longer_exports_hermes_scope(monkeypatch, tmp_path):
    """헤르메스 git 버스 retire — 구독 생성/수정/삭제가 스콥 스냅샷 export 를 더 이상 호출하지 않는다."""
    import sqlite3
    db = tmp_path / "s.db"
    with sqlite3.connect(db) as conn:
        conn.execute(_SUB_DDL)
    monkeypatch.setattr(s.db, "_connect", lambda: sqlite3.connect(db))
    calls: list = []
    monkeypatch.setattr(s, "_mail_scope_export", lambda publish: calls.append(publish))
    c = s.app.test_client()
    r = c.post("/api/mail-subscriptions", headers=_user(),
               json={"name": "파드셉", "keywords": ["파드셉"], "schedule": "Daily", "time": "08:00",
                     "emails": ["r@msd.com"]})
    assert r.status_code == 201, r.get_json()
    sid = r.get_json()["item"]["id"]
    assert c.patch(f"/api/mail-subscriptions/{sid}", headers=_user(), json={"time": "09:00"}).status_code == 200
    assert c.delete(f"/api/mail-subscriptions/{sid}", headers=_user()).status_code == 200
    assert calls == []


def test_test_request_runs_in_background(monkeypatch):
    calls = []
    monkeypatch.setattr(s, "_mail_sub_exists", lambda item_id, owner: True)
    monkeypatch.setattr(dispatch.DispatchDeps, "production", classmethod(lambda cls, db_path=None: SimpleNamespace()))
    monkeypatch.setattr(dispatch, "send_test", lambda sid, requester_email, deps: calls.append((sid, requester_email)) or {})
    r = s.app.test_client().post("/api/mail-subscriptions/19/test-request", headers=_user())
    assert r.status_code == 202
    assert "me@msd.com" in r.get_json()["message"]
    for _ in range(50):
        if calls:
            break
        time.sleep(0.02)
    assert calls == [(19, "me@msd.com")]


def _inflight_cleared(sid, tries=100):
    for _ in range(tries):
        if sid not in s._DM_TEST_INFLIGHT:
            return True
        time.sleep(0.02)
    return False


def test_test_request_rejects_second_click_while_first_is_running(monkeypatch):
    """더블클릭/재클릭이 엔진 run 을 두 번 돌려 [TEST] 메일 2통 + LLM·Naver 비용 2배가 되지 않도록 구독별 in-flight 가드."""
    import threading
    started, release = threading.Event(), threading.Event()
    calls: list = []

    def slow_send_test(sid, requester_email, deps):
        calls.append(sid)
        started.set()
        release.wait(5)
        return {}

    monkeypatch.setattr(s, "_mail_sub_exists", lambda item_id, owner: True)
    monkeypatch.setattr(dispatch.DispatchDeps, "production", classmethod(lambda cls, db_path=None: SimpleNamespace()))
    monkeypatch.setattr(dispatch, "send_test", slow_send_test)
    c = s.app.test_client()
    try:
        assert c.post("/api/mail-subscriptions/19/test-request", headers=_user()).status_code == 202
        assert started.wait(2)
        second = c.post("/api/mail-subscriptions/19/test-request", headers=_user())
        assert second.status_code == 409 and second.get_json()["code"] == "IN_PROGRESS"
        assert "생성 중" in second.get_json()["error"]
        assert calls == [19]
    finally:
        release.set()
    assert _inflight_cleared(19)
    assert c.post("/api/mail-subscriptions/19/test-request", headers=_user()).status_code == 202   # 완료 후 재요청 OK
    assert _inflight_cleared(19) and calls == [19, 19]


def test_test_request_guard_is_per_subscription_and_cleared_on_failure(monkeypatch):
    import threading
    release = threading.Event()

    def send_test(sid, requester_email, deps):
        if sid == 19:
            release.wait(5)
            return {}
        raise RuntimeError("engine down")

    monkeypatch.setattr(s, "_mail_sub_exists", lambda item_id, owner: True)
    monkeypatch.setattr(dispatch.DispatchDeps, "production", classmethod(lambda cls, db_path=None: SimpleNamespace()))
    monkeypatch.setattr(dispatch, "send_test", send_test)
    c = s.app.test_client()
    try:
        assert c.post("/api/mail-subscriptions/19/test-request", headers=_user()).status_code == 202
        assert c.post("/api/mail-subscriptions/20/test-request", headers=_user()).status_code == 202  # 다른 구독은 무관
        assert _inflight_cleared(20)                                                                  # 실패해도 finally 로 해제
        assert c.post("/api/mail-subscriptions/20/test-request", headers=_user()).status_code == 202
        assert _inflight_cleared(20)
    finally:
        release.set()
    assert _inflight_cleared(19)
