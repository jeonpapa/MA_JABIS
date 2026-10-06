"""도메인 신선도 판정기 (P1.5 Task 3).

잡이 '성공' 해도 데이터가 안 늘어나는 경우(폴백·빈 결과·수동 업로드 누락·잡 미등록)를 잡는다. 도메인마다
관측값(마지막 데이터 시점)과 기한(오늘 기준 언제까지는 있어야 하는가)을 계산해 stale 를 판정한다.
하루 1회 잡(scheduler.domain_freshness_job) + CLI(--freshness-now). 결과는 domain_freshness 테이블(도메인당 1행)에
기록하고 stale 만 원장 과제로 만든다.

중복 억제: source_ref = "{domain}:{YYYY-MM}" — 같은 달 안에서는 과제 하나(blocked 포함). 사람이나 디스패처가 닫았는데 다음 날에도
stale 이면 **같은 행을 다시 연다**(signals.record_recurring → store.reopen_task; attempts 보존, 한도 3회면 blocked 로 사람 확인).
새 행을 만들지 않는 이유(P1.5 fix B): 분리 런북은 '접수' 만으로 ok 라 디스패처가 매일 closed 로 닫고, 새 행은 attempts=0 이라
재시도 한도가 영원히 안 걸렸다. 월 키는 유지 — 새 달엔 새 행(attempts 0)으로 월 단위 재시도 예산 + 도메인당 월 1행 상한.

판정 불가(테이블·컬럼·파일 부재, 권한)는 과제가 아니다 — evidence 를 "판정 불가: ..." 로 남기고 stale=False.
데이터 DB 는 읽기만 한다(SELECT) — `file:...?mode=ro` URI 도 받으므로 로컬 실측은 실제 DB 를 읽기 전용으로 연다.
판정 결과·과제는 원장 DB(jobs.resolve_db_path(); 프로덕션에서는 같은 drug_prices.db)에 쓴다.

과제 종류(P1.6, 예산 ≤3건/일 설계 제약) — 판정마다 kind 가 있다:
  - ops_incident(기본): 장애/회귀. 위의 월 키 — 런북이 있으면 디스패처가 선점, 없으면 blocked 로 사람 확인.
  - improvement: 사람 몫 적체(체크리스트 입력·브랜드 승인·NHIS 수동 매칭·보고서 작성). source_ref "{domain}:backlog" —
    월 키 없음, 도메인당 1행이 끝날 때까지 열려 있고 디스패처(CLAIMABLE_KINDS=ops_incident)는 집지 않는다. closed 뒤 재발이면
    같은 행을 다시 열고(끝난 게 아니었다), rejected 는 '안 한다' 는 결정으로 존중한다(recurred ≤1/일만).
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

from agents.exchange_rate import _calc_date_range      # KEB 36개월 창 규칙의 단일 소스 — 복사하지 않는다

from .jobs import resolve_db_path
from .signals import record_recurring, runbook_line
from .store import DEFAULT_DB_PATH, connect

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
FOREIGN_PRICE_MAX_DAYS = 45          # 추적 제품마다 '가격 있는' 최신 행이 이보다 오래되면 stale (P1.6 관측)
FOREIGN_COVERAGE_WINDOW_DAYS = 90    # 이 창 안에 가격 행이 없는 국가는 (제도상 공백이 아니면) 수집 실패
PIPELINE_EXPECTED_LAG_DAYS = 14      # expected_session_id 가 이보다 오래 지난 차수를 가리키면(협상 미종결) 보드가 '예상일 경과' 를 그린다

KINDS = ("ops_incident", "improvement")
IMPROVEMENT_REF_SUFFIX = "backlog"   # improvement 의 source_ref "{domain}:backlog" — 월 키 없음(도메인당 1행)

DOMAIN_FRESHNESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS domain_freshness (
    domain     TEXT PRIMARY KEY,
    checked_at TEXT NOT NULL,
    stale      INTEGER NOT NULL,
    observed   TEXT,
    deadline   TEXT,
    evidence   TEXT,
    runbook    TEXT,
    kind       TEXT NOT NULL DEFAULT 'ops_incident'
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
    kind: str = "ops_incident"   # KINDS — improvement 는 사람 몫 적체(도메인당 1행, 디스패처 미선점)

    @property
    def determinable(self) -> bool:
        return not self.evidence.startswith(UNDETERMINABLE)


@dataclass(frozen=True)
class Ctx:
    db_path: Path | str       # 데이터 DB(읽기 전용 사용). "file:...?mode=ro" URI 허용
    base_dir: Path
    today: date
    policy_root: Path | None = None   # None 이면 agents.policy_analysis.default_root()
    ledger_path: Path | str | None = None   # 원장(scheduler_job_run — 잡 자체의 실패 신호). None 이면 db_path 와 같은 파일


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


def latest_job_run(ctx: Ctx, job_id: str) -> dict | None:
    """원장의 scheduler_job_run 에서 잡의 최근 실행 1건 {ok, errors, finished_at}. 테이블·기록이 없으면 None(실패 신호 없음)."""
    conn = _open(ctx.ledger_path if ctx.ledger_path else ctx.db_path)
    try:
        row = conn.execute("SELECT ok, errors, finished_at FROM scheduler_job_run WHERE job_id=? ORDER BY id DESC LIMIT 1",
                           (job_id,)).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not row:
        return None
    try:
        errors = json.loads(row[1] or "[]")
    except Exception:
        errors = [str(row[1])]
    return {"ok": bool(row[0]), "errors": [str(e) for e in errors], "finished_at": row[2]}


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


def expected_fx_window_to(today: date) -> str:
    """오늘 KEB 캐시(와 모든 조정가 행)가 가져야 할 36개월 창의 종료일(YYYYMMDD).

    규칙은 agents/exchange_rate._calc_date_range(전월 말일)를 그대로 쓴다. 매월 2일 갱신 잡의 유예로 4일까지는 전전월 말일."""
    ref = today if today.day >= 5 else _last_day_of_prev_month(today)
    return _calc_date_range(ref)[1]


def check_exchange_rate_cache(ctx: Ctx) -> Freshness:
    to = _latest_fx_window_to(ctx)
    deadline = expected_fx_window_to(ctx.today)
    return Freshness("exchange_rate_cache", (to or "") < deadline, to, deadline,
                     f"KEB 캐시 창 종료일={to}, 기대≥{deadline} (매월 2일 갱신 잡)", "exchange_rate_refresh")


def check_foreign_price_fx_alignment(ctx: Ctx) -> Freshness:
    """최신 가격 행의 exchange_rate_to 를 **오늘 있어야 할 창**과 비교한다 — 캐시 파일의 창이 아니다.

    캐시 파일 기준이면 행과 캐시가 함께 구창일 때(감사가 본 상태: 둘 다 20260531) ok 로 보인다. 캐시도 구창이면 런북은
    갱신(exchange_rate_refresh — 갱신 잡이 recompute 까지 한다), 캐시가 최신인데 행만 구창이면 재계산(exchange_rate_recompute)."""
    expected = expected_fx_window_to(ctx.today)
    cache_to = _latest_fx_window_to(ctx)
    n = _scalar(ctx, """
        SELECT COUNT(*) FROM foreign_drug_prices f
        WHERE f.local_price IS NOT NULL AND f.exchange_rate_to IS NOT NULL AND f.exchange_rate_to != ?
          AND f.id = (SELECT MAX(g.id) FROM foreign_drug_prices g
                      WHERE g.query_name = f.query_name AND g.country = f.country AND g.local_price IS NOT NULL)""", (expected,))
    cache_stale = (cache_to or "") < expected
    why = ("캐시도 구창 — 환율 갱신 뒤 재계산" if cache_stale else "캐시는 최신 — 환율 갱신 후 재계산 누락")
    return Freshness("foreign_price_fx_alignment", int(n or 0) > 0, str(n), "0",
                     f"최신 해외약가 행 중 기대 환율창({expected})과 다른 행 {n}건 (KEB 캐시 창={cache_to}; {why})",
                     "exchange_rate_refresh" if cache_stale else "exchange_rate_recompute", 3)


# ── 3b 해외약가 가격 나이 (P1.6 관측) ──────────────────────────────────────
# 질문은 하나다: "현 시점에 해당 약제의 가격을 가지고 올 수 있느냐". 잡 성공·세션 수가 아니라 **가격이 있는 행(local_price
# NOT NULL)의 나이**를 제품마다 본다. 수집은 사용자 검색으로만 일어나므로 런북이 없다 — evidence 가 사람이 할 일을 적는다.

FOREIGN_PRICE_GUIDANCE = ("조치: 수집은 사용자 검색으로만 일어난다 — 해외약가 화면에서 오래된 제품을 재검색(전체 국가)하고, "
                          "국가 카드가 '수집 실패(로그인·의존성·403·시간 초과)' 면 원장 foreign_search 과제와 "
                          "foreign_config_probe 결과(config/.env 키·scrapling·프록시)를 본다")


def _tracked_products(ctx: Ctx) -> dict[str, set[str]]:
    """대쉬보드가 추적하는 제품(indications_master.product) → 그 제품의 가격 행이 가질 수 있는 query_name 집합(소문자).

    product_alias_map(slug·inn·brand_aliases)로 확장해 keytruda ↔ pembrolizumab 을 한 제품으로 본다. 제품이 alias 맵의
    inn 으로 적혀 있어도(human_papillomavirus_vaccine) canonical slug(gardasil)로 합친다. alias 테이블이 없으면 slug 만."""
    products = {(r[0] or "").strip().lower() for r in _rows(ctx, "SELECT DISTINCT product FROM indications_master") if r[0]}
    canon_of: dict[str, str] = {}
    names_of: dict[str, set[str]] = {}
    try:
        alias_rows = _rows(ctx, "SELECT product_slug, inn, brand_aliases_json FROM product_alias_map")
    except sqlite3.OperationalError:
        alias_rows = []
    for slug, inn, ba_json in alias_rows:
        slug = (slug or "").strip().lower()
        if not slug:
            continue
        names = {slug}
        if inn:
            names.add(str(inn).strip().lower())
        try:
            names |= {str(a).strip().lower() for a in json.loads(ba_json or "[]") if str(a).strip()}
        except Exception:
            pass
        names_of[slug] = names
        for n in names:
            canon_of.setdefault(n, slug)
    out: dict[str, set[str]] = {}
    for p in products:
        canon = canon_of.get(p, p)
        out.setdefault(canon, set()).update(names_of.get(canon, set()) | {canon, p})
    return out


def _last_priced_by_query_name(ctx: Ctx) -> dict[str, str]:
    """query_name(소문자) → 가격 있는 행의 MAX(searched_at)."""
    return {n: ts for n, ts in _rows(ctx, "SELECT LOWER(TRIM(query_name)), MAX(searched_at) FROM foreign_drug_prices "
                                        "WHERE local_price IS NOT NULL AND query_name IS NOT NULL GROUP BY 1") if n}


def check_foreign_price_age(ctx: Ctx) -> Freshness:
    tracked = _tracked_products(ctx)
    deadline = _days_ago(ctx, FOREIGN_PRICE_MAX_DAYS)
    if not tracked:
        return Freshness("foreign_price_age", False, None, deadline, "추적 제품 없음(indications_master 비어 있음)", None)
    last_by_name = _last_priced_by_query_name(ctx)
    last: dict[str, str | None] = {}
    for slug, names in sorted(tracked.items()):
        dates = [last_by_name[n] for n in names if n in last_by_name]
        last[slug] = max(dates)[:10] if dates else None
    old = [s for s, d in last.items() if d and d < deadline]
    never = [s for s, d in last.items() if d is None]
    observed = min((d for d in last.values() if d), default=None)
    listing = ", ".join(f"{s}={d or '미수집'}" for s, d in last.items())
    evidence = (f"제품별 가격 있는 최신 행: {listing}; 기대≥{deadline} ({FOREIGN_PRICE_MAX_DAYS}일) — "
                f"오래됨 {len(old)}건 {old}, 미수집 {len(never)}건 {never}. {FOREIGN_PRICE_GUIDANCE}")
    return Freshness("foreign_price_age", bool(old or never), observed, deadline, evidence, None, 2)


# ── 3c 해외약가 국가 커버리지 (P1.6 관측) ──────────────────────────────────
# 구현된 국가(AVAILABLE_COUNTRIES) 중 최근 창 안에 가격 행이 없는 국가를 '제도상 공백' 과 '수집 실패' 로 가른다.
# 그 기준은 agents/scrapers/country_coverage.py:is_structural_absence 한 곳이다 — 추적 제품 전부가 제도상 공백인 국가만
# 공백(CA: EAP 에 추적 제품 없음, FR: 병원 전용 항암제만 추적할 때). 구독·로그인 국가(US/UK/DE)가 비면 수집 실패다.

def foreign_country_coverage_detail(ctx: Ctx) -> dict:
    from agents.foreign_price_agent import AVAILABLE_COUNTRIES          # lazy — 스크레이퍼·playwright import
    from agents.scrapers.country_coverage import is_structural_absence
    tracked = sorted(_tracked_products(ctx))
    since = _days_ago(ctx, FOREIGN_COVERAGE_WINDOW_DAYS)
    last = {cc: (ts or "")[:10] for cc, ts in _rows(ctx, "SELECT country, MAX(searched_at) FROM foreign_drug_prices "
                                                        "WHERE local_price IS NOT NULL GROUP BY country") if cc}
    recent = sorted(cc for cc in AVAILABLE_COUNTRIES if last.get(cc, "") >= since)
    structural, failed = [], []
    for cc in AVAILABLE_COUNTRIES:
        if cc in recent:
            continue
        (structural if tracked and all(is_structural_absence(cc, p) for p in tracked) else failed).append(cc)
    return {"since": since, "recent": recent, "failed": sorted(failed), "structural": sorted(structural),
            "last": last, "tracked": tracked}


def check_foreign_country_coverage(ctx: Ctx) -> Freshness:
    d = foreign_country_coverage_detail(ctx)
    failed_last = ", ".join(f"{cc}={d['last'].get(cc) or '없음'}" for cc in d["failed"])
    evidence = (f"최근 {FOREIGN_COVERAGE_WINDOW_DAYS}일(≥{d['since']}) 가격 행 있는 국가 {d['recent']}; "
                f"수집 실패(기대했으나 창 안 가격 행 0건) {d['failed']} — 마지막 가격 행: {failed_last or '-'}; "
                f"제도상 공백(과제 아님) {d['structural']} — country_coverage.is_structural_absence(추적 제품 {d['tracked']} 전부). "
                f"{FOREIGN_PRICE_GUIDANCE}")
    return Freshness("foreign_country_coverage", bool(d["failed"]), failed_last or None, d["since"], evidence, None, 2)


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


COMMITTEE_KR = {"AMJILSIM": "암질심", "YAKPYUNGWI": "약평위"}
COMMITTEE_RESULTS_JSON = Path("agents") / "ingest" / "committee_results.json"   # 회차 결과의 소스(헤르메스/사람이 git 에 커밋)


def committee_source_state(ctx: Ctx) -> dict:
    """소스 JSON 의 상태 — 마지막 반영 회차(session_status_updates 의 COMPLETED 와 missing_sessions 의 최대 session_date;
    missing_sessions 는 importer 가 COMPLETED 로 INSERT 한다), 파일 갱신일(mtime — 체크아웃/빌드 시각일 수 있어 보조 지표)."""
    p = ctx.base_dir / COMMITTEE_RESULTS_JSON
    out: dict = {"exists": p.exists(), "mtime": None, "last_completed": None}
    if not p.exists():
        return out
    out["mtime"] = datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return out
    dates = [str(u[0]) for u in (data.get("session_status_updates") or []) if len(u) >= 2 and u[1] == "COMPLETED"]
    dates += [str(m[3]) for m in (data.get("missing_sessions") or []) if len(m) >= 4]
    out["last_completed"] = max(dates) if dates else None
    return out


def _session_listing(rows: list[tuple]) -> str:
    return ", ".join(f"#{sid}={d}({COMMITTEE_KR.get(c, c)})" for sid, d, c in rows) or "-"


def check_committee_results(ctx: Ctx) -> Freshness:
    """지난 세션이 SCHEDULED 로 남아 있다 = 회차 결과 미반영. 원인은 둘이고 조치가 다르다(P1.6):
      ① reimb_data_sync 잡 자체가 실패(원격 fetch 실패→폴백 등) — 원장 scheduler_job_run 의 최근 실행이 ok=0 → 런북 유지.
      ② 잡은 정상인데 소스 JSON(agents/ingest/committee_results.json)이 안 바뀜 — 02:00 잡은 '변경 없음 skip' 이라 런북은
         무효(디스패처가 3회 '접수' 뒤 blocked, 사람은 'sync 실패' 로 오독). 런북 None + 누가 무엇을 써야 하는지 evidence 에.
    누락된 것은 **회차 결과**(session_status_updates/drugs)이지 D+1 보고서(reports_manifest.json → reimb_reports)가 아니다 —
    보고서 누락은 committee_reports 도메인이 본다."""
    cutoff = _days_ago(ctx, RESULTS_LAG_DAYS)
    overdue = _rows(ctx, "SELECT session_id, session_date, committee_type FROM amjilsim_sessions "
                         "WHERE status='SCHEDULED' AND session_date < ? ORDER BY session_date", (cutoff,))
    grace = _rows(ctx, "SELECT session_id, session_date, committee_type FROM amjilsim_sessions "
                       "WHERE status='SCHEDULED' AND session_date >= ? AND session_date < ? ORDER BY session_date",
                  (cutoff, ctx.today.isoformat()))
    oldest = overdue[0][1] if overdue else None
    head = (f"{RESULTS_LAG_DAYS}일 넘게 지난 세션이 SCHEDULED 로 남음 {len(overdue)}건(최초 {oldest}): {_session_listing(overdue)}; "
            f"유예 중({RESULTS_LAG_DAYS}일 이내, 아직 과제 아님) {len(grace)}건: {_session_listing(grace)}")
    if not overdue:
        return Freshness("committee_results", False, oldest, cutoff, head, None)
    run = latest_job_run(ctx, "reimb_data_sync")
    if run is not None and not run["ok"]:
        first = (run["errors"] or ["?"])[0][:160]
        cause = (f"원인 ①: reimb_data_sync 잡 실패 중(최근 실행 {str(run['finished_at'])[:16]}: {first}) — 소스가 반영되지 못함. "
                 "런북 재실행이 답이다")
        runbook: str | None = "reimb_data_sync"
    else:
        src = committee_source_state(ctx)
        signal = "최근 실행 ok" if run is not None else "원장에 실행 기록 없음 = 실패 신호 없음"
        cause = (f"원인 ②: reimb_data_sync 는 정상({signal}, 변경 없음 skip)이라 런북은 무효 — "
                 f"헤르메스/사람이 committee_results.json 에 회차 결과 미작성 — 마지막 반영 회차 {src['last_completed']}, "
                 f"소스 갱신일 {src['mtime'] or '파일 없음'}(mtime). 누락된 것은 회차 결과(session_status_updates/drugs)이지 "
                 "D+1 보고서(reports_manifest.json)가 아니다 — 보고서 누락은 committee_reports 도메인이 본다")
        runbook = None
    return Freshness("committee_results", True, oldest, cutoff, f"{head}. {cause}", runbook)


# ── 7a 위원회 보고서 누락 (P1.6, improvement — 담당 헤르메스) ──────────────
# 회차마다 D+1 결과 리뷰가 reports_manifest.json → reimb_reports 로 들어와야 한다(session_date 일치). 지난 60일의 지난 세션 중
# 행이 없는 것을 센다. 회차 당일·다음 날은 유예(REPORT_LAG_DAYS) — D+1 발행 → 02:00 sync 라 07:45 판정은 D+2 부터 의미가 있다.

REPORTS_WINDOW_DAYS = 60
REPORT_LAG_DAYS = 2


def check_committee_reports(ctx: Ctx) -> Freshness:
    since = _days_ago(ctx, REPORTS_WINDOW_DAYS)
    due = _days_ago(ctx, REPORT_LAG_DAYS)
    sessions = _rows(ctx, "SELECT session_id, session_date, committee_type FROM amjilsim_sessions "
                          "WHERE session_date >= ? AND session_date < ? ORDER BY session_date", (since, due))
    reports = _rows(ctx, "SELECT session_date, committee, report_type, title FROM reimb_reports "
                         "WHERE session_date >= ? AND session_date < ? ORDER BY session_date", (since, ctx.today.isoformat()))
    have = {r[0] for r in reports}
    missing = [s for s in sessions if s[1] not in have]
    latest = max((r[0] for r in reports), default=None)
    present = "; ".join(f"{d} {c}/{t} '{title}'" for d, c, t, title in reports) or "-"
    evidence = (f"최근 {REPORTS_WINDOW_DAYS}일(≥{since}) 지난 세션(D+{REPORT_LAG_DAYS} 경과) {len(sessions)}건 중 "
                f"reimb_reports 행(session_date 일치) 없음 {len(missing)}건: {_session_listing(missing)}; "
                f"창 안 보고서: {present}. 담당: 헤르메스 — D+1 결과 리뷰를 reports_manifest.json 에 발행(session_date 를 회차일에 맞춤) "
                "→ 02:00 reimb_data_sync 가 적재. 병합 보고서(combined)는 session_date 가 한 회차만 가리키므로 다른 회차는 여기 남는다")
    return Freshness("committee_reports", bool(missing), latest, since, evidence, None, 3, kind="improvement")


# ── 7b 파이프라인 경과 예상일 (P1.6) ───────────────────────────────────────
# 보드 타임라인은 expected_session_id 차수가 지났는데 결과가 없으면 '예상일 경과'(overdue) 를 그린다(agents/reimb_pipeline._timeline).
# 그 상태가 14일 넘게 이어지면 데이터가 틀린 것이다. 이 컬럼을 쓰는 코드는 agents/ingest/reimb_committee_import.run() 하나 —
# JSON 의 expected_next_session(브랜드→회차일)을 그대로 옮긴다. 잡·관리자 API 어디도 다음 차수로 옮기지 않으므로 런북
# reimb_data_sync 는 같은 JSON 을 다시 적용할 뿐(해시 동일 → '변경 없음 skip')이라 무효 → 런북 None, 사람/헤르메스가 JSON 을 고친다.

NEGOTIATION_CONCLUDED = ("AGREED", "REJECTED")      # amjilsim_drugs.negotiation_status 종결값 (reimb_pipeline._negotiation_phase_status)


def check_pipeline_expected_sessions(ctx: Ctx) -> Freshness:
    cutoff = _days_ago(ctx, PIPELINE_EXPECTED_LAG_DAYS)
    rows = _rows(ctx, f"""
        SELECT d.brand_kr, s.session_date, s.session_id, s.committee_type
        FROM amjilsim_drugs d JOIN amjilsim_sessions s ON s.session_id = d.expected_session_id
        WHERE s.session_date < ?
          AND (d.negotiation_status IS NULL OR d.negotiation_status NOT IN ({",".join("?" * len(NEGOTIATION_CONCLUDED))}))
          AND (d.negotiation_complete_date IS NULL OR d.negotiation_complete_date = '')
        ORDER BY s.session_date, d.brand_kr""", (cutoff, *NEGOTIATION_CONCLUDED))
    listing = ", ".join(f"{b}({d} {COMMITTEE_KR.get(c, c)} #{sid})" for b, d, sid, c in rows)
    oldest = rows[0][1] if rows else None
    evidence = (f"expected_session_id 가 {PIPELINE_EXPECTED_LAG_DAYS}일 넘게 지난 차수(<{cutoff})를 가리키는데 협상 미종결"
                f"(negotiation_status ∉ {list(NEGOTIATION_CONCLUDED)}, negotiation_complete_date 없음)인 약제 {len(rows)}건"
                + (f"(최초 {oldest}): {listing}. " if rows else ". ")
                + "이 컬럼을 쓰는 코드는 agents/ingest/reimb_committee_import.run() 의 expected_next_session(브랜드→회차일) 뿐 — "
                  "런북 reimb_data_sync 는 같은 JSON 을 다시 적용할 뿐(해시 동일 → skip)이라 무효. 조치: 헤르메스/사람이 "
                  "committee_results.json 의 expected_next_session 을 다음 차수로 옮기거나 결과를 drugs/events 에 기록 → 02:00 sync 가 반영")
    return Freshness("pipeline_expected_sessions", bool(rows), oldest, cutoff, evidence, None, 2)


# ── 7c 급여 체크리스트 커버리지 (P1.6, improvement — 담당 사람) ─────────────
# Home 의 Keytruda 급여 현황은 급여/미급여/미확인 3분할이다(api/server.py:msd_summary). '미확인' = indication_reimbursement 행 없음 —
# 체크리스트는 사람이 /admin/reimbursement 에서 적응증별로 입력한다(프로젝트 노트: 수동 1회 약 1시간). 미확인이 절반을 넘거나
# 마지막 편집이 90일을 넘으면 적체.

CHECKLIST_PRODUCT = "keytruda"
CHECKLIST_UNVERIFIED_MAX_SHARE = 0.5
CHECKLIST_EDIT_MAX_DAYS = 90


def check_reimbursement_checklist_coverage(ctx: Ctx) -> Freshness:
    total = int(_scalar(ctx, "SELECT COUNT(*) FROM indications_master WHERE product=?", (CHECKLIST_PRODUCT,)) or 0)
    covered = int(_scalar(ctx, "SELECT COUNT(*) FROM indications_master m JOIN indication_reimbursement r "
                               "ON r.indication_id = m.indication_id WHERE m.product=?", (CHECKLIST_PRODUCT,)) or 0)
    last_edit = str(_scalar(ctx, "SELECT MAX(updated_at) FROM indication_reimbursement") or "")[:10] or None
    deadline = _days_ago(ctx, CHECKLIST_EDIT_MAX_DAYS)
    unverified = total - covered
    share = unverified / total if total else 0.0
    share_bad = total > 0 and share > CHECKLIST_UNVERIFIED_MAX_SHARE
    edit_stale = total > 0 and (last_edit or "") < deadline
    evidence = (f"Keytruda 적응증 {total}건 중 체크리스트(indication_reimbursement) 행 없음 = 미확인 {unverified}건({share:.0%}) — "
                f"기준 ≤{CHECKLIST_UNVERIFIED_MAX_SHARE:.0%}; 마지막 편집 {last_edit}(기대≥{deadline}, {CHECKLIST_EDIT_MAX_DAYS}일). "
                "담당: 사람 — /admin/reimbursement 에서 적응증별 급여 여부·조건·공고 입력(수동 1회 약 1시간). "
                "Home 3분할(급여/미급여/미확인)의 '미확인' 이 이 수다")
    return Freshness("reimbursement_checklist_coverage", share_bad or edit_stale, last_edit, deadline, evidence, None, 3,
                     kind="improvement")


# ── 7d 사람 몫 적체 (P1.6, improvement — 한 과제로 묶음, 싸게) ──────────────
# Home 브랜드 후보(home_brand source='related'·active=0 — home_brand_related_refresh 가 만들고 사람이 /admin/home-brands 에서
# 활성화/삭제)가 14일 넘게 대기 중인 것과, NHIS 협상 공개자료 중 amjilsim_drugs 와 매칭 안 된 행(nhis_negotiations.drug_id IS NULL —
# /admin/reimbursement-pipeline 의 nhis-unmatched 에서 수동 매칭). 둘 다 사람 몫이라 도메인 하나 = 과제 하나.

HOME_BRAND_PENDING_DAYS = 14


def check_human_backlog(ctx: Ctx) -> Freshness:
    cutoff = _days_ago(ctx, HOME_BRAND_PENDING_DAYS)
    n_brand, first = _rows(ctx, "SELECT COUNT(*), MIN(created_at) FROM home_brand "
                                "WHERE source='related' AND active=0 AND COALESCE(created_at,'') < ?", (cutoff,))[0]
    n_brand = int(n_brand or 0)
    try:
        n_nhis, n_prog = _rows(ctx, "SELECT COUNT(*), SUM(CASE WHEN completed_ym IS NULL OR completed_ym='' THEN 1 ELSE 0 END) "
                                    "FROM nhis_negotiations WHERE drug_id IS NULL")[0]
        n_nhis = int(n_nhis or 0)
        nhis_txt = (f"NHIS 협상 공개자료 미매칭(drug_id NULL) {n_nhis}건(협상중 {int(n_prog or 0)}) "
                    "→ /admin/reimbursement-pipeline(nhis-unmatched 에서 수동 매칭)")
    except sqlite3.OperationalError:
        n_nhis = 0
        nhis_txt = "NHIS: nhis_negotiations 테이블 없음(판정 제외)"
    evidence = (f"Home 브랜드 후보(home_brand source=related·active=0) {HOME_BRAND_PENDING_DAYS}일 넘게 승인 대기 {n_brand}건"
                f"(최초 {str(first or '-')[:10]}) → /admin/home-brands(활성화/삭제); {nhis_txt}. 담당: 사람 — 두 적체를 과제 하나로 묶는다")
    return Freshness("human_backlog", n_brand > 0 or n_nhis > 0, f"brand={n_brand},nhis={n_nhis}", "0", evidence, None, 3,
                     kind="improvement")


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
    "foreign_price_age": check_foreign_price_age,
    "foreign_country_coverage": check_foreign_country_coverage,
    "foreign_approval_coverage": check_foreign_approval_coverage,
    "reimbursement_xnational": check_reimbursement_xnational,
    "committee_schedule": check_committee_schedule,
    "committee_results": check_committee_results,
    "committee_reports": check_committee_reports,
    "pipeline_expected_sessions": check_pipeline_expected_sessions,
    "reimbursement_checklist_coverage": check_reimbursement_checklist_coverage,
    "human_backlog": check_human_backlog,
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
        if "kind" not in {r[1] for r in conn.execute("PRAGMA table_info(domain_freshness)")}:   # P1.6 이전 테이블 — 멱등 보강
            conn.execute("ALTER TABLE domain_freshness ADD COLUMN kind TEXT NOT NULL DEFAULT 'ops_incident'")
        now = datetime.now(timezone.utc).isoformat()
        for r in results:
            conn.execute("INSERT INTO domain_freshness (domain, checked_at, stale, observed, deadline, evidence, runbook, kind) "
                         "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(domain) DO UPDATE SET checked_at=excluded.checked_at, "
                         "stale=excluded.stale, observed=excluded.observed, deadline=excluded.deadline, "
                         "evidence=excluded.evidence, runbook=excluded.runbook, kind=excluded.kind",
                         (r.domain, now, 1 if r.stale else 0, r.observed, r.deadline, r.evidence, r.runbook, r.kind))
        conn.commit()
    finally:
        conn.close()


def record_freshness_problems(results: list[Freshness], *, today: date,
                              db_path: str | Path = DEFAULT_DB_PATH) -> list[int]:
    """stale 도메인마다 과제 id. 판정 불가·신선은 건너뛴다.

    ops_incident: 같은 달의 같은 행(열려 있으면 recurred, 닫혔으면 다시 연다 — rejected 포함, 조건이 되돌아온 것이므로).
    improvement:  "{domain}:backlog" 1행 — 월이 바뀌어도 같은 행. closed 뒤 재발이면 다시 열고 rejected 는 존중한다.
    """
    ids: list[int] = []
    for r in results:
        if not r.stale:
            continue
        if r.kind not in KINDS:
            raise ValueError(f"알 수 없는 kind: {r.kind!r} ({r.domain})")
        improvement = r.kind == "improvement"
        ids.append(record_recurring(
            kind=r.kind, source="domain_freshness",
            source_ref=f"{r.domain}:{IMPROVEMENT_REF_SUFFIX}" if improvement else f"{r.domain}:{today:%Y-%m}",
            title=f"사람 몫 적체 — {r.domain}" if improvement else f"데이터 신선도 — {r.domain} stale",
            detail=f"{r.evidence}\n관측={r.observed} 기한={r.deadline}\n{runbook_line(r.runbook)}",
            priority=r.priority, actor="freshness", db_path=db_path, reopen_rejected=not improvement)[0])
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
    ctx = Ctx(db_path=data_db_path if data_db_path else ledger, base_dir=Path(base_dir), today=today or today_kst(),
              ledger_path=ledger)
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
