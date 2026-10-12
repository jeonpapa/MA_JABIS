"""Stakeholder 볼륨 경로 + sync 허용목록 + 경로 안전 검사.

볼륨 root(`data/stakeholder/`)는 소스 repo(jeonpapa/kr-drug-pricing-nhi-finance-intelligence)와 **같은 상대경로**를 쓴다
(sources/, monitoring-runs/, audit-runs/, data/, scripts/, 루트 md/json/dot/svg/png). 대쉬보드가 쓰는 파일은 photos/ 하나.
경로 문자열은 원격(GitHub tree)·manifest 에서 오므로 신뢰하지 않는다 — 쓰기·읽기 전 is_safe_rel_path / resolve_under 로 검사.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def stakeholder_root() -> Path:
    return Path(os.environ.get("STAKEHOLDER_ROOT") or BASE_DIR / "data" / "stakeholder")


PHOTOS_SUBDIR = "photos"
SYNC_SUBDIR = ".sync"

# 소스 repo 상대경로를 그대로 쓴다: sources/, monitoring-runs/, audit-runs/, data/, scripts/, 루트 md/json/dot/svg/png
SYNC_ALLOW_PREFIXES = ("sources/", "monitoring-runs/", "audit-runs/", "data/", "scripts/")
SYNC_ALLOW_ROOT_SUFFIXES = (".md", ".json", ".dot", ".svg", ".png", "render_relationship_map.py")
SYNC_DENY_PREFIXES = ("dashboard-sketches/", "03-Work-Logs/", ".")   # 숨김·환경 파일 제외
MAX_BLOB_BYTES = 20 * 1024 * 1024
SAFE_PATH = re.compile(r"^[A-Za-z0-9가-힣._ /()+-]+$")   # 한글 파일명(person_00_강준혁_bing.xml) 허용, '..'·절대경로 거부


def is_safe_rel_path(rel: str) -> bool:
    """상대경로 문자열 검사: SAFE_PATH 문자집합, 절대경로·빈 세그먼트·'.'/'..'·숨김 세그먼트 거부."""
    if not rel or not isinstance(rel, str) or not SAFE_PATH.match(rel) or rel.startswith("/"):
        return False
    parts = rel.split("/")
    return all(p and p.strip() == p and not p.startswith(".") for p in parts)


def is_sync_allowed(rel: str) -> bool:
    """sync 허용목록 판정 — 안전 경로이고, 거부 접두가 아니며, 허용 접두 아래이거나 루트의 허용 확장자 파일."""
    if not is_safe_rel_path(rel):
        return False
    if any(rel.startswith(p) for p in SYNC_DENY_PREFIXES):
        return False
    if any(rel.startswith(p) for p in SYNC_ALLOW_PREFIXES):
        return True
    return "/" not in rel and rel.endswith(SYNC_ALLOW_ROOT_SUFFIXES)


def resolve_under(root: str | Path, rel: str) -> Path:
    """root 아래의 실제 경로. 안전하지 않거나 root 밖으로 나가면 ValueError (심볼릭 링크 탈출 포함)."""
    if not is_safe_rel_path(rel):
        raise ValueError(f"unsafe path: {rel!r}")
    base = Path(root).resolve()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"path escapes root: {rel!r}")
    return target


def photos_dir(root: str | Path | None = None) -> Path:
    return Path(root or stakeholder_root()) / PHOTOS_SUBDIR
