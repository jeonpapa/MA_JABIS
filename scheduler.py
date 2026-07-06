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
import os
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from agents.domestic_price_agent import DomesticPriceAgent
from agents.dashboard_agent import DashboardAgent
from agents.ingest.policy_analysis_sync import sync_analysis as _sync_policy_analysis
from agents.ingest.daily_mailing_sync import sync_daily_mailing_runs as _sync_daily_mailing_runs

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
    """전체 파이프라인: 국내 약가 월별 catch-up(누락 자동복구) → 대쉬보드.

    매월 1일 09:00 실행. 기존 단발(download_latest)에서 **catch_up** 으로 전환 —
    스케줄러 미가동/실패로 빠진 달이 있어도 DB 최신일보다 새로운 모든 월을 한 번에 적재.
    """
    logger.info("━━━ 파이프라인 시작 ━━━")
    config = load_config()

    # 1) 국내 약가 월별 catch-up (누락 월 자동 복구 적재)
    from agents.domestic_catchup import catch_up
    try:
        result = await catch_up(config, BASE_DIR)
        logger.info("국내 약가 catch-up: 신규 적재 %s · 오류 %s",
                    result.get("ingested"), result.get("errors"))
        if result.get("errors"):
            logger.warning("catch-up 일부 오류 — 다음 실행 시 재시도(멱등)")
    except Exception as e:
        logger.error("국내 약가 catch-up 실패: %s", e, exc_info=True)
        # 적재 실패해도 대쉬보드는 기존 데이터로 갱신

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


def exchange_rate_refresh_job():
    """KEB 36개월 평균환율 갱신 — 매월 2일 04:00 Seoul.

    전월 말일이 확정된 직후(2일)에 KEB하나은행 기간평균(36개월 rolling, 매매기준율)
    엑셀을 Playwright 로 재다운로드해 data/foreign/exchange_rate/ 캐시를 최신화.
    이후 ForeignPriceAgent 의 A8 조정가 계산이 항상 최신 36개월 창을 사용하도록 보장.

    이전에는 정기 갱신 잡이 없어 누군가 refresh() 를 호출하기 전까지 마지막 xlsx 가
    계속 쓰였음(KEB 36mo 창 drift 의 원인). chromium 은 Dockerfile 에 이미 설치됨.
    배포 안전: 네트워크/Playwright 실패가 전체 스케줄러를 막지 않도록 try/except 격리.
    """
    logger.info("━━━ KEB 36개월 평균환율 갱신 시작 ━━━")
    try:
        from agents.exchange_rate import ExchangeRateFetcher
        fetcher = ExchangeRateFetcher()
        rates = asyncio.run(fetcher.refresh(headless=True))
        meta = fetcher._rate_meta
        logger.info(
            "환율 갱신 완료: %d개 통화 (기간 %s~%s) — %s",
            len(rates), meta.get("from", "?"), meta.get("to", "?"),
            {k: rates.get(k) for k in ("USD", "EUR", "JPY", "GBP", "CHF") if k in rates},
        )
        # 갱신 직후 기존 최신 해외약가 행도 최종환율로 재계산 (주간 백필 대기 없이 즉시 반영)
        try:
            from agents.db import DrugPriceDB
            db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
            rc = db.recompute_foreign_fx(rates, meta)
            logger.info("환율 갱신 후 기존행 재계산: 대상 %d · 갱신 %d · skip %d",
                        rc["candidates"], rc["updated"], rc["skipped"])
        except Exception as e:
            logger.exception("환율 갱신 후 재계산 실패(캐시는 갱신됨): %s", e)
    except Exception as e:
        logger.exception("KEB 환율 갱신 실패: %s", e)


def exchange_rate_recompute_job():
    """기존 최신 해외약가 행을 현재 캐시 환율(최종)로 재계산 — 재스크레이프 없음.

    환율은 adjusted_price_krw 에 선형이라 new/old 비율로 스케일해 새 행을 append.
    환율 잡(매월 2일)에 이미 포함돼 있고, 이 잡은 캐시는 그대로 두고 재계산만
    수동 실행할 때 사용 (예: 고시회차 수정 후 기존행 즉시 반영).
    """
    logger.info("━━━ 해외약가 FX 재계산 시작 ━━━")
    try:
        from agents.exchange_rate import ExchangeRateFetcher
        from agents.db import DrugPriceDB
        fetcher = ExchangeRateFetcher()
        if not fetcher._load_latest_cache():
            logger.warning("FX 재계산: 환율 캐시 없음 — --fx-refresh-now 먼저 실행 필요")
            return
        db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
        res = db.recompute_foreign_fx(fetcher._rates, fetcher._rate_meta)
        logger.info("FX 재계산 완료: 대상 %d · 갱신 %d · skip %d (기간 %s~%s)",
                    res["candidates"], res["updated"], res["skipped"],
                    fetcher._rate_meta.get("from"), fetcher._rate_meta.get("to"))
    except Exception as e:
        logger.exception("FX 재계산 실패: %s", e)


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

