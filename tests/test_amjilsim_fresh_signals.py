"""Access Insight S5 — 신선 신호 크롤러(tier A/B/D) + signal_extractor 테스트.

네트워크 없이 fixture 주입(naver_fetch/site_search)으로 검증한다.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.naver_news import NewsItem
from agents.scrapers.tier1_news_sites import SiteNewsItem
from agents.amjilsim_tracker.crawlers import tier_a, tier_b, tier_d, tiered_news
from agents.amjilsim_tracker.signal_extractor import extract_signals, run_fresh_crawl
from agents.access_insight.classify import (
    GOV_STATEMENT,
    KOL_OPINION,
    PATIENT_PETITION,
    signal_weight,
)
from agents.access_insight.link import invalidate_index_cache


# ── 테스트 DB (S1 테스트 스키마와 동일 계약) ──────────────────────────────────

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
CREATE TABLE amjilsim_media_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drug_id INTEGER,
    session_id INTEGER,
    tier TEXT,
    outlet TEXT NOT NULL,
    url TEXT NOT NULL,
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
    raw_html_path TEXT,
    UNIQUE(outlet, url)
);
"""


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO amjilsim_drugs (drug_id, product_slug, brand_kr, brand_en, ingredient_inn, expected_session_id) VALUES "
        "(1, 'welireg', '웰리렉', 'Welireg', 'belzutifan', NULL),"
        "(2, 'keytruda', '키트루다', 'Keytruda', 'pembrolizumab', NULL),"
        "(5, NULL, '베오바정 50mg 외 1품목', NULL, NULL, NULL)"
    )
    conn.execute(
        "INSERT INTO product_alias_map (product_slug, inn, brand_aliases_json) VALUES "
        "('keytruda', 'pembrolizumab', ?), ('welireg', 'belzutifan', ?)",
        (
            json.dumps(["Keytruda", "키트루다", "키트루다주", "펨브롤리주맙"], ensure_ascii=False),
            json.dumps(["Welireg", "웰리렉", "웰리렉정", "벨주티판"], ensure_ascii=False),
        ),
    )
    conn.execute(
        "INSERT INTO amjilsim_sessions (session_id, year, ordinal_official, session_date, status, committee_type) VALUES "
        "(100, 2026, 1, '2026-01-10', 'COMPLETED', 'AMJILSIM'),"
        "(101, 2026, 2, '2026-07-08', 'SCHEDULED', 'AMJILSIM'),"
        "(102, 2026, 3, '2026-09-05', 'SCHEDULED', 'YAKPYUNGWI')"
    )
    conn.commit()
    conn.close()
    invalidate_index_cache()


# ── fixture fetchers ─────────────────────────────────────────────────────────

def _news(title, url, desc, y=2026, m=7, d=1):
    return NewsItem(
        title=title, link=url, original_link=url,
        description=desc, pub_date=datetime(y, m, d),
    )


def _site(title, url, desc, source_name="뉴스더보이스", domain="newsthevoice.com"):
    return SiteNewsItem(
        title=title, url=url, description=desc,
        pub_date=datetime(2026, 7, 2), source_name=source_name, source_domain=domain,
    )


def _fixture_naver(keyword, lookback_days):
    if keyword != "키트루다":
        return []
    return [
        # tier1 도메인 → A
        _news("키트루다 급여 확대 논의", "https://www.dailypharm.com/news/1", "약평위 상정 예정"),
        # tier2 도메인 → B
        _news("키트루다 관련 국회 질의", "https://www.yna.co.kr/view/2", "국정감사 키트루다 지적"),
        # 미매핑 도메인 → D
        _news("키트루다 블로그성 기사", "https://www.unknown-media.co.kr/3", "키트루다 소식"),
        # 관련성 게이트: 표면에 키워드 없음 → 제외
        _news("무관한 파이프라인 기사", "https://www.dailypharm.com/news/4", "타사 IR 나열"),
    ]


def _fixture_site(keyword, lookback_days):
    if keyword != "키트루다":
        return []
    return [
        # Naver 축과 동일 기사 (canonical dedupe 대상)
        _site("키트루다 급여 확대 논의", "https://dailypharm.com/news/1", "약평위 상정 예정", "데일리팜", "dailypharm.com"),
        # 신규 (Naver 미인덱싱 갭필러)
        _site("키트루다 학회 좌담", "https://www.newsthevoice.com/news/articleView.html?idxno=55", "대한암학회 교수 좌담"),
    ]


# ── 크롤러: 정규화 + tier 버킷 + dedupe ─────────────────────────────────────

