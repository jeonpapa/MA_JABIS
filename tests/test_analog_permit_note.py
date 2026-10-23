"""허가일 빈칸 사유 칼럼 — 마이그레이션·검색 응답 포함. 값은 '미허가' 가 아니라 '왜 비었는지' 만 말한다."""
import sqlite3

from agents.analog import store


def test_column_is_migrated_and_returned_by_search(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.ensure_schema()
    with sqlite3.connect(store.DB_PATH) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(analog_reports)")}
        assert "mfds_permit_note" in cols
        c.execute("INSERT INTO analog_reports (file_name, file_hash, brand_name, mfds_permit_note) VALUES ('a.pdf','h1','테스트정','api_zero')")
        c.execute("INSERT INTO analog_reports (file_name, file_hash, brand_name, mfds_permit_date) VALUES ('b.pdf','h2','허가정','2020.01.01')")
    rows = {r["brand_name"]: r for r in store.search(filters={}, limit=10)["results"]}
    assert rows["테스트정"]["mfds_permit_note"] == "api_zero" and not rows["테스트정"].get("mfds_permit_date")
    assert rows["허가정"].get("mfds_permit_note") in (None, "")


def test_migration_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.ensure_schema(); store.ensure_schema()
