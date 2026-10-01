"""테스트가 실제 플랫폼 DB 를 건드리지 못하게 하는 두 겹의 안전망 (P1.5 fix D).

1) 원장 격리: 잡 래퍼·신호가 실제 원장(data/db/drug_prices.db)에 쓰지 않게 매 테스트마다 AGENT_TASK_DB 를 임시 파일로 지정한다.
   agents/agent_tasks/jobs.resolve_db_path() 와 scheduler 의 _agent_* 경계 함수가 이 환경변수를 읽는다.
2) 읽기 전용 리다이렉트: 실제 플랫폼 DB(drug_prices.db·users.db)로 가는 sqlite3.connect 를 `file:...?mode=ro` 로 바꾼다.
   읽기는 그대로(로컬 실측·스키마 확인) 되고 쓰기는 `attempt to write a readonly database` 로 즉시 실패한다 — 스모크 테스트가
   search_log 에, dosing resolver 테스트가 dosing_resolved 에 쓰던 사고의 재발 방지. 가드가 잡은 테스트는 임시 DB 로 고친다.
3) 세션 종료 시 실제 DB 파일(mtime·size)이 바뀌지 않았는지 확인 — 자식 프로세스 등 1)·2) 를 우회한 쓰기의 마지막 그물.
   (외부 프로세스가 동시에 쓰면 오탐이 될 수 있다 — 메시지가 그 가능성을 적는다.)
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTECTED_DBS = tuple(p.resolve() for p in (REPO_ROOT / "data" / "db" / "drug_prices.db",
                                             REPO_ROOT / "data" / "db" / "users.db"))
_ORIGINAL_CONNECT = sqlite3.connect


def _target_path(database) -> Path | None:
    """connect() 의 database 인자 → 파일 경로. ':memory:'·bytes·file: URI 의 쿼리는 걷어낸다."""
    if isinstance(database, (bytes, memoryview)):
        return None
    s = os.fspath(database)
    if s.startswith("file:"):
        s = s[5:].split("?", 1)[0]
        if s.startswith("//"):
            s = s[2:]
    if not s or s == ":memory:":
        return None
    try:
        return Path(s).resolve()
    except OSError:
        return None


def _readonly_connect(database, *args, **kwargs):
    target = _target_path(database)
    if target in PROTECTED_DBS:
        s = os.fspath(database)
        if s.startswith("file:"):
            uri = s if "mode=" in s else s + ("&" if "?" in s else "?") + "mode=ro"
        else:
            uri = f"file:{target}?mode=ro"
        kwargs["uri"] = True
        return _ORIGINAL_CONNECT(uri, *args, **kwargs)
    return _ORIGINAL_CONNECT(database, *args, **kwargs)


@pytest.fixture(autouse=True)
def _isolated_agent_task_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_TASK_DB", str(tmp_path / "agent_tasks.db"))


@pytest.fixture(autouse=True)
def _real_platform_db_is_readonly(monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", _readonly_connect)


def _db_fingerprints() -> dict[str, tuple[int, int] | None]:
    """본 파일은 (mtime, size). WAL 은 내용이 있을 때만 크기 — 읽기 전용 연결도 빈 -wal 을 만들 수 있어 없음/0 바이트는 같게 본다."""
    out: dict[str, tuple[int, int] | None] = {}
    for db in PROTECTED_DBS:
        try:
            st = db.stat()
            out[str(db)] = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            out[str(db)] = None
        wal = db.with_name(db.name + "-wal")
        try:
            size = wal.stat().st_size
        except FileNotFoundError:
            size = 0
        out[str(wal)] = (0, size) if size else None
    return out


@pytest.fixture(scope="session", autouse=True)
def _real_platform_db_untouched_after_session():
    before = _db_fingerprints()
    yield
    after = _db_fingerprints()
    changed = sorted(k for k in before if before[k] != after[k])
    assert not changed, (
        f"테스트 세션 중 실제 플랫폼 DB 파일이 바뀌었다: {changed} — sqlite3 가드를 우회한 쓰기(자식 프로세스·다른 드라이버)가 있거나 "
        "외부 프로세스(로컬 스케줄러 등)가 동시에 썼다. 어느 테스트인지 찾아 임시 DB 로 고친다.")
