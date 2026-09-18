from __future__ import annotations

import sqlite3

from agents.daily_mailing.subscription_bridge import ensure_delivery_columns, subscription_to_scope


def test_ensure_delivery_columns_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    conn.execute("CREATE TABLE mail_subscription (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO mail_subscription (name) VALUES ('x')")
    ensure_delivery_columns(conn)
    ensure_delivery_columns(conn)
    row = conn.execute("SELECT delivery_mode, include_top_ma_news FROM mail_subscription").fetchone()
    assert row == ("approval", 1)


def test_ensure_delivery_columns_without_table_is_noop(tmp_path):
    conn = sqlite3.connect(tmp_path / "e.db")
    ensure_delivery_columns(conn)  # no error


def test_scope_mapping():
    scope = subscription_to_scope({"id": 19, "name": "파드셉", "emails": ["r@msd.com"], "brands": ["파드셉"],
                                   "delivery_mode": "auto", "include_top_ma_news": False})
    assert scope["delivery_mode"] == "auto"
    assert scope["include_top_ma_signals"] is False
    default = subscription_to_scope({"id": 1, "name": "n"})
    assert default["delivery_mode"] == "approval"
    assert default["include_top_ma_signals"] is True
