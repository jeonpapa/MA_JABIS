"""해외약가 관측 — 신선도 판정 (P1.6 관측 슬라이스).

원칙(사용자): "중요한건 현 시점에 해당 하는 약제의 가격을 가지고 올 수 있느냐야." — 잡 성공/실패가 아니라 **가격 행의 나이**를 본다.
실제 DB 는 읽지 않는다: 필요한 테이블만 가진 tmp_path DB 로 판정 논리만 확인한다.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from agents.agent_tasks import freshness as fr
from agents.agent_tasks.store import list_tasks

TODAY = date(2026, 10, 6)

SCHEMA = """
CREATE TABLE foreign_drug_prices (id INTEGER PRIMARY KEY, query_name TEXT, country TEXT, local_price REAL,
                                  searched_at TEXT, exchange_rate_to TEXT);
CREATE TABLE indications_master (product TEXT);
CREATE TABLE product_alias_map (product_slug TEXT PRIMARY KEY, inn TEXT, brand_aliases_json TEXT);
INSERT INTO product_alias_map VALUES ('keytruda', 'pembrolizumab', '["Keytruda", "키트루다", "pembrolizumab"]'),
                                     ('welireg', 'belzutifan', '["Welireg", "웰리렉"]'),
                                     ('gardasil', 'human_papillomavirus_vaccine', '["Gardasil 9"]');
INSERT INTO indications_master VALUES ('keytruda'), ('welireg'), ('gardasil'), ('human_papillomavirus_vaccine');
"""


def _seed(db: Path, *statements: str) -> None:
    conn = sqlite3.connect(db)
    for s in statements:
        conn.executescript(s)
    conn.commit()
    conn.close()


def _ctx(tmp_path, today: date = TODAY) -> fr.Ctx:
    db = tmp_path / "d.db"
    if not db.exists():
        sqlite3.connect(db).close()
    return fr.Ctx(db_path=db, base_dir=tmp_path, today=today, policy_root=tmp_path / "pi")


# ── 1 가격 나이 (foreign_price_age) ─────────────────────────────────────────────

def test_price_age_resolves_brand_and_inn_to_one_product_and_lists_each(tmp_path):
    """keytruda ↔ pembrolizumab 은 한 제품(alias 맵). 가격 없는 행은 세지 않는다. 미수집 제품은 '미수집' 으로 적는다."""
    _seed(tmp_path / "d.db", SCHEMA, """
        INSERT INTO foreign_drug_prices VALUES (1, 'keytruda', 'UK', 10, '2026-07-24T11:35:13', '20260531');
        INSERT INTO foreign_drug_prices VALUES (2, 'pembrolizumab', 'DE', 20, '2026-09-30T03:04:32', '20260531');  -- INN 행이 최신
        INSERT INTO foreign_drug_prices VALUES (3, 'welireg', 'IT', NULL, '2026-10-05T00:00:00', '20260531');     -- 가격 없음 → 제외
        INSERT INTO foreign_drug_prices VALUES (4, 'Welireg', 'UK', 30, '2026-07-24T12:41:09', '20260531');
    """)
    r = fr.check_foreign_price_age(_ctx(tmp_path))
    assert r.domain == "foreign_price_age" and r.stale is True
    assert r.deadline == "2026-08-22"                                   # 45일
    assert "keytruda=2026-09-30" in r.evidence and "welireg=2026-07-24" in r.evidence and "gardasil=미수집" in r.evidence
    assert "human_papillomavirus_vaccine=" not in r.evidence            # inn 표기는 gardasil 로 합쳐진다
    assert r.observed == "2026-07-24"                                   # 가장 오래된 제품의 최신 행
    assert r.runbook is None and "재검색" in r.evidence                  # 수집은 사용자 검색 — 사람이 할 일을 적는다


def test_price_age_fresh_when_every_product_priced_within_window(tmp_path):
    _seed(tmp_path / "d.db", SCHEMA, """
        INSERT INTO foreign_drug_prices VALUES (1, 'keytruda', 'UK', 10, '2026-09-30T11:35:13', '20260831');
        INSERT INTO foreign_drug_prices VALUES (2, 'belzutifan', 'IT', 20, '2026-09-01T03:04:32', '20260831');
        INSERT INTO foreign_drug_prices VALUES (3, 'gardasil', 'JP', 5, '2026-10-01T00:00:00', '20260831');
    """)
    r = fr.check_foreign_price_age(_ctx(tmp_path))
    assert r.stale is False and r.observed == "2026-09-01"


def test_price_age_never_priced_product_is_stale(tmp_path):
    """추적 제품인데 가격 행이 한 번도 없으면(미수집) 가져올 수 있는지 확인된 적이 없다 — stale."""
    _seed(tmp_path / "d.db", SCHEMA, """
        INSERT INTO foreign_drug_prices VALUES (1, 'keytruda', 'UK', 10, '2026-09-30T11:35:13', '20260831');
        INSERT INTO foreign_drug_prices VALUES (2, 'belzutifan', 'IT', 20, '2026-09-01T03:04:32', '20260831');
    """)
    r = fr.check_foreign_price_age(_ctx(tmp_path))
    assert r.stale is True and "gardasil=미수집" in r.evidence and "미수집 1" in r.evidence


def test_price_age_without_alias_table_falls_back_to_product_slug(tmp_path):
    _seed(tmp_path / "d.db", """
        CREATE TABLE foreign_drug_prices (id INTEGER PRIMARY KEY, query_name TEXT, country TEXT, local_price REAL, searched_at TEXT);
        CREATE TABLE indications_master (product TEXT);
        INSERT INTO indications_master VALUES ('keytruda');
        INSERT INTO foreign_drug_prices VALUES (1, 'keytruda', 'UK', 10, '2026-10-01T11:35:13');
    """)
    r = fr.check_foreign_price_age(_ctx(tmp_path))
    assert r.stale is False and r.determinable and "keytruda=2026-10-01" in r.evidence


def test_price_age_is_registered_and_dedups_per_month(tmp_path):
    assert fr.DOMAIN_CHECKS["foreign_price_age"] is fr.check_foreign_price_age
    r = fr.Freshness("foreign_price_age", True, "2026-07-24", "2026-08-22", "오래됨", None)
    ids = fr.record_freshness_problems([r], today=TODAY, db_path=tmp_path / "a.db")
    assert fr.record_freshness_problems([r], today=TODAY, db_path=tmp_path / "a.db") == ids
    t = list_tasks(db_path=tmp_path / "a.db")[0]
    assert t["source_ref"] == "foreign_price_age:2026-10" and "런북 없음" in t["detail"]


# ── 2 국가 커버리지 (foreign_country_coverage) ───────────────────────────────
# 빈 국가가 '제도상 공백' 인지 '수집 실패' 인지는 agents/scrapers/country_coverage.py 의 정책 메타 한 곳이 결정한다.

RECENT_SIX = """
    INSERT INTO foreign_drug_prices VALUES (1, 'keytruda', 'JP', 1, '2026-09-27T21:09:15', '20260831');
    INSERT INTO foreign_drug_prices VALUES (2, 'keytruda', 'IT', 1, '2026-09-27T21:09:03', '20260831');
    INSERT INTO foreign_drug_prices VALUES (3, 'keytruda', 'FR', 1, '2026-09-28T10:05:00', '20260831');
    INSERT INTO foreign_drug_prices VALUES (4, 'welireg', 'CH', 1, '2026-09-27T03:03:45', '20260831');
    INSERT INTO foreign_drug_prices VALUES (5, 'welireg', 'UK', 1, '2026-09-24T12:41:09', '20260831');
    INSERT INTO foreign_drug_prices VALUES (6, 'pembrolizumab', 'DE', 1, '2026-09-27T03:04:32', '20260831');
