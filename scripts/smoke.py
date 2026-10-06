"""플랫폼 스모크 — 화면별 API 세트를 실제 서버에 쳐서 기대 조건을 확인한다 (P1.5 Task 4).

in-process(스케줄러 잡 platform_smoke, 매일 07:50): base=http://127.0.0.1:5001, 토큰은 공유 JWT_SECRET 으로 직접 발급
  (mint_token_factory — fly secret 이 환경변수라 web·scheduler 두 프로세스가 같은 비밀을 갖는다. 스모크 계정은 없다).
외부(VPS·로컬 수동): python scripts/smoke.py --base https://ma-ai-dossier.fly.dev --login-email ... --login-password ...
  (관리자가 만든 admin 계정 — admin 은 user 라우트도 통과한다. 새 계정 생성은 사용자 몫: VPS_SETUP.md §1 과 같은 절차)
기대 조건은 '200 + JSON' 이 기본이고 화면별로 키 존재·상태코드를 덧붙인다. 데이터 내용은 검사하지 않는다(신선도는 Task 3).
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
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)
DEFAULT_BASE = "http://127.0.0.1:5001"
SMOKE_SUBJECT = "smoke@internal"                    # 토큰 sub — users DB 에 없는 가상 주체(계정 아님)
# 모든 요청에 붙인다 — 서버(api/server.py:SMOKE_HEADER)가 이 요청에서 GET 라우트의 부수효과(검색 로그 INSERT·만료 purge·
# 캐시 미스 동기 크롤)만 건너뛴다. 읽기 판정은 그대로다(스모크의 목적). 값은 server 와 같아야 한다(테스트가 대조).
SMOKE_HEADER = "X-MA-Smoke"
Expect = Callable[[int, object], "str | None"]     # (status, body) → 실패 사유 | None
TokenFor = Callable[["str | None"], "str | None"]  # role → 토큰 | None(그 역할 화면은 skipped)


def _json_ok(status: int, body) -> str | None:
    return None if status == 200 and isinstance(body, (dict, list)) else f"HTTP {status}"


def _has_key(key: str) -> Expect:
    return lambda status, body: None if status == 200 and isinstance(body, dict) and key in body else f"HTTP {status} / '{key}' 없음"


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


# 캐시 미스 시 무거운 라우트: 정부요약은 OpenAI+Gemini(각 45초) 순차 호출, 나머지는 외부 수집/집계. 기본값은 run_smoke(timeout=).
HEAVY_TIMEOUT = 90.0      # /api/home/government-keyword-summary — LLM 2회
SLOW_TIMEOUT = 30.0       # media-intelligence(Naver 트래픽)·gov-agency-clouds(아카이브 집계)·hta/approvals(캐시 미스 시 HTA 스크레이프)


@dataclass(frozen=True)
class Check:
    path: str
    expect: Expect = _json_ok
    timeout: float | None = None       # None = run_smoke 의 기본값


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
    Screen("home", None, (Check("/api/home/top-price-changes?limit=5"), Check("/api/msd/summary"),
                          Check("/api/home/media-intelligence", timeout=SLOW_TIMEOUT),
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
    Screen("reimbursement-status", "user", (Check("/api/reimbursement/meetings"), Check("/api/reimbursement/pipeline"),
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
    Screen("admin/daily-mailing-kanban", "admin", (Check("/api/admin/daily-mailing/kanban"), Check("/api/admin/daily-mailing/health"))),
    Screen("admin/home-brands", "admin", (Check("/api/admin/home-brands"),)),
    Screen("admin/service-requests", "admin", (Check("/api/admin/service-requests"), Check("/api/admin/service-requests/outbox/count"))),
    Screen("admin/agent-tasks", "admin", (Check("/api/admin/agent-tasks", _has_key("tasks")),)),
    # ── 권한 경계: user 토큰으로 admin 라우트 → 403 (토큰 없음 → 401 은 login 화면이 본다) ──
    Screen("auth-gates", "user", (Check("/api/admin/agent-tasks", _status(403)), Check("/api/admin/users", _status(403)))),
)


def _get(base: str, path: str, token: str | None, opener, timeout: float) -> tuple[int, object]:
    req = urllib.request.Request(f"{base}{path}")
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
