"""해외약가 '변경 이력' (2026-11-03 사용자 승인 개선안).

- 압축(compact)은 과거 세션을 지우지 않고 보관 테이블로 옮긴다 — 화면 이력의 원천.
- 변경 유형: 같은 출처·단계의 실제 가격 변동(price) 과 출처/단계 변경(basis) 을 구분한다.
  운영 데이터의 독일 UVP→EB, 이탈리아 목록가→결정문, 프랑스 공중가→PFHT 는 모두 basis 이며 가격 인하로 읽으면 안 된다.
- 목록 API 는 국가별 상태(가격 있음/미확정·확인일)와 '오래됨'·변경 요약을 준다.
- 결과 메일로 확정된 값은 이력 행에 표시하되 요청자·이메일은 내보내지 않는다.
"""
from datetime import datetime

from agents.db import DrugPriceDB
from agents.db.foreign import classify_price_sessions
from agents.price_request_store import PriceRequestStore

RL_AVP = "Rote Liste (AVP, DocCheck)"
RL_UVP = "Rote Liste (AVP/UVP; DocCheck)"
RL_EB = "Rote Liste (AVP (EB)/FB; DocCheck)"


def _rec(**kw) -> dict:
    base = {"searched_at": "2026-08-10T10:15:43", "query_name": "winrevair", "country": "DE",
            "product_name": "Winrevair", "ingredient": "sotatercept", "dosage_strength": "45 mg",
            "dosage_form": "", "package_unit": "", "local_price": 4535.78, "currency": "EUR",
            "exchange_rate": None, "exchange_rate_from": None, "exchange_rate_to": None,
            "factory_price_krw": None, "vat_rate": None, "distribution_margin": None,
            "adjusted_price_krw": None, "source_url": "", "source_label": RL_AVP, "raw_data": "{}",
            "form_type": "injection", "formulation_key": "45mg|injection", "pack_count": 1}
    return {**base, **kw}


def _de_series(db):
    db.save_foreign_price(_rec(searched_at="2026-08-10T10:15:43"))
    db.save_foreign_price(_rec(searched_at="2026-09-02T04:00:30"))
    db.save_foreign_price(_rec(searched_at="2026-10-26T17:42:29", local_price=7894.51, source_label=RL_UVP))
    db.save_foreign_price(_rec(searched_at="2026-11-02T07:44:25", local_price=4535.78, source_label=RL_EB))


def test_compaction_archives_old_sessions_instead_of_deleting(tmp_path):
    db = DrugPriceDB(tmp_path / "f.db")
    _de_series(db)
    assert db.compact_foreign_prices("winrevair") == 3          # 현재 테이블에서 빠진 행 수는 이전과 같은 의미
    live = db.get_foreign_prices("winrevair")
    assert [r["local_price"] for r in live] == [4535.78]          # 화면 현재가는 그대로 최신 세션
    hist = db.get_foreign_price_history("winrevair")["countries"]["DE"]
    assert [e["searched_at"][:10] for e in hist] == ["2026-08-10", "2026-09-02", "2026-10-26", "2026-11-02"]
    assert [r["archived"] for e in hist for r in e["rows"]] == [True, True, True, False]


def test_change_kinds_separate_basis_change_from_real_price_change():
    rows = [
        _rec(searched_at="2026-08-10T10:15:43"),
        _rec(searched_at="2026-09-02T04:00:30"),                                         # 재확인
        _rec(searched_at="2026-10-26T17:42:29", local_price=7894.51, source_label=RL_UVP),  # 단계 변경
        _rec(searched_at="2026-11-02T07:44:25", local_price=4535.78, source_label=RL_EB),   # 단계 변경(복귀)
        _rec(searched_at="2026-12-01T00:00:00", local_price=4400.00, source_label=RL_EB),   # 같은 단계 실제 인하
        _rec(searched_at="2026-12-15T00:00:00", local_price=None, source_label=RL_EB),      # 수집 실패
        _rec(searched_at="2027-01-01T00:00:00", local_price=4400.00, source_label="rote liste (avp (eb)/fb;  docCheck)"),
    ]
    kinds = [e["kind"] for e in classify_price_sessions(rows)["DE"]]
    assert kinds == ["first", "same", "basis", "basis", "price", "unpriced", "same"]


