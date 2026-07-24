"""헤르메스 러너 — 스콥 변경(push→dispatch) 시 GitHub Actions 에서 실행.

브릿지에서 scope fetch → generate_daily_mailing_draft 파이프라인(규칙기반, LLM 없음)
→ delivery 분기(test_request 만 즉시 발송) → run 번들을 브릿지 runs/ 에 커밋.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_API_BASE = "https://api.github.com/repos/jeonpapa/AccessRoutineAnalystic/contents/daily_mailing/runs"
SCOPES_RAW_BASE = "https://raw.githubusercontent.com/jeonpapa/AccessRoutineAnalystic/main/daily_mailing/scopes"
BRANCH = "main"


def decide_delivery(scope: dict, has_test_request: bool) -> dict:
    """delivery 결정. **정규 발송은 일일 스케줄에 위임** — push 트리거는 test_request 만 즉시 발송."""
    mode = scope.get("delivery_mode") or "gmail_draft"
    if has_test_request:
        return {"deliver": True, "mode": mode, "subject_prefix": "[TEST] ", "is_test": True}
    return {"deliver": False, "mode": mode, "subject_prefix": "", "is_test": False}


def _http_get_bytes(url: str, token: str | None) -> bytes:
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def load_scope_local(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fetch_scope(scope_id: str, token: str | None) -> dict:
    url = f"{SCOPES_RAW_BASE}/{scope_id}.json"
    return json.loads(_http_get_bytes(url, token).decode("utf-8"))


def _gh_request(method: str, url: str, token: str, body: dict | None = None):
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


def _gh_get_sha(name: str, token: str) -> str | None:
    try:
        res = _gh_request("GET", f"{RUNS_API_BASE}/{name}?ref={BRANCH}", token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return res.get("sha")


def _gh_put(name: str, content: bytes, token: str, message: str) -> None:
    sha = _gh_get_sha(name, token)
    body = {"message": message, "branch": BRANCH,
            "content": base64.b64encode(content).decode("ascii")}
    if sha:
        body["sha"] = sha
    _gh_request("PUT", f"{RUNS_API_BASE}/{name}", token, body)


def put_run_bundle(name: str, bundle: dict, token: str) -> None:
    """run 번들 파일 + runs_index.json 갱신 (append/replace, 멱등)."""
    payload = bundle.get("payload") or {}
    content = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
    _gh_put(name, content, token, f"daily-mailing: run bundle {name}")
    try:
        idx_raw = _http_get_bytes(
            f"https://raw.githubusercontent.com/jeonpapa/AccessRoutineAnalystic/{BRANCH}/daily_mailing/runs/runs_index.json",
            token,
        )
        index = json.loads(idx_raw.decode("utf-8"))
        if not isinstance(index, list):
            index = []
    except Exception:
        index = []
    index = [e for e in index if e.get("name") != name]
    index.append({
        "name": name,
        "run_id": payload.get("run_id"),
        "subscription_id": payload.get("subscription_id"),
        "generated_at": payload.get("generated_at"),
        "is_test": bool(bundle.get("is_test")),
    })
    _gh_put("runs_index.json",
            json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"),
            token, "daily-mailing: runs_index update")


def run_pipeline(scope_path: str, out_dir: str, env_path: str, db_path: str) -> dict:
    """generate_daily_mailing_draft.py 를 subprocess 로 실행하고 산출물을 로드."""
    cmd = [
        sys.executable, str(REPO_ROOT / "scripts" / "generate_daily_mailing_draft.py"),
        "--dashboard-scope", scope_path,
        "--out-dir", out_dir,
        "--env", env_path,
        "--db", db_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pipeline failed rc={proc.returncode}: {proc.stderr[-500:]}")
    summary = json.loads(proc.stdout)
    payload = json.loads(Path(summary["json"]).read_text(encoding="utf-8"))
    html_body = Path(summary["html"]).read_text(encoding="utf-8") if summary.get("html") else ""
    articles = []
    if summary.get("review_board"):
        rb = json.loads(Path(summary["review_board"]).read_text(encoding="utf-8"))
        articles = rb.get("articles", [])
    return {"summary": summary, "payload": payload, "html_body": html_body, "articles": articles}
