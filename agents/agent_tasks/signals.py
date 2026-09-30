"""운영 신호 → 작업 원장 과제 (설계 §5).

같은 신호가 하루에 여러 번 와도 과제는 하나다(source_ref 에 날짜를 넣고 store 가 중복을 막는다).
어떤 런북으로 고칠 수 있는지 detail 에 적어 둔다 — 디스패처가 이 힌트로 런북을 고른다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import DEFAULT_DB_PATH, create_task

KST = timezone(timedelta(hours=9))

# 신호 → 런북 매핑 (없으면 사람이 봐야 하는 과제)
JOB_RUNBOOK = {
    "domestic_price_pipeline": "domestic_price_catchup",
    "daily_mailing_dispatch": "daily_mailing_dispatch",
    "daily_mailing_top_news": "daily_mailing_top_news",
}
HEALTH_RUNBOOK = {
    "not_generated": "daily_mailing_dispatch",
    "send_failed": "daily_mailing_dispatch",
    "ready_to_send": "daily_mailing_dispatch",
}
HIGH_PRIORITY_PROBLEMS = {"gmail_token_invalid", "error"}


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def _runbook_line(name: str | None) -> str:
    return f"런북: {name}" if name else "런북 없음 — 사람 확인 필요"


def record_job_failure(job_id: str, error: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    runbook = JOB_RUNBOOK.get(job_id)
    return create_task(
        kind="ops_incident", source="scheduler_job", source_ref=f"{job_id}:{_today()}",
        title=f"스케줄러 잡 실패 — {job_id}",
        detail=f"{error}\n{_runbook_line(runbook)}", priority=2,
        actor="scheduler", db_path=db_path)


def record_health_problems(report: dict, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    run_date = report.get("run_date") or _today()
    ids: list[int] = []
    for problem in report.get("problems") or []:
        code = str(problem.get("problem") or "unknown")
        sub_id = problem.get("subscription_id")
        ref_tail = f"{sub_id}:{code}" if sub_id is not None else code
        runbook = HEALTH_RUNBOOK.get(code)
        detail = [f"구독: {problem.get('name') or '-'} ({sub_id})", f"문제: {code}"]
        if problem.get("detail"):
            detail.append(f"상세: {problem['detail']}")
        detail.append(_runbook_line(runbook))
        ids.append(create_task(
            kind="ops_incident", source="daily_mailing_health",
            source_ref=f"daily_mailing:{run_date}:{ref_tail}",
            title=f"Daily Mailing {code} — 구독 {sub_id if sub_id is not None else '-'}",
            detail="\n".join(detail),
            priority=1 if code in HIGH_PRIORITY_PROBLEMS else 2,
            actor="daily_mailing_health", db_path=db_path))
    return ids
