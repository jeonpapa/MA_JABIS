"""/api/market-share/* 기준 분기(quarter) · 기간(from/to) 파라미터 회귀 테스트.

사용자 테스트 버그: 기간을 Q2 25~Q2 25 로 바꿔도 현황(카드·도넛·상세)이 최신 분기(Q1 26)에 고정.
→ atc4 / brand / search / export 가 quarter 파라미터를 받고, export 는 from/to 로 추이 시트 범위를 제한해야 한다.
실제 플랫폼 DB 는 건드리지 않는다 (임시 sqlite + s.db._connect monkeypatch).
"""
from __future__ import annotations

import io
import sqlite3
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import api.server as s

_DDL = """
CREATE TABLE market_share_products (
    product_id TEXT PRIMARY KEY, otc_ethical TEXT, atc2_code TEXT, atc3_code TEXT,
    atc4_code TEXT, atc4_desc TEXT, mfr_name TEXT, corp TEXT, mnc13 TEXT,
    product_name TEXT NOT NULL, product_group TEXT, molecule_desc TEXT NOT NULL,
    em_ethical TEXT, kr_market TEXT, nfc3 TEXT, strength TEXT, pack_desc TEXT,
    pack TEXT, pack_launch_date TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE market_share_quarterly (
    product_id TEXT NOT NULL, quarter TEXT NOT NULL, values_lc REAL, dosage_units REAL,
    PRIMARY KEY (product_id, quarter)
);
CREATE TABLE market_share_upload_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uploaded_at TEXT NOT NULL, uploaded_by TEXT,
    filename TEXT, rows_ingested INTEGER, quarters_json TEXT
);
"""