def competitor_news_weekly_job():
    """매주 월요일 03:30 Seoul — 경쟁사 Tier 1 뉴스 하이브리드 수집 + 1년 보존 정리.

    하이브리드 2축 (agents/competitor_news_agent.crawl):
      ① Naver News API → 전 매체 검색 후 T1 도메인만 필터 (config/media_tiers.json).
      ② T1 전문지 직접 검색 (agents/scrapers/tier1_news_sites) — Naver 미인덱싱 갭필러
         (뉴스더보이스·히트뉴스). canonical URL 로 ①②/페이지 중복 제거.
    competitor_news 테이블 누적, expires_at(발행일+365)<오늘 자동 삭제. Naver 키 필요(사이트축은 키 불필요).
    배포 안전: 사이트별/페이지별 try/except·timeout·정중딜레이, 한 소스 실패가 전체를 막지 않음.
    """
    logger.info("━━━ Competitor News (Tier1) 주간 크롤 시작 ━━━")
    try:
        from agents import competitor_news_agent as cn
        result = cn.crawl(lookback_days=cn.DEFAULT_LOOKBACK_DAYS, t1_only=True)
        logger.info("Competitor News 크롤 완료: 신규 %d건, 만료정리 %d건",
                    result.get("total_stored", 0), result.get("expired_removed", 0))
    except Exception as e:
        logger.exception("Competitor News 주간 크롤 실패: %s", e)

    # ── 정부·보건당국 정책 뉴스 아카이브 (Home 키워드 클라우드 소스) ──
    # 같은 competitor_news 테이블(kind='gov_policy')에 누적 → 키워드↔근거기사 보장.
    logger.info("━━━ Gov Policy News 주간 크롤 시작 ━━━")
    try:
        from agents import gov_policy_news as gpn
        gov_result = gpn.crawl(lookback_days=gpn.DEFAULT_LOOKBACK_DAYS)
        logger.info("Gov Policy News 크롤 완료: 신규 %d건", gov_result.get("total_stored", 0))
    except Exception as e:
        logger.exception("Gov Policy News 주간 크롤 실패: %s", e)


def competitor_news_daily_job():
    """매일 06:30 Seoul — 경쟁사 Tier1 + 정부정책 뉴스 소규모(최근 3일) fetch.

    주간 딥 크롤(competitor_news_weekly_job, 6개월+만료정리)과 별개로 매일 아침 신선분만
    수집 → 07:00 promote 가 동향 카드/홈 대쉬보드에 반영. dedup(link)으로 중복 저장 없음.
    """
    logger.info("━━━ Competitor/Gov News 매일 fetch 시작 ━━━")
    try:
        from agents import competitor_news_agent as cn
        r = cn.crawl(lookback_days=3, t1_only=True)
        logger.info("Competitor News 매일 fetch: 신규 %d건", r.get("total_stored", 0))
    except Exception as e:
        logger.exception("Competitor News 매일 fetch 실패: %s", e)
    try:
        from agents import gov_policy_news as gpn
        g = gpn.crawl(lookback_days=3)
        logger.info("Gov Policy News 매일 fetch: 신규 %d건", g.get("total_stored", 0))
    except Exception as e:
        logger.exception("Gov Policy News 매일 fetch 실패: %s", e)


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
    try:
        from agents.amjilsim_tracker.signal_extractor import run_fresh_crawl
        res = run_fresh_crawl()
        logger.info(
            "신선 신호 크롤 완료: articles=%d matched=%d inserted=%d dup=%d (tier별 %s)",
            res.get("articles", 0), res.get("matched", 0),
            res.get("inserted", 0), res.get("duplicate_skipped", 0),
            res.get("by_tier", {}),
        )
    except Exception as e:
        logger.exception("Access Insight 신선 신호 크롤 실패: %s", e)


