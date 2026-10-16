"""T8 — stakeholder_data_sync 잡·런북·신선도 5종·스모크 4화면 (에이전트 팀 규약).

네트워크·LLM 없음: sync 는 가짜(sync_now monkeypatch) 또는 합성 픽스처(source_dir 모드)로만 돈다. DB 는 tmp_path 뿐.
규약: JOB_RUNBOOK 키 == 등록 잡 id, 신선도 판정은 kind(ops_incident|improvement)를 가진다, 정상 상태 ops 과제 0건/일,
반복 신호는 같은 행을 다시 연다(record_recurring). 판정 불가(테이블 부재·한 번도 sync 안 됨)는 과제가 아니다.
"""
from __future__ import annotations

import inspect
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

import scheduler as sch
import scripts.smoke as smoke
from agents.agent_tasks import freshness as fr
from agents.agent_tasks import runbooks
from agents.agent_tasks.signals import JOB_RUNBOOK
from agents.agent_tasks.store import list_tasks

FIX = Path(__file__).parent / "fixtures" / "stakeholder_src_mini"
TODAY = date(2026, 10, 12)
SV_DOMAINS = ("stakeholder_monitoring_run", "stakeholder_audit_run", "stakeholder_proposal_backlog",
              "stakeholder_source_integrity", "stakeholder_quote_provenance")
CONFIG_MSG = "auth required: STAKEHOLDER_SYNC_TOKEN missing (fail-closed in production)"
LOCK_MSG = "stakeholder sync already running (lock held)"


def _sync_result(*, ok: bool, msg: str | None = None, error_class: str | None = None) -> dict:
    """agents.stakeholder.sync.sync() 의 반환 모양 — 실패면 errors 와 error 에 같은 문구가 들어 있다(T5 _early_failure)."""
    res = {"ok": ok, "counts": {"listed": 0 if msg else 370, "downloaded": 0, "changes": 0, "scheduled_applied": 0},
           "errors": [msg] if msg else [], "warnings": ["직책·출처 반영 실패 0건"] if ok else [],
           "error_class": None if ok else error_class}
    if msg:
        res["error"] = msg
    return res


# ── 잡 등록 · 결과 계약 ─────────────────────────────────────────────────────────────

def test_job_registered_guarded_and_runbook_mapped():
    specs = {s.id: s for s in sch.build_job_specs({"day": "1-7", "hour": 9, "minute": 0})}
    s = specs["stakeholder_data_sync"]
    assert getattr(s.fn, "__job_id__") == "stakeholder_data_sync"
    assert JOB_RUNBOOK["stakeholder_data_sync"] == "stakeholder_data_sync"
    assert "stakeholder_data_sync" in runbooks.RUNBOOKS
    fields = {f.name: str(f) for f in s.trigger.fields}
    assert fields["hour"] == "2,8" and fields["minute"] == "20"          # 02:20(reimb 02:00·policy 02:10 뒤) · 08:20(VPS 07:00·07:40 뒤)
    assert s.misfire_grace_time == sch.DAILY_GRACE


