"""런북 레지스트리·실행 모드 (P1 + P1.5 Task 5).

실제 외부 작업 금지 — 잡 함수(`_job`)·`subprocess.Popen`·`os.kill` 을 가짜로 바꿔 "무엇을 부르고 무엇을 돌려주는가" 만 본다.
분리(detached) 런북의 ok=True 는 "요청 접수" 이지 "복구 확인" 이 아니다 — 실제 결과는 자식의 guarded_job 이
scheduler_job_run 에 남기고, 다음 날 신선도 판정이 다시 연다. 이 파일의 테스트는 그 계약을 문자열로 고정한다.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

from agents.agent_tasks import runbooks

# freshness.py(Task 3) 가 detail 의 '런북: <이름>' 힌트로 쓰는 이름 전부 — 하나라도 미등록이면 디스패처가 400 → blocked.
FRESHNESS_HINTS = (
    "domestic_price_catchup", "exchange_rate_refresh", "exchange_rate_recompute", "foreign_approval_sync",
    "reimbursement_xnational_sync", "reimb_data_sync", "policy_intel_analysis_sync", "news_daily_fetch",
    "competitor_trend_promote", "access_insight_fresh_signals", "nhis_negotiation_sync", "stakeholder_data_sync",
)
# 동기 런북 → scheduler 의 guarded 잡 함수 (잡 = 런북, 코드 경로 하나)
SYNC_RUNBOOK_JOBS = {
    "exchange_rate_refresh": "exchange_rate_refresh_job",
    "exchange_rate_recompute": "exchange_rate_recompute_job",
    "news_daily_fetch": "competitor_news_daily_job",
    "competitor_trend_promote": "competitor_trend_promote_job",
    "nhis_negotiation_sync": "nhis_negotiation_sync_job",
    "reimb_data_sync": "reimb_data_sync_job",
    "policy_intel_analysis_sync": "policy_analysis_sync_job",
    "stakeholder_data_sync": "stakeholder_data_sync_job",
}
# 분리 런북 → `python scheduler.py <flag>` (600초 디스패처 타임아웃을 넘길 수 있는 크롤)
DETACHED_RUNBOOK_FLAGS = {
    "foreign_approval_sync": "--approval-sync-now",
    "reimbursement_xnational_sync": "--reimb-sync-now",
    "news_weekly_crawl": "--news-weekly-now",
    "access_insight_fresh_signals": "--fresh-signals-now",
}


def test_registry_lists_known_runbooks():
    assert set(runbooks.list_runbooks()) == {
        "domestic_price_catchup", "daily_mailing_dispatch", "daily_mailing_top_news",
        "exchange_rate_refresh", "exchange_rate_recompute", "foreign_approval_sync", "reimbursement_xnational_sync",
        "news_daily_fetch", "news_weekly_crawl", "competitor_trend_promote", "nhis_negotiation_sync",
        "access_insight_fresh_signals", "reimb_data_sync", "policy_intel_analysis_sync", "stakeholder_data_sync"}


def test_every_runbook_hint_in_signals_and_freshness_is_registered():
    from agents.agent_tasks.signals import HEALTH_RUNBOOK, JOB_RUNBOOK
    registered = set(runbooks.list_runbooks())
    hints = {v for v in JOB_RUNBOOK.values() if v} | {v for v in HEALTH_RUNBOOK.values() if v}
    assert hints <= registered, hints - registered
    # freshness 의 런북 이름은 함수 소스에 문자열로 있다 — 판정을 돌리지 않고 상수만 대조.
    # 이 브랜치(Task 1 기반)에는 freshness.py 가 없을 수 있다 — 그래도 11개 이름의 등록은 항상 확인하고,
    # 모듈이 있으면(머지 후) 소스에 실제로 그 문자열이 있는지까지 본다.
    missing = [n for n in FRESHNESS_HINTS if n not in registered]
    assert not missing, missing
    try:
        from agents.agent_tasks import freshness
    except ImportError:
        pytest.skip("freshness.py 없음 (Task 3 머지 전) — 11개 이름 등록만 확인")
    src = inspect.getsource(freshness)
    for name in FRESHNESS_HINTS:
        assert f'"{name}"' in src, name


def test_job_runbook_values_point_to_registered_runbooks():
    """Task 5 가 채운 JOB_RUNBOOK 값 10개. 키는 건드리지 않는다(다른 Task 가 키를 추가한다)."""
    from agents.agent_tasks.signals import JOB_RUNBOOK
    expected = {
        "foreign_approval_sync": "foreign_approval_sync",
        "competitor_news_weekly": "news_weekly_crawl",
        "competitor_news_daily": "news_daily_fetch",
        "competitor_trend_promote": "competitor_trend_promote",
        "access_insight_fresh_signals": "access_insight_fresh_signals",
        "reimbursement_xnational_sync": "reimbursement_xnational_sync",
        "exchange_rate_refresh": "exchange_rate_refresh",
        "reimb_data_sync": "reimb_data_sync",
        "policy_intel_analysis_sync": "policy_intel_analysis_sync",
        "nhis_negotiation_sync": "nhis_negotiation_sync",
    }
    for job_id, name in expected.items():
        assert JOB_RUNBOOK.get(job_id) == name, job_id
    # 재실행이 답이 아니거나 그 자체가 관측 잡인 것은 None 유지
    for job_id in ("git_auto_backup", "rule_compliance_audit", "quality_guard_review", "daily_mailing_health",
                   "home_brand_related_refresh"):
        assert JOB_RUNBOOK.get(job_id) is None, job_id


def test_unknown_runbook_raises():
    with pytest.raises(runbooks.RunbookError):
        runbooks.run("does_not_exist")


# ── P1 런북 3개 ──────────────────────────────────────────────────────────────

def test_domestic_catchup_reports_ingested(monkeypatch):
    calls = {}

    def fake_run_sync(config, base_dir, max_pages=3, force=False):
        calls["force"] = force
        return {"ingested": [{"apply_date": "2026.09.01", "rows": 21980}], "errors": []}

    monkeypatch.setattr(runbooks, "_domestic_run_sync", fake_run_sync)
    out = runbooks.run("domestic_price_catchup")
    assert out["ok"] is True and calls["force"] is True
    assert "2026.09.01" in out["summary"]


def test_domestic_catchup_failure_is_not_ok(monkeypatch):
    monkeypatch.setattr(runbooks, "_domestic_run_sync",
                        lambda *a, **k: {"ingested": [], "errors": ["HIRA 게시판 로딩 실패"]})
    out = runbooks.run("domestic_price_catchup")
    assert out["ok"] is False and "HIRA" in out["summary"]


def test_daily_mailing_dispatch_runbook(monkeypatch):
    monkeypatch.setattr(runbooks, "_dispatch_due",
                        lambda deps: {"generated": [{"subscription_id": 14}], "errors": [], "retried": 1})
    monkeypatch.setattr(runbooks, "_dispatch_deps", lambda: object())
    out = runbooks.run("daily_mailing_dispatch")
    assert out["ok"] is True and "1건 생성" in out["summary"]


def test_top_news_runbook(monkeypatch):
    monkeypatch.setattr(runbooks, "_dispatch_deps", lambda: type("D", (), {
        "load_top_ma_news": staticmethod(lambda run_date, retry_failed=False: [1, 2])})())
    out = runbooks.run("daily_mailing_top_news")
    assert out["ok"] is True and "2건" in out["summary"]


# ── 동기 런북: guarded_job 의 {ok, counts, errors} 를 {ok, summary, detail} 로 ──────────────

def test_sync_runbook_wraps_guarded_job_result(monkeypatch):
    monkeypatch.setattr(runbooks, "_job", lambda name: (
        lambda: {"ok": True, "counts": {"fetched": 12}, "errors": [], "error_class": "transient"}))
    out = runbooks.run("nhis_negotiation_sync")
    assert out["ok"] is True and "완료" in out["summary"] and out["detail"]["counts"] == {"fetched": 12}


def test_sync_runbook_failure_is_not_ok(monkeypatch):
    monkeypatch.setattr(runbooks, "_job", lambda name: (
        lambda: {"ok": False, "counts": {}, "errors": ["Naver 5xx"], "error_class": "transient"}))
    out = runbooks.run("news_daily_fetch")
    assert out["ok"] is False and "Naver 5xx" in out["summary"] and "실패" in out["summary"]


def test_sync_runbook_exception_becomes_result_not_raise(monkeypatch):
    def broken():
        raise RuntimeError("scheduler import 실패")
    monkeypatch.setattr(runbooks, "_job", lambda name: broken)
    out = runbooks.run("exchange_rate_recompute")
    assert out["ok"] is False and "RuntimeError" in out["summary"] and out["detail"] == {}


def test_sync_runbooks_call_the_guarded_scheduler_job(monkeypatch):
    """런북 표의 동기 7개: 각각 scheduler 의 guarded 잡 함수 하나를 부른다 — 잡 = 런북(코드 경로 하나)."""
    import scheduler
    called: list[str] = []

    def fake_from_job(job_fn_name, label):
        called.append(job_fn_name)
        return {"ok": True, "summary": label, "detail": {}}
    monkeypatch.setattr(runbooks, "_from_job", fake_from_job)
    for name, job_fn in SYNC_RUNBOOK_JOBS.items():
        called.clear()
        assert runbooks.run(name)["ok"] is True
        assert called == [job_fn], name
        assert getattr(getattr(scheduler, job_fn), "__job_id__", None), f"{job_fn} 이 guarded_job 이 아니다"


def test_job_lazy_import_resolves_guarded_scheduler_function():
    fn = runbooks._job("competitor_news_daily_job")
    assert fn.__job_id__ == "competitor_news_daily"
    with pytest.raises(AttributeError):
        runbooks._job("no_such_job")


# ── 분리 런북: 별도 프로세스 + pid 가드 ──────────────────────────────────────────

class _FakeProc:
    def __init__(self, pid=4242, returncode=None):
        self.pid, self._rc = pid, returncode

    def poll(self):
        return self._rc                                   # None = 아직 실행 중


@pytest.fixture
def detached_env(monkeypatch, tmp_path):
    """logs/ 를 임시 디렉터리로, Popen 을 기록기로. 반환: spawned 호출 목록 [(cmd, kwargs)]."""
    monkeypatch.setattr(runbooks, "DETACHED_LOG_DIR", tmp_path)
    runbooks._DETACHED.clear()
    spawned: list[tuple[list, dict]] = []

    def fake_popen(cmd, **kw):
        spawned.append((cmd, kw))
        return _FakeProc()
    monkeypatch.setattr(runbooks.subprocess, "Popen", fake_popen)
    return spawned


def test_detached_runbook_spawns_cli_and_guards_overlap(detached_env, tmp_path):
    spawned = detached_env
    out = runbooks.run("reimbursement_xnational_sync")
    assert out["ok"] is True and "백그라운드" in out["summary"] and out["detail"]["pid"] == 4242
    cmd, kw = spawned[0]
    assert cmd[0] == sys.executable
    assert cmd[1:] == [str(runbooks.REPO_ROOT / "scheduler.py"), "--reimb-sync-now"]
    assert kw["start_new_session"] is True and kw["cwd"] == str(runbooks.REPO_ROOT)
    assert kw["stdout"].name == str(tmp_path / "runbook_reimbursement_xnational_sync.out")
    assert (tmp_path / "runbook_reimbursement_xnational_sync.pid").read_text() == "4242"
    # 접수 ≠ 복구 — summary 가 확인 경로(scheduler_job_run·신선도)를 가리킨다
    assert "scheduler_job_run" in out["summary"] and out["detail"]["mode"] == "detached"
    again = runbooks.run("reimbursement_xnational_sync")
    assert again["ok"] is False and "이미 실행 중" in again["summary"] and len(spawned) == 1


def test_detached_runbooks_pass_flags_scheduler_main_accepts(monkeypatch):
    """런북 표의 분리 4개: 넘기는 CLI 플래그가 scheduler.main() 의 argparse 에 실제로 있다."""
    import scheduler
    main_src = inspect.getsource(scheduler.main)
    seen: dict[str, str] = {}

    def fake_detached(name, flag):
        seen[name] = flag
        return {"ok": True, "summary": "x", "detail": {}}
    monkeypatch.setattr(runbooks, "_run_detached", fake_detached)
    for name, flag in DETACHED_RUNBOOK_FLAGS.items():
        runbooks.run(name)
        assert seen[name] == flag, name
        assert f'"{flag}"' in main_src, f"{flag} 가 scheduler.main() 에 없다"


def test_detached_pidfile_live_process_refuses_without_spawning(detached_env, tmp_path):
    """서버 재기동으로 _DETACHED 가 비었어도 pid 파일의 프로세스가 살아 있으면 거부 (자기 pid = 확실히 살아 있음)."""
    spawned = detached_env
    pidfile = tmp_path / "runbook_news_weekly_crawl.pid"
    pidfile.write_text(str(os.getpid()))
    out = runbooks.run("news_weekly_crawl")
    assert out["ok"] is False and "이미 실행 중" in out["summary"] and out["detail"]["pid"] == os.getpid()
    assert spawned == [] and pidfile.read_text() == str(os.getpid())


def test_detached_pidfile_stale_process_proceeds(detached_env, tmp_path, monkeypatch):
    """pid 파일이 있어도 그 프로세스가 없으면(지난 실행 종료·컨테이너 재시작) 지우고 새로 띄운다."""
    spawned = detached_env
    pidfile = tmp_path / "runbook_news_weekly_crawl.pid"
    pidfile.write_text("99999999")

    def no_such_process(pid, sig):
        assert pid == 99999999 and sig == 0
        raise ProcessLookupError
    monkeypatch.setattr(runbooks.os, "kill", no_such_process)
    out = runbooks.run("news_weekly_crawl")
    assert out["ok"] is True and len(spawned) == 1 and spawned[0][0][-1] == "--news-weekly-now"
    assert pidfile.read_text() == "4242"


def test_detached_pidfile_garbage_is_ignored(detached_env, tmp_path):
    spawned = detached_env
    (tmp_path / "runbook_foreign_approval_sync.pid").write_text("not-a-pid")
    out = runbooks.run("foreign_approval_sync")
    assert out["ok"] is True and len(spawned) == 1


def test_detached_finished_child_is_reaped_and_not_treated_as_running(detached_env, tmp_path, monkeypatch):
    """이전 자식이 끝났으면(poll() != None) 같은 이름을 다시 띄울 수 있다 — zombie 가 '실행 중' 으로 남지 않는다."""
    spawned = detached_env
    runbooks._DETACHED["access_insight_fresh_signals"] = _FakeProc(pid=777, returncode=0)
    (tmp_path / "runbook_access_insight_fresh_signals.pid").write_text("777")
    monkeypatch.setattr(runbooks.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    out = runbooks.run("access_insight_fresh_signals")
    assert out["ok"] is True and len(spawned) == 1 and out["detail"]["pid"] == 4242


def test_pid_alive_treats_zombie_as_finished(monkeypatch):
    """Linux: 부모가 wait 하지 않은 zombie 는 kill(pid, 0) 이 성공한다 — /proc 상태 'Z' 면 끝난 것으로 본다."""
    monkeypatch.setattr(runbooks.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(runbooks, "_proc_state", lambda pid: "Z")
    assert runbooks._pid_alive(123) is False
    monkeypatch.setattr(runbooks, "_proc_state", lambda pid: "S")
    assert runbooks._pid_alive(123) is True
    monkeypatch.setattr(runbooks, "_proc_state", lambda pid: None)      # /proc 없음(macOS) → kill 결과만
    assert runbooks._pid_alive(123) is True


def test_pid_alive_permission_error_means_alive(monkeypatch):
    """존재하지만 시그널 권한이 없는 프로세스는 '살아 있음' — 겹쳐 띄우는 쪽이 더 위험하다."""
    def denied(pid, sig):
        raise PermissionError
    monkeypatch.setattr(runbooks.os, "kill", denied)
    assert runbooks._pid_alive(1) is True
