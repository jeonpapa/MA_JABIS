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
