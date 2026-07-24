# Daily Mailing 스콥 push 트리거 (헤르메스 즉시 반영) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대쉬보드 스콥 변경(push)을 GitHub `repository_dispatch` 로 헤르메스(GitHub Actions CI)에 즉시 전달해, 폴링 대기 없이 near-real-time 으로 반영한다.

**Architecture:** 브릿지 repo(AccessRoutineAnalystic)의 `scopes/**` push → 얇은 워크플로가 `repository_dispatch` 발사 → MA_AI_Dossier 워크플로가 `scripts/hermes_runner.py` 실행. runner 는 브릿지에서 scope 를 fetch → 기존 규칙기반 파이프라인(`generate_daily_mailing_draft.py`, LLM 없음)으로 초안 생성 → delivery 분기(test_request 만 즉시 발송) → run 번들을 브릿지 `runs/` 에 커밋. 대쉬보드는 기존 sync 로 칸반 반영.

**Tech Stack:** Python 3.12 (stdlib urllib for GitHub Contents API), pytest, GitHub Actions, Gmail API (google-api-python-client, 기존).

**Spec:** `docs/superpowers/specs/2026-07-24-hermes-daily-mailing-push-trigger-design.md`

---

## File Structure

- Create: `scripts/hermes_runner.py` — 오케스트레이터 (scope fetch → 파이프라인 → delivery → run 번들 커밋)
- Modify: `agents/notify/gmail_delivery.py` — `send_gmail_message()` 추가 (gmail_send 경로)
- Create: `.github/workflows/hermes-daily-mailing.yml` — MA_AI_Dossier 메인 워크플로
- Create: `docs/daily_mailing/notify-scope-change.workflow.yml` — 브릿지 repo 배치용 트리거 워크플로 (템플릿)
- Create: `tests/test_hermes_runner.py` — runner 단위 테스트 (네트워크·subprocess 모킹)
- Modify: `tests/test_gmail_delivery.py` (없으면 Create) — send_gmail_message 테스트

**재사용 (변경 없음):** `scripts/generate_daily_mailing_draft.py`, `agents/daily_mailing/dashboard_scope.py`, `agents/ingest/daily_mailing_sync.py` (run 번들 포맷 `{payload, articles, is_test}` 소비).

---

## Task 1: Gmail live-send 함수 추가

**Files:**
- Modify: `agents/notify/gmail_delivery.py` (파일 끝에 추가)
- Test: `tests/test_gmail_delivery.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_gmail_delivery.py
from agents.notify.gmail_delivery import send_gmail_message


class _FakeSend:
    def __init__(self): self.sent = None
    def users(self): return self
    def messages(self): return self
    def send(self, userId, body): self.sent = (userId, body); return self
    def execute(self): return {"id": "msg123", "threadId": "thr123", "labelIds": ["SENT"]}


def test_send_gmail_message_sends_and_returns_ids():
    svc = _FakeSend()
    out = send_gmail_message(
        recipients=["a@x.com"], subject="Hi", body_html="<b>hi</b>", service=svc,
    )
    assert out["ok"] is True
    assert out["mode"] == "gmail_send"
    assert out["sent"] is True
    assert out["gmail_message_id"] == "msg123"
    assert svc.sent[0] == "me"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_gmail_delivery.py -q`
Expected: FAIL — `ImportError: cannot import name 'send_gmail_message'`

- [ ] **Step 3: 최소 구현**

`agents/notify/gmail_delivery.py` 파일 끝에 추가:

