from agents.daily_mailing.scope_validation import MSD_BRANDS, validate_scope


def test_empty_scope_rejected():
    errors, _ = validate_scope({"name": "x"}, [])
    assert errors


def test_policy_only_scope_allowed():
    errors, warnings = validate_scope({"name": "정책", "policy_topics": ["약가 유연계약"]}, MSD_BRANDS)
    assert errors == [] and warnings == []


def test_name_brand_missing_from_terms_warns():
    errors, warnings = validate_scope({"name": "파드셉", "keywords": ["약가 인하", "급여 등재"]}, ["파드셉", "키트루다"])
    assert errors == []
    assert warnings == ["구독 이름의 '파드셉' 가 키워드·브랜드에 없습니다 — 이 약제 기사는 수집되지 않습니다"]
    _, ok = validate_scope({"name": "파드셉", "brands": ["파드셉"]}, ["파드셉"])
    assert ok == []
