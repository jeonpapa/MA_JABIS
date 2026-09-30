"""복구 런북 — 운영 감시자가 실행하는 결정적 절차 (LLM 없음).

프로덕션 안에서 실행된다(스케줄러·DB 접근 필요). 각 런북은 {'ok', 'summary', 'detail'} 를 돌려주고,
예외를 밖으로 던지지 않는다 — 실패도 결과값으로 표현해 과제에 기록한다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]


class RunbookError(RuntimeError):
    """등록되지 않은 런북."""


def _load_config() -> dict:
    return json.loads((REPO_ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


def _domestic_run_sync(config: dict, base_dir: Path, max_pages: int = 3, force: bool = False) -> dict:
    from agents.domestic_catchup import run_sync
    return run_sync(config, base_dir, max_pages=max_pages, force=force)


def _dispatch_deps():
    from agents.daily_mailing.dispatch import DispatchDeps
    return DispatchDeps.production()


def _dispatch_due(deps):
    from agents.daily_mailing.dispatch import dispatch_due_subscriptions
    return dispatch_due_subscriptions(deps)


def _runbook_domestic_price_catchup() -> dict:
    out = _domestic_run_sync(_load_config(), REPO_ROOT, max_pages=3, force=True)
    ingested = out.get("ingested") or []
    errors = out.get("errors") or []
    if errors and not ingested:
        return {"ok": False, "summary": f"국내 약가 캐치업 실패: {errors[0]}", "detail": out}
    dates = ", ".join(str(i.get("apply_date")) for i in ingested) or "신규 없음"
    return {"ok": True, "summary": f"국내 약가 캐치업 완료 — {dates}", "detail": out}


def _runbook_daily_mailing_dispatch() -> dict:
    res = _dispatch_due(_dispatch_deps())
    generated = res.get("generated") or []
    errors = res.get("errors") or []
    ok = not errors
    return {"ok": ok,
            "summary": f"Daily Mailing 판정 — {len(generated)}건 생성, 재시도 {res.get('retried', 0)}, 오류 {len(errors)}",
            "detail": res}


def _runbook_daily_mailing_top_news() -> dict:
    from agents.daily_mailing.schedule import KST
    run_date = datetime.now(KST).date().isoformat()
    items = _dispatch_deps().load_top_ma_news(run_date, retry_failed=True)
    return {"ok": bool(items), "summary": f"주요 MA 뉴스 {len(items)}건", "detail": {"run_date": run_date}}


RUNBOOKS: dict[str, Callable[[], dict]] = {
    "domestic_price_catchup": _runbook_domestic_price_catchup,
    "daily_mailing_dispatch": _runbook_daily_mailing_dispatch,
    "daily_mailing_top_news": _runbook_daily_mailing_top_news,
}


def list_runbooks() -> list[str]:
    return sorted(RUNBOOKS)


def run(name: str) -> dict:
    fn = RUNBOOKS.get(name)
    if fn is None:
        raise RunbookError(f"등록되지 않은 런북: {name}")
    try:
        result = fn()
    except Exception as exc:  # 런북 실패는 결과값으로 표현한다
        logger.exception("[agent-team] 런북 실패: %s", name)
        return {"ok": False, "summary": f"{name} 실행 중 예외: {type(exc).__name__}: {exc}"[:300], "detail": {}}
    result.setdefault("detail", {})
    return result
