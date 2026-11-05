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
from datetime import datetime, timedelta, timezone
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


_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GITHUB_TREE_URL = "https://api.github.com/repos/jeonpapa/AccessRoutineAnalystic/git/trees/main?recursive=1"
RUNS_PREFIX = "daily_mailing/runs/"
DEFAULT_LOOKBACK_DAYS = 3
# 원격 blob sha 캐시 — 같은 프로세스(스케줄러) 안에서 바뀌지 않은 번들은 다시 받지 않는다(20분 주기 · API 호출 절약).
_SEEN_BLOB_SHA: dict[str, str] = {}


def _iter_bundles_local(source_dir: Path) -> dict[str, bytes]:
    """평면 레이아웃(`<run>.json`)은 key=파일명, 날짜 폴더 레이아웃(`<YYYY-MM-DD>/<sub>_<run>.json`)은 key=`날짜/파일명`."""
    out = {p.name: p.read_bytes() for p in sorted(source_dir.glob("*.json")) if p.name != "runs_index.json"}
    for p in sorted(source_dir.glob("*/*.json")):
        if _DATE_DIR.match(p.parent.name):
            out[f"{p.parent.name}/{p.name}"] = p.read_bytes()
    return out


def _split_key(key: str) -> tuple[str | None, str]:
    """`날짜/파일명` → (날짜, 파일명). 평면 키는 (None, 파일명)."""
    if "/" in key:
        day, name = key.split("/", 1)
        if _DATE_DIR.match(day):
            return day, name
    return None, key


def _iter_bundles_dated_remote(token: str, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                               today: str | None = None) -> dict[str, bytes]:
    """헤르메스 현행 레이아웃 `daily_mailing/runs/<YYYY-MM-DD>/<sub>_<run>.json` (2026-10 확인) — 최근 N일만.

    트리 1회 조회로 경로·blob sha 를 얻고, sha 가 바뀐 파일만 내려받는다.
    """
    tree = json.loads(_http_get(GITHUB_TREE_URL, token).decode("utf-8"))
    kst_today = datetime.fromisoformat(today).date() if today else datetime.now(timezone(timedelta(hours=9))).date()
    cutoff = (kst_today - timedelta(days=max(lookback_days, 0))).isoformat()
    out: dict[str, bytes] = {}
    for node in tree.get("tree") or []:
        path = node.get("path") or ""
        if node.get("type") != "blob" or not path.startswith(RUNS_PREFIX):
            continue
        rest = path[len(RUNS_PREFIX):]
        day, name = _split_key(rest)
        if day is None or day < cutoff or not _is_safe_name(name):
            continue
        sha = node.get("sha") or ""
        if sha and _SEEN_BLOB_SHA.get(path) == sha:
            continue
        out[f"{day}/{name}"] = _http_get(f"{GITHUB_API_BASE}/{day}/{name}?ref=main", token)
        if sha:
            _SEEN_BLOB_SHA[path] = sha
    return out


def _normalize_dated_bundle(bundle: dict, payload: dict, run_date: str) -> dict:
    """헤르메스 run 번들(날짜 폴더) → 대시보드 run 계약.

    - run_id: 헤르메스 run_id 는 구독 간 시각만 다르다 → `hermes_<sub>_<run_id>` 로 네임스페이스(결정적 → 멱등).
    - run_date: 폴더 날짜(KST 실행일). 헬스체크·칸반이 날짜로 찾는다.
    - owner_email·recipients·dashboard_scope: payload 에 없으면 `dashboard_scope_snapshot` 에서 채운다.
    """
    p = dict(payload)
    sub = p.get("subscription_id", bundle.get("subscription_id"))
    try:
        sub = int(sub)
    except (TypeError, ValueError):
        pass
    p["subscription_id"] = sub
    rid = str(p.get("run_id") or bundle.get("run_id") or "")
    if not rid.startswith("hermes_"):
        p["run_id"] = f"hermes_{sub}_{rid}"
    p["run_date"] = p.get("run_date") or p.get("run_date_kst") or run_date
    snap = p.get("dashboard_scope") or p.get("dashboard_scope_snapshot") or {}
    if isinstance(snap, dict):
        p["dashboard_scope"] = snap
        p["owner_email"] = p.get("owner_email") or snap.get("owner_email")
        p["recipients"] = p.get("recipients") or snap.get("recipients")
        p["keywords"] = p.get("keywords") or snap.get("keywords")
    if bundle.get("is_test"):
        p["is_test"] = True
    return p


