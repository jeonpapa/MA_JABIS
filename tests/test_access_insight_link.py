"""Access Insight S1 — 뉴스↔약제 매핑 + signal 백필 테스트."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.access_insight.link import build_alias_index, resolve_drug
from agents.access_insight.classify import (
    classify_signal_type,
    signal_weight,
    GOV_STATEMENT,
    KOL_OPINION,
    IR_RELEASE,
    RESULT_REPORT,
    PATIENT_PETITION,
    PRE_AGENDA_LEAK,
)
from agents.access_insight.backfill import backfill_signals


_SCHEMA = """
CREATE TABLE amjilsim_drugs (
    drug_id INTEGER PRIMARY KEY,
    product_slug TEXT,
    brand_kr TEXT NOT NULL,
    brand_en TEXT,
    ingredient_inn TEXT,
    expected_session_id INTEGER
);
CREATE TABLE product_alias_map (
    product_slug TEXT PRIMARY KEY,
    inn TEXT,
    brand_aliases_json TEXT
);
CREATE TABLE amjilsim_sessions (
    session_id INTEGER PRIMARY KEY,
    year INTEGER,
    ordinal_official INTEGER,
    session_date DATE,
    status TEXT,
    committee_type TEXT
);
CREATE TABLE competitor_news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash TEXT,
    brand TEXT,
    company TEXT,
    anchor TEXT,
    kind TEXT,
    title TEXT,
    url TEXT,
    naver_link TEXT,
    source_domain TEXT,
    source_name TEXT,
    tier INTEGER,
    description TEXT,
    pub_date TEXT,
    trend_id INTEGER,
    collected_via TEXT,
    fetched_at TEXT,
    expires_at TEXT
);
CREATE TABLE amjilsim_media_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id INTEGER,
    session_id INTEGER,
    tier TEXT,
    outlet TEXT,
    url TEXT,
    title TEXT,
    published_at TEXT,
    snippet TEXT,
    signal_type TEXT,
    signal_phrases TEXT,
    crossref_count INTEGER DEFAULT 1,
    weight REAL DEFAULT 1.0,
    crawled_at TEXT,
    committee_target TEXT DEFAULT 'UNKNOWN',
    source_verified TEXT DEFAULT 'headline_only',
    raw_html_path TEXT
);
"""


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO amjilsim_drugs (drug_id, product_slug, brand_kr, brand_en, ingredient_inn, expected_session_id) VALUES "
        "(2, NULL, '키트루다', NULL, 'pembrolizumab', NULL),"
        "(1, 'welireg', '웰리렉', 'Welireg', 'belzutifan', NULL),"
        "(41, NULL, '옵디보 + 여보이', NULL, 'nivolumab+ipilimumab', NULL),"
        "(5, NULL, '베오바정 50mg 외 1품목', NULL, NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO product_alias_map (product_slug, inn, brand_aliases_json) VALUES "
        "('keytruda', 'pembrolizumab', ?),"
        "('welireg', 'belzutifan', ?),"
        "('opdivo', 'nivolumab', ?)",
        (
            json.dumps(["Keytruda", "키트루다", "키트루다주", "MK-3475", "펨브롤리주맙", "pembrolizumab"], ensure_ascii=False),
            json.dumps(["Welireg", "웰리렉", "웰리렉정", "MK-6482", "벨주티판"], ensure_ascii=False),
            json.dumps(["Opdivo", "옵디보", "옵디보주", "nivolumab", "니볼루맙"], ensure_ascii=False),
        ),
    )
    conn.execute(
        "INSERT INTO amjilsim_sessions (session_id, year, ordinal_official, session_date, status, committee_type) VALUES "
        "(100, 2026, 1, '2026-01-10', 'COMPLETED', 'AMJILSIM'),"
        "(101, 2026, 2, '2026-06-10', 'SCHEDULED', 'YAKPYUNGWI'),"
        "(102, 2026, 3, '2026-09-05', 'SCHEDULED', 'AMJILSIM')"
    )
    conn.commit()
    conn.close()


def _insert_news(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    for r in rows:
        conn.execute(
            "INSERT INTO competitor_news (brand, company, anchor, kind, title, url, source_domain, source_name, tier, description, pub_date) "
            "VALUES (:brand,:company,:anchor,:kind,:title,:url,:source_domain,:source_name,:tier,:description,:pub_date)",
            r,
        )
    conn.commit()
    conn.close()


# ── link.py ──────────────────────────────────────────────────────────────

def test_alias_index_maps_keytruda_brand(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)
    index = build_alias_index(db)
    assert index.get("키트루다") == 2
    assert index.get("mk-3475") == 2  # bridged via product_alias_map (ingredient_inn match)


def test_resolve_drug_from_title(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)
    index = build_alias_index(db)
    drug_id = resolve_drug("키트루다 급여 확대 논의", index)
    assert drug_id == 2


def test_resolve_drug_prefers_longest_alias(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)
    index = build_alias_index(db)
    # "키트루다주" (longer, still drug 2) 와 "키트루다" 모두 매치되지만 동일 drug_id 라 충돌 없음
    drug_id = resolve_drug("키트루다주 관련 소식", index)
    assert drug_id == 2


def test_resolve_drug_no_match_returns_none(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)
    index = build_alias_index(db)
    assert resolve_drug("전혀 관련없는 기사 제목", index) is None


def test_multi_item_brand_kr_skipped_from_alias_index(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)
    index = build_alias_index(db)
    # '베오바정 50mg 외 1품목' 은 다품목 나열이라 alias 로 등록되지 않아야 함
    assert not any(v == 5 for v in index.values())


# ── classify.py ──────────────────────────────────────────────────────────

def test_classify_patient_petition():
    st, phrases = classify_signal_type("환자단체 청원 잇따라", "희귀질환 환우회 성명 발표", "gov_policy")
    assert st == PATIENT_PETITION
    assert phrases


def test_classify_kol_opinion():
    st, phrases = classify_signal_type("대한암학회 전문가 좌담회", "교수들 의견 제시", "competitor")
    assert st == KOL_OPINION


def test_classify_gov_statement():
    st, phrases = classify_signal_type("국회 보건복지위 질의", "국정감사서 지적", "gov_policy")
    assert st == GOV_STATEMENT


def test_classify_gov_statement_via_kind_and_agency_keyword():
    st, phrases = classify_signal_type("심평원 발표", "건정심 상정 검토", "gov_policy")
    assert st == GOV_STATEMENT


def test_classify_ir_release():
    st, phrases = classify_signal_type("2분기 실적 발표", "컨퍼런스콜 개최", "competitor")
    assert st == IR_RELEASE


def test_classify_result_report():
    st, phrases = classify_signal_type("약평위 통과", "급여 결정 완료", "competitor")
    assert st == RESULT_REPORT


def test_classify_pre_agenda_leak():
    st, phrases = classify_signal_type("이번 회의 안건 상정", "심의 예정", "competitor")
    assert st == PRE_AGENDA_LEAK


# ── S4 소스 확장 — 국회·환자단체·의료진 신규 lexicon 매핑 ─────────────────

def test_classify_s4_patient_access_keyword():
    st, phrases = classify_signal_type(
        "신약 환자 접근성 개선 요구", "치료 기회 확대 주장", "gov_policy")
    assert st == PATIENT_PETITION
    assert "환자 접근성" in phrases


def test_classify_s4_medical_society_guideline():
    st, phrases = classify_signal_type(
        "진료지침 개정, 급여 기준 반영 요청", "전문의 대상 설문", "gov_policy")
    assert st == KOL_OPINION
    assert set(phrases) & {"진료지침", "전문의"}


def test_classify_s4_assembly_bill():
    st, phrases = classify_signal_type(
        "약가 제도 개선 법안 발의", "건강보험법 개정안 국회 제출", "gov_policy")
    assert st == GOV_STATEMENT
    assert set(phrases) & {"법안", "발의", "국회"}


def test_classify_s4_pharma_press_conference_not_patient():
    # 무맥락 '기자회견' 은 lexicon 에 없어야 함 — 제약사 회견이 PATIENT 로 오분류 금지
    st, _ = classify_signal_type("제약사 신제품 기자회견", "매출 목표 발표", "competitor")
    assert st == IR_RELEASE


def test_classify_fallback_gov_policy_kind():
    st, phrases = classify_signal_type("특이 키워드 없는 제목", "본문도 평범함", "gov_policy")
    assert st == GOV_STATEMENT


def test_classify_fallback_other_kind():
    st, phrases = classify_signal_type("특이 키워드 없는 제목", "본문도 평범함", "competitor")
    assert st == IR_RELEASE


def test_signal_weight_official_higher_than_ir():
    assert signal_weight("A", GOV_STATEMENT) > signal_weight("A", IR_RELEASE)


def test_signal_weight_default_tier_d():
    # 기본 tier 는 'D' — 명시적으로 넘긴 tier='D' 와 동일해야 함
    assert signal_weight(signal_type=IR_RELEASE) == signal_weight("D", IR_RELEASE)


# ── backfill.py ────────────────────────────────────────────────────────────

def test_backfill_inserts_matched_signals_and_skips_unmatched(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)
    _insert_news(db, [
        {
            "brand": "옵디보", "company": "BMS Korea", "anchor": "키트루다 / PD-(L)1",
            "kind": "competitor", "title": "키트루다 급여 확대 논의 본격화",
            "url": "https://example.com/a1", "source_domain": "dailypharm.com",
            "source_name": "데일리팜", "tier": 1,
            "description": "암질심 통과 이후 약평위 절차 주목", "pub_date": "2026-05-01",
        },
        {
            "brand": "보건복지부", "company": None, "anchor": None,
            "kind": "gov_policy", "title": "국회 보건복지위, 신약 급여 논의",
            "url": "https://example.com/a2", "source_domain": "newspim.com",
            "source_name": None, "tier": 3,
            "description": "웰리렉 관련 국정감사 지적 이어져", "pub_date": "2026-04-01",
        },
        {
            "brand": "정책일반", "company": None, "anchor": None,
            "kind": "gov_policy", "title": "전혀 관련없는 정책 기사",
            "url": "https://example.com/a3", "source_domain": "etc.com",
            "source_name": None, "tier": 3,
            "description": "약과 무관한 내용", "pub_date": "2026-03-01",
        },
    ])

    stats = backfill_signals(db_path=db)

    assert stats["scanned"] == 3
    assert stats["matched"] == 2
    assert stats["inserted"] == 2
    assert stats["unmatched"] == 1

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM amjilsim_media_signals ORDER BY url").fetchall()
    conn.close()
    assert len(rows) == 2

    a1 = next(r for r in rows if r["url"] == "https://example.com/a1")
    assert a1["drug_id"] == 2  # 키트루다
    assert a1["signal_type"] == RESULT_REPORT  # "통과" 매치
    assert a1["crossref_count"] == 0
    assert a1["source_verified"] == "snippet_match"

    a2 = next(r for r in rows if r["url"] == "https://example.com/a2")
    assert a2["drug_id"] == 1  # 웰리렉
    assert a2["signal_type"] == GOV_STATEMENT
    # nearest session_date >= pub_date(2026-04-01) among sessions (2026-01-10 completed, 2026-06-10, 2026-09-05)
    assert a2["session_id"] == 101


def test_backfill_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)
    _insert_news(db, [
        {
            "brand": "옵디보", "company": "BMS Korea", "anchor": "키트루다 / PD-(L)1",
            "kind": "competitor", "title": "키트루다 급여 확대 논의 본격화",
            "url": "https://example.com/a1", "source_domain": "dailypharm.com",
            "source_name": "데일리팜", "tier": 1,
            "description": "암질심 통과 이후 약평위 절차 주목", "pub_date": "2026-05-01",
        },
    ])
    first = backfill_signals(db_path=db)
    assert first["inserted"] == 1
    second = backfill_signals(db_path=db)
    assert second["inserted"] == 0
    assert second["duplicate_skipped"] == 1

    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT count(*) FROM amjilsim_media_signals").fetchone()[0]
    conn.close()
    assert count == 1


def test_backfill_respects_limit(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)
    _insert_news(db, [
        {
            "brand": "옵디보", "company": "BMS Korea", "anchor": "키트루다 / PD-(L)1",
            "kind": "competitor", "title": "키트루다 소식 1",
            "url": "https://example.com/b1", "source_domain": "dailypharm.com",
            "source_name": "데일리팜", "tier": 1,
            "description": "본문 1", "pub_date": "2026-05-01",
        },
        {
            "brand": "옵디보", "company": "BMS Korea", "anchor": "키트루다 / PD-(L)1",
            "kind": "competitor", "title": "키트루다 소식 2",
            "url": "https://example.com/b2", "source_domain": "dailypharm.com",
            "source_name": "데일리팜", "tier": 1,
            "description": "본문 2", "pub_date": "2026-05-02",
        },
    ])
    stats = backfill_signals(db_path=db, limit=1)
    assert stats["scanned"] == 1
