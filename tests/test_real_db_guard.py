"""conftest 의 실제 플랫폼 DB 안전망이 실제로 작동하는지 (P1.5 fix D).

재현됐던 사고: 전체 스위트가 실제 data/db/drug_prices.db 에 search_log 행(스모크 테스트)과 dosing_resolved TEST_* 행(dosing
resolver 테스트)을 남겼다. 가드는 실제 DB 로 가는 sqlite3.connect 를 mode=ro 로 바꾼다 — 읽기는 되고 쓰기는 즉시 실패.
이 테스트 자체는 실제 DB 에 한 바이트도 쓰지 않는다(쓰기 시도가 거부되는 것을 단언한다).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.conftest import PROTECTED_DBS

REAL = PROTECTED_DBS[0]
pytestmark = pytest.mark.skipif(not REAL.exists(), reason="실제 플랫폼 DB 없음(stub 체크아웃)")


def test_direct_write_to_real_platform_db_is_refused_but_reads_work():
    conn = sqlite3.connect(REAL)                                   # 가드가 file:...?mode=ro 로 바꾼다
    try:
        assert conn.execute("SELECT 1").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE IF NOT EXISTS __guard_probe (x)")
    finally:
        conn.close()
    for spelled in (str(REAL), Path("data/db/drug_prices.db"), f"file:{REAL}", f"file:{REAL}?cache=shared"):
        c = sqlite3.connect(spelled, uri=str(spelled).startswith("file:"))
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                c.execute("INSERT INTO search_log (query, search_type, searched_at) VALUES ('probe','probe','x')")
        finally:
            c.close()


def test_platform_db_class_cannot_log_search_against_real_db():
    from agents.db import DrugPriceDB
    db = DrugPriceDB(REAL)                                         # 스키마 CREATE IF NOT EXISTS 는 읽기 전용에서도 통과
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.log_search("probe", "foreign_price", result_count=0)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        db.save_dosing({"cache_key": "TEST_guard", "schedule": None})


def test_agent_ledger_default_path_is_refused():
    """원장 기본 경로(DEFAULT_DB_PATH = 실제 DB)로 connect 하면 스키마 생성/쓰기가 막힌다 — conftest 의 AGENT_TASK_DB 격리를 우회한 코드를 잡는다."""
    from agents.agent_tasks import store
    assert Path(store.DEFAULT_DB_PATH).resolve() == REAL
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        store.create_task(kind="ops_incident", source="probe", title="probe", db_path=store.DEFAULT_DB_PATH)


def test_temporary_dbs_are_unaffected(tmp_path):
    p = tmp_path / "t.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE t (x)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()
    c = sqlite3.connect(f"file:{p}?mode=rw", uri=True)
    assert c.execute("SELECT x FROM t").fetchone() == (1,)
    c.execute("INSERT INTO t VALUES (2)")
    c.close()
    m = sqlite3.connect(":memory:")
    m.execute("CREATE TABLE t (x)")
    m.close()
