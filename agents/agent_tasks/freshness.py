"""도메인 신선도 판정기 (P1.5 Task 3).

잡이 '성공' 해도 데이터가 안 늘어나는 경우(폴백·빈 결과·수동 업로드 누락·잡 미등록)를 잡는다. 도메인마다
관측값(마지막 데이터 시점)과 기한(오늘 기준 언제까지는 있어야 하는가)을 계산해 stale 를 판정한다.
하루 1회 잡(scheduler.domain_freshness_job) + CLI(--freshness-now). 결과는 domain_freshness 테이블(도메인당 1행)에
기록하고 stale 만 원장 과제로 만든다.

중복 억제: source_ref = "{domain}:{YYYY-MM}" — 같은 달 안에서는 과제 하나(blocked 포함). 사람이 닫았는데 다음 날에도
stale 이면 다시 연다(고친 게 아니었다는 뜻). 새 달이 되면 다시 연다(월 상한 14건).

판정 불가(테이블·컬럼·파일 부재, 권한)는 과제가 아니다 — evidence 를 "판정 불가: ..." 로 남기고 stale=False.
데이터 DB 는 읽기만 한다(SELECT) — `file:...?mode=ro` URI 도 받으므로 로컬 실측은 실제 DB 를 읽기 전용으로 연다.
판정 결과·과제는 원장 DB(jobs.resolve_db_path(); 프로덕션에서는 같은 drug_prices.db)에 쓴다.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .jobs import resolve_db_path
from .signals import DEDUP_STATUSES, runbook_line
from .store import DEFAULT_DB_PATH, connect, create_task

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parents[2]
UNDETERMINABLE = "판정 불가"

XNATIONAL_MAX_DAYS = 100
SCHEDULE_HORIZON_DAYS = 60
RESULTS_LAG_DAYS = 14
ANALOG_MAX_DAYS = 75
MARKET_SHARE_LAG_DAYS = 90
POLICY_INGEST_MAX_DAYS = 21
POLICY_ANALYSIS_MAX_DAYS = 3
NEWS_MAX_DAYS = 3
TRENDS_MAX_DAYS = 7
SIGNALS_MAX_DAYS = 10
NHIS_MAX_DAYS = 10

DOMAIN_FRESHNESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS domain_freshness (
    domain     TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    stale      INTEGER NOT NULL,
    observed   TEXT,
    deadline   TEXT,
    evidence   TEXT,
    runbook    TEXT
);
"""


@dataclass(frozen=True)
class Freshness:
    domain: str
    stale: bool
    observed: str | None      # 마지막 데이터 시점(원본 표기 그대로)
    deadline: str             # 이 값 이상이어야 신선
    evidence: str             # 사람용 한 줄
    runbook: str | None = None
    priority: int = 2

    @property
    def determinable(self) -> bool:
        return not self.evidence.startswith(UNDETERMINABLE)


@dataclass(frozen=True)
class Ctx:
    db_path: Path | str       # 데이터 DB(읽기 전용 사용). "file:...?mode=ro" URI 허용
    base_dir: Path
    today: date
    policy_root: Path | None = None   # None 이면 agents.policy_analysis.default_root()


def today_kst() -> date:
    return datetime.now(KST).date()


def _open(db_path: Path | str) -> sqlite3.Connection:
    p = str(db_path)
    return sqlite3.connect(p, uri=p.startswith("file:"))


