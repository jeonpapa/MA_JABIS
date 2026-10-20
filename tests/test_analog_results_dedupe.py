"""등재 아날로그 — 심의결과 일관성(조건부 급여)·중복 병합 회귀 테스트 (2026-10-20).

사례: 울토미리스(수용 후 확정)와 빔젤릭스(수용 대기)는 판정문이 같은데 '급여 적정' / '급여 불인정'으로 갈렸고,
씨트렐린구강붕해정은 같은 약평위가 두 소스로 들어와 두 줄(함량 꼬리 유무만 다름)로 보였다.
"""
import sqlite3

import pytest

from agents.analog import dedupe, pdf_parser, store
from agents.analog.export_calibration import label_of
from agents.ingest.yakpyungwi_meta_import import RESULT_NORM

COND_TEXT = ("평가금액 이하 수용 시 급여의 적정성이 있음 ○ 신청품은 대체약제 대비 소요비용이 고가로 비용효과성이 불분명하여 비급여함. "
             "- 단, 제약사가 대체약제의 가중평균가로 환산된 금액 이하를 수용할 경우, 급여의 적정성이 있으며")
ACCEPTED_ONLY = "최종 결과 ○ 제약사가 원 이하를 수용하였으므로 급여의 적정성이 있음."
ULTOMIRIS = ("최종 결과 ○ 제약사가 원/병 이하를 수용하였으므로 급여의 적정성이 있음 ※ 2021년 제 2차 약제급여평가위원회 평가결과: "
             "평가금액 이하 수용시 급여의 적정성이 있음 ○ 신청품은 비급여함. - 단, 제약사가 대체약제의 가중평균가로 환산된 금액 이하를 수용할 경우 급여의 적정성이 있으며")


def test_conditional_price_is_one_result_regardless_of_acceptance_state():
    """빔젤릭스(수용 대기)와 울토미리스(수용 후 확정)의 판정문은 같은 결과여야 한다."""
    assert pdf_parser._extract_review_result(COND_TEXT, COND_TEXT) == "CONDITIONAL_PRICE"
    assert pdf_parser._extract_review_result(ULTOMIRIS, ULTOMIRIS) == "CONDITIONAL_PRICE"
    assert pdf_parser._extract_review_result("조건부 비급여 ○ 신청품은 …비급여함.", "") == "CONDITIONAL_PRICE"


def test_plain_and_other_results_are_unchanged():
    assert pdf_parser._extract_review_result("급여의 적정성이 있음", "") == "APPROVED"
    assert pdf_parser._extract_review_result(ACCEPTED_ONLY, "") == "APPROVED"          # 심의 시점 조건 문구 없이 수용 후 결과만
    assert pdf_parser._extract_review_result("조건부 급여의 적정성이 있음", "") == "CONDITIONAL_APPROVED"
    assert pdf_parser._extract_review_result("급여의 적정성이 없음", "") == "REJECTED"


def test_hira_conditional_non_benefit_is_not_rejected_and_label_excludes_it_from_calibration():
    assert RESULT_NORM["조건부비급여"] == "CONDITIONAL_PRICE" and RESULT_NORM["비급여"] == "REJECTED"
    assert label_of("CONDITIONAL_PRICE") is None             # 수용 여부로 통과/미통과가 갈리므로 보정 라벨에서 제외
    assert label_of("REJECTED") == "미통과"


# ── 중복 병합 ────────────────────────────────────────────────────────────────

