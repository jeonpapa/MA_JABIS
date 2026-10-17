"""플랫폼 스모크 — 화면별 API 세트를 실제 서버에 쳐서 기대 조건을 확인한다 (P1.5 Task 4).

in-process(스케줄러 잡 platform_smoke, 매일 07:50): base=http://127.0.0.1:5001, 토큰은 공유 JWT_SECRET 으로 직접 발급
  (mint_token_factory — fly secret 이 환경변수라 web·scheduler 두 프로세스가 같은 비밀을 갖는다. 스모크 계정은 없다).
외부(VPS·로컬 수동): python scripts/smoke.py --base https://ma-ai-dossier.fly.dev --login-email ... --login-password ...
  (관리자가 만든 admin 계정 — admin 은 user 라우트도 통과한다. 새 계정 생성은 사용자 몫: VPS_SETUP.md §1 과 같은 절차)
기대 조건은 '200 + JSON' 이 기본이고 화면별로 키 존재·상태코드를 덧붙인다. 데이터 나이는 검사하지 않는다(신선도는 Task 3) —
다만 합쳐진 플랫폼 수정(P1.6)이 지키는 **불변식**은 본다: 급여 3분할 합계(msd/summary), 지난 예상일은 overdue(pipeline),
지난 회차가 SCHEDULED 로 남지 않음(meetings), 미디어 집계가 비어 있지 않음(media-intelligence), 칸반 runs 키. 이것들은
200 + JSON 이어도 화면이 '조용히 틀린' 상태라서다. 실패 문구는 숫자 외 가변 텍스트(약제명·오류 원문)를 넣지 않는다 —
과제 키가 실패 집합의 숫자 무시 내용 해시라, 건수가 바뀌어도 같은 과제 1건으로 남아야 한다(signals.CONTENT_KEYED_JOBS).
반환은 잡 규약 {ok, counts, errors} — guarded_job 이 실패를 platform_smoke:<실패 집합 해시> 과제로 남긴다(같은 실패면 1건).
체크별 타임아웃: 기본 10초. 캐시 미스에 LLM·외부 수집이 끼는 Home 라우트는 Check(timeout=...) 로 넉넉히 준다 — 07:50 첫 호출
(일자 캐시 미스)이 10초를 넘겨 거짓 platform_smoke 과제가 생기지 않게(P1.5 fix F).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)
DEFAULT_BASE = "http://127.0.0.1:5001"
SMOKE_SUBJECT = "smoke@internal"                    # 토큰 sub — users DB 에 없는 가상 주체(계정 아님)
# 모든 요청에 붙인다 — 서버(api/server.py:SMOKE_HEADER)가 이 요청에서 GET 라우트의 부수효과(검색 로그 INSERT·정부요약의
# 캐시 미스 동기 크롤)만 건너뛴다. 읽기 판정은 그대로다(스모크의 목적). 값은 server 와 같아야 한다(테스트가 대조).
# 칸반 GET 은 더 이상 아무것도 purge 하지 않는다(헤더와 무관 — 만료 행 DELETE 는 09:30 daily_mailing_health_job 의 일).
SMOKE_HEADER = "X-MA-Smoke"
Expect = Callable[[int, object], "str | None"]     # (status, body) → 실패 사유 | None
TokenFor = Callable[["str | None"], "str | None"]  # role → 토큰 | None(그 역할 화면은 skipped)


def _json_ok(status: int, body) -> str | None:
    return None if status == 200 and isinstance(body, (dict, list)) else f"HTTP {status}"


def _has_key(key: str) -> Expect:
    return lambda status, body: None if status == 200 and isinstance(body, dict) and key in body else f"HTTP {status} / '{key}' 없음"


def _has_keys(*keys: str) -> Expect:
    def expect(status: int, body) -> str | None:
        if status != 200 or not isinstance(body, dict):
            return f"HTTP {status}"
        missing = [k for k in keys if k not in body]
        return f"HTTP 200 / 키 없음 {missing}" if missing else None
    return expect


# Stakeholder 권한 경계(api/stakeholder_routes.USER_HIDDEN_KEYS 와 같은 집합 — 테스트가 대조): user 응답 어디에도 원문 경로·언론
# CDN 사진 링크가 없어야 한다. 런 payload(외부 에이전트 JSON)에 local_path 가 실제로 들어 있어 상세 응답이 진짜 누출 경로다.
USER_HIDDEN_KEYS = frozenset({"photo_url", "photo_path", "local_path", "text_path"})


def _hidden_keys(obj) -> set[str]:
    if isinstance(obj, dict):
        return {k for k in obj if k in USER_HIDDEN_KEYS} | {h for v in obj.values() for h in _hidden_keys(v)}
    if isinstance(obj, list):
        return {h for v in obj for h in _hidden_keys(v)}
    return set()


def _user_safe(key: str) -> Expect:
    """200 + dict + key 존재 + 어느 깊이에도 숨김 키 없음. user 토큰 전용 — admin 은 원본 경로를 보는 게 정상이라
    외부 admin 로그인 모드에서는 auth-gates 의 다른 체크처럼 실패한다(그 화면 이름이 '권한 경계' 인 이유)."""
    def expect(status: int, body) -> str | None:
        if status != 200 or not isinstance(body, dict):
            return f"HTTP {status}"
        if key not in body:
            return f"HTTP 200 / '{key}' 없음"
        leaked = _hidden_keys(body)
        return f"사용자 응답에 숨김 키 노출 {sorted(leaked)}" if leaked else None
    return expect


def _first_monitoring_run(body) -> str | None:
    """/api/stakeholder/runs?kind=monitoring 목록 → 최신 런 상세 경로. 런이 없으면 None(상세 검사 생략 — 실패 아님)."""
    items = body.get("items") if isinstance(body, dict) else None
    run_id = items[0].get("run_id") if isinstance(items, list) and items and isinstance(items[0], dict) else None
    return f"/api/stakeholder/runs/monitoring/{urllib.parse.quote(str(run_id), safe='')}" if run_id else None


def _status(code: int) -> Expect:
    return lambda status, body: None if status == code else f"HTTP {status} (기대 {code})"


def _priced_country(status: int, body) -> str | None:
    """/api/foreign/cached — 조정가(adjusted_price_krw)가 있는 국가가 하나는 있어야 한다 (P1.6).

    '200 + JSON' 만 보면 가격 동결·US 0행 상태(감사)에서도 통과한다 — 사용자 원칙은 "현 시점에 그 약제의 가격을 가지고 올 수
    있느냐" 라 빈 results 는 화면이 깨진 것이다. X-MA-Smoke 라 라이브 수집은 없고 캐시(DB)만 본다."""
    if status != 200 or not isinstance(body, dict):
        return f"HTTP {status}"
    results = body.get("results") or {}
    priced = [cc for cc, rows in results.items() if isinstance(rows, list)
              and any(isinstance(r, dict) and r.get("adjusted_price_krw") is not None for r in rows)]
    return None if priced else "조정가(adjusted_price_krw) 있는 국가 0 — 캐시 비었거나 수집 중단"


