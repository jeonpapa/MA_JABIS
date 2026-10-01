"""RC 메모리 없는 모드(프로덕션) + QG/RC 산출물 경로(볼륨) — P1.5 Task 6.

프로덕션 컨테이너에는 Claude 메모리 디렉터리가 없다. 14개 체크는 전부 DB/파일 증거라 메모리는 라벨만 준다 —
require_memory=False 면 CHECKS 레지스트리가 곧 목록. 로컬(메모리 있음)의 MEMORY.md 경로는 그대로.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import agents.quality_guard.deviations as deviations
import agents.rule_compliance.agent as rc_agent
from agents.rule_compliance.checks import CHECKS


@pytest.fixture
def no_memory_anywhere(monkeypatch, tmp_path):
    """컨테이너처럼: 환경변수 없음, 기본 경로 없음, ~/.claude/projects 없음."""
    monkeypatch.delenv("CLAUDE_MEMORY_DIR", raising=False)
    monkeypatch.setattr(rc_agent, "_DEFAULT_MEMORY_DIR", tmp_path / "nope" / "memory")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(deviations, "GUARD_DIR", tmp_path / "guard")
    return tmp_path


def test_resolve_memory_dir_required_raises_and_optional_returns_none(no_memory_anywhere):
    with pytest.raises(FileNotFoundError):
        rc_agent._resolve_memory_dir()
    assert rc_agent._resolve_memory_dir(required=False) is None


def test_memoryless_agent_runs_every_registered_check(no_memory_anywhere):
    root = no_memory_anywhere / "project"
    root.mkdir()
    agent = rc_agent.RuleComplianceAgent(project_root=root, require_memory=False)
    assert agent.memory_dir is None
    assert [e["name"] for e in agent.load_index()] == list(CHECKS)
    results = agent.audit()
    assert [r.memory for r in results] == list(CHECKS)                 # 14 체크 전부 돈다
    assert {r.status for r in results} <= {"pass", "fail", "skip"}
    assert agent.report_dir == no_memory_anywhere / "guard"             # QG 와 같은 볼륨 경로
    path = agent.write_report(results)
    assert path.parent == agent.report_dir and path.name.startswith("compliance_")


def test_memoryless_default_is_still_required(no_memory_anywhere):
    with pytest.raises(FileNotFoundError):
        rc_agent.RuleComplianceAgent(project_root=no_memory_anywhere)


def test_memory_mode_parses_index_as_before(no_memory_anywhere):
    mem = no_memory_anywhere / "mem"
    mem.mkdir()
    (mem / "MEMORY.md").write_text(
        "# Memory Index\n\n- [Project: X](project_x.md) — 훅 x\n- [User: J](user_joseph.md) — 프로파일\n",
        encoding="utf-8")
    agent = rc_agent.RuleComplianceAgent(memory_dir=mem, project_root=no_memory_anywhere)
    assert [e["name"] for e in agent.load_index()] == ["project_x", "user_joseph"]
    results = {r.memory: r for r in agent.audit()}
    assert results["user_joseph"].status == "skip" and results["project_x"].status == "skip"


def test_guard_dir_defaults_to_volume_and_honours_override(monkeypatch, tmp_path):
    monkeypatch.delenv("QUALITY_GUARD_DIR", raising=False)
    assert deviations.guard_dir() == deviations.BASE_DIR / "data" / "quality_guard"
    assert deviations.guard_dir() != deviations.BASE_DIR / "quality_guard"      # 컨테이너 경로(재배포 시 소실) 아님
    monkeypatch.setenv("QUALITY_GUARD_DIR", str(tmp_path / "qg"))
    assert deviations.guard_dir() == tmp_path / "qg"
    # 모듈 상수는 import 시점의 guard_dir() — 오버라이드 후 reload 하면 따라간다
    reloaded = importlib.reload(deviations)
    try:
        assert reloaded.GUARD_DIR == tmp_path / "qg" and reloaded.DEVIATION_LOG == tmp_path / "qg" / "deviation_log.jsonl"
    finally:
        monkeypatch.delenv("QUALITY_GUARD_DIR", raising=False)
        importlib.reload(deviations)
