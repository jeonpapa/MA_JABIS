from __future__ import annotations

import json

from agents.daily_mailing.dashboard_scope import load_dashboard_scope, scope_from_dict


def test_scope_from_dict_matches_file_loader(tmp_path):
    data = {"subscription_id": 19, "name": "파드셉", "keywords": ["약가 인하"], "brands": ["파드셉"],
            "aliases": {"파드셉": "Padcev"}, "include_top_ma_signals": False, "lookback_hours": 36}
    scope = scope_from_dict(data)
    assert scope.subscription_id == "19"
    assert scope.brands == ("파드셉",)
    assert scope.aliases == {"파드셉": ("Padcev",)}
    assert scope.include_top_ma_signals is False
    assert scope.lookback_hours == 36
    path = tmp_path / "s.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert load_dashboard_scope(path) == scope