def test_fetch_articles_normalizes_buckets_and_dedupes():
    arts = tiered_news.fetch_articles(
        ["키트루다"], 8, naver_fetch=_fixture_naver, site_search=_fixture_site)
    by_url = {a.url: a for a in arts}

    # dedupe: dailypharm 기사 1건만 (Naver 축 우선, 사이트 축 중복 제거)
    assert len(arts) == 4
    a1 = by_url["https://www.dailypharm.com/news/1"]
    assert (a1.tier, a1.outlet) == ("A", "데일리팜")
    assert a1.published_at == datetime(2026, 7, 1)
    assert a1.snippet == "약평위 상정 예정"
    assert by_url["https://www.yna.co.kr/view/2"].tier == "B"
    d_art = by_url["https://www.unknown-media.co.kr/3"]
    assert d_art.tier == "D"
    assert d_art.outlet == "unknown-media.co.kr"  # 미등록 매체는 도메인
    # 사이트 축 신규 기사 → A
    assert by_url["https://www.newsthevoice.com/news/articleView.html?idxno=55"].tier == "A"
    # 관련성 게이트로 무관 기사 제외
    assert "https://www.dailypharm.com/news/4" not in by_url


def test_tier_packages_filter_by_tier(tmp_path):
    kwargs = dict(keywords=["키트루다"], lookback_days=8,
                  naver_fetch=_fixture_naver, site_search=_fixture_site)
    a = tier_a.crawl(**kwargs)
    b = tier_b.crawl(**kwargs)
    d = tier_d.crawl(**kwargs)
    assert {x.tier for x in a} == {"A"} and len(a) == 2
    assert {x.tier for x in b} == {"B"} and len(b) == 1
    assert {x.tier for x in d} == {"D"} and len(d) == 1
    assert (tier_a.MEDIA_TIER, tier_b.MEDIA_TIER, tier_d.MEDIA_TIER) == ("A", "B", "D")


def test_fetch_articles_isolates_fetcher_failure():
    def _boom(keyword, lookback_days):
        raise RuntimeError("network down")

    arts = tiered_news.fetch_articles(
        ["키트루다"], 8, naver_fetch=_boom, site_search=_fixture_site)
    # Naver 축 실패해도 사이트 축은 진행
    assert len(arts) == 2
    assert all(a.tier == "A" for a in arts)


def test_default_keywords_from_amjilsim_drugs(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)
    kws = tiered_news.default_keywords(db)
    assert "웰리렉" in kws
    assert "키트루다" in kws
    # 다품목 나열은 검색어로 쓰지 않음
    assert not any("외 1품목" in k for k in kws)
    # 위원회 보조 키워드 포함
    assert "암질심" in kws and "약제급여평가위원회" in kws


# ── signal_extractor: 매핑·분류·멱등 INSERT ─────────────────────────────────

def _fixture_articles():
    return [
        # 국회 기사 → 웰리렉(drug 1) + GOV_STATEMENT, snippet 거명 → snippet_match
        tiered_news.TieredArticle(
            outlet="연합뉴스", url="https://yna.co.kr/gov1",
            title="국회 보건복지위, 신약 급여 질의",
            published_at=datetime(2026, 7, 1),
            snippet="국정감사에서 웰리렉 급여 지연 지적", tier="B"),
        # 환자단체 기사 → 키트루다(drug 2) + PATIENT_PETITION (암질심 언급 → committee_target)
        tiered_news.TieredArticle(
            outlet="데일리팜", url="https://dailypharm.com/pat1",
            title="환자단체, 키트루다 급여 확대 청원",
            published_at=datetime(2026, 7, 2),
            snippet="암질심 앞두고 환우회 성명", tier="A"),
        # 학회 기사 → 키트루다 + KOL_OPINION, 제목만 거명 → headline_only
        tiered_news.TieredArticle(
            outlet="뉴스더보이스", url="https://newsthevoice.com/kol1",
            title="대한암학회 교수 좌담 — 키트루다 진료지침",
            published_at=datetime(2026, 8, 1),
            snippet="전문가 의견 제시", tier="A"),
        # 약물 미거명 → skip
        tiered_news.TieredArticle(
            outlet="기타", url="https://etc.com/none",
            title="무관한 제약 정책 기사",
            published_at=datetime(2026, 7, 3),
            snippet="약가 제도 일반론", tier="D"),
    ]


