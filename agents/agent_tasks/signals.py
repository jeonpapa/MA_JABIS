"""운영 신호 → 작업 원장 과제 (설계 §5, P1.5 Task 1).

중복 억제 규칙(오탐 억제의 핵심):
  - 잡 실패(transient): source_ref = "{job_id}:{YYYY-MM-DD}" — 하루 1건. 10분 잡이 144번 실패해도 1건 + recurred 이벤트 1건/일.
  - 잡 실패(config):    source_ref = "{job_id}:config" — 사람이 닫을 때까지 1건 (토큰·키 부재는 재실행으로 안 풀린다).
  - 놓친 실행(misfire):  source_ref = "scheduler_missed:{날짜}" — 하루 1건, 놓친 잡은 이벤트로 누적.
  - blocked 도 중복 억제에 포함(DEDUP_STATUSES) — '런북 없음 → blocked' 가 매일 새 과제를 만들지 않게.
어떤 런북으로 고칠 수 있는지 detail 에 '런북: <이름>' 으로 적는다 — 디스패처(scripts/agent_dispatcher.py:pick_runbook)가 이 힌트를 읽는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import DEFAULT_DB_PATH, OPEN_STATUSES, add_event, create_task, list_events

KST = timezone(timedelta(hours=9))
DEDUP_STATUSES = OPEN_STATUSES + ("blocked",)

# 잡 id → 런북 (None = 런북 없음, 사람 확인). 키 집합은 scheduler.build_job_specs() 의 id 전체와 같아야 한다
# (tests/test_scheduler_job_specs.py 가 대조). Task 5 가 None 을 등록된 런북 이름으로 바꾼다.
JOB_RUNBOOK: dict[str, str | None] = {
    "domestic_price_pipeline": "domestic_price_catchup",
    "git_auto_backup": None,
    "foreign_approval_sync": None,
    "rule_compliance_audit": None,
    "quality_guard_review": None,
    "competitor_news_weekly": None,
    "competitor_news_daily": None,
    "competitor_trend_promote": None,
    "access_insight_fresh_signals": None,
    "reimbursement_xnational_sync": None,
    "exchange_rate_refresh": None,
    "reimb_data_sync": None,
    "policy_intel_analysis_sync": None,
    "daily_mailing_dispatch": "daily_mailing_dispatch",
    "daily_mailing_top_news": "daily_mailing_top_news",
    "daily_mailing_health": None,
    "nhis_negotiation_sync": None,
    "home_brand_related_refresh": None,
    "amjilsim_d_minus_2_reporter": None,
    "amjilsim_d_plus_1_reporter": None,
    "amjilsim_monthly_trend": None,
    "hira_schedule_fetcher_annual": None,
}
# Daily Mailing 헬스 문제 코드 전부(agents/daily_mailing/dispatch.py:50,490,498,504). None 은 런북으로 안 풀린다:
# held = 재시도 3회 소진(사람 승인/원인 확인), error = 생성 예외(원인 확인), expired = 승인 기한 경과(사람), gmail_token_invalid = 토큰 재발급(사람).
HEALTH_RUNBOOK: dict[str, str | None] = {
    "not_generated": "daily_mailing_dispatch",
    "send_failed": "daily_mailing_dispatch",
    "ready_to_send": "daily_mailing_dispatch",
    "held": None,
    "error": None,
    "expired": None,
    "gmail_token_invalid": None,
}
HIGH_PRIORITY_PROBLEMS = {"gmail_token_invalid", "error"}
# 날짜 없이 1건만 유지하는 헬스 문제(설정 문제) — 매일 새 과제가 아니라 사람이 닫을 때까지 하나
CONFIG_PROBLEMS = {"gmail_token_invalid"}


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def _kst_date(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(KST).date().isoformat()


def runbook_line(name: str | None) -> str:
    return f"런북: {name}" if name else "런북 없음 — 사람 확인 필요"


_runbook_line = runbook_line   # P1 호환


def _note_recurrence(task_id: int, error: str, today: str, *, db_path: str | Path) -> None:
    """이미 있던 과제에 오늘 처음 다시 났을 때만 recurred 이벤트 1건 (하루 1건 상한)."""
    for e in reversed(list_events(task_id, db_path=db_path)):
        if e["event"] in ("created", "recurred") and _kst_date(e["created_at"]) == today:
            return
    add_event(task_id, actor="scheduler", event="recurred", detail=str(error)[:500], db_path=db_path)


def record_job_failure(job_id: str, error: str, *, error_class: str = "transient",
                       db_path: str | Path = DEFAULT_DB_PATH) -> int:
    runbook = JOB_RUNBOOK.get(job_id)
    today = _today()
    ref = f"{job_id}:config" if error_class == "config" else f"{job_id}:{today}"
    title = f"스케줄러 잡 실패 — {job_id}" + (" (설정 문제)" if error_class == "config" else "")
    tid = create_task(
        kind="ops_incident", source="scheduler_job", source_ref=ref, title=title,
        detail=f"{error}\n{runbook_line(runbook)}", priority=2, actor="scheduler",
        dedup_statuses=DEDUP_STATUSES, db_path=db_path)
    _note_recurrence(tid, error, today, db_path=db_path)
    return tid


def record_job_missed(job_id: str, scheduled_run_time, *, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """APScheduler EVENT_JOB_MISSED — misfire grace 를 넘겨 폐기된 실행. 하루 1건, 놓친 잡은 이벤트로 누적."""
    today = _today()
    tid = create_task(
        kind="ops_incident", source="scheduler_missed", source_ref=f"scheduler_missed:{today}",
        title="스케줄러가 놓친 실행 (misfire grace 초과)",
        detail="예정 시각에 스케줄러가 살아 있지 않았다(재기동·정지). 놓친 잡은 이벤트 목록 참조.\n"
               + runbook_line(None) + " — 재기동 원인 확인 후 필요한 잡만 `python scheduler.py --<job>-now`",
        priority=3, actor="scheduler", dedup_statuses=DEDUP_STATUSES, db_path=db_path)
    add_event(tid, actor="scheduler", event="missed", detail=f"{job_id} @ {scheduled_run_time}", db_path=db_path)
    return tid


def record_health_problems(report: dict, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    run_date = report.get("run_date") or _today()
    ids: list[int] = []
    for problem in report.get("problems") or []:
        code = str(problem.get("problem") or "unknown")
        sub_id = problem.get("subscription_id")
        ref_tail = f"{sub_id}:{code}" if sub_id is not None else code
        ref = f"daily_mailing:{ref_tail}" if code in CONFIG_PROBLEMS else f"daily_mailing:{run_date}:{ref_tail}"
        runbook = HEALTH_RUNBOOK.get(code)
        detail = [f"구독: {problem.get('name') or '-'} ({sub_id})", f"문제: {code}"]
        if problem.get("detail"):
            detail.append(f"상세: {problem['detail']}")
        detail.append(runbook_line(runbook))
        ids.append(create_task(
            kind="ops_incident", source="daily_mailing_health", source_ref=ref,
            title=f"Daily Mailing {code} — 구독 {sub_id if sub_id is not None else '-'}",
            detail="\n".join(detail),
            priority=1 if code in HIGH_PRIORITY_PROBLEMS else 2,
            actor="daily_mailing_health", dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    return ids
