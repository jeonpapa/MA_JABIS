"""Daily Mailing 발송 오케스트레이션 — 판정·생성·승인·발송·재시도·테스트·헬스체크.

설계: docs/superpowers/specs/2026-09-18-daily-mailing-service-design.md §4·§6·§8.
콘텐츠 생성은 engine.generate_run(슬라이스 A)을 DispatchDeps.generate 로 주입받는다.
상태: pending_approval → (승인) → sending → sent / send_failed(→재시도 최대 3회→held)
      ready_to_send(자동) → sending → sent | no_news_sent ; held / error / expired
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .dashboard_scope import scope_from_dict
from .run_store import (
    RETRYABLE_SEND_STATUSES,
    claim_for_sending,
    expire_pending_before,
    find_regular_run,
    get_run,
    list_failed_sends,
    list_runs_for_date,
    list_sending_runs,
    record_error_run,
    update_run,
)
from .schedule import KST, is_due
from .storage import DEFAULT_DB_PATH, connect, ensure_daily_mailing_tables, persist_daily_mailing_run
from .subscription_bridge import ensure_delivery_columns, subscription_to_scope

logger = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 3
RETRY_MIN_INTERVAL_MINUTES = 9   # 10분 tick 기준: 실패 직후 같은 tick 재시도 금지, 다음 tick 부터
STALE_SENDING_MINUTES = 15
STALE_SENDING_ERROR = "발송 중 프로세스 중단 — 실제 발송 여부 확인 필요"
STUCK_READY_MINUTES = 5   # ready_to_send 가 이만큼 지나도 선점되지 않았으면 헬스체크 문제로 표시 (재시도 대상)
STUCK_READY_DETAIL = "발송 대기(ready_to_send) 정체 — 선점 전 중단, 다음 주기 재시도 대상"
# 발송 성공 '후' bookkeeping(update_run·last_sent_at) 이 database-is-locked 로 실패하면 sent_at·gmail_message_id 를
# 잃고 run 이 sending → held(프로세스 중단 오인) 로 빠진다. 짧게 재시도하고, 그래도 안 되면 복구 가능한 ERROR 로그.
DB_WRITE_RETRY_ATTEMPTS = 3
DB_WRITE_RETRY_BACKOFF_SECONDS = (0.5, 1.0)
REPO_ROOT = Path(__file__).resolve().parents[2]
PROBLEM_STATUSES = ("held", "send_failed", "error", "expired")


class ApprovalError(ValueError):
    """승인/반려할 수 없는 상태."""


def _default_now() -> datetime:
    return datetime.now(KST)


@dataclass
class DispatchDeps:
    db_path: str | Path = DEFAULT_DB_PATH
    generate: Callable[..., Any] | None = None          # (scope, *, name, now, top_ma_news, is_test) -> RunResult
    load_top_ma_news: Callable[[str], list] | None = None  # run_date -> list[BriefItem]
    send: Callable[..., dict] | None = None             # (*, recipients, subject, body_html) -> dict
    now: Callable[[], datetime] = _default_now
    gmail_ready: Callable[[], bool] = lambda: False
    sleep: Callable[[float], None] = time.sleep          # DB 쓰기 재시도 백오프 (테스트에서 주입)

    @classmethod
    def production(cls, db_path: str | Path = DEFAULT_DB_PATH) -> "DispatchDeps":
        """실제 엔진·Gmail 연결. 엔진/LLM 클라이언트는 첫 생성 시점에 지연 생성."""
        from agents.notify.gmail_delivery import gmail_configured, materialize_token_from_env, send_gmail_message

        from .brief import BriefItem
        from .engine import EngineConfig, EngineDeps, generate_run, generate_top_ma_news
        from .top_news import load_top_ma_news, store_top_ma_news

        config = EngineConfig.from_settings(_load_settings())
        cache: dict[str, Any] = {}

        def engine_deps():
            if "deps" not in cache:
                cache["deps"] = EngineDeps.default(db_path=str(db_path))
            return cache["deps"]

        def generate(scope, **kwargs):
            return generate_run(scope, deps=engine_deps(), config=config, **kwargs)

        def top_ma_news(run_date: str) -> list:
            cached = load_top_ma_news(run_date, db_path=db_path)
            if cached is not None:
                return [BriefItem.from_dict(d) for d in cached]
            items = generate_top_ma_news(deps=engine_deps(), config=config)
            store_top_ma_news(run_date, [i.to_dict() for i in items], db_path=db_path)
            return items

        def token_path():
            try:
                return materialize_token_from_env()
            except ValueError as exc:
                logger.error("[daily_mailing] Gmail 토큰 secret 오류: %s", exc)
                return None

        def send(**kwargs) -> dict:
            path = token_path()
            if path is None:
                return {"ok": False, "sent": False, "message": "Gmail 토큰 없음 (GOOGLE_TOKEN_JSON)"}
            return send_gmail_message(token_path=path, **kwargs)

        def gmail_ready() -> bool:
            path = token_path()
            return bool(path) and gmail_configured(path)

        return cls(db_path=db_path, generate=generate, load_top_ma_news=top_ma_news, send=send,
                   gmail_ready=gmail_ready)


def _load_settings() -> dict:
    try:
        return json.loads((REPO_ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _json_list(raw) -> list:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _sub_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    return {
        "id": d["id"], "name": d["name"], "owner_email": d.get("owner_email"),
        "keywords": _json_list(d.get("keywords_json")), "media": _json_list(d.get("media_json")),
        "schedule": d.get("schedule"), "time": d.get("time"), "weekDay": d.get("week_day"),
        "emails": _json_list(d.get("emails_json")), "active": bool(d.get("active")),
        "companies": _json_list(d.get("companies_json")), "brands": _json_list(d.get("brands_json")),
        "policy_topics": _json_list(d.get("policy_topics_json")),
        "disease_areas": _json_list(d.get("disease_areas_json")),
        "custom_sources": _json_list(d.get("custom_sources_json")),
        "delivery_mode": d.get("delivery_mode") or "approval",
        "include_top_ma_news": bool(d.get("include_top_ma_news", 1)),
        "last_sent_at": d.get("last_sent_at"),
    }


def load_subscriptions(db_path: str | Path, *, active_only: bool = True) -> list[dict]:
    with connect(db_path) as conn:
        ensure_delivery_columns(conn)
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM mail_subscription" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
        return [_sub_row_to_dict(r) for r in conn.execute(sql).fetchall()]


def _now(deps: DispatchDeps) -> datetime:
    return deps.now().astimezone(KST)


def _parse_ts(value) -> datetime | None:
    """DB 의 ISO 타임스탬프 → aware datetime (naive 는 KST 로 간주). 파싱 실패/NULL 은 None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def recover_stale_sending(deps: DispatchDeps, *, older_than_minutes: int = STALE_SENDING_MINUTES) -> int:
    """'sending' 으로 선점된 뒤 프로세스가 죽어 멈춘 run 을 held 로 닫는다 (설계 §8).

    메일이 이미 나갔을 수 있으므로 **자동 재발송하지 않는다** — held 는 헬스체크 문제 상태라
    관리자가 실제 발송 여부를 확인한다. claimed_at 이 없는(컬럼 도입 전) sending 행도 stale 로 본다.
    """
    cutoff = _now(deps) - timedelta(minutes=older_than_minutes)
    recovered = 0
    for run in list_sending_runs(db_path=deps.db_path):
        claimed = _parse_ts(run.get("claimed_at"))
        if claimed is not None and claimed > cutoff:
            continue  # 진행 중 (fresh)
        update_run(run["run_id"], db_path=deps.db_path, delivery_status="held", delivery_error=STALE_SENDING_ERROR)
        logger.error("[daily_mailing] run %s 발송 선점 %s 이후 중단 — held 로 전환 (실제 발송 여부 확인 필요)",
                     run["run_id"], run.get("claimed_at"))
        recovered += 1
    return recovered


