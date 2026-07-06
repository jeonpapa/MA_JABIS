"""Access Insight S2 — momentum 집계 + journey/leaderboard + prediction_audit 테스트."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agents.access_insight.aggregate import (
    drug_momentum,
    journey,
    leaderboard,
    list_drugs_with_signals,
    record_prediction,
    reconcile_predictions,
)


_SCHEMA = """
CREATE TABLE amjilsim_drugs (
    drug_id INTEGER PRIMARY KEY,
    product_slug TEXT,
    brand_kr TEXT NOT NULL,
    brand_en TEXT,
    ingredient_inn TEXT,
    msd_flag INTEGER NOT NULL DEFAULT 0,
    competitor_class TEXT,
    tracking_priority TEXT NOT NULL DEFAULT 'generic_new_drug',
    amjilsim_pass_date DATE,
    yakpyungwi_pass_date DATE,
    negotiation_status TEXT,
    indication TEXT,
    expected_session_id INTEGER
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
    source_verified TEXT NOT NULL DEFAULT 'headline_only'
);

CREATE TABLE amjilsim_prediction_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES amjilsim_sessions(session_id),
    drug_id INTEGER NOT NULL REFERENCES amjilsim_drugs(drug_id),
    predicted_state TEXT NOT NULL,
    predicted_score REAL,
    actual_state TEXT,
    match_type TEXT CHECK (match_type IN
        ('TRUE_POSITIVE','FALSE_POSITIVE','TRUE_NEGATIVE','FALSE_NEGATIVE')),
    pattern_hits TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE analog_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_name TEXT,
    generic_name_en TEXT,
    mfds_permit_date TEXT,
    amjilsim_date TEXT,
    pass_session_date TEXT,
    first_reimbursement_date TEXT
);

