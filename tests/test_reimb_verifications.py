"""헤르메스 확인 기록(verifications)·이벤트 정정(event_removals)·매니페스트에서 빠진 보고서 숨김 — 2026-11-05.

배경: 헤르메스가 카드 28장을 HIRA 원문으로 확인했지만 대부분 '변화 없음' 이라 보드 stale 이 28→26 으로만 줄었다.
'확인했지만 변화 없음' 을 기록할 방법, 잘못 들어간 이벤트(웰리렉 2026-03-20 placeholder)를 지울 방법,
매니페스트에서 지운 중복 보고서를 운영에서 숨길 방법이 없었다.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.reimb_pipeline as rp  # noqa: E402
from agents.ingest import reimb_committee_import as imp  # noqa: E402

_SCHEMA = """
CREATE TABLE amjilsim_sessions (session_id INTEGER PRIMARY KEY, year INTEGER NOT NULL, ordinal_assumed INTEGER NOT NULL DEFAULT 0,
    ordinal_official INTEGER, session_date DATE NOT NULL, status TEXT NOT NULL DEFAULT 'SCHEDULED', official_minutes_url TEXT,
    note TEXT, created_at TEXT, committee_type TEXT NOT NULL DEFAULT 'AMJILSIM');
CREATE TABLE amjilsim_drugs (drug_id INTEGER PRIMARY KEY, product_slug TEXT, brand_kr TEXT NOT NULL, brand_en TEXT, ingredient_inn TEXT,
    atc TEXT, manufacturer TEXT, msd_flag INTEGER NOT NULL DEFAULT 0, competitor_class TEXT, first_seen_at TEXT,
    tracking_priority TEXT NOT NULL DEFAULT 'generic_new_drug', amjilsim_pass_date DATE, yakpyungwi_pass_date DATE,
    negotiation_status TEXT, indication TEXT, listing_type TEXT, submitted_date DATE, notes TEXT, expected_session_id INTEGER,
    key_issues TEXT, negotiation_complete_date TEXT, negotiation_date_source TEXT, nhis_registered_ym TEXT, efficacy_group TEXT);
CREATE TABLE amjilsim_drug_queue_status (id INTEGER PRIMARY KEY AUTOINCREMENT, drug_id INTEGER NOT NULL, session_id INTEGER,
    queue_state TEXT NOT NULL, queue_entry_date DATE, n_th_attempt INTEGER NOT NULL DEFAULT 1, evidence_url TEXT, observed_at TEXT,
    committee_type TEXT NOT NULL DEFAULT 'AMJILSIM');
CREATE TABLE reimb_reports (id INTEGER PRIMARY KEY, title TEXT, summary TEXT, highlights_json TEXT, report_type TEXT, committee TEXT,
    year INTEGER, cycle INTEGER, session_date TEXT, analyzed INTEGER NOT NULL DEFAULT 0);