def competitor_trend_promote_job():
    """매일 07:00 Seoul — 아카이브(competitor_news) 최근 2일 뉴스 → 동향 카드 승격.

    06:30 매일 fetch(competitor_news_daily_job)로 들어온 신선분을 소스로 LLM 필터
    (badge/importance) 후 competitor_trend 에 source_type='promoted' UPSERT. manual 카드
    보존. 윈도우 2일은 Naver 인덱싱/수집 지연을 흡수(UPSERT 라 재승격 멱등).
    """
    logger.info("━━━ Competitor Trend 승격 (아카이브→카드) 시작 ━━━")
    try:
        from agents import competitor_trends_agent as ct
        res = ct.promote_from_archive(days=2)
        t = res.get("totals", {})
        logger.info("Trend 승격 완료: archive=%s accepted=%s upserted=%s",
                    t.get("archive"), t.get("accepted"), t.get("upserted"))
    except Exception as e:
        logger.exception("Competitor Trend 승격 실패: %s", e)


def _reimb_committee_data_sync():
    """위원회 데이터(JSON) git-sync — 멱등. 데이터 변경 없으면 skip."""
    from agents.ingest import reimb_committee_import as imp
    from agents import reimb_reports as rr

    source = os.environ.get("REIMB_DATA_URL") or None
    if source:  # git URL 우선, 실패 시 이미지 로컬 JSON 폴백
        try:
            payload = imp.load_payload(source)
        except Exception as e:
            logger.warning("데이터 sync: REIMB_DATA_URL fetch 실패(%s) → 로컬 폴백", e)
            payload = imp.load_payload(None)
            source = f"{source} (폴백:local)"
    else:
        payload = imp.load_payload(None)
    new_hash = imp.payload_hash(payload)

    hash_file = BASE_DIR / "data" / "reimb" / ".last_applied_hash"
    prev_hash = hash_file.read_text().strip() if hash_file.exists() else ""
    if new_hash == prev_hash:
        logger.info("데이터 sync: 변경 없음 (hash %s) — skip", new_hash[:12])
        return

    result = imp.run(payload)
    try:
        rr.backfill_blobs()
    except Exception as e:
        logger.warning("데이터 sync: blob backfill 스킵: %s", e)
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text(new_hash)
    logger.info("데이터 sync 완료: 약제 %d · 이벤트 %d (신규 %d) · hash %s · source=%s",
                result["drugs_after"], result["events_total"], result["events_added"],
                new_hash[:12], source or "local")