def test_job_config_failure_returns_contract_and_keeps_one_config_task(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    monkeypatch.setattr("agents.stakeholder.sync.sync_now",
                        lambda **kw: _sync_result(ok=False, msg=CONFIG_MSG, error_class="config"))
    r1, r2 = sch.stakeholder_data_sync_job(), sch.stakeholder_data_sync_job()
    assert r1 == {"ok": False, "counts": r1["counts"], "errors": [CONFIG_MSG], "error_class": "config"}
    assert r1["counts"]["listed"] == 0 and r2["error_class"] == "config"
    tasks = list_tasks(db_path=db)
    assert [t["source_ref"] for t in tasks] == ["stakeholder_data_sync:config"]       # 매일 새 과제가 아니다
    assert tasks[0]["detail"].count(CONFIG_MSG) == 1                                # error 를 두 번 넘기면 두 번 기록된다
    assert "런북: stakeholder_data_sync" in tasks[0]["detail"]


def test_job_lock_held_is_transient_daily_key(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    monkeypatch.setattr("agents.stakeholder.sync.sync_now",
                        lambda **kw: _sync_result(ok=False, msg=LOCK_MSG, error_class="transient"))
    res = sch.stakeholder_data_sync_job()
    assert res["ok"] is False and res["error_class"] == "transient" and res["errors"] == [LOCK_MSG]
    ref = list_tasks(db_path=db)[0]["source_ref"]
    assert ref.startswith("stakeholder_data_sync:") and ref != "stakeholder_data_sync:config"


def test_job_success_records_run_and_no_task(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    calls: list[dict] = []

    def fake_sync_now(**kw):
        calls.append(kw)
        return _sync_result(ok=True)
    monkeypatch.setattr("agents.stakeholder.sync.sync_now", fake_sync_now)
    res = sch.stakeholder_data_sync_job()
    assert res["ok"] is True and res["errors"] == [] and res["counts"]["listed"] == 370
    assert calls == [{}]                                   # 기본 root(STAKEHOLDER_ROOT)·기본 DB — 원격 모드
    assert list_tasks(db_path=db) == []
    assert sqlite3.connect(db).execute("SELECT job_id, ok FROM scheduler_job_run").fetchall() == [
        ("stakeholder_data_sync", 1)]


def test_job_exception_is_contained(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))

    def boom(**kw):
        raise RuntimeError("sqlite locked")
    monkeypatch.setattr("agents.stakeholder.sync.sync_now", boom)
    res = sch.stakeholder_data_sync_job()
    assert res["ok"] is False and "RuntimeError: sqlite locked" in res["errors"][0]


# ── 런북 · T6 수동 sync 라우트와의 계약 ─────────────────────────────────────────────

def test_runbook_runs_the_guarded_job(monkeypatch):
    called = []
    monkeypatch.setattr(runbooks, "_from_job", lambda fn, label: called.append(fn) or {"ok": True, "summary": label})
    assert runbooks.run("stakeholder_data_sync")["ok"] is True and called == ["stakeholder_data_sync_job"]


@pytest.mark.parametrize("msg,cls,expected", [(CONFIG_MSG, "config", (503, "SYNC_CONFIG")),
                                              (LOCK_MSG, "transient", (409, "SYNC_RUNNING"))])
def test_runbook_result_maps_to_manual_sync_route_status(monkeypatch, tmp_path, msg, cls, expected):
    """T6 /api/admin/stakeholder/sync 는 런북 결과(detail=잡 결과)로 HTTP 상태를 정한다 — config 503, 잠금 409."""
    from api.stakeholder_routes import _resolve_sync_runner, sync_failure_status
    monkeypatch.setenv("AGENT_TASK_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr("agents.stakeholder.sync.sync_now", lambda **kw: _sync_result(ok=False, msg=msg, error_class=cls))
    out = _resolve_sync_runner()(tmp_path / "s.db", tmp_path / "root")
    assert out["ok"] is False and msg in out["summary"]
    assert sync_failure_status(out) == expected


def test_cli_flag_runs_job_once(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(sch, "stakeholder_data_sync_job", lambda: ran.append(1) or {"ok": True, "counts": {"changes": 0},
                                                                                    "errors": [], "error_class": "transient"})
    monkeypatch.setattr(sch, "setup_logging", lambda config: None)
    monkeypatch.setattr(sys, "argv", ["scheduler.py", "--stakeholder-sync-now"])
    sch.main()
    assert ran == [1] and '"changes": 0' in capsys.readouterr().out
    assert '"--stakeholder-sync-now"' in inspect.getsource(sch.main)


# ── 신선도 ──────────────────────────────────────────────────────────────────────

def _seed(db, *sql):
    c = sqlite3.connect(db)
    for s in sql:
        c.executescript(s)
    c.commit()
    c.close()


def _ctx(tmp_path, today=TODAY, ledger=None):
    db = tmp_path / "d.db"
    sqlite3.connect(db).close()
    return fr.Ctx(db_path=db, base_dir=tmp_path, today=today, ledger_path=ledger)


def _ledger(tmp_path, ok: bool, errors: str = "[]", config_task: str | None = None) -> Path:
    led = tmp_path / "ledger.db"
    _seed(led, "CREATE TABLE scheduler_job_run (id INTEGER PRIMARY KEY, job_id TEXT, started_at TEXT, finished_at TEXT, "
               "ok INTEGER, counts TEXT, errors TEXT);"
               f"INSERT INTO scheduler_job_run VALUES (1,'stakeholder_data_sync','2026-10-11T23:20','2026-10-11T23:21',"
               f"{1 if ok else 0},'{{}}','{errors}');")
    if config_task:
        from agents.agent_tasks.store import create_task
        create_task(kind="ops_incident", source="scheduler_job", source_ref="stakeholder_data_sync:config",
                    title="x", detail="x", priority=2, db_path=led)
    return led


def test_freshness_monitoring_and_audit_runs(tmp_path):
    _seed(tmp_path / "d.db", "CREATE TABLE sv_monitoring_run (run_date TEXT); INSERT INTO sv_monitoring_run VALUES ('2026-10-09');",
          "CREATE TABLE sv_audit_run (run_date TEXT); INSERT INTO sv_audit_run VALUES ('2026-10-10');")
    r = fr.check_stakeholder_monitoring_run(_ctx(tmp_path))
    assert r.stale is True and r.deadline == "2026-10-10" and r.runbook == "stakeholder_data_sync" and r.kind == "ops_incident"
    a = fr.check_stakeholder_audit_run(_ctx(tmp_path))
    assert a.stale is False and a.deadline == "2026-10-09" and a.kind == "ops_incident"         # 3일 허용
    assert fr.check_stakeholder_monitoring_run(_ctx(tmp_path, today=date(2026, 10, 11))).stale is False


def test_monitoring_stale_while_sync_healthy_names_external_agent_and_drops_runbook(tmp_path):
    """sync 는 정상인데 런이 안 늘었다 = 외부 에이전트(VPS)가 커밋하지 않음 — 재sync 는 무효(committee_results 선례)."""
    _seed(tmp_path / "d.db", "CREATE TABLE sv_monitoring_run (run_date TEXT); INSERT INTO sv_monitoring_run VALUES ('2026-10-08');")
    r = fr.check_stakeholder_monitoring_run(_ctx(tmp_path, ledger=_ledger(tmp_path, ok=True)))
    assert r.stale is True and r.runbook is None and r.kind == "ops_incident"
    assert "외부 에이전트" in r.evidence and "07:00" in r.evidence


def test_monitoring_stale_while_sync_failing_keeps_runbook_or_points_to_config(tmp_path):
    _seed(tmp_path / "d.db", "CREATE TABLE sv_monitoring_run (run_date TEXT); INSERT INTO sv_monitoring_run VALUES ('2026-10-08');")
    failing = fr.check_stakeholder_monitoring_run(
        _ctx(tmp_path, ledger=_ledger(tmp_path, ok=False, errors='["tree: HTTP 502 Bad Gateway"]')))
    assert failing.stale and failing.runbook == "stakeholder_data_sync" and "HTTP 502" in failing.evidence
    (tmp_path / "ledger.db").unlink()
    config = fr.check_stakeholder_monitoring_run(
        _ctx(tmp_path, ledger=_ledger(tmp_path, ok=False, errors=f'["{CONFIG_MSG}"]', config_task="open")))
    assert config.stale and config.runbook is None and "stakeholder_data_sync:config" in config.evidence


def test_never_synced_store_is_undeterminable_not_a_task(tmp_path):
    """토큰 없이 배포(첫 sync 전): sv_* 는 비어 있고 last_sync_at 도 없다 — 원인은 config 과제 1건이 이미 말한다.
    런·감사 신선도가 같은 원인으로 과제 2건을 더 만들면 배포 당일 ≤3건/일 예산을 혼자 다 쓴다."""
    from agents.stakeholder import store
    store.ensure_tables(tmp_path / "d.db")
    ctx = _ctx(tmp_path)
    for fn in (fr.check_stakeholder_monitoring_run, fr.check_stakeholder_audit_run):
        r = fn(ctx)
        assert r.stale is False and not r.determinable and "sync" in r.evidence
    store.set_meta("last_sync_at", "2026-10-12T08:20:00+09:00", db_path=tmp_path / "d.db")
    assert fr.check_stakeholder_monitoring_run(ctx).stale is True                     # sync 는 됐는데 런이 0건 — 사실


def test_freshness_backlog_is_improvement_and_integrity_is_incident(tmp_path):
    _seed(tmp_path / "d.db", "CREATE TABLE sv_audit_proposal (status TEXT, created_at TEXT);"
          "INSERT INTO sv_audit_proposal VALUES ('needs_review','2026-10-01T07:00:00+09:00');"
          "INSERT INTO sv_audit_proposal VALUES ('needs_review','2026-10-11T07:00:00+09:00');",
          "CREATE TABLE sv_source (source_id TEXT, integrity_status TEXT); INSERT INTO sv_source VALUES ('S9','mismatch');")
    b = fr.check_stakeholder_proposal_backlog(_ctx(tmp_path))
    assert b.stale and b.kind == "improvement" and b.runbook is None and "1건" in b.evidence
    assert "담당: admin" in b.evidence and "승인·반려" in b.evidence
    i = fr.check_stakeholder_source_integrity(_ctx(tmp_path))
    assert i.stale and i.kind == "ops_incident" and i.runbook == "stakeholder_data_sync" and i.priority == 3


def test_backlog_counts_conflicts_awaiting_acknowledgement(tmp_path):
    _seed(tmp_path / "d.db", "CREATE TABLE sv_audit_proposal (proposal_id TEXT, status TEXT, created_at TEXT, conflicts_json TEXT);"
          "INSERT INTO sv_audit_proposal VALUES ('P1','needs_review','2026-10-01T07:00:00+09:00','[{\"x\":1},{\"y\":2}]');"
          "INSERT INTO sv_audit_proposal VALUES ('P2','needs_evidence','2026-10-02T07:00:00+09:00','[]');"
          "INSERT INTO sv_audit_proposal VALUES ('P3','rejected','2026-09-01T07:00:00+09:00','[{\"z\":1}]');")
    b = fr.check_stakeholder_proposal_backlog(_ctx(tmp_path))
    assert b.stale and "대기 2건" in b.evidence and "충돌 확인 대기 1건" in b.evidence
    fresh = fr.check_stakeholder_proposal_backlog(_ctx(tmp_path, today=date(2026, 10, 8)))
    assert fresh.stale is False                                                      # 7일 안 — 아직 적체 아님


def test_integrity_no_local_is_source_state_not_failure_and_artifacts_count(tmp_path):
    _seed(tmp_path / "d.db", "CREATE TABLE sv_source (source_id TEXT, integrity_status TEXT);"
          "INSERT INTO sv_source VALUES ('S1','verified'),('S2','no_local');")
    ok = fr.check_stakeholder_source_integrity(_ctx(tmp_path))
    assert ok.stale is False and "no_local 1건" in ok.evidence
    _seed(tmp_path / "d.db", "CREATE TABLE sv_artifact (path TEXT, integrity_status TEXT);"
          "INSERT INTO sv_artifact VALUES ('x.svg','missing');")
    bad = fr.check_stakeholder_source_integrity(_ctx(tmp_path))
    assert bad.stale and bad.observed == "1" and "x.svg" in bad.evidence and "외부 에이전트" in bad.evidence


def test_integrity_excluded_is_counted_separately_not_failure(tmp_path):
    """manifest 가 등재한 허용목록 밖 산출물(stakeholder_service.py·dashboard-sketches/…)은 excluded — ops 과제가 아니다."""
    _seed(tmp_path / "d.db", "CREATE TABLE sv_source (source_id TEXT, integrity_status TEXT);"
          "INSERT INTO sv_source VALUES ('S1','verified');"
          "CREATE TABLE sv_artifact (path TEXT, integrity_status TEXT);"
          "INSERT INTO sv_artifact VALUES ('a.md','verified'),('stakeholder_service.py','excluded'),"
          "('dashboard-sketches/001-leadership-timeline/index.html','excluded');")
    r = fr.check_stakeholder_source_integrity(_ctx(tmp_path))
    assert r.stale is False and r.runbook is None and r.observed == "0"
    assert "excluded 2건" in r.evidence and "stakeholder_service.py" in r.evidence and "허용목록 밖" in r.evidence


def test_quote_provenance_none_is_improvement_owned_by_external_agent(tmp_path):
    _seed(tmp_path / "d.db", "CREATE TABLE sv_quote (quote_id TEXT, event_id TEXT, speaker_name TEXT, locator TEXT, locator_match TEXT);"
          "INSERT INTO sv_quote VALUES ('Q1','E1','홍길동','sources/a.txt:3-5','range'),"
          "('Q2','E2','김가상','sources/b.txt:9-12','none'),('Q3','E3','이가상',NULL,'unchecked');")
    q = fr.check_stakeholder_quote_provenance(_ctx(tmp_path))
    assert q.stale and q.kind == "improvement" and q.runbook is None and q.priority == 3
    assert q.observed == "1" and "sources/b.txt:9-12" in q.evidence and "외부 에이전트" in q.evidence
    _seed(tmp_path / "d.db", "UPDATE sv_quote SET locator_match='file' WHERE quote_id='Q2';")
    assert fr.check_stakeholder_quote_provenance(_ctx(tmp_path)).stale is False     # unchecked 는 판정 대상 아님


def test_domain_checks_registered():
    for d in SV_DOMAINS:
        assert d in fr.DOMAIN_CHECKS


def test_missing_tables_are_undeterminable(tmp_path):
    results = {r.domain: r for r in fr.run_all(_ctx(tmp_path))}
    for d in SV_DOMAINS:
        assert results[d].stale is False and not results[d].determinable, d


# ── 예산 리허설: 합성 픽스처를 실제 sync 경로로 적재 → 판정 2회 ─────────────────────────────

def _synced_fixture(tmp_path) -> Path:
    from agents.stakeholder import sync
    db = tmp_path / "sv.db"
    res = sync.sync(tmp_path / "root", db, source_dir=FIX, today=TODAY)
    assert res["ok"], res["errors"]
    return db


def test_rehearsal_healthy_snapshot_adds_no_ops_task_and_second_run_adds_zero(tmp_path):
    data = _synced_fixture(tmp_path)
    ledger = tmp_path / "ledger.db"
    res1 = fr.run_and_record(db_path=ledger, data_db_path=data, base_dir=tmp_path, today=TODAY)
    by = {r["domain"]: r for r in res1["results"]}
    assert all(by[d]["stale"] is False for d in SV_DOMAINS if d != "stakeholder_quote_provenance"), \
        {d: by[d]["evidence"] for d in SV_DOMAINS}
    ops = [t for t in list_tasks(db_path=ledger) if t["kind"] == "ops_incident" and "stakeholder" in t["source_ref"]]
    assert ops == []
    res2 = fr.run_and_record(db_path=ledger, data_db_path=data, base_dir=tmp_path, today=TODAY)
    assert res2["counts"]["tasks_new"] == 0


def test_rehearsal_week_later_backlog_is_one_improvement_row(tmp_path):
    data = _synced_fixture(tmp_path)
    ledger = tmp_path / "ledger.db"
    later = date(2026, 10, 20)
    fr.run_and_record(db_path=ledger, data_db_path=data, base_dir=tmp_path, today=later)
    fr.run_and_record(db_path=ledger, data_db_path=data, base_dir=tmp_path, today=later)
    refs = [t["source_ref"] for t in list_tasks(db_path=ledger) if t["source_ref"].startswith("stakeholder_")]
    assert refs.count("stakeholder_proposal_backlog:backlog") == 1
    assert "stakeholder_monitoring_run:2026-10" in refs and refs.count("stakeholder_monitoring_run:2026-10") == 1


# ── 스모크 ──────────────────────────────────────────────────────────────────────

def test_smoke_screens_added():
    names = {s.name for s in smoke.SCREENS}
    assert {"stakeholder-signals", "stakeholder-signals/workbench", "admin/stakeholder-people",
            "admin/stakeholder-audit"} <= names
    gates = {c.path for s in smoke.SCREENS if s.name == "auth-gates" for c in s.checks}
    assert "/api/admin/stakeholder/proposals" in gates and "/api/agent/stakeholder/registry" in gates
    assert "/api/stakeholder/sources" in gates and "/api/stakeholder/runs?kind=monitoring&limit=1" in gates


def test_smoke_hidden_key_set_matches_api_scrub_set():
    from api.stakeholder_routes import USER_HIDDEN_KEYS
    assert smoke.USER_HIDDEN_KEYS == USER_HIDDEN_KEYS


def test_user_safe_expectation_finds_hidden_keys_at_any_depth():
    expect = smoke._user_safe("items")
    assert expect(200, {"items": [{"source_id": "S1", "sha256": "ab"}]}) is None
    assert expect(200, {"items": [{"payload": {"scope": [{"local_path": "sources/x"}]}}]}) == \
        "사용자 응답에 숨김 키 노출 ['local_path']"
    assert expect(200, {"rows": []}) == "HTTP 200 / 'items' 없음"
    assert expect(403, {"error": "x"}) == "HTTP 403"


def _sv_app(tmp_path):
    from flask import Flask
    from api.stakeholder_routes import build_stakeholder_blueprint
    db = _synced_fixture(tmp_path)
    app = Flask("sv-smoke")
    app.register_blueprint(build_stakeholder_blueprint(db_path=db, root=tmp_path / "root"))
    return app


def _flask_opener(app):
    import io
    import urllib.error
    client = app.test_client()

    def opener(req, timeout=10):
        path = req.full_url.split("http://test", 1)[1]
        resp = client.open(path, method=req.get_method(), headers=dict(req.header_items()))
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, resp.status_code, "err", {}, io.BytesIO(resp.get_data()))
        out = io.BytesIO(resp.get_data())
        out.status = resp.status_code
        return out
    return opener


def _sv_screens():
    sv_paths = ("/api/stakeholder/", "/api/admin/stakeholder/", "/api/agent/stakeholder/")
    out = []
    for s in smoke.SCREENS:
        checks = tuple(c for c in s.checks if c.path.startswith(sv_paths))
        if checks:
            out.append(smoke.Screen(s.name, s.role, checks))
    return tuple(out)


def test_stakeholder_smoke_against_real_blueprint_with_synced_fixture(monkeypatch, tmp_path):
    """실제 Blueprint + 합성 픽스처(실제 sync 로 적재): user 토큰은 숨김 키 0(런 상세 payload 포함), 게이트 403, 화면 키 존재."""
    from api.auth import _issue_token
    monkeypatch.setattr(smoke, "SCREENS", _sv_screens())
    opener = _flask_opener(_sv_app(tmp_path))
    res = smoke.run_smoke("http://test", lambda role: _issue_token(f"{role}@x.test", role) if role else None, opener=opener)
    assert res["ok"] is True, res["errors"]
    assert res["counts"]["skipped"] == 0 and res["counts"]["passed"] == sum(len(s.checks) for s in smoke.SCREENS)


def test_user_safe_check_bites_when_admin_token_is_used_for_user_role(monkeypatch, tmp_path):
    """외부 admin 로그인 모드처럼 user 역할에 admin 토큰을 주면 원본 경로가 보인다 — 검사가 실제로 잡는다(런 상세 포함)."""
    from api.auth import _issue_token
    monkeypatch.setattr(smoke, "SCREENS", tuple(s for s in _sv_screens() if s.name == "auth-gates"))
    opener = _flask_opener(_sv_app(tmp_path))
    res = smoke.run_smoke("http://test", lambda role: _issue_token("admin@x.test", "admin") if role else None, opener=opener)
    errs = res["errors"]
    assert "auth-gates /api/stakeholder/sources → 사용자 응답에 숨김 키 노출 ['local_path', 'text_path']" in errs
    detail = [e for e in errs if e.startswith("auth-gates /api/stakeholder/runs?kind=monitoring&limit=1 → 상세: ")]
    assert len(detail) == 1 and "사용자 응답에 숨김 키 노출" in detail[0] and "'local_path'" in detail[0]
    assert "auth-gates /api/agent/stakeholder/registry → HTTP 200 (기대 403)" in errs     # admin 은 agent 라우트도 통과
