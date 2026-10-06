"""
MA AI 대쉬보드 스케줄러
- 매월 1~7일 09:00 에 국내 약가 catch-up 을 자동 실행한다 (첫 주 매일 재시도 —
  당월이 이미 적재돼 있으면 브라우저/프록시 기동 없이 즉시 종료).
- 실행 후 대쉬보드를 자동으로 갱신한다.
- 수동 실행: python scheduler.py --run-now
- 로컬 전용 잡(git 백업·Rule Compliance 메모리 감사)은 프로덕션(FLY_APP_NAME)에서 INFO 로 skip.
"""

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from dataclasses import dataclass
from typing import Callable

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agents.agent_tasks.jobs import guarded_job, resolve_db_path
from agents.domestic_price_agent import DomesticPriceAgent
from agents.dashboard_agent import DashboardAgent
from agents.ingest.policy_analysis_sync import sync_analysis as _sync_policy_analysis
from agents.ingest.daily_mailing_sync import sync_daily_mailing_runs as _sync_daily_mailing_runs  # noqa: F401  (헤르메스 버스 retire — 롤백용 보존)
from agents.ingest.daily_mailing_scope_export import export_scopes as _export_daily_mailing_scopes
from agents.runtime_env import is_production

# misfire 정책(초). 재기동이 잦아 기본 1초로는 놓친 실행이 조용히 폐기됐다(로컬 로그 'was missed by' 611건).
DAILY_GRACE = 6 * 3600        # 일간 cron: 6시간 안에 재기동하면 1회 실행(coalesce)
PERIODIC_GRACE = 24 * 3600    # 주간·월간·분기: 하루 안에 재기동하면 1회 실행
INTERVAL_GRACE = 300          # 10분 interval: 다음 tick 이 곧 오므로 5분

# ── 로깅 설정 ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