def test_product_key_ignores_strength_tail_spaces_and_bundle_suffix():
    k = dedupe.product_key
    assert k("씨트렐린구강붕해정5밀리그램") == k("씨트렐린구강붕해정") == "씨트렐린구강붕해정"
    assert k("피블라스트 스프레이") == k("피블라스트스프레이")
    assert k("레시노원주 등 5품목유영제약 등 5개사") == "레시노원주"
    assert dedupe.clean_brand("루센티스주10mg-ml") == "루센티스주"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.ensure_schema()
    rows = [  # (file, brand, dosage, session, ordinal, manufacturer, post_url)
        ("씨트렐린구강붕해정5밀리그램_조건부급여_2025년 제7차_평가결과.pdf", "씨트렐린구강붕해정5밀리그램", "5밀리그램", "2025-07-11", 7, None, None),
        ("평가결과_씨트렐린구강붕해정5밀리그램(탈티렐린수화물).pdf", "씨트렐린구강붕해정", "5mg", "2025-07-11", None, "에이치엘비제약(주)", "https://x/47077"),
        ("씨트렐린_비급여_18-2_평가결과.pdf", "씨트렐린구강붕해정5밀리그램", "5밀리그램", "2018-01-25", 2, None, None),      # 다른 회차(재심의)
        ("트렐리지100_평가결과.pdf", "트렐리지100엘립타", "30회", "2023-11-09", 12, None, None),
        ("트렐리지200_평가결과.pdf", "트렐리지200엘립타", "30회", "2023-11-09", 12, None, None),                          # 다른 규격 — 합치면 안 됨
        ("A_4차.pdf", "비스메드점안액", None, "2009-01-01", 5, None, None),
        ("A_13차.pdf", "비스메드점안액", None, "2009-01-01", 13, None, None),                                            # 연도만 아는 날짜·다른 차수
        ("아프레_소프레.pdf", "소프레정", "30mg", "2024-10-10", 10, None, None),
        ("아프레_압솔라.pdf", "압솔라정", "30mg", "2024-10-10", 10, None, None),                                          # 다른 상표 — 유지
    ]
    with sqlite3.connect(store.DB_PATH) as c:
        for i, (f, b, d, sd, o, mf, pu) in enumerate(rows, 1):
            c.execute("INSERT INTO analog_reports (file_name, file_hash, brand_name, brand_name_raw, dosage, session_date, ordinal, manufacturer, "
                      "post_url, review_result, pdf_extractable) VALUES (?,?,?,?,?,?,?,?,?, 'CONDITIONAL_PRICE', 1)", (f, f"h{i}", b, b, d, sd, o, mf, pu))
    return store.DB_PATH


def test_plan_merges_only_same_product_same_session(db):
    with sqlite3.connect(db) as c:
        pl = dedupe.plan(c)
    pairs = [(m["keep_brand"], m["drop_brand"]) for m in pl["merges"]]
    assert pairs == [("씨트렐린구강붕해정5밀리그램", "씨트렐린구강붕해정")]          # 기존 수집분을 남기고 'DREC 추가'분을 흡수
    m = pl["merges"][0]
    assert m["fills"].get("manufacturer") == "에이치엘비제약(주)" and m["fills"].get("post_url") == "https://x/47077"
    assert any(r["new"] == "씨트렐린구강붕해정" for r in pl["renames"])               # 표기 통일: 함량 꼬리 제거


def test_apply_plan_keeps_other_brands_and_leaves_alias(db):
    with sqlite3.connect(db) as c:
        pl = dedupe.plan(c)
        res = dedupe.apply_plan(c, pl)
        names = [r[0] for r in c.execute("SELECT brand_name FROM analog_reports ORDER BY id")]
        alias = c.execute("SELECT file_name, canonical_id FROM analog_file_alias").fetchall()
    assert res["merged"] == 1 and len(names) == 8
    assert names.count("소프레정") == 1 and names.count("압솔라정") == 1               # 여러 상표는 각각 유지
    assert names.count("트렐리지100엘립타") == 1 and names.count("트렐리지200엘립타") == 1
    assert names.count("비스메드점안액") == 2                                       # 서로 다른 차수
    assert alias[0][0].startswith("평가결과_씨트렐린") and alias[0][1] == 1


def test_ingest_skips_aliased_file_and_find_existing_matches_new_report(db):
    with sqlite3.connect(db) as c:
        dedupe.apply_plan(c, dedupe.plan(c))
        found = dedupe.find_existing(c, {"brand_name": "씨트렐린구강붕해정", "session_date": "2025-07-11", "ordinal": None})
        assert found == 1
        assert dedupe.find_existing(c, {"brand_name": "씨트렐린구강붕해정", "session_date": "2018-01-25", "ordinal": None}) == 3
        assert dedupe.find_existing(c, {"brand_name": "다른약정", "session_date": "2025-07-11", "ordinal": None}) is None
    res = store.ingest_corpus({"reports": [{"file_name": "평가결과_씨트렐린구강붕해정5밀리그램(탈티렐린수화물).pdf", "file_hash": "x",
                                          "brand_name": "씨트렐린구강붕해정"}]}, embed=False)
    assert res["inserted"] == 0 and res["skipped"] == 1                              # 같은 파일이 다시 수집돼도 새 행이 생기지 않는다