def _supersede_older_hermes_run(payload: dict, db_path) -> bool:
    """같은 구독·날짜의 다른 헤르메스 정규 run 이 있으면 더 최신(generated_at) 것만 남긴다.

    Returns False = 이 번들이 더 오래됐다(건너뜀). 서버 자체 run(`hermes_` 아님)은 건드리지 않는다 —
    그 경우 persist 가 DuplicateRegularRunError 로 건너뛰게 둔다.
    """
    sub, day, rid = payload.get("subscription_id"), payload.get("run_date"), str(payload.get("run_id"))
    if sub is None or not day or payload.get("is_test"):
        return True
    try:
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT run_id, generated_at FROM daily_mailing_run WHERE subscription_id=? AND run_date=? "
                "AND is_test=0 AND run_id<>? AND run_id LIKE 'hermes\\_%' ESCAPE '\\'",
                (sub, day, rid),
            ).fetchall()
            new_dt = _parse_dt(payload.get("generated_at"))
            for old_id, old_gen in rows:
                old_dt = _parse_dt(old_gen)
                if old_dt is not None and (new_dt is None or old_dt >= new_dt):
                    return False
            for old_id, _ in rows:
                conn.execute("DELETE FROM daily_mailing_article WHERE run_id=?", (old_id,))
                conn.execute("DELETE FROM daily_mailing_run WHERE run_id=?", (old_id,))
            conn.commit()
    except sqlite3.OperationalError:
        return True   # 테이블 미생성(첫 적재) — persist 가 만든다
    return True


def _record_hermes_delivery(payload: dict, bundle: dict, db_path) -> bool:
    """헤르메스가 실제 발송한 run 이면 sent_at·gmail_message_id 를 기록. 발송 증빙(message id) 없으면 기록하지 않는다."""
    from agents.daily_mailing.run_store import update_run

    result = payload.get("delivery_result")
    msg_id = payload.get("gmail_message_id") or (result.get("gmail_message_id") if isinstance(result, dict) else None)
    if payload.get("delivery_status") != "sent" or not msg_id:
        return False
    update_run(str(payload["run_id"]), db_path=db_path,
               sent_at=str(payload.get("sent_at") or bundle.get("executed_at") or payload.get("generated_at") or ""),
               gmail_message_id=str(msg_id))
    return True


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
                            db_path: str | Path | None = None, *,
                            lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict[str, Any]:
    """헤르메스 run 번들 → DB. 원격은 날짜 폴더 레이아웃(현행)을 우선, 없으면 구 `runs_index.json` 평면 레이아웃.

    토큰: DAILY_MAILING_RUNS_TOKEN > DAILY_MAILING_SCOPES_TOKEN > GITHUB_TOKEN (비공개 repo — 토큰 없으면 실패 보고).
    """
    from agents.daily_mailing.storage import DuplicateRegularRunError

    db_path = db_path or DEFAULT_DB_PATH
    if source_dir is not None:
        try:
            bundles = _iter_bundles_local(Path(source_dir))
        except Exception as exc:
            return {"imported": 0, "skipped": 0, "error": f"local read failed: {exc}"}
    else:
        base = os.environ.get("DAILY_MAILING_RUNS_URL") or GITHUB_RAW_BASE
        token = (os.environ.get("DAILY_MAILING_RUNS_TOKEN") or os.environ.get("DAILY_MAILING_SCOPES_TOKEN")
                 or os.environ.get("GITHUB_TOKEN"))
        try:
            if token:
                bundles = _iter_bundles_dated_remote(token, lookback_days=lookback_days)
            else:
                bundles = _iter_bundles_url(base, token=None)
        except Exception as exc:
            return {"imported": 0, "skipped": 0, "error": f"source fetch failed: {exc}"}

    imported, skipped, errors = 0, 0, []
    duplicates: list[str] = []
    sent_recorded = 0
    # {subscription_id: 가장 최신 [TEST] run generated_at} — 요청보다 최신인 run 만 플래그 소비.
    latest_test_run: dict[int, str] = {}
    for key, data in bundles.items():
        run_day, name = _split_key(key)
        if not _is_safe_name(name):
            skipped += 1
            continue
        try:
            bundle = json.loads(data.decode("utf-8"))
            payload = bundle.get("payload") or bundle
            if run_day is not None:
                payload = _normalize_dated_bundle(bundle, payload, run_day)
                if not _supersede_older_hermes_run(payload, db_path):
                    skipped += 1
                    continue
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
            if run_day is not None and _record_hermes_delivery(payload, bundle, db_path):
                sent_recorded += 1
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
        except DuplicateRegularRunError as exc:
            # 같은 구독·날짜에 서버 자체 run 이 이미 있다(전환 당일) — 덮어쓰지 않고 건너뛴다.
            skipped += 1
            duplicates.append(f"{key}: {exc}")
        except Exception as exc:
            errors.append({"name": key, "error": str(exc)})
    cleared = _clear_consumed_test_requests(db_path, latest_test_run)
    return {"imported": imported, "skipped": skipped, "errors": errors, "duplicates": duplicates,
            "sent_recorded": sent_recorded, "test_requests_cleared": cleared}
