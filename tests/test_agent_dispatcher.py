"""VPS 디스패처 — 선점·런북 요청·회신 (계획 Task 7).

네트워크 없음: Dispatcher 에는 가짜 클라이언트를, ApiClient 에는 가짜 opener 를 주입한다.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.agent_dispatcher as mod
from scripts.agent_dispatcher import ApiClient, CLAIMABLE_STATUSES, Dispatcher, main, pick_runbook


class FakeClient:
    """API 호출을 기록하는 가짜 클라이언트."""

    def __init__(self, tasks, runbook_result=None, claim_ok=True):
        self.tasks = tasks
        self.runbook_result = runbook_result or {"ok": True, "summary": "복구 완료"}
        self.claim_ok = claim_ok
        self.calls = []

    def list_tasks(self, status):
        self.calls.append(("list", status))
        return self.tasks

    def claim(self, task_id):
        self.calls.append(("claim", task_id))
        return self.claim_ok

    def run_runbook(self, task_id, name):
        self.calls.append(("runbook", task_id, name))
        return self.runbook_result

    def resolve(self, task_id, status, resolution):
        self.calls.append(("resolve", task_id, status, resolution))


def _task(tid=1, detail="런북: daily_mailing_dispatch", priority=2, status="triaged"):
    return {"id": tid, "title": "Daily Mailing not_generated", "detail": detail,
            "priority": priority, "status": status, "kind": "ops_incident"}


def test_pick_runbook_reads_hint():
    assert pick_runbook(_task()) == "daily_mailing_dispatch"
    assert pick_runbook(_task(detail="런북 없음 — 사람 확인 필요")) is None
    # signals.py 형식: 오류 본문 뒤 줄바꿈 다음에 힌트
    assert pick_runbook(_task(detail="Timeout\n런북: domestic_price_catchup")) == "domestic_price_catchup"
    assert pick_runbook({"detail": None}) is None


def test_claimable_statuses_include_new_signals():
    """signals.py 는 과제를 new 로 만든다 — 디스패처가 new 를 보지 않으면 P1 루프가 시작되지 않는다."""
    assert CLAIMABLE_STATUSES == "new,triaged"


def test_runs_runbook_and_closes():
    client = FakeClient([_task()])
    out = Dispatcher(client).run_once()
    assert client.calls == [("list", "new,triaged"), ("claim", 1),
                            ("runbook", 1, "daily_mailing_dispatch"),
                            ("resolve", 1, "closed", "복구 완료")]
    assert out["handled"] == 1 and out["candidates"] == 1


def test_runbook_failure_blocks_task():
    client = FakeClient([_task()], runbook_result={"ok": False, "summary": "여전히 실패"})
    out = Dispatcher(client).run_once()
    assert client.calls[-1] == ("resolve", 1, "blocked", "여전히 실패")
    assert out["handled"] == 1


def test_task_without_runbook_is_blocked_for_human():
    client = FakeClient([_task(detail="런북 없음 — 사람 확인 필요")])
    out = Dispatcher(client).run_once()
    assert ("runbook", 1, None) not in client.calls
    assert client.calls[-1][0:2] == ("resolve", 1) and client.calls[-1][2] == "blocked"
    assert out["handled"] == 0


def test_lost_claim_is_skipped():
    client = FakeClient([_task()], claim_ok=False)
    out = Dispatcher(client).run_once()
    assert out["handled"] == 0 and ("runbook", 1, "daily_mailing_dispatch") not in client.calls
    assert not any(c[0] == "resolve" for c in client.calls)


def test_dry_run_does_not_mutate():
    client = FakeClient([_task()])
    out = Dispatcher(client, dry_run=True).run_once()
    assert client.calls == [("list", "new,triaged")]
    assert out["planned"] == [{"task_id": 1, "runbook": "daily_mailing_dispatch"}]
    assert out["handled"] == 0


def test_priority_order_and_limit():
    client = FakeClient([_task(1, priority=3), _task(2, priority=1)])
    Dispatcher(client, max_tasks=1).run_once()
    assert ("claim", 2) in client.calls and ("claim", 1) not in client.calls


def test_runbook_exception_blocks_task_instead_of_leaving_it_claimed():
    """런북 요청이 HTTP 오류 등으로 터지면 과제를 in_progress 에 방치하지 않고 blocked 로 회신한다."""

    class Boom(FakeClient):
        def run_runbook(self, task_id, name):
            self.calls.append(("runbook", task_id, name))
            raise RuntimeError("HTTP Error 500")

    client = Boom([_task(), _task(2)])
    out = Dispatcher(client).run_once()
    resolves = [c for c in client.calls if c[0] == "resolve"]
    assert [c[1:3] for c in resolves] == [(1, "blocked"), (2, "blocked")]
    assert "RuntimeError" in resolves[0][3]
    assert out["handled"] == 0 and out["errors"] == 2


# ── ApiClient: 표준 라이브러리 HTTP, 가짜 opener 로 검증 ─────────────────────────

class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.test/x", code, "err", None, io.BytesIO(b"{}"))


def _opener(routes: dict):
    seen = []

    def opener(req, timeout=None):
        key = (req.get_method(), req.full_url.replace("https://api.test", ""))
        seen.append((key, req.get_header("Authorization"), req.data))
        out = routes[key]
        if isinstance(out, Exception):
            raise out
        return _Resp(out)

    opener.seen = seen
    return opener


def test_api_client_logs_in_once_and_sends_bearer():
    op = _opener({("POST", "/api/auth/login"): {"token": "T", "user": {"role": "agent"}},
                  ("GET", "/api/agent/tasks?status=new,triaged"): {"tasks": [{"id": 1}], "policy": {}}})
    c = ApiClient("https://api.test/", "agent@msd.com", "pw", opener=op)
    assert c.list_tasks("new,triaged") == [{"id": 1}]
    (login_key, login_auth, login_body), (list_key, list_auth, _) = op.seen
    assert login_key == ("POST", "/api/auth/login") and login_auth is None
    assert json.loads(login_body) == {"email": "agent@msd.com", "password": "pw"}
    assert list_auth == "Bearer T"


def test_api_client_claim_maps_409_423_to_false_and_raises_others():
    op = _opener({("POST", "/api/auth/login"): {"token": "T"},
                  ("POST", "/api/agent/tasks/1/claim"): _http_error(409),
                  ("POST", "/api/agent/tasks/2/claim"): _http_error(423),
                  ("POST", "/api/agent/tasks/3/claim"): _http_error(500),
                  ("POST", "/api/agent/tasks/4/claim"): {"claimed": True, "task": {}}})
    c = ApiClient("https://api.test", "a", "b", opener=op)
    assert c.claim(1) is False and c.claim(2) is False and c.claim(4) is True
    with pytest.raises(urllib.error.HTTPError):
        c.claim(3)


def test_api_client_runbook_and_resolve_bodies():
    op = _opener({("POST", "/api/auth/login"): {"token": "T"},
                  ("POST", "/api/agent/tasks/7/runbook"): {"result": {"ok": True, "summary": "s"}},
                  ("POST", "/api/agent/tasks/7/resolve"): {"task": {"id": 7, "status": "closed"}}})
    c = ApiClient("https://api.test", "a", "b", opener=op)
    assert c.run_runbook(7, "domestic_price_catchup") == {"ok": True, "summary": "s"}
    c.resolve(7, "closed", "done")
    assert json.loads(op.seen[1][2]) == {"name": "domestic_price_catchup"}
    assert json.loads(op.seen[2][2]) == {"status": "closed", "resolution": "done"}


# ── CLI ────────────────────────────────────────────────────────────────────

def test_main_requires_env(monkeypatch):
    for key in ("MA_API_BASE", "MA_AGENT_EMAIL", "MA_AGENT_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    assert main([]) == 2


def test_main_dry_run_with_injected_client(monkeypatch):
    built = {}

    def fake_client(base, email, password):
        built.update(base=base, email=email, password=password)
        return FakeClient([_task()])

    monkeypatch.setattr(mod, "ApiClient", fake_client)
    monkeypatch.setenv("MA_API_BASE", "https://api.test")
    monkeypatch.setenv("MA_AGENT_EMAIL", "agent@msd.com")
    monkeypatch.setenv("MA_AGENT_PASSWORD", "pw")
    assert main(["--dry-run", "--max-tasks", "2"]) == 0
    assert built == {"base": "https://api.test", "email": "agent@msd.com", "password": "pw"}
