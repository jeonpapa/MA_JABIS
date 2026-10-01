"""구현 대기 기능(placeholder)이 스케줄에서 내려가고 원장의 영구 과제로 남는지 (P1.5 Task 7)."""
from __future__ import annotations

import scheduler as sch
from agents.agent_tasks.signals import KNOWN_DEBTS, JOB_RUNBOOK, record_known_debts
from agents.agent_tasks.store import list_tasks, update_task

SCHED_CFG = {"day": "1-7", "hour": 9, "minute": 0}
PLACEHOLDERS = {"amjilsim_d_minus_2_reporter", "amjilsim_d_plus_1_reporter",
                "amjilsim_monthly_trend", "hira_schedule_fetcher_annual"}


def test_placeholder_jobs_are_not_registered():
    ids = {s.id for s in sch.build_job_specs(SCHED_CFG)}
    assert ids & PLACEHOLDERS == set()
    assert set(JOB_RUNBOOK) & PLACEHOLDERS == set()      # 매핑도 같이 지운다
    assert ids == set(JOB_RUNBOOK)                        # 등록부 ↔ 매핑 일치 유지


def test_placeholder_functions_are_kept_for_later_implementation():
    # 스케줄에서 내렸을 뿐, 후속 구현 자리는 남긴다
    for name in ("amjilsim_d_minus_2_reporter_job", "amjilsim_d_plus_1_reporter_job",
                 "amjilsim_monthly_trend_job", "hira_schedule_fetcher_job"):
        assert callable(getattr(sch, name))


def test_record_known_debts_creates_one_improvement_task_each(tmp_path):
    db = tmp_path / "a.db"
    ids = record_known_debts(db_path=db)
    assert len(ids) == len(KNOWN_DEBTS) == 2
    tasks = list_tasks(db_path=db, kinds=("improvement",))
    assert {t["source"] for t in tasks} == {"known_debt"}
    assert {t["source_ref"] for t in tasks} == {"debt:amjilsim_reporters", "debt:hira_schedule_fetcher"}
    # 부채는 런북으로 안 풀린다 — 디스패처가 집지 않도록 kind=improvement + '런북 없음'
    assert all("런북 없음" in t["detail"] for t in tasks)


def test_second_run_adds_nothing(tmp_path):
    db = tmp_path / "a.db"
    first = record_known_debts(db_path=db)
    second = record_known_debts(db_path=db)
    assert first == second                                 # 같은 과제 id 를 돌려준다
    assert len(list_tasks(db_path=db, kinds=("improvement",))) == 2


def test_blocked_debt_is_not_recreated(tmp_path):
    """런북 없음 → blocked 로 끝난 부채가 재기동마다 새로 생기면 원장이 쓰레기가 된다."""
    db = tmp_path / "a.db"
    tid = record_known_debts(db_path=db)[0]
    update_task(tid, db_path=db, actor="admin@msd.com", status="triaged")
    update_task(tid, db_path=db, actor="admin@msd.com", status="blocked",
                resolution="구현 대기 — P2")
    record_known_debts(db_path=db)
    assert len(list_tasks(db_path=db, statuses=("new", "triaged", "blocked"),
                          kinds=("improvement",))) == 2


def test_closed_debt_reopens(tmp_path):
    """구현 완료로 닫은 뒤 다시 placeholder 로 되돌아가면(회귀) 과제가 다시 열려야 한다."""
    db = tmp_path / "a.db"
    tid = record_known_debts(db_path=db)[0]
    update_task(tid, db_path=db, actor="admin@msd.com", status="triaged")
    update_task(tid, db_path=db, actor="admin@msd.com", status="in_progress")
    update_task(tid, db_path=db, actor="admin@msd.com", status="closed", resolution="구현 완료")
    again = record_known_debts(db_path=db)
    assert tid not in again


def test_main_records_debts_once_on_startup(monkeypatch, tmp_path):
    db = tmp_path / "a.db"
    monkeypatch.setenv("AGENT_TASK_DB", str(db))
    calls = []
    monkeypatch.setattr(sch, "_agent_record_known_debts", lambda: calls.append(1))

    class StopScheduler(sch.BlockingScheduler):
        def start(self, *a, **k):
            raise KeyboardInterrupt

    monkeypatch.setattr(sch, "BlockingScheduler", StopScheduler)
    monkeypatch.setattr("sys.argv", ["scheduler.py"])
    sch.main()
    assert calls == [1]