"""


def _db(tmp_path):
    p = tmp_path / "v.db"
    c = sqlite3.connect(p)
    c.executescript(_SCHEMA)
    c.execute("INSERT INTO amjilsim_sessions VALUES (6, 2025, 9, 9, '2025-12-10', 'COMPLETED', NULL, NULL, NULL, 'AMJILSIM')")
    c.execute("INSERT INTO amjilsim_drugs (drug_id, brand_kr, ingredient_inn, msd_flag) VALUES "
              "(1, '웰리렉', 'belzutifan', 1), (2, '타그리소 확대', 'osimertinib', 0), (3, '키스칼리정', 'ribociclib', 0)")
    c.executemany("INSERT INTO amjilsim_drug_queue_status (drug_id, session_id, queue_state, queue_entry_date, observed_at, committee_type) "
                  "VALUES (?,?,?,?,?,?)",
                  [(1, None, "QUEUE_PENDING", "2026-03-20", "2026-05-28 04:18:43", "AMJILSIM"),
                   (1, 6, "REJECTED_REQUEUE", "2025-12-10", "2026-06-12 05:09:31", "AMJILSIM"),
                   (2, None, "REJECTED_REQUEUE", "2024-11-13", "2026-06-11 00:00:00", "AMJILSIM"),
                   (3, None, "REJECTED_REQUEUE", "2026-05-27", "2026-06-11 00:00:00", "AMJILSIM"),
                   (3, None, "REJECTED_REQUEUE", "2026-05-27", "2026-06-11 00:00:00", "YAKPYUNGWI")])
    c.commit()
    c.close()
    return p


def _conn(p):
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


def test_verifications_apply_and_match_brand_core(tmp_path):
    c = _conn(_db(tmp_path))
    res = imp._apply_verifications(c, {
        "타그리소 확대": {"verified_at": "2026-11-06", "source": "HIRA brdBltNo=11927", "note": "변화 없음"},
        "키스칼리": {"verified_at": "2026-11-06T07:00:00", "source": "HIRA 11808"},      # brand core 일치
        "없는약": {"verified_at": "2026-11-06"},
        "웰리렉": {"verified_at": "11/6"},                                                 # 형식 오류
    })
    assert res["applied"] == 2 and len(res["unmatched"]) == 2
    rows = {r["brand_kr"]: dict(r) for r in c.execute("SELECT brand_kr, last_verified_at, last_verified_source FROM amjilsim_drugs")}
    assert rows["타그리소 확대"]["last_verified_at"] == "2026-11-06"
    assert rows["타그리소 확대"]["last_verified_source"] == "HIRA brdBltNo=11927 · 변화 없음"
    assert rows["키스칼리정"]["last_verified_at"] == "2026-11-06"
    # 더 오래된 확인일로 덮지 않는다
    imp._apply_verifications(c, {"타그리소 확대": {"verified_at": "2026-10-01", "source": "old"}})
    assert c.execute("SELECT last_verified_at FROM amjilsim_drugs WHERE drug_id=2").fetchone()[0] == "2026-11-06"


def test_event_removals_exact_ambiguous_and_idempotent(tmp_path):
    c = _conn(_db(tmp_path))
    rm = [{"brand_kr": "웰리렉", "committee": "AMJILSIM", "state": "QUEUE_PENDING", "queue_entry_date": "2026-03-20",
           "reason": "초기 입력 placeholder — 공식 2025-03-19 결과로 대체"},
          {"brand_kr": "키스칼리정", "state": "REJECTED_REQUEUE", "queue_entry_date": "2026-05-27"},   # 2건 일치 → 모호
          {"brand_kr": "타그리소 확대"}]                                                           # 조건 부족
    res = imp._apply_event_removals(c, rm)
    assert res["removed"] == 1 and len(res["unmatched"]) == 2
    assert [r[0] for r in c.execute("SELECT queue_entry_date FROM amjilsim_drug_queue_status WHERE drug_id=1")] == ["2025-12-10"]
    assert c.execute("SELECT COUNT(*) FROM amjilsim_drug_queue_status WHERE drug_id=3").fetchone()[0] == 2
    again = imp._apply_event_removals(c, rm[:1])
    assert again == {"removed": 0, "unmatched": []}


class _Frozen(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 6)


def test_verified_card_is_not_stale_and_exposes_source(tmp_path, monkeypatch):
    p = _db(tmp_path)
    c = _conn(p)
    imp._apply_verifications(c, {"타그리소 확대": {"verified_at": "2026-11-06", "source": "HIRA brdBltNo=11927", "note": "변화 없음"}})
    c.commit()
    c.close()
    monkeypatch.setattr(rp, "DB_PATH", p)
    monkeypatch.setattr(rp, "date", _Frozen)
    board = {d["name"]: d for st in rp.get_pipeline()["stages"] for d in st["drugs"]}
    assert board["타그리소 확대"]["isStale"] is False and board["타그리소 확대"]["verifiedAt"] == "2026-11-06"
    assert board["타그리소 확대"]["verifiedSource"].startswith("HIRA brdBltNo=11927")
    assert board["키스칼리정"]["isStale"] is True and board["키스칼리정"]["verifiedAt"] is None
    # 확인일도 60일이 지나면 다시 stale
    monkeypatch.setattr(rp, "date", type("_Later", (date,), {"today": classmethod(lambda cls: cls(2027, 1, 10))}))
    board = {d["name"]: d for st in rp.get_pipeline()["stages"] for d in st["drugs"]}
    assert board["타그리소 확대"]["isStale"] is True


def test_run_applies_verifications_and_removals_from_payload(tmp_path, monkeypatch):
    p = _db(tmp_path)
    monkeypatch.setattr(imp, "DB_PATH", p)
    payload = {"schema_version": 1, "missing_sessions": [], "session_status_updates": [], "expected_next_session": {},
               "key_issues": {}, "drugs": [], "existing_drug_supplements": [],
               "verifications": {"웰리렉": {"verified_at": "2026-11-06", "source": "HIRA 11432·11684"}},
               "event_removals": [{"brand_kr": "웰리렉", "committee": "AMJILSIM", "state": "QUEUE_PENDING",
                                   "queue_entry_date": "2026-03-20"}]}
    res = imp.run(payload)
    assert res["verified_applied"] == 1 and res["events_removed"] == 1
    assert res["verified_unmatched"] == [] and res["event_removals_unmatched"] == []