def _top_news(deps: DispatchDeps, run_date: str) -> list:
    if deps.load_top_ma_news is None:
        return []
    try:
        return list(deps.load_top_ma_news(run_date) or [])
    except Exception:
        logger.exception("[daily_mailing] 주요 MA 뉴스 로드 실패 — 없이 진행")
        return []


def _persist(result, deps: DispatchDeps, *, delivery_status: str, approval_status: str) -> dict | None:
    payload = dict(result.payload)
    payload["delivery_status"] = delivery_status
    payload["approval_status"] = approval_status
    persist_daily_mailing_run(payload, articles=result.articles, db_path=deps.db_path)
    return get_run(result.run_id, db_path=deps.db_path)


def _retry_db_write(write: Callable[[], None], *, deps: DispatchDeps, describe: str) -> bool:
    """database-is-locked(OperationalError) 에 한해 DB_WRITE_RETRY_ATTEMPTS 회 재시도. 최종 실패는 ERROR 로그 + False.

    발송이 이미 끝난 뒤의 bookkeeping 에 쓴다 — 예외를 전파하면 API 500/스케줄러 중단으로 이어지지만
    메일은 이미 나갔으므로, 운영자가 로그의 식별자(run_id·gmail_message_id)로 수동 반영할 수 있게 남긴다.
    """
    for attempt in range(1, DB_WRITE_RETRY_ATTEMPTS + 1):
        try:
            write()
            return True
        except sqlite3.OperationalError as exc:
            if attempt >= DB_WRITE_RETRY_ATTEMPTS:
                logger.error("[daily_mailing] DB 쓰기 %d회 모두 실패 — %s: %s", attempt, describe, exc)
                return False
            backoff = DB_WRITE_RETRY_BACKOFF_SECONDS[min(attempt - 1, len(DB_WRITE_RETRY_BACKOFF_SECONDS) - 1)]
            logger.warning("[daily_mailing] DB 잠김 (%d/%d) — %.1fs 후 재시도: %s",
                           attempt, DB_WRITE_RETRY_ATTEMPTS, backoff, describe)
            deps.sleep(backoff)
    return False


