"""Access Insight A1/A2/A3 — prominence gate · track/stage 모델 · score_bands.

- A1: resolve_drug_with_prominence(title/body_strong/passing), passing 신호의
  momentum 제외(행 보존, journey flag), backfill_prominence 멱등 UPDATE.
- A2: track(oncology/general/unknown) + stages/current_stage, 일반약 → 약평위
  세션 배정(암질심 금지), relink_sessions 재배정.
- A3: SCORE_BANDS 단일 소스 (leaderboard/detail API 가 노출).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agents.access_insight import backfill as B
from agents.access_insight import link as L
from agents.access_insight.aggregate import (
    SCORE_BANDS,
    drug_momentum,
    journey,
    leaderboard,
    list_drugs_with_signals,
    score_bands,
)


_SCHEMA = """
CREATE TABLE amjilsim_drugs (
    drug_id INTEGER PRIMARY KEY,
    product_slug TEXT,
    brand_kr TEXT NOT NULL,
    brand_en TEXT,
    ingredient_inn TEXT,
    msd_flag INTEGER NOT NULL DEFAULT 0,
    expected_session_id INTEGER,
    amjilsim_pass_date DATE,
    yakpyungwi_pass_date DATE,
    negotiation_status TEXT,
    negotiation_complete_date TEXT,
    nhis_registered_ym TEXT,
    submitted_date DATE,
    indication TEXT,
    is_oncology INTEGER
);
CREATE TABLE product_alias_map (
    product_slug TEXT PRIMARY KEY,
    inn TEXT,
    brand_aliases_json TEXT
);
CREATE TABLE amjilsim_sessions (
    session_id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    ordinal_assumed INTEGER NOT NULL DEFAULT 0,
    ordinal_official INTEGER,
    session_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'SCHEDULED',
    committee_type TEXT NOT NULL DEFAULT 'AMJILSIM'
);
CREATE TABLE amjilsim_media_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id INTEGER,
    session_id INTEGER,
    tier TEXT NOT NULL,
    outlet TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    published_at TEXT,
    snippet TEXT,
    signal_type TEXT,
    signal_phrases TEXT,
    crossref_count INTEGER NOT NULL DEFAULT 1,
    weight REAL NOT NULL DEFAULT 1.0,
    crawled_at TEXT,
    committee_target TEXT NOT NULL DEFAULT 'UNKNOWN',
    source_verified TEXT NOT NULL DEFAULT 'headline_only',
    UNIQUE(outlet, url)
);
CREATE TABLE analog_reports (
    id INTEGER PRIMARY KEY,
    brand_name TEXT,
    disease_category TEXT,
    mfds_permit_date TEXT,
    first_reimbursement_date TEXT
);
CREATE TABLE indications_master (
    indication_id TEXT PRIMARY KEY,
    product TEXT NOT NULL
);
CREATE TABLE indication_reimbursement (
    indication_id TEXT PRIMARY KEY,
    is_reimbursed INTEGER NOT NULL DEFAULT 0,
    effective_date TEXT
);
"""


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    # 약제: 1=키트루다(항암), 2=마운자로(일반), 3=미상약(is_oncology NULL)
    conn.executemany(
        "INSERT INTO amjilsim_drugs (drug_id, product_slug, brand_kr, brand_en, "
        "ingredient_inn, is_oncology, amjilsim_pass_date, yakpyungwi_pass_date, "
        "submitted_date) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, "keytruda", "키트루다", "Keytruda", "pembrolizumab",
             1, "2026-03-04", None, "2026-01-15"),
            (2, "mounjaro", "마운자로", "Mounjaro", "tirzepatide",
             0, None, None, "2026-02-01"),
            (3, None, "미상약", None, None, None, None, None, None),
        ],
    )
    # 세션: 암질심 2, 약평위 1
    conn.executemany(
        "INSERT INTO amjilsim_sessions (session_id, year, session_date, status, "
        "committee_type) VALUES (?,?,?,?,?)",
        [
            (10, 2026, "2026-07-08", "SCHEDULED", "AMJILSIM"),
            (11, 2026, "2026-08-19", "SCHEDULED", "AMJILSIM"),
            (20, 2026, "2026-07-22", "SCHEDULED", "YAKPYUNGWI"),
        ],
    )
    conn.commit()
    conn.close()
    L.invalidate_index_cache()
    yield str(path)
    L.invalidate_index_cache()


def _sig(conn, drug_id, url, title, snippet, pub, session_id=None,
         prominence=None, weight=1.0):
    B.ensure_prominence_column(conn)
    conn.execute(
        "INSERT INTO amjilsim_media_signals (drug_id, session_id, tier, outlet, url, "
        "title, published_at, snippet, signal_type, weight, prominence) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (drug_id, session_id, "A", "outlet", url, title, pub, snippet,
         "GOV_STATEMENT", weight, prominence),
    )


# ── A1: resolve_drug_with_prominence ────────────────────────────────────────

def test_prominence_title(db):
    drug_id, prom = L.resolve_drug_with_prominence(
        "키트루다 급여 확대 논의", "약평위 상정 예정", db_path=db)
    assert drug_id == 1
    assert prom == "title"


def test_prominence_body_strong_repeated_mention(db):
    drug_id, prom = L.resolve_drug_with_prominence(
        "면역항암제 시장 동향",
        "올해 시장은 확대됐다. 키트루다 매출이 급증했고 키트루다 적응증도 늘었다.",
        db_path=db)
    assert drug_id == 1
    assert prom == "body_strong"


def test_prominence_body_strong_first_sentence(db):
    drug_id, prom = L.resolve_drug_with_prominence(
        "면역항암제 시장 동향",
        "키트루다 매출이 급증했다. 다른 약도 성장세다.",
        db_path=db)
    assert drug_id == 1
    assert prom == "body_strong"


def test_prominence_passing_single_mid_body_mention(db):
    """산업 라운드업 — 제목에 없고 본문 중간 1회 스침 → passing."""
    drug_id, prom = L.resolve_drug_with_prominence(
        "제약업계 주간 동향 종합",
        "여러 소식이 있었다. 항암제 분야에서는 키트루다 등 다수 품목이 언급됐다.",
        db_path=db)
    assert drug_id == 1
    assert prom == "passing"


def test_prominence_no_match(db):
    drug_id, prom = L.resolve_drug_with_prominence(
        "무관한 제목", "무관한 본문", db_path=db)
    assert drug_id is None and prom is None


def test_resolve_drug_wrapper_unchanged(db):
    assert L.resolve_drug("키트루다 급여 확대", db_path=db) == 1
    assert L.resolve_drug("무관한 텍스트", db_path=db) is None


# ── A1: passing 은 momentum 에서 제외 (행 보존, journey flag) ─────────────────

def test_passing_excluded_from_momentum(db):
    conn = sqlite3.connect(db)
    _sig(conn, 1, "u1", "키트루다 급여 확대", "약평위 상정", "2026-06-20",
         prominence="title", weight=1.5)
    _sig(conn, 1, "u2", "업계 라운드업", "여러 소식. 키트루다 등 언급.", "2026-06-21",
         prominence="passing", weight=1.5)
    conn.commit()
    conn.close()

    m = drug_momentum(1, db_path=db, as_of="2026-07-01", window_days=90)
    assert m["signal_count"] == 1          # passing 제외
    assert m["excluded_passing"] == 1
    assert m["by_type"]["GOV_STATEMENT"] == 1

    # journey 는 행을 보존하되 flag 로 노출
    j = journey(1, db_path=db)
    assert len(j["signals"]) == 2
    by_url = {s["url"]: s for s in j["signals"]}
    assert by_url["u2"]["passing"] is True
    assert by_url["u2"]["prominence"] == "passing"
    assert by_url["u1"]["passing"] is False
    assert j["signal_count"] == 1
    assert j["passing_count"] == 1


def test_null_prominence_included_in_momentum(db):
    """미백필(NULL) prominence 는 종전대로 집계에 포함 (안전 기본값)."""
    conn = sqlite3.connect(db)
    _sig(conn, 1, "u1", "키트루다 급여", "본문", "2026-06-20", prominence=None)
    conn.commit()
    conn.close()
    m = drug_momentum(1, db_path=db, as_of="2026-07-01", window_days=90)
    assert m["signal_count"] == 1
    assert m["excluded_passing"] == 0


# ── A1: backfill_prominence (UPDATE-only, 멱등) ──────────────────────────────

def test_backfill_prominence_updates_existing_rows(db):
    conn = sqlite3.connect(db)
    # prominence 미기록 기존 행 3종 (구 백필 데이터 시뮬레이션)
    _sig(conn, 1, "t1", "키트루다 급여 확대", "약평위 상정", "2026-06-01")
    _sig(conn, 1, "t2", "업계 종합", "여러 소식이 있었다. 키트루다 등 언급됐다.", "2026-06-02")
    # 과거 brand 태그 매칭으로 들어온 행 — 표면 텍스트에 약 이름 없음 → passing
    _sig(conn, 1, "t3", "무관한 제목", "무관한 본문", "2026-06-03")
    conn.commit()
    conn.close()

    res = B.backfill_prominence(db)
    assert res["total"] == 3
    assert res["by_prominence"] == {"title": 1, "passing": 2}

    conn = sqlite3.connect(db)
    proms = dict(conn.execute("SELECT url, prominence FROM amjilsim_media_signals"))
    conn.close()
    assert proms == {"t1": "title", "t2": "passing", "t3": "passing"}

    # 멱등 — 재실행 시 변경 0
    res2 = B.backfill_prominence(db)
    assert res2["updated"] == 0


# ── A2: track / stages / current_stage ──────────────────────────────────────

def test_oncology_track_stages(db):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO analog_reports (brand_name, mfds_permit_date) "
        "VALUES ('키트루다', '2015-03-20')")
    _sig(conn, 1, "u1", "키트루다 급여", "본문", "2026-06-20", prominence="title")
    conn.commit()
    conn.close()

    m = drug_momentum(1, db_path=db, as_of="2026-07-01")
    assert m["track"] == "oncology"
    keys = [s["key"] for s in m["stages"]]
    assert keys == ["permit", "submission", "amjilsim", "yakpyungwi",
                    "negotiation", "final_notice", "listing"]
    labels = [s["label"] for s in m["stages"]]
    assert labels == ["허가", "신청", "암질심", "약평위", "공단협상", "건정심·고시", "등재"]
    by_key = {s["key"]: s for s in m["stages"]}
    assert by_key["permit"]["date"] == "2015-03-20"
    assert by_key["amjilsim"]["date"] == "2026-03-04"
    assert by_key["amjilsim"]["status"] == "done"
    # 암질심 통과 후 약평위 대기 → current_stage=AMJILSIM_PASSED, 약평위가 current
    assert m["current_stage"] == "AMJILSIM_PASSED"
    assert by_key["yakpyungwi"]["status"] == "current"
    assert by_key["negotiation"]["status"] == "pending"
    assert m["expected_committee"] == "AMJILSIM"


def test_general_track_has_no_amjilsim_stage(db):
    conn = sqlite3.connect(db)
    _sig(conn, 2, "u2", "마운자로 급여", "본문", "2026-06-20", prominence="title")
    conn.commit()
    conn.close()

    m = drug_momentum(2, db_path=db, as_of="2026-07-01")
    assert m["track"] == "general"
    keys = [s["key"] for s in m["stages"]]
    assert "amjilsim" not in keys           # 일반약은 암질심 스테이지 없음
    assert "yakpyungwi" in keys             # 약평위는 유지 (공통 결정 위원회)
    assert m["current_stage"] == "PRE_COMMITTEE"
    assert m["expected_committee"] == "YAKPYUNGWI"

    j = journey(2, db_path=db)
    assert j["track"] == "general"
    assert "amjilsim" not in [s["key"] for s in j["stages"]]


def test_current_stage_listed_and_negotiation(db):
    conn = sqlite3.connect(db)
    # 마운자로: 약평위 통과 + 협상 진행중
    conn.execute(
        "UPDATE amjilsim_drugs SET yakpyungwi_pass_date='2026-05-01', "
        "negotiation_status='IN_PROGRESS' WHERE drug_id=2")
    # 키트루다: 등재 완료
    conn.execute(
        "INSERT INTO analog_reports (brand_name, mfds_permit_date, first_reimbursement_date) "
        "VALUES ('키트루다', '2015-03-20', '2017-08-21')")
    _sig(conn, 1, "u1", "키트루다", "키트루다 본문", "2026-06-20", prominence="title")
    _sig(conn, 2, "u2", "마운자로", "마운자로 본문", "2026-06-20", prominence="title")
    conn.commit()
    conn.close()

    m1 = drug_momentum(1, db_path=db, as_of="2026-07-01")
    assert m1["current_stage"] == "LISTED"
    assert all(s["status"] == "done" for s in m1["stages"])

    m2 = drug_momentum(2, db_path=db, as_of="2026-07-01")
    assert m2["current_stage"] == "NEGOTIATION"
    by_key = {s["key"]: s for s in m2["stages"]}
    assert by_key["yakpyungwi"]["status"] == "done"
    assert by_key["negotiation"]["status"] == "current"


def test_unknown_track(db):
    conn = sqlite3.connect(db)
    _sig(conn, 3, "u3", "미상약 소식", "미상약 본문", "2026-06-20", prominence="title")
    conn.commit()
    conn.close()
    m = drug_momentum(3, db_path=db, as_of="2026-07-01")
    assert m["track"] == "unknown"
    assert m["expected_committee"] is None
    assert m["current_stage"] == "PRE_COMMITTEE"


# ── A2: 일반약 → 약평위 세션 배정 (암질심 금지) + relink ─────────────────────

def test_backfill_links_general_drug_to_yakpyungwi_session(db):
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE competitor_news (id INTEGER PRIMARY KEY, brand TEXT, kind TEXT, "
        "title TEXT, url TEXT, source_name TEXT, source_domain TEXT, tier INTEGER, "
        "description TEXT, pub_date TEXT)")
    conn.execute(
        "INSERT INTO competitor_news (brand, kind, title, url, source_name, "
        "source_domain, tier, description, pub_date) VALUES "
        "('노보', 'competitor', '마운자로 급여 논의', 'https://ex.com/m1', '데일리팜', "
        "'dailypharm.com', 1, '마운자로 급여 검토 본격화', '2026-07-01')")
    conn.commit()
    conn.close()

    stats = B.backfill_signals(db_path=db)
    assert stats["inserted"] == 1

    conn = sqlite3.connect(db)
    sid = conn.execute(
        "SELECT session_id FROM amjilsim_media_signals WHERE url='https://ex.com/m1'"
    ).fetchone()[0]
    conn.close()
    # 2026-07-01 이후 최근접: 암질심 7/8(10) 이 아니라 약평위 7/22(20) 이어야 한다.
    assert sid == 20


def test_backfill_drops_brand_tag_from_match_text(db):
    """기사 표면(title+snippet)에 약 이름이 없으면 brand 태그만으로 매칭 금지."""
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE competitor_news (id INTEGER PRIMARY KEY, brand TEXT, kind TEXT, "
        "title TEXT, url TEXT, source_name TEXT, source_domain TEXT, tier INTEGER, "
        "description TEXT, pub_date TEXT)")
    conn.execute(
        "INSERT INTO competitor_news (brand, kind, title, url, source_name, "
        "source_domain, tier, description, pub_date) VALUES "
        "('키트루다', 'competitor', '제약 산업 일반 뉴스', 'https://ex.com/x1', '데일리팜', "
        "'dailypharm.com', 1, '약 이름 없는 본문', '2026-07-01')")
    conn.commit()
    conn.close()

    stats = B.backfill_signals(db_path=db)
    assert stats["matched"] == 0
    assert stats["unmatched"] == 1


def test_relink_sessions_moves_general_drug_off_amjilsim(db):
    conn = sqlite3.connect(db)
    # 마운자로(일반) 신호가 암질심 세션(10)에 잘못 배정된 상태 (구 committee-agnostic)
    _sig(conn, 2, "m1", "마운자로 급여", "마운자로 본문", "2026-07-01",
         session_id=10, prominence="title")
    # 키트루다(항암) 신호는 암질심 세션(10) 그대로 유지되어야 함
    _sig(conn, 1, "k1", "키트루다 급여", "키트루다 본문", "2026-07-01",
         session_id=10, prominence="title")
    conn.commit()
    conn.close()

    res = B.relink_sessions(db)
    assert res["total"] == 2
    assert res["changed"] == 1

    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT url, session_id FROM amjilsim_media_signals"))
    conn.close()
    assert rows["m1"] == 20   # 일반 → 약평위 7/22
    assert rows["k1"] == 10   # 항암 → 암질심 7/8 유지

    # 멱등 — 재실행 변경 0
    res2 = B.relink_sessions(db)
    assert res2["changed"] == 0


def test_relink_clears_session_when_no_matching_committee(db):
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM amjilsim_sessions WHERE committee_type='YAKPYUNGWI'")
    _sig(conn, 2, "m1", "마운자로 급여", "마운자로 본문", "2026-07-01",
         session_id=10, prominence="title")
    conn.commit()
    conn.close()

    res = B.relink_sessions(db)
    assert res["changed"] == 1
    assert res["cleared"] == 1
    conn = sqlite3.connect(db)
    sid = conn.execute(
        "SELECT session_id FROM amjilsim_media_signals WHERE url='m1'").fetchone()[0]
    conn.close()
    assert sid is None  # 약평위 세션 없음 → 암질심에 남겨두지 않고 NULL


# ── A3: score_bands 단일 소스 ────────────────────────────────────────────────

def test_score_bands_single_source():
    from agents.access_insight.aggregate import _PREDICT_HIGH, _PREDICT_MEDIUM

    bands = score_bands()
    assert bands == {"high": _PREDICT_HIGH, "medium": _PREDICT_MEDIUM}
    assert SCORE_BANDS["high"] == _PREDICT_HIGH
    # 복사본 반환 — 호출측 변형이 원본을 오염시키지 않음
    bands["high"] = 999
    assert SCORE_BANDS["high"] == _PREDICT_HIGH


def test_leaderboard_and_drugs_expose_track_fields(db):
    conn = sqlite3.connect(db)
    _sig(conn, 1, "u1", "키트루다 급여", "키트루다 본문", "2026-06-20", prominence="title")
    _sig(conn, 2, "u2", "마운자로 급여", "마운자로 본문", "2026-06-20", prominence="title")
    conn.commit()
    conn.close()

    items = leaderboard(db_path=db, today="2026-07-01")
    by_id = {i["drug_id"]: i for i in items}
    assert by_id[1]["track"] == "oncology"
    assert by_id[2]["track"] == "general"
    for item in items:
        assert {"key", "label", "date", "status"} <= set(item["stages"][0].keys())
        assert "current_stage" in item

    drugs = list_drugs_with_signals(db_path=db)
    by_id = {i["drug_id"]: i for i in drugs}
    assert by_id[2]["track"] == "general"
    assert by_id[2]["expected_committee"] == "YAKPYUNGWI"
    assert "stages" in by_id[1] and "current_stage" in by_id[1]


def test_insert_signal_without_prominence_column_still_works(db):
    """prominence 컬럼이 없는 (마이그레이션 전) DB 에서도 insert_signal 동작."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ok = B.insert_signal(
        conn, drug_id=1, session_id=None, tier="A", outlet="o", url="nu1",
        title="t", published_at="2026-07-01", snippet="s", signal_type="IR_RELEASE",
        signal_phrases=[], prominence="title",
    )
    assert ok is True
    conn.commit()
    conn.close()
