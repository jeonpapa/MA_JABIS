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
