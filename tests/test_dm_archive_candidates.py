from __future__ import annotations

import sqlite3
from datetime import date

from agents.daily_mailing.archive_candidates import load_archive_candidates
from agents.daily_mailing.source_registry import SourceInfo, SourceRegistry


def _db(tmp_path):
    path = tmp_path / "a.db"
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE competitor_news (id INTEGER PRIMARY KEY, url_hash TEXT, brand TEXT, kind TEXT,
            title TEXT, url TEXT, naver_link TEXT, description TEXT, pub_date TEXT, source_name TEXT, tier INTEGER)""")
        rows = [
            ("competitor", "파드셉 급여 확대 논의", "https://www.yakup.com/1?utm_source=x", "https://n.news.naver.com/1", "요로상피암", "2026-09-18", "약업신문", 1),
            ("competitor", "파드셉 중복 URL", "https://www.yakup.com/1", "", "", "2026-09-17", "약업신문", 1),
            ("gov_policy", "키트루다 뉴스", "https://www.hitnews.co.kr/2", "", "파드셉 병용", "2026-09-17", "히트뉴스", 1),
            ("competitor", "오래된 파드셉", "https://www.yakup.com/3", "", "", "2026-09-10", "약업신문", 1),
            ("gov_policy", "무관 기사", "https://www.yakup.com/4", "", "", "2026-09-18", "약업신문", 1),
        ]
        c.executemany("INSERT INTO competitor_news (url_hash, brand, kind, title, url, naver_link, description, pub_date, source_name, tier) VALUES ('h','b',?,?,?,?,?,?,?,?)", rows)
    return path


def test_loads_matching_recent_unique(tmp_path):
    registry = SourceRegistry([SourceInfo(name="약업신문", domains=("yakup.com",), source_tier="media_tier_A", weight=1.3)])
    items = load_archive_candidates(["파드셉"], db_path=_db(tmp_path), today=date(2026, 9, 18), registry=registry)
    titles = [i.title for i in items]
    assert titles == ["파드셉 급여 확대 논의", "키트루다 뉴스"]
    first = items[0]
    assert first.publisher_url == "https://www.yakup.com/1"
    assert first.discovery_channel == "news_archive"
    assert first.source_tier == "media_tier_A" and first.source_weight == 1.3
    assert first.matched_keywords == ("파드셉",)
    assert first.published_at == "2026-09-18T00:00:00+09:00"


def test_kinds_filter_restricts_to_given_kinds(tmp_path):
    db = _db(tmp_path)
    gov = load_archive_candidates(["파드셉"], db_path=db, today=date(2026, 9, 18), kinds=("gov_policy",))
    assert [i.title for i in gov] == ["키트루다 뉴스"]
    comp = load_archive_candidates(["파드셉"], db_path=db, today=date(2026, 9, 18), kinds=("competitor",))
    assert [i.title for i in comp] == ["파드셉 급여 확대 논의"]
    both = load_archive_candidates(["파드셉"], db_path=db, today=date(2026, 9, 18), kinds=("gov_policy", "competitor"))
    assert [i.title for i in both] == ["파드셉 급여 확대 논의", "키트루다 뉴스"]
    assert load_archive_candidates(["파드셉"], db_path=db, today=date(2026, 9, 18), kinds=("nope",)) == []


def test_kinds_filter_without_kind_column_returns_empty(tmp_path):
    path = tmp_path / "nokind.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE competitor_news (id INTEGER PRIMARY KEY, title TEXT, url TEXT, naver_link TEXT, description TEXT, pub_date TEXT, source_name TEXT)")
        c.execute("INSERT INTO competitor_news (title, url, naver_link, description, pub_date, source_name) VALUES ('파드셉 기사', 'https://www.yakup.com/9', '', '', '2026-09-18', '약업신문')")
    assert load_archive_candidates(["파드셉"], db_path=path, today=date(2026, 9, 18), kinds=("gov_policy",)) == []
    # kinds 미지정이면 kind 컬럼 없이도 동작
    assert [i.title for i in load_archive_candidates(["파드셉"], db_path=path, today=date(2026, 9, 18))] == ["파드셉 기사"]


def test_missing_db_or_table_or_terms_returns_empty(tmp_path):
    assert load_archive_candidates(["x"], db_path=tmp_path / "none.db", today=date(2026, 9, 18)) == []
    empty = tmp_path / "e.db"
    sqlite3.connect(empty).close()
    assert load_archive_candidates(["x"], db_path=empty, today=date(2026, 9, 18)) == []
    assert load_archive_candidates([], db_path=_db(tmp_path), today=date(2026, 9, 18)) == []