# ── P1.6 불변식 — 합쳐진 플랫폼 수정이 되돌아오면 여기서 잡힌다 ──────────────────────────────────────────

TRISTATE_KEYS = ("total_indications", "reimbursed_indications", "not_reimbursed_indications", "unverified_reimbursement",
                 "checklist_covered")


def _msd_summary_tristate(status: int, body) -> str | None:
    """/api/msd/summary — Keytruda 급여 현황 3분할: 급여 + 미급여 + 미확인 == 전체, checklist_covered == 급여 + 미급여.
    (56939091: 체크리스트 행 없음 51건을 '미급여' 로 세던 결함 — 키가 사라지거나 합이 안 맞으면 그 회귀다.)"""
    if status != 200 or not isinstance(body, dict):
        return f"HTTP {status}"
    k = body.get("keytruda")
    if not isinstance(k, dict):
        return "HTTP 200 / 'keytruda' 없음"
    missing = [x for x in TRISTATE_KEYS if x not in k]
    if missing:
        return f"급여 3분할 키 없음 {missing}"
    total, a, b, c, covered = (int(k[x] or 0) for x in TRISTATE_KEYS)
    d = int(k.get("not_mfds_approved") or 0)   # 국내 미허가(외국 전용)는 미확인과 분리 집계
    if a + b + c + d != total:
        return f"급여{a}+미급여{b}+미확인{c}+국내미허가{d}={a + b + c + d} ≠ 전체 {total}"
    if covered != a + b:
        return f"checklist_covered {covered} ≠ 급여{a}+미급여{b}"
    return None


