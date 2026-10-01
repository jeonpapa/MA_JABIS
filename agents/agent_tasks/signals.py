"""운영 신호 → 작업 원장 과제 (설계 §5, P1.5 Task 1).

중복 억제 규칙(오탐 억제의 핵심):
  - 잡 실패(transient): source_ref = "{job_id}:{YYYY-MM-DD}" — 하루 1건. 10분 잡이 144번 실패해도 1건 + recurred 이벤트 1건/일.
  - 잡 실패(config):    source_ref = "{job_id}:config" — 사람이 닫을 때까지 1건 (토큰·키 부재는 재실행으로 안 풀린다).
  - 놓친 실행(misfire):  source_ref = "scheduler_missed:{날짜}" — 하루 1건, 놓친 잡은 이벤트로 누적.
  - blocked 도 중복 억제에 포함(DEDUP_STATUSES) — '런북 없음 → blocked' 가 매일 새 과제를 만들지 않게.
  - QG 코드패턴/제안(Task 6): source_ref = "qg:issue:<유형>" / "qg:suggestion:<이모지>" — 그룹당 improvement 1건, 내용이
    바뀌면 refreshed 이벤트. QG 회귀/drift 와 RC FAIL 은 내용 키("qg:regression:<indication>", "qg:drift:<해시>",
    "rc:<memory>") 로 ops_incident 1건 — 날짜 키였다면 매일 13건이 쌓여 원장이 일주일 만에 무용지물이 된다.
어떤 런북으로 고칠 수 있는지 detail 에 '런북: <이름>' 으로 적는다 — 디스패처(scripts/agent_dispatcher.py:pick_runbook)가 이 힌트를 읽는다.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import (
    DEFAULT_DB_PATH, OPEN_STATUSES, add_event, connect, create_task, find_task, list_events, update_task,
)

KST = timezone(timedelta(hours=9))
DEDUP_STATUSES = OPEN_STATUSES + ("blocked",)

# 잡 id → 런북 (None = 런북 없음, 사람 확인). 키 집합은 scheduler.build_job_specs() 의 id 전체와 같아야 한다
# (tests/test_scheduler_job_specs.py 가 대조). 값은 runbooks.list_runbooks() 의 이름이어야 한다
# (tests/test_agent_runbooks.py 가 대조). None = 재실행이 답이 아니거나 그 자체가 관측 잡.
JOB_RUNBOOK: dict[str, str | None] = {
    "domestic_price_pipeline": "domestic_price_catchup",
    "git_auto_backup": None,
    "foreign_approval_sync": "foreign_approval_sync",
    "rule_compliance_audit": None,
    "quality_guard_review": None,
    "competitor_news_weekly": "news_weekly_crawl",
    "competitor_news_daily": "news_daily_fetch",
    "competitor_trend_promote": "competitor_trend_promote",
    "access_insight_fresh_signals": "access_insight_fresh_signals",
    "reimbursement_xnational_sync": "reimbursement_xnational_sync",
    "exchange_rate_refresh": "exchange_rate_refresh",
    "reimb_data_sync": "reimb_data_sync",
    "policy_intel_analysis_sync": "policy_intel_analysis_sync",
    "daily_mailing_dispatch": "daily_mailing_dispatch",
    "daily_mailing_top_news": "daily_mailing_top_news",
    "daily_mailing_health": None,
    "nhis_negotiation_sync": "nhis_negotiation_sync",
    "home_brand_related_refresh": None,
    "domain_freshness": None,                 # 판정기 자체의 실패용. stale 도메인 과제는 freshness.py 가 도메인별 런북 힌트로 만든다
    "scheduler_heartbeat": None,     # T4: /api/health 가 stale 을 보면 같은 source_ref 로 기록(하루 1건) — 사람 확인
    "platform_smoke": None,          # T4: 화면 깨짐(라우트 5xx·키 부재·권한 게이트 붕괴) — 사람 확인
}
# 스케줄에 없는 수동 전용 잡(guarded_job 은 있으나 build_job_specs 미등록) → 런북. JOB_RUNBOOK 과 분리하는 이유:
# tests/test_scheduler_job_specs.py 가 JOB_RUNBOOK 키 집합 == 등록 잡 id 집합을 강제한다. record_job_failure 는
# JOB_RUNBOOK → MANUAL_JOB_RUNBOOK 순으로 힌트를 찾는다(job_runbook()).
MANUAL_JOB_RUNBOOK: dict[str, str | None] = {
    "exchange_rate_recompute": "exchange_rate_recompute",   # 런북이 부르는 FX 재계산 — 실패가 과제가 될 때 같은 런북으로 재시도
}


def job_runbook(job_id: str) -> str | None:
    """잡 id 의 런북 이름. 등록 잡은 JOB_RUNBOOK(None 포함)이 결정하고, 수동 전용 잡만 MANUAL_JOB_RUNBOOK 을 본다."""
    if job_id in JOB_RUNBOOK:
        return JOB_RUNBOOK[job_id]
    return MANUAL_JOB_RUNBOOK.get(job_id)
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
    runbook = job_runbook(job_id)
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


# ── QualityGuard · RuleCompliance 산출물 → 과제 (P1.5 Task 6) ────────────────
# RC FAIL 메모리 → 런북(runbooks.list_runbooks() 이름). 없으면 사람 확인.
RC_RUNBOOK: dict[str, str | None] = {
    "project_xnational_reimbursement": "reimbursement_xnational_sync",
    "project_price_approval_coverage": "foreign_approval_sync",
}
_EMOJI_PREFIX = re.compile(r"^\s*([\U0001F300-\U0001FAFF☀-➿])")


def _content_key(text: str) -> str:
    """숫자를 지운 내용 해시 — '793줄' 이 '801줄' 이 돼도, '28/67846' 이 '28/67900' 이 돼도 같은 항목."""
    return hashlib.sha1(re.sub(r"\d+", "#", str(text)).strip().encode("utf-8")).hexdigest()[:12]


def _issue_group(text: str) -> str:
    """코드 패턴 위반 'BaseScraper 미상속: kr_health_kr.py' → 유형 'BaseScraper 미상속' (파일명은 detail 로)."""
    return str(text).split(":", 1)[0].strip() or "기타"


def _suggestion_group(text: str) -> str:
    """개선 제안의 선두 이모지가 카테고리(📦 모듈 분리 · 🔑 DISEASE_KR · 💊 enrichment · 🔐 DocCheck ...)."""
    m = _EMOJI_PREFIX.match(str(text))
    return m.group(1) if m else "기타"


def _group_detail(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) + "\n" + runbook_line(None)


def _group_signature(detail: str) -> set[str]:
    """detail 의 항목 줄('- ...')을 숫자 무시 내용 키 집합으로 — 줄 수·건수 같은 숫자 변동은 '변화' 가 아니다."""
    return {_content_key(line[2:]) for line in detail.splitlines() if line.startswith("- ")}


def count_source_tasks(source: str, *, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    """source 의 과제 행 수(상태 무관). create_task 는 dedup 시 기존 id 를 돌려주므로 '이번에 새로 만든 수' 는
    호출 전후의 차로 센다 — 두 번째 실행은 0 이어야 한다(잡 counts.tasks_new)."""
    conn = connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM agent_task WHERE source=?", (source,)).fetchone()[0])
    finally:
        conn.close()


def _upsert_group_task(*, source_ref: str, title: str, items: list[str], db_path: str | Path) -> int:
    """그룹 과제: 없으면(또는 닫혔으면) 생성. 열려 있거나 blocked 면 그대로 — 항목 집합이 바뀌었을 때만 detail 갱신 +
    refreshed 이벤트(숫자만 바뀐 건 갱신하지 않는다: updated_at 이 매일 움직이면 원장 화면 첫 페이지가 이걸로 찬다)."""
    detail = _group_detail(items)
    existing = find_task("quality_guard", source_ref, db_path=db_path)
    if existing and existing["status"] in DEDUP_STATUSES:
        if _group_signature(existing["detail"]) != _group_signature(detail):
            update_task(existing["id"], actor="quality_guard", db_path=db_path, detail=detail)
            add_event(existing["id"], actor="quality_guard", event="refreshed", detail=f"{len(items)}건", db_path=db_path)
        return int(existing["id"])
    return create_task(kind="improvement", source="quality_guard", source_ref=source_ref, title=title, detail=detail,
                       priority=3, actor="quality_guard", dedup_statuses=DEDUP_STATUSES, db_path=db_path)


def record_quality_review(result: dict, *, db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, list[int]]:
    """QualityGuardAgent.review_codebase() 반환값 → 과제. 회귀·drift 는 ops_incident(낱개), 코드패턴·제안은 improvement(그룹).

    improvement 는 디스패처가 집지 않는다(CLAIMABLE_KINDS="ops_incident") — P3 개선자의 입력으로 남는다.
    """
    ids: dict[str, list[int]] = {"incident": [], "improvement": []}
    for m in result.get("mfds_regressions") or []:
        ind = str(m.get("indication_id"))
        ids["incident"].append(create_task(
            kind="ops_incident", source="quality_guard", source_ref=f"qg:regression:{ind}",
            title=f"MFDS baseline 회귀 — {ind}",
            detail=f"expected {m.get('expected')} / actual {m.get('actual')} ({m.get('reason', '')})\n"
                   "런북 없음 — `python -m scripts.apply_mfds_official_dates --all --apply` 후 "
                   "`agents/rules/kr_mfds_approval_agent_rules.md` §2 LayerSpec 확인",
            priority=1, actor="quality_guard", dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    for d in result.get("rule_drifts") or []:
        text = f"{d.get('rule')} — {d.get('file')}"
        ids["incident"].append(create_task(
            kind="ops_incident", source="quality_guard", source_ref=f"qg:drift:{_content_key(text)}",
            title=f"규칙↔코드 drift — {d.get('rule')}", detail=f"{text}\n{runbook_line(None)}",
            priority=2 if d.get("severity") == "ERROR" else 3, actor="quality_guard",
            dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    groups: dict[str, list[str]] = {}
    for text in result.get("code_issues") or []:
        groups.setdefault(f"qg:issue:{_issue_group(text)}", []).append(str(text))
    for text in result.get("suggestions") or []:
        groups.setdefault(f"qg:suggestion:{_suggestion_group(text)}", []).append(str(text))
    for ref, items in groups.items():
        kind_label = "코드 패턴 위반" if ref.startswith("qg:issue:") else "개선 제안"
        ids["improvement"].append(_upsert_group_task(
            source_ref=ref, title=f"QG {kind_label} — {ref.split(':', 2)[2]} ({len(items)}건)", items=items, db_path=db_path))
    return ids


def record_compliance_results(results, *, db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    """RuleComplianceAgent.audit() 의 CheckResult 목록 → FAIL 만 ops_incident, 메모리당 1건(rc:<memory>)."""
    ids: list[int] = []
    for r in results:
        if r.status != "fail":
            continue
        ids.append(create_task(
            kind="ops_incident", source="rule_compliance", source_ref=f"rc:{r.memory}",
            title=f"Rule Compliance FAIL — {r.memory}",
            detail=f"{r.detail}\n수치: {json.dumps(r.metrics, ensure_ascii=False, default=str)[:800]}\n"
                   f"{runbook_line(RC_RUNBOOK.get(r.memory))}",
            priority=2, actor="rule_compliance", dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    return ids


# ── 구현 대기 기능 부채 (P1.5 Task 7) ─────────────────────────────────────────
# placeholder 잡은 매일 '성공' 으로 집계돼 관측에 거짓 신호를 섞는다. 스케줄에서 내리고(scheduler.build_job_specs)
# 미구현 사실만 원장에 남긴다. source_ref 에 날짜가 없으므로 사람이 닫을 때까지 1건이고,
# DEDUP_STATUSES(=OPEN_STATUSES + blocked) 때문에 blocked 로 끝난 부채도 재기동마다 새로 생기지 않는다.
# closed 로 닫힌 뒤 다시 호출되면(placeholder 회귀) 새로 열린다 — 구현이 끝나면 여기서 항목을 지운다.
KNOWN_DEBTS: tuple[dict[str, str], ...] = (
    {
        "slug": "amjilsim_reporters",
        "title": "암질심·약평위 리포터 3종 백엔드 미구현 (D-2 예측 / D+1 리뷰 / 월간 트렌드)",
        "detail": (
            "scheduler.py 의 amjilsim_d_minus_2_reporter_job(:721) · amjilsim_d_plus_1_reporter_job(:748) · "
            "amjilsim_monthly_trend_job(:775) 은 calendar 조회 뒤 'TODO: backend 호출' 주석만 남기고 끝난다 — "
            "매일 성공으로 집계되지만 보고서는 생성되지 않는다. P1.5 Task 7 에서 스케줄 등록을 해제했다(함수는 보존).\n"
            "필요한 것: agents/amjilsim_tracker/agent.py 의 run_d_minus_2 / run_d_plus_1 / run_monthly_trend 구현. "
            "구현 후 build_job_specs 에 다시 등록하고 JOB_RUNBOOK 에 런북을 매핑한다."
        ),
    },
    {
        "slug": "hira_schedule_fetcher",
        "title": "HIRA 차수 일정 fetcher 미구현 — 2026-12-03 이후 위원회 일정 공백",
        "detail": (
            "scheduler.py 의 hira_schedule_fetcher_job(:798) 은 본문 전체가 placeholder 다. amjilsim_sessions 의 "
            "SCHEDULED 는 약평위 2026-12-03 · 암질심 2026-12-23 까지만 있어 그 뒤로는 D-2/D+1 게이팅과 "
            "위원회 보드가 영구 idle 이 된다.\n"
            "필요한 것: agents/amjilsim_tracker/schedule_fetcher.py (HIRA 공식 일정 페이지 파싱 + 실패 시 검색 fallback) "
            "+ amjilsim_sessions 적재. 공백 자체는 Task 3 의 committee_schedule 신선도 판정이 "
            "'향후 60일 SCHEDULED 0건' 으로 먼저 경고한다."
        ),
    },
)


def record_known_debts(*, db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    """구현 대기 기능을 improvement 과제로 등록한다(이미 열려 있으면 그 id 를 돌려준다). 스케줄러 기동 시 1회.

    kind=improvement 라 디스패처(CLAIMABLE_KINDS='ops_incident')는 집지 않는다 — P3 개선자의 입력이다.
    """
    ids: list[int] = []
    for debt in KNOWN_DEBTS:
        ids.append(create_task(
            kind="improvement", source="known_debt", source_ref=f"debt:{debt['slug']}",
            title=debt["title"], detail=f"{debt['detail']}\n\n{runbook_line(None)}",
            priority=3, dedup_statuses=DEDUP_STATUSES, db_path=db_path,
        ))
    return ids
