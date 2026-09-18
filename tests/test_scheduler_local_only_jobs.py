"""스케줄러 로컬 전용 잡의 프로덕션 skip (2026-09-18 프로덕션 로그 ERROR 매일 반복 회귀).

- git_backup_job: 컨테이너에 git 없음 → `[Errno 2] ... 'git'` ERROR 매일 00:00
- rule_compliance_audit_job: Claude 메모리 디렉터리는 개발 Mac 에만 존재 → ERROR 매일 05:30
프로덕션(FLY_APP_NAME) 이거나 git 바이너리 없으면 INFO 한 줄로 skip. 로컬 Mac 동작은 불변.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scheduler as sched  # noqa: E402
import agents.rule_compliance as rc_pkg  # noqa: E402
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

def test_rule_compliance_skipped_in_production(monkeypatch, caplog):
    monkeypatch.setenv("FLY_APP_NAME", "ma-ai-dossier")

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("RuleComplianceAgent 가 생성되면 안 됨")
    monkeypatch.setattr(rc_pkg, "RuleComplianceAgent", _Boom)
    caplog.set_level(logging.INFO, logger="scheduler")

    sched.rule_compliance_audit_job()

    assert not _errors(caplog)
    assert len(_skip_infos(caplog)) == 1


def test_rule_compliance_runs_locally(monkeypatch, caplog, tmp_path):
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    called = {"audit": 0, "report": 0}

    class _Fake:
        def __init__(self, *a, **k):
            pass

        def audit(self):
            called["audit"] += 1
            return []

        def write_report(self, results):
            called["report"] += 1
            return tmp_path / "compliance.md"
    monkeypatch.setattr(rc_pkg, "RuleComplianceAgent", _Fake)
    caplog.set_level(logging.INFO, logger="scheduler")

    sched.rule_compliance_audit_job()

    assert called == {"audit": 1, "report": 1}
    assert not _errors(caplog)
