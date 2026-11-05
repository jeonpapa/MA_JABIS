"""암질심 상정 후보 자동 추가(2026-11-05 사용자 결정) — 네트워크·LLM·실제 DB 없이 주입으로 검증.

결정: ① 암질심 기사에 거명된 미등록 약제는 자동 후보로 추가(origin='news_auto')
      ② 상정·논의가 없으면 갱신하지 않는다 — 위원회 이벤트 없는 자동 후보는 stale 경고·신선도 과제 제외
      ③ 공단 연동은 막는다 — NHIS 매칭이 자동 후보를 건드리지 않고, 미매칭 공단 행으로 약제를 만들지 않는다
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.reimb_pipeline as rp  # noqa: E402
from agents.access_insight.link import invalidate_index_cache  # noqa: E402
from agents.amjilsim_tracker.candidate_discovery import (  # noqa: E402
    ORIGIN_NEWS_AUTO, brand_core, discover_candidates, find_auto_candidate,
)
from agents.amjilsim_tracker.crawlers.tiered_news import COMMITTEE_KEYWORDS, TieredArticle  # noqa: E402

_SCHEMA = """
CREATE TABLE amjilsim_sessions (
    session_id INTEGER PRIMARY KEY, year INTEGER NOT NULL, ordinal_assumed INTEGER NOT NULL DEFAULT 0,
    ordinal_official INTEGER, session_date DATE NOT NULL, status TEXT NOT NULL DEFAULT 'SCHEDULED',
    official_minutes_url TEXT, note TEXT, created_at TEXT, committee_type TEXT NOT NULL DEFAULT 'AMJILSIM'
);
CREATE TABLE amjilsim_drugs (
    drug_id INTEGER PRIMARY KEY AUTOINCREMENT, product_slug TEXT, brand_kr TEXT NOT NULL, brand_en TEXT, ingredient_inn TEXT,
    atc TEXT, manufacturer TEXT, msd_flag INTEGER NOT NULL DEFAULT 0, competitor_class TEXT,
    first_seen_at TEXT DEFAULT (datetime('now')),
    tracking_priority TEXT NOT NULL DEFAULT 'generic_new_drug', amjilsim_pass_date DATE, yakpyungwi_pass_date DATE,
    negotiation_status TEXT, indication TEXT, listing_type TEXT, submitted_date DATE, notes TEXT,
    expected_session_id INTEGER, key_issues TEXT, negotiation_complete_date TEXT, negotiation_date_source TEXT,
    nhis_registered_ym TEXT, efficacy_group TEXT, is_oncology INTEGER,
    UNIQUE(brand_kr, ingredient_inn)
);
CREATE TABLE amjilsim_drug_queue_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT, drug_id INTEGER NOT NULL, session_id INTEGER, queue_state TEXT NOT NULL,
    queue_entry_date DATE, n_th_attempt INTEGER NOT NULL DEFAULT 1, evidence_url TEXT, observed_at TEXT,
    committee_type TEXT NOT NULL DEFAULT 'AMJILSIM'
);
CREATE TABLE product_alias_map (product_slug TEXT PRIMARY KEY, inn TEXT, brand_aliases_json TEXT);
CREATE TABLE reimb_reports (
    id INTEGER PRIMARY KEY, title TEXT, summary TEXT, highlights_json TEXT, report_type TEXT, committee TEXT,
    year INTEGER, cycle INTEGER, session_date TEXT, analyzed INTEGER NOT NULL DEFAULT 0
);
"""


def _db(tmp_path) -> Path:
    p = tmp_path / "c.db"
    conn = sqlite3.connect(p)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO amjilsim_sessions (session_id, year, ordinal_assumed, session_date, status, committee_type) "
                 "VALUES (8, 2026, 9, '2026-11-11', 'SCHEDULED', 'AMJILSIM')")
    conn.execute("INSERT INTO amjilsim_drugs (drug_id, brand_kr, ingredient_inn, manufacturer, msd_flag) "
                 "VALUES (1, '웰리렉', 'belzutifan', '한국MSD', 1), (2, '타그리소', 'osimertinib', '한국AZ', 0)")
    conn.commit()
    conn.close()
    invalidate_index_cache()
    return p


def _art(title, snippet="", outlet="데일리팜", url=None):
    return TieredArticle(outlet=outlet, url=url or f"https://x/{abs(hash((title, outlet)))}", title=title,
                         published_at=datetime(2026, 11, 2), snippet=snippet, tier="A")


def _extractor(mapping):
    """기사 제목 → 추출 결과 목록 고정 주입 (LLM 대체)."""
    def fn(arts):
        out = []
        for i, a in enumerate(arts):
            for it in mapping.get(a.title, []):
                out.append({"index": i, **it})
        return out
    return fn


MFDS_OK = {"source": "mfds_api", "item_name": "엔허투주100밀리그램", "main_ingr_eng": "Trastuzumab Deruxtecan",
           "entp_name": "한국다이이찌산쿄(주)", "atc_code": "L01FD04"}


def _rows(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return {r["brand_kr"]: dict(r) for r in conn.execute("SELECT * FROM amjilsim_drugs")}
    finally:
        conn.close()


def test_keywords_include_formal_committee_names():
    assert {"암질심", "암질환심의위원회", "중증(암)질환심의위원회"} <= set(COMMITTEE_KEYWORDS)


def test_brand_core_normalizes_form_and_dose():
    assert brand_core("엔허투주 100mg") == brand_core("엔허투") == "엔허투"
    assert brand_core("티루캡정") == "티루캡"


def test_adds_unknown_drug_with_mfds_values(tmp_path):
    db = _db(tmp_path)
    arts = [_art("엔허투, 11월 암질심 상정…HER2 저발현 유방암", "다이이찌산쿄 엔허투가 암질심에 상정된다")]
    res = discover_candidates(arts, db, extractor=_extractor({arts[0].title: [{"brand_kr": "엔허투", "indication": "HER2 저발현 유방암"}]}),
                              permit_lookup=lambda b: MFDS_OK, today=date(2026, 11, 5))
    assert [a["brand_kr"] for a in res["added"]] == ["엔허투"]
    row = _rows(db)["엔허투"]
    assert row["origin"] == ORIGIN_NEWS_AUTO
    assert row["ingredient_inn"] == "trastuzumab deruxtecan" and row["manufacturer"] == "한국다이이찌산쿄(주)"
    assert row["is_oncology"] == 1 and row["msd_flag"] == 0 and row["atc"] == "L01FD04"
    ev = json.loads(row["origin_evidence"])
    assert ev["mfds"] == "verified" and ev["first_seen"] == "2026-11-05" and ev["articles"][0]["outlet"] == "데일리팜"
    # 큐 이벤트는 만들지 않는다 — 상정 여부는 헤르메스 정본이 단일 소스
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM amjilsim_drug_queue_status").fetchone()[0] == 0
    conn.close()


def test_skips_existing_hallucinated_and_non_amjilsim(tmp_path):
    db = _db(tmp_path)
    a1 = _art("타그리소·웰리렉 암질심 재상정 전망", "타그리소 확대와 웰리렉이 암질심 재도전")
    a2 = _art("암질심 결과 발표", "급여기준 설정 약제 공개")
    a3 = _art("엔허투 미국 허가 확대", "FDA 승인")           # 암질심 표면어 없음 → 대상 아님
    ext = _extractor({a1.title: [{"brand_kr": "타그리소"}, {"brand_kr": "웰리렉정"}],
                      a2.title: [{"brand_kr": "가짜맙"}],     # 기사 표면에 없음 → 환각
                      a3.title: [{"brand_kr": "엔허투"}]})
    res = discover_candidates([a1, a2, a3], db, extractor=ext, permit_lookup=lambda b: MFDS_OK)
    assert res["amjilsim_articles"] == 2 and res["added"] == []
    reasons = {s["brand_kr"]: s["reason"] for s in res["skipped"]}
    assert reasons["타그리소"] == "이미 보드에 있음" and reasons["웰리렉정"] == "이미 보드에 있음"
    assert "기사 표면에 없음" in reasons["가짜맙"]
    assert set(_rows(db)) == {"웰리렉", "타그리소"}


def test_unverified_needs_two_outlets(tmp_path):
    db = _db(tmp_path)
    miss = lambda b: {"source": "miss"}
    one = [_art("신약A맙 암질심 상정", "신약A맙", outlet="데일리팜")]
    res = discover_candidates(one, db, extractor=_extractor({one[0].title: [{"brand_kr": "신약A맙"}]}), permit_lookup=miss)
    assert res["added"] == [] and "MFDS 미확인" in res["skipped"][0]["reason"]
    two = [_art("신약A맙 암질심 상정", "신약A맙", outlet="데일리팜"), _art("신약A맙, 암질심 문턱", "신약A맙", outlet="약업신문")]
    res = discover_candidates(two, db, extractor=_extractor({t.title: [{"brand_kr": "신약A맙"}] for t in two}), permit_lookup=miss)
    assert [a["brand_kr"] for a in res["added"]] == ["신약A맙"] and res["added"][0]["mfds"] == "unverified:miss"


def test_dry_run_and_idempotent(tmp_path):
    db = _db(tmp_path)
    arts = [_art("엔허투 암질심 상정", "엔허투")]
    ext = _extractor({arts[0].title: [{"brand_kr": "엔허투"}]})
    dry = discover_candidates(arts, db, extractor=ext, permit_lookup=lambda b: MFDS_OK, dry_run=True)
    assert len(dry["added"]) == 1 and "엔허투" not in _rows(db)
    discover_candidates(arts, db, extractor=ext, permit_lookup=lambda b: MFDS_OK)
    again = discover_candidates(arts, db, extractor=ext, permit_lookup=lambda b: MFDS_OK)
    assert again["added"] == [] and len([b for b in _rows(db) if b == "엔허투"]) == 1


def test_skips_non_oncology_atc(tmp_path):
    db = _db(tmp_path)
    arts = [_art("암질심·약평위 결과…하임파지 급여 적정", "하임파지")]
    res = discover_candidates(arts, db, extractor=_extractor({arts[0].title: [{"brand_kr": "하임파지"}]}),
                              permit_lookup=lambda b: {**MFDS_OK, "atc_code": "B02BX11"})
    assert res["added"] == [] and "비항암 ATC B02BX11" in res["skipped"][0]["reason"]


def test_cap_per_run(tmp_path):
    db = _db(tmp_path)
    arts = [_art(f"후보{i}맙 암질심 상정", f"후보{i}맙") for i in range(5)]
    ext = _extractor({a.title: [{"brand_kr": f"후보{i}맙"}] for i, a in enumerate(arts)})
    res = discover_candidates(arts, db, extractor=ext, permit_lookup=lambda b: MFDS_OK, max_new=3)
    assert len(res["added"]) == 3 and sum("상한" in s["reason"] for s in res["skipped"]) == 2


def test_run_fresh_crawl_wires_discovery_and_links_signals(tmp_path):
    from agents.amjilsim_tracker.signal_extractor import run_fresh_crawl
    from agents.naver_news import NewsItem
    db = _db(tmp_path)
    # signal_extractor 가 요구하는 media_signals 테이블
    conn = sqlite3.connect(db)
    conn.executescript("""CREATE TABLE amjilsim_media_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, drug_id INTEGER,
        session_id INTEGER, tier TEXT, outlet TEXT NOT NULL, url TEXT NOT NULL, title TEXT, published_at TEXT, snippet TEXT,
        signal_type TEXT, signal_phrases TEXT, crossref_count INTEGER DEFAULT 1, weight REAL DEFAULT 1.0, crawled_at TEXT,
        committee_target TEXT DEFAULT 'UNKNOWN', source_verified TEXT DEFAULT 'headline_only', raw_html_path TEXT,
        UNIQUE(outlet, url));""")
    conn.close()
    title = "엔허투, 암질심 상정 예정"
    item = NewsItem(title=title, link="https://www.dailypharm.com/n/1", original_link="https://www.dailypharm.com/n/1",
                    description="엔허투가 다음 암질심에 상정될 예정이다", pub_date=datetime(2026, 11, 2), source="데일리팜")
    res = run_fresh_crawl(db_path=db, keywords=["암질심"], lookback_days=8,
                          naver_fetch=lambda kw, d: [item], site_search=lambda kw, d: [],
                          tier_of=lambda url: (1, "데일리팜"),
                          candidate_extractor=_extractor({title: [{"brand_kr": "엔허투"}]}),
                          permit_lookup=lambda b: MFDS_OK)
    assert res["candidates_added"] == 1
    conn = sqlite3.connect(db)
    did = conn.execute("SELECT drug_id FROM amjilsim_drugs WHERE brand_kr='엔허투'").fetchone()[0]
    # 새 후보도 같은 실행에서 신호가 붙는다(후보 발굴이 신호 적재보다 먼저)
    assert conn.execute("SELECT COUNT(*) FROM amjilsim_media_signals WHERE drug_id=?", (did,)).fetchone()[0] == 1
    conn.close()


def test_run_fresh_crawl_survives_discovery_failure(tmp_path):
    from agents.amjilsim_tracker.signal_extractor import run_fresh_crawl
    db = _db(tmp_path)
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE amjilsim_media_signals (id INTEGER PRIMARY KEY, drug_id INTEGER, session_id INTEGER, "
                       "tier TEXT, outlet TEXT NOT NULL, url TEXT NOT NULL, title TEXT, published_at TEXT, snippet TEXT, "
                       "signal_type TEXT, signal_phrases TEXT, crossref_count INTEGER, weight REAL, crawled_at TEXT, "
                       "committee_target TEXT, source_verified TEXT, raw_html_path TEXT, UNIQUE(outlet, url));")
    conn.close()

    def boom(arts):
        raise RuntimeError("llm down")
    from agents.naver_news import NewsItem
    item = NewsItem(title="암질심 상정", link="https://x/1", original_link="https://x/1", description="암질심",
                    pub_date=datetime(2026, 11, 2), source="x")
    res = run_fresh_crawl(db_path=db, keywords=["암질심"], naver_fetch=lambda kw, d: [item], site_search=lambda kw, d: [],
                          tier_of=lambda url: (1, "x"), candidate_extractor=boom, permit_lookup=lambda b: MFDS_OK)
    assert "llm down" in res["candidates"]["error"] and res["candidates_added"] == 0 and res["articles"] == 1


# ── ② 갱신 정책: 이벤트 없는 자동 후보는 stale 아님 / 이벤트 생기면 일반 카드 ──────────────

class _Frozen(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 5)


def _board(db, monkeypatch):
    monkeypatch.setattr(rp, "DB_PATH", db)
    monkeypatch.setattr(rp, "date", _Frozen)
    return {d["name"]: d for st in rp.get_pipeline()["stages"] for d in st["drugs"]}


def test_auto_candidate_without_events_is_never_stale(tmp_path, monkeypatch):
    db = _db(tmp_path)
    discover_candidates([_art("엔허투 암질심 상정", "엔허투")], db,
                        extractor=lambda arts: [{"index": 0, "brand_kr": "엔허투"}], permit_lookup=lambda b: MFDS_OK)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE amjilsim_drugs SET first_seen_at='2026-06-01 00:00:00' WHERE brand_kr='엔허투'")
    # 기존 카드는 오래된 관측 → stale (대조군)
    conn.execute("INSERT INTO amjilsim_drug_queue_status (drug_id, queue_state, observed_at) VALUES (2, 'QUEUE_PENDING', '2026-06-11 00:00:00')")
    conn.commit()
    conn.close()
    board = _board(db, monkeypatch)
    assert board["엔허투"]["autoCandidate"] is True and board["엔허투"]["isStale"] is False
    assert board["엔허투"]["origin"] == ORIGIN_NEWS_AUTO and board["엔허투"]["firstSeenAt"].startswith("2026-06-01")
    assert board["타그리소"]["isStale"] is True and board["타그리소"]["autoCandidate"] is False

    # 위원회 이벤트가 생기면 일반 카드 규칙
    conn = sqlite3.connect(db)
    did = conn.execute("SELECT drug_id FROM amjilsim_drugs WHERE brand_kr='엔허투'").fetchone()[0]
    conn.execute("INSERT INTO amjilsim_drug_queue_status (drug_id, session_id, queue_state, observed_at) "
                 "VALUES (?, 8, 'QUEUE_PENDING', '2026-07-01 00:00:00')", (did,))
    conn.commit()
    conn.close()
    board = _board(db, monkeypatch)
    assert board["엔허투"]["autoCandidate"] is False and board["엔허투"]["isStale"] is True


def test_freshness_stale_cards_excludes_auto_candidates(tmp_path, monkeypatch):
    from agents.agent_tasks.freshness import DOMAIN_CHECKS, Ctx, check_pipeline_stale_cards, owner_action
    db = _db(tmp_path)
    discover_candidates([_art("엔허투 암질심 상정", "엔허투")], db,
                        extractor=lambda arts: [{"index": 0, "brand_kr": "엔허투"}], permit_lookup=lambda b: MFDS_OK)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE amjilsim_drugs SET first_seen_at='2026-06-01 00:00:00' WHERE brand_kr='엔허투'")
    conn.execute("INSERT INTO amjilsim_drug_queue_status (drug_id, queue_state, observed_at) VALUES (1, 'QUEUE_PENDING', '2026-06-12 00:00:00')")
    conn.execute("INSERT INTO amjilsim_drug_queue_status (drug_id, queue_state, observed_at) VALUES (2, 'QUEUE_PENDING', '2026-10-20 00:00:00')")
    conn.commit()
    conn.close()
    assert "pipeline_stale_cards" in DOMAIN_CHECKS
    r = check_pipeline_stale_cards(Ctx(db_path=f"file:{db}?mode=ro", base_dir=tmp_path, today=date(2026, 11, 5)))
    assert r.stale and r.observed == "1" and r.kind == "improvement"
    assert "[MSD] 웰리렉" in r.evidence and "엔허투" not in r.evidence.split("제외")[0]
    owner, action = owner_action(r)
    assert owner == "hermes" and action.startswith("Hermes: 급여 현황 보드 카드 갱신 필요 (1장")


# ── 헤르메스 정본이 들어오면 자동 후보를 승격 (중복 카드 금지) ─────────────────────────

def test_importer_promotes_auto_candidate(tmp_path):
    from agents.ingest.reimb_committee_import import _upsert_drug
    db = _db(tmp_path)
    discover_candidates([_art("엔허투 암질심 상정", "엔허투")], db,
                        extractor=lambda arts: [{"index": 0, "brand_kr": "엔허투"}], permit_lookup=lambda b: MFDS_OK)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    did = find_auto_candidate(conn, "엔허투주")
    assert did is not None
    got = _upsert_drug(conn, {"brand_kr": "엔허투주", "ingredient_inn": "trastuzumab deruxtecan", "manufacturer": "한국다이이찌산쿄",
                              "indication": "HER2 저발현 유방암"})
    conn.commit()
    assert got == did
    rows = conn.execute("SELECT brand_kr, origin FROM amjilsim_drugs WHERE drug_id=?", (did,)).fetchone()
    assert tuple(rows) == ("엔허투주", "news_auto_promoted")
    assert conn.execute("SELECT COUNT(*) FROM amjilsim_drugs").fetchone()[0] == 3
    conn.close()


# ── ③ 공단 연동 차단 ───────────────────────────────────────────────────────────

def test_nhis_matching_skips_auto_candidates_and_never_creates(tmp_path, monkeypatch):
    try:
        import agents.scrapers.nhis_negotiation  # noqa: F401  (lxml 필요 — 샌드박스 등 미설치 환경이면 스텁)
    except ImportError:
        import types
        monkeypatch.setitem(sys.modules, "agents.scrapers.nhis_negotiation", types.ModuleType("nhis_negotiation"))
    from agents.ingest.nhis_negotiation_import import match_and_apply
    db = _db(tmp_path)
    discover_candidates([_art("엔허투 암질심 상정", "엔허투")], db,
                        extractor=lambda arts: [{"index": 0, "brand_kr": "엔허투"}], permit_lookup=lambda b: MFDS_OK)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [{"product_name": "엔허투주", "manufacturer": "한국다이이찌산쿄(주)", "content_hash": "h1", "result": None,
             "is_in_progress": 1, "completed_ym": None, "registered_ym": "2026-10", "efficacy_group": None, "list_type": "신규"},
            {"product_name": "새공단약정", "manufacturer": "어떤제약", "content_hash": "h2", "result": None,
             "is_in_progress": 1, "completed_ym": None, "registered_ym": "2026-10", "efficacy_group": None, "list_type": "신규"}]
    res = match_and_apply(conn, rows, dry_run=True)
    assert res["matched_drugs"] == 0 and {r["product_name"] for r in res["unmatched"]} == {"엔허투주", "새공단약정"}
    assert conn.execute("SELECT COUNT(*) FROM amjilsim_drugs").fetchone()[0] == 3   # 공단 행으로 약제 생성 없음
    conn.close()
