"""작업 원장 담당(owner) 라우팅 — server | aside | hermes (docs/ops/OWNERSHIP.md).

1) 결정적 판정 규칙(agents/agent_tasks/owner.py)  2) store.list_tasks 필터·주석  3) API 역할 경계
(agent=server·hermes, price_agent=aside, admin=전부)  4) 디스패처는 자기 담당만  5) 신선도 과제의 담당별 구체 조치 문구.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.agent_tasks import owner as ow
from agents.agent_tasks import store
from agents.agent_tasks.signals import JOB_RUNBOOK, runbook_line


# ── 1) 판정 규칙 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source,ref,detail,expected", [
    # 해외약가 → aside (런북 힌트·config 여부와 무관하게 먼저)
    ("foreign_search", "US:login", runbook_line(None), "aside"),
    ("foreign_search", "UK:stale_cache", runbook_line(None), "aside"),
    ("scheduler_job", "foreign_config_probe:config:JP:proxy", runbook_line(None), "aside"),
    ("scheduler_job", "foreign_config_probe:config:CH:proxy", runbook_line(None), "aside"),
    ("scheduler_job", "foreign_config_probe:config:scrapling:import", runbook_line(None), "aside"),
    ("scheduler_job", "foreign_price_backfill:2026-10-17", runbook_line(None), "aside"),
    ("domain_freshness", "foreign_price_age:2026-10", runbook_line(None), "aside"),
    ("domain_freshness", "foreign_country_coverage:2026-10", runbook_line(None), "aside"),
    ("price_request", "req-1", "", "aside"),
    ("price_request_worker", "lease", "", "aside"),
    # 서버 런북으로 풀리는 것 → server
    ("scheduler_job", "exchange_rate_refresh:2026-10-17", "x\n" + runbook_line("exchange_rate_refresh"), "server"),
    ("scheduler_job", "competitor_trend_promote:2026-10-17", "x\n" + runbook_line("competitor_trend_promote"), "server"),
    ("domain_freshness", "news_archive:2026-10", "x\n" + runbook_line("news_daily_fetch"), "server"),
    ("domain_freshness", "foreign_price_fx_alignment:2026-10", "x\n" + runbook_line("exchange_rate_recompute"), "server"),
    ("domain_freshness", "stakeholder_monitoring_run:2026-10", "원인 ①\n" + runbook_line("stakeholder_data_sync"), "server"),
    ("daily_mailing_health", "daily_mailing:2026-10-17:3:not_generated", runbook_line("daily_mailing_dispatch"), "server"),
    ("rule_compliance", "rc:project_xnational_reimbursement", runbook_line("reimbursement_xnational_sync"), "server"),
    # 판단·큐레이션·에스컬레이션 → hermes
    ("scheduler_job", "stakeholder_data_sync:config", "토큰 없음\n" + runbook_line("stakeholder_data_sync"), "hermes"),
    ("scheduler_job", "git_auto_backup:2026-10-17", runbook_line(None), "hermes"),
    ("scheduler_job", "platform_smoke:abc123", runbook_line(None), "hermes"),
    ("scheduler_missed", "scheduler_missed:2026-10-17", runbook_line(None), "hermes"),
    ("domain_freshness", "stakeholder_monitoring_run:2026-10", "원인 ②\n" + runbook_line(None), "hermes"),
    ("domain_freshness", "stakeholder_audit_run:2026-10", runbook_line(None), "hermes"),
    ("domain_freshness", "policy_intelligence:2026-10", runbook_line(None), "hermes"),
    ("domain_freshness", "committee_results:2026-10", runbook_line(None), "hermes"),
    ("domain_freshness", "committee_reports:backlog", runbook_line(None), "hermes"),
    ("daily_mailing_health", "daily_mailing:3:gmail_token_invalid", runbook_line(None), "hermes"),
    ("daily_mailing_health", "daily_mailing:2026-10-17:3:held", runbook_line(None), "hermes"),
    ("quality_guard", "qg:suggestion:📦", "- a\n" + runbook_line(None), "hermes"),
    ("quality_guard", "qg:regression:keytruda_nsclc_adj", "런북 없음 — `python -m ...`", "hermes"),
    ("known_debt", "debt:amjilsim_reporters", runbook_line(None), "hermes"),
    ("x", "y", "런북: not_a_registered_runbook", "hermes"),     # 미등록 런북 이름은 server 가 아니다
    ("", None, None, "hermes"),
])
def test_owner_rules(source, ref, detail, expected):
    assert ow.owner_for(source, ref, detail) == expected
    assert ow.task_owner({"source": source, "source_ref": ref, "detail": detail}) == expected


def test_every_scheduler_job_failure_has_a_deterministic_owner():
    """등록 잡의 실패 과제(signals.record_job_failure 형식)는 런북이 있으면 server, 해외약가 잡은 aside, 나머지 hermes."""
    for job_id, runbook in JOB_RUNBOOK.items():
        got = ow.owner_for("scheduler_job", f"{job_id}:2026-10-17", f"err\n{runbook_line(runbook)}")
        if job_id in ow.ASIDE_JOBS:
            assert got == "aside", job_id
        elif runbook:
            assert got == "server", job_id
        else:
            assert got == "hermes", job_id


def test_role_owner_map_is_least_privilege():
    assert ow.ROLE_OWNERS["price_agent"] == ("aside",)
    assert "aside" not in ow.ROLE_OWNERS["agent"]
    assert ow.role_may_handle("admin", "aside") and ow.role_may_handle("admin", "hermes")
    assert not ow.role_may_handle("user", "hermes") and not ow.role_may_handle(None, "server")


def test_dispatcher_owner_constants_match_module():
    from scripts import agent_dispatcher as d
    # 분류 폴백(담당 미상 → hermes)과 VPS 디스패처 기본 선점 범위(hermes+server)는 다른 개념이다
    assert d.OWNERS == ow.OWNERS and ow.DEFAULT_OWNER == "hermes"
    assert d.DEFAULT_OWNERS == ("hermes", "server") and "aside" not in d.DEFAULT_OWNERS


# ── 2) store ────────────────────────────────────────────────────────────────

def _mk(db, source, ref, detail="", kind="ops_incident", title="t"):
    return store.create_task(kind=kind, source=source, source_ref=ref, title=title, detail=detail, db_path=db)


def test_list_tasks_annotates_and_filters_by_owner(tmp_path):
    db = tmp_path / "a.db"
    a = _mk(db, "foreign_search", "US:login", runbook_line(None))
    s = _mk(db, "scheduler_job", "exchange_rate_refresh:2026-10-17", runbook_line("exchange_rate_refresh"))
    h = _mk(db, "domain_freshness", "stakeholder_monitoring_run:2026-10", runbook_line(None))
    rows = store.list_tasks(db_path=db)
    assert {r["id"]: r["owner"] for r in rows} == {a: "aside", s: "server", h: "hermes"}
    assert [r["id"] for r in store.list_tasks(db_path=db, owners=("hermes",))] == [h]
    assert sorted(r["id"] for r in store.list_tasks(db_path=db, owners=("hermes", "server"))) == sorted([h, s])
    with pytest.raises(store.AgentTaskError):
        store.list_tasks(db_path=db, owners=("bogus",))


def test_owner_filter_applies_before_limit(tmp_path):
    """LIMIT 이 필터 전에 걸리면 aside 과제가 앞 페이지에 몰렸을 때 hermes 목록이 빈다."""
    db = tmp_path / "a.db"
    for i in range(5):
        _mk(db, "foreign_search", f"C{i}:login", runbook_line(None))
    h = _mk(db, "known_debt", "debt:x", runbook_line(None))
    assert [r["id"] for r in store.list_tasks(db_path=db, owners=("hermes",), limit=2)] == [h]
    assert len(store.list_tasks(db_path=db, owners=("aside",), limit=2)) == 2


def test_dispatcher_activity_counts_waiting_by_owner(tmp_path):
    db = tmp_path / "a.db"
    _mk(db, "foreign_search", "US:login", runbook_line(None))
    _mk(db, "known_debt", "debt:x", runbook_line(None))
    _mk(db, "quality_guard", "qg:issue:x", runbook_line(None))
    act = store.dispatcher_activity(db_path=db)
    assert act["waiting"] == 3
    assert act["waiting_by_owner"] == {"server": 0, "aside": 1, "hermes": 2}


# ── 3) 신선도 과제의 담당별 구체 조치 ────────────────────────────────────────

def test_hermes_freshness_task_states_concrete_action(tmp_path):
    from agents.agent_tasks import freshness as fr
    db = tmp_path / "a.db"
    r = fr.Freshness("stakeholder_monitoring_run", True, "2026-10-11", "2026-10-15",
                     "sv_monitoring_run MAX(run_date)=2026-10-11. 원인 ②: sync 정상 — 외부 에이전트 미커밋", None, 2)
    tid = fr.record_freshness_problems([r], today=date(2026, 10, 17), db_path=db)[0]
    t = store.get_task(tid, db_path=db)
    assert t["title"].startswith("데이터 신선도 — stakeholder_monitoring_run stale")
    assert "Hermes: stakeholder monitoring run 커밋 필요 (마지막 2026-10-11)" in t["title"]
    assert t["detail"].splitlines()[0].startswith("Hermes: stakeholder monitoring run 커밋 필요 (마지막 2026-10-11)")
    assert "런북 없음" in t["detail"] and ow.task_owner(t) == "hermes"


@pytest.mark.parametrize("domain,observed,needle", [
    ("stakeholder_audit_run", "2026-10-10", "coverage audit run 커밋 필요 (마지막 2026-10-10)"),
    ("policy_intelligence", "2026-06-30T09:00:00+09:00", "KRPIA ingest 재개 필요 (마지막 ingest 2026-06-30)"),
    ("committee_results", "2026-09-03", "committee_results.json 회차 결과 작성·커밋 필요 (최초 미반영 회차 2026-09-03)"),
])
def test_hermes_actions_per_domain(tmp_path, domain, observed, needle):
    from agents.agent_tasks import freshness as fr
    owner, action = fr.owner_action(fr.Freshness(domain, True, observed, "d", "evidence", None))
    assert owner == "hermes" and action.startswith("Hermes: ") and needle in action


def test_config_cause_asks_for_token_not_commit():
    from agents.agent_tasks import freshness as fr
    r = fr.Freshness("stakeholder_monitoring_run", True, "2026-10-11", "d",
                     "head. 원인: sync 설정 문제(최근 실행 …) — 재실행으로 안 풀린다", None)
    _, action = fr.owner_action(r)
    assert "설정 문제 해결 요청" in action and "커밋 필요" not in action


def test_server_freshness_task_keeps_plain_title(tmp_path):
    from agents.agent_tasks import freshness as fr
    db = tmp_path / "a.db"
    r = fr.Freshness("news_archive", True, "2026-10-01", "2026-10-14", "e", "news_daily_fetch")
    assert fr.owner_action(r) == ("server", None)
    t = store.get_task(fr.record_freshness_problems([r], today=date(2026, 10, 17), db_path=db)[0], db_path=db)
    assert t["title"] == "데이터 신선도 — news_archive stale" and ow.task_owner(t) == "server"


def test_aside_freshness_task_names_aside(tmp_path):
    from agents.agent_tasks import freshness as fr
    owner, action = fr.owner_action(fr.Freshness("foreign_price_age", True, "2026-08-01", "d", "e", None))
    assert owner == "aside" and action.startswith("Aside: ")


def test_every_domain_without_runbook_gets_an_action():
    """런북 없는 stale 은 server 가 아니므로 반드시 '담당: 조치' 문구가 붙는다(템플릿 누락 시에도 일반 문구)."""
    from agents.agent_tasks import freshness as fr
    for domain in fr.DOMAIN_CHECKS:
        owner, action = fr.owner_action(fr.Freshness(domain, True, "2026-10-01", "d", "e", None))
        assert owner in ("hermes", "aside") and action and action.split(":", 1)[0] in ("Hermes", "Aside"), domain


# ── 4) 디스패처 ─────────────────────────────────────────────────────────────

class _Client:
    def __init__(self, tasks):
        self.tasks, self.calls, self.owner_arg = tasks, [], None

    def list_tasks(self, status, kind, owner=None):
        self.owner_arg = owner
        return self.tasks

    def claim(self, task_id):
        self.calls.append(("claim", task_id))
        return True

    def run_runbook(self, task_id, name):
        self.calls.append(("runbook", task_id, name))
        return {"ok": True, "summary": "ok"}

    def resolve(self, task_id, status, resolution):
        self.calls.append(("resolve", task_id, status))


def _t(tid, owner, detail="런북: daily_mailing_dispatch"):
    return {"id": tid, "priority": 2, "kind": "ops_incident", "status": "triaged", "detail": detail, "owner": owner}


def test_dispatcher_defaults_to_hermes_and_server_and_never_claims_aside():
    from scripts.agent_dispatcher import Dispatcher
    c = _Client([_t(1, "aside"), _t(2, "hermes"), _t(3, "server")])
    out = Dispatcher(c).run_once()
    assert c.owner_arg == "hermes,server"
    claimed = sorted(x[1] for x in c.calls if x[0] == "claim")
    assert claimed == [2, 3] and out["owners"] == ["hermes", "server"] and out["foreign"] == 1


def test_dispatcher_hermes_only_skips_server_and_aside():
    from scripts.agent_dispatcher import Dispatcher
    c = _Client([_t(1, "aside"), _t(2, "hermes"), _t(3, "server")])
    out = Dispatcher(c, owners=("hermes",)).run_once()
    assert [x[1] for x in c.calls if x[0] == "claim"] == [2] and out["foreign"] == 2


def test_dispatcher_owner_list_includes_server():
    from scripts.agent_dispatcher import Dispatcher
    c = _Client([_t(1, "aside"), _t(2, "hermes"), _t(3, "server")])
    Dispatcher(c, owners=("hermes", "server")).run_once()
    assert c.owner_arg == "hermes,server"
    assert sorted(x[1] for x in c.calls if x[0] == "claim") == [2, 3]


def test_dispatcher_legacy_rows_without_owner_are_still_handled():
    from scripts.agent_dispatcher import Dispatcher
    c = _Client([{"id": 9, "priority": 2, "detail": "런북: daily_mailing_dispatch"}])
    Dispatcher(c).run_once()
    assert ("claim", 9) in c.calls


def test_dispatcher_rejects_unknown_owner(monkeypatch):
    from scripts import agent_dispatcher as d
    with pytest.raises(ValueError):
        d.Dispatcher(_Client([]), owners=("nobody",))
    monkeypatch.setenv("MA_API_BASE", "https://api.test")
    monkeypatch.setenv("MA_AGENT_EMAIL", "a@x")
    monkeypatch.setenv("MA_AGENT_PASSWORD", "pw")
    with pytest.raises(SystemExit):
        d.main(["--dry-run", "--owner", "nobody"])


def test_dispatcher_main_reads_owner_env(monkeypatch):
    from scripts import agent_dispatcher as d
    seen = {}

    def fake_client(base, email, password):
        c = _Client([_t(1, "server")])
        seen["c"] = c
        return c

    monkeypatch.setattr(d, "ApiClient", fake_client)
    monkeypatch.setenv("MA_API_BASE", "https://api.test")
    monkeypatch.setenv("MA_AGENT_EMAIL", "a@x")
    monkeypatch.setenv("MA_AGENT_PASSWORD", "pw")
    monkeypatch.setenv("MA_AGENT_OWNER", "server")
    assert d.main(["--dry-run"]) == 0
    assert seen["c"].owner_arg == "server"


def test_api_client_sends_owner_query():
    import io
    import json
    from scripts.agent_dispatcher import ApiClient
    seen = []

    class _R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        seen.append(req.full_url)
        body = {"token": "T"} if req.full_url.endswith("/login") else {"tasks": []}
        return _R(json.dumps(body).encode())

    c = ApiClient("https://api.test", "a@x", "pw", opener=opener)
    c.list_tasks("new,triaged", "ops_incident", owner="hermes")
    assert seen[-1] == "https://api.test/api/agent/tasks?status=new,triaged&kind=ops_incident&owner=hermes"


# ── 5) API 역할 경계 (Flask — Linux 환경) ────────────────────────────────────

@pytest.fixture
def api(monkeypatch, tmp_path):
    s = pytest.importorskip("api.server")
    from api.auth import _issue_token
    db = tmp_path / "ledger.db"
    monkeypatch.setattr(s, "_AGENT_TASK_DB_PATH", db)
    monkeypatch.setattr(s._agent_runbooks, "run", lambda name: {"ok": True, "summary": f"{name} ok", "detail": {}})

    def h(role, email=None):
        return {"Authorization": f"Bearer {_issue_token(email or f'{role}@msd.com', role)}"}

    ids = {
        "aside": _mk(db, "foreign_search", "US:login", runbook_line(None)),
        "server": _mk(db, "scheduler_job", "exchange_rate_refresh:2026-10-17", runbook_line("exchange_rate_refresh")),
        "hermes": _mk(db, "domain_freshness", "stakeholder_monitoring_run:2026-10", runbook_line(None)),
    }
    return s.app.test_client(), h, ids, db


def test_agent_tasks_list_owner_filter(api):
    c, h, ids, _ = api
    rows = c.get("/api/agent/tasks", headers=h("agent")).get_json()["tasks"]
    assert {r["owner"] for r in rows} == {"aside", "server", "hermes"}     # agent 는 읽기는 전부(필터 없으면 기존 동작)
    rows = c.get("/api/agent/tasks?owner=hermes", headers=h("agent")).get_json()["tasks"]
    assert [r["id"] for r in rows] == [ids["hermes"]]
    assert c.get("/api/agent/tasks?owner=bogus", headers=h("agent")).status_code == 400


def test_price_agent_sees_only_aside(api):
    c, h, ids, _ = api
    rows = c.get("/api/agent/tasks", headers=h("price_agent")).get_json()["tasks"]
    assert [r["id"] for r in rows] == [ids["aside"]] and rows[0]["owner"] == "aside"
    assert c.get("/api/agent/tasks?owner=aside", headers=h("price_agent")).status_code == 200
    assert c.get("/api/agent/tasks?owner=hermes", headers=h("price_agent")).status_code == 403
    assert c.get("/api/agent/tasks?owner=aside,hermes", headers=h("price_agent")).status_code == 403
    assert c.get("/api/agent/tasks", headers=h("user")).status_code == 403


def test_agent_cannot_claim_aside_task(api):
    c, h, ids, db = api
    r = c.post(f"/api/agent/tasks/{ids['aside']}/claim", headers=h("agent"))
    assert r.status_code == 409 and r.get_json()["code"] == "OWNER_MISMATCH" and r.get_json()["owner"] == "aside"
    assert store.get_task(ids["aside"], db_path=db)["status"] == "new"           # 자동 분류도 일어나지 않는다
    assert c.post(f"/api/agent/tasks/{ids['hermes']}/claim", headers=h("agent")).get_json()["claimed"] is True
    assert c.post(f"/api/agent/tasks/{ids['server']}/claim", headers=h("agent")).get_json()["task"]["owner"] == "server"


def test_price_agent_claims_and_resolves_only_aside(api):
    c, h, ids, db = api
    pa = h("price_agent")
    r = c.post(f"/api/agent/tasks/{ids['hermes']}/claim", headers=pa)
    assert r.status_code == 409 and r.get_json()["code"] == "OWNER_MISMATCH"
    r = c.post(f"/api/agent/tasks/{ids['aside']}/claim", headers=pa)
    assert r.status_code == 200 and r.get_json()["claimed"] is True
    # 런북 실행은 서버 런북(agent 전용) — price_agent 는 403
    assert c.post(f"/api/agent/tasks/{ids['aside']}/runbook", headers=pa,
                  json={"name": "exchange_rate_refresh"}).status_code == 403
    r = c.post(f"/api/agent/tasks/{ids['aside']}/resolve", headers=pa,
               json={"status": "closed", "resolution": "Aside: US 재검색 성공"})
    assert r.status_code == 200 and store.get_task(ids["aside"], db_path=db)["status"] == "closed"
    r = c.post(f"/api/agent/tasks/{ids['hermes']}/resolve", headers=pa, json={"status": "rejected"})
    assert r.status_code == 409 and r.get_json()["code"] == "OWNER_MISMATCH"


def test_price_agent_still_confined_from_admin_and_user_routes(api):
    c, h, _, _ = api
    assert c.get("/api/admin/agent-tasks", headers=h("price_agent")).status_code == 403
    assert c.get("/api/mail-subscriptions", headers=h("price_agent")).status_code == 403


def test_admin_ledger_exposes_owner_and_filter(api):
    c, h, ids, _ = api
    body = c.get("/api/admin/agent-tasks", headers=h("admin")).get_json()
    assert body["owners"] == ["server", "aside", "hermes"]
    assert {t["id"]: t["owner"] for t in body["tasks"]} == {ids["aside"]: "aside", ids["server"]: "server",
                                                            ids["hermes"]: "hermes"}
    assert body["activity"]["waiting_by_owner"] == {"server": 1, "aside": 1, "hermes": 1}
    only = c.get("/api/admin/agent-tasks?owner=aside", headers=h("admin")).get_json()["tasks"]
    assert [t["id"] for t in only] == [ids["aside"]]
    detail = c.get(f"/api/admin/agent-tasks/{ids['hermes']}", headers=h("admin")).get_json()
    assert detail["task"]["owner"] == "hermes"
    # admin 은 운영 개입용으로 어느 담당이든 선점할 수 있다
    assert c.post(f"/api/agent/tasks/{ids['aside']}/claim", headers=h("admin")).status_code == 200
