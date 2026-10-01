"""스케줄러 로컬 전용 잡의 프로덕션 skip (2026-09-18 프로덕션 로그 ERROR 매일 반복 회귀).

- git_backup_job: 컨테이너에 git 없음 → `[Errno 2] ... 'git'` ERROR 매일 00:00
- rule_compliance_audit_job: Claude 메모리 디렉터리는 개발 Mac 에만 존재 → 프로덕션에서는 메모리 없는 모드로 실행
  (require_memory=False, CHECKS 레지스트리만 — P1.5 Task 6). 로컬은 메모리 대조 그대로.
git 은 프로덕션(FLY_APP_NAME) 이거나 바이너리 없으면 INFO 한 줄로 skip. 로컬 Mac 동작은 불변.
QG/RC 산출물은 원장 과제가 된다(내용 키 dedup) — 두 번째 실행은 tasks_new=0.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scheduler as sched  # noqa: E402
import agents.rule_compliance as rc_pkg  # noqa: E402
import agents.quality_guard as qg_pkg  # noqa: E402
from agents.agent_tasks.jobs import resolve_db_path  # noqa: E402
from agents.agent_tasks.store import list_tasks  # noqa: E402
from agents.rule_compliance.checks import CheckResult  # noqa: E402
from agents.runtime_env import is_production  # noqa: E402


def _errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def _skip_infos(caplog):
    return [r for r in caplog.records
            if r.levelno == logging.INFO and "skip" in r.getMessage().lower()]


def test_is_production_follows_fly_app_name(monkeypatch):
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    assert is_production() is False
    monkeypatch.setenv("FLY_APP_NAME", "ma-ai-dossier")
    assert is_production() is True


# ── git_backup_job ───────────────────────────────────────────────────────────

def test_git_backup_skipped_in_production(monkeypatch, caplog):
    monkeypatch.setenv("FLY_APP_NAME", "ma-ai-dossier")

    def boom(*a, **k):
        raise AssertionError("git 명령이 실행되면 안 됨")
    monkeypatch.setattr(sched.subprocess, "run", boom)
    caplog.set_level(logging.INFO, logger="scheduler")

    sched.git_backup_job()

    assert not _errors(caplog)
    assert len(_skip_infos(caplog)) == 1


def test_git_backup_skipped_when_git_binary_missing(monkeypatch, caplog):
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setattr(sched.shutil, "which", lambda name: None)

    def boom(*a, **k):
        raise AssertionError("git 명령이 실행되면 안 됨")
    monkeypatch.setattr(sched.subprocess, "run", boom)
    caplog.set_level(logging.INFO, logger="scheduler")

    sched.git_backup_job()

    assert not _errors(caplog)
    assert len(_skip_infos(caplog)) == 1


def test_git_backup_runs_locally(monkeypatch, caplog):
    """로컬 Mac(FLY_APP_NAME 없음 + git 있음) 동작 불변 — git add 부터 실행."""
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setattr(sched.shutil, "which", lambda name: "/usr/bin/git")
    calls: list[list[str]] = []

    class _Res:
        stdout = "nothing to commit, working tree clean"
        stderr = ""
        returncode = 0

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return _Res()
    monkeypatch.setattr(sched.subprocess, "run", fake_run)
    caplog.set_level(logging.INFO, logger="scheduler")

    sched.git_backup_job()

    assert calls[0] == ["git", "add", "."]
    assert calls[1][:2] == ["git", "commit"]
    assert not _errors(caplog)


# ── rule_compliance_audit_job ────────────────────────────────────────────────

def test_rule_compliance_runs_in_production_without_memory(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv("FLY_APP_NAME", "ma-ai-dossier")
    seen = {}

    class _Fake:
        def __init__(self, *a, **k):
            seen.update(k)

        def audit(self):
            return []

        def write_report(self, results):
            return tmp_path / "compliance.md"
    monkeypatch.setattr(rc_pkg, "RuleComplianceAgent", _Fake)
    caplog.set_level(logging.INFO, logger="scheduler")
    res = sched.rule_compliance_audit_job()
    assert seen == {"require_memory": False} and res["ok"] is True
    assert not _errors(caplog)
    assert not _skip_infos(caplog)


def test_rule_compliance_runs_locally_with_memory(monkeypatch, caplog, tmp_path):
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    seen = {}

    class _Fake:
        def __init__(self, *a, **k):
            seen.update(k)

        def audit(self):
            return []

        def write_report(self, results):
            return tmp_path / "compliance.md"
    monkeypatch.setattr(rc_pkg, "RuleComplianceAgent", _Fake)
    caplog.set_level(logging.INFO, logger="scheduler")
    res = sched.rule_compliance_audit_job()
    assert seen == {"require_memory": True}
    assert res["ok"] is True and res["counts"]["pass"] == 0 and res["counts"]["tasks_new"] == 0
    assert not _errors(caplog)


def test_rule_compliance_fail_becomes_ledger_task_once(monkeypatch, caplog, tmp_path):
    """FAIL 은 과제(메모리당 1건)이지 잡 실패가 아니다 — ok=True, 두 번째 실행 tasks_new=0."""
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    results = [
        CheckResult("project_xnational_reimbursement", "fail", "last_fetched > 90일", {"total": 44}),
        CheckResult("project_indication_level_approval", "pass", "104건", {}),
        CheckResult("user_joseph", "skip", "정적", {}),
    ]

    class _Fake:
        def __init__(self, *a, **k):
            pass

        def audit(self):
            return list(results)

        def write_report(self, results):
            return tmp_path / "compliance.md"
    monkeypatch.setattr(rc_pkg, "RuleComplianceAgent", _Fake)
    caplog.set_level(logging.INFO, logger="scheduler")

    first = sched.rule_compliance_audit_job()
    second = sched.rule_compliance_audit_job()

    assert first["ok"] is True and second["ok"] is True
    assert first["counts"] == {"pass": 1, "fail": 1, "skip": 1, "tasks": 1, "tasks_new": 1}
    assert second["counts"]["tasks_new"] == 0 and second["counts"]["tasks"] == 1
    tasks = list_tasks(db_path=resolve_db_path(), kinds=("ops_incident",))
    assert [t["source_ref"] for t in tasks] == ["rc:project_xnational_reimbursement"]
    assert "런북: reimbursement_xnational_sync" in tasks[0]["detail"]


# ── quality_guard_review_job ─────────────────────────────────────────────────

def test_quality_guard_review_groups_into_ledger_once(monkeypatch, caplog, tmp_path):
    review = {
        "date": "2026-07-28",
        "code_issues": ["BaseScraper 미상속: tier1_news_sites.py", "BaseScraper 미상속: kr_health_kr.py"],
        "rule_drifts": [],
        "mfds_regressions": [],
        "suggestions": ["📦 `agents/a.py` (793줄) — 모듈 분리 검토", "📦 `agents/b.py` (764줄) — 모듈 분리 검토",
                        "🔑 `DISEASE_KR` 미커버 disease: CMV", "💊 drug_enrichment 커버리지 28/67846 (0.041%)",
                        "🔐 DocCheck 로그인 실패"],
        "report_path": str(tmp_path / "review.md"),
    }

    class _Fake:
        def __init__(self, *a, **k):
            pass

        def review_codebase(self):
            return dict(review)
    monkeypatch.setattr(qg_pkg, "QualityGuardAgent", _Fake)
    caplog.set_level(logging.INFO, logger="scheduler")

    first = sched.quality_guard_review_job()
    second = sched.quality_guard_review_job()

    assert first["ok"] is True and second["ok"] is True
    assert first["counts"]["code_issues"] == 2 and first["counts"]["suggestions"] == 5
    assert first["counts"]["tasks_improvement"] == 5 and first["counts"]["tasks_incident"] == 0
    assert first["counts"]["tasks_new"] == 5 and second["counts"]["tasks_new"] == 0
    tasks = list_tasks(db_path=resolve_db_path())
    assert len(tasks) == 5 and {t["kind"] for t in tasks} == {"improvement"}
    assert {t["source_ref"] for t in tasks} == {
        "qg:issue:BaseScraper 미상속", "qg:suggestion:📦", "qg:suggestion:🔑", "qg:suggestion:💊", "qg:suggestion:🔐"}
    assert not _errors(caplog)


def test_quality_guard_regression_is_error_log_and_incident(monkeypatch, caplog, tmp_path):
    review = {"date": "2026-07-28", "code_issues": [], "rule_drifts": [], "suggestions": [],
              "mfds_regressions": [{"indication_id": "keytruda-nsclc-adj", "expected": "2023-12-19",
                                    "actual": "2024-05-14", "reason": "peri"}],
              "report_path": str(tmp_path / "review.md")}

    class _Fake:
        def review_codebase(self):
            return dict(review)
    monkeypatch.setattr(qg_pkg, "QualityGuardAgent", _Fake)
    caplog.set_level(logging.INFO, logger="scheduler")

    res = sched.quality_guard_review_job()

    assert res["ok"] is True and res["counts"]["tasks_incident"] == 1 and res["counts"]["tasks_new"] == 1
    assert _errors(caplog)                                               # 회귀는 ERROR 로그 유지
    tasks = list_tasks(db_path=resolve_db_path(), kinds=("ops_incident",))
    assert [t["source_ref"] for t in tasks] == ["qg:regression:keytruda-nsclc-adj"] and tasks[0]["priority"] == 1


def test_quality_guard_signal_failure_does_not_kill_job(monkeypatch, caplog, tmp_path):
    """신호 경계: 원장 기록이 죽어도 잡은 ok — warning 만."""
    review = {"date": "x", "code_issues": [], "rule_drifts": [], "mfds_regressions": [], "suggestions": ["📦 a"],
              "report_path": "r"}

    class _Fake:
        def review_codebase(self):
            return dict(review)
    monkeypatch.setattr(qg_pkg, "QualityGuardAgent", _Fake)
    import agents.agent_tasks.signals as signals

    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr(signals, "record_quality_review", boom)
    caplog.set_level(logging.INFO, logger="scheduler")

    res = sched.quality_guard_review_job()

    assert res["ok"] is True and "tasks_new" not in res["counts"]
    assert any(r.levelno == logging.WARNING and "신호 기록 실패" in r.getMessage() for r in caplog.records)
    assert not _errors(caplog)
