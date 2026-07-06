"""헤르메스 Daily Mailing run 번들(JSON) → 대쉬보드 DB 멱등 적재.

번들 = {"payload": {...run meta...}, "articles": [...card...],
        "markdown_path"?, "html_path"?, "json_path"?, "review_board_path"?,
        "is_test"?}.
persist_daily_mailing_run 이 INSERT OR REPLACE(run_id) 라 재적재 멱등.
소스: source_dir(테스트) > DAILY_MAILING_RUNS_URL/토큰(비공개 repo) > 없음.
정책 사이드카 sync(agents/ingest/policy_analysis_sync.py)와 동일 사상.

test_request 소비: 핸드오프(§2-테스트) 계약상 헤르메스는 [TEST] 발송 run 번들에
`"is_test": true` 를 기록한다(번들 top-level 또는 payload). subscription_id 가 있는
test run 을 import 하면 해당 구독의 `mail_subscription.test_request_json` 을 해제해
플래그가 1회성으로 소비되게 한다 (중복 [TEST] 발송 방지 — outbound scope export 대칭).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.daily_mailing.storage import persist_daily_mailing_run, DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/jeonpapa/AccessRoutineAnalystic/main/daily_mailing/runs"
GITHUB_API_BASE = "https://api.github.com/repos/jeonpapa/AccessRoutineAnalystic/contents/daily_mailing/runs"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.json$")


def _is_safe_name(name: str) -> bool:
    return bool(_SAFE_NAME.match(name)) and "/" not in name and "\\" not in name and ".." not in name


def _build_request(url: str, token: str | None = None) -> urllib.request.Request:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github.raw")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
    return req


def _http_get(url: str, token: str | None = None) -> bytes:
    return urllib.request.urlopen(_build_request(url, token), timeout=30).read()


def _iter_bundles_local(source_dir: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(source_dir.glob("*.json")) if p.name != "runs_index.json"}


def _iter_bundles_url(base_url: str, token: str | None = None) -> dict[str, bytes]:
    api = GITHUB_API_BASE if token else base_url
    index = json.loads(_http_get(f"{api}/runs_index.json{'?ref=main' if token else ''}", token).decode("utf-8"))
    run_ids = index if isinstance(index, list) else list(index.keys())
    out: dict[str, bytes] = {}
    for rid in run_ids:
        name = f"{rid}.json"
        if not _is_safe_name(name):
            continue
        out[name] = _http_get(f"{api}/{name}{'?ref=main' if token else ''}", token)
    return out


def _parse_dt(value) -> "datetime | None":
    """ISO8601(오프셋 유무 무관) 문자열 → aware datetime. 파싱 불가/빈값이면 None."""
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clear_consumed_test_requests(db_path: str | Path,
                                  latest_test_run: dict[int, str]) -> int:
    """[TEST] run 이 import 된 구독의 test_request_json 해제 (플래그 1회성 소비).

    ``latest_test_run``: {subscription_id: 가장 최신 test run generated_at(ISO)}.

    runs_index 는 매 sync/부팅마다 **모든** 번들을 재import 하므로, 과거 [TEST] 번들이
    runs/ 에 남아 있으면 매번 이 경로를 탄다. 아직 헤르메스가 처리하지 않은 **새 요청**을
    과거 번들이 소비해 버리는 것을 막으려면, run 이 요청보다 **최신일 때만** 해제해야 한다.
      해제 조건: run.generated_at >= 저장된 test_request.requested_at.
    generated_at / requested_at 파싱 불가 시 **해제하지 않음**(안전 기본값).
    mail_subscription 테이블/컬럼이 없는 DB(테스트 등)에서는 조용히 0 반환.
    """
    if not latest_test_run:
        return 0
    cleared = 0
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(mail_subscription)").fetchall()}
            if "test_request_json" not in cols:
                return 0
            for sid, run_generated_at in sorted(latest_test_run.items()):
                row = conn.execute(
                    "SELECT test_request_json FROM mail_subscription WHERE id=?", (sid,)
                ).fetchone()
                if not row or not row[0]:
                    continue
                try:
                    requested_at = (json.loads(row[0]) or {}).get("requested_at")
                except (TypeError, ValueError):
                    continue
                run_dt = _parse_dt(run_generated_at)
                req_dt = _parse_dt(requested_at)
                # 안전 기본값: 둘 중 하나라도 파싱 불가면 소비하지 않음(fresh 요청 보호).
                if run_dt is None or req_dt is None or run_dt < req_dt:
                    continue
                cur = conn.execute(
                    "UPDATE mail_subscription SET test_request_json=NULL "
                    "WHERE id=? AND test_request_json IS NOT NULL",
                    (sid,),
                )
                cleared += cur.rowcount
            conn.commit()
    except Exception as exc:
        logger.warning("daily mailing test_request 해제 실패(무시): %s", exc)
    return cleared


def sync_daily_mailing_runs(source_dir: str | Path | None = None,
                            db_path: str | Path | None = None) -> dict[str, Any]:
    db_path = db_path or DEFAULT_DB_PATH
    if source_dir is not None:
        try:
            bundles = _iter_bundles_local(Path(source_dir))
        except Exception as exc:
            return {"imported": 0, "skipped": 0, "error": f"local read failed: {exc}"}
    else:
        base = os.environ.get("DAILY_MAILING_RUNS_URL") or GITHUB_RAW_BASE
        token = os.environ.get("DAILY_MAILING_RUNS_TOKEN") or os.environ.get("GITHUB_TOKEN")
        try:
            bundles = _iter_bundles_url(base, token=token)
        except Exception as exc:
            return {"imported": 0, "skipped": 0, "error": f"source fetch failed: {exc}"}

    imported, skipped, errors = 0, 0, []
    # {subscription_id: 가장 최신 [TEST] run generated_at} — 요청보다 최신인 run 만 플래그 소비.
    latest_test_run: dict[int, str] = {}
    for name, data in bundles.items():
        if not _is_safe_name(name):
            skipped += 1
            continue
        try:
            bundle = json.loads(data.decode("utf-8"))
            payload = bundle.get("payload") or bundle
            articles = bundle.get("articles") or payload.get("items") or []
            persist_daily_mailing_run(
                payload,
                articles=articles,
                markdown_path=bundle.get("markdown_path"),
                html_path=bundle.get("html_path"),
                json_path=bundle.get("json_path"),
                review_board_path=bundle.get("review_board_path"),
                db_path=db_path,
            )
            imported += 1
            # [TEST] run → 해당 구독의 test_request 플래그 소비 (핸드오프 §2-테스트 계약).
            is_test = bool(bundle.get("is_test") or payload.get("is_test"))
            sub_id = payload.get("subscription_id")
            if is_test and sub_id is not None:
                try:
                    sid = int(sub_id)
                except (TypeError, ValueError):
                    sid = None
                if sid is not None:
                    gen = payload.get("generated_at")
                    prev = latest_test_run.get(sid)
                    prev_dt, gen_dt = _parse_dt(prev), _parse_dt(gen)
                    # 구독별로 가장 최신 test run 의 generated_at 만 유지.
                    if prev is None or (gen_dt is not None and (prev_dt is None or gen_dt > prev_dt)):
                        latest_test_run[sid] = gen
        except Exception as exc:
            errors.append({"name": name, "error": str(exc)})
    cleared = _clear_consumed_test_requests(db_path, latest_test_run)
    return {"imported": imported, "skipped": skipped, "errors": errors,
            "test_requests_cleared": cleared}
