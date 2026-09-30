from agents.daily_mailing.top_news import (
    TOP_NEWS_FAILED,
    TOP_NEWS_OK,
    load_top_ma_news,
    load_top_ma_news_entry,
    mark_top_ma_news_failed,
    store_top_ma_news,
)


def test_round_trip(tmp_path):
    db = tmp_path / "d.db"
    assert load_top_ma_news("2026-09-18", db_path=db) is None
    store_top_ma_news("2026-09-18", [{"title": "약평위"}], db_path=db)
    assert load_top_ma_news("2026-09-18", db_path=db) == [{"title": "약평위"}]
    store_top_ma_news("2026-09-18", [], db_path=db)
    assert load_top_ma_news("2026-09-18", db_path=db) == []


def test_failed_marker_is_cached_as_empty_list_with_status(tmp_path):
    """생성 실패를 그날의 negative 마커로 남긴다 — 이후 tick 은 빈 목록을 쓰고 엔진을 다시 돌리지 않는다."""
    db = tmp_path / "d.db"
    assert load_top_ma_news_entry("2026-09-18", db_path=db) is None
    mark_top_ma_news_failed("2026-09-18", "LLMError: down", db_path=db)
    entry = load_top_ma_news_entry("2026-09-18", db_path=db)
    assert (entry["items"], entry["status"], entry["error"]) == ([], TOP_NEWS_FAILED, "LLMError: down")
    assert load_top_ma_news("2026-09-18", db_path=db) == []
    store_top_ma_news("2026-09-18", [{"title": "약평위"}], db_path=db)        # 성공 저장이 마커를 덮는다
    entry = load_top_ma_news_entry("2026-09-18", db_path=db)
    assert (entry["items"], entry["status"], entry["error"]) == ([{"title": "약평위"}], TOP_NEWS_OK, None)


def test_status_columns_are_added_to_pre_existing_table(tmp_path):
    import sqlite3
    db = tmp_path / "d.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE daily_mailing_top_news (run_date TEXT PRIMARY KEY, items_json TEXT NOT NULL, created_at TEXT NOT NULL)")
        c.execute("INSERT INTO daily_mailing_top_news VALUES ('2026-09-17', '[{\"title\": \"x\"}]', 'c')")
    entry = load_top_ma_news_entry("2026-09-17", db_path=db)
    assert entry["status"] == TOP_NEWS_OK and entry["items"] == [{"title": "x"}]
