"""AccessRoutineAnalystic(비공개) → prod <root>/analysis/ 멱등 sync.

소스 우선순위: source_dir(테스트) > POLICY_ANALYSIS_URL(git raw manifest) > 로컬 폴백.
sha256 비교로 변경분만 복사. reimb_reports.sync_reports 패턴과 동일 사상.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from agents.policy_analysis import default_root, ANALYSIS_SUBDIR

GITHUB_RAW_BASE = "https://raw.githubusercontent.com/jeonpapa/AccessRoutineAnalystic/main/policy_intelligence/analysis"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iter_source_files_local(source_dir: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for p in sorted(source_dir.glob("*.json")):
        out[p.name] = p.read_bytes()
    return out


def _iter_source_files_url(base_url: str) -> dict[str, bytes]:
    manifest_bytes = urllib.request.urlopen(f"{base_url}/analysis_manifest.json", timeout=30).read()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    out: dict[str, bytes] = {"analysis_manifest.json": manifest_bytes}
    for event_id in manifest:
        name = f"{event_id}.json"
        out[name] = urllib.request.urlopen(f"{base_url}/{name}", timeout=30).read()
    return out


def sync_analysis(source_dir: str | Path | None = None, root: str | Path | None = None) -> dict[str, Any]:
    root = Path(root) if root is not None else default_root()
    dest = root / ANALYSIS_SUBDIR
    dest.mkdir(parents=True, exist_ok=True)

    if source_dir is not None:
        files = _iter_source_files_local(Path(source_dir))
    else:
        base = os.environ.get("POLICY_ANALYSIS_URL") or GITHUB_RAW_BASE
        try:
            files = _iter_source_files_url(base)
        except Exception as exc:
            return {"copied": 0, "skipped": 0, "error": f"source fetch failed: {exc}"}

    copied, skipped = 0, 0
    for name, data in files.items():
        target = dest / name
        if target.exists() and _sha(target.read_bytes()) == _sha(data):
            skipped += 1
            continue
        target.write_bytes(data)
        copied += 1
    return {"copied": copied, "skipped": skipped, "dest": str(dest)}
