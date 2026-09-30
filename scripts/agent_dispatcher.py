"""VPS 디스패처 — 열린 과제를 집어 프로덕션 런북으로 복구한다 (P1 운영 감시자: LLM 없음).

흐름(설계 §3): 로그인 → 선점 가능한 과제 조회 → 우선순위순 선점 → 런북 실행 요청 → 결과 회신.
원장은 API 로만 만진다(DB 직접 쓰기 금지). 표준 라이브러리만 쓰므로 VPS 에 pip 의존성이 없다.

크론 예: */10 * * * * cd /srv/ma-dossier && set -a && . ./.env && set +a \
            && ./venv/bin/python scripts/agent_dispatcher.py >> logs/agent.log 2>&1

환경변수: MA_API_BASE(예: https://ma-ai-dossier.fly.dev), MA_AGENT_EMAIL, MA_AGENT_PASSWORD.
로그인은 매 실행마다 한다(7일 JWT 를 디스크에 보관하지 않는다).

과제 상태: 프로덕션 신호(signals.py)는 과제를 new 로 만든다. 서버의 claim 이 ops_incident 를 자동
분류(new→triaged)한 뒤 선점하므로 디스패처는 new·triaged 를 함께 조회한다(kind=ops_incident 만).
런북 결과가 ok 면 closed, 아니면 blocked(사람 확인). 런북 힌트가 없는 과제도 blocked 로 넘겨 사람이
보게 한다. 재시도는 사람이 화면에서 triaged 로 되돌리면 되고, 서버가 3회 한도를 넘기면 blocked 로 막는다.
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
# 런북은 동기 실행이다(domestic_price_catchup 은 Playwright 크롤로 수 분). 짧은 타임아웃이면 서버는 계속
# 도는데 클라이언트만 끊겨 과제가 blocked 로 남으므로 넉넉히 둔다.
REQUEST_TIMEOUT_SECONDS = 600
NO_RUNBOOK_RESOLUTION = "자동 복구 런북 없음 — 사람 확인 필요"


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

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        with self._opener(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
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

    def run_runbook(self, task_id: int, name: str) -> dict:
        return self._request("POST", f"/api/agent/tasks/{task_id}/runbook", {"name": name})["result"]

    def resolve(self, task_id: int, status: str, resolution: str) -> None:
        self._request("POST", f"/api/agent/tasks/{task_id}/resolve",
                      {"status": status, "resolution": resolution})


class Dispatcher:
    def __init__(self, client, *, max_tasks: int = DEFAULT_MAX_TASKS, dry_run: bool = False):
        self.client = client
        self.max_tasks = max_tasks
        self.dry_run = dry_run

    def run_once(self) -> dict:
        tasks = sorted(self.client.list_tasks(CLAIMABLE_STATUSES, CLAIMABLE_KINDS),
                       key=lambda t: (t.get("priority", 3), t["id"]))
        planned: list[dict] = []
        handled = 0
        errors = 0
        for task in tasks[: self.max_tasks]:
            task_id = task["id"]
            runbook = pick_runbook(task)
            planned.append({"task_id": task_id, "runbook": runbook})
            if self.dry_run:
                continue
            if not self.client.claim(task_id):
                logger.info("선점 실패(다른 실행이 가져갔거나 정책이 막음): %s", task_id)
                continue
            if runbook is None:
                self.client.resolve(task_id, "blocked", NO_RUNBOOK_RESOLUTION)
                continue
            try:
                result = self.client.run_runbook(task_id, runbook)
                self.client.resolve(task_id, "closed" if result.get("ok") else "blocked",
                                    str(result.get("summary") or ""))
                handled += 1
            except Exception as exc:  # 선점한 과제를 in_progress 에 방치하지 않는다
                errors += 1
                logger.error("런북 요청 실패 task=%s runbook=%s: %s", task_id, runbook, exc, exc_info=True)
                try:
                    self.client.resolve(task_id, "blocked",
                                        f"디스패처 오류: {type(exc).__name__}: {exc}"[:2000])
                except Exception as exc2:
                    logger.error("blocked 회신도 실패 task=%s: %s", task_id, exc2)
        return {"planned": planned, "handled": handled, "candidates": len(tasks), "errors": errors}


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
