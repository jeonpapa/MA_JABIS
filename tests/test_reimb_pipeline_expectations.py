"""급여 파이프라인 보드 — 과거 예상일은 '예상일 경과'(overdue), 암질심 예상은 항암제(약평위 미통과)에만.

감사 재현 (2026-10-06, 실제 DB):
- expected_session_id 가 지난 차수를 가리키는 6종(카보메틱스·사이람자·옵디보+여보이·엘라히어주·버제니오정 → 16=2026-07-02,
  림카토주 → 5=2026-07-08)의 timeline 이 그 날짜를 expectedDate 로 내보내 UI 가 "예상 2026-07-02" 로 그림.
- _timeline 이 amj_pass 가 없기만 하면 다음 암질심 일정을 붙여 마운자로(당뇨)·보노칸정(소화성궤양)·타이바소(PAH)·
  이미 약평위를 통과한 스핀라자에도 "암질심 통과 예상 2026-11-11" 이 붙음.

항암 판정은 access_insight 와 같은 기준을 재사용한다: is_oncology 컬럼(있으면 우선) → 실측 증거(암질심 통과일·
암질심 차수 예정/큐) → ATC L01/L02 · efficacy_group '항악성종양제' · indication 암 키워드 · KNOWN_ONCOLOGY_INN.
efficacy_group 이 다른 효능군이면 비항암 확정, 아무 근거도 없으면 미상(예상 없음).
네트워크·실제 DB 금지: 임시 sqlite 파일로 DB_PATH 를 바꾼다.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.reimb_pipeline as rp  # noqa: E402

TODAY = "2026-10-06"

_SCHEMA = """
CREATE TABLE amjilsim_sessions (
    session_id INTEGER PRIMARY KEY, year INTEGER NOT NULL, ordinal_assumed INTEGER NOT NULL DEFAULT 0,
    ordinal_official INTEGER, session_date DATE NOT NULL, status TEXT NOT NULL DEFAULT 'SCHEDULED',
    official_minutes_url TEXT, note TEXT, created_at TEXT, committee_type TEXT NOT NULL DEFAULT 'AMJILSIM'
);
CREATE TABLE amjilsim_drugs (
    drug_id INTEGER PRIMARY KEY, product_slug TEXT, brand_kr TEXT NOT NULL, brand_en TEXT, ingredient_inn TEXT,
    atc TEXT, manufacturer TEXT, msd_flag INTEGER NOT NULL DEFAULT 0, competitor_class TEXT, first_seen_at TEXT,
    tracking_priority TEXT NOT NULL DEFAULT 'generic_new_drug', amjilsim_pass_date DATE, yakpyungwi_pass_date DATE,
    negotiation_status TEXT, indication TEXT, listing_type TEXT, submitted_date DATE, notes TEXT,
    expected_session_id INTEGER, key_issues TEXT, negotiation_complete_date TEXT, negotiation_date_source TEXT,
    nhis_registered_ym TEXT, efficacy_group TEXT{extra}
);
CREATE TABLE amjilsim_drug_queue_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT, drug_id INTEGER NOT NULL, session_id INTEGER, queue_state TEXT NOT NULL,
    queue_entry_date DATE, n_th_attempt INTEGER NOT NULL DEFAULT 1, evidence_url TEXT, observed_at TEXT
);
CREATE TABLE reimb_reports (
    id INTEGER PRIMARY KEY, title TEXT, summary TEXT, highlights_json TEXT, report_type TEXT, committee TEXT,
    year INTEGER, cycle INTEGER, session_date TEXT
);
"""

SESSIONS = [  # 실제 DB 2026-10-06 상태의 부분 복제
    (15, 2026, 6, None, "2026-06-04", "COMPLETED", "YAKPYUNGWI"),
    (16, 2026, 7, None, "2026-07-02", "SCHEDULED", "YAKPYUNGWI"),
    (5, 2026, 6, 6, "2026-07-08", "SCHEDULED", "AMJILSIM"),
    (20, 2026, 11, None, "2026-11-05", "SCHEDULED", "YAKPYUNGWI"),
    (8, 2026, 10, 10, "2026-11-11", "SCHEDULED", "AMJILSIM"),
]

# (drug_id, brand_kr, ingredient, atc, efficacy_group, amj_pass, yak_pass, neg_status, indication, expected_session_id)
DRUGS = [
    (39, "카보메틱스", "cabozantinib", None, None, "2026-01-21", None, "IN_PROGRESS", "신장세포암 단독 확대", 16),
    (44, "버제니오정", "abemaciclib", None, None, "2026-05-27", None, "IN_PROGRESS", "HR+/HER2- 조기 유방암 보조", 16),
    (48, "림카토주", "anbalcabtagene autoleucel", None, None, None, None, None, "r/r DLBCL·PMBCL CAR-T", 5),
    (23, "마운자로", "tirzepatide", None, "당뇨병용제", None, "2025-12-04", "REJECTED", "제2형 당뇨병", None),
    (12, "보노칸정", "vonoprazan", None, "소화성궤양용제", None, "2026-05-07", "IN_PROGRESS", "위궤양", None),
    (46, "타이바소 흡입액", "treprostinil", None, None, None, None, None, None, None),
    (21, "스핀라자", "nusinersen", None, None, None, "2026-01-15", "IN_PROGRESS", "SMA 확대", None),
    (90, "신규항암제", "x-mab", "L01XC99", "항악성종양제", None, None, None, "전이성 위암", None),
]


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 6)


def _make_db(path: Path, *, with_is_oncology: bool = False) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA.format(extra=", is_oncology INTEGER" if with_is_oncology else ""))
    conn.executemany("INSERT INTO amjilsim_sessions (session_id, year, ordinal_assumed, ordinal_official, "
                     "session_date, status, committee_type) VALUES (?,?,?,?,?,?,?)", SESSIONS)
    conn.executemany("INSERT INTO amjilsim_drugs (drug_id, brand_kr, ingredient_inn, atc, efficacy_group, "
                     "amjilsim_pass_date, yakpyungwi_pass_date, negotiation_status, indication, expected_session_id) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)", DRUGS)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db(tmp_path, monkeypatch) -> Path:
    path = _make_db(tmp_path / "reimb.db")
    monkeypatch.setattr(rp, "DB_PATH", path)
    monkeypatch.setattr(rp, "date", _FrozenDate)
    return path


def _board() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for stage in rp.get_pipeline(include_completed=True)["stages"]:
        for d in stage["drugs"]:
            out[d["name"]] = {**d, "_stage": stage["id"]}
    return out


def _step(drug: dict, phase: str) -> dict:
    return next(t for t in drug["timeline"] if t["phase"] == phase)


# ── 1. 지난 차수를 가리키는 expected_session_id → 예상일 경과 ────────────────

def test_past_expected_session_is_marked_overdue_not_forecast(db):
    board = _board()
    for name in ("카보메틱스", "버제니오정"):
        step = _step(board[name], "약평위 통과")
        assert step["expectedDate"] == "2026-07-02" and step["expectedStatus"] == "overdue"
        assert step["status"] == "expected" and step["date"] is None
        assert board[name]["expectedStatus"] == "overdue"
    step = _step(board["림카토주"], "암질심 통과")
    assert step["expectedDate"] == "2026-07-08" and step["expectedStatus"] == "overdue"
    assert board["림카토주"]["expectedStatus"] == "overdue"


def test_future_expected_session_is_forecast(db):
    board = _board()
    step = _step(board["신규항암제"], "암질심 통과")
    assert step["expectedDate"] == "2026-11-11" and step["expectedStatus"] == "forecast"
    assert board["신규항암제"]["expectedStatus"] == "forecast"


# ── 2. 암질심 예상은 항암제(약평위 미통과)에만 ─────────────────────────────────

def test_non_oncology_drugs_get_no_amjilsim_expectation(db):
    board = _board()
    for name in ("마운자로", "보노칸정"):              # efficacy_group 이 비항암 효능군 → 해당 없음
        step = _step(board[name], "암질심 통과")
        assert step["expectedDate"] is None and step["status"] == "not_applicable", name
    step = _step(board["타이바소 흡입액"], "암질심 통과")  # 아무 근거 없음(미상) → 예상 없음
    assert step["expectedDate"] is None and step["status"] == "upcoming"


def test_already_passed_yakpyungwi_gets_no_amjilsim_expectation(db):
    step = _step(_board()["스핀라자"], "암질심 통과")
    assert step["expectedDate"] is None and step["status"] in ("upcoming", "not_applicable")
    assert _step(_board()["스핀라자"], "약평위 통과")["status"] == "done"


def test_oncology_evidence_cascade():
    assert rp.oncology_track({"is_oncology": None, "efficacy_group": "항악성종양제"}) is True
    assert rp.oncology_track({"is_oncology": None, "atc": "L01XC18"}) is True
    assert rp.oncology_track({"is_oncology": None, "indication": "r/r DLBCL·PMBCL CAR-T"}) is True
    assert rp.oncology_track({"is_oncology": None, "amjilsim_pass_date": "2026-01-21"}) is True
    assert rp.oncology_track({"is_oncology": None, "ingredient_inn": "osimertinib"}) is True
    assert rp.oncology_track({"is_oncology": None, "efficacy_group": "당뇨병용제", "indication": "제2형 당뇨병"}) is False
    assert rp.oncology_track({"is_oncology": None}) is None
    # 컬럼이 채워져 있으면 그것이 우선 (access_insight backfill 결과)
    assert rp.oncology_track({"is_oncology": 0, "indication": "위암"}) is False
    assert rp.oncology_track({"is_oncology": 1, "efficacy_group": "당뇨병용제"}) is True


def test_is_oncology_column_wins_when_present(tmp_path, monkeypatch):
    path = _make_db(tmp_path / "onco.db", with_is_oncology=True)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE amjilsim_drugs SET is_oncology=0 WHERE drug_id=90")   # 백필이 비항암이라 함 → 예상 없음
    conn.execute("UPDATE amjilsim_drugs SET is_oncology=1 WHERE drug_id=46")   # 백필이 항암이라 함 → 다음 암질심 예상
    conn.commit()
    conn.close()
    monkeypatch.setattr(rp, "DB_PATH", path)
    monkeypatch.setattr(rp, "date", _FrozenDate)
    board = _board()
    assert _step(board["신규항암제"], "암질심 통과")["status"] == "not_applicable"
    step = _step(board["타이바소 흡입액"], "암질심 통과")
    assert step["expectedDate"] == "2026-11-11" and step["expectedStatus"] == "forecast"
