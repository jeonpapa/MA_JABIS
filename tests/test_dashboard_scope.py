from __future__ import annotations

import json
from pathlib import Path

from agents.daily_mailing.dashboard_scope import load_dashboard_scope


def test_dashboard_scope_json_expands_user_selected_keywords(tmp_path: Path):
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps({
        "subscription_id": "joseph-ma",
        "owner_email": "yo.seop.jeon@msd.com",
        "recipients": ["yo.seop.jeon@msd.com"],
        "companies": ["MSD", "한국MSD"],
        "brands": ["Keytruda"],
        "aliases": {"Keytruda": ["키트루다", "pembrolizumab"]},
        "policy_topics": ["약평위", "암질심"],
        "media": ["dailypharm", "yakup"],
        "personas": ["ma_lead", "brand_strategy"],
        "lookback_hours": 24
    }, ensure_ascii=False), encoding="utf-8")

    scope = load_dashboard_scope(scope_path)

    assert scope.media == ("dailypharm", "yakup")
    assert scope.personas == ("ma_lead", "brand_strategy")
    assert scope.expanded_keywords() == ["MSD", "한국MSD", "Keytruda", "약평위", "암질심", "키트루다", "pembrolizumab"]