def _reimb_reports_sync():
    """위원회 전후 보고서 git-sync (매니페스트 → Intelligence Reports) — 멱등."""
    import hashlib as _hl
    import json as _j
    from agents import reimb_reports as rr

    rsrc = os.environ.get("REPORTS_DATA_URL") or None
    rhash_file = BASE_DIR / "data" / "reimb" / ".last_reports_hash"
    prev_rhash = rhash_file.read_text().strip() if rhash_file.exists() else ""
    try:  # 매니페스트 URL 우선, 실패 시 이미지 로컬 폴백
        manifest = rr.load_reports_manifest(rsrc)
    except Exception as e:
        logger.warning("보고서 sync: REPORTS_DATA_URL fetch 실패(%s) → 로컬 매니페스트 폴백", e)
        manifest = rr.load_reports_manifest(None)
    m_hash = _hl.sha256(_j.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if m_hash == prev_rhash:
        logger.info("보고서 sync: 매니페스트 변경 없음 (hash %s) — skip", m_hash[:12])
        return
    rres = rr.sync_reports(manifest)
    rhash_file.parent.mkdir(parents=True, exist_ok=True)
    rhash_file.write_text(m_hash)
    logger.info("보고서 sync 완료: 매니페스트 %d · 신규적재 %d · skip %d · 오류 %d · hash %s",
                rres["reports_in_manifest"], len(rres["ingested"]), rres["skipped"],
                len(rres["errors"]), m_hash[:12])
    if rres["errors"]:
        logger.warning("보고서 sync 오류: %s", rres["errors"])


def reimb_data_sync_job():
    """매일 02:00 Seoul + 부팅 1회 — 헤르메스(또는 사람)가 git 에 커밋한
    위원회 **데이터**(committee_results.json) + **보고서**(reports_manifest.json) 를
    가져와 프로덕션 DB 에 멱등 적재. **재배포·재시딩 불필요**.

    데이터/보고서는 독립 실행 (한쪽 변경 없음·실패가 다른쪽을 막지 않음).
    소스: REIMB_DATA_URL / REPORTS_DATA_URL (git raw). 미설정 시 이미지 로컬 폴백.
    """
    logger.info("━━━ Reimbursement 데이터·보고서 sync 시작 ━━━")
    try:
        _reimb_committee_data_sync()
    except Exception as e:
        logger.exception("Reimbursement 데이터 sync 실패: %s", e)
    try:
        _reimb_reports_sync()
    except Exception as e:
        logger.exception("Reimbursement 보고서 sync 실패: %s", e)


def nhis_negotiation_sync_job():
    """매주 월요일 02:30 Seoul — 건강보험공단 약가협상 공개자료(신규·확대) 크롤 →
    nhis_negotiations 멱등 아카이브 + amjilsim_drugs 매칭 교체(NHIS 공식 우선).

    소스(공개, 자격증명 불필요): retrieveMediList.do(신약)·retrieveMediList2.do(확대).
    - content_hash 멱등 UPSERT(삭제 금지 — 등록 후 1년만 공개되므로 영구 아카이브).
    - 매칭 시 negotiation_status/완료일을 nhis_official 로 자동 교체, 미매칭은 audit(drug_id NULL).
    배포 안전: 네트워크/파싱 실패가 전체 스케줄러를 막지 않도록 try/except 격리.
    """
    logger.info("━━━ NHIS 약가협상 공개자료 주간 sync 시작 ━━━")
    try:
        from agents.ingest import nhis_negotiation_import as imp
        res = imp.run()
        logger.info(
            "NHIS sync 완료: 수집 %d · 신규아카이브 %d · 매칭 drug %d/row %d · 미매칭 %d",
            res["fetched"], res["inserted"], res["matched_drugs"],
            res["matched_rows"], res["unmatched_count"])
    except Exception as e:
        logger.exception("NHIS 약가협상 sync 실패: %s", e)


def home_brand_related_refresh_job():
    """매주 월요일 05:00 Seoul — Home 브랜드 목록(home_brand) 활성 브랜드마다 Naver
    연관검색어(자동완성, 크레덴셜 불필요)를 조회해 신규 브랜드 후보를 제안.

    agents.naver_related.expand_home_brands() 가 후보를 필터링해
    home_brand 에 source='related', active=0 으로 추가 — Home 브랜드 트래픽 집계에는
    반영되지 않고 admin 이 /api/admin/home-brands 에서 검토 후 활성화해야 반영된다.
    배포 안전: 네트워크/파싱 실패가 전체 스케줄러를 막지 않도록 try/except 격리.
    """
    logger.info("━━━ Home 브랜드 연관검색어 확장 주간 sync 시작 ━━━")
    try:
        from agents import naver_related
        res = naver_related.expand_home_brands()
        logger.info(
            "Home 브랜드 연관검색어 확장 완료: seeds_processed=%d candidates_added=%d",
            res.get("seeds_processed", 0), res.get("candidates_added", 0),
        )
    except Exception as e:
        logger.exception("Home 브랜드 연관검색어 확장 실패: %s", e)


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
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)

    if args.run_now:
        logger.info("수동 즉시 실행 모드")
        asyncio.run(run_pipeline())
        return

    if args.domestic_catchup_now:
        logger.info("국내 약가 catch-up 즉시 실행 (누락 월 적재)")
        from agents.domestic_catchup import run_sync
        out = run_sync(load_config(), BASE_DIR, max_pages=3)
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
        try:
            _sync_policy_analysis()
        except Exception as _e:
            logger.warning("policy analysis 부팅 sync 실패: %s", _e)
        try:
            _sync_daily_mailing_runs()
        except Exception as _e:
            logger.warning("daily mailing runs 부팅 sync 실패: %s", _e)
        return

    if args.hira_fetch_now:
        logger.info("HIRA 공식 일정 즉시 fetch")
        hira_schedule_fetcher_job()
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

    # Competitor News — 매주 월요일 03:30 Seoul (Tier 1 전문지 6개월 크롤 + 1년 보존)
    scheduler.add_job(
        competitor_news_weekly_job,
        trigger=CronTrigger(
            day_of_week="mon",
            hour=3,
            minute=30,
            timezone="Asia/Seoul",
        ),
        id="competitor_news_weekly",
        name="경쟁사 뉴스 주간 크롤 (Tier 1, 13 브랜드)",
        replace_existing=True,
    )

    # Competitor/Gov News — 매일 06:30 Seoul 소규모 fetch (신선분, promote 직전)
    scheduler.add_job(
        competitor_news_daily_job,
        trigger=CronTrigger(hour=6, minute=30, timezone="Asia/Seoul"),
        id="competitor_news_daily",
        name="Competitor/Gov News 매일 06:30 fetch (신선분)",
        replace_existing=True,
    )

    # Competitor Trend 승격 — 매일 07:00 Seoul (아카이브→동향 카드, LLM badge 분류)
    scheduler.add_job(
        competitor_trend_promote_job,
        trigger=CronTrigger(hour=7, minute=0, timezone="Asia/Seoul"),
        id="competitor_trend_promote",
        name="경쟁사 동향 카드 일일 승격 (아카이브→카드)",
        replace_existing=True,
    )

    # Access Insight 신선 신호 — 매주 월요일 04:00 Seoul (S5: tier A/B/D 크롤 →
    # signal_extractor → amjilsim_media_signals 멱등 INSERT. competitor_news
    # 주간 크롤(03:30)과 같은 소스 패밀리·주간 cadence, 30분 후행)
    scheduler.add_job(
        access_insight_fresh_signal_job,
        trigger=CronTrigger(
            day_of_week="mon",
            hour=4,
            minute=0,
            timezone="Asia/Seoul",
        ),
        id="access_insight_fresh_signals",
        name="Access Insight 신선 신호 주간 크롤 (tier A/B/D → media signals)",
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

    # KEB 36개월 평균환율 갱신 — 매월 2일 04:00 Seoul (전월 말일 확정 직후)
    scheduler.add_job(
        exchange_rate_refresh_job,
        trigger=CronTrigger(
            day=2,
            hour=4,
            minute=0,
            timezone="Asia/Seoul",
        ),
        id="exchange_rate_refresh",
        name="KEB 36개월 평균환율 월간 갱신 (매월 2일 04:00)",
        replace_existing=True,
    )

    # ─── HIRA Pipeline Tracker (암질심·약평위) ────────────────────────────────

    # 매일 02:00 — Reimbursement 위원회 데이터 git→DB sync (헤르메스 커밋 자동 반영)
    scheduler.add_job(
        reimb_data_sync_job,
        trigger=CronTrigger(hour=2, minute=0, timezone="Asia/Seoul"),
        id="reimb_data_sync",
        name="Reimbursement 위원회 데이터 매일 02:00 git sync",
        replace_existing=True,
    )

    # 매일 02:10 — Policy Intelligence 큐레이션 사이드카 git sync (헤르메스 커밋 자동 반영)
    def _policy_analysis_sync_job():
        try:
            res = _sync_policy_analysis()
            logger.info("policy analysis sync: %s", res)
        except Exception as e:
            logger.warning("policy analysis sync 실패: %s", e)

    scheduler.add_job(
        _policy_analysis_sync_job,
        trigger=CronTrigger(hour=2, minute=10, timezone="Asia/Seoul"),
        id="policy_intel_analysis_sync",
        name="Policy Intelligence 큐레이션 사이드카 매일 02:10 git sync",
        replace_existing=True,
    )

    # 매일 07:30 — Daily Mailing run 번들 git sync (헤르메스 발송 후 산출물 → 칸반)
    def _daily_mailing_runs_sync_job():
        try:
            res = _sync_daily_mailing_runs()
            logger.info("daily mailing runs sync: %s", res)
        except Exception as e:
            logger.warning("daily mailing runs sync 실패: %s", e)

    scheduler.add_job(
        _daily_mailing_runs_sync_job,
        trigger=CronTrigger(hour=7, minute=30, timezone="Asia/Seoul"),
        id="daily_mailing_runs_sync",
        name="Daily Mailing run 번들 매일 07:30 git sync (칸반 반영)",
        replace_existing=True,
    )

    # 매주 월요일 02:30 — NHIS 약가협상 공개자료(신규·확대) 크롤 + 매칭 교체
    scheduler.add_job(
        nhis_negotiation_sync_job,
        trigger=CronTrigger(day_of_week="mon", hour=2, minute=30, timezone="Asia/Seoul"),
        id="nhis_negotiation_sync",
        name="NHIS 약가협상 공개자료 주간 sync (신규/확대 → 매칭 교체)",
        replace_existing=True,
    )

    # 매주 월요일 05:00 — Home 브랜드 목록 Naver 연관검색어 확장(신규 후보 제안, admin 검토용)
    scheduler.add_job(
        home_brand_related_refresh_job,
        trigger=CronTrigger(day_of_week="mon", hour=5, minute=0, timezone="Asia/Seoul"),
        id="home_brand_related_refresh",
        name="Home 브랜드 Naver 연관검색어 확장 (주간, 후보 제안 → admin 승인 대기)",
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