def _mark_last_sent(subscription_id: int, sent_at: str, deps: DispatchDeps) -> None:
    """발송 성공 후 표시용 bookkeeping — 실패해도 이미 발송된 run 상태(sent)를 되돌리지 않는다."""
    def write() -> None:
        with connect(deps.db_path) as conn:
            conn.execute("UPDATE mail_subscription SET last_sent_at=? WHERE id=?", (sent_at, subscription_id))
            conn.commit()
    try:
        _retry_db_write(write, deps=deps, describe=f"구독 {subscription_id} last_sent_at={sent_at} 갱신 (발송은 완료)")
    except sqlite3.Error as exc:
        logger.warning("[daily_mailing] 구독 %s last_sent_at 갱신 실패 (발송은 완료): %s", subscription_id, exc)


def _send_claimed(run_id: str, *, deps: DispatchDeps, from_statuses: tuple[str, ...],
                  recipients_override: list[str] | None = None,
                  on_claim: dict[str, Any] | None = None) -> tuple[dict | None, bool]:
    """선점(claim) → 발송 → 상태 갱신. (run, claimed) 를 돌려준다.

    claimed=False 면 다른 요청(web 승인 / scheduler 재시도)이 이미 선점한 것 — 호출부가 판단한다.
    on_claim 은 선점에 **성공한 요청만** 기록하는 필드(예: 승인자) — 진 요청이 덮어쓰지 못한다.
    """
    run = get_run(run_id, db_path=deps.db_path)
    if run is None:
        return None, False
    if not claim_for_sending(run_id, from_statuses=from_statuses,
                             claimed_at=_now(deps).isoformat(), db_path=deps.db_path):
        return run, False
    if on_claim:
        update_run(run_id, db_path=deps.db_path, **on_claim)
    recipients = recipients_override or _json_list(run.get("recipients_json"))
    attempts = int(run.get("send_attempts") or 0) + 1
    try:
        res = deps.send(recipients=recipients, subject=run.get("email_subject") or "",
                        body_html=run.get("email_html") or "")
    except Exception as exc:
        res = {"ok": False, "sent": False, "message": f"{type(exc).__name__}: {exc}"}
    if res.get("ok") and res.get("sent"):
        sent_at = _now(deps).isoformat()
        message_id = res.get("gmail_message_id")
        final_status = "no_news_sent" if run.get("status") == "no_news" else "sent"
        # 메일은 이미 나갔다 — 여기서 예외가 새면 run 이 'sending' 에 남아 나중에 held(프로세스 중단)로 오인된다
        _retry_db_write(
            lambda: update_run(run_id, db_path=deps.db_path, delivery_status=final_status, sent_at=sent_at,
                               gmail_message_id=message_id, send_attempts=attempts, delivery_error=None),
            deps=deps,
            describe=(f"run {run_id} 발송 완료 기록 실패 — 수동 반영 필요: delivery_status={final_status} "
                      f"sent_at={sent_at} gmail_message_id={message_id} send_attempts={attempts}"))
        if not run.get("is_test") and run.get("subscription_id") is not None:
            _mark_last_sent(run["subscription_id"], sent_at, deps)
    else:
        failed_status = "send_failed" if attempts < MAX_SEND_ATTEMPTS else "held"
        error = str(res.get("message") or "send failed")[:500]
        _retry_db_write(
            lambda: update_run(run_id, db_path=deps.db_path, delivery_status=failed_status,
                               send_attempts=attempts, delivery_error=error),
            deps=deps, describe=f"run {run_id} 발송 실패 기록 (attempt {attempts}: {error})")
    return get_run(run_id, db_path=deps.db_path), True