```python
def send_gmail_message(
    *,
    recipients: Iterable[str],
    subject: str,
    body_html: str,
    body_text: str | None = None,
    sender: str | None = None,
    token_path: str | Path = TOKEN_PATH,
    service: GmailServiceProtocol | None = None,
) -> dict:
    """Send an email via Gmail API (live send). Caller must gate this explicitly."""
    if service is None:
        if not gmail_configured(token_path):
            return {"ok": False, "mode": "gmail_send", "sent": False,
                    "message": "Gmail OAuth token missing gmail.send scope"}
        service = _build_service(token_path)
    assert service is not None
    msg = build_email_message(
        recipients=recipients, subject=subject, body_html=body_html,
        body_text=body_text, sender=sender,
    )
    raw = encode_raw_message(msg)
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {
        "ok": True,
        "mode": "gmail_send",
        "sent": True,
        "gmail_message_id": sent.get("id"),
        "thread_id": sent.get("threadId"),
        "recipients": [r.strip() for r in recipients if r and r.strip()],
        "subject": subject,
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_gmail_delivery.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add agents/notify/gmail_delivery.py tests/test_gmail_delivery.py
git commit -m "feat(daily-mailing): Gmail live-send 함수 추가 (gmail_send 경로)"
```

---

## Task 2: delivery 결정 로직 (순수 함수)

**Files:**
- Create: `scripts/hermes_runner.py` (신규 파일 시작)
- Test: `tests/test_hermes_runner.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
# tests/test_hermes_runner.py
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "hermes_runner", Path(__file__).resolve().parent.parent / "scripts" / "hermes_runner.py")
hermes_runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hermes_runner)
decide_delivery = hermes_runner.decide_delivery


def test_test_request_triggers_delivery_with_test_prefix():
    d = decide_delivery({"delivery_mode": "gmail_draft"}, has_test_request=True)
    assert d == {"deliver": True, "mode": "gmail_draft", "subject_prefix": "[TEST] ", "is_test": True}


def test_normal_edit_does_not_deliver():
    d = decide_delivery({"delivery_mode": "gmail_send"}, has_test_request=False)
    assert d["deliver"] is False and d["is_test"] is False


def test_default_mode_is_gmail_draft():
    d = decide_delivery({}, has_test_request=True)
    assert d["mode"] == "gmail_draft"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -q`
Expected: FAIL — `FileNotFoundError` (scripts/hermes_runner.py 없음)

- [ ] **Step 3: 최소 구현**

```python
# scripts/hermes_runner.py
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add scripts/hermes_runner.py tests/test_hermes_runner.py
git commit -m "feat(hermes): delivery 결정 로직 (test_request 만 즉시 발송)"
```

---

## Task 3: scope fetch (브릿지 raw / 로컬 파일)

**Files:**
- Modify: `scripts/hermes_runner.py`
- Test: `tests/test_hermes_runner.py`

- [ ] **Step 1: 실패 테스트 작성** (파일 끝에 추가)

```python
def test_load_scope_local_reads_json(tmp_path):
    p = tmp_path / "42.json"
    p.write_text('{"subscription_id": 42, "delivery_mode": "gmail_draft"}', encoding="utf-8")
    scope = hermes_runner.load_scope_local(str(p))
    assert scope["subscription_id"] == 42


def test_fetch_scope_uses_raw_url(monkeypatch):
    calls = {}
    def fake_get(url, token):
        calls["url"] = url
        return b'{"subscription_id": 7}'
    monkeypatch.setattr(hermes_runner, "_http_get_bytes", fake_get)
    scope = hermes_runner.fetch_scope("7", token="tok")
    assert scope["subscription_id"] == 7
    assert calls["url"].endswith("/scopes/7.json")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -k "scope" -q`
Expected: FAIL — `AttributeError: ... has no attribute 'load_scope_local'`

