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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .dashboard_scope import scope_from_dict
from .run_store import (
    claim_for_sending,
    expire_pending_before,
    find_regular_run,
    get_run,
    list_failed_sends,
    list_runs_for_date,
    record_error_run,
    update_run,
)
from .schedule import KST, is_due
from .storage import DEFAULT_DB_PATH, ensure_daily_mailing_tables, persist_daily_mailing_run
from .subscription_bridge import ensure_delivery_columns, subscription_to_scope

logger = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 3
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
    with sqlite3.connect(str(db_path)) as conn:
        ensure_delivery_columns(conn)
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM mail_subscription" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
        return [_sub_row_to_dict(r) for r in conn.execute(sql).fetchall()]


def _now(deps: DispatchDeps) -> datetime:
    return deps.now().astimezone(KST)


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


def _mark_last_sent(subscription_id: int, sent_at: str, db_path: str | Path) -> None:
    """발송 성공 후 표시용 bookkeeping — 실패해도 이미 발송된 run 상태(sent)를 되돌리지 않는다."""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("UPDATE mail_subscription SET last_sent_at=? WHERE id=?", (sent_at, subscription_id))
            conn.commit()
    except sqlite3.Error as exc:
        logger.warning("[daily_mailing] 구독 %s last_sent_at 갱신 실패 (발송은 완료): %s", subscription_id, exc)


def send_run(run_id: str, *, deps: DispatchDeps, from_statuses: tuple[str, ...],
             recipients_override: list[str] | None = None) -> dict | None:
    run = get_run(run_id, db_path=deps.db_path)
    if run is None or not claim_for_sending(run_id, from_statuses=from_statuses, db_path=deps.db_path):
        return run
    recipients = recipients_override or _json_list(run.get("recipients_json"))
    attempts = int(run.get("send_attempts") or 0) + 1
    try:
        res = deps.send(recipients=recipients, subject=run.get("email_subject") or "",
                        body_html=run.get("email_html") or "")
    except Exception as exc:
        res = {"ok": False, "sent": False, "message": f"{type(exc).__name__}: {exc}"}
    if res.get("ok") and res.get("sent"):
        sent_at = _now(deps).isoformat()
        update_run(run_id, db_path=deps.db_path,
                   delivery_status="no_news_sent" if run.get("status") == "no_news" else "sent",
                   sent_at=sent_at, gmail_message_id=res.get("gmail_message_id"),
                   send_attempts=attempts, delivery_error=None)
        if not run.get("is_test") and run.get("subscription_id") is not None:
            _mark_last_sent(run["subscription_id"], sent_at, deps.db_path)
    else:
        update_run(run_id, db_path=deps.db_path,
                   delivery_status="send_failed" if attempts < MAX_SEND_ATTEMPTS else "held",
                   send_attempts=attempts, delivery_error=str(res.get("message") or "send failed")[:500])
    return get_run(run_id, db_path=deps.db_path)


def _route_new_run(result, sub: dict, deps: DispatchDeps) -> dict | None:
    if result.status == "held":
        return _persist(result, deps, delivery_status="held", approval_status="not_required")
    if sub.get("delivery_mode") == "auto":
        _persist(result, deps, delivery_status="ready_to_send", approval_status="not_required")
        return send_run(result.run_id, deps=deps, from_statuses=("ready_to_send",))
    return _persist(result, deps, delivery_status="pending_approval", approval_status="pending")


def retry_failed_sends(deps: DispatchDeps) -> int:
    runs = list_failed_sends(db_path=deps.db_path, max_attempts=MAX_SEND_ATTEMPTS)
    for run in runs:
        send_run(run["run_id"], deps=deps, from_statuses=("send_failed",))
    return len(runs)


def dispatch_due_subscriptions(deps: DispatchDeps) -> dict:
    """10분 주기: 만료 처리 → 발송 시각 도래 구독 생성·분기 → 실패 발송 재시도."""
    now = _now(deps)
    run_date = now.date().isoformat()
    ensure_daily_mailing_tables(deps.db_path)
    summary: dict[str, Any] = {"run_date": run_date,
                               "expired": expire_pending_before(run_date, db_path=deps.db_path),
                               "generated": [], "skipped": 0, "errors": []}
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
            run = _route_new_run(result, sub, deps)
        except Exception as exc:
            logger.exception("[daily_mailing] 구독 %s 생성 실패", sub["id"])
            record_error_run(subscription_id=sub["id"], run_date=run_date,
                             error=f"{type(exc).__name__}: {exc}", now=now, db_path=deps.db_path)
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
    update_run(run_id, db_path=deps.db_path, approval_status="approved",
               approved_by=approver, approved_at=now.isoformat())
    return send_run(run_id, deps=deps, from_statuses=("pending_approval",)) or {}


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
    scope = scope_from_dict(subscription_to_scope(sub))
    result = deps.generate(scope, name=sub["name"], now=now,
                           top_ma_news=_top_news(deps, now.date().isoformat()), is_test=True)
    _persist(result, deps, delivery_status="ready_to_send", approval_status="not_required")
    run = send_run(result.run_id, deps=deps, from_statuses=("ready_to_send",),
                   recipients_override=[requester_email])
    return {"run_id": result.run_id, "status": result.status, "delivery_status": (run or {}).get("delivery_status")}


def health_check(deps: DispatchDeps) -> dict:
    """오늘 이미 발송 시각이 지난 구독 중 미생성·보류·발송실패·오류·만료 + Gmail 토큰 상태."""
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
    try:
        gmail_ok = bool(deps.gmail_ready())
    except Exception:
        gmail_ok = False
    if not gmail_ok:
        problems.append({"subscription_id": None, "name": "-", "problem": "gmail_token_invalid"})
    return {"run_date": run_date, "checked_at": now.isoformat(), "ok": not problems,
            "problems": problems, "gmail_ready": gmail_ok}