def send_run(run_id: str, *, deps: DispatchDeps, from_statuses: tuple[str, ...],
             recipients_override: list[str] | None = None) -> dict | None:
    """scheduler 경로 — 선점에 졌으면 조용히 현재 상태만 돌려준다 (다른 쪽이 발송 중)."""
    return _send_claimed(run_id, deps=deps, from_statuses=from_statuses,
                         recipients_override=recipients_override)[0]


def _route_new_run(result, sub: dict, deps: DispatchDeps) -> dict | None:
    if result.status == "held":
        return _persist(result, deps, delivery_status="held", approval_status="not_required")
    if sub.get("delivery_mode") == "auto":
        _persist(result, deps, delivery_status="ready_to_send", approval_status="not_required")
        return send_run(result.run_id, deps=deps, from_statuses=("ready_to_send",))
    return _persist(result, deps, delivery_status="pending_approval", approval_status="pending")


def retry_failed_sends(deps: DispatchDeps) -> int:
    """오늘(run_date) send_failed·ready_to_send 재시도 — 마지막 선점(claimed_at) 후 RETRY_MIN_INTERVAL_MINUTES 이상 지난 run 만.

    전날 실패분은 재시도하지 않는다 (dispatch 가 expire_pending_before 로 expired 처리 — 며칠 늦은 발송 방지).

    실패 직후 같은 tick 에서 바로 재시도하지 않고 주기 간격을 둔다 (일시 장애 회복 여유).
    claimed_at 이 없는 행(컬럼 도입 전, 또는 persist 직후·선점 전에 프로세스가 죽어 남은 ready_to_send 고아)은
    선점된 적이 없으므로 바로 재시도한다 — 발송 시도 0회라 이중 발송 위험이 없다.
    """
    now = _now(deps)
    cutoff = now - timedelta(minutes=RETRY_MIN_INTERVAL_MINUTES)
    retried = 0
    for run in list_failed_sends(db_path=deps.db_path, max_attempts=MAX_SEND_ATTEMPTS,
                                 run_date=now.date().isoformat()):
        claimed = _parse_ts(run.get("claimed_at"))
        if claimed is not None and claimed > cutoff:
            continue  # 직전 tick 에서 막 실패 — 다음 주기로
        try:
            send_run(run["run_id"], deps=deps, from_statuses=RETRYABLE_SEND_STATUSES)
        except sqlite3.OperationalError as exc:
            logger.error("[daily_mailing] run %s 재시도 중 DB 잠김 — 다음 주기로: %s", run["run_id"], exc)
            continue
        retried += 1
    return retried