def test_absorb_report_fills_blanks_only(db):
    with sqlite3.connect(db) as c:
        n = dedupe.absorb_report(c, 3, {"file_name": "새파일.pdf", "file_hash": "n", "manufacturer": "새제약", "dosage": "999mg"}, ["manufacturer", "dosage"])
        row = c.execute("SELECT manufacturer, dosage FROM analog_reports WHERE id=3").fetchone()
    assert n == ["manufacturer"] and row == ("새제약", "5밀리그램")                    # 이미 있던 용량은 덮어쓰지 않는다


# ── 가드 ────────────────────────────────────────────────────────────────────

def test_guard_flags_conditional_price_stored_as_rejected_and_duplicates(db):
    with sqlite3.connect(db) as c:
        c.execute("UPDATE analog_reports SET result_meta='조건부비급여', review_result='REJECTED' WHERE id=4")
    from agents.quality_guard.data_contracts import check_data_contracts
    f = {x["check"]: x for x in check_data_contracts(db, write_deviation=False)}
    assert f["analog_conditional_price_as_rejected"]["severity"] == "ERROR" and f["analog_conditional_price_as_rejected"]["ids"] == [4]
    assert f["analog_duplicate_rows"]["severity"] == "WARNING" and f["analog_duplicate_rows"]["count"] == 1
    with sqlite3.connect(db) as c:
        dedupe.apply_plan(c, dedupe.plan(c))
        c.execute("UPDATE analog_reports SET review_result='CONDITIONAL_PRICE' WHERE id=4")
    f2 = {x["check"] for x in check_data_contracts(db, write_deviation=False)}
    assert "analog_duplicate_rows" not in f2 and "analog_conditional_price_as_rejected" not in f2


def test_prefix_key_merges_company_suffix_and_broken_names_and_fixes_the_surviving_name(tmp_path, monkeypatch):
    """회귀(2026-10-20): '루타테라주 옥소도트레오타이드)_-1병'(깨진 표기)과 '루타테라주', '오테밀라정'과 '오테밀라정동구바이오제약'이 별도 행으로 남았다."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "p.db")
    store.ensure_schema()
    rows = [("a.pdf", "루타테라주 옥소도트레오타이드)_-1병", "2021-11-11", 7), ("평가결과_b.pdf", "루타테라주", "2021-11-11", None),
            ("c.pdf", "오테밀라정", "2024-10-10", 10), ("평가결과_d.pdf", "오테밀라정동구바이오제약", "2024-10-10", None),
            ("e.pdf", "듀피젠트", "2024-10-10", 10)]                  # 접두(3자)라 합치지 않는다
    with sqlite3.connect(store.DB_PATH) as c:
        for i, (f, b, sd, o) in enumerate(rows, 1):
            c.execute("INSERT INTO analog_reports (file_name, file_hash, brand_name, brand_name_raw, session_date, ordinal, review_result, pdf_extractable) "
                      "VALUES (?,?,?,?,?,?, 'APPROVED', 1)", (f, f"h{i}", b, b, sd, o))
        pl = dedupe.plan(c)
        assert sorted((m["keep"], m["drop"]) for m in pl["merges"]) == [(1, 2), (3, 4)]
        dedupe.apply_plan(c, pl)
        names = [r[0] for r in c.execute("SELECT brand_name FROM analog_reports ORDER BY id")]
    assert "루타테라주" in names and "오테밀라정" in names and len(names) == 3 and "루타테라주 옥소도트레오타이드)_-1병" not in names
    assert dedupe.keys_match("루타테라주옥소도트레오타이드", "루타테라주") and not dedupe.keys_match("듀피젠트", "듀피")