def test_relabel_and_pack_additions_are_not_price_changes():
    a = _rec(searched_at="2026-08-11T12:00:00", country="FR", local_price=6393.0, source_label="JO PFHT")
    b = _rec(searched_at="2026-09-02T12:00:00", country="FR", local_price=6393.0, source_label="Journal officiel (PFHT)")
    c1 = _rec(searched_at="2026-10-01T12:00:00", country="FR", local_price=6393.0, source_label="Journal officiel (PFHT)")
    c2 = _rec(searched_at="2026-10-01T12:00:00", country="FR", local_price=8524.0, source_label="Journal officiel (PFHT)",
              formulation_key="60mg|injection")
    kinds = [e["kind"] for e in classify_price_sessions([a, b, c1, c2])["FR"]]
    assert kinds == ["first", "relabel", "packs"]


def test_channels_milliseconds_apart_form_one_session():
    rows = [_rec(country="IT", searched_at="2026-07-27T03:02:57.080401", source_label="AIFA CSV", local_price=3428.0),
            _rec(country="IT", searched_at="2026-07-27T03:02:57.158115", source_label="AIFA TNF", local_price=3798.34)]
    ev = classify_price_sessions(rows)["IT"]
    assert len(ev) == 1 and len(ev[0]["rows"]) == 2


def test_drug_list_reports_country_status_stale_and_change_counts(tmp_path):
    db = DrugPriceDB(tmp_path / "f.db")
    _de_series(db)
    db.save_foreign_price(_rec(country="CA", searched_at="2026-08-10T10:15:43", local_price=None,
                               source_label="Ontario EAP", currency="CAD"))
    db.compact_foreign_prices("winrevair")
    item = db.get_foreign_drug_list(today=datetime(2026, 11, 3))[0]
    status = {c["country"]: (c["status"], c["checked_at"]) for c in item["countries"]}
    assert status == {"CA": ("unpriced", "2026-08-10"), "DE": ("priced", "2026-11-02")}
    assert item["stale_countries"] == ["CA"]                      # 30일 넘게 확인 안 함
    assert item["change_counts"] == {"basis": 1, "price": 0}      # 독일 단계 변경은 가격 변동으로 세지 않는다


def test_emailed_values_are_marked_without_requester_details(tmp_path):
    path = tmp_path / "f.db"
    db = DrugPriceDB(path)
    store = PriceRequestStore(path)
    rid_row = db.save_foreign_price(_rec(searched_at="2026-11-02T07:44:25", source_label=RL_EB))
    with store.connect() as c:
        c.execute("INSERT INTO price_requests(id,query,scope,countries,requester,notify_email,status,created_at,updated_at,"
                  "notification_status,notified_at) VALUES('r1','winrevair','a8','[\"DE\"]','u1','someone@example.com',"
                  "'completed','2026-11-02','2026-11-02','sent','2026-11-02T08:00:00')")
        c.execute("INSERT INTO price_request_price_snapshots(request_id,country,row_id,payload,captured_at) "
                  "VALUES('r1','DE',?,'{}','2026-11-02T07:50:00')", (rid_row,))
    row = db.get_foreign_price_history("winrevair")["countries"]["DE"][0]["rows"][0]
    assert row["confirmed"] == [{"captured_at": "2026-11-02T07:50:00", "emailed": True, "notified_at": "2026-11-02T08:00:00"}]
    assert "someone@example.com" not in str(db.get_foreign_price_history("winrevair"))


def test_deleting_a_product_also_clears_its_archive(tmp_path):
    db = DrugPriceDB(tmp_path / "f.db")
    _de_series(db)
    db.compact_foreign_prices("winrevair")
    db.delete_foreign_drug("winrevair")
    assert db.get_foreign_price_history("winrevair")["countries"] == {}


def test_archive_follows_new_columns(tmp_path):
    db = DrugPriceDB(tmp_path / "f.db")
    _de_series(db)
    db.compact_foreign_prices("winrevair")                         # 보관 테이블 생성
    with db._connect() as conn:
        conn.execute("ALTER TABLE foreign_drug_prices ADD COLUMN extra_note TEXT")
    db.save_foreign_price(_rec(searched_at="2026-12-01T00:00:00", local_price=4400.0, source_label=RL_EB))
    db.compact_foreign_prices("winrevair")
    with db._connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(foreign_drug_prices_archive)")}
    assert {"extra_note", "archived_at"} <= cols