def _pipeline_expectations(status: int, body) -> str | None:
    """/api/reimbursement/pipeline — 지난 expectedDate 는 expectedStatus='overdue' 여야 하고(f86cb0f5: 과거 날짜를 예보처럼
    그리던 결함), not_applicable 단계(비항암의 암질심)는 날짜를 들고 있으면 안 된다. 서버 date.today() 와 하루 어긋나도 오탐이
    없게 '어제보다 전' 만 본다. 문구에는 약제명을 넣지 않고 단계 이름 집합만 — 과제 키 안정."""
    if status != 200 or not isinstance(body, dict):
        return f"HTTP {status}"
    stages = body.get("stages")
    if not isinstance(stages, list):
        return "HTTP 200 / 'stages' 없음"
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    n_past, n_na = 0, 0
    past_phases: set[str] = set()
    na_phases: set[str] = set()
    for stage in stages:
        for drug in (stage.get("drugs") or []) if isinstance(stage, dict) else []:
            for step in (drug.get("timeline") or []) if isinstance(drug, dict) else []:
                if not isinstance(step, dict):
                    continue
                exp = str(step.get("expectedDate") or "")
                if exp and exp < yesterday and step.get("expectedStatus") != "overdue":
                    n_past += 1
                    past_phases.add(str(step.get("phase")))
                if step.get("status") == "not_applicable" and (step.get("date") or step.get("expectedDate")):
                    n_na += 1
                    na_phases.add(str(step.get("phase")))
    problems = []
    if n_past:
        problems.append(f"지난 expectedDate 인데 expectedStatus≠overdue {n_past}건 {sorted(past_phases)}")
    if n_na:
        problems.append(f"not_applicable 단계에 날짜 {n_na}건 {sorted(na_phases)}")
    return "; ".join(problems) or None


MEETING_RESULTS_LAG_DAYS = 14


def _meetings_results_ingested(status: int, body) -> str | None:
    """/api/reimbursement/meetings — 14일 넘게 지난 회차가 SCHEDULED 로 남아 있으면 결과 미반영(85c0428d 의 not_ingested 상태).
    오늘 실패하는 것이 맞다(7건). 문구는 건수와 최초 날짜뿐 — 세션당 과제가 아니라 platform_smoke 과제 1건."""
    if status != 200 or not isinstance(body, dict):
        return f"HTTP {status}"
    items = body.get("items")
    if not isinstance(items, list):
        return "HTTP 200 / 'items' 없음"
    cutoff = (date.today() - timedelta(days=MEETING_RESULTS_LAG_DAYS)).isoformat()
    late = sorted(str(m.get("date")) for m in items
                  if isinstance(m, dict) and m.get("status") == "SCHEDULED" and str(m.get("date") or "") < cutoff)
    if late:
        return f"{MEETING_RESULTS_LAG_DAYS}일 넘게 지난 SCHEDULED 세션 {len(late)}건 — 결과 미반영(최초 {late[0]})"
    return None


