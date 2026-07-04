from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.daily_mailing.subscription_bridge import subscription_to_scope, write_scope_snapshot
from agents.daily_mailing.dashboard_scope import DashboardScope


def test_subscription_maps_to_valid_scope():
    sub = {"id": 7, "name": "Joseph MA Daily", "owner_email": "j@msd.com",
           "keywords": ["키트루다", "약평위"], "media": ["dailypharm", "yakup"],
           "emails": ["j@msd.com"], "schedule": "Daily", "time": "08:00"}
    scope = subscription_to_scope(sub)
    assert scope["subscription_id"] == "7"
    assert scope["owner_email"] == "j@msd.com"
    assert scope["recipients"] == ["j@msd.com"]
    assert scope["keywords"] == ["키트루다", "약평위"]
    assert scope["media"] == ["dailypharm", "yakup"]
    assert scope["personas"] == ["ma_lead", "brand_strategy", "policy_watch"]
    # dashboard_scope 계약으로 로드 가능해야 함(헤르메스가 동일 계약 소비)
    ds = DashboardScope(
        subscription_id=scope["subscription_id"], owner_email=scope["owner_email"],
        recipients=tuple(scope["recipients"]), keywords=tuple(scope["keywords"]),
        media=tuple(scope["media"]))
    assert "키트루다" in ds.expanded_keywords()


def test_write_scope_snapshot_roundtrip(tmp_path):
    sub = {"id": 3, "name": "n", "owner_email": "o@x.com", "keywords": ["a"],
           "media": [], "emails": ["o@x.com"]}
    scope = subscription_to_scope(sub)
    p = write_scope_snapshot(scope, root=tmp_path)
    assert p.exists() and p.name == "3.json"
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["subscription_id"] == "3" and loaded["keywords"] == ["a"]


def test_custom_sources_flow_into_scope():
    sub = {"id": 5, "name": "n", "owner_email": "j@msd.com", "keywords": ["a"], "media": ["dailypharm"],
           "emails": ["j@msd.com"], "custom_sources": [{"url": "https://example.com", "name": "예시"}]}
    scope = subscription_to_scope(sub)
    assert scope["custom_sources"] == [{"url": "https://example.com", "name": "예시"}]


def test_scope_snapshot_preserves_test_request(tmp_path):
    sub = {"id": 9, "name": "n", "owner_email": "j@msd.com", "keywords": ["a"], "media": [], "emails": ["j@msd.com"]}
    scope = subscription_to_scope(sub)
    scope["test_request"] = {"requested_at": "2026-07-04T00:00:00Z", "requested_by": "j@msd.com"}
    p = write_scope_snapshot(scope, root=tmp_path)
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert loaded["test_request"]["requested_by"] == "j@msd.com"
