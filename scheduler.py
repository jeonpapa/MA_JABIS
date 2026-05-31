"""
MA AI 대쉬보드 스케줄러
- 매월 1일 09:00 에 국내 약가 에이전트를 자동 실행한다.
- 실행 후 대쉬보드를 자동으로 갱신한다.
- 수동 실행: python scheduler.py --run-now
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from agents.domestic_price_agent import DomesticPriceAgent
from agents.dashboard_agent import DashboardAgent

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


# ── 파이프라인 ──────────────────────────────────────────────────────────────

def load_config() -> dict:
    config_path = BASE_DIR / "config" / "settings.json"
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


async def run_pipeline():
    """전체 파이프라인: 국내 약가 에이전트 → 대쉬보드 에이전트"""
    logger.info("━━━ 파이프라인 시작 ━━━")
    config = load_config()

    # 1) 국내 약가 에이전트
    domestic_agent = DomesticPriceAgent(config, BASE_DIR)
    meta = await domestic_agent.run()

    if meta is None:
        logger.error("국내 약가 에이전트 실패 — 대쉬보드 업데이트 건너뜀")
        return

    # 2) 대쉬보드 에이전트
    dashboard_agent = DashboardAgent(config, BASE_DIR)
    html_path = dashboard_agent.run()

    logger.info("━━━ 파이프라인 완료 ━━━")
    logger.info("대쉬보드: file://%s", html_path.resolve())


def job():
    """APScheduler 콜백 — 비동기 파이프라인 실행"""
    asyncio.run(run_pipeline())


import subprocess
from datetime import datetime


def foreign_approval_sync_job():
    """ForeignApprovalAgent auto-sync — 매일 04:30 Seoul (compliance 감사 전).

    foreign_drug_prices 에 있으나 indications_master 에 없는 제품을 자동 build.
    """
    from agents.foreign_approval.agent import ForeignApprovalAgent

    logger.info("━━━ ForeignApproval auto-sync 시작 ━━━")
    try:
        agent = ForeignApprovalAgent()
        gaps = agent.list_coverage_gaps()
        if not gaps:
            logger.info("ForeignApproval: gap 0건 — 이미 동기화됨")
            return
        logger.info("ForeignApproval: gap %d건 — %s", len(gaps), gaps)
        result = agent.sync_from_prices()
        logger.info(
            "ForeignApproval: built=%d failed=%d",
            len(result["built"]), len(result["failed"]),
        )
        for f in result["failed"]:
            logger.warning("  ✗ %s: %s", f["slug"], f["reason"])
    except Exception as e:
        logger.exception("ForeignApproval auto-sync 실패: %s", e)


def rule_compliance_audit_job():
    """RuleComplianceAgent — 매일 05:30 Seoul (QG 06:00 리뷰 직전).

    사용자와 합의한 메모리 항목(feedback/project/reference)이 실제 런타임에서 지켜지는지
    자동 감사. 증거 기반 PASS/FAIL/SKIP 보고서를 `quality_guard/compliance_YYYY-MM-DD.md` 로 저장.
    """
    from agents.rule_compliance import RuleComplianceAgent

    logger.info("━━━ Rule Compliance 감사 시작 ━━━")
    try:
        agent = RuleComplianceAgent()
        results = agent.audit()
        path = agent.write_report(results)
        fails = [r for r in results if r.status == "fail"]
        passes = [r for r in results if r.status == "pass"]
        if fails:
            logger.error(
                "Rule Compliance: ❌ FAIL %d건 / ✅ PASS %d건 — %s",
                len(fails), len(passes), path,
            )
            for f in fails:
                logger.error("  • %s: %s", f.memory, f.detail)
        else:
            logger.info(
                "Rule Compliance: ✅ PASS %d건, 회귀 0건 — %s",
                len(passes), path,
            )
    except Exception as e:
        logger.exception("Rule Compliance 감사 실패: %s", e)


def quality_guard_review_job():
    """QualityGuardAgent 지속 리뷰 — 매일 06:00 Seoul.

    규칙 drift / MFDS baseline 회귀 / 개선 제안을 점검하고
    `quality_guard/review_YYYY-MM-DD.md` 를 생성한다. 회귀 발견 시 ERROR 로깅.
    """
    from agents.quality_guard import QualityGuardAgent

    logger.info("━━━ QualityGuard 일일 리뷰 시작 ━━━")
    try:
        guard = QualityGuardAgent()
        result = guard.review_codebase()
        regressions = result.get("mfds_regressions", [])
        drifts = result.get("rule_drifts", [])
        if regressions:
            logger.error("QualityGuard: MFDS baseline 회귀 %d건 — %s",
                         len(regressions), result["report_path"])
        elif drifts:
            logger.warning("QualityGuard: 규칙↔코드 drift %d건 — %s",
                           len(drifts), result["report_path"])
        else:
            logger.info("QualityGuard: 회귀 0건 / 제안 %d건 — %s",
                        len(result.get("suggestions", [])), result["report_path"])
    except Exception as e:
        logger.exception("QualityGuard 리뷰 실패: %s", e)


def foreign_price_backfill_job():
    """ForeignPrice backfill — 주 1회 (월요일 03:00 Seoul).

    indications_master 의 product slug 별로 ForeignPriceAgent.search_all 실행.
    foreign_drug_prices 가 Welireg 외 비어있는 케이스 자동 채움.
    pure-napping-goose plan Phase 6.
    """
    from agents.foreign_price_agent import ForeignPriceAgent

    logger.info("━━━ ForeignPrice 주간 백필 시작 ━━━")
    try:
        # indications_master 의 unique product slug
        from agents.db import DrugPriceDB
        db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
        with db._connect() as conn:
            slugs = [r[0] for r in conn.execute(
                "SELECT DISTINCT product FROM indications_master ORDER BY product"
            ).fetchall()]
        logger.info("ForeignPrice backfill: %d 약제 — %s", len(slugs), slugs)

        agent = ForeignPriceAgent(BASE_DIR)
        ok, fail = 0, 0
        for slug in slugs:
            # alias 우선 INN 검색 (예: keytruda → pembrolizumab)
            alias = db.get_product_alias(slug) or {}
            query = alias.get("inn") or slug
            try:
                results = asyncio.run(agent.search_all(query))
                total = sum(len(v) if isinstance(v, list) else 0 for v in results.values())
                logger.info("  ✓ %s (query=%s): %d 국가 응답", slug, query, total)
                ok += 1
            except Exception as e:
                logger.warning("  ✗ %s (query=%s): %s", slug, query, e)
                fail += 1
        logger.info("ForeignPrice 백필 완료: ok=%d fail=%d", ok, fail)
    except Exception as e:
        logger.exception("ForeignPrice backfill 실패: %s", e)


def reimbursement_xnational_sync_job():
    """Cross-national reimbursement sync — 분기 1회 (1·4·7·10월 1일 02:00 Seoul).

    NICE / PBAC / CMS / 일본 中医協 4 site 자동 호출 → reimbursement_xnational 적재.
    pure-napping-goose plan Phase 6.
    """
    logger.info("━━━ Reimbursement xnational sync 시작 ━━━")
    try:
        from agents.foreign_approval.reimbursement_sync import sync_all
        summary = sync_all()
        logger.info("Reimbursement sync 완료:")
        for slug, counts in summary.items():
            logger.info("  %s: %s", slug, counts)
    except Exception as e:
        logger.exception("Reimbursement sync 실패: %s", e)


# ── HIRA Pipeline Tracker (암질심 + 약평위) — hira-pipeline-tracker skill backend ──

def amjilsim_daily_crawl_job():
    """매일 02:00 Seoul — HIRA 공식 보도자료 + 27개 매체 일별 크롤.

    1. HIRA 게시판 신규 보도자료 list fetch (https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA020041000100)
    2. 신규 약평위/암질심 보도자료 발견 시 본문 fetch + DB 적재
    3. 27개 의약 전문지 약평위·암질심 키워드 크롤 → media_signals 적재
    4. 신호 body verification 강제 (signal_attribution_rules.md)
    """
    logger.info("━━━ amjilsim daily crawl 시작 ━━━")
    try:
        # TODO: backend python module 구현 후 실제 호출
        # from agents.amjilsim_tracker.agent import AmjilsimTrackerAgent
        # AmjilsimTrackerAgent().daily_crawl()
        logger.info("amjilsim daily crawl placeholder — backend 구현 대기")
    except Exception as e:
        logger.exception("amjilsim daily crawl 실패: %s", e)


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


def git_backup_job():
    """Git 자정 자동 백업"""
    logger.info("━━━ 자정 Git 자동 백업 시작 ━━━")
    try:
        subprocess.run(["git", "add", "."], cwd=BASE_DIR, check=True)
        
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        res_commit = subprocess.run(
            ["git", "commit", "-m", f"Auto backup: {now_str}"], 
            cwd=BASE_DIR, capture_output=True, text=True
        )
        
        if "nothing to commit" in res_commit.stdout or "nothing to commit" in res_commit.stderr:
            logger.info("변경된 파일이 없어 백업(Commit)을 건너뜁니다.")
            return

        res_push = subprocess.run(
            ["git", "push"], 
            cwd=BASE_DIR, capture_output=True, text=True, check=True
        )
        logger.info("Git 백업 성공")
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Git 명령어 실행 실패 (exit code: {e.returncode}): {e.stderr or e.output}")
    except Exception as e:
        logger.error(f"Git 백업 중 오류 발생: {e}")


# ── 진입점 ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MA AI 대쉬보드 스케줄러")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="스케줄 무시하고 즉시 실행",
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
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)

    if args.run_now:
        logger.info("수동 즉시 실행 모드")
        asyncio.run(run_pipeline())
        return

    if args.review_now:
        logger.info("QualityGuard 리뷰 즉시 실행")
        quality_guard_review_job()
        return

    if args.compliance_now:
        logger.info("Rule Compliance 감사 즉시 실행")
        rule_compliance_audit_job()
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
        logger.info("amjilsim 일별 크롤 즉시 실행")
        amjilsim_daily_crawl_job()
        return

    if args.hira_fetch_now:
        logger.info("HIRA 공식 일정 즉시 fetch")
        hira_schedule_fetcher_job()
        return

    # 스케줄러 설정: 매월 1일 09:00
    sched_cfg = config["domestic_agent"]["schedule"]
    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        job,
        trigger=CronTrigger(
            day=sched_cfg["day"],
            hour=sched_cfg["hour"],
            minute=sched_cfg["minute"],
            timezone="Asia/Seoul",
        ),
        id="domestic_price_pipeline",
        name="국내 약가 모니터링 파이프라인",
        replace_existing=True,
    )

    # Git 자정 자동 백업 스케줄 추가
    scheduler.add_job(
        git_backup_job,
        trigger=CronTrigger(
            hour=0,
            minute=0,
            timezone="Asia/Seoul",
        ),
        id="git_auto_backup",
        name="Git 자동 백업",
        replace_existing=True,
    )

    # ForeignApproval auto-sync — 매일 04:30 Seoul (compliance 감사 전, 가격↔허가 비대칭 해소)
    scheduler.add_job(
        foreign_approval_sync_job,
        trigger=CronTrigger(
            hour=4,
            minute=30,
            timezone="Asia/Seoul",
        ),
        id="foreign_approval_sync",
        name="ForeignApproval 가격↔허가 auto-sync",
        replace_existing=True,
    )

    # Rule Compliance 감사 — 매일 05:30 Seoul (QG 06:00 직전, 합의 룰 ↔ 런타임 대조)
    scheduler.add_job(
        rule_compliance_audit_job,
        trigger=CronTrigger(
            hour=5,
            minute=30,
            timezone="Asia/Seoul",
        ),
        id="rule_compliance_audit",
        name="Rule Compliance 일일 감사",
        replace_existing=True,
    )

    # QualityGuard 지속 리뷰 — 매일 06:00 Seoul (업무 시작 전)
    scheduler.add_job(
        quality_guard_review_job,
        trigger=CronTrigger(
            hour=6,
            minute=0,
            timezone="Asia/Seoul",
        ),
        id="quality_guard_review",
        name="QualityGuard 코드베이스 일일 리뷰",
        replace_existing=True,
    )

    # ForeignPrice 백필 — 매주 월요일 03:00 Seoul (모든 indications_master product)
    scheduler.add_job(
        foreign_price_backfill_job,
        trigger=CronTrigger(
            day_of_week="mon",
            hour=3,
            minute=0,
            timezone="Asia/Seoul",
        ),
        id="foreign_price_backfill",
        name="ForeignPrice 주간 백필 (8개국)",
        replace_existing=True,
    )

    # Reimbursement xnational sync — 분기 1회 (1·4·7·10월 1일 02:00 Seoul)
    scheduler.add_job(
        reimbursement_xnational_sync_job,
        trigger=CronTrigger(
            month="1,4,7,10",
            day=1,
            hour=2,
            minute=0,
            timezone="Asia/Seoul",
        ),
        id="reimbursement_xnational_sync",
        name="Reimbursement cross-national 분기 sync (NICE/PBAC/CMS/CHUIKYO)",
        replace_existing=True,
    )

    # ─── HIRA Pipeline Tracker (암질심·약평위) ────────────────────────────────

    # 매일 02:00 — HIRA 공식 보도자료 + 27개 의약 전문지 일별 크롤
    scheduler.add_job(
        amjilsim_daily_crawl_job,
        trigger=CronTrigger(hour=2, minute=0, timezone="Asia/Seoul"),
        id="amjilsim_daily_crawl",
        name="amjilsim·약평위 매일 02:00 일별 크롤",
        replace_existing=True,
    )

    # 매일 16:00 — D-2 사전 예측 보고서 (calendar gating)
    scheduler.add_job(
        amjilsim_d_minus_2_reporter_job,
        trigger=CronTrigger(hour=16, minute=0, timezone="Asia/Seoul"),
        id="amjilsim_d_minus_2_reporter",
        name="amjilsim·약평위 D-2 사전 예측 보고서 발사 (16:00, 17:00 마감)",
        replace_existing=True,
    )

    # 매일 08:00 — D+1 결과 리뷰 + 자가 학습 audit (calendar gating)
    scheduler.add_job(
        amjilsim_d_plus_1_reporter_job,
        trigger=CronTrigger(hour=8, minute=0, timezone="Asia/Seoul"),
        id="amjilsim_d_plus_1_reporter",
        name="amjilsim·약평위 D+1 결과 리뷰 + 예측 룰 자가 학습 (08:00, 09:00 마감)",
        replace_existing=True,
    )

    # 매일 09:00 — 월간 트렌드 진단 (매월 마지막 약평위 D+7 gating)
    scheduler.add_job(
        amjilsim_monthly_trend_job,
        trigger=CronTrigger(hour=9, minute=0, timezone="Asia/Seoul"),
        id="amjilsim_monthly_trend",
        name="amjilsim·약평위 월간 트렌드 진단 (매월 마지막 약평위 D+7)",
        replace_existing=True,
    )

    # 매년 1월 1일 09:00 + 각 차수 D-30 06:00 — HIRA 공식 일정 자동 갱신
    scheduler.add_job(
        hira_schedule_fetcher_job,
        trigger=CronTrigger(month=1, day=1, hour=9, minute=0, timezone="Asia/Seoul"),
        id="hira_schedule_fetcher_annual",
        name="HIRA 공식 일정 매년 1/1 자동 갱신",
        replace_existing=True,
    )

    logger.info(
        "스케줄러 시작 — 파이프라인(매월 %d일 %02d:%02d) / "
        "Git 백업(매일 00:00) / QualityGuard 리뷰(매일 06:00)",
        sched_cfg["day"], sched_cfg["hour"], sched_cfg["minute"],
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    main()