def _media_intelligence_live(status: int, body) -> str | None:
    """/api/home/media-intelligence — 200 이고 error 없음, 브랜드 ≥1, 기사 합계 > 0 (3ddeb93b: 실패를 '기사 0건' 으로 삼키고 그
    모양을 캐시하던 결함 — looks_poisoned 모양이 다시 서빙되면 여기서 잡힌다).

    라이브 수집 판단: 이 라우트는 X-MA-Smoke 게이트가 없어 07:50 의 일자 캐시 미스에서 **원래부터** 스모크가 그날 첫 네이버 수집을
    촉발했다(2026-10-01 의 오염 캐시도 그렇게 생겼다). 그대로 둔다 — ① 합쳐진 수정은 실패/0건 결과를 캐시에 쓰지도 서빙하지도
    않으므로 스모크의 수집이 하루를 망칠 수 없고, ② 같은 수집은 어차피 첫 Home 방문이 촉발하며(비용 동일), ③ 07:50 에 캐시가
    데워져 업무 시작 전 카드가 준비된다. 스모크에서 수집을 막으면(allow_fetch=False) 거의 매일인 캐시 미스에서 내용 단언이
    무의미해진다. 네이버 장애 아침엔 이 체크가 실패 → platform_smoke 과제 1건(내용 키, 런북 없음 → 사람) — 그것이 사실이다."""
    if status != 200 or not isinstance(body, dict):
        return f"HTTP {status}"
    if body.get("error"):
        return "error 키 있음(네이버 수집 실패 — 캐시 미기록·미서빙 규약대로 실패가 노출됨)"
    brands = [b for b in (body.get("brands") or []) if isinstance(b, dict)]
    if not brands:
        return "브랜드 0개(모니터링 목록 비어 있음)"
    total = sum(int(b.get("total_count") or 0) for b in brands)
    if total <= 0:
        return f"전 브랜드 기사 0건({len(brands)}개 브랜드) — 오염/빈 집계(looks_poisoned 모양)"
    return None


# 캐시 미스 시 무거운 라우트: 정부요약은 OpenAI+Gemini(각 45초) 순차 호출, 나머지는 외부 수집/집계. 기본값은 run_smoke(timeout=).
HEAVY_TIMEOUT = 90.0      # /api/home/government-keyword-summary — LLM 2회
SLOW_TIMEOUT = 30.0       # media-intelligence(Naver 트래픽)·gov-agency-clouds(아카이브 집계)·hta/approvals(캐시 미스 시 HTA 스크레이프)


@dataclass(frozen=True)
class Check:
    path: str
    expect: Expect = _json_ok
    timeout: float | None = None       # None = run_smoke 의 기본값
    # 통과한 본문 → 상세 경로를 follow_expect(없으면 expect)로 한 번 더 — 같은 체크로 집계. None 이면 상세 생략. 실패 문구에는
    # 상세 경로(가변 id)를 넣지 않는다 — '상세: <사유>' 만(과제 키는 실패 집합의 내용 해시라 id 가 바뀌어도 같은 과제여야 한다).
    follow: Callable[[object], "str | None"] | None = None
    follow_expect: Expect | None = None


@dataclass(frozen=True)
class Screen:
    name: str
    role: str | None          # None = 토큰 없이, "user"/"admin" = 그 역할 토큰
    checks: tuple[Check, ...]


