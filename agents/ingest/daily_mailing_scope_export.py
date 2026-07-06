"""대쉬보드 mail_subscription → 헤르메스 스콥 채널 OUTBOUND export.

inbound `agents/ingest/daily_mailing_sync.py`(runs/ 소비)의 대칭 모듈 — 대쉬보드가
정한 모니터링 스콥(dashboard_scope JSON)을 **같은 비공개 repo** 의
`daily_mailing/scopes/` 에 발행해 헤르메스가 pull 만으로 소비한다 (ssh 불필요).

- 로컬: 항상 `<scope_root>/<safe_id>.json` + `scopes_index.json` 스냅샷 갱신
  (scope_root = DAILY_MAILING_SCOPE_ROOT env > /app/data/daily_mailing/scopes > data/daily_mailing/scopes).
- 원격(publish=True + 토큰): GitHub contents API 로 PUT. **멱등** — 원격 내용과
  동일하면 PUT 생략(커밋 churn 방지). active 셋에 없는 원격 scope 파일은 DELETE(prune,
  구독 삭제/비활성 반영). 토큰 없으면 local-only 로 degrade (경고 로그, 예외 없음).
- 토큰: DAILY_MAILING_SCOPES_TOKEN(쓰기 권한 필요) > DAILY_MAILING_RUNS_TOKEN > GITHUB_TOKEN.
- 네트워크 오류는 절대 raise 하지 않고 summary["errors"] 에 수집.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agents.daily_mailing.storage import DEFAULT_DB_PATH
from agents.daily_mailing.subscription_bridge import (
    ensure_test_request_column,
    safe_scope_filename,
    subscription_to_scope,
    write_scope_snapshot,
    _default_scope_root,
)

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com/repos/jeonpapa/AccessRoutineAnalystic/contents/daily_mailing/scopes"
BRANCH = "main"
INDEX_NAME = "scopes_index.json"


def _token() -> str | None:
    return (
        os.environ.get("DAILY_MAILING_SCOPES_TOKEN")
        or os.environ.get("DAILY_MAILING_RUNS_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )


# ── GitHub contents API 레이어 (테스트에서 monkeypatch) ─────────────────────

def _gh_request(method: str, url: str, token: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def _gh_get_file(name: str, token: str) -> tuple[str | None, bytes | None]:
    """원격 파일 (sha, content bytes). 없으면 (None, None)."""
    try:
        res = _gh_request("GET", f"{GITHUB_API_BASE}/{name}?ref={BRANCH}", token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        raise
    content = base64.b64decode(res.get("content") or "")
    return res.get("sha"), content


def _gh_put_file(name: str, content: bytes, sha: str | None, token: str) -> None:
    body = {
        "message": f"daily-mailing: scope export {name}",
        "content": base64.b64encode(content).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    _gh_request("PUT", f"{GITHUB_API_BASE}/{name}", token, body)


def _gh_delete_file(name: str, sha: str, token: str) -> None:
    _gh_request(
        "DELETE",
        f"{GITHUB_API_BASE}/{name}",
        token,
        {"message": f"daily-mailing: scope prune {name} (구독 삭제/비활성)", "sha": sha, "branch": BRANCH},
    )


def _gh_list_dir(token: str) -> list[dict]:
    """scopes/ 디렉토리 파일 목록 ({name, sha} dict 배열). 디렉토리 미존재 시 []."""
    try:
        res = _gh_request("GET", f"{GITHUB_API_BASE}?ref={BRANCH}", token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    return res if isinstance(res, list) else []


# ── DB → scope 빌드 ─────────────────────────────────────────────────────────

def _json_or(raw, default):
    if raw is None or raw == "":
        return default
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return default if parsed is None else parsed


def _row_to_sub(row: sqlite3.Row) -> dict:
    keys = set(row.keys())

    def j(col, default):
        return _json_or(row[col], default) if col in keys else default

    def v(col):
        return row[col] if col in keys else None

    return {
        "id": v("id"),
        "name": v("name"),
        "owner_email": v("owner_email"),
        "keywords": j("keywords_json", []),
        "media": j("media_json", []),
        "emails": j("emails_json", []),
        "schedule": v("schedule"),
        "time": v("time"),
        "week_day": v("week_day"),
        "active": bool(v("active")),
        "companies": j("companies_json", []),
        "brands": j("brands_json", []),
        "policy_topics": j("policy_topics_json", []),
        "disease_areas": j("disease_areas_json", []),
        "custom_sources": j("custom_sources_json", []),
        "test_request": j("test_request_json", None),
        "updated_at": v("updated_at"),
    }


def build_active_scopes(db_path: str | Path | None = None) -> list[dict]:
    """active=1 구독 전체 → dashboard_scope dict 리스트 (test_request 플래그 포함).

    각 scope 에 `updated_at`(구독 최종 수정 시각)을 부가해 헤르메스가 신선도를 알 수 있게 한다.
    """
    db_path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_test_request_column(conn)
        rows = conn.execute(
            "SELECT * FROM mail_subscription WHERE active=1 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    scopes: list[dict] = []
    for row in rows:
        sub = _row_to_sub(row)
        scope = subscription_to_scope(sub)
        scope["updated_at"] = sub.get("updated_at")
        scopes.append(scope)
    return scopes


# ── export 본체 ──────────────────────────────────────────────────────────────

def export_scopes(db_path: str | Path | None = None, *, publish: bool = True,
                  scope_root: str | Path | None = None) -> dict:
    """active 구독 스콥을 로컬 스냅샷(항상) + 비공개 repo scopes/ 채널(publish)에 export.

    Returns: {"active", "local", "published", "pruned", "errors", "channel"}.
    네트워크/발행 실패는 errors 로 수집하고 절대 raise 하지 않는다 (저장 UX 보호).
    """
    summary: dict[str, Any] = {"active": 0, "local": 0, "published": 0, "pruned": 0, "errors": []}
    root = Path(scope_root) if scope_root is not None else _default_scope_root()

    try:
        scopes = build_active_scopes(db_path)
    except Exception as exc:
        summary["errors"].append({"name": "<db>", "error": f"scope build failed: {exc}"})
        summary["channel"] = "local-only"
        return summary
    summary["active"] = len(scopes)

    # 1) 로컬 스냅샷 (항상) — 파일명은 원격과 동일 규칙(safe_scope_filename).
    files: dict[str, bytes] = {}
    index: list[dict] = []
    for scope in scopes:
        try:
            path = write_scope_snapshot(scope, root=root)
            files[path.name] = path.read_bytes()
            summary["local"] += 1
        except Exception as exc:
            summary["errors"].append({"name": safe_scope_filename(scope.get("subscription_id")),
                                      "error": f"local write failed: {exc}"})
            continue
        index.append({
            "subscription_id": scope.get("subscription_id"),
            "name": scope.get("name"),
            "active": bool(scope.get("active", True)),
            "updated_at": scope.get("updated_at"),
            "has_test_request": bool(scope.get("test_request")),
        })
    try:
        root.mkdir(parents=True, exist_ok=True)
        index_bytes = json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8")
        (root / INDEX_NAME).write_bytes(index_bytes)
        files[INDEX_NAME] = index_bytes
    except Exception as exc:
        summary["errors"].append({"name": INDEX_NAME, "error": f"local write failed: {exc}"})

    # 로컬 미러 prune — 삭제/비활성 구독의 stale 스콥(잔존 test_request 위험) 제거.
    # (쓰기 실패가 하나라도 있으면 오삭제 방지 위해 prune 생략)
    summary["local_pruned"] = 0
    if summary["local"] == summary["active"] and INDEX_NAME in files:
        try:
            for stale in root.glob("*.json"):
                if stale.name not in files:
                    stale.unlink()
                    summary["local_pruned"] += 1
        except Exception as exc:
            summary["errors"].append({"name": "<local-prune>", "error": str(exc)})

    # 2) 원격 발행 (publish + 토큰) — 멱등 PUT + stale prune.
    token = _token() if publish else None
    if not publish:
        summary["channel"] = "local-only"
        return summary
    if not token:
        summary["channel"] = "local-only"
        logger.warning(
            "daily mailing scope export: 토큰 미설정(DAILY_MAILING_SCOPES_TOKEN) — "
            "로컬 스냅샷만 갱신 (%d개)", summary["local"],
        )
        return summary

    summary["channel"] = "github"
    for name, content in files.items():
        try:
            sha, remote = _gh_get_file(name, token)
            if remote is not None and remote == content:
                continue  # 멱등: 내용 동일 → PUT 생략 (커밋 churn 방지)
            _gh_put_file(name, content, sha, token)
            summary["published"] += 1
        except Exception as exc:
            summary["errors"].append({"name": name, "error": f"publish failed: {exc}"})

    # 원격 prune — 삭제/비활성 구독의 stale scope 제거. 오삭제 방지 가드:
    #  ① keep 은 성공 write 파일(files)이 아니라 **active 구독 정본 파일명 + index** 로 구성
    #     (부분 write 실패로 active 파일이 files 에서 빠져도 그 원격본을 지우지 않는다),
    #  ② 로컬 write 가 전부 성공했을 때만(all-writes-succeeded) DELETE 수행 — 아니면 skip
    #     (다음 사이클 self-heal). index 파일은 절대 prune 대상이 아니다.
    all_local_ok = summary["local"] == summary["active"] and INDEX_NAME in files
    if not all_local_ok:
        logger.warning("daily mailing scope export: 로컬 write 부분 실패 → 원격 prune skip (self-heal 대기)")
        return summary
    try:
        keep = {safe_scope_filename(s.get("subscription_id")) for s in scopes}
        keep.add(INDEX_NAME)
        for entry in _gh_list_dir(token):
            name = entry.get("name") or ""
            if name in keep or not name.endswith(".json"):
                continue
            try:
                _gh_delete_file(name, entry.get("sha") or "", token)
                summary["pruned"] += 1
            except Exception as exc:
                summary["errors"].append({"name": name, "error": f"prune failed: {exc}"})
    except Exception as exc:
        summary["errors"].append({"name": "<list>", "error": f"prune listing failed: {exc}"})

    return summary