"""


def test_is_structural_absence_comes_from_country_coverage_policy():
    from agents.scrapers.country_coverage import is_structural_absence
    assert is_structural_absence("CA", "keytruda") and is_structural_absence("CA", "januvia") and is_structural_absence("CA")
    assert is_structural_absence("FR", "keytruda")              # 병원 전용 항암제 — BDPM 이 PPH 를 공개하지 않는다
    assert not is_structural_absence("FR", "januvia")           # BDPM PPC 공시 — 비면 수집 실패
    assert not is_structural_absence("US", "keytruda")          # '구독 필요' 는 제도상 공백이 아니다 — 로그인 실패는 수집 실패
    assert not is_structural_absence("JP") and not is_structural_absence("UK") and not is_structural_absence("XX")


def test_country_coverage_separates_structural_absence_from_collection_failure(tmp_path):
    _seed(tmp_path / "d.db", SCHEMA, RECENT_SIX)
    d = fr.foreign_country_coverage_detail(_ctx(tmp_path))
    assert d["since"] == "2026-07-08"                                                  # 90일
    assert d["recent"] == ["CH", "DE", "FR", "IT", "JP", "UK"]
    assert d["failed"] == ["US"] and d["structural"] == ["CA"]
    r = fr.check_foreign_country_coverage(_ctx(tmp_path))
    assert r.domain == "foreign_country_coverage" and r.stale is True and r.runbook is None
    assert "수집 실패" in r.evidence and "['US']" in r.evidence and "제도상 공백" in r.evidence and "['CA']" in r.evidence
    assert "재검색" in r.evidence


def test_country_coverage_structural_only_when_every_tracked_product_is_structural(tmp_path):
    """FR: keytruda(병원 전용 항암제)만 추적하면 제도상 공백, januvia(PPC 공시)도 추적하면 기대 국가 → 비면 수집 실패."""
    _seed(tmp_path / "d.db", """
        CREATE TABLE foreign_drug_prices (id INTEGER PRIMARY KEY, query_name TEXT, country TEXT, local_price REAL, searched_at TEXT);
        CREATE TABLE indications_master (product TEXT);
        INSERT INTO indications_master VALUES ('keytruda');
    """)
    d = fr.foreign_country_coverage_detail(_ctx(tmp_path))
    assert "FR" in d["structural"] and "CA" in d["structural"] and "FR" not in d["failed"]
    _seed(tmp_path / "d.db", "INSERT INTO indications_master VALUES ('januvia');")
    d2 = fr.foreign_country_coverage_detail(_ctx(tmp_path))
    assert "FR" in d2["failed"] and "CA" in d2["structural"]


def test_country_coverage_rows_older_than_window_do_not_count(tmp_path):
    _seed(tmp_path / "d.db", SCHEMA, RECENT_SIX,
          "UPDATE foreign_drug_prices SET searched_at='2026-06-01T00:00:00' WHERE country='UK';")
    d = fr.foreign_country_coverage_detail(_ctx(tmp_path))
    assert "UK" in d["failed"] and "UK" not in d["recent"]
    r = fr.check_foreign_country_coverage(_ctx(tmp_path))
    assert "UK=2026-06-01" in r.evidence                       # 실패 국가의 마지막 가격 행 시점


def test_country_coverage_fresh_when_only_structural_countries_are_empty(tmp_path):
    _seed(tmp_path / "d.db", SCHEMA, RECENT_SIX,
          "INSERT INTO foreign_drug_prices VALUES (7, 'keytruda', 'US', 1, '2026-09-20T00:00:00', '20260831');")
    r = fr.check_foreign_country_coverage(_ctx(tmp_path))
    assert r.stale is False and fr.DOMAIN_CHECKS["foreign_country_coverage"] is fr.check_foreign_country_coverage


# ── 3 FX 정합 — 캐시 파일 창이 아니라 '오늘 있어야 할 창' 기준 ───────────────────

def _fx_cache(tmp_path, *windows: str) -> None:
    d = tmp_path / "data" / "foreign" / "exchange_rate"
    d.mkdir(parents=True, exist_ok=True)
    for w in windows:
        (d / f"keb_avg_rate_{w}.xlsx").write_bytes(b"x")


def test_expected_fx_window_reuses_exchange_rate_calc_date_range():
    from agents.exchange_rate import _calc_date_range
    assert fr.expected_fx_window_to(date(2026, 10, 6)) == _calc_date_range(date(2026, 10, 6))[1] == "20260930"
    # 매월 2일 갱신 잡 유예: 4일까지는 전전월 말일 (exchange_rate_cache 의 기한과 같은 규칙)
    assert fr.expected_fx_window_to(date(2026, 10, 3)) == "20260831"
    assert fr.check_exchange_rate_cache(fr.Ctx(db_path=":memory:", base_dir=Path("/nonexistent"), today=date(2026, 10, 3))).deadline == "20260831"


def test_fx_alignment_stale_cache_with_matching_rows_is_stale(tmp_path):
    """감사가 본 결함: 행과 캐시가 함께 구창(20260531)이면 예전 판정은 ok 였다. 기대 창(20260930) 기준이면 stale."""
    _fx_cache(tmp_path, "20230403_20260331", "20230603_20260531")
    _seed(tmp_path / "d.db", SCHEMA, """
        INSERT INTO foreign_drug_prices VALUES (1, 'keytruda', 'UK', 10, '2026-07-24T11:35:13', '20260531');
        INSERT INTO foreign_drug_prices VALUES (2, 'welireg', 'IT', 20, '2026-07-24T10:54:35', '20260531');
    """)
    r = fr.check_foreign_price_fx_alignment(_ctx(tmp_path))
    assert r.stale is True and r.observed == "2" and r.deadline == "0"
    assert "20260930" in r.evidence and "20260531" in r.evidence
    assert r.runbook == "exchange_rate_refresh"                # 캐시도 구창 — 갱신 잡이 재계산까지 한다


def test_fx_alignment_fresh_when_rows_match_expected_window(tmp_path):
    _fx_cache(tmp_path, "20231003_20260930")
    _seed(tmp_path / "d.db", SCHEMA, """
        INSERT INTO foreign_drug_prices VALUES (1, 'keytruda', 'UK', 10, '2026-07-24T11:35:13', '20260531');  -- 구 세션
        INSERT INTO foreign_drug_prices VALUES (2, 'keytruda', 'UK', 10, '2026-10-02T11:35:13', '20260930');  -- 최신 행 정합
    """)
    r = fr.check_foreign_price_fx_alignment(_ctx(tmp_path))
    assert r.stale is False and r.observed == "0"


def test_fx_alignment_without_cache_file_still_judges_rows(tmp_path):
    """캐시 파일이 없어도 기대 창은 계산된다 — 예전엔 '캐시 없음' 으로 판정을 건너뛰었다."""
    _seed(tmp_path / "d.db", SCHEMA, "INSERT INTO foreign_drug_prices VALUES (1, 'keytruda', 'UK', 10, '2026-07-24T11:35:13', '20260531');")
    r = fr.check_foreign_price_fx_alignment(_ctx(tmp_path))
    assert r.stale is True and r.determinable and r.runbook == "exchange_rate_refresh"
