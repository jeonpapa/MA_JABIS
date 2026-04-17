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