def _record_generate_error(sub_id: int, run_date: str, exc: BaseException, *, now: datetime,
                           deps: DispatchDeps) -> None:
    """생성 예외를 그날의 error run 으로 기록 (설계 §6). DB 잠김이면 기록을 포기하고 다른 구독 처리를 계속한다."""
    try:
        record_error_run(subscription_id=sub_id, run_date=run_date, error=f"{type(exc).__name__}: {exc}",
                         now=now, db_path=deps.db_path)
    except sqlite3.OperationalError as db_exc:
        logger.error("[daily_mailing] 구독 %s 오류 run 기록 실패 (DB 잠김) — 다음 주기에 재생성 시도: %s", sub_id, db_exc)


def dispatch_due_subscriptions(deps: DispatchDeps) -> dict:
    """10분 주기: 중단된 sending 복구 → 만료 처리 → 발송 시각 도래 구독 생성·분기 → 실패 발송 재시도."""
    now = _now(deps)
    run_date = now.date().isoformat()
    ensure_daily_mailing_tables(deps.db_path)
    summary: dict[str, Any] = {"run_date": run_date,
                               "recovered": recover_stale_sending(deps),
                               "expired": expire_pending_before(run_date, db_path=deps.db_path),
                               "generated": [], "skipped": 0, "errors": [], "deferred": []}
    top: list | None = None
    for sub in load_subscriptions(deps.db_path):
        if not is_due(sub, now):
            continue
        if find_regular_run(sub["id"], run_date, db_path=deps.db_path):
            summary["skipped"] += 1
            continue
        try:
            if top is None:
                top = _top_news(deps, run_date)
            scope = scope_from_dict(subscription_to_scope(sub))
            result = deps.generate(scope, name=sub["name"], now=now, top_ma_news=top, is_test=False)
        except Exception as exc:
            logger.exception("[daily_mailing] 구독 %s 생성 실패", sub["id"])
            _record_generate_error(sub["id"], run_date, exc, now=now, deps=deps)
            summary["errors"].append(sub["id"])
            continue
        try:
            run = _route_new_run(result, sub, deps)
        except sqlite3.OperationalError as exc:
            # 저장/선점 단계의 DB 잠김은 일시 장애 — 그날을 점유하는 error run 을 남기지 않고 다음 주기에 재시도
            logger.error("[daily_mailing] 구독 %s run 저장 중 DB 잠김 — 이번 주기 건너뜀 (다음 주기 재시도): %s",
                         sub["id"], exc)
            summary["deferred"].append(sub["id"])
            continue
        except Exception as exc:
            logger.exception("[daily_mailing] 구독 %s run 저장/분기 실패", sub["id"])
            _record_generate_error(sub["id"], run_date, exc, now=now, deps=deps)
            summary["errors"].append(sub["id"])
            continue
        summary["generated"].append({"subscription_id": sub["id"], "run_id": result.run_id,
                                     "delivery_status": (run or {}).get("delivery_status")})
    summary["retried"] = retry_failed_sends(deps)
    return summary


def approve_run(run_id: str, *, approver: str, deps: DispatchDeps) -> dict:
    run = get_run(run_id, db_path=deps.db_path)
    if run is None:
        raise LookupError(run_id)
    if run.get("delivery_status") != "pending_approval" or run.get("approval_status") != "pending":
        raise ApprovalError(f"승인 대기 상태가 아님: {run.get('delivery_status')}/{run.get('approval_status')}")
    now = _now(deps)
    if run.get("run_date") and run["run_date"] < now.date().isoformat():
        update_run(run_id, db_path=deps.db_path, delivery_status="expired")
        raise ApprovalError("당일 승인 기한이 지나 만료되었습니다")
    # 승인자 기록은 선점에 성공한 요청만 — 동시 승인에서 진 요청이 stale 200 을 받거나 승인자를 덮어쓰지 않는다
    sent, claimed = _send_claimed(run_id, deps=deps, from_statuses=("pending_approval",),
                                  on_claim={"approval_status": "approved", "approved_by": approver,
                                            "approved_at": now.isoformat()})
    if not claimed:
        raise ApprovalError("다른 요청이 이미 발송을 진행 중입니다")
    return sent or {}


