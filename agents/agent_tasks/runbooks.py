"""복구 런북 — 운영 감시자가 실행하는 결정적 절차 (LLM 없음).

프로덕션 안에서 실행된다(스케줄러·DB 접근 필요). 각 런북은 {'ok', 'summary', 'detail'} 를 돌려주고,
예외를 밖으로 던지지 않는다 — 실패도 결과값으로 표현해 과제에 기록한다.

P1.5 Task 5 — 두 실행 모드:
  동기(_from_job):   웹 프로세스 안에서 `scheduler` 모듈을 지연 import 해 **같은 guarded 잡 함수**를 부른다.
                     잡 = 런북(코드 경로 하나)이라 scheduler_job_run 기록·실패 신호가 스케줄 실행과 똑같이 남는다.
                     디스패처 타임아웃(600초) 안에 끝나는 것만.
  분리(_run_detached): 600초를 넘길 수 있는 크롤은 `python scheduler.py --<flag>-now` 를 별도 프로세스로 띄우고 즉시
                     돌아온다. **이때 ok=True 는 "요청 접수" 이지 "복구 확인" 이 아니다** — 디스패처는 과제를 closed 로
                     닫지만 실제 결과는 (1) 자식이 도는 guarded_job 의 scheduler_job_run 행(실패면 그날 `{job_id}:{날짜}`
                     과제), (2) 다음 날 신선도 판정(freshness.py)이 여전히 stale 이면 다시 여는 과제로 드러난다.
                     겹침 방지: logs/runbook_<name>.pid + 살아 있는 프로세스면 거부(디스패처 flock 과 이중 가드).
                     202 폴링은 P2.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[2]
DETACHED_LOG_DIR = REPO_ROOT / "logs"
# 이 프로세스가 띄운 분리 런북 자식 — 이름당 하나. 단일 Werkzeug 프로세스 전제(api/server.py 와 같은 전제).
_DETACHED: dict[str, "subprocess.Popen"] = {}


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


# ── P1.5 Task 5: 코드에 이미 있는 복구 절차 11개 — 동기(잡 함수 재사용) / 분리(별도 프로세스) ─────────────

def _job(name: str):
    """scheduler.py 의 guarded 잡 함수를 지연 import — 잡과 런북이 같은 코드 경로·같은 기록을 갖는다.

    scheduler.py 는 import 시 main()·setup_logging 을 부르지 않으므로 웹 프로세스 안에서 안전하다.
    """
    import importlib
    return getattr(importlib.import_module("scheduler"), name)


def _from_job(job_fn_name: str, label: str) -> dict:
    """동기 런북: guarded_job 이 돌려주는 {ok, counts, errors, error_class} 를 런북 결과 {ok, summary, detail} 로."""
    res = _job(job_fn_name)()
    errors = res.get("errors") or []
    state = "완료" if res.get("ok") else "실패"
    tail = f"; 오류: {errors[0]}" if errors else ""
    return {"ok": bool(res.get("ok")), "summary": f"{label} {state} — {res.get('counts')}{tail}"[:300], "detail": res}


def _proc_state(pid: int) -> str | None:
    """Linux /proc 의 프로세스 상태 문자('R','S','Z',…). /proc 이 없으면(macOS) None."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    try:
        return stat.rsplit(")", 1)[1].split()[0]
    except IndexError:
        return None