CREATE TABLE indications_master (
    indication_id TEXT PRIMARY KEY,
    product TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE indication_reimbursement (
    indication_id TEXT PRIMARY KEY,
    is_reimbursed INTEGER NOT NULL DEFAULT 0,
    effective_date TEXT,
    notice_date TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "test_access_insight.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)

    # 세션: drug 1 의 기준 세션은 2026-06-15 (AMJILSIM).
    conn.execute(
        "INSERT INTO amjilsim_sessions (session_id, year, session_date, status, committee_type) "
        "VALUES (1, 2026, '2026-06-15', 'SCHEDULED', 'AMJILSIM')"
    )
    # 완료된 세션(드럭2용, reconcile 테스트)
    conn.execute(
        "INSERT INTO amjilsim_sessions (session_id, year, session_date, status, committee_type) "
        "VALUES (2, 2026, '2026-03-01', 'COMPLETED', 'YAKPYUNGWI')"
    )

    # 약제 1: 키트루다st ub — expected_session_id=1, momentum 신호 다수.
    conn.execute(
        """
        INSERT INTO amjilsim_drugs
            (drug_id, product_slug, brand_kr, expected_session_id,
             amjilsim_pass_date, yakpyungwi_pass_date)
        VALUES (1, 'testdrug', '테스트약', 1, NULL, NULL)
        """
    )
    # 약제 2: signal 적음 + 세션 완료 + 약평위 통과(TP 테스트용).
    conn.execute(
        """
        INSERT INTO amjilsim_drugs
            (drug_id, product_slug, brand_kr, expected_session_id,
             amjilsim_pass_date, yakpyungwi_pass_date)
        VALUES (2, 'testdrug2', '약한약', 2, '2026-01-01', '2026-03-01')
        """
    )
    # 약제 3: signal 없음 → leaderboard 대상 아님(집계 skip 확인용).
    conn.execute(
        """
        INSERT INTO amjilsim_drugs
            (drug_id, product_slug, brand_kr, expected_session_id)
        VALUES (3, 'testdrug3', '무신호약', NULL)
        """
    )

    def _sig(drug_id, session_id, pub, stype, weight, tier="A", title="t", url_suffix=""):
        conn.execute(
            """
            INSERT INTO amjilsim_media_signals
                (drug_id, session_id, tier, outlet, url, title, published_at,
                 signal_type, weight)
            VALUES (?, ?, ?, 'outlet', ?, ?, ?, ?, ?)
            """,
            (drug_id, session_id, tier, f"https://example.com/{drug_id}/{pub}{url_suffix}",
             title, pub, stype, weight),
        )

    # window_days=90 기준 [2026-03-17, 2026-06-15] 안의 신호들.
    # 세션에 가까운(최근) 신호 다수 + 다양한 유형 → momentum/engage_diversity 높게.
    _sig(1, 1, "2026-06-14", "GOV_STATEMENT", 1.5)   # 세션 1일전 (recency ~1.0)
    _sig(1, 1, "2026-06-10", "PATIENT_PETITION", 1.4)  # 5일전
    _sig(1, 1, "2026-06-05", "KOL_OPINION", 1.2)       # 10일전
    _sig(1, 1, "2026-05-20", "IR_RELEASE", 0.8)        # 26일전 (recent_30d 경계 안)
    _sig(1, 1, "2026-04-01", "IR_RELEASE", 0.8)        # 75일전 (prior_30d 밖, window 안)
    # 윈도우 밖(너무 이른) 신호 — 집계에서 제외되어야 함.
    _sig(1, 1, "2026-01-01", "GOV_STATEMENT", 1.5, url_suffix="-outside")

    # 약제 2: 신호 1건뿐, 세션(완료)과 가까움 — momentum 낮음.
    _sig(2, 2, "2026-02-28", "IR_RELEASE", 0.8)

    conn.commit()
    conn.close()
    return str(path)


def test_drug_momentum_basic(db):
    m = drug_momentum(1, db_path=db, window_days=90)
    assert m["drug_id"] == 1
    assert m["brand_kr"] == "테스트약"
    assert m["expected_session"]["session_id"] == 1
    assert m["expected_session"]["session_date"] == "2026-06-15"
    # 윈도우 밖 신호(2026-01-01) 는 제외 → 5건만 카운트.
    assert m["signal_count"] == 5
    assert m["by_type"] == {
        "GOV_STATEMENT": 1,
        "PATIENT_PETITION": 1,
        "KOL_OPINION": 1,
        "IR_RELEASE": 2,
        "RESULT_REPORT": 0,
        "PRE_AGENDA_LEAK": 0,
        "UNCLASSIFIED": 0,  # B7 저신뢰 미분류 버킷 (항상 0 이라도 노출)
    }
    # 4종의 서로 다른 signal_type 이 등장 (GOV/PATIENT/KOL/IR).
    assert m["engage_diversity"] == 4
    assert m["weighted_sum"] > 0
    assert m["momentum_score"] > 0


def test_recency_weighting_favors_closer_signals(db):
    """동일 weight 라도 세션에 가까운 신호가 더 큰 기여를 해야 한다."""
    m = drug_momentum(1, db_path=db, window_days=90)
    # IR_RELEASE 신호 2건(weight=0.8 동일): 2026-05-20(26일전) vs 2026-04-01(75일전).
    # weighted_sum 전체에서 이를 직접 분리 검증하기 위해 별도 계산으로 재확인.
    from agents.access_insight.aggregate import _recency_factor
    from datetime import date

    ref = date(2026, 6, 15)
    close = _recency_factor(date(2026, 5, 20), ref, 90)
    far = _recency_factor(date(2026, 4, 1), ref, 90)
    assert close > far


def test_trend_up_when_recent_denser(db):
    m = drug_momentum(1, db_path=db, window_days=90)
    # recent_30d(<=30일전): 06-14,06-10,06-05,05-20 = 4건. prior_30d(31~60일전): 0건.
    assert m["trend"]["recent_30d"] == 4
    assert m["trend"]["prior_30d"] == 0
    assert m["trend"]["direction"] == "up"


def test_drug_momentum_unknown_drug_raises(db):
    with pytest.raises(ValueError):
        drug_momentum(999, db_path=db)


def test_drug_momentum_no_session_falls_back_to_as_of(db):
    # 약제 3 은 expected_session_id 없음 + signal 없음 → ref_date=None, 전부 0.
    m = drug_momentum(3, db_path=db, window_days=90, as_of="2026-06-15")
    assert m["signal_count"] == 0
    assert m["momentum_score"] == 0
    assert m["engage_diversity"] == 0


def test_leaderboard_ranks_by_momentum(db):
    items = leaderboard(db_path=db, window_days=90, limit=10, today="2026-06-01")
    ids = [i["drug_id"] for i in items]
    # signal 없는 drug 3 은 leaderboard 대상에서 제외.
    assert 3 not in ids
    # drug 1 이 momentum 이 훨씬 높으므로 1위.
    assert ids[0] == 1
    scores = [i["momentum_score"] for i in items]
    assert scores == sorted(scores, reverse=True)


def test_leaderboard_session_imminent_flag(db):
    # drug1 세션 2026-06-15, today=2026-06-01 → 14일 이내(45일 이내) → imminent True.
    items = leaderboard(db_path=db, window_days=90, limit=10, today="2026-06-01")
    by_id = {i["drug_id"]: i for i in items}
    assert by_id[1]["session_imminent"] is True

    # today 를 세션보다 훨씬 이전으로(200일전) 주면 imminent False.
    items_far = leaderboard(db_path=db, window_days=90, limit=10, today="2025-11-01")
    by_id_far = {i["drug_id"]: i for i in items_far}
    assert by_id_far[1]["session_imminent"] is False


def test_leaderboard_default_today_is_deterministic(db):
    # today 미지정 시 wall-clock 이 아니라 데이터 내 최신 published_at 로 fallback.
    items = leaderboard(db_path=db, window_days=90, limit=10)
    assert isinstance(items, list)
    assert len(items) == 2  # drug1, drug2 만 (signal 있는 약제)


def test_list_drugs_with_signals(db):
    items = list_drugs_with_signals(db_path=db)
    ids = {i["drug_id"] for i in items}
    assert ids == {1, 2}
    by_id = {i["drug_id"]: i for i in items}
    assert by_id[1]["signal_count"] == 6  # 윈도우 밖 신호 포함 전체 카운트
    assert by_id[1]["brand_kr"] == "테스트약"


def test_journey_returns_signals_sessions_milestones_sorted(db):
    j = journey(1, db_path=db)
    assert j["drug_id"] == 1
    assert j["brand_kr"] == "테스트약"
    # signals 는 published_at 오름차순.
    dates = [s["published_at"] for s in j["signals"]]
    assert dates == sorted(dates)
    assert len(j["signals"]) == 6
    assert j["signals"][0]["signal_type"] == "GOV_STATEMENT"

    # sessions: drug1 은 session_id=1 만 참조.
    session_ids = {s["session_id"] for s in j["sessions"]}
    assert session_ids == {1}
    assert j["sessions"][0]["session_date"] == "2026-06-15"

    assert "milestones" in j
    assert j["milestones"]["amjilsim_pass_date"] is None


def test_journey_milestones_from_analog_and_reimbursement(db):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO analog_reports (brand_name, mfds_permit_date, first_reimbursement_date) "
        "VALUES ('테스트약', '2024-01-10', '2024-06-01')"
    )
    conn.execute(
        "INSERT INTO indications_master (indication_id, product, created_at, updated_at) "
        "VALUES ('ind-1', 'testdrug', datetime('now'), datetime('now'))"
    )
    conn.execute(
        "INSERT INTO indication_reimbursement (indication_id, is_reimbursed, effective_date) "
        "VALUES ('ind-1', 1, '2024-07-01')"
    )
    conn.commit()
    conn.close()

    j = journey(1, db_path=db)
    assert j["milestones"]["mfds_permit_date"] == "2024-01-10"
    assert j["milestones"]["first_reimbursement_date"] == "2024-06-01"
    assert j["milestones"]["reimbursement_effective_date"] == "2024-07-01"


def test_record_prediction_inserts_row(db):
    result = record_prediction(1, db_path=db, window_days=90)
    assert result["drug_id"] == 1
    assert result["session_id"] == 1
    assert result["predicted_state"] in ("HIGH", "MEDIUM", "LOW")

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT session_id, drug_id, predicted_state, predicted_score, pattern_hits "
        "FROM amjilsim_prediction_audit WHERE id = ?",
        (result["id"],),
    ).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == 1
    assert row[2] == result["predicted_state"]
    assert row[4] is not None