- [ ] **Step 3: 최소 구현** (hermes_runner.py 에 추가)

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -k "scope" -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/hermes_runner.py tests/test_hermes_runner.py
git commit -m "feat(hermes): scope fetch (브릿지 raw / 로컬 파일)"
```

---

## Task 4: run 번들 GitHub Contents API 커밋

**Files:**
- Modify: `scripts/hermes_runner.py`
- Test: `tests/test_hermes_runner.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_put_run_bundle_puts_and_updates_index(monkeypatch):
    puts = []
    def fake_req(method, url, token, body=None):
        puts.append((method, url, body))
        if method == "GET":
            raise hermes_runner.urllib.error.HTTPError(url, 404, "nf", {}, None)
        return {}
    monkeypatch.setattr(hermes_runner, "_gh_request", fake_req)
    hermes_runner.put_run_bundle(
        "run_20260724.json",
        {"payload": {"run_id": "20260724", "subscription_id": 7}, "articles": []},
        token="tok",
    )
    put_urls = [u for (m, u, _) in puts if m == "PUT"]
    assert any(u.endswith("/runs/run_20260724.json") for u in put_urls)
    assert any(u.endswith("/runs/runs_index.json") for u in put_urls)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -k "bundle" -q`
Expected: FAIL — `AttributeError: ... 'put_run_bundle'`

- [ ] **Step 3: 최소 구현** (hermes_runner.py 에 추가)

```python
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
    # 인덱스 갱신
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -k "bundle" -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/hermes_runner.py tests/test_hermes_runner.py
git commit -m "feat(hermes): run 번들 GitHub Contents API 커밋 + 인덱스 갱신"
```

---

## Task 5: 파이프라인 실행 + 아티팩트 로드

**Files:**
- Modify: `scripts/hermes_runner.py`
- Test: `tests/test_hermes_runner.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_run_pipeline_parses_stdout_and_reads_html(monkeypatch, tmp_path):
    html = tmp_path / "d.html"; html.write_text("<b>x</b>", encoding="utf-8")
    jsonf = tmp_path / "d.json"; jsonf.write_text('{"run_id":"20260724","selected_count":3}', encoding="utf-8")
    summary = {"json": str(jsonf), "html": str(html), "review_board": None,
               "status": "ok", "selected_count": 3}
    class _P:
        returncode = 0
        stdout = json.dumps(summary)
        stderr = ""
    monkeypatch.setattr(hermes_runner.subprocess, "run", lambda *a, **k: _P())
    out = hermes_runner.run_pipeline("scope.json", str(tmp_path), "env", "db.sqlite")
    assert out["payload"]["run_id"] == "20260724"
    assert out["html_body"] == "<b>x</b>"
    assert out["summary"]["status"] == "ok"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -k "pipeline" -q`
Expected: FAIL — `AttributeError: ... 'run_pipeline'`

- [ ] **Step 3: 최소 구현** (hermes_runner.py 에 추가)

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -k "pipeline" -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add scripts/hermes_runner.py tests/test_hermes_runner.py
git commit -m "feat(hermes): 파이프라인 subprocess 실행 + 아티팩트 로드"
```

---

## Task 6: 스콥 처리 오케스트레이션 + CLI

**Files:**
- Modify: `scripts/hermes_runner.py`
- Test: `tests/test_hermes_runner.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_process_scope_delivers_on_test_request(monkeypatch):
    scope = {"subscription_id": 7, "delivery_mode": "gmail_draft",
             "emails": ["a@x.com"], "test_request": {"requested_at": "t"}}
    monkeypatch.setattr(hermes_runner, "run_pipeline", lambda *a, **k: {
        "summary": {"status": "ok"},
        "payload": {"run_id": "R1", "subscription_id": 7, "generated_at": "g"},
        "html_body": "<b>x</b>", "articles": [],
    })
    delivered = {}
    monkeypatch.setattr(hermes_runner, "_deliver", lambda scope, html, subject, decision: delivered.update(subject=subject) or {"ok": True})
    committed = {}
    monkeypatch.setattr(hermes_runner, "put_run_bundle", lambda name, bundle, token: committed.update(name=name, is_test=bundle.get("is_test")))
    out = hermes_runner.process_scope(scope, "scope.json", "/tmp/out", "env", "db", token="tok", commit=True)
    assert out["delivered"] is True
    assert delivered["subject"].startswith("[TEST] ")
    assert committed["is_test"] is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -k "process_scope" -q`
Expected: FAIL — `AttributeError: ... 'process_scope'`

