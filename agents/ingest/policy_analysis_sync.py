"""AccessRoutineAnalystic(비공개) → prod <root>/analysis/ 멱등 sync.

소스 우선순위: source_dir(테스트) > POLICY_ANALYSIS_URL(git raw manifest) > 로컬 폴백.
sha256 비교로 변경분만 복사. reimb_reports.sync_reports 패턴과 동일 사상.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

from agents.policy_analysis import default_root, ANALYSIS_SUBDIR

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/jeonpapa/AccessRoutineAnalystic/main/policy_intelligence/analysis"
# 비공개(private) repo 인증 fetch 용 — GitHub Contents API (Accept: raw → 파일 바이트 반환)
GITHUB_API_BASE = "https://api.github.com/repos/jeonpapa/AccessRoutineAnalystic/contents/policy_intelligence/analysis"

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.json$")


def _is_safe_name(name: str) -> bool:
    return bool(_SAFE_NAME.match(name)) and "/" not in name and "\\" not in name and ".." not in name


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_request(url: str, token: str | None = None) -> urllib.request.Request:
    """토큰이 있으면 GitHub API 인증 헤더 부착(비공개 repo). 없으면 공개 raw 요청."""
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github.raw")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
    return req


def _http_get(url: str, token: str | None = None) -> bytes:
    return urllib.request.urlopen(_build_request(url, token), timeout=30).read()


def _iter_source_files_local(source_dir: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(source_dir.glob("*.json")):
        out[p.name] = p.read_bytes()
    return out


def _iter_source_files_url(base_url: str, token: str | None = None) -> dict[str, bytes]:
    # 토큰 有 → GitHub API(비공개 repo 인증), 토큰 無 → 공개 raw
    if token:
        api = os.environ.get("POLICY_ANALYSIS_API") or GITHUB_API_BASE
        manifest_bytes = _http_get(f"{api}/analysis_manifest.json?ref=main", token)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        out: dict[str, bytes] = {"analysis_manifest.json": manifest_bytes}
        for event_id in manifest:
            name = f"{event_id}.json"
            out[name] = _http_get(f"{api}/{name}?ref=main", token)
        return out
    manifest_bytes = _http_get(f"{base_url}/analysis_manifest.json")
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    out = {"analysis_manifest.json": manifest_bytes}
    for event_id in manifest:
        name = f"{event_id}.json"
        out[name] = _http_get(f"{base_url}/{name}")
    return out


def sync_analysis(source_dir: str | Path | None = None, root: str | Path | None = None) -> dict[str, Any]:
    root = Path(root) if root is not None else default_root()
    dest = root / ANALYSIS_SUBDIR
    dest.mkdir(parents=True, exist_ok=True)

    if source_dir is not None:
        files = _iter_source_files_local(Path(source_dir))
    else:
        base = os.environ.get("POLICY_ANALYSIS_URL") or GITHUB_RAW_BASE
        token = os.environ.get("POLICY_ANALYSIS_TOKEN") or os.environ.get("GITHUB_TOKEN")
        try:
            files = _iter_source_files_url(base, token=token)
        except Exception as exc:
            return {"copied": 0, "skipped": 0, "rejected": 0, "error": f"source fetch failed: {exc}", "dest": str(dest)}

    dest_res = dest.resolve()
    copied, skipped, rejected = 0, 0, 0
    for name, data in files.items():
        if not _is_safe_name(name):
            rejected += 1
            continue
        target = dest / name
        if target.resolve().parent != dest_res:  # 방어적 traversal 가드
            rejected += 1
            continue
        if target.exists() and _sha(target.read_bytes()) == _sha(data):
            skipped += 1
            continue
        target.write_bytes(data)
        copied += 1
    return {"copied": copied, "skipped": skipped, "rejected": rejected, "dest": str(dest)}
