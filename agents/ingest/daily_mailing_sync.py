"""헤르메스 Daily Mailing run 번들(JSON) → 대쉬보드 DB 멱등 적재.

번들 = {"payload": {...run meta...}, "articles": [...card...],
        "markdown_path"?, "html_path"?, "json_path"?, "review_board_path"?}.
persist_daily_mailing_run 이 INSERT OR REPLACE(run_id) 라 재적재 멱등.
소스: source_dir(테스트) > DAILY_MAILING_RUNS_URL/토큰(비공개 repo) > 없음.
정책 사이드카 sync(agents/ingest/policy_analysis_sync.py)와 동일 사상.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

from agents.daily_mailing.storage import persist_daily_mailing_run, DEFAULT_DB_PATH

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
        except Exception as exc:
            errors.append({"name": name, "error": str(exc)})
    return {"imported": imported, "skipped": skipped, "errors": errors}