def test_extractor_maps_classifies_and_inserts(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)

    stats = extract_signals(_fixture_articles(), db_path=db)
    assert stats["scanned"] == 4
    assert stats["matched"] == 3
    assert stats["unmatched"] == 1
    assert stats["inserted"] == 3
    assert stats["by_tier"] == {"A": 2, "B": 1}

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = {r["url"]: r for r in conn.execute("SELECT * FROM amjilsim_media_signals")}
    conn.close()
    assert len(rows) == 3

    gov = rows["https://yna.co.kr/gov1"]
    assert gov["drug_id"] == 1
    assert gov["signal_type"] == GOV_STATEMENT
    assert gov["tier"] == "B"
    assert gov["weight"] == signal_weight("B", GOV_STATEMENT)
    assert gov["source_verified"] == "snippet_match"
    assert gov["session_id"] == 101          # 2026-07-01 이후 최근접 차수 (7/8)
    assert gov["crossref_count"] == 1        # 웰리렉 거명 매체 1곳
    assert gov["published_at"] == "2026-07-01"
    assert json.loads(gov["signal_phrases"])  # lexicon 매치 보존

    pat = rows["https://dailypharm.com/pat1"]
    assert pat["drug_id"] == 2
    assert pat["signal_type"] == PATIENT_PETITION
    assert pat["committee_target"] == "AMJILSIM"   # '암질심' 표면어
    assert pat["crossref_count"] == 2              # 키트루다 거명 매체 2곳

    kol = rows["https://newsthevoice.com/kol1"]
    assert kol["drug_id"] == 2
    assert kol["signal_type"] == KOL_OPINION
    assert kol["source_verified"] == "headline_only"  # 발췌에 약물 미거명
    assert kol["session_id"] == 102                   # 2026-08-01 이후 최근접 (9/5)


def test_extractor_is_idempotent_second_run_inserts_zero(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)

    first = extract_signals(_fixture_articles(), db_path=db)
    assert first["inserted"] == 3
    second = extract_signals(_fixture_articles(), db_path=db)
    assert second["inserted"] == 0
    assert second["duplicate_skipped"] == 3

    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM amjilsim_media_signals").fetchone()[0]
    conn.close()
    assert n == 3


def test_extractor_preserves_existing_backfill_rows(tmp_path):
    """S1 백필 기존 행과 같은 (url, drug) 기사가 신선 크롤에 다시 잡혀도
    기존 행은 그대로 (INSERT-only, no UPDATE)."""
    db = tmp_path / "t.db"
    _seed_db(db)

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO amjilsim_media_signals (drug_id, session_id, tier, outlet, url, title, "
        "published_at, snippet, signal_type, signal_phrases, crossref_count, weight, source_verified) "
        "VALUES (2, 100, 'A', '데일리팜', 'https://dailypharm.com/pat1', '백필 원본 제목', "
        "'2026-05-01', '백필 스니펫', 'IR_RELEASE', '[]', 0, 0.96, 'snippet_match')")
    conn.commit()
    conn.close()

    stats = extract_signals(_fixture_articles(), db_path=db)
    assert stats["duplicate_skipped"] == 1
    assert stats["inserted"] == 2

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM amjilsim_media_signals WHERE url='https://dailypharm.com/pat1'"
    ).fetchone()
    n = conn.execute(
        "SELECT COUNT(*) FROM amjilsim_media_signals WHERE url='https://dailypharm.com/pat1'"
    ).fetchone()[0]
    conn.close()
    assert n == 1
    assert row["title"] == "백필 원본 제목"        # 기존 행 무변경
    assert row["signal_type"] == "IR_RELEASE"


def test_run_fresh_crawl_end_to_end_idempotent(tmp_path):
    db = tmp_path / "t.db"
    _seed_db(db)

    kwargs = dict(
        db_path=db, keywords=["키트루다"], lookback_days=8,
        naver_fetch=_fixture_naver, site_search=_fixture_site,
    )
    first = run_fresh_crawl(**kwargs)
    assert first["articles"] == 4
    assert first["inserted"] == first["matched"] > 0
    second = run_fresh_crawl(**kwargs)
    assert second["inserted"] == 0
    assert second["duplicate_skipped"] == first["inserted"]


# ── 스케줄러 등록 smoke ──────────────────────────────────────────────────────

def test_scheduler_job_defined_and_registered():
    import scheduler as sched

    assert callable(getattr(sched, "access_insight_fresh_signal_job", None))
    src = Path(sched.__file__).read_text(encoding="utf-8")
    assert 'id="access_insight_fresh_signals"' in src
    assert 'day_of_week="mon"' in src
