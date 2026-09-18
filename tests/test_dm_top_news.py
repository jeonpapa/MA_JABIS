from agents.daily_mailing.top_news import load_top_ma_news, store_top_ma_news


def test_round_trip(tmp_path):
    db = tmp_path / "d.db"
    assert load_top_ma_news("2026-09-18", db_path=db) is None
    store_top_ma_news("2026-09-18", [{"title": "약평위"}], db_path=db)
    assert load_top_ma_news("2026-09-18", db_path=db) == [{"title": "약평위"}]
    store_top_ma_news("2026-09-18", [], db_path=db)
    assert load_top_ma_news("2026-09-18", db_path=db) == []
