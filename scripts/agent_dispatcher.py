"""VPS 디스패처 — 열린 과제를 집어 프로덕션 런북으로 복구한다 (P1 운영 감시자: LLM 없음).

흐름(설계 §3): 로그인 → 선점 가능한 과제 조회 → 우선순위순 선점 → 런북 실행 요청 → 결과 회신.
원장은 API 로만 만진다(DB 직접 쓰기 금지). 표준 라이브러리만 쓰므로 VPS 에 pip 의존성이 없다.

크론 예(VPS_SETUP §4 — flock 으로 실행 겹침 방지, 안 그러면 서버에 Playwright 크롤이 동시에 뜬다):
  */10 * * * * cd /srv/ma-dossier && flock -n /srv/ma-dossier/agent.lock sh -c 'set -a && . ./.env \
               && set +a && exec ./venv/bin/python scripts/agent_dispatcher.py' >> logs/agent.log 2>&1

환경변수: MA_API_BASE(예: https://ma-ai-dossier.fly.dev), MA_AGENT_EMAIL, MA_AGENT_PASSWORD.
로그인은 매 실행마다 한다(7일 JWT 를 디스크에 보관하지 않는다).

과제 상태: 프로덕션 신호(signals.py)는 과제를 new 로 만든다. 서버의 claim 이 ops_incident 를 자동
분류(new→triaged)한 뒤 선점하므로 디스패처는 new·triaged 를 함께 조회한다(kind=ops_incident 만).
런북 결과가 ok 면 closed, 아니면 blocked(사람 확인). 런북 힌트가 없는 과제도 blocked 로 넘겨 사람이
보게 한다. 재시도는 운영자가 admin 토큰으로 `POST /api/agent/tasks/<id>/resolve {"status":"triaged"}`
를 보내면 된다(관리자 화면에는 정책 토글만 있고 상태 변경 버튼은 P2 — VPS_SETUP §6). 서버가 3회 한도를
넘기면 blocked 로 막는다.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("agent_dispatcher")
RUNBOOK_HINT = re.compile(r"런북:\s*([a-z_]+)")
CLAIMABLE_STATUSES = "new,triaged"
# P1 이 처리할 수 있는 종류만 받는다 — user_request 등이 --max-tasks 슬롯을 먹지 않게 (P2 에서 확장)
CLAIMABLE_KINDS = "ops_incident"
DEFAULT_MAX_TASKS = 3
# 요청별 타임아웃. login/list/claim/resolve 는 짧게 — 로그인 hang 하나가 프로세스를 10분 잡지 않게.
REQUEST_TIMEOUT_SECONDS = 30
# 런북 호출만 길게: 서버 안에서 동기 실행이다(domestic_price_catchup 은 Playwright 크롤로 수 분). 짧으면 서버는
# 계속 도는데 클라이언트만 끊겨 과제가 blocked 로 남는다. 단 프록시/워커가 이보다 먼저 끊을 수 있다는 것은
# **미검증** (api/server.py 의 "수 분짜리 동기 요청은 프록시 타임아웃에 끊긴다 → job 분리 + 202 폴링" 참고).
# 그 경우 서버엔 runbook_ok 가 남고 과제는 "디스패처 오류" 로 blocked 되는 조합으로 나타난다. 근본 해결은 P2 의
# 202 + 폴링.
RUNBOOK_TIMEOUT_SECONDS = 600
NO_RUNBOOK_RESOLUTION = "자동 복구 런북 없음 — 사람 확인 필요"
# 서버가 409 로 "이 과제는 네 것이 아니다" 라고 답하는 코드. STALE_CLAIM: 운영자가 triaged 로 되돌린 뒤 다른
# 실행이 재선점(claimed_by 불일치). NOT_CLAIMED: 선점 직후 누군가 상태를 바꿔 in_progress 가 아니다.
# 둘 다 이 과제를 더 만지면 남의 결과를 덮는다 — blocked 회신조차 하지 않고 다음 과제로 넘어간다.
LOST_CLAIM_CODES = frozenset({"STALE_CLAIM", "NOT_CLAIMED"})


class LostClaim(Exception):
    """서버가 409 STALE_CLAIM/NOT_CLAIMED 로 답했다 — 이 과제는 더 이상 이 실행의 것이 아니다."""


def _error_code(exc: urllib.error.HTTPError) -> str:
    """HTTPError 본문({"code": ...})의 code. 본문이 JSON 이 아니면 빈 문자열."""
    try:
        return str(json.loads(exc.read().decode("utf-8") or "{}").get("code") or "")
    except Exception:
        return ""


def pick_runbook(task: dict) -> str | None:
    """과제 detail 의 '런북: <이름>' 힌트(signals._runbook_line 형식)를 읽는다. 없으면 None(사람 확인)."""
    match = RUNBOOK_HINT.search(str(task.get("detail") or ""))
    return match.group(1) if match else None


class ApiClient:
    """프로덕션 /api/agent/* 클라이언트. opener 주입으로 테스트에서 네트워크를 끊는다."""

    def __init__(self, base: str, email: str, password: str, *, opener=urllib.request.urlopen):
        self.base = base.rstrip("/")
        self._opener = opener
        self._token: str | None = None
        self._token = self._login(email, password)

    def _request(self, method: str, path: str, body: dict | None = None, *,
                 timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        with self._opener(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    def _login(self, email: str, password: str) -> str:
        return self._request("POST", "/api/auth/login", {"email": email, "password": password})["token"]

    def list_tasks(self, status: str, kind: str) -> list[dict]:
        return self._request("GET", f"/api/agent/tasks?status={status}&kind={kind}")["tasks"]

    def claim(self, task_id: int) -> bool:
        try:
            return bool(self._request("POST", f"/api/agent/tasks/{task_id}/claim")["claimed"])
        except urllib.error.HTTPError as exc:
            if exc.code in (409, 423):     # 이미 선점됐거나(409) 정책·예산이 막음(423)
                logger.info("선점 거부 %s: HTTP %s", task_id, exc.code)
                return False
            raise

    def _owned_request(self, path: str, body: dict, *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict:
        """선점한 과제에만 허용되는 요청 — 409 STALE_CLAIM/NOT_CLAIMED 는 LostClaim 으로 올린다."""
        try:
            return self._request("POST", path, body, timeout=timeout)
        except urllib.error.HTTPError as exc:
            code = _error_code(exc) if exc.code == 409 else ""
            if code in LOST_CLAIM_CODES:
                raise LostClaim(code) from exc
            raise

    def run_runbook(self, task_id: int, name: str) -> dict:
        return self._owned_request(f"/api/agent/tasks/{task_id}/runbook", {"name": name},
                                   timeout=RUNBOOK_TIMEOUT_SECONDS)["result"]

    def resolve(self, task_id: int, status: str, resolution: str) -> None:
        self._owned_request(f"/api/agent/tasks/{task_id}/resolve", {"status": status, "resolution": resolution})


class Dispatcher:
    def __init__(self, client, *, max_tasks: int = DEFAULT_MAX_TASKS, dry_run: bool = False):
        self.client = client
        self.max_tasks = max_tasks
        self.dry_run = dry_run

    def run_once(self) -> dict:
        tasks = sorted(self.client.list_tasks(CLAIMABLE_STATUSES, CLAIMABLE_KINDS),
                       key=lambda t: (t.get("priority", 3), t["id"]))
        planned: list[dict] = []
        handled = errors = skipped = 0
        for task in tasks[: self.max_tasks]:
            task_id = task["id"]
            runbook = pick_runbook(task)
            planned.append({"task_id": task_id, "runbook": runbook})
            if self.dry_run:
                continue
            try:
                claimed = self.client.claim(task_id)
            except Exception as exc:   # 5xx(sqlite 잠김 등)·네트워크 — 이 과제만 건너뛰고 실행은 계속한다
                errors += 1
                logger.error("선점 요청 실패 task=%s: %s — 건너뜀", task_id, exc)
                continue
            if not claimed:
                logger.info("선점 실패(다른 실행이 가져갔거나 정책이 막음): %s", task_id)
                continue
            try:
                handled += self._handle_claimed(task_id, runbook)
            except LostClaim as exc:
                # 이 과제는 내 것이 아니다(운영자 되돌리기 후 재선점 등) — 회신 없이 다음 과제로
                skipped += 1
                logger.warning("선점 무효 task=%s (%s) — 다른 실행이 쥐고 있어 건너뜀", task_id, exc)
            except Exception as exc:  # 선점한 과제를 in_progress 에 방치하지 않는다
                errors += 1
                logger.error("런북 요청 실패 task=%s runbook=%s: %s", task_id, runbook, exc, exc_info=True)
                try:
                    self.client.resolve(task_id, "blocked",
                                        f"디스패처 오류: {type(exc).__name__}: {exc}"[:2000])
                except LostClaim as exc2:
                    skipped += 1
                    logger.warning("선점 무효 task=%s (%s) — blocked 회신 생략", task_id, exc2)
                except Exception as exc2:
                    logger.error("blocked 회신도 실패 task=%s: %s", task_id, exc2)
        return {"planned": planned, "handled": handled, "candidates": len(tasks), "errors": errors,
                "skipped": skipped}

    def _handle_claimed(self, task_id: int, runbook: str | None) -> int:
        """선점한 과제 하나: 런북 실행 → 회신. 처리 수(0|1)를 돌려주고 LostClaim 은 그대로 올린다."""
        if runbook is None:
            self.client.resolve(task_id, "blocked", NO_RUNBOOK_RESOLUTION)
            return 0
        result = self.client.run_runbook(task_id, runbook)
        self.client.resolve(task_id, "closed" if result.get("ok") else "blocked",
                            str(result.get("summary") or ""))
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="에이전트 팀 디스패처 (P1 운영 감시자)")
    parser.add_argument("--dry-run", action="store_true", help="선점/실행 없이 계획만 출력")
    parser.add_argument("--max-tasks", type=int, default=DEFAULT_MAX_TASKS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    base = os.environ.get("MA_API_BASE", "").strip()
    email = os.environ.get("MA_AGENT_EMAIL", "").strip()
    password = os.environ.get("MA_AGENT_PASSWORD", "").strip()
    if not (base and email and password):
        logger.error("MA_API_BASE·MA_AGENT_EMAIL·MA_AGENT_PASSWORD 환경변수가 필요합니다")
        return 2
    out = Dispatcher(ApiClient(base, email, password), max_tasks=args.max_tasks, dry_run=args.dry_run).run_once()
    logger.info("dispatch: %s", json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