- [ ] **Step 3: 최소 구현** (hermes_runner.py 에 추가)

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_hermes_runner.py -q`
Expected: PASS (모든 테스트)

- [ ] **Step 5: 로컬 드라이런 (실제 파이프라인, 커밋·발송 없음)**

Run:
```bash
.venv/bin/python scripts/hermes_runner.py \
  --scope-file agents/daily_mailing/dashboard_scope.example.json \
  --out-dir runs/drafts --no-commit
```
Expected: `results` JSON 출력, `delivered: false`(example 에 test_request 없음), 파이프라인 status 포함. Gmail/네트워크 호출 없음.

- [ ] **Step 6: 커밋**

```bash
git add scripts/hermes_runner.py tests/test_hermes_runner.py
git commit -m "feat(hermes): 스콥 처리 오케스트레이션 + CLI"
```

---

## Task 7: MA_AI_Dossier 메인 워크플로

**Files:**
- Create: `.github/workflows/hermes-daily-mailing.yml`

- [ ] **Step 1: 워크플로 작성**

```yaml
# .github/workflows/hermes-daily-mailing.yml
name: Hermes Daily Mailing (scope-triggered)

on:
  repository_dispatch:
    types: [scope-changed]
  workflow_dispatch:
    inputs:
      scope_ids:
        description: "공백 구분 scope id (수동 드라이런)"
        required: false
        default: ""

concurrency:
  group: hermes-${{ github.event.client_payload.scope_ids && join(github.event.client_payload.scope_ids, '-') || github.event.inputs.scope_ids }}
  cancel-in-progress: true

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      - name: Write Gmail token
        run: |
          mkdir -p /tmp/secrets
          printf '%s' "${{ secrets.GOOGLE_TOKEN_JSON }}" > /tmp/secrets/google_token.json
      - name: Write env
        run: |
          mkdir -p config
          {
            echo "NAVER_API_CLIENT_ID=${{ secrets.NAVER_API_CLIENT_ID }}"
            echo "NAVER_API_CLIENT_SECRET=${{ secrets.NAVER_API_CLIENT_SECRET }}"
          } > config/.env
      - name: Run hermes runner
        env:
          DAILY_MAILING_RUNS_TOKEN: ${{ secrets.DAILY_MAILING_RUNS_TOKEN }}
          GOOGLE_TOKEN_PATH: /tmp/secrets/google_token.json
          SCOPE_IDS: ${{ join(github.event.client_payload.scope_ids, ' ') }}
          MANUAL_IDS: ${{ github.event.inputs.scope_ids }}
        run: |
          IDS="${SCOPE_IDS:-$MANUAL_IDS}"
          if [ -z "$IDS" ]; then echo "no scope ids"; exit 0; fi
          python scripts/hermes_runner.py --scope-ids $IDS --out-dir runs/drafts
```

- [ ] **Step 2: 로컬 YAML 문법 검증**

Run: `.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/hermes-daily-mailing.yml')); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 3: 커밋**

```bash
git add .github/workflows/hermes-daily-mailing.yml
git commit -m "ci(hermes): scope-changed dispatch 시 daily-mailing 러너 실행"
```

---

## Task 8: 브릿지 repo 트리거 워크플로 (템플릿)

**Files:**
- Create: `docs/daily_mailing/notify-scope-change.workflow.yml`

- [ ] **Step 1: 템플릿 작성** (사용자가 AccessRoutineAnalystic repo 의 `.github/workflows/` 에 배치)