ATC = "L01G5"
DESC = "MAB A-NEOPLAS PD-1/PD-L1"
# 분기별 매출(백만원) — 2025Q2 에는 IMFINZI 1위, 2026Q1 에는 KEYTRUDA 1위 (기준 분기에 따라 순위가 바뀐다)
VALUES = {
    "KEYTRUDA": {"2025Q2": 100.0, "2025Q3": 300.0, "2026Q1": 500.0},
    "IMFINZI": {"2025Q2": 200.0, "2025Q3": 100.0, "2026Q1": 100.0},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    dbp = tmp_path / "ms.db"
    with sqlite3.connect(dbp) as conn:
        conn.executescript(_DDL)
        for i, (name, series) in enumerate(VALUES.items()):
            pid = f"p{i}"
            conn.execute(
                "INSERT INTO market_share_products (product_id, atc4_code, atc4_desc, mfr_name, product_name, "
                "molecule_desc, pack, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (pid, ATC, DESC, f"MFR{i}", name, f"MOL{i}", "VIAL", "2026-06-15"),
            )
            for q, v in series.items():
                conn.execute("INSERT INTO market_share_quarterly VALUES (?,?,?,?)",
                             (pid, q, v * 1_000_000, v / 10))
        conn.execute(
            "INSERT INTO market_share_upload_log (uploaded_at, filename, rows_ingested, quarters_json) "
            "VALUES ('2026-06-15T09:00:00', 'nsa.xlsx', 6, '[\"2026Q1\"]')"
        )
    monkeypatch.setattr(s.db, "_connect", lambda: sqlite3.connect(dbp))
    return s.app.test_client()


def test_resolve_quarter_helper():
    qs = ["2025Q2", "2025Q3", "2026Q1"]
    assert s._ms_resolve_quarter(qs, "2025Q2") == "2025Q2"
    assert s._ms_resolve_quarter(qs, None) == "2026Q1"
    assert s._ms_resolve_quarter(qs, "1999Q1") == "2026Q1"   # 미적재 분기 → 최신
    assert s._ms_resolve_quarter([], "2025Q2") is None


def test_atc4_default_latest_and_meta(client):
    r = client.get(f"/api/market-share/atc4/{ATC}")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["quarter"] == "2026Q1"
    assert d["products"][0]["product_name"] == "KEYTRUDA"
    assert d["meta"] == {"source": "IQVIA NSA-E", "latest_quarter": "2026Q1",
                         "last_uploaded_at": "2026-06-15T09:00:00"}


def test_atc4_explicit_quarter_changes_snapshot(client):
    d = client.get(f"/api/market-share/atc4/{ATC}?quarter=2025Q2").get_json()
    assert d["quarter"] == "2025Q2"
    assert d["products"][0]["product_name"] == "IMFINZI"
    assert d["totals"]["values_lc"] == pytest.approx(300.0 * 1_000_000)
    assert d["products"][0]["values_share_pct"] == pytest.approx(200 / 3)


def test_atc4_meta_survives_missing_upload_log(client, tmp_path):
    with sqlite3.connect(tmp_path / "ms.db") as conn:
        conn.execute("DROP TABLE market_share_upload_log")
    d = client.get(f"/api/market-share/atc4/{ATC}").get_json()
    assert d["meta"]["last_uploaded_at"] is None and d["meta"]["latest_quarter"] == "2026Q1"


def test_brand_quarter_param(client):
    latest = client.get(f"/api/market-share/brand?name=IMFINZI&atc4={ATC}").get_json()
    assert latest["quarter"] == "2026Q1" and latest["market_rank"] == 2
    assert latest["quarter_values_lc"] == pytest.approx(100.0 * 1_000_000)

    q2 = client.get(f"/api/market-share/brand?name=IMFINZI&atc4={ATC}&quarter=2025Q2").get_json()
    assert q2["quarter"] == "2025Q2"
    assert q2["market_rank"] == 1
    assert q2["market_share_pct"] == pytest.approx(200 / 3)
    assert q2["quarter_values_lc"] == pytest.approx(200.0 * 1_000_000)
    assert q2["quarter_dosage_units"] == pytest.approx(20.0)
    assert [x["quarter"] for x in q2["quarterly"]] == ["2025Q2", "2025Q3", "2026Q1"]   # 시계열은 전체 유지


def test_search_quarter_param(client):
    d = client.get("/api/market-share/search?q=imfinzi&quarter=2025Q2").get_json()
    assert d["quarter"] == "2025Q2"
    assert d["items"][0]["values_lc"] == pytest.approx(200.0 * 1_000_000)
    assert client.get("/api/market-share/search?q=imfinzi").get_json()["quarter"] == "2026Q1"


def test_trend_range_top_brands_follow_range_end(client):
    d = client.get(f"/api/market-share/atc4/{ATC}/trend?from_quarter=2025Q2&to_quarter=2025Q2").get_json()
    assert d["quarters"] == ["2025Q2"]
    assert d["top_brands"][0] == "IMFINZI"


def test_export_range_in_filename_and_sheets(client):
    openpyxl = pytest.importorskip("openpyxl")
    r = client.get(f"/api/market-share/export?atc4={ATC}&quarter=2025Q3"
                   "&from_quarter=2025Q2&to_quarter=2025Q3&top=8")
    assert r.status_code == 200, r.data[:300]
    cd = unquote(r.headers["Content-Disposition"])
    assert "snap2025Q3" in cd and "trend2025Q2-2025Q3" in cd
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    assert wb.sheetnames == ["Market Share", "Unit Trend", "Revenue Trend", "Info"]
    rev_quarters = [row[0] for row in wb["Revenue Trend"].iter_rows(min_row=2, values_only=True)]
    assert rev_quarters == ["2025Q2", "2025Q3"]               # 2026Q1 은 범위 밖
    info = {row[0]: row[1] for row in wb["Info"].iter_rows(min_row=2, values_only=True)}
    assert info["현황(Market Share 시트) 기준 분기"] == "2025Q3"
    assert info["추이(Unit/Revenue Trend 시트) 기간"] == "2025Q2 ~ 2025Q3"


def test_export_without_range_keeps_all_quarters(client):
    openpyxl = pytest.importorskip("openpyxl")
    r = client.get(f"/api/market-share/export?atc4={ATC}")
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    rev_quarters = [row[0] for row in wb["Revenue Trend"].iter_rows(min_row=2, values_only=True)]
    assert rev_quarters == ["2025Q2", "2025Q3", "2026Q1"]
    assert "snap2026Q1" in unquote(r.headers["Content-Disposition"])
