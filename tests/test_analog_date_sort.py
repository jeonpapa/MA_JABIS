"""등재 아날로그 — 날짜 정렬(약평위 날짜·식약처 허가일·최초 약가 등재일) 회귀 테스트.

임시 DB 를 쓰므로 운영 DB 없이 돈다. 확인하는 것:
  - 정렬 키별 오름/내림차순 (허가일은 'YYYY.MM.DD' 점 표기, 나머지는 'YYYY-MM-DD')
  - 값 없는 행은 방향과 무관하게 항상 맨 뒤
  - 필터만 쓰는 목록에서도 SQL 정렬이 실제로 반영된다(이전에는 IN 재조회가 순서를 깨 id 순으로 나왔다)
  - 허용되지 않은 정렬 컬럼은 무시
  - 'YYYY-01-01' 은 연도만 아는 자리표시값 → session_date_precision='year'
"""
import sqlite3

import pytest

from agents.analog import store


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.ensure_schema()
    rows = [
        # (brand, session_date, ordinal, permit, reimb)
        ("가약", "2020-03-12", 3, "2019.05.01", "2020-09-01"),
        ("나약", "2022-07-07", 7, "2018.01.15", None),
        ("다약", "2008-01-01", 4, None, "2009-02-01"),          # 연도만 아는 자리표시 날짜
        ("라약", "2022-07-07", 8, "2021.12.31", "2022-12-01"),   # 같은 날짜 → 차수로 구분
        ("마약", None, None, "2016.06.10", None),                # 날짜 없음
    ]
    with sqlite3.connect(store.DB_PATH) as c:
        for i, (b, sd, o, p, r) in enumerate(rows, 1):
            c.execute(
                "INSERT INTO analog_reports (file_name, file_hash, brand_name, brand_name_raw, session_date, ordinal, "
                "mfds_permit_date, first_reimbursement_date, review_result, pdf_extractable) VALUES (?,?,?,?,?,?,?,?,?,1)",
                (f"{b}.pdf", f"h{i}", b, b, sd, o, p, r, "APPROVED"),
            )
    return store


def _brands(res):
    return [r["brand_name"] for r in res["results"]]


def test_session_date_desc_and_asc(db):
    assert _brands(db.search(sort="session_date", order="desc", limit=10)) == ["라약", "나약", "가약", "다약", "마약"]
    assert _brands(db.search(sort="session_date", order="asc", limit=10)) == ["다약", "가약", "나약", "라약", "마약"]


def test_permit_date_uses_dot_format_and_keeps_empty_last(db):
    asc = _brands(db.search(sort="mfds_permit_date", order="asc", limit=10))
    assert asc == ["마약", "나약", "가약", "라약", "다약"]            # 2016 < 2018 < 2019 < 2021, 값 없는 다약은 맨 뒤
    desc = _brands(db.search(sort="mfds_permit_date", order="desc", limit=10))
    assert desc == ["라약", "가약", "나약", "마약", "다약"]            # 내림차순에서도 값 없는 행은 맨 뒤


def test_first_reimbursement_sort_puts_missing_last_in_both_directions(db):
    for order in ("asc", "desc"):
        res = _brands(db.search(sort="first_reimbursement_date", order=order, limit=10))
        assert set(res[-2:]) == {"나약", "마약"}


def test_default_filter_listing_is_date_desc_not_id_order(db):
    """회귀: 필터만 쓰는 목록은 ORDER BY session_date DESC 인데 IN 재조회가 순서를 깨 id 순으로 나왔다."""
    assert _brands(db.search(limit=10))[:3] == ["라약", "나약", "가약"]


def test_unknown_sort_column_is_ignored(db):
    res = db.search(sort="brand_name; DROP TABLE analog_reports", limit=10)
    assert "sort" not in res and len(res["results"]) == 5


def test_year_only_placeholder_is_flagged(db):
    by = {r["brand_name"]: r for r in db.search(limit=10)["results"]}
    assert by["다약"]["session_date_precision"] == "year"
    assert by["가약"]["session_date_precision"] == "day"
    assert by["마약"]["session_date_precision"] is None