def _scalar(ctx: Ctx, sql: str, params: tuple = ()):
    conn = _open(ctx.db_path)
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _rows(ctx: Ctx, sql: str, params: tuple = ()) -> list[tuple]:
    conn = _open(ctx.db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _days_ago(ctx: Ctx, days: int) -> str:
    return (ctx.today - timedelta(days=days)).isoformat()


def _max_ts(ctx: Ctx, domain: str, sql: str, max_days: int, runbook: str | None, what: str, priority: int = 2) -> Freshness:
    observed = _scalar(ctx, sql)
    deadline = _days_ago(ctx, max_days)
    stale = (str(observed or "")[:10]) < deadline
    return Freshness(domain, stale, observed, deadline, f"{what}={observed}, 기대≥{deadline} ({max_days}일)", runbook, priority)


# ── 1 국내약가 ─────────────────────────────────────────────────────────────

def _first_of(y: int, m: int) -> str:
    return f"{y:04d}.{m:02d}.01"


def check_domestic_price(ctx: Ctx) -> Freshness:
    observed = _scalar(ctx, "SELECT MAX(apply_date) FROM drug_prices")
    t = ctx.today
    if t.day >= 10:
        deadline = _first_of(t.year, t.month)
    else:
        deadline = _first_of(t.year if t.month > 1 else t.year - 1, t.month - 1 if t.month > 1 else 12)
    return Freshness("domestic_price", (observed or "") < deadline, observed, deadline,
                     f"drug_prices MAX(apply_date)={observed}, 기대≥{deadline} (HIRA 월초 고시, 1~7일 캐치업)",
                     "domestic_price_catchup")


# ── 2 환율 캐시 · 3 FX 정합 ────────────────────────────────────────────────

def _latest_fx_window_to(ctx: Ctx) -> str | None:
    files = sorted((ctx.base_dir / "data" / "foreign" / "exchange_rate").glob("keb_avg_rate_*.xlsx"))
    if not files:
        return None
    m = re.search(r"keb_avg_rate_(\d{8})_(\d{8})", files[-1].stem)
    return m.group(2) if m else None


def _last_day_of_prev_month(d: date) -> date:
    return d.replace(day=1) - timedelta(days=1)


def check_exchange_rate_cache(ctx: Ctx) -> Freshness:
    to = _latest_fx_window_to(ctx)
    ref = ctx.today if ctx.today.day >= 5 else _last_day_of_prev_month(ctx.today)
    deadline = _last_day_of_prev_month(ref).strftime("%Y%m%d")
    return Freshness("exchange_rate_cache", (to or "") < deadline, to, deadline,
                     f"KEB 캐시 창 종료일={to}, 기대≥{deadline} (매월 2일 갱신 잡)", "exchange_rate_refresh")


def check_foreign_price_fx_alignment(ctx: Ctx) -> Freshness:
    to = _latest_fx_window_to(ctx)
    if not to:
        return Freshness("foreign_price_fx_alignment", False, None, "0", "환율 캐시 없음 — exchange_rate_cache 가 먼저", None, 3)
    n = _scalar(ctx, """
        SELECT COUNT(*) FROM foreign_drug_prices f
        WHERE f.local_price IS NOT NULL AND f.exchange_rate_to IS NOT NULL AND f.exchange_rate_to != ?
          AND f.id = (SELECT MAX(g.id) FROM foreign_drug_prices g
                      WHERE g.query_name = f.query_name AND g.country = f.country AND g.local_price IS NOT NULL)""", (to,))
    return Freshness("foreign_price_fx_alignment", int(n or 0) > 0, str(n), "0",
                     f"최신 해외약가 행 중 캐시 환율창({to})과 다른 행 {n}건 — 환율 갱신 후 재계산 누락", "exchange_rate_recompute", 3)


# ── 4 해외 허가 커버리지 ───────────────────────────────────────────────────

def check_foreign_approval_coverage(ctx: Ctx) -> Freshness:
    from agents.foreign_approval.agent import coverage_gaps
    gaps = coverage_gaps(ctx.db_path)
    return Freshness("foreign_approval_coverage", bool(gaps), str(len(gaps)), "0",
                     f"가격은 있으나 허가 master 없는 제품 {len(gaps)}건: {gaps[:10]}", "foreign_approval_sync")


# ── 5 해외 급여 ────────────────────────────────────────────────────────────

def check_reimbursement_xnational(ctx: Ctx) -> Freshness:
    r = _max_ts(ctx, "reimbursement_xnational", "SELECT MAX(fetched_at) FROM reimbursement_xnational",
                XNATIONAL_MAX_DAYS, "reimbursement_xnational_sync", "MAX(fetched_at)")
    au = _scalar(ctx, "SELECT COUNT(*) FROM reimbursement_xnational WHERE country='AU'")
    return Freshness(r.domain, r.stale, r.observed, r.deadline, f"{r.evidence}; AU/PBAC 행 {au}건", r.runbook)


# ── 6 위원회 일정 · 7 위원회 결과 ──────────────────────────────────────────

COMMITTEES = ("AMJILSIM", "YAKPYUNGWI")


def check_committee_schedule(ctx: Ctx) -> Freshness:
    horizon = (ctx.today + timedelta(days=SCHEDULE_HORIZON_DAYS)).isoformat()
    counts = {k: 0 for k in COMMITTEES}
    for committee, n in _rows(ctx, "SELECT committee_type, COUNT(*) FROM amjilsim_sessions WHERE status='SCHEDULED' "
                                   "AND session_date BETWEEN ? AND ? GROUP BY committee_type",
                              (ctx.today.isoformat(), horizon)):
        counts[committee] = n
    missing = [k for k in COMMITTEES if counts[k] == 0]
    last = _scalar(ctx, "SELECT MAX(session_date) FROM amjilsim_sessions WHERE status='SCHEDULED'")
    return Freshness("committee_schedule", bool(missing), last, horizon,
                     f"향후 {SCHEDULE_HORIZON_DAYS}일 SCHEDULED 세션 {counts} — 0건 위원회 {missing}. "
                     "HIRA 일정 fetcher 는 placeholder(미구현) → 사람이 amjilsim_sessions 에 차수 입력", None)


def check_committee_results(ctx: Ctx) -> Freshness:
    cutoff = _days_ago(ctx, RESULTS_LAG_DAYS)
    n = _scalar(ctx, "SELECT COUNT(*) FROM amjilsim_sessions WHERE status='SCHEDULED' AND session_date < ?", (cutoff,))
    oldest = _scalar(ctx, "SELECT MIN(session_date) FROM amjilsim_sessions WHERE status='SCHEDULED' AND session_date < ?", (cutoff,))
    return Freshness("committee_results", int(n or 0) > 0, oldest, cutoff,
                     f"{RESULTS_LAG_DAYS}일 넘게 지난 세션이 SCHEDULED 로 남음 {n}건(최초 {oldest}) — 위원회 결과(committee_results.json) 미반영",
                     "reimb_data_sync")


# ── 8 아날로그 · 9 마켓셰어 ────────────────────────────────────────────────

def check_analog_reports(ctx: Ctx) -> Freshness:
    r = _max_ts(ctx, "analog_reports", "SELECT MAX(session_date) FROM analog_reports", ANALOG_MAX_DAYS, None,
                "analog_reports MAX(session_date)", 3)
    return Freshness(r.domain, r.stale, r.observed, r.deadline,
                     r.evidence + " — 약평위 결과 PDF 는 Mac 에서 apply_analog_sync 로 수동 반영", None, 3)


def expected_quarter(today: date) -> str:
    """오늘 기준 MARKET_SHARE_LAG_DAYS 일 전 시점에 이미 끝나 있던 마지막 분기 (예: 2026-09-30 → 2026Q2)."""
    ref = today - timedelta(days=MARKET_SHARE_LAG_DAYS)
    q = (ref.month - 1) // 3          # ref 가 속한 분기 전에 끝난 분기 수
    return f"{ref.year - 1}Q4" if q == 0 else f"{ref.year}Q{q}"


def check_market_share(ctx: Ctx) -> Freshness:
    # market_share_upload_log 에는 quarter 컬럼이 없다(quarters_json) — 분기 사실은 market_share_quarterly 가 들고 있다
    last = _scalar(ctx, "SELECT MAX(quarter) FROM market_share_quarterly")
    exp = expected_quarter(ctx.today)
    return Freshness("market_share", (last or "") < exp, last, exp,
                     f"market_share_quarterly MAX(quarter)={last}, 기대≥{exp} (분기 종료 +{MARKET_SHARE_LAG_DAYS}일) — admin 엑셀 업로드", None, 3)


# ── 10 정책 인텔리전스 ─────────────────────────────────────────────────────

def _policy_root(ctx: Ctx) -> Path:
    if ctx.policy_root is not None:
        return Path(ctx.policy_root)
    from agents.policy_analysis import default_root     # POLICY_INTELLIGENCE_ROOT → /app/data → /opt/data
    return default_root()


def check_policy_intelligence(ctx: Ctx) -> Freshness:
    root = _policy_root(ctx)
    status = root / "manifests" / "latest_ingest_status.json"
    ran_at = None
    if status.exists():
        try:
            ran_at = json.loads(status.read_text(encoding="utf-8")).get("ran_at")
        except Exception:
            ran_at = None
    ingest_deadline = _days_ago(ctx, POLICY_INGEST_MAX_DAYS)
    ingest_stale = (ran_at or "")[:10] < ingest_deadline
    manifest = root / "analysis" / "analysis_manifest.json"
    a_date = datetime.fromtimestamp(manifest.stat().st_mtime).date().isoformat() if manifest.exists() else None
    analysis_deadline = _days_ago(ctx, POLICY_ANALYSIS_MAX_DAYS)
    analysis_stale = (a_date or "") < analysis_deadline
    evidence = (f"ingest ran_at={ran_at} (기대≥{ingest_deadline}); analysis manifest={a_date} (기대≥{analysis_deadline}); root={root}"
                + ("" if status.exists() else " — latest_ingest_status.json 없음: /api/policy-intelligence/overview 500 (agents/policy_intelligence.py:145-146)"))
    return Freshness("policy_intelligence", ingest_stale or analysis_stale, ran_at,
                     ingest_deadline if ingest_stale else analysis_deadline, evidence,
                     None if ingest_stale else "policy_intel_analysis_sync")


# ── 11~14 뉴스·동향·신호·NHIS ──────────────────────────────────────────────

def check_news_archive(ctx: Ctx) -> Freshness:
    return _max_ts(ctx, "news_archive",
                   "SELECT MAX(fetched_at) FROM competitor_news WHERE kind IN ('competitor','gov_policy')",
                   NEWS_MAX_DAYS, "news_daily_fetch", "competitor_news MAX(fetched_at)")


def check_competitor_trends(ctx: Ctx) -> Freshness:
    return _max_ts(ctx, "competitor_trends",
                   "SELECT MAX(updated_at) FROM competitor_trend WHERE source_type='promoted'",
                   TRENDS_MAX_DAYS, "competitor_trend_promote", "competitor_trend(promoted) MAX(updated_at)", 3)


def check_access_insight_signals(ctx: Ctx) -> Freshness:
    return _max_ts(ctx, "access_insight_signals", "SELECT MAX(crawled_at) FROM amjilsim_media_signals",
                   SIGNALS_MAX_DAYS, "access_insight_fresh_signals", "amjilsim_media_signals MAX(crawled_at)", 3)


def check_nhis_negotiations(ctx: Ctx) -> Freshness:
    return _max_ts(ctx, "nhis_negotiations", "SELECT MAX(fetched_at) FROM nhis_negotiations",
                   NHIS_MAX_DAYS, "nhis_negotiation_sync", "nhis_negotiations MAX(fetched_at)", 3)


DOMAIN_CHECKS: dict[str, Callable[[Ctx], Freshness]] = {
    "domestic_price": check_domestic_price,
    "exchange_rate_cache": check_exchange_rate_cache,
    "foreign_price_fx_alignment": check_foreign_price_fx_alignment,
    "foreign_approval_coverage": check_foreign_approval_coverage,
    "reimbursement_xnational": check_reimbursement_xnational,
    "committee_schedule": check_committee_schedule,
    "committee_results": check_committee_results,
    "analog_reports": check_analog_reports,
    "market_share": check_market_share,
    "policy_intelligence": check_policy_intelligence,
    "news_archive": check_news_archive,
    "competitor_trends": check_competitor_trends,
    "access_insight_signals": check_access_insight_signals,
    "nhis_negotiations": check_nhis_negotiations,
}


def run_all(ctx: Ctx) -> list[Freshness]:
    out: list[Freshness] = []
    for domain, fn in DOMAIN_CHECKS.items():
        try:
            out.append(fn(ctx))
        except Exception as exc:   # 테이블·컬럼 부재, 파일 권한 등 — 판정 불가는 과제가 아니다(로그로만)
            logger.warning("[freshness] %s 판정 불가: %s", domain, exc)
            out.append(Freshness(domain, False, None, "", f"{UNDETERMINABLE}: {type(exc).__name__}: {exc}"[:300]))
    return out


def format_table(results: list[Freshness]) -> str:
    """CLI 용 한 줄/도메인. STALE · ok · n/a(판정 불가)."""
    lines = []
    for r in results:
        tag = "STALE" if r.stale else ("ok   " if r.determinable else "n/a  ")
        lines.append(f"{tag} {r.domain:28s} 관측={r.observed} 기한={r.deadline} — {r.evidence}")
    return "\n".join(lines)


def write_domain_freshness(results: list[Freshness], *, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(DOMAIN_FRESHNESS_SCHEMA)
        now = datetime.now(timezone.utc).isoformat()
        for r in results:
            conn.execute("INSERT INTO domain_freshness (domain, checked_at, stale, observed, deadline, evidence, runbook) "
                         "VALUES (?,?,?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET checked_at=excluded.checked_at, "
                         "stale=excluded.stale, observed=excluded.observed, deadline=excluded.deadline, "
                         "evidence=excluded.evidence, runbook=excluded.runbook",
                         (r.domain, now, 1 if r.stale else 0, r.observed, r.deadline, r.evidence, r.runbook))
        conn.commit()
    finally:
        conn.close()


def record_freshness_problems(results: list[Freshness], *, today: date,
                              db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    """stale 도메인마다 과제 id (이미 열려/blocked 면 그 id). 판정 불가·신선은 건너뛴다."""
    ids: list[int] = []
    for r in results:
        if not r.stale:
            continue
        ids.append(create_task(
            kind="ops_incident", source="domain_freshness", source_ref=f"{r.domain}:{today:%Y-%m}",
            title=f"데이터 신선도 — {r.domain} stale",
            detail=f"{r.evidence}\n관측={r.observed} 기한={r.deadline}\n{runbook_line(r.runbook)}",
            priority=r.priority, actor="freshness", dedup_statuses=DEDUP_STATUSES, db_path=db_path))
    return ids


def _count_freshness_tasks(db_path: str | Path) -> int:
    conn = connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM agent_task WHERE source='domain_freshness'").fetchone()[0])
    finally:
        conn.close()


def run_and_record(*, db_path: str | Path | None = None, data_db_path: str | Path | None = None,
                   base_dir: Path = REPO_ROOT, today: date | None = None) -> dict:
    """잡·CLI 진입점. stale 도메인은 과제이지 잡 실패가 아니다(ok=True) — 아니면 domain_freshness:{날짜} 과제가 하나 더 생긴다.

    db_path      — 원장 DB(과제 + domain_freshness 기록). 기본 jobs.resolve_db_path().
    data_db_path — 판정할 데이터 DB. 기본은 원장과 같은 파일(프로덕션 drug_prices.db). 로컬 실측은
                   `file:/.../drug_prices.db?mode=ro` 로 실제 DB 를 읽기 전용으로 연다.
    counts.tasks 는 stale 과제 id 수(기존 포함), counts.tasks_new 는 이번 실행이 새로 만든 수 — 두 번째 실행은 0 이어야 한다.
    """
    ledger = Path(db_path) if db_path else resolve_db_path()
    ctx = Ctx(db_path=data_db_path if data_db_path else ledger, base_dir=Path(base_dir), today=today or today_kst())
    results = run_all(ctx)
    write_domain_freshness(results, db_path=ledger)
    before = _count_freshness_tasks(ledger)
    ids = record_freshness_problems(results, today=ctx.today, db_path=ledger)
    return {"ok": True,
            "counts": {"checked": len(results), "stale": sum(1 for r in results if r.stale),
                       "undeterminable": sum(1 for r in results if not r.determinable),
                       "tasks": len(ids), "tasks_new": _count_freshness_tasks(ledger) - before},
            "stale_domains": [r.domain for r in results if r.stale],
            "results": [asdict(r) for r in results]}
