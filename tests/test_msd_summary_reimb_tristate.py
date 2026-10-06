"""Home Keytruda 카드 — 급여 / 미급여 / 미확인 3분할 (감사 재현: '급여 4 / 미급여 62' 과소 표시).

HIRA 급여 체크리스트(indication_reimbursement)는 Keytruda 66개 적응증 중 15개만 덮는다. 체크리스트 행이 없는 51개를
'미급여' 로 세면 실제보다 비급여가 훨씬 많아 보인다. 체크리스트 행이 없는 적응증은 '미확인' 이다 — 급여/미급여 판정 기준
자체는 바꾸지 않는다 (is_reimbursed=1 만 급여).
네트워크·실제 DB 금지: Flask test_client + 임시 DrugPriceDB.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s  # noqa: E402
from agents.db import DrugPriceDB  # noqa: E402

NOW = "2026-10-06T00:00:00"


@pytest.fixture
def api_db(monkeypatch, tmp_path):
    db = DrugPriceDB(tmp_path / "api.db")
    monkeypatch.setattr(s, "db", db)
    with db._connect() as conn:
        for iid, disease in (("keytruda_a", "NSCLC"), ("keytruda_b", "MEL"), ("keytruda_c", "HNSCC"),
                             ("keytruda_d", "UC")):
            conn.execute("INSERT INTO indications_master (indication_id, product, disease, line_of_therapy, "
                         "created_at, updated_at) VALUES (?,?,?,?,?,?)", (iid, "keytruda", disease, "1L", NOW, NOW))
            conn.execute("INSERT INTO indications_by_agency (indication_id, agency, approval_date, fetched_at) "
                         "VALUES (?,?,?,?)", (iid, "MFDS", "2024-01-01", NOW))
        # a: 급여 / b: 체크리스트 있음·미급여 / c, d: 체크리스트 없음 → 미확인
        conn.execute("INSERT INTO indication_reimbursement (indication_id, is_reimbursed, effective_date, updated_at) "
                     "VALUES (?,?,?,?)", ("keytruda_a", 1, "2024-06-01", NOW))
        conn.execute("INSERT INTO indication_reimbursement (indication_id, is_reimbursed, updated_at) "
                     "VALUES (?,?,?)", ("keytruda_b", 0, NOW))
        conn.commit()
    return db


def test_summary_splits_reimbursed_not_reimbursed_unverified(api_db):
    body = s.app.test_client().get("/api/msd/summary").get_json()
    k = body["keytruda"]
    assert k["total_indications"] == 4
    assert k["reimbursed_indications"] == 1            # 급여 기준 불변: is_reimbursed=1 만
    assert k["not_reimbursed_indications"] == 1        # 체크리스트가 '미급여' 라고 말한 것만
    assert k["unverified_reimbursement"] == 2          # 체크리스트 행 없음 — 미급여가 아니라 미확인
    assert k["checklist_covered"] == 2
    # 하위호환: pending_reimbursement 는 '급여 아님' 전체(미급여 + 미확인) 그대로
    assert k["pending_reimbursement"] == 3


def test_items_carry_tristate_and_keep_is_reimbursed_boolean(api_db):
    body = s.app.test_client().get("/api/msd/summary").get_json()
    by = {i["id"]: i for i in body["keytruda"]["items"]}
    assert by["keytruda_a"]["reimbursement_status"] == "reimbursed" and by["keytruda_a"]["is_reimbursed"] is True
    assert by["keytruda_b"]["reimbursement_status"] == "not_reimbursed" and by["keytruda_b"]["is_reimbursed"] is False
    assert by["keytruda_c"]["reimbursement_status"] == "unverified" and by["keytruda_c"]["is_reimbursed"] is False
    assert by["keytruda_c"]["reimbursement_checked"] is False and by["keytruda_b"]["reimbursement_checked"] is True