def setup_logging(config: dict):
    log_dir = BASE_DIR / config["logging"]["dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ma_ai_dossier.log"

    logging.basicConfig(
        level=getattr(logging, config["logging"]["level"], logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )

logger = logging.getLogger(__name__)


# 신호 경계: 원장 기록 실패(DB 잠김 등)는 여기서 삼키고 warning 만 남긴다 — 호출부는 보호막 없이 부른다.
def _agent_record_health_problems(report: dict) -> None:
    """헬스 문제를 에이전트 작업 원장에 남긴다 (실패해도 잡을 막지 않는다)."""
    try:
        from agents.agent_tasks.signals import record_health_problems
        record_health_problems(report, db_path=resolve_db_path())
    except Exception as signal_err:
        logger.warning("daily mailing health 신호 기록 실패: %s", signal_err)


def _agent_record_job_failure(job_id: str, error: str) -> None:
    """잡 실패를 에이전트 작업 원장에 남긴다 (실패해도 잡을 막지 않는다)."""
    try:
        from agents.agent_tasks.signals import record_job_failure
        record_job_failure(job_id, error, db_path=resolve_db_path())
    except Exception as signal_err:
        logger.warning("%s 실패 신호 기록 실패: %s", job_id, signal_err)


def _agent_record_job_missed(job_id: str, scheduled_run_time) -> None:
    """놓친 실행(misfire grace 초과)을 에이전트 작업 원장에 남긴다 (실패해도 리스너를 죽이지 않는다)."""
    try:
        from agents.agent_tasks.signals import record_job_missed
        record_job_missed(job_id, scheduled_run_time, db_path=resolve_db_path())
    except Exception as signal_err:
        logger.warning("%s 놓친 실행 신호 기록 실패: %s", job_id, signal_err)


def _agent_record_quality_review(result: dict) -> dict:
    """QG 리뷰 결과를 원장 과제로 (회귀·drift=ops_incident, 코드패턴·제안=improvement 그룹). 잡 counts 에 합칠 수를
    돌려준다 — tasks_new 는 이번 실행이 새로 만든 행 수(내용 불변이면 0). 실패해도 잡을 막지 않는다."""
    try:
        from agents.agent_tasks.signals import count_source_tasks, record_quality_review
        db = resolve_db_path()
        before = count_source_tasks("quality_guard", db_path=db)
        ids = record_quality_review(result, db_path=db)
        new = count_source_tasks("quality_guard", db_path=db) - before
        logger.info("QualityGuard → 원장: incident %d · improvement %d (신규 %d)",
                    len(ids["incident"]), len(ids["improvement"]), new)
        return {"tasks_incident": len(ids["incident"]), "tasks_improvement": len(ids["improvement"]), "tasks_new": new}
    except Exception as signal_err:
        logger.warning("QualityGuard 신호 기록 실패: %s", signal_err)
        return {}


def _agent_record_compliance_results(results) -> dict:
    """RC FAIL 을 원장 과제로(메모리당 1건). 잡 counts 에 합칠 수를 돌려준다. 실패해도 잡을 막지 않는다."""
    try:
        from agents.agent_tasks.signals import count_source_tasks, record_compliance_results
        db = resolve_db_path()
        before = count_source_tasks("rule_compliance", db_path=db)
        ids = record_compliance_results(results, db_path=db)
        new = count_source_tasks("rule_compliance", db_path=db) - before
        logger.info("Rule Compliance → 원장: FAIL 과제 %d (신규 %d)", len(ids), new)
        return {"tasks": len(ids), "tasks_new": new}
    except Exception as signal_err:
        logger.warning("Rule Compliance 신호 기록 실패: %s", signal_err)
        return {}


def _agent_record_known_debts() -> None:
    """구현 대기 기능 부채(signals.KNOWN_DEBTS)를 원장에 남긴다. 신호 경계 — 실패는 여기서 삼킨다.

    스케줄러 기동 시 1회 + `--record-debts-now`. 이미 열려 있거나 blocked 면 같은 id 를 돌려받을 뿐 새 행은 없다."""
    try:
        from agents.agent_tasks.signals import record_known_debts
        ids = record_known_debts(db_path=resolve_db_path())
        logger.info("구현 대기 부채 과제 %d건 확인: %s", len(ids), ids)
    except Exception as signal_err:                        # noqa: BLE001 — 신호 실패가 기동을 막으면 안 된다
        logger.warning("부채 과제 기록 실패(무시): %s", signal_err)


def _on_job_event(event) -> None:
    """APScheduler 리스너. guarded_job 이 예외를 삼키므로 EVENT_JOB_ERROR 는 데코레이터 없는 잡의 안전망이고,
    EVENT_JOB_MISSED 는 misfire grace 초과(스케줄러 정지·재기동 지연)의 유일한 신호다."""
    if event.code == EVENT_JOB_MISSED:
        logger.warning("놓친 실행: %s @ %s", event.job_id, event.scheduled_run_time)
        _agent_record_job_missed(event.job_id, event.scheduled_run_time)
    elif event.code == EVENT_JOB_ERROR:
        exc = event.exception
        _agent_record_job_failure(event.job_id, f"{type(exc).__name__}: {exc}"[:300])


# ── 파이프라인 ──────────────────────────────────────────────────────────────

def load_config() -> dict:
    config_path = BASE_DIR / "config" / "settings.json"
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


async def run_pipeline():
    """전체 파이프라인: 국내 약가 월별 catch-up(누락 자동복구) → 대쉬보드.

    매월 1~7일 09:00 실행 (첫 주 매일 재시도). 기존 단발(download_latest)에서 **catch_up** 으로
    전환 — 스케줄러 미가동/실패로 빠진 달이 있어도 DB 최신일보다 새로운 모든 월을 한 번에 적재.
    당월이 이미 적재돼 있으면 catch_up 이 브라우저/프록시 기동 없이 skip 한다.
    """
    logger.info("━━━ 파이프라인 시작 ━━━")
    config = load_config()

    # 1) 국내 약가 월별 catch-up (누락 월 자동 복구 적재)
    from agents.domestic_catchup import catch_up
    counts = {"ingested": 0, "catchup_errors": 0}
    try:
        result = await catch_up(config, BASE_DIR)
        counts["ingested"] = len(result.get("ingested") or [])
        counts["catchup_errors"] = len(result.get("errors") or [])
        logger.info("국내 약가 catch-up: 신규 적재 %s · 오류 %s",
                    result.get("ingested"), result.get("errors"))
        if result.get("skipped"):
            logger.info("국내 약가 catch-up skip (%s) — 당월 이미 적재", result["skipped"])
        elif result.get("errors") and not result.get("ingested"):
            # 게시판 로딩 실패 등 전체 실패 — silent no-op 이 아니라 ERROR 로 남긴다
            logger.error("국내 약가 catch-up 실패 — 적재 0건, 오류 %s (1~7일 일일 재시도)",
                         result["errors"])
            _agent_record_job_failure("domestic_price_pipeline",
                                      "; ".join(str(e) for e in result["errors"])[:300])
        elif result.get("errors"):
            logger.warning("catch-up 일부 오류 — 다음 실행 시 재시도(멱등): %s", result["errors"])
    except Exception as e:
        logger.error("국내 약가 catch-up 실패: %s", e, exc_info=True)
        # 캐치업 자체가 예외로 죽은 경우에도 에이전트 원장에 신호를 남긴다 (래퍼가 신호 실패를 삼킨다)
        _agent_record_job_failure("domestic_price_pipeline", f"{type(e).__name__}: {e}"[:300])
        # 적재 실패해도 대쉬보드는 기존 데이터로 갱신

    # 2) 대쉬보드 에이전트 — 예외는 guarded_job(job) 이 받는다
    dashboard_agent = DashboardAgent(config, BASE_DIR)
    html_path = dashboard_agent.run()

    logger.info("━━━ 파이프라인 완료 ━━━")
    logger.info("대쉬보드: file://%s", html_path.resolve())
    counts["dashboard"] = str(html_path)
    return {"ok": True, "counts": counts}


@guarded_job("domestic_price_pipeline")
def job():
    """APScheduler 콜백 — 비동기 파이프라인 실행"""
    return asyncio.run(run_pipeline())


import subprocess
from datetime import datetime


@guarded_job("foreign_approval_sync")
def foreign_approval_sync_job():
    """ForeignApprovalAgent auto-sync — 매일 04:30 Seoul (compliance 감사 전).

    foreign_drug_prices 에 있으나 indications_master 에 없는 제품을 자동 build. 실패 slug 는 failed 로 반환 → 과제.
    """
    from agents.foreign_approval.agent import ForeignApprovalAgent
    logger.info("━━━ ForeignApproval auto-sync 시작 ━━━")
    agent = ForeignApprovalAgent()
    gaps = agent.list_coverage_gaps()
    if not gaps:
        logger.info("ForeignApproval: gap 0건 — 이미 동기화됨")
        return {"counts": {"gaps": 0, "built": 0, "failed": 0}}
    logger.info("ForeignApproval: gap %d건 — %s", len(gaps), gaps)
    result = agent.sync_from_prices()
    logger.info("ForeignApproval: built=%d failed=%d", len(result["built"]), len(result["failed"]))
    for f in result["failed"]:
        logger.warning("  ✗ %s: %s", f["slug"], f["reason"])
    return {"counts": {"gaps": len(gaps), "built": len(result["built"]), "failed": len(result["failed"])},
            "failed": result["failed"]}


@guarded_job("rule_compliance_audit")
def rule_compliance_audit_job():
    """RuleComplianceAgent — 매일 05:30 Seoul (QG 06:00 직전).

    로컬: MEMORY.md 인덱스를 소스로 메모리 ↔ 런타임 대조. 프로덕션: 메모리 디렉터리가 없으므로 CHECKS 레지스트리
    (전부 DB/파일 증거)만 돈다(require_memory=False). 보고서는 data/quality_guard/compliance_YYYY-MM-DD.md (볼륨).
    FAIL 은 과제(메모리당 1건, rc:<memory>)이지 잡 실패가 아니다 — ok=True.
    """
    from agents.rule_compliance import RuleComplianceAgent

    prod = is_production()
    logger.info("━━━ Rule Compliance 감사 시작 (%s) ━━━", "DB 체크만 — 메모리 없음" if prod else "메모리 대조")
    agent = RuleComplianceAgent(require_memory=not prod)
    results = agent.audit()
    path = agent.write_report(results)
    fails = [r for r in results if r.status == "fail"]
    passes = [r for r in results if r.status == "pass"]
    if fails:
        logger.error("Rule Compliance: ❌ FAIL %d건 / ✅ PASS %d건 — %s", len(fails), len(passes), path)
        for f in fails:
            logger.error("  • %s: %s", f.memory, f.detail)
    else:
        logger.info("Rule Compliance: ✅ PASS %d건, 회귀 0건 — %s", len(passes), path)
    counts = {"pass": len(passes), "fail": len(fails), "skip": len(results) - len(passes) - len(fails)}
    counts.update(_agent_record_compliance_results(results))
    return {"counts": counts}


@guarded_job("quality_guard_review")
def quality_guard_review_job():
    """QualityGuardAgent 지속 리뷰 — 매일 06:00 Seoul.

    규칙 drift / MFDS baseline 회귀 / 개선 제안을 점검하고 `data/quality_guard/review_YYYY-MM-DD.md` (볼륨) 를 생성한다.
    회귀·drift 는 ops_incident 낱개, 코드패턴·제안은 improvement 그룹 과제(내용 키 dedup — 매일 같은 13건이 쌓이지 않는다).
    """
    from agents.quality_guard import QualityGuardAgent

    logger.info("━━━ QualityGuard 일일 리뷰 시작 ━━━")
    result = QualityGuardAgent().review_codebase()
    regressions = result.get("mfds_regressions", [])
    drifts = result.get("rule_drifts", [])
    if regressions:
        logger.error("QualityGuard: MFDS baseline 회귀 %d건 — %s", len(regressions), result["report_path"])
    elif drifts:
        logger.warning("QualityGuard: 규칙↔코드 drift %d건 — %s", len(drifts), result["report_path"])
    else:
        logger.info("QualityGuard: 회귀 0건 / 제안 %d건 — %s", len(result.get("suggestions", [])), result["report_path"])
    counts = {k: len(result.get(k) or []) for k in ("code_issues", "rule_drifts", "mfds_regressions", "suggestions")}
    counts.update(_agent_record_quality_review(result))
    return {"counts": counts}


@guarded_job("foreign_price_backfill")     # 수동 전용(스케줄 미등록, MANUAL_JOB_RUNBOOK) — 실패는 원장에 남긴다
def foreign_price_backfill_job():
    """ForeignPrice backfill — `--price-backfill-now` 수동 전용. **스케줄에 올리지 않는다**(build_job_specs 미등록).

    indications_master 의 product slug 별로 ForeignPriceAgent.search_all 실행(alias inn 우선). 2026-07-24 비활성화 사유
    (append 누적·검색 히스토리 고정 노출)에 더해 identity 라우팅·IT 채널 문제가 풀리기 전까지 켜지 않는다(P1.6 감사 합의).
    반환은 잡 규약 {counts, failed} — 제품 단위 예외는 failed 로(guarded_job 이 foreign_price_backfill:{날짜} 과제),
    국가 단위 스크레이퍼 실패는 search_all 이 이미 foreign_search:{국가}:{분류} 로 남기므로 counts.country_errors 로만 센다.
    """
    from agents.db import DrugPriceDB
    from agents.foreign_price_agent import ForeignPriceAgent

    logger.info("━━━ ForeignPrice 백필 시작 (수동) ━━━")
    db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
    with db._connect() as conn:
        slugs = [r[0] for r in conn.execute(
            "SELECT DISTINCT product FROM indications_master ORDER BY product"
        ).fetchall()]
    logger.info("ForeignPrice backfill: %d 약제 — %s", len(slugs), slugs)

    agent = ForeignPriceAgent(BASE_DIR)
    ok, failed, country_errors = 0, [], 0
    for slug in slugs:
        # alias 우선 INN 검색 (예: keytruda → pembrolizumab)
        alias = db.get_product_alias(slug) or {}
        query = alias.get("inn") or slug
        try:
            results = asyncio.run(agent.search_all(query))
            total = sum(len(v) if isinstance(v, list) else 0 for v in results.values())
            errs = getattr(results, "errors", None) or {}
            country_errors += len(errs)
            logger.info("  ✓ %s (query=%s): %d행, 국가 실패 %s", slug, query, total, sorted(errs) or "-")
            ok += 1
        except Exception as e:
            logger.warning("  ✗ %s (query=%s): %s", slug, query, e)
            failed.append({"slug": slug, "error": f"{type(e).__name__}: {e}"[:200]})
    logger.info("ForeignPrice 백필 완료: ok=%d fail=%d 국가실패=%d", ok, len(failed), country_errors)
    return {"counts": {"products": len(slugs), "ok": ok, "fail": len(failed), "country_errors": country_errors},
            "failed": failed}


@guarded_job("exchange_rate_refresh")
def exchange_rate_refresh_job():
    """KEB 36개월 평균환율 갱신 — 매월 2일 04:00 Seoul.

    전월 말일이 확정된 직후(2일)에 KEB하나은행 기간평균(36개월 rolling, 매매기준율)
    엑셀을 Playwright 로 재다운로드해 data/foreign/exchange_rate/ 캐시를 최신화.
    이후 ForeignPriceAgent 의 A8 조정가 계산이 항상 최신 36개월 창을 사용하도록 보장.

    이전에는 정기 갱신 잡이 없어 누군가 refresh() 를 호출하기 전까지 마지막 xlsx 가
    계속 쓰였음(KEB 36mo 창 drift 의 원인). chromium 은 Dockerfile 에 이미 설치됨.
    배포 안전: 네트워크/Playwright 실패는 guarded_job 이 받아 원장 과제로 남긴다.
    """
    logger.info("━━━ KEB 36개월 평균환율 갱신 시작 ━━━")
    from agents.exchange_rate import ExchangeRateFetcher
    fetcher = ExchangeRateFetcher()
    rates = asyncio.run(fetcher.refresh(headless=True))
    meta = fetcher._rate_meta
    logger.info("환율 갱신 완료: %d개 통화 (기간 %s~%s) — %s", len(rates), meta.get("from", "?"), meta.get("to", "?"),
                {k: rates.get(k) for k in ("USD", "EUR", "JPY", "GBP", "CHF") if k in rates})
    counts = {"currencies": len(rates), "to": meta.get("to")}
    errors = []
    # 갱신 직후 기존 최신 해외약가 행도 최종환율로 재계산 (주간 백필 대기 없이 즉시 반영)
    try:
        from agents.db import DrugPriceDB
        db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
        rc = db.recompute_foreign_fx(rates, meta)
        logger.info("환율 갱신 후 기존행 재계산: 대상 %d · 갱신 %d · skip %d", rc["candidates"], rc["updated"], rc["skipped"])
        counts.update({"recompute_candidates": rc["candidates"], "recompute_updated": rc["updated"]})
    except Exception as e:
        logger.exception("환율 갱신 후 재계산 실패(캐시는 갱신됨): %s", e)
        errors.append(f"재계산 실패(캐시는 갱신됨): {type(e).__name__}: {e}")
    return {"ok": not errors, "counts": counts, "errors": errors}


@guarded_job("exchange_rate_recompute")     # 수동 전용(스케줄 미등록) — 런북(Task 5)이 부르므로 기록은 남긴다
def exchange_rate_recompute_job():
    """기존 최신 해외약가 행을 현재 캐시 환율(최종)로 재계산 — 재스크레이프 없음.

    환율은 adjusted_price_krw 에 선형이라 new/old 비율로 스케일해 새 행을 append.
    환율 잡(매월 2일)에 이미 포함돼 있고, 이 잡은 캐시는 그대로 두고 재계산만
    수동 실행할 때 사용 (예: 고시회차 수정 후 기존행 즉시 반영).

    실패와 '캐시 없음' 은 {"ok": False, "errors": [...]} 다 — 예외를 여기서 삼키고 None 을 돌려주면 normalize_result 가
    ok=True 로 만들어 런북이 거짓 '복구 완료' 를 내고, 디스패처가 foreign_price_fx_alignment 신선도 과제를 아무것도
    재계산하지 않은 채 닫는다(P1.5 검증 결함 A). 예외는 guarded_job 이 받아 실패 신호로 남긴다. 스케줄 미등록이라
    런북 힌트는 signals.MANUAL_JOB_RUNBOOK 이 준다(JOB_RUNBOOK 은 등록 잡 id 집합과 같아야 한다).
    """
    logger.info("━━━ 해외약가 FX 재계산 시작 ━━━")
    from agents.exchange_rate import ExchangeRateFetcher
    from agents.db import DrugPriceDB
    fetcher = ExchangeRateFetcher()
    if not fetcher._load_latest_cache():
        msg = "환율 캐시 없음 — 런북 exchange_rate_refresh(`scheduler.py --fx-refresh-now`) 를 먼저 실행해야 한다"
        logger.warning("FX 재계산: %s", msg)
        return {"ok": False, "counts": {}, "errors": [msg]}
    db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
    res = db.recompute_foreign_fx(fetcher._rates, fetcher._rate_meta)
    logger.info("FX 재계산 완료: 대상 %d · 갱신 %d · skip %d (기간 %s~%s)",
                res["candidates"], res["updated"], res["skipped"],
                fetcher._rate_meta.get("from"), fetcher._rate_meta.get("to"))
    return {"ok": True, "errors": [],
            "counts": {"candidates": res["candidates"], "updated": res["updated"], "skipped": res["skipped"],
                       "to": fetcher._rate_meta.get("to")}}


@guarded_job("reimbursement_xnational_sync")
def reimbursement_xnational_sync_job():
    """Cross-national reimbursement sync — 분기 1회 (1·4·7·10월 1일 02:00 Seoul).

    NICE / PBAC / CMS / 일본 中医協 4 site 자동 호출 → reimbursement_xnational 적재.
    pure-napping-goose plan Phase 6.
    """
    logger.info("━━━ Reimbursement xnational sync 시작 ━━━")
    from agents.foreign_approval.reimbursement_sync import sync_all
    summary = sync_all()
    logger.info("Reimbursement sync 완료:")
    total = 0
    for slug, counts in summary.items():
        logger.info("  %s: %s", slug, counts)
        total += sum(int(v or 0) for v in counts.values())
    errors = [] if total > 0 else ["4개 사이트 모두 0행 — 스크레이퍼/셀렉터 확인"]
    return {"ok": not errors, "counts": {"products": len(summary), "rows": total}, "errors": errors}


# ── HIRA Pipeline Tracker (암질심 + 약평위) — hira-pipeline-tracker skill backend ──

def _crawl_news(lookback_competitor: int, lookback_gov: int) -> dict:
    """경쟁사·정부 두 크롤을 독립 실행 — 한쪽 실패가 다른 쪽을 막지 않고, 둘 다 errors 로 반환.

    정부·보건당국 정책 뉴스는 같은 competitor_news 테이블(kind='gov_policy')에 누적 → 키워드↔근거기사 보장.
    Naver 키 미설정(error 에 '미설정')은 재실행으로 안 풀리므로 error_class='config'.
    """
    counts, errors, error_class = {}, [], "transient"
    try:
        from agents import competitor_news_agent as cn
        r = cn.crawl(lookback_days=lookback_competitor, t1_only=True)
        if r.get("error"):
            errors.append(f"competitor: {r['error']}")
            if "미설정" in str(r["error"]):
                error_class = "config"
        else:
            counts["competitor"] = r.get("total_stored", 0)
            if r.get("expired_removed") is not None:
                counts["expired_removed"] = r.get("expired_removed", 0)
    except Exception as e:
        logger.exception("Competitor News 크롤 실패: %s", e)
        errors.append(f"competitor: {type(e).__name__}: {e}")
    try:
        from agents import gov_policy_news as gpn
        g = gpn.crawl(lookback_days=lookback_gov)
        if g.get("error"):
            errors.append(f"gov_policy: {g['error']}")
        else:
            counts["gov_policy"] = g.get("total_stored", 0)
    except Exception as e:
        logger.exception("Gov Policy News 크롤 실패: %s", e)
        errors.append(f"gov_policy: {type(e).__name__}: {e}")
    return {"ok": not errors, "counts": counts, "errors": errors, "error_class": error_class}


@guarded_job("competitor_news_weekly")
def competitor_news_weekly_job():
    """매주 월요일 03:30 Seoul — 경쟁사 Tier 1 뉴스 하이브리드 수집 + 1년 보존 정리.

    하이브리드 2축 (agents/competitor_news_agent.crawl):
      ① Naver News API → 전 매체 검색 후 T1 도메인만 필터 (config/media_tiers.json).
      ② T1 전문지 직접 검색 (agents/scrapers/tier1_news_sites) — Naver 미인덱싱 갭필러
         (뉴스더보이스·히트뉴스). canonical URL 로 ①②/페이지 중복 제거.
    competitor_news 테이블 누적, expires_at(발행일+365)<오늘 자동 삭제. Naver 키 필요(사이트축은 키 불필요).
    배포 안전: 사이트별/페이지별 try/except·timeout·정중딜레이, 한 소스 실패가 전체를 막지 않음.
    """
    logger.info("━━━ Competitor/Gov News 주간 크롤 시작 ━━━")
    from agents import competitor_news_agent as cn
    from agents import gov_policy_news as gpn
    res = _crawl_news(cn.DEFAULT_LOOKBACK_DAYS, gpn.DEFAULT_LOOKBACK_DAYS)
    logger.info("주간 크롤 완료: %s", res["counts"])
    return res


@guarded_job("competitor_news_daily")
def competitor_news_daily_job():
    """매일 06:30 Seoul — 경쟁사 Tier1 + 정부정책 뉴스 소규모(최근 3일) fetch.

    주간 딥 크롤(competitor_news_weekly_job, 6개월+만료정리)과 별개로 매일 아침 신선분만
    수집 → 07:00 promote 가 동향 카드/홈 대쉬보드에 반영. dedup(link)으로 중복 저장 없음.
    """
    logger.info("━━━ Competitor/Gov News 매일 fetch 시작 ━━━")
    res = _crawl_news(3, 3)
    logger.info("매일 fetch 완료: %s", res["counts"])
    return res


@guarded_job("access_insight_fresh_signals")
def access_insight_fresh_signal_job():
    """매주 월요일 04:00 Seoul — Access Insight 신선 신호 크롤 (S5).

    tier A(전문지)/B(종합지)/D(미분류) 매체 기사(최근 8일)를 수집해 신호로 적재:
      ① Naver News API 1-pass (amjilsim_drugs 추적 약제 + 위원회 키워드) — 도메인
         tier 로 A/B/D 버킷 분류 (agents/amjilsim_tracker/crawlers/tiered_news).
      ② T1 전문지 직접 검색 갭필러 (tier1_news_sites — 키 불필요).
      → signal_extractor: 약물 거명(resolve_drug) + signal_type 분류(classify)
      → amjilsim_media_signals (url, drug_id) 멱등 INSERT.
    INSERT-only — S1 백필 기존 행은 절대 변경하지 않음. 재실행 안전(중복 0).
    Naver 키 미설정 시 Naver 축만 skip (사이트 축은 진행).
    """
    logger.info("━━━ Access Insight 신선 신호 주간 크롤 시작 ━━━")
    from agents.amjilsim_tracker.signal_extractor import run_fresh_crawl
    res = run_fresh_crawl()
    logger.info("신선 신호 크롤 완료: articles=%d matched=%d inserted=%d dup=%d (tier별 %s)",
                res.get("articles", 0), res.get("matched", 0), res.get("inserted", 0),
                res.get("duplicate_skipped", 0), res.get("by_tier", {}))
    return {"counts": {k: res.get(k, 0) for k in ("articles", "matched", "inserted", "duplicate_skipped")}}


@guarded_job("competitor_trend_promote")
def competitor_trend_promote_job():
    """매일 07:00 Seoul — 아카이브(competitor_news) 최근 2일 뉴스 → 동향 카드 승격.

    06:30 매일 fetch(competitor_news_daily_job)로 들어온 신선분을 소스로 LLM 필터
    (badge/importance) 후 competitor_trend 에 source_type='promoted' UPSERT. manual 카드
    보존. 윈도우 2일은 Naver 인덱싱/수집 지연을 흡수(UPSERT 라 재승격 멱등).
    """
    logger.info("━━━ Competitor Trend 승격 (아카이브→카드) 시작 ━━━")
    from agents import competitor_trends_agent as ct
    res = ct.promote_from_archive(days=2)
    t = res.get("totals", {})
    logger.info("Trend 승격 완료: archive=%s accepted=%s upserted=%s", t.get("archive"), t.get("accepted"), t.get("upserted"))
    return {"counts": {k: t.get(k, 0) for k in ("archive", "accepted", "upserted")}}


def _reimb_committee_data_sync() -> dict:
    """위원회 데이터(JSON) git-sync — 멱등. 반환: {counts, fallback}. fallback 은 원격 실패 사유(없으면 None)."""
    from agents.ingest import reimb_committee_import as imp
    from agents import reimb_reports as rr

    source = os.environ.get("REIMB_DATA_URL") or None
    fallback = None
    if source:  # git URL 우선, 실패 시 이미지 로컬 JSON 폴백 — 폴백 사실은 반환값으로 올려 과제가 된다
        try:
            payload = imp.load_payload(source)
        except Exception as e:
            logger.warning("데이터 sync: REIMB_DATA_URL fetch 실패(%s) → 로컬 폴백", e)
            payload = imp.load_payload(None)
            fallback = f"{type(e).__name__}: {e}"
    else:
        payload = imp.load_payload(None)
    new_hash = imp.payload_hash(payload)

    hash_file = BASE_DIR / "data" / "reimb" / ".last_applied_hash"
    prev_hash = hash_file.read_text().strip() if hash_file.exists() else ""
    if new_hash == prev_hash:
        logger.info("데이터 sync: 변경 없음 (hash %s) — skip", new_hash[:12])
        return {"counts": {"changed": 0}, "fallback": fallback}

    result = imp.run(payload)
    try:
        rr.backfill_blobs()
    except Exception as e:
        logger.warning("데이터 sync: blob backfill 스킵: %s", e)
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text(new_hash)
    logger.info("데이터 sync 완료: 약제 %d · 이벤트 %d (신규 %d) · hash %s · source=%s",
                result["drugs_after"], result["events_total"], result["events_added"], new_hash[:12],
                f"{source} (폴백:local)" if fallback else (source or "local"))
    return {"counts": {k: result[k] for k in ("drugs_after", "events_total", "events_added")}, "fallback": fallback}


def _reimb_reports_sync() -> dict:
    """위원회 전후 보고서 git-sync (매니페스트 → Intelligence Reports) — 멱등. 반환: {counts, fallback, errors}."""
    import hashlib as _hl
    import json as _j
    from agents import reimb_reports as rr

    rsrc = os.environ.get("REPORTS_DATA_URL") or None
    rhash_file = BASE_DIR / "data" / "reimb" / ".last_reports_hash"
    prev_rhash = rhash_file.read_text().strip() if rhash_file.exists() else ""
    fallback = None
    if rsrc:  # 매니페스트 URL 우선, 실패 시 이미지 로컬 폴백 — 폴백 사실은 반환값으로 올려 과제가 된다
        try:
            manifest = rr.load_reports_manifest(rsrc)
        except Exception as e:
            logger.warning("보고서 sync: REPORTS_DATA_URL fetch 실패(%s) → 로컬 매니페스트 폴백", e)
            manifest = rr.load_reports_manifest(None)
            fallback = f"{type(e).__name__}: {e}"
    else:
        manifest = rr.load_reports_manifest(None)
    m_hash = _hl.sha256(_j.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if m_hash == prev_rhash:
        logger.info("보고서 sync: 매니페스트 변경 없음 (hash %s) — skip", m_hash[:12])
        return {"counts": {"changed": 0}, "fallback": fallback, "errors": []}
    rres = rr.sync_reports(manifest)
    rhash_file.parent.mkdir(parents=True, exist_ok=True)
    rhash_file.write_text(m_hash)
    logger.info("보고서 sync 완료: 매니페스트 %d · 신규적재 %d · skip %d · 오류 %d · hash %s",
                rres["reports_in_manifest"], len(rres["ingested"]), rres["skipped"], len(rres["errors"]), m_hash[:12])
    return {"counts": {"manifest": rres["reports_in_manifest"], "ingested": len(rres["ingested"]),
                       "skipped": rres["skipped"]},
            "fallback": fallback, "errors": [str(e)[:200] for e in rres["errors"]]}


@guarded_job("reimb_data_sync")
def reimb_data_sync_job():
    """매일 02:00 Seoul + 부팅 1회 — 헤르메스(또는 사람)가 git 에 커밋한
    위원회 **데이터**(committee_results.json) + **보고서**(reports_manifest.json) 를
    가져와 프로덕션 DB 에 멱등 적재. **재배포·재시딩 불필요**.

    데이터/보고서는 독립 실행 (한쪽 변경 없음·실패가 다른쪽을 막지 않음).
    소스: REIMB_DATA_URL / REPORTS_DATA_URL (git raw). 미설정 시 이미지 로컬 폴백.
    원격 fetch 실패 시 로컬 이미지 폴백을 **적용하되** 그 사실을 errors 로 반환한다 —
    '변경 없음 skip' 처럼 보이던 문제(감사 #3)를 과제로 드러낸다.
    """
    logger.info("━━━ Reimbursement 데이터·보고서 sync 시작 ━━━")
    counts, errors = {}, []
    try:
        d = _reimb_committee_data_sync()
        counts["data"] = d["counts"]
        if d["fallback"]:
            errors.append(f"위원회 데이터 원격 fetch 실패 → 로컬 이미지 폴백 적용 (데이터는 적용됨): {d['fallback']}")
    except Exception as e:
        logger.exception("Reimbursement 데이터 sync 실패: %s", e)
        errors.append(f"데이터 sync: {type(e).__name__}: {e}")
    try:
        r = _reimb_reports_sync()
        counts["reports"] = r["counts"]
        if r["fallback"]:
            errors.append(f"보고서 매니페스트 원격 fetch 실패 → 로컬 폴백 적용: {r['fallback']}")
        errors += [f"보고서 적재 오류: {e}" for e in r["errors"]]
    except Exception as e:
        logger.exception("Reimbursement 보고서 sync 실패: %s", e)
        errors.append(f"보고서 sync: {type(e).__name__}: {e}")
    return {"ok": not errors, "counts": counts, "errors": errors}


def daily_mailing_scope_export_job():
    """15분 주기 + 부팅 1회 — Daily Mailing 모니터링 스콥 OUTBOUND export.

    active 구독의 dashboard_scope 를 로컬 스냅샷 + 비공개 repo
    (jeonpapa/AccessRoutineAnalystic `daily_mailing/scopes/`) 에 발행한다 —
    inbound run-sync 의 대칭 (같은 repo 가 양방향 버스). 서버 훅의 즉시 export 가
    실패해도 이 잡이 권위 발행 백스톱. 멱등(내용 동일 시 PUT 생략) + prune
    (삭제/비활성 구독 원격 제거). 토큰(DAILY_MAILING_SCOPES_TOKEN) 미설정 시 local-only.
    """
    try:
        res = _export_daily_mailing_scopes(publish=True)
        logger.info("daily mailing scope export: %s", res)
    except Exception as e:
        logger.warning("daily mailing scope export 실패: %s", e)


def _n(x) -> int:
    return len(x) if isinstance(x, (list, tuple, set, dict)) else int(x or 0)


@guarded_job("daily_mailing_dispatch")
def daily_mailing_dispatch_job():
    """10분 주기 — 발송 시각이 된 구독 브리핑 생성 → 승인 대기/자동 발송 + 실패 재시도 (설계 §6).
    생성 실패 구독(errors)은 과제로 — 하루 1건(ref 에 날짜), 다음 tick 에 성공하면 런북이 닫는다."""
    from agents.daily_mailing.dispatch import DispatchDeps, dispatch_due_subscriptions
    res = dispatch_due_subscriptions(DispatchDeps.production())
    if res.get("generated") or res.get("errors") or res.get("expired") or res.get("retried"):
        logger.info("daily mailing dispatch: %s", res)
    return {"ok": not res.get("errors"),
            "counts": {"generated": _n(res.get("generated")), "expired": _n(res.get("expired")),
                       "retried": _n(res.get("retried")), "skipped": _n(res.get("skipped"))},
            "errors": [f"구독 {sid} 생성 실패" for sid in (res.get("errors") or [])]}


@guarded_job("daily_mailing_top_news")
def daily_mailing_top_news_job():
    """매일 06:40 — '오늘의 주요 MA 뉴스' 산출·캐시 (06:30 뉴스 수집 직후, 구독 공통)."""
    from agents.daily_mailing.dispatch import DispatchDeps
    from agents.daily_mailing.schedule import KST
    # retry_failed=True — 앞선 10분 tick 이 실패해 음성 캐시가 남아도 06:40 정기 잡은 1회 재시도.
    items = DispatchDeps.production().load_top_ma_news(datetime.now(KST).date().isoformat(), retry_failed=True)
    logger.info("daily mailing 주요 MA 뉴스: %d건", len(items))
    return {"counts": {"items": len(items)}}


@guarded_job("daily_mailing_health")
def daily_mailing_health_job():
    """매일 09:30 — 오늘 예정 구독의 미생성·보류·발송실패·Gmail 토큰 상태 점검. 문제는 헬스 신호로(잡 실패 아님)."""
    from agents.daily_mailing.dispatch import DispatchDeps, health_check
    res = health_check(DispatchDeps.production())
    if res["ok"]:
        logger.info("daily mailing health OK (%s)", res["run_date"])
    else:
        logger.error("daily mailing health 문제 %d건: %s", len(res["problems"]), res["problems"])
        _agent_record_health_problems(res)
    return {"counts": {"problems": len(res.get("problems") or [])}}


@guarded_job("nhis_negotiation_sync")
def nhis_negotiation_sync_job():
    """매주 월요일 02:30 Seoul — 건강보험공단 약가협상 공개자료(신규·확대) 크롤 →
    nhis_negotiations 멱등 아카이브 + amjilsim_drugs 매칭 교체(NHIS 공식 우선).

    소스(공개, 자격증명 불필요): retrieveMediList.do(신약)·retrieveMediList2.do(확대).
    - content_hash 멱등 UPSERT(삭제 금지 — 등록 후 1년만 공개되므로 영구 아카이브).
    - 매칭 시 negotiation_status/완료일을 nhis_official 로 자동 교체, 미매칭은 audit(drug_id NULL).
    배포 안전: 네트워크/파싱 실패는 guarded_job 이 받아 원장 과제로 남긴다.
    """
    logger.info("━━━ NHIS 약가협상 공개자료 주간 sync 시작 ━━━")
    from agents.ingest import nhis_negotiation_import as imp
    res = imp.run()
    logger.info("NHIS sync 완료: 수집 %d · 신규아카이브 %d · 매칭 drug %d/row %d · 미매칭 %d",
                res["fetched"], res["inserted"], res["matched_drugs"], res["matched_rows"], res["unmatched_count"])
    errors = [] if res["fetched"] > 0 else ["NHIS 수집 0건 — 사이트/파서 확인"]
    return {"ok": not errors, "counts": {k: res[k] for k in ("fetched", "inserted", "matched_drugs", "unmatched_count")},
            "errors": errors}


@guarded_job("home_brand_related_refresh")
def home_brand_related_refresh_job():
    """매주 월요일 05:00 Seoul — Home 브랜드 목록(home_brand) 활성 브랜드마다 Naver
    연관검색어(자동완성, 크레덴셜 불필요)를 조회해 신규 브랜드 후보를 제안.

    agents.naver_related.expand_home_brands() 가 후보를 필터링해
    home_brand 에 source='related', active=0 으로 추가 — Home 브랜드 트래픽 집계에는
    반영되지 않고 admin 이 /api/admin/home-brands 에서 검토 후 활성화해야 반영된다.
    배포 안전: 네트워크/파싱 실패는 guarded_job 이 받아 원장 과제로 남긴다.
    """
    logger.info("━━━ Home 브랜드 연관검색어 확장 주간 sync 시작 ━━━")
    from agents import naver_related
    res = naver_related.expand_home_brands()
    logger.info("Home 브랜드 연관검색어 확장 완료: seeds_processed=%d candidates_added=%d",
                res.get("seeds_processed", 0), res.get("candidates_added", 0))
    return {"counts": {k: res.get(k, 0) for k in ("seeds_processed", "candidates_added")}}


@guarded_job("foreign_config_probe")
def foreign_config_probe_job():
    """매일 07:40 Seoul — 해외약가 설정 프로브(네트워크 없음): 로그인 필수 스크레이퍼의 자격증명 키 **이름**, scrapling import,
    선언된 프록시 키. 문제마다 config 과제 1건(foreign_config_probe:config:{문제}) — 사람이 닫을 때까지.

    별도 잡인 이유: 설정 문제는 데이터 나이(신선도)와 다른 종류라 키·런북이 다르고, 신선도가 판정 불가여도 돌아야 하며,
    사람이 .env 를 고친 뒤 `--config-probe-now` 로 바로 재확인할 수 있어야 한다. 설정 문제는 과제이지 잡 실패가 아니다(ok=True).
    """
    from agents.agent_tasks.config_probe import run_and_record
    res = run_and_record(base_dir=BASE_DIR)
    logger.info("해외약가 설정 프로브: %s", res["counts"])
    return res


@guarded_job("domain_freshness")
def domain_freshness_job(data_db_path: str | None = None):
    """매일 07:45 Seoul — 16개 데이터 도메인 신선도 판정 → stale 은 원장 과제(도메인당 월 1건). 02:00~07:00 수집 잡 뒤, 업무 전.

    잡 성공 여부와 무관하게 '데이터가 언제 것인가' 를 본다 — 폴백·빈 크롤·수동 업로드 누락은 여기서만 잡힌다.
    stale 은 과제이지 잡 실패가 아니다(ok=True). data_db_path 는 CLI 전용(읽기 전용 URI 로 실제 DB 실측).
    """
    from agents.agent_tasks.freshness import run_and_record
    res = run_and_record(base_dir=BASE_DIR, data_db_path=data_db_path)
    logger.info("도메인 신선도: %s (stale=%s)", res["counts"], res["stale_domains"])
    return res


# ── 구현 대기(placeholder) — 스케줄 등록 해제됨 (P1.5 Task 7) ──────────────────────
# 아래 네 함수는 backend 미구현 상태로 매일 '성공' 로그만 남겼다. 거짓 성공이 관측을 오염시키므로
# build_job_specs 에서 내렸고, 미구현 사실은 signals.KNOWN_DEBTS → 원장 improvement 과제로 남는다.
# 구현이 끝나면 build_job_specs 와 JOB_RUNBOOK 에 다시 넣고 KNOWN_DEBTS 에서 해당 항목을 지운다.
# 함수·docstring·CLI(--amjilsim-d-minus-2-now 등)는 후속 구현 자리로 보존한다.
@guarded_job("amjilsim_d_minus_2_reporter")
def amjilsim_d_minus_2_reporter_job():
    """매일 16:00 Seoul — 오늘이 어느 위원회 D-2이면 사전 예측 보고서 발사 (17:00 마감).

    calendar.py 조회 → "오늘 + 2일"이 약평위/암질심 차수면:
      → context_packager.build(committee, session_id)
      → claude --agent amjilsim-d-minus-2 호출
      → markdown 보고서 → ~/심평원보고/reports/ + Obsidian + PDF
    """
    logger.info("━━━ amjilsim D-2 reporter 점검 ━━━")
    try:
        from datetime import date
        from agents.amjilsim_tracker.calendar import session_for_offset
        today = date.today()
        session = session_for_offset(today, "d_minus_2")
        if session is None:
            logger.info("오늘은 어떤 차수의 D-2도 아님 — idle pass")
            return
        logger.info("D-2 발사 대상: %s %d차 (%s)",
                    session.committee, session.ordinal, session.session_date)
        # TODO: backend 호출
        # from agents.amjilsim_tracker.agent import AmjilsimTrackerAgent
        # AmjilsimTrackerAgent().run_d_minus_2(session.committee, session.ordinal)
    except Exception as e:
        logger.exception("amjilsim D-2 reporter 실패: %s", e)


@guarded_job("amjilsim_d_plus_1_reporter")
def amjilsim_d_plus_1_reporter_job():
    """매일 08:00 Seoul — 전일이 차수일이면 결과 리뷰 + 자가 학습 audit (09:00 마감).

    calendar.py 조회 → "오늘 - 1일"이 약평위/암질심 차수면:
      → HIRA 공식 보도자료 본문 자동 fetch (전일 발표분)
      → 예측 vs 실제 audit (audit_log.md 갱신)
      → 룰 weight 자동 보정 (FP -0.05) + 신규 CANDIDATE 룰 등록
      → claude --agent amjilsim-d-plus-1 → markdown → Obsidian + PDF
    """
    logger.info("━━━ amjilsim D+1 reporter 점검 ━━━")
    try:
        from datetime import date
        from agents.amjilsim_tracker.calendar import session_for_offset
        today = date.today()
        session = session_for_offset(today, "d_plus_1")
        if session is None:
            logger.info("어제는 어떤 차수도 아님 — idle pass")
            return
        logger.info("D+1 발사 대상: %s %d차 (%s)",
                    session.committee, session.ordinal, session.session_date)
        # TODO: backend 호출 + audit 실행
        # AmjilsimTrackerAgent().run_d_plus_1(session.committee, session.ordinal)
    except Exception as e:
        logger.exception("amjilsim D+1 reporter 실패: %s", e)


@guarded_job("amjilsim_monthly_trend")
def amjilsim_monthly_trend_job():
    """매일 09:00 Seoul — 오늘이 그 달 마지막 약평위 D+7이면 월간 트렌드 진단 발사.

    calendar.py is_last_yakpyungwi_of_month() 조회 → 매월 마지막 약평위 + 7일이면:
      → 직전 4주 양 위원회 누적 데이터 + 정책 시그널 + 임상 update + 경쟁 환경
      → claude --agent hira-monthly-trend → markdown → Obsidian + PDF
    """
    logger.info("━━━ amjilsim monthly trend 점검 ━━━")
    try:
        from datetime import date, timedelta
        from agents.amjilsim_tracker.calendar import is_last_yakpyungwi_of_month
        today = date.today()
        seven_days_ago = today - timedelta(days=7)
        if not is_last_yakpyungwi_of_month(seven_days_ago):
            logger.info("오늘은 월간 트렌드 발사일 아님 (마지막 약평위 D+7 X) — idle pass")
            return
        logger.info("월간 트렌드 발사 — 직전 약평위 차수: %s + 7일", seven_days_ago)
        # TODO: AmjilsimTrackerAgent().run_monthly_trend()
    except Exception as e:
        logger.exception("amjilsim monthly trend 실패: %s", e)


@guarded_job("hira_schedule_fetcher_annual")
def hira_schedule_fetcher_job():
    """매년 1월 1일 09:00 + 각 차수 D-30 06:00 — HIRA 공식 일정 자동 갱신.

    Step 1: HIRA 공식 URL fetch (암질심·약평위 각각)
      - 암질심: https://www.hira.or.kr/dummy.do?pgmid=HIRAA030051000016
      - 약평위: https://www.hira.or.kr/dummy.do?pgmid=HIRAA030051000006
    Step 2: 추출 실패 시 fallback (WebSearch site:hira.or.kr "약평위 일정")
    Step 3: amjilsim_sessions DB 갱신 + 변경 감지 시 알림
    """
    logger.info("━━━ HIRA schedule fetcher 시작 ━━━")
    try:
        # TODO: agents/amjilsim_tracker/schedule_fetcher.py 구현 후
        # from agents.amjilsim_tracker.schedule_fetcher import fetch_and_sync
        # fetch_and_sync()
        logger.info("HIRA schedule fetcher placeholder — backend 구현 대기")
    except Exception as e:
        logger.exception("HIRA schedule fetcher 실패: %s", e)


@guarded_job("git_auto_backup")
def git_backup_job():
    """Git 자정 자동 백업 — 로컬 전용 (프로덕션 컨테이너는 git 없음 → 매일 ERROR 방지)"""
    if is_production() or shutil.which("git") is None:
        logger.info("Git 자동 백업 skip — 로컬 전용 잡 (프로덕션 컨테이너 또는 git 미설치)")
        return None
    logger.info("━━━ 자정 Git 자동 백업 시작 ━━━")
    try:
        subprocess.run(["git", "add", "."], cwd=BASE_DIR, check=True)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        res_commit = subprocess.run(["git", "commit", "-m", f"Auto backup: {now_str}"],
                                    cwd=BASE_DIR, capture_output=True, text=True)
        if "nothing to commit" in res_commit.stdout or "nothing to commit" in res_commit.stderr:
            logger.info("변경된 파일이 없어 백업(Commit)을 건너뜁니다.")
            return {"counts": {"committed": 0}}
        subprocess.run(["git", "push"], cwd=BASE_DIR, capture_output=True, text=True, check=True)
        logger.info("Git 백업 성공")
        return {"counts": {"committed": 1}}
    except subprocess.CalledProcessError as e:
        return {"errors": [f"git exit {e.returncode}: {(e.stderr or e.output or '')[:200]}"]}


@guarded_job("policy_intel_analysis_sync")
def policy_analysis_sync_job():
    """매일 02:10 — Policy Intelligence 큐레이션 사이드카 git sync. 반환 dict 의 error 키를 실패로 해석한다.

    `auth required`(토큰 부재, 프로덕션 fail-closed)는 재실행으로 안 풀리므로 error_class="config" —
    ref 가 `policy_intel_analysis_sync:config` 로 고정돼 사람이 닫을 때까지 과제 1건만 유지된다."""
    res = _sync_policy_analysis()
    counts = {k: res.get(k, 0) for k in ("copied", "skipped", "rejected")}
    if res.get("error"):
        logger.warning("policy analysis sync 실패: %s", res["error"])
        cls = "config" if str(res["error"]).startswith("auth required") else "transient"
        return {"ok": False, "counts": counts, "errors": [str(res["error"])], "error_class": cls}
    logger.info("policy analysis sync: %s", res)
    return {"ok": True, "counts": counts, "errors": []}


@guarded_job("scheduler_heartbeat", record_runs=False)
def scheduler_heartbeat_job():
    """60초 — 생존 파일 갱신(atomic rename). /api/health 가 mtime 으로 판정(웹 기동 5분 유예 뒤 15분 초과 → 503 → Fly check 실패).

    record_runs=False: 분 단위 잡이라 scheduler_job_run 을 채우지 않는다. 쓰기 실패(볼륨·디스크)는 guarded_job 이 과제로 남긴다(하루 1건).
    """
    from agents.agent_tasks.heartbeat import write_heartbeat
    write_heartbeat(pid=os.getpid())
    return None


@guarded_job("platform_smoke")
def platform_smoke_job():
    """매일 07:50 — 21개 화면 + 권한 게이트 스모크 (같은 컨테이너의 웹 프로세스, 공유 JWT_SECRET 으로 토큰 직접 발급).

    실패 = 화면 깨짐 → 과제 platform_smoke:<실패 집합 해시> 1건(같은 화면이 계속 깨져 있으면 날이 바뀌어도 같은 과제, 집합이
    바뀌면 새 과제 — signals.CONTENT_KEYED_JOBS). 토큰을 못 만들면(비밀 미공유) 그 역할
    화면은 skipped — 오탐 아님. base 는 MA_SMOKE_BASE 로 바꿀 수 있다(로컬 포트 등).
    """
    from scripts.smoke import DEFAULT_BASE, mint_token_factory, run_smoke
    base = os.environ.get("MA_SMOKE_BASE", DEFAULT_BASE)
    res = run_smoke(base, mint_token_factory(base))
    logger.info("플랫폼 스모크: %s", res["counts"])
    return res


# ── 등록부 ──────────────────────────────────────────────────────────────────
# 잡 전부를 데이터(JobSpec)로 둔다 — tests/test_scheduler_job_specs.py 가 guarded·misfire·JOB_RUNBOOK 일치를 대조한다.

@dataclass(frozen=True)
class JobSpec:
    id: str
    name: str
    fn: Callable
    trigger: object
    misfire_grace_time: int = DAILY_GRACE
    coalesce: bool = True
    max_instances: int = 1


def _cron(**kw) -> CronTrigger:
    return CronTrigger(timezone="Asia/Seoul", **kw)


def build_job_specs(sched_cfg: dict) -> list[JobSpec]:
    """등록할 잡 전부. 테스트가 이 목록으로 guarded·misfire·JOB_RUNBOOK 일치를 대조한다.

    ForeignPrice 주간 백필은 **비활성화**(2026-07-24) — 전체 indications_master 세트를 매주 재스크레이프해
    append 하면서 검색 히스토리 고정 노출·행 무한 누적 문제. 수동 `--price-backfill-now` 만 보존.
    Daily Mailing 헤르메스 git 버스(runs_sync 07:30 · scope_export 15분)는 retire — 함수/CLI 는 롤백용 보존, 잡 미등록.
    placeholder 4개(amjilsim D-2/D+1/월간 트렌드 · HIRA 일정 fetcher)는 P1.5 Task 7 에서 등록 해제 — backend 미구현으로
    매일 거짓 '성공' 만 남겼다. 함수/CLI 보존, 미구현 사실은 signals.KNOWN_DEBTS 가 원장 improvement 과제로 유지한다.
    """
    return [
        # 매월 1~7일 09:00 (config day="1-7" cron 범위 — 첫 주 매일 재시도. catch_up 이 당월 적재 확인 시 즉시 종료)
        JobSpec("domestic_price_pipeline", "국내 약가 모니터링 파이프라인", job,
                _cron(day=sched_cfg["day"], hour=sched_cfg["hour"], minute=sched_cfg["minute"]),
                misfire_grace_time=86400),                                   # 기존 정책(7/1·6/1 미실행 원인) 유지
        JobSpec("git_auto_backup", "Git 자동 백업", git_backup_job, _cron(hour=0, minute=0)),
        JobSpec("foreign_approval_sync", "ForeignApproval 가격↔허가 auto-sync", foreign_approval_sync_job,
                _cron(hour=4, minute=30)),
        JobSpec("rule_compliance_audit", "Rule Compliance 일일 감사", rule_compliance_audit_job, _cron(hour=5, minute=30)),
        JobSpec("quality_guard_review", "QualityGuard 코드베이스 일일 리뷰", quality_guard_review_job, _cron(hour=6, minute=0)),
        JobSpec("competitor_news_weekly", "경쟁사 뉴스 주간 크롤 (Tier 1, 13 브랜드)", competitor_news_weekly_job,
                _cron(day_of_week="mon", hour=3, minute=30), misfire_grace_time=PERIODIC_GRACE),
        JobSpec("competitor_news_daily", "Competitor/Gov News 매일 06:30 fetch (신선분)", competitor_news_daily_job,
                _cron(hour=6, minute=30)),
        JobSpec("competitor_trend_promote", "경쟁사 동향 카드 일일 승격 (아카이브→카드)", competitor_trend_promote_job,
                _cron(hour=7, minute=0)),
        JobSpec("access_insight_fresh_signals", "Access Insight 신선 신호 주간 크롤 (tier A/B/D → media signals)",
                access_insight_fresh_signal_job, _cron(day_of_week="mon", hour=4, minute=0),
                misfire_grace_time=PERIODIC_GRACE),
        JobSpec("reimbursement_xnational_sync", "Reimbursement cross-national 분기 sync (NICE/PBAC/CMS/CHUIKYO)",
                reimbursement_xnational_sync_job, _cron(month="1,4,7,10", day=1, hour=2, minute=0),
                misfire_grace_time=PERIODIC_GRACE),
        JobSpec("exchange_rate_refresh", "KEB 36개월 평균환율 월간 갱신 (매월 2일 04:00)", exchange_rate_refresh_job,
                _cron(day=2, hour=4, minute=0), misfire_grace_time=PERIODIC_GRACE),
        JobSpec("reimb_data_sync", "Reimbursement 위원회 데이터 매일 02:00 git sync", reimb_data_sync_job,
                _cron(hour=2, minute=0)),
        JobSpec("policy_intel_analysis_sync", "Policy Intelligence 큐레이션 사이드카 매일 02:10 git sync",
                policy_analysis_sync_job, _cron(hour=2, minute=10)),
        JobSpec("daily_mailing_dispatch", "Daily Mailing 10분 주기 발송 판정 (생성→승인대기/자동발송·재시도)",
                daily_mailing_dispatch_job, IntervalTrigger(minutes=10, timezone="Asia/Seoul"),
                misfire_grace_time=INTERVAL_GRACE),
        JobSpec("daily_mailing_top_news", "Daily Mailing 주요 MA 뉴스 매일 06:40", daily_mailing_top_news_job,
                _cron(hour=6, minute=40)),
        JobSpec("daily_mailing_health", "Daily Mailing 헬스체크 매일 09:30", daily_mailing_health_job, _cron(hour=9, minute=30)),
        JobSpec("nhis_negotiation_sync", "NHIS 약가협상 공개자료 주간 sync (신규/확대 → 매칭 교체)", nhis_negotiation_sync_job,
                _cron(day_of_week="mon", hour=2, minute=30), misfire_grace_time=PERIODIC_GRACE),
        JobSpec("home_brand_related_refresh", "Home 브랜드 Naver 연관검색어 확장 (주간, 후보 제안 → admin 승인 대기)",
                home_brand_related_refresh_job, _cron(day_of_week="mon", hour=5, minute=0),
                misfire_grace_time=PERIODIC_GRACE),
        JobSpec("foreign_config_probe", "해외약가 설정 프로브 (매일 07:40, 자격증명 키 이름·scrapling·프록시 — 문제당 config 과제)",
                foreign_config_probe_job, _cron(hour=7, minute=40)),
        JobSpec("domain_freshness", "데이터 도메인 신선도 판정 (매일 07:45, stale → 원장)", domain_freshness_job,
                _cron(hour=7, minute=45)),
        JobSpec("scheduler_heartbeat", "스케줄러 heartbeat (60초, /api/health 생존 판정)", scheduler_heartbeat_job,
                IntervalTrigger(seconds=60, timezone="Asia/Seoul"), misfire_grace_time=30),
        JobSpec("platform_smoke", "화면별 API 스모크 (매일 07:50, 21 화면 + 권한 게이트)", platform_smoke_job,
                _cron(hour=7, minute=50)),
    ]

# ── 진입점 ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MA AI 대쉬보드 스케줄러")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="스케줄 무시하고 즉시 실행 (국내약가 catch-up + 대쉬보드)",
    )
    parser.add_argument(
        "--domestic-catchup-now",
        action="store_true",
        help="국내 약가 월별 catch-up 만 즉시 실행 (누락 월 자동 적재)",
    )
    parser.add_argument(
        "--review-now",
        action="store_true",
        help="QualityGuard 리뷰만 즉시 실행",
    )
    parser.add_argument(
        "--compliance-now",
        action="store_true",
        help="Rule Compliance 감사만 즉시 실행",
    )
    parser.add_argument(
        "--record-debts-now",
        action="store_true",
        help="구현 대기 부채(signals.KNOWN_DEBTS)를 원장 improvement 과제로 즉시 기록 (멱등 — 열려 있으면 추가 없음)",
    )
    parser.add_argument(
        "--approval-sync-now",
        action="store_true",
        help="ForeignApproval auto-sync 즉시 실행",
    )
    parser.add_argument(
        "--price-backfill-now",
        action="store_true",
        help="ForeignPrice 주간 백필 즉시 실행 (모든 indications_master product)",
    )
    parser.add_argument(
        "--reimb-sync-now",
        action="store_true",
        help="Reimbursement xnational sync 즉시 실행 (NICE/PBAC/CMS/CHUIKYO)",
    )
    parser.add_argument(
        "--amjilsim-d-minus-2-now",
        action="store_true",
        help="amjilsim·약평위 D-2 reporter 즉시 실행 (calendar 무시 X — calendar gating 유지)",
    )
    parser.add_argument(
        "--amjilsim-d-plus-1-now",
        action="store_true",
        help="amjilsim·약평위 D+1 reporter 즉시 실행 (calendar gating 유지)",
    )
    parser.add_argument(
        "--amjilsim-daily-crawl-now",
        action="store_true",
        help="amjilsim 일별 크롤 즉시 실행",
    )
    parser.add_argument(
        "--hira-fetch-now",
        action="store_true",
        help="HIRA 공식 일정 즉시 fetch",
    )
    parser.add_argument(
        "--scope-export-now",
        action="store_true",
        help="Daily Mailing 스콥 export 즉시 실행 (active 구독 → 로컬 + 비공개 repo scopes/)",
    )
    parser.add_argument("--mailing-dispatch-now", action="store_true",
                        help="Daily Mailing 발송 판정 1회 즉시 실행")
    parser.add_argument("--mailing-health-now", action="store_true",
                        help="Daily Mailing 헬스체크 1회 즉시 실행")
    parser.add_argument("--smoke-now", action="store_true",
                        help="화면별 스모크 즉시 실행 (웹 서버 필요 — 기본 http://127.0.0.1:5001, MA_SMOKE_BASE 로 변경)")
    parser.add_argument("--config-probe-now", action="store_true",
                        help="해외약가 설정 프로브 즉시 실행 (네트워크 없음 — 자격증명 키 이름·scrapling import·프록시 키, 문제당 config 과제)")
    parser.add_argument(
        "--nhis-sync-now",
        action="store_true",
        help="NHIS 약가협상 공개자료 즉시 크롤+매칭 (신규/확대)",
    )
    parser.add_argument(
        "--fx-refresh-now",
        action="store_true",
        help="KEB 36개월 평균환율 즉시 갱신 (캐시 최신화 + 기존행 재계산)",
    )
    parser.add_argument(
        "--fx-recompute-now",
        action="store_true",
        help="기존 해외약가 행을 현재 캐시 환율로 재계산 (재스크레이프 없음)",
    )
    parser.add_argument(
        "--promote-trends-now",
        action="store_true",
        help="아카이브 뉴스 → 경쟁사 동향 카드 승격 즉시 실행",
    )
    parser.add_argument(
        "--fresh-signals-now",
        action="store_true",
        help="Access Insight 신선 신호 크롤 즉시 실행 (tier A/B/D → amjilsim_media_signals)",
    )
    # 런북(agents/agent_tasks/runbooks.py) 이 쓰는 뉴스 플래그 — news_weekly_crawl 은 분리 프로세스로 이 플래그를 띄운다
    parser.add_argument("--news-daily-now", action="store_true", help="경쟁사·정부 뉴스 3일 fetch 즉시 실행")
    parser.add_argument("--news-weekly-now", action="store_true", help="경쟁사·정부 뉴스 주간(6개월 창) 크롤 즉시 실행")
    parser.add_argument(
        "--reclassify-signals-now",
        action="store_true",
        help="현재 lexicon 으로 기존 amjilsim_media_signals 재분류 (signal_type/weight UPDATE, 삭제 없음)",
    )
    parser.add_argument(
        "--backfill-oncology-now",
        action="store_true",
        help="amjilsim_drugs.is_oncology 캐스케이드 백필 (ATC/efficacy/analog/indication)",
    )
    parser.add_argument(
        "--backfill-prominence-now",
        action="store_true",
        help="기존 amjilsim_media_signals prominence 재판정 (title/body_strong/passing UPDATE-only, 멱등)",
    )
    parser.add_argument(
        "--relink-sessions-now",
        action="store_true",
        help="신호 session_id 를 약제 track 별 위원회(항암=암질심/일반=약평위) 세션으로 재배정 (UPDATE-only, 멱등)",
    )
    parser.add_argument(
        "--freshness-now", nargs="?", const="", metavar="DATA_DB",
        help="16개 도메인 신선도 즉시 판정 + stale 과제 생성 (표 출력). 값을 주면 그 DB 를 판정 "
             "(file:/경로/drug_prices.db?mode=ro 로 실제 DB 읽기 전용 실측; 과제는 AGENT_TASK_DB 로)",
    )
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)

    if args.run_now:
        logger.info("수동 즉시 실행 모드")
        asyncio.run(run_pipeline())
        return

    if args.domestic_catchup_now:
        logger.info("국내 약가 catch-up 즉시 실행 (누락 월 적재, 당월 적재 여부 무관 force)")
        from agents.domestic_catchup import run_sync
        out = run_sync(load_config(), BASE_DIR, max_pages=3, force=True)
        logger.info("catch-up 결과: 신규 적재 %s · 오류 %s",
                    out.get("ingested"), out.get("errors"))
        return

    if args.review_now:
        logger.info("QualityGuard 리뷰 즉시 실행")
        quality_guard_review_job()
        return

    if args.compliance_now:
        logger.info("Rule Compliance 감사 즉시 실행")
        rule_compliance_audit_job()
        return

    if args.record_debts_now:
        logger.info("구현 대기 부채 과제 즉시 기록 (원장=%s)", resolve_db_path())
        _agent_record_known_debts()
        return

    if args.approval_sync_now:
        logger.info("ForeignApproval auto-sync 즉시 실행")
        foreign_approval_sync_job()
        return

    if args.price_backfill_now:
        logger.info("ForeignPrice 백필 즉시 실행")
        foreign_price_backfill_job()
        return

    if args.reimb_sync_now:
        logger.info("Reimbursement xnational sync 즉시 실행")
        reimbursement_xnational_sync_job()
        return

    if args.amjilsim_d_minus_2_now:
        logger.info("amjilsim D-2 reporter 즉시 점검")
        amjilsim_d_minus_2_reporter_job()
        return

    if args.amjilsim_d_plus_1_now:
        logger.info("amjilsim D+1 reporter 즉시 점검")
        amjilsim_d_plus_1_reporter_job()
        return

    if args.amjilsim_daily_crawl_now:
        logger.info("Reimbursement 데이터 sync 즉시 실행 (위원회 데이터 git→DB)")
        reimb_data_sync_job()
        policy_analysis_sync_job()          # 래퍼 경유 — 실패는 원장 과제로
        # Daily Mailing 헤르메스 버스 retire (2026-09-18) — 부팅 sync/export 제거
        return

    if args.hira_fetch_now:
        logger.info("HIRA 공식 일정 즉시 fetch")
        hira_schedule_fetcher_job()
        return

    if args.scope_export_now:
        logger.info("Daily Mailing 스콥 export 즉시 실행")
        daily_mailing_scope_export_job()
        return

    if args.mailing_dispatch_now:
        logger.info("Daily Mailing 발송 판정 즉시 실행")
        daily_mailing_dispatch_job()
        return

    if args.mailing_health_now:
        logger.info("Daily Mailing 헬스체크 즉시 실행")
        daily_mailing_health_job()
        return

    if args.smoke_now:
        logger.info("플랫폼 스모크 즉시 실행: %s", platform_smoke_job())
        return

    if args.config_probe_now:
        from agents.agent_tasks.config_probe import env_from, format_table, probe
        # 표는 순수 판정(기록 없음)에서 — guarded_job 의 normalize_result 는 {ok, counts, errors} 만 남겨 problems 를 잃는다
        print(format_table(probe(env=env_from(BASE_DIR))))
        res = foreign_config_probe_job()                       # 기록: 실행 1행 + 문제당 config 과제
        print(json.dumps(res["counts"], ensure_ascii=False))
        return

    if args.nhis_sync_now:
        logger.info("NHIS 약가협상 공개자료 즉시 sync")
        nhis_negotiation_sync_job()
        return

    if args.fx_refresh_now:
        logger.info("KEB 36개월 평균환율 즉시 갱신")
        exchange_rate_refresh_job()
        return

    if args.fx_recompute_now:
        logger.info("해외약가 FX 재계산 즉시 실행 (재스크레이프 없음)")
        exchange_rate_recompute_job()
        return

    if args.promote_trends_now:
        logger.info("경쟁사 동향 카드 승격 즉시 실행 (아카이브→카드)")
        competitor_trend_promote_job()
        return

    if args.fresh_signals_now:
        logger.info("Access Insight 신선 신호 크롤 즉시 실행")
        access_insight_fresh_signal_job()
        return

    if args.news_daily_now:
        logger.info("뉴스 매일 fetch 즉시 실행: %s", competitor_news_daily_job())
        return

    if args.news_weekly_now:
        logger.info("뉴스 주간 크롤 즉시 실행: %s", competitor_news_weekly_job())
        return

    if args.reclassify_signals_now:
        logger.info("Access Insight 신호 재분류 즉시 실행 (lexicon 기반 UPDATE)")
        from agents.access_insight.classify import reclassify_signals
        res = reclassify_signals()
        logger.info(
            "재분류 완료: total=%s changed=%s / before=%s after=%s",
            res.get("total"), res.get("changed"), res.get("before"), res.get("after"),
        )
        return

    if args.backfill_oncology_now:
        logger.info("amjilsim_drugs.is_oncology 백필 즉시 실행")
        from agents.access_insight.backfill import backfill_oncology
        res = backfill_oncology()
        logger.info(
            "is_oncology 백필: oncology=%s general=%s by_rule=%s (manual_review=%s건)",
            res.get("oncology"), res.get("general"), res.get("by_rule"),
            len(res.get("manual_review", [])),
        )
        return

    if args.backfill_prominence_now:
        logger.info("amjilsim_media_signals prominence 재판정 즉시 실행 (A1, UPDATE-only)")
        from agents.access_insight.backfill import backfill_prominence
        res = backfill_prominence()
        logger.info(
            "prominence 백필: total=%s updated=%s source_verified_fixed=%s by_prominence=%s",
            res.get("total"), res.get("updated"),
            res.get("source_verified_fixed"), res.get("by_prominence"),
        )
        return

    if args.freshness_now is not None:
        from agents.agent_tasks.freshness import Ctx, format_table, run_all, today_kst
        data_db = args.freshness_now or resolve_db_path()
        logger.info("도메인 신선도 즉시 판정 (데이터 DB=%s, 원장=%s)", data_db, resolve_db_path())
        print(format_table(run_all(Ctx(db_path=data_db, base_dir=BASE_DIR, today=today_kst()))))
        res = domain_freshness_job(data_db_path=args.freshness_now or None)
        logger.info("신선도 판정 결과: %s", res)
        return

    if args.relink_sessions_now:
        logger.info("신호 session_id track 별 위원회 재배정 즉시 실행 (A2, UPDATE-only)")
        from agents.access_insight.backfill import relink_sessions
        res = relink_sessions()
        logger.info(
            "세션 재배정: total=%s changed=%s cleared=%s by_committee=%s",
            res.get("total"), res.get("changed"),
            res.get("cleared"), res.get("by_committee"),
        )
        return

    # 스케줄러 설정: 매월 1~7일 09:00 (config day="1-7" cron 범위 — 첫 주 매일 재시도.
    # 9/1 HIRA 다운로드 실패 시 다음 달까지 재시도가 없던 문제. catch_up 이 당월 적재
    # 확인 시 브라우저/프록시 없이 즉시 종료하므로 2~7일 실행은 사실상 무비용)
    sched_cfg = config["domestic_agent"]["schedule"]
    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    for spec in build_job_specs(sched_cfg):
        scheduler.add_job(spec.fn, trigger=spec.trigger, id=spec.id, name=spec.name, replace_existing=True,
                          misfire_grace_time=spec.misfire_grace_time, coalesce=spec.coalesce,
                          max_instances=spec.max_instances)
    scheduler.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    _agent_record_known_debts()          # 구현 대기 부채 → 원장 (기동 시 1회, 멱등 — 열려 있으면 0건 추가)

    logger.info("%s", _schedule_banner(sched_cfg))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


def _schedule_banner(sched_cfg: dict) -> str:
    """스케줄러 시작 로그 — day 는 int(1) 또는 cron 범위 문자열("1-7") 모두 허용."""
    return (
        f"스케줄러 시작 — 파이프라인(매월 {sched_cfg['day']}일 "
        f"{int(sched_cfg['hour']):02d}:{int(sched_cfg['minute']):02d}) / "
        "Git 백업(매일 00:00) / QualityGuard 리뷰(매일 06:00)"
    )


if __name__ == "__main__":
    main()
