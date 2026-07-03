from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s


def _client_with_stub(monkeypatch, delay: float = 0.2, raise_exc: bool = False):
    async def _stub(query, countries=None):
        await asyncio.sleep(delay)
        if raise_exc:
            raise RuntimeError("boom")
        return {c: [{"local_price": 100}] for c in (countries or [])}
    monkeypatch.setattr(s.foreign_agent, "search_all", _stub)
    return s.app.test_client()


def _poll(client, jid, tries: int = 60):
    final = None
    for _ in range(tries):
        time.sleep(0.05)
        final = client.get(f"/api/foreign/search/status/{jid}").get_json()
        if final["status"] != "running":
            break
    return final


def test_live_search_runs_as_background_job(monkeypatch):
    c = _client_with_stub(monkeypatch)
    r = c.post("/api/foreign/search", json={"query": "Keytruda", "countries": ["JP"], "use_cache": False})
    assert r.status_code == 202
    body = r.get_json()
    assert body["status"] == "running" and body["job_id"]
    final = _poll(c, body["job_id"])
    assert final["status"] == "done"


def test_dedupe_running_job(monkeypatch):
    c = _client_with_stub(monkeypatch, delay=0.6)
    r1 = c.post("/api/foreign/search", json={"query": "Opdivo", "countries": ["JP"], "use_cache": False})
    jid = r1.get_json()["job_id"]
    r2 = c.post("/api/foreign/search", json={"query": "opdivo", "countries": ["JP"], "use_cache": False})
    assert r2.get_json()["job_id"] == jid and r2.get_json().get("reused") is True
    _poll(c, jid)


def test_error_job_reports_error(monkeypatch):
    c = _client_with_stub(monkeypatch, raise_exc=True)
    r = c.post("/api/foreign/search", json={"query": "BadDrug", "countries": ["JP"], "use_cache": False})
    final = _poll(c, r.get_json()["job_id"])
    assert final["status"] == "error" and final.get("error")


def test_unknown_job_returns_404(monkeypatch):
    c = _client_with_stub(monkeypatch)
    r = c.get("/api/foreign/search/status/DOESNOTEXIST")
    assert r.status_code == 404 and r.get_json()["status"] == "unknown"


def test_cache_mode_remains_synchronous(monkeypatch):
    c = _client_with_stub(monkeypatch)
    r = c.post("/api/foreign/search", json={"query": "Keytruda", "use_cache": True})
    assert r.status_code == 200 and r.get_json()["mode"] == "cache"
