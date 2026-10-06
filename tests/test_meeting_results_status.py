"""지난 SCHEDULED 세션의 결과 모달 — '결과 미반영' 을 '심의 약제 없음' 과 구분한다.

감사 재현 (2026-10-06 실제 DB): 날짜가 지났는데 status 가 SCHEDULED 인 세션 7개(07-02·07-08·08-06·08-19·09-03·09-30·10-01)에
/api/reimbursement/meetings/<id>/results 가 drugs=[] · report=null · totals 전부 0 을 돌려 "이 회차는 심의 약제가 없었다" 로
읽혔다. 결과가 아직 적재되지 않은 것(not_ingested)과 적재했는데 0건(empty)은 다른 사실이다.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.reimb_pipeline as rp  # noqa: E402
from tests.test_reimb_pipeline_expectations import _FrozenDate, _make_db  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch) -> Path:
    path = _make_db(tmp_path / "reimb.db")
    conn = sqlite3.connect(path)
    # 15(2026-06-04, COMPLETED) 에 결과 1건 / 22: COMPLETED 인데 큐 0건(실제로 심의 약제 없음)
    conn.execute("INSERT INTO amjilsim_drug_queue_status (drug_id, session_id, queue_state, observed_at) "
                 "VALUES (39, 15, 'APPROVED', '2026-06-04T10:00:00')")
    conn.execute("INSERT INTO amjilsim_sessions (session_id, year, ordinal_assumed, session_date, status, committee_type) "
                 "VALUES (22, 2026, 5, '2026-05-07', 'COMPLETED', 'YAKPYUNGWI')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(rp, "DB_PATH", path)
    monkeypatch.setattr(rp, "date", _FrozenDate)
    return path


def test_past_scheduled_session_is_not_ingested_not_empty(db):
    res = rp.get_meeting_results(16)                   # 2026-07-02, SCHEDULED, 큐 0건
    assert res["meeting"]["status"] == "SCHEDULED" and res["drugs"] == [] and res["report"] is None
    assert res["resultsStatus"] == "not_ingested"
    assert "미반영" in res["resultsNote"]


def test_completed_session_with_rows_is_available(db):
    res = rp.get_meeting_results(15)
    assert res["resultsStatus"] == "available" and len(res["drugs"]) == 1 and res["resultsNote"] is None


def test_completed_session_with_zero_rows_is_empty(db):
    """적재했는데 0건 — '심의 약제 없음' 은 COMPLETED 일 때만 말할 수 있다."""
    res = rp.get_meeting_results(22)
    assert res["resultsStatus"] == "empty" and res["drugs"] == []


def test_future_scheduled_session_is_upcoming(db):
    res = rp.get_meeting_results(8)                    # 2026-11-11 (오늘 2026-10-06 기준 미래)
    assert res["resultsStatus"] == "upcoming"
