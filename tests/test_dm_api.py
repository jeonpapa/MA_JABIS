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
    now = datetime(2026, 9, 18, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    deps = dispatch.DispatchDeps(db_path=db, send=lambda **kw: (sent.append(kw), {"ok": True, "sent": True, "gmail_message_id": "m"})[1],
                                 now=lambda: now, gmail_ready=lambda: True)
    monkeypatch.setattr(dispatch.DispatchDeps, "production", classmethod(lambda cls, db_path=None: deps))


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