SCREENS: tuple[Screen, ...] = (
    # ── 사용자 12 화면 (frontend/src/router/config.tsx:27-38) ──
    Screen("login", None, (Check("/api/auth/me", _status(401)),                          # 인증 게이트 생존
                           Check("/api/competitor-trends", _status(401)),                # 토큰 없이 user 라우트 → 401
                           Check("/api/admin/agent-tasks", _status(401)))),              # 토큰 없이 admin 라우트 → 401
    Screen("home", None, (Check("/api/home/top-price-changes?limit=5"), Check("/api/msd/summary", _msd_summary_tristate),
                          Check("/api/home/media-intelligence", _media_intelligence_live, timeout=SLOW_TIMEOUT),
                          Check("/api/home/government-keyword-summary", timeout=HEAVY_TIMEOUT),
                          Check("/api/home/gov-agency-clouds", timeout=SLOW_TIMEOUT))),
    Screen("domestic-pricing", None, (Check("/api/domestic/price-changes?q=키트루다"), Check("/api/domestic/search?q=키트루다"),
                                      Check("/api/domestic/stats"))),
    Screen("regimen-cost", "user", (Check("/api/regimen/onco/search?q=pembrolizumab"), Check("/api/regimen-comparisons"))),
    Screen("international-pricing", None, (Check("/api/foreign/drugs"), Check("/api/foreign/cached?q=keytruda", _priced_country),
                                           Check("/api/foreign/country-overview?query=keytruda", _has_key("countries")),   # 파라미터는 query
                                           Check("/api/foreign/available_countries"),
                                           Check("/api/hta/approvals?drug=pembrolizumab", timeout=SLOW_TIMEOUT))),
    Screen("market-share", None, (Check("/api/market-share/search?q=키트루다"),)),
    Screen("competitor-trends", "user", (Check("/api/competitor-trends"), Check("/api/competitor-news?limit=5"),
                                         Check("/api/access-insight/drugs"))),
    Screen("daily-mailing", "user", (Check("/api/mail-subscriptions"),)),
    Screen("reimbursement-status", "user", (Check("/api/reimbursement/meetings", _meetings_results_ingested),
                                            Check("/api/reimbursement/pipeline", _pipeline_expectations),
                                            Check("/api/reimbursement/reports"), Check("/api/reimbursement/nhis-negotiations"))),
    Screen("policy-intelligence", "user", (Check("/api/policy-intelligence/overview"), Check("/api/policy-intelligence/events"),
                                           Check("/api/policy-intelligence/ingest-status"))),
    Screen("analog-search", "user", (Check("/api/analog/facets"), Check("/api/analog/search?q=키트루다"))),
    Screen("my-requests", "user", (Check("/api/service-requests/mine"),)),
    # ── 관리자 9 화면 (config.tsx:39-47) ──
    Screen("admin/market-share", "admin", (Check("/api/admin/market-share/uploads"),)),
    Screen("admin/msd-pipeline", "admin", (Check("/api/msd/pipeline"),)),
    Screen("admin/competitor-trends", "admin", (Check("/api/competitor-trends"),)),
    Screen("admin/reimbursement", "admin", (Check("/api/admin/reimbursement?product=keytruda"),)),
    Screen("admin/reimbursement-pipeline", "admin", (Check("/api/admin/reimb-pipeline/drugs"),
                                                     Check("/api/admin/reimb-pipeline/nhis-unmatched"))),
    Screen("admin/daily-mailing-kanban", "admin", (Check("/api/admin/daily-mailing/kanban", _has_key("runs")),   # 503 은 runs=[] 라도 상태로 걸린다
                                                   Check("/api/admin/daily-mailing/health"))),
    Screen("admin/home-brands", "admin", (Check("/api/admin/home-brands"),)),
    Screen("admin/service-requests", "admin", (Check("/api/admin/service-requests"), Check("/api/admin/service-requests/outbox/count"))),
    Screen("admin/agent-tasks", "admin", (Check("/api/admin/agent-tasks", _has_key("tasks")),)),
    # ── Stakeholder Signals (T8, config.tsx: user 2 + admin 2) — 데이터 나이는 신선도(stakeholder_*)가 본다 ──
    Screen("stakeholder-signals", "user", (Check("/api/stakeholder/overview", _has_keys("kpi", "integrity", "topics")),
                                           Check("/api/stakeholder/statements?limit=1", _has_key("items")))),
    Screen("stakeholder-signals/workbench", "user", (Check("/api/stakeholder/statements?topic=price_reform&grade=A&limit=1",
                                                           _has_key("items")),
                                                     Check("/api/stakeholder/events", _has_key("items")))),
    Screen("admin/stakeholder-people", "admin", (Check("/api/stakeholder/people", _has_key("items")),
                                                 Check("/api/admin/stakeholder/source-domains", _has_key("items")))),
    Screen("admin/stakeholder-audit", "admin", (Check("/api/admin/stakeholder/proposals", _has_key("proposals")),
                                                Check("/api/admin/stakeholder/integrity", _has_key("summary")),
                                                Check("/api/stakeholder/runs?kind=audit&limit=1", _has_key("items")))),
    # ── 권한 경계: user 토큰으로 admin·agent 라우트 → 403 (토큰 없음 → 401 은 login 화면이 본다), user 응답의 원문 경로 0 ──
    Screen("auth-gates", "user", (Check("/api/admin/agent-tasks", _status(403)), Check("/api/admin/users", _status(403)),
                                  Check("/api/admin/stakeholder/proposals", _status(403)),
                                  Check("/api/agent/stakeholder/registry", _status(403)),
                                  Check("/api/stakeholder/sources", _user_safe("items")),
                                  Check("/api/stakeholder/runs?kind=monitoring&limit=1", _user_safe("items"),
                                        follow=_first_monitoring_run, follow_expect=_user_safe("run_id")))),
)


