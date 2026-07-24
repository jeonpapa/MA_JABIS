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


def _recipients(scope: dict) -> list[str]:
    r = scope.get("emails") or scope.get("recipients") or []
    if not r and scope.get("owner_email"):
        r = [scope["owner_email"]]
    return [x for x in r if x]


def _deliver(scope: dict, html_body: str, subject: str, decision: dict) -> dict:
    from agents.notify.gmail_delivery import create_gmail_draft, send_gmail_message
    recipients = _recipients(scope)
    if not recipients:
        return {"ok": False, "message": "no recipients"}
    if decision["mode"] == "gmail_send":
        return send_gmail_message(recipients=recipients, subject=subject, body_html=html_body)
    return create_gmail_draft(recipients=recipients, subject=subject, body_html=html_body)


def process_scope(scope: dict, scope_path: str, out_dir: str, env_path: str,
                  db_path: str, token: str | None, commit: bool) -> dict:
    has_test = bool(scope.get("test_request"))
    decision = decide_delivery(scope, has_test)
    result = run_pipeline(scope_path, out_dir, env_path, db_path)
    payload = result["payload"]
    payload["is_test"] = decision["is_test"]
    payload["delivery_mode"] = decision["mode"]

    delivered = False
    delivery_result = None
    if decision["deliver"]:
        subject = decision["subject_prefix"] + f"[MA Daily] {scope.get('name') or scope.get('subscription_id')}"
        delivery_result = _deliver(scope, result["html_body"], subject, decision)
        delivered = bool(delivery_result.get("ok"))
        payload["delivery_status"] = "sent" if decision["mode"] == "gmail_send" and delivered else (
            "draft_created" if delivered else "delivery_failed")

    bundle = {"payload": payload, "articles": result["articles"], "is_test": decision["is_test"]}
    committed = False
    if commit and token:
        name = f"run_{payload.get('run_id')}_sub{payload.get('subscription_id')}.json"
        put_run_bundle(name, bundle, token)
        committed = True
    return {"subscription_id": scope.get("subscription_id"), "status": result["summary"].get("status"),
            "delivered": delivered, "delivery_result": delivery_result, "committed": committed}


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes daily-mailing runner (push-triggered).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scope-ids", nargs="*", help="브릿지 scopes/<id>.json 에서 fetch")
    g.add_argument("--scope-file", help="로컬 scope JSON (드라이런)")
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "runs" / "drafts"))
    ap.add_argument("--env", default=str(REPO_ROOT / "config" / ".env"))
    ap.add_argument("--db", default=str(REPO_ROOT / "data" / "db" / "drug_prices.db"))
    ap.add_argument("--no-commit", action="store_true", help="run 번들 커밋 생략 (로컬 드라이런)")
    args = ap.parse_args()

    token = os.environ.get("DAILY_MAILING_RUNS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    results = []
    if args.scope_file:
        scope = load_scope_local(args.scope_file)
        results.append(process_scope(scope, args.scope_file, args.out_dir, args.env,
                                     args.db, token, commit=not args.no_commit))
    else:
        for sid in args.scope_ids:
            scope = fetch_scope(sid, token)
            scope_path = str(Path(args.out_dir) / f"_scope_{sid}.json")
            Path(args.out_dir).mkdir(parents=True, exist_ok=True)
            Path(scope_path).write_text(json.dumps(scope, ensure_ascii=False), encoding="utf-8")
            try:
                results.append(process_scope(scope, scope_path, args.out_dir, args.env,
                                             args.db, token, commit=not args.no_commit))
            except Exception as e:
                results.append({"subscription_id": sid, "error": str(e)})
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