def reject_run(run_id: str, *, approver: str, deps: DispatchDeps) -> dict:
    run = get_run(run_id, db_path=deps.db_path)
    if run is None:
        raise LookupError(run_id)
    if run.get("delivery_status") != "pending_approval":
        raise ApprovalError(f"승인 대기 상태가 아님: {run.get('delivery_status')}")
    update_run(run_id, db_path=deps.db_path, delivery_status="held", approval_status="rejected",
               approved_by=approver, approved_at=_now(deps).isoformat())
    return get_run(run_id, db_path=deps.db_path) or {}


def send_test(subscription_id: int, *, requester_email: str, deps: DispatchDeps) -> dict:
    """테스트 메일: 게이트·승인 모드와 무관하게 즉시 생성 → 요청자에게만 [TEST] 발송 (정규 run 과 분리)."""
    sub = next((s for s in load_subscriptions(deps.db_path, active_only=False) if s["id"] == subscription_id), None)
    if sub is None:
        raise LookupError(subscription_id)
    now = _now(deps)
    try:
        scope = scope_from_dict(subscription_to_scope(sub))
        result = deps.generate(scope, name=sub["name"], now=now,
                               top_ma_news=_top_news(deps, now.date().isoformat()), is_test=True)
    except Exception as exc:
        # 백그라운드 스레드에서 실행되므로 로그만으로는 묻힌다 — 테스트 오류 run 으로 칸반에 남기고 재전파
        logger.exception("[daily_mailing] 구독 %s 테스트 브리핑 생성 실패", subscription_id)
        record_error_run(subscription_id=subscription_id, run_date=now.date().isoformat(),
                         error=f"{type(exc).__name__}: {exc}", now=now, is_test=True, db_path=deps.db_path)
        raise
    _persist(result, deps, delivery_status="ready_to_send", approval_status="not_required")
    run = send_run(result.run_id, deps=deps, from_statuses=("ready_to_send",),
                   recipients_override=[requester_email])
    return {"run_id": result.run_id, "status": result.status, "delivery_status": (run or {}).get("delivery_status")}


def _stuck_ready(run: dict, now: datetime) -> bool:
    """ready_to_send 인 채 STUCK_READY_MINUTES 넘게 선점되지 않은 run (생성 시각 기준; 없으면 정체로 본다)."""
    if run.get("delivery_status") != "ready_to_send":
        return False
    generated = _parse_ts(run.get("generated_at")) or _parse_ts(run.get("created_at"))
    return generated is None or generated <= now - timedelta(minutes=STUCK_READY_MINUTES)


def health_check(deps: DispatchDeps) -> dict:
    """오늘 이미 발송 시각이 지난 구독 중 미생성·보류·발송실패·오류·만료·발송대기 정체 + Gmail 토큰 상태."""
    now = _now(deps)
    run_date = now.date().isoformat()
    runs = {r["subscription_id"]: r for r in list_runs_for_date(run_date, db_path=deps.db_path)}
    problems: list[dict] = []
    for sub in load_subscriptions(deps.db_path):
        if not is_due(sub, now):
            continue
        run = runs.get(sub["id"])
        if run is None:
            problems.append({"subscription_id": sub["id"], "name": sub["name"], "problem": "not_generated"})
        elif run.get("delivery_status") in PROBLEM_STATUSES:
            problems.append({"subscription_id": sub["id"], "name": sub["name"],
                             "problem": run["delivery_status"], "detail": run.get("delivery_error")})
        elif _stuck_ready(run, now):
            problems.append({"subscription_id": sub["id"], "name": sub["name"],
                             "problem": "ready_to_send", "detail": STUCK_READY_DETAIL})
    try:
        gmail_ok = bool(deps.gmail_ready())
    except Exception:
        gmail_ok = False
    if not gmail_ok:
        problems.append({"subscription_id": None, "name": "-", "problem": "gmail_token_invalid"})
    return {"run_date": run_date, "checked_at": now.isoformat(), "ok": not problems,
            "problems": problems, "gmail_ready": gmail_ok}