def _get(base: str, path: str, token: str | None, opener, timeout: float) -> tuple[int, object]:
    # 한글 질의(?q=키트루다)를 그대로 넣으면 urllib 이 ASCII 인코딩에서 UnicodeEncodeError 를 낸다 — 그 예외가
    # 'API 실패'로 집계돼 platform_smoke 가 거짓 실패하던 결함. 이미 인코딩된 %XX·예약 문자는 그대로 둔다.
    safe_path = urllib.parse.quote(path, safe="/?&=%:+,;@!$'()*~#[]")
    req = urllib.request.Request(f"{base}{safe_path}")
    req.add_header(SMOKE_HEADER, "1")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read()
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        status = exc.code
    try:
        body = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        body = None
    return status, body


def run_smoke(base: str, token_for: TokenFor, *, opener=urllib.request.urlopen, timeout: float = 10.0) -> dict:
    """모든 화면을 돌고 {ok, counts, errors} 를 돌려준다(잡 규약). 토큰을 못 만든 역할의 화면은 skipped (실패 아님)."""
    errors: list[str] = []
    passed = skipped = 0
    for screen in SCREENS:
        token = token_for(screen.role) if screen.role else None
        if screen.role and not token:
            skipped += len(screen.checks)
            continue
        for check in screen.checks:
            try:
                status, body = _get(base, check.path, token, opener, check.timeout or timeout)
                why = check.expect(status, body)
                detail_path = check.follow(body) if (not why and check.follow) else None
                if detail_path:
                    d_status, d_body = _get(base, detail_path, token, opener, check.timeout or timeout)
                    d_why = (check.follow_expect or check.expect)(d_status, d_body)
                    why = f"상세: {d_why}" if d_why else None
            except Exception as exc:
                why = f"{type(exc).__name__}: {exc}"[:200]
            if why:
                errors.append(f"{screen.name} {check.path} → {why}")
            else:
                passed += 1
    return {"ok": not errors,
            "counts": {"screens": len(SCREENS), "passed": passed, "failed": len(errors), "skipped": skipped},
            "errors": errors}


def mint_token_factory(base: str, *, opener=urllib.request.urlopen, timeout: float = 10.0) -> TokenFor:
    """계정 없이 역할 토큰 발급 — 이 프로세스의 JWT_SECRET(api/auth.py:28-42: config/.env → 환경변수) 으로 서명한다.

    비밀이 웹 프로세스와 공유되는지는 파일을 들여다보지 않고 /api/auth/me 로 역할당 1회 확인한다: 401 이면 서명 거부
    (로컬에서 JWT_SECRET 이 없어 프로세스별 임시 비밀) → None → 그 역할 화면은 skipped(오탐 아님). 404 는 서명 OK + 주체가
    users DB 에 없는 정상 결과(smoke@internal 은 계정이 아니다). 결과는 역할별로 캐시한다.
    """
    cache: dict[str, str | None] = {}

    def token_for(role: str | None) -> str | None:
        if not role:
            return None
        if role in cache:
            return cache[role]
        token: str | None = None
        try:
            from api import auth
            candidate = auth._issue_token(SMOKE_SUBJECT, role)
            status, _ = _get(base, "/api/auth/me", candidate, opener, timeout)
            if status == 401:
                logger.warning("smoke: 발급 토큰 거부(401) — 웹 프로세스와 JWT_SECRET 미공유 → %s 화면 skipped", role)
            else:
                token = candidate
        except Exception as exc:
            logger.warning("smoke: 토큰 발급 실패(%s): %s", role, exc)
        cache[role] = token
        return token

    return token_for


def login_token_factory(base: str, email: str, password: str) -> TokenFor:
    """외부 실행용: 한 계정(admin 권장)으로 로그인해 모든 역할에 같은 토큰을 준다."""
    data = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(f"{base}/api/auth/login", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        token = json.loads(resp.read().decode())["token"]
    return lambda role: token if role else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MA AI Dossier 화면별 스모크")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--login-email")
    parser.add_argument("--login-password")
    args = parser.parse_args(argv)
    token_for = (login_token_factory(args.base, args.login_email, args.login_password)
                 if args.login_email else mint_token_factory(args.base))
    res = run_smoke(args.base, token_for)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