def _pid_alive(pid: int) -> bool:
    """pid 파일의 프로세스가 아직 도는가. zombie(끝났는데 부모가 wait 안 함)는 kill(pid,0) 이 성공하므로 /proc 으로 거른다.

    PermissionError 는 '존재하지만 내 것이 아님' — 살아 있다고 본다(겹쳐 띄우는 쪽이 더 위험. 컨테이너는 단일 사용자라 사실상 미도달).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return _proc_state(pid) != "Z"


def _run_detached(name: str, flag: str) -> dict:
    """600초를 넘길 수 있는 런북: 별도 프로세스로 `scheduler.py <flag>` 를 띄우고 즉시 돌아온다.

    결과 확인은 자식이 도는 guarded_job 의 scheduler_job_run 행 + 다음 신선도 판정 — 여기서의 ok=True 는 접수 확인일 뿐이다.
    pid 파일(logs/runbook_<name>.pid)로 겹침을 막는다: 이 프로세스가 띄운 자식(_DETACHED)이 아직 돌거나, 서버 재기동 전에
    띄운 프로세스(pid 파일)가 아직 살아 있으면 거부. 끝난 자식은 poll() 로 거둬(zombie 방지) 다시 띄울 수 있게 한다.
    """
    DETACHED_LOG_DIR.mkdir(parents=True, exist_ok=True)
    for prev_name, prev_proc in list(_DETACHED.items()):   # 끝난 자식은 거둔다 — zombie 가 '실행 중' 으로 남지 않게
        if prev_proc.poll() is not None:
            _DETACHED.pop(prev_name, None)
    prev = _DETACHED.get(name)
    if prev is not None:
        return {"ok": False, "summary": f"{name} 이미 실행 중 (pid {prev.pid}) — 겹침 방지", "detail": {"pid": prev.pid}}
    pidfile = DETACHED_LOG_DIR / f"runbook_{name}.pid"
    if pidfile.exists():                                   # 서버 재기동 전에 띄운 프로세스가 아직 살아 있나
        try:
            pid = int(pidfile.read_text().strip())
        except ValueError:
            pid = None
        if pid is not None and _pid_alive(pid):
            return {"ok": False, "summary": f"{name} 이미 실행 중 (pid {pid}) — 겹침 방지", "detail": {"pid": pid}}
        pidfile.unlink(missing_ok=True)
    # 자식은 stdout fd 를 복제해 가지므로 부모 핸들은 바로 닫는다(fd 누수 방지)
    with open(DETACHED_LOG_DIR / f"runbook_{name}.out", "ab") as log:
        proc = subprocess.Popen([sys.executable, str(REPO_ROOT / "scheduler.py"), flag], cwd=str(REPO_ROOT),
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    _DETACHED[name] = proc
    pidfile.write_text(str(proc.pid))
    logger.info("[agent-team] 분리 런북 시작: %s pid=%s (`scheduler.py %s`)", name, proc.pid, flag)
    return {"ok": True,
            "summary": f"{name} 백그라운드 시작 (pid {proc.pid}, `scheduler.py {flag}`) — 완료 여부는 scheduler_job_run 과 다음 신선도 판정으로 확인",
            "detail": {"pid": proc.pid, "mode": "detached", "flag": flag}}


def _runbook_exchange_rate_refresh() -> dict:
    return _from_job("exchange_rate_refresh_job", "KEB 환율 갱신 + 기존행 재계산")


def _runbook_exchange_rate_recompute() -> dict:
    return _from_job("exchange_rate_recompute_job", "해외약가 FX 재계산")


def _runbook_foreign_approval_sync() -> dict:
    return _run_detached("foreign_approval_sync", "--approval-sync-now")


def _runbook_reimbursement_xnational_sync() -> dict:
    return _run_detached("reimbursement_xnational_sync", "--reimb-sync-now")


def _runbook_news_daily_fetch() -> dict:
    return _from_job("competitor_news_daily_job", "경쟁사·정부 뉴스 3일 fetch")


def _runbook_news_weekly_crawl() -> dict:
    return _run_detached("news_weekly_crawl", "--news-weekly-now")


def _runbook_competitor_trend_promote() -> dict:
    return _from_job("competitor_trend_promote_job", "동향 카드 승격")


def _runbook_nhis_negotiation_sync() -> dict:
    return _from_job("nhis_negotiation_sync_job", "NHIS 협상 공개자료 sync")


def _runbook_access_insight_fresh_signals() -> dict:
    return _run_detached("access_insight_fresh_signals", "--fresh-signals-now")


def _runbook_reimb_data_sync() -> dict:
    return _from_job("reimb_data_sync_job", "위원회 데이터·보고서 sync")


def _runbook_policy_intel_analysis_sync() -> dict:
    return _from_job("policy_analysis_sync_job", "정책 분석 사이드카 sync")


def _runbook_stakeholder_data_sync() -> dict:
    return _from_job("stakeholder_data_sync_job", "Stakeholder sync")


RUNBOOKS.update({
    "exchange_rate_refresh": _runbook_exchange_rate_refresh,
    "exchange_rate_recompute": _runbook_exchange_rate_recompute,
    "foreign_approval_sync": _runbook_foreign_approval_sync,
    "reimbursement_xnational_sync": _runbook_reimbursement_xnational_sync,
    "news_daily_fetch": _runbook_news_daily_fetch,
    "news_weekly_crawl": _runbook_news_weekly_crawl,
    "competitor_trend_promote": _runbook_competitor_trend_promote,
    "nhis_negotiation_sync": _runbook_nhis_negotiation_sync,
    "access_insight_fresh_signals": _runbook_access_insight_fresh_signals,
    "reimb_data_sync": _runbook_reimb_data_sync,
    "policy_intel_analysis_sync": _runbook_policy_intel_analysis_sync,
    "stakeholder_data_sync": _runbook_stakeholder_data_sync,
})


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
