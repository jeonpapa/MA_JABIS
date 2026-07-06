"""GROUP COMPETITOR (B1+B2+B3) — 클러스터링 / tier 정렬 / relevance 배선 검증.

B1: LLM 계약 news_indexes:[int] — 같은 이벤트 기사 N건 → 카드 1장 + trend_id 역링크.
B2: 매체 tier — list_news 정렬(tier ASC 우선), 클러스터 대표 = 최저 tier(최고 신뢰).
B3: news_keyword_factor(competitor/relevance) 로더 + _is_relevant / 제목 가드 소비.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agents import competitor_news_agent as cn
from agents import competitor_trends_agent as ct
from agents.editable_factors import (
    COMPETITOR_RELEVANCE_DEFAULT_TERMS,
    get_competitor_relevance_terms,
    invalidate_cache,
)
from agents.naver_news import NewsItem

BRAND_META = {
    "query": "옵디보", "company": "BMS Korea", "anchor": "키트루다 / PD-(L)1",
    "kind": "competitor", "logo": "BMS", "color": "#3B82F6",
}


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path: Path, monkeypatch) -> Path:
    """임시 sqlite — competitor_news(뉴스 에이전트 스키마) + 전체 DB_SCHEMA(트렌드)."""
    db_path = tmp_path / "test_competitor.db"
    monkeypatch.setattr(cn, "DB_PATH", db_path)
    monkeypatch.setattr(ct, "DB_PATH", db_path)
    cn.ensure_schema()
    from agents.db import DrugPriceDB
    DrugPriceDB(db_path)  # competitor_trend 포함 전체 스키마
    invalidate_cache()
    yield db_path
    invalidate_cache()


def _insert_news(db_path: Path, *, title: str, url: str, tier: int,
                 pub_date: str, source_name: str, description: str = "",
                 brand: str = "옵디보") -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            """INSERT INTO competitor_news
               (url_hash, brand, company, anchor, kind, title, url, naver_link,
                source_domain, source_name, tier, description, pub_date, trend_id,
                collected_via, fetched_at, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)""",
            (cn._url_hash(url), brand, BRAND_META["company"], BRAND_META["anchor"],
             "competitor", title, url, None, cn._domain(url), source_name, tier,
             description, pub_date, "naver",
             datetime.now().isoformat(timespec="seconds"),
             (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _news_item(title: str, desc: str = "", url: str = "http://x.example.com/1") -> NewsItem:
    return NewsItem(title=title, link=url, original_link=url,
                    description=desc, pub_date=datetime.now())


# ── B1: 클러스터링 — 같은 이벤트 N개 기사 → 카드 1장 ─────────────────────────

def test_promote_clusters_same_event_into_one_card(tmp_db: Path, monkeypatch):
    d1 = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    d2 = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    # 같은 이벤트를 다룬 두 기사 — tier 2(종합지) + tier 1(전문지)
    _insert_news(tmp_db, title="옵디보 급여 확대 결정", url="http://general.example.com/a/1",
                 tier=2, pub_date=d1, source_name="종합지A")
    _insert_news(tmp_db, title="옵디보 급여 확대…암질심 통과", url="http://prof.example.com/news/2",
                 tier=1, pub_date=d2, source_name="전문지B")

    monkeypatch.setattr(ct, "get_competitor_brands", lambda *a, **k: [dict(BRAND_META)])
    monkeypatch.setattr(ct, "get_competitor_relevance_terms", lambda *a, **k: ("급여",))
    monkeypatch.setattr(ct, "_llm_filter", lambda news, brand, model: [{
        "news_indexes": [0, 1], "importance": "critical", "badge": "급여 등재",
        "headline": "옵디보 급여 확대", "detail": "옵디보 급여 확대 상세 내용.",
    }])

    res = ct.promote_from_archive(days=30)
    assert res["totals"]["accepted"] == 1
    assert res["totals"]["upserted"] == 1

    conn = sqlite3.connect(str(tmp_db))
    try:
        cards = conn.execute(
            "SELECT id, url, source_tier, source_type, source FROM competitor_trend"
        ).fetchall()
        assert len(cards) == 1, "같은 이벤트 기사 2건은 카드 1장으로 클러스터"
        card_id, url, source_tier, source_type, source = cards[0]
        assert source_type == "promoted"
        # B2: 대표 = 최저 tier(최고 신뢰) — 전문지(T1) 기사가 primary
        assert source_tier == 1
        assert "prof.example.com" in url
        assert source == "전문지B"
        # B1: 멤버 기사 역링크 (trend_id)
        linked = conn.execute(
            "SELECT COUNT(*) FROM competitor_news WHERE trend_id = ?", (card_id,)
        ).fetchone()[0]
        assert linked == 2, "멤버 기사 전부 trend_id 역링크 (source_count>1)"
    finally:
        conn.close()


def test_promote_guard_drops_passing_mention_members(tmp_db: Path, monkeypatch):
    """제목에 브랜드도 relevance 키워드도 없는 멤버는 클러스터에서 제외."""
    d1 = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _insert_news(tmp_db, title="옵디보 급여 확대 결정", url="http://prof.example.com/n/1",
                 tier=1, pub_date=d1, source_name="전문지B")
    _insert_news(tmp_db, title="제약업계 이모저모", url="http://general.example.com/n/2",
                 tier=2, pub_date=d1, source_name="종합지A", description="옵디보 스치는 언급")

    monkeypatch.setattr(ct, "get_competitor_brands", lambda *a, **k: [dict(BRAND_META)])
    monkeypatch.setattr(ct, "get_competitor_relevance_terms", lambda *a, **k: ("급여",))
    monkeypatch.setattr(ct, "_llm_filter", lambda news, brand, model: [{
        "news_indexes": [0, 1], "importance": "moderate", "badge": "급여 등재",
        "headline": "옵디보 급여 확대", "detail": "상세.",
    }])

    ct.promote_from_archive(days=30)
    conn = sqlite3.connect(str(tmp_db))
    try:
        card_id = conn.execute("SELECT id FROM competitor_trend").fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM competitor_news WHERE trend_id = ?", (card_id,)
        ).fetchone()[0]
        assert linked == 1, "가드 미통과 기사는 멤버에서 제외"
    finally:
        conn.close()


def test_parse_news_indexes_new_and_legacy_contract():
    assert ct._parse_news_indexes({"news_indexes": [0, 1, 1, "2"]}) == [0, 1, 2]
    assert ct._parse_news_indexes({"news_indexes": 4}) == [4]
    assert ct._parse_news_indexes({"news_index": 3}) == [3]          # 구 계약 흡수
    assert ct._parse_news_indexes({"news_indexes": "bad"}) == []
    assert ct._parse_news_indexes({}) == []


def test_pick_representative_lowest_tier_then_latest():
    members = [
        {"tier": 2, "url": "u1", "date": "2026-07-05"},
        {"tier": 1, "url": "u2", "date": "2026-07-01"},
        {"tier": 1, "url": "u3", "date": "2026-07-03"},
        {"tier": None, "url": "u4", "date": "2026-07-06"},  # None → 3 취급
    ]
    rep = ct._pick_representative(members)
    assert rep["url"] == "u3"  # tier 1 중 최신


# ── B2: tier 정렬 — 고신뢰 매체 우선 ─────────────────────────────────────────

def test_list_news_orders_by_tier_then_pub_date(tmp_db: Path):
    today = datetime.now().strftime("%Y-%m-%d")
    yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _insert_news(tmp_db, title="종합지 최신 기사 급여", url="http://g.example.com/1",
                 tier=2, pub_date=today, source_name="종합지A")
    _insert_news(tmp_db, title="전문지 어제 기사 급여", url="http://p.example.com/1",
                 tier=1, pub_date=yday, source_name="전문지B")
    _insert_news(tmp_db, title="전문지 오늘 기사 급여", url="http://p.example.com/2",
                 tier=1, pub_date=today, source_name="전문지B")

    rows = cn.list_news(brand="옵디보")
    assert [r["tier"] for r in rows] == [1, 1, 2], "tier ASC(전문지 우선) 정렬"
    assert rows[0]["pub_date"] == today, "같은 tier 내에서는 최신순"


# ── B3: relevance 로더 + 배선 ────────────────────────────────────────────────

def test_relevance_terms_seeded_from_defaults(tmp_path: Path):
    invalidate_cache()
    terms = get_competitor_relevance_terms(tmp_path / "factors.db")
    assert set(terms) == set(COMPETITOR_RELEVANCE_DEFAULT_TERMS)
    invalidate_cache()


def test_relevance_terms_bad_path_falls_back_to_constant(tmp_path: Path):
    invalidate_cache()
    terms = get_competitor_relevance_terms(tmp_path / "no_dir" / "nested" / "f.db")
    assert set(terms) == set(COMPETITOR_RELEVANCE_DEFAULT_TERMS)
    invalidate_cache()


def test_relevance_terms_respects_admin_edit(tmp_path: Path):
    """admin 이 term 을 비활성/추가하면 로더가 그대로 반영 (시드 부활 없음)."""
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    get_competitor_relevance_terms(db_path)  # 최초 시드
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE news_keyword_factor SET active = 0 "
            "WHERE scope='competitor' AND kind='relevance' AND term = ?",
            (COMPETITOR_RELEVANCE_DEFAULT_TERMS[0],),
        )
        conn.execute(
            """INSERT INTO news_keyword_factor
               (scope, kind, agency, term, active, created_at, updated_at)
               VALUES ('competitor','relevance',NULL,'사용자키워드',1,'','')""",
        )
        conn.commit()
    finally:
        conn.close()
    invalidate_cache()
    terms = get_competitor_relevance_terms(db_path)
    assert COMPETITOR_RELEVANCE_DEFAULT_TERMS[0] not in terms
    assert "사용자키워드" in terms
    invalidate_cache()


def test_is_relevant_consults_relevance_terms():
    terms = ("급여",)
    assert cn._is_relevant(_news_item("옵디보 급여 확대"), "옵디보", terms)
    # 브랜드는 있으나 relevance 키워드 전무 (주가성 잡음) → 제외
    assert not cn._is_relevant(_news_item("옵디보 관련주 상승"), "옵디보", terms)
    # 브랜드 자체가 없으면 무조건 제외
    assert not cn._is_relevant(_news_item("급여 확대 소식"), "옵디보", terms)
    # terms 미전달(None) → 브랜드-only 구 동작 (amjilsim tiered_news 등 타 호출자 보호)
    assert cn._is_relevant(_news_item("옵디보 관련주 상승"), "옵디보")


def test_crawl_naver_axis_uses_relevance_terms(tmp_db: Path, monkeypatch):
    """crawl() 이 로더의 relevance 키워드로 _is_relevant 를 실제 소비하는지 확인."""
    monkeypatch.setattr(cn, "get_competitor_relevance_terms", lambda *a, **k: ("급여",))
    monkeypatch.setattr(cn, "get_competitor_brands", lambda *a, **k: [dict(BRAND_META)])

    class _FakeClient:
        is_configured = True

        def search(self, query, display=100, start=1, sort="date"):
            if start > 1:
                return []
            return [
                _news_item("옵디보 급여 확대 결정", url="http://www.dailypharm.com/n/1"),
                _news_item("옵디보 관련주 급등", url="http://www.dailypharm.com/n/2"),
            ]

    monkeypatch.setattr(cn, "NaverNewsClient", lambda: _FakeClient())
    monkeypatch.setattr(cn._t1sites, "search_all_sites", lambda q, d: [])

    res = cn.crawl(lookback_days=7, t1_only=False)
    r = res["results"][0]
    assert r["stored"] == 1, "relevance 키워드 없는 주가성 기사는 제외"
    assert r["skipped_irrelevant"] == 1


def test_title_guard_consults_relevance_terms(monkeypatch):
    monkeypatch.setattr(ct, "get_competitor_relevance_terms", lambda *a, **k: ("급여",))
    # 브랜드가 제목에 → 통과
    assert ct._passes_relevance_guard("옵디보", "옵디보 신약 소식")
    # 브랜드는 발췌에만 + relevance 키워드가 제목에 → 통과 (admin 키워드가 구조)
    assert ct._passes_relevance_guard("옵디보", "PD-1 급여 확대 결정", "옵디보 병용 요법")
    # 브랜드 발췌에만 + 키워드 없음 → drop (본문 스치는 언급)
    assert not ct._passes_relevance_guard("옵디보", "PD-1 시장 동향", "옵디보 언급")
    # 브랜드 어디에도 없음 → drop
    assert not ct._passes_relevance_guard("옵디보", "급여 확대 결정", "무관 기사")