```yaml
# 배치 위치: jeonpapa/AccessRoutineAnalystic/.github/workflows/notify-scope-change.yml
name: Notify scope change

on:
  push:
    paths:
      - "daily_mailing/scopes/**"

jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2
      - name: Collect changed scope ids
        id: ids
        run: |
          CHANGED=$(git diff --name-only HEAD~1 HEAD -- 'daily_mailing/scopes/*.json' \
            | grep -v 'scopes_index.json' | sed -E 's#.*/##; s/\.json$//' | tr '\n' ' ')
          echo "ids=$CHANGED" >> "$GITHUB_OUTPUT"
      - name: Dispatch to MA_AI_Dossier
        if: steps.ids.outputs.ids != ''
        env:
          TOKEN: ${{ secrets.DISPATCH_TOKEN }}
        run: |
          IDS_JSON=$(printf '%s\n' ${{ steps.ids.outputs.ids }} | jq -R . | jq -s -c .)
          curl -sf -X POST \
            -H "Authorization: Bearer $TOKEN" \
            -H "Accept: application/vnd.github+json" \
            https://api.github.com/repos/jeonpapa/MA_AI_Dossier/dispatches \
            -d "{\"event_type\":\"scope-changed\",\"client_payload\":{\"scope_ids\":$IDS_JSON}}"
```

- [ ] **Step 2: 배치 안내 주석 추가** (파일 상단에 배치 위치·시크릿 안내 — 위 주석 라인 확인)

- [ ] **Step 3: 커밋**

```bash
git add docs/daily_mailing/notify-scope-change.workflow.yml
git commit -m "docs(hermes): 브릿지 repo scope-change 트리거 워크플로 템플릿"
```

---

## Task 9: 통합 문서 + 시크릿 체크리스트

**Files:**
- Modify: `docs/daily_mailing/HERMES_DAILY_MAILING_HANDOFF.md` (섹션 추가)

- [ ] **Step 1: 운영 섹션 추가** (파일 끝에)

```markdown
## 8. push 트리거 (스콥 변경 → 즉시 반영, 2026-07-24)
- 브릿지 `scopes/**` push → `notify-scope-change.yml` → `repository_dispatch(scope-changed)` → MA_AI_Dossier `hermes-daily-mailing.yml` → `scripts/hermes_runner.py`.
- **test_request 만 즉시 발송** (기본 gmail_draft). 정규 발송은 기존 일일 스케줄 유지.
- 필요 시크릿:
  - 브릿지 repo: `DISPATCH_TOKEN`(MA_AI_Dossier repo dispatch PAT).
  - MA_AI_Dossier repo: `NAVER_API_CLIENT_ID`, `NAVER_API_CLIENT_SECRET`, `GOOGLE_TOKEN_JSON`, `DAILY_MAILING_RUNS_TOKEN`.
- 수동 드라이런: Actions → hermes-daily-mailing → Run workflow → scope_ids 입력.
```

- [ ] **Step 2: 커밋**

```bash
git add docs/daily_mailing/HERMES_DAILY_MAILING_HANDOFF.md
git commit -m "docs(hermes): push 트리거 운영·시크릿 체크리스트"
```

---

## 배포 / 활성화 (사용자 직접)

1. MA_AI_Dossier + 브릿지 repo 에 시크릿 등록 (위 체크리스트).
2. 브릿지 repo 에 `notify-scope-change.yml` 배치.
3. 코드 배포(`flyctl deploy`)는 이 기능에 불필요 — 워크플로는 GitHub Actions 에서 실행(대쉬보드 fly 와 독립). 단 대쉬보드가 최신 코드여야 run 번들 sync·칸반이 정상.
4. gmail_draft 로 검증 후 필요 시 구독별 `delivery_mode=gmail_send` 전환.

---

## Self-Review 결과
- **Spec 커버리지**: §2 아키텍처→Task 2~8, §4 시크릿→Task 7·9, §5 에러/중복→Task 6(concurrency Task 7), §6 테스트→각 Task TDD + Task 6 드라이런. 전부 매핑됨.
- **Placeholder**: 없음 (모든 스텝 실제 코드/명령 포함).
- **타입 일관성**: `decide_delivery`→dict{deliver,mode,subject_prefix,is_test}, `run_pipeline`→{summary,payload,html_body,articles}, `process_scope`→{...,delivered,committed}, `put_run_bundle(name,bundle,token)`, `_deliver(scope,html,subject,decision)` — Task 간 시그니처 일치 확인.
