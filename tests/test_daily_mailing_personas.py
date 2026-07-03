from __future__ import annotations

from agents.daily_mailing.discovery import DEFAULT_KEYWORDS, NewsDiscoveryItem, expand_keywords_for_personas, rank_items
from agents.daily_mailing.personas import resolve_personas, resolve_reviewer_roles
from agents.daily_mailing.review_board import build_article_card
from agents.daily_mailing.writer import assess_article_quality, assess_content_completeness, render_daily_draft_html, render_daily_draft_markdown, should_include_in_top_signals


def _item(**overrides) -> NewsDiscoveryItem:
    base = dict(
        title="MSD 키트루다 급여 신청 재조명",
        publisher_url="https://www.dailypharm.com/user/news/persona",
        naver_url="",
        description="키트루다 환자 접근성과 급여 신청 이슈를 다룬 기사",
        published_at="2026-07-01T05:00:00+09:00",
        keyword="MSD",
        discovery_channel="naver_news_api",
        source_name="데일리팜",
        source_tier="media_tier_A",
        source_weight=3.0,
        ma_depth=5,
        novelty=3,
        volume=5,
        matched_keywords=("MSD",),
    )
    base.update(overrides)
    return NewsDiscoveryItem(**base)


def test_persona_keyword_expansion_preserves_user_keywords_and_adds_defaults():
    personas = resolve_personas(["brand_strategy"])

    expanded = expand_keywords_for_personas(["custom oncology"], personas)

    assert "custom oncology" in expanded
    assert "키트루다" in expanded
    assert "MSD" in expanded


def test_persona_priority_terms_boost_relevant_items():
    personas = resolve_personas(["brand_strategy"])
    msd = _item(title="MSD 키트루다 급여 신청 재조명")
    general = _item(
        title="일반 제약 산업 기사",
        description="특정 제품이나 회사 언급이 없는 일반 기사",
        publisher_url="https://example.com/general",
        source_weight=3.0,
        ma_depth=0,
        novelty=0,
        matched_keywords=(),
    )

    ranked = rank_items([general, msd], keywords=["급여"], personas=personas)

    assert ranked[0].title == "MSD 키트루다 급여 신청 재조명"


def test_content_completeness_flags_missing_source_and_short_description():
    personas = resolve_personas(["ma_lead"])
    report = assess_content_completeness(
        {
            "title": "약평위 급여 기사",
            "description": "짧음",
            "source_name": "Unknown publisher",
            "source_tier": "unregistered",
        },
        personas=personas,
    )

    assert "source_url" in report["missing"]
    assert "published_at" in report["missing"]
    assert "source_name" in report["missing"]
    assert "short_description_requires_source_check" in report["warnings"]
    assert report["score"] < 100


def test_low_ma_msd_mentions_stay_watchlist_not_top_signal():
    item = _item(
        title="MSD 임직원 사회공헌 캠페인",
        description="MSD 임직원 봉사와 질환 교육 캠페인",
        source_name="일반매체",
        source_tier="unregistered",
        ma_depth=0,
        matched_keywords=("MSD",),
    )

    assert should_include_in_top_signals(item) is False
    md = render_daily_draft_markdown(items=[item], keywords=["MSD"], max_items=1)
    assert "Top Monitoring Highlights" not in md
    assert "Watchlist (1)" in md
    assert "Executive Snapshot" in md
    assert "Official Follow-up Checklist" in md


def test_html_article_cards_group_each_story_into_reader_friendly_container():
    item = _item(
        title="HRD 양성 난소암 린파자 병용 유지요법 급여 확대 촉각",
        description="암질심 통과 후 약평위 상정 예정이며 급여 사각지대와 재정영향이 쟁점이다.",
        source_name="청년의사",
        source_tier="media_tier_B",
        publisher_url="https://www.docdocdoc.co.kr/news/articleView.html?idxno=3040517",
        matched_keywords=("린파자", "약평위"),
    )

    html = render_daily_draft_html(items=[item], keywords=["린파자", "약평위"], max_items=1)

    assert 'data-component="article-card"' in html
    assert 'aria-label="기사 카드: HRD 양성 난소암 린파자 병용 유지요법 급여 확대 촉각"' in html
    assert "핵심 기사 요약" in html
    assert "MA/Business Implication" in html
    assert "다음 확인 포인트" in html
    assert "원문 보기" in html
    assert "청년의사" in html
    assert html.index("핵심 기사 요약") < html.index("MA/Business Implication") < html.index("다음 확인 포인트")


def test_reviewer_roles_resolve_default_service_lanes():
    role_ids = [role.role_id for role in resolve_reviewer_roles(None)]

    assert role_ids == [
        "source_verifier",
        "ma_strategist",
        "competitive_intel",
        "clinical_context",
        "executive_editor",
        "compliance_safety",
    ]


def test_keytruda_direct_candidate_is_promoted_to_original_source_verification():
    item = _item(
        title="키트루다 급여 확대 신청 재조명",
        description="MSD 키트루다 환자 접근성과 급여 신청 이슈를 다룬 기사",
        source_name="일반매체",
        source_tier="unregistered",
        publisher_url="https://regional.example.com/keytruda-access",
        matched_keywords=("키트루다",),
    )

    quality = assess_article_quality(item)
    card = build_article_card(item, selected_for_draft=True, reviewer_roles=resolve_reviewer_roles(["source_verifier"]))

    assert quality["priority"] == "High"
    assert "keytruda_direct_source_verification_promoted" in quality["quality_flags"]
    assert card["review_status"] == "ready_for_writer"
    assert card["tracking_lane"] == "keytruda_source_verification"
    assert card["next_action"].startswith("Keytruda 직접 관련 후보")


def test_pricing_policy_theme_is_accumulated_as_policy_tracker():
    item = _item(
        title="한계 직면 1약=1약가…적응증별 약가 정책 테이블에 올랐다",
        description="적응증별 약가와 RSA, 사용량-약가 연동제가 정책 논의로 재부상했다.",
        source_name="히트뉴스",
        source_tier="media_tier_A",
        matched_keywords=("적응증별 약가", "RSA", "사용량-약가"),
    )

    quality = assess_article_quality(item)
    card = build_article_card(item, selected_for_draft=True)

    assert "policy_pricing_tracker" in quality["quality_flags"]
    assert card["tracking_lane"] == "policy_pricing_tracker"
    assert card["tracker_tags"] == ["indication_based_pricing", "rsa", "price_volume"]


def test_competitor_axis_keywords_are_user_selectable_not_default_scope():
    default_text = " ".join(DEFAULT_KEYWORDS).lower()

    assert "padcev" not in default_text
    assert "pd-1 vegf" not in default_text
    assert "adc 항암제" not in default_text
