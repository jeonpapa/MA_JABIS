"""foreign_drug_prices 컬럼 마이그레이션 — 읽기 전용 DB 에서는 ALTER 를 경고로 넘긴다.

tests/conftest.py 는 실제 플랫폼 DB 로 가는 연결을 mode=ro 로 돌린다. 새 컬럼(pack_count_source) 이 추가된 뒤 운영 DB 가
아직 마이그레이션되지 않았으면, 테스트에서 api.server 를 import 하는 순간 DrugPriceDB() 의 ALTER 가 "attempt to write a
readonly database" 로 죽는다. 읽기만 하는 연결에서는 건너뛰고(경고), 쓰기 가능한 DB 에서는 평소처럼 ALTER 한다.

실행: .venv/bin/python -m pytest tests/test_db_migration_readonly.py -q
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.db import DrugPriceDB


def _db_without_pack_count_source(path: Path) -> None:
    DrugPriceDB(path)                                            # 전체 스키마 (컬럼 포함)
    with sqlite3.connect(path) as conn:                          # 컬럼만 제거 — 마이그레이션 전 운영 DB 흉내
        conn.execute("ALTER TABLE foreign_drug_prices DROP COLUMN pack_count_source")
    assert "pack_count_source" not in {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(foreign_drug_prices)")}


def test_readonly_connection_skips_alter_with_warning(tmp_path, monkeypatch, caplog):
    db = tmp_path / "legacy.db"
    _db_without_pack_count_source(db)
    original = sqlite3.connect

    def ro_connect(database, *a, **k):
        if Path(str(database)).resolve() == db.resolve():
            k["uri"] = True
            return original(f"file:{db}?mode=ro", *a, **k)
        return original(database, *a, **k)

    monkeypatch.setattr(sqlite3, "connect", ro_connect)
    DrugPriceDB(db)                                              # 예외 없이 열린다
    assert any("Migration skipped (read-only DB): foreign_drug_prices.pack_count_source" in r.message for r in caplog.records)
    assert "pack_count_source" not in {r[1] for r in original(db).execute("PRAGMA table_info(foreign_drug_prices)")}


def test_writable_connection_still_migrates(tmp_path):
    db = tmp_path / "legacy.db"
    _db_without_pack_count_source(db)
    DrugPriceDB(db)
    assert "pack_count_source" in {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(foreign_drug_prices)")}
