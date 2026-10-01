# tests/test_scheduler_masked_failures.py
"""잡이 '성공' 로그를 남기며 삼키던 4종 실패가 표준 결과(errors)와 원장 과제로 드러나는지 (P1.5 Task 2).

4종: reimb_data_sync 의 원격 fetch 실패 → 로컬 폴백(INFO 로만 남던 것) · policy_intel_analysis_sync 의 반환 dict `error` 키 ·
foreign_approval_sync 의 `failed` 리스트 · daily_mailing_dispatch 의 `errors` 리스트.
원칙: 데이터는 그대로 적용하되(폴백·부분 성공), 그 사실을 errors 로 반환해 guarded_job 이 과제를 만들게 한다.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import scheduler as sch
from agents.agent_tasks.store import list_tasks

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date().isoformat()


def _db(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    return db


def _install_fake_module(monkeypatch, dotted: str, mod: types.ModuleType) -> None:
    """`from agents import x` / `from agents.ingest import x` 는 sys.modules 보다 **패키지 속성**을 먼저 본다
    (앞선 테스트가 api.server 를 import 하면 실제 모듈이 속성으로 남는다) — 둘 다 바꿔야 실행 순서와 무관하게 가짜가 잡힌다.
    (tests/test_scheduler_job_specs.py:_install_fake_news_modules 와 같은 이유.)"""
    monkeypatch.setitem(sys.modules, dotted, mod)
    parent_name, _, attr = dotted.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None:
        monkeypatch.setattr(parent, attr, mod, raising=False)


# ── reimb_data_sync: 원격 fetch 실패 → 로컬 폴백은 '적용은 하되 실패 신호' ─────────────────

def _fake_reimb_modules(monkeypatch, *, url_fails: bool):
    imp = types.ModuleType("agents.ingest.reimb_committee_import")

    def load_payload(source=None):
        if source is not None and url_fails:
            raise RuntimeError("HTTP 404")
        return {"drugs": [], "events": []}
    imp.load_payload = load_payload
    imp.payload_hash = lambda payload: "h" + str(len(str(payload)))
    imp.run = lambda payload: {"drugs_after": 65, "events_total": 120, "events_added": 0}
    rr = types.ModuleType("agents.reimb_reports")
    rr.backfill_blobs = lambda: {}
    rr.load_reports_manifest = lambda source=None: {"reports": []}
    rr.sync_reports = lambda manifest: {"reports_in_manifest": 0, "ingested": [], "skipped": 0, "errors": []}
    _install_fake_module(monkeypatch, "agents.ingest.reimb_committee_import", imp)
    _install_fake_module(monkeypatch, "agents.reimb_reports", rr)


def test_reimb_sync_fallback_is_reported_but_data_still_applied(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "BASE_DIR", tmp_path)            # .last_applied_hash 를 임시 경로에
    monkeypatch.setenv("REIMB_DATA_URL", "https://raw.example/committee_results.json")
    monkeypatch.delenv("REPORTS_DATA_URL", raising=False)
    _fake_reimb_modules(monkeypatch, url_fails=True)
    res = sch.reimb_data_sync_job()
    assert res["ok"] is False
    assert res["counts"]["data"] == {"drugs_after": 65, "events_total": 120, "events_added": 0}   # 폴백 데이터는 적용됨
    assert "로컬 이미지 폴백" in res["errors"][0] and "HTTP 404" in res["errors"][0]
    assert (tmp_path / "data" / "reimb" / ".last_applied_hash").exists()
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1 and tasks[0]["source_ref"] == f"reimb_data_sync:{TODAY}"


def test_reimb_sync_without_fallback_is_clean(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "BASE_DIR", tmp_path)
    monkeypatch.setenv("REIMB_DATA_URL", "https://raw.example/committee_results.json")
    monkeypatch.delenv("REPORTS_DATA_URL", raising=False)
    _fake_reimb_modules(monkeypatch, url_fails=False)
    res = sch.reimb_data_sync_job()
    assert res["ok"] is True and res["errors"] == []
    assert list_tasks(db_path=db) == []
    res2 = sch.reimb_data_sync_job()                             # 같은 해시 → 변경 없음 skip, 여전히 ok
    assert res2["ok"] is True and res2["counts"]["data"] == {"changed": 0}


# ── policy_intel_analysis_sync: 반환 dict 의 error 키 ────────────────────────────────

def test_policy_sync_auth_error_is_config_class(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "_sync_policy_analysis", lambda: {
        "copied": 0, "skipped": 0, "rejected": 0,
        "error": "auth required: POLICY_ANALYSIS_TOKEN/GITHUB_TOKEN missing (fail-closed in production)", "dest": "x"})
    res = sch.policy_analysis_sync_job()
    assert res["ok"] is False and res["error_class"] == "config"
    assert list_tasks(db_path=db)[0]["source_ref"] == "policy_intel_analysis_sync:config"


def test_policy_sync_fetch_error_is_transient(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "_sync_policy_analysis", lambda: {
        "copied": 0, "skipped": 0, "rejected": 0, "error": "source fetch failed: timeout", "dest": "x"})
    res = sch.policy_analysis_sync_job()
    assert res["ok"] is False and res["error_class"] == "transient"
    assert list_tasks(db_path=db)[0]["source_ref"] == f"policy_intel_analysis_sync:{TODAY}"


def test_policy_sync_success(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    monkeypatch.setattr(sch, "_sync_policy_analysis", lambda: {"copied": 2, "skipped": 5, "rejected": 0, "dest": "x"})
    res = sch.policy_analysis_sync_job()
    assert res == {"ok": True, "counts": {"copied": 2, "skipped": 5, "rejected": 0}, "errors": [], "error_class": "transient"}
    assert list_tasks(db_path=db) == []


# ── foreign_approval_sync: result["failed"] ─────────────────────────────────────────

def test_foreign_approval_failed_slugs_become_errors(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    mod = types.ModuleType("agents.foreign_approval.agent")

    class FakeAgent:
        def list_coverage_gaps(self):
            return ["welireg", "prevymis"]

        def sync_from_prices(self, **kw):
            return {"built": ["welireg"], "failed": [{"slug": "prevymis", "reason": "FDA ID 미확인"}], "skipped": []}
    mod.ForeignApprovalAgent = FakeAgent
    _install_fake_module(monkeypatch, "agents.foreign_approval.agent", mod)
    res = sch.foreign_approval_sync_job()
    assert res["ok"] is False and res["errors"] == ["prevymis: FDA ID 미확인"]
    assert res["counts"] == {"gaps": 2, "built": 1, "failed": 1}
    assert list_tasks(db_path=db)[0]["source_ref"] == f"foreign_approval_sync:{TODAY}"


def test_foreign_approval_no_gaps_is_ok(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    mod = types.ModuleType("agents.foreign_approval.agent")

    class FakeAgent:
        def list_coverage_gaps(self):
            return []
    mod.ForeignApprovalAgent = FakeAgent
    _install_fake_module(monkeypatch, "agents.foreign_approval.agent", mod)
    assert sch.foreign_approval_sync_job()["counts"] == {"gaps": 0, "built": 0, "failed": 0}
    assert list_tasks(db_path=db) == []


# ── daily_mailing_dispatch: res["errors"] ───────────────────────────────────────────

def test_dispatch_errors_become_task(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)

    class FakeDeps:
        @classmethod
        def production(cls):
            return cls()
    monkeypatch.setattr("agents.daily_mailing.dispatch.DispatchDeps", FakeDeps)
    monkeypatch.setattr("agents.daily_mailing.dispatch.dispatch_due_subscriptions",
                        lambda deps: {"run_date": TODAY, "expired": [], "generated": [{"subscription_id": 3}],
                                      "skipped": 1, "errors": [14], "deferred": [], "retried": 0})
    res = sch.daily_mailing_dispatch_job()
    assert res["ok"] is False and res["errors"] == ["구독 14 생성 실패"]
    assert res["counts"] == {"generated": 1, "expired": 0, "retried": 0, "skipped": 1}
    assert list_tasks(db_path=db)[0]["source_ref"] == f"daily_mailing_dispatch:{TODAY}"