def test_record_prediction_without_expected_session_raises(db):
    with pytest.raises(ValueError):
        record_prediction(3, db_path=db, as_of="2026-06-15")


def test_reconcile_predictions_marks_true_positive(db):
    # 약제 2: session 2(COMPLETED) 대상 예측을 강제로 HIGH 로 기록 후 reconcile.
    conn = sqlite3.connect(db)
    conn.execute(
        """
        INSERT INTO amjilsim_prediction_audit
            (session_id, drug_id, predicted_state, predicted_score)
        VALUES (2, 2, 'HIGH', 5.0)
        """
    )
    conn.commit()
    conn.close()

    result = reconcile_predictions(db_path=db)
    assert result["reconciled"] == 1

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT actual_state, match_type FROM amjilsim_prediction_audit "
        "WHERE drug_id = 2 AND session_id = 2"
    ).fetchone()
    conn.close()
    # drug2 의 yakpyungwi_pass_date = '2026-03-01' (통과) → HIGH 예측과 일치 → TP.
    assert row[0] == "PASSED"
    assert row[1] == "TRUE_POSITIVE"


def test_reconcile_predictions_skips_scheduled_sessions(db):
    # drug1 의 세션(1)은 SCHEDULED 상태 → reconcile 대상 아님.
    record_prediction(1, db_path=db, window_days=90)
    result = reconcile_predictions(db_path=db)
    assert result["reconciled"] == 0
