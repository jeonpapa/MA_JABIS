from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from agents.daily_mailing.discovery import (
    NewsDiscoveryItem,
    SourceRegistry,
    discover_naver_news,
)
from agents.daily_mailing.writer import render_daily_draft_html
from agents.notify.gmail_delivery import create_gmail_draft, gmail_configured
from agents.daily_mailing.quality import evaluate_draft_quality
from agents.daily_mailing.calibration import CALIBRATION_SYSTEM_PROMPT, ARTICLE_CARD_SCHEMA


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({
            "items": [
                {
                    "title": "약업신문 약평위 급여 기사",
                    "originallink": "https://www.yakup.com/news/index.html?mode=view&cat=1&nid=1&utm_source=naver",
                    "link": "https://n.news.naver.com/mnews/article/001/1",
                    "description": "약평위 급여 적정성 기사",
                    "pubDate": "Tue, 30 Jun 2026 05:00:00 +0900",
                },
                {
                    "title": "데일리팜 약평위 급여 기사",
                    "originallink": "https://www.dailypharm.com/user/news/123",
                    "link": "https://n.news.naver.com/mnews/article/001/2",
                    "description": "약평위 급여 적정성 기사",
                    "pubDate": "Tue, 30 Jun 2026 05:00:00 +0900",
                },
            ]
        }).encode("utf-8")


def fake_opener(req, timeout=15):
    return FakeResponse()


def test_dashboard_media_scope_defaults_to_boost_not_strict_filter():
    registry = SourceRegistry.from_file(str(Path(__file__).resolve().parents[1] / "config" / "source_registry.yaml"))

    items = discover_naver_news(
        ["약평위"],
        client=cast(Any, type("Client", (), {"search": lambda self, query, display=20, sort="date": json.loads(FakeResponse().read())["items"]})()),
        registry=registry,
        media=["yakup"],
    )

    assert {item.source_name for item in items} == {"약업신문", "데일리팜"}


def test_dashboard_media_scope_can_strict_filter_when_requested():
    registry = SourceRegistry.from_file(str(Path(__file__).resolve().parents[1] / "config" / "source_registry.yaml"))

    items = discover_naver_news(
        ["약평위"],
        client=cast(Any, type("Client", (), {"search": lambda self, query, display=20, sort="date": json.loads(FakeResponse().read())["items"]})()),
        registry=registry,
        media=["yakup"],
        media_strategy="strict",
    )

    assert [item.source_name for item in items] == ["약업신문"]


def test_rendered_email_has_separate_monitoring_highlight_and_watchlist_sections():
    top = NewsDiscoveryItem(
        title="약평위 급여 적정성 결과 공개",
        publisher_url="https://www.dailypharm.com/user/news/1",
        naver_url="",
        description="약평위 급여 적정성과 평가금액 이하 수용 조건 기사",
        published_at="2026-07-01T05:00:00+09:00",
        keyword="약평위",
        discovery_channel="naver_news_api",
        source_name="데일리팜",
        source_tier="media_tier_A",
        source_weight=3.0,
        ma_depth=5,
        novelty=4,
        volume=5,
        score=7.0,
    )
    watch = NewsDiscoveryItem(
        title="MSD 임직원 사회공헌 캠페인",
        publisher_url="https://example.com/msd-csr",
        naver_url="",
        description="MSD 임직원 봉사와 질환 교육 캠페인",
        published_at="2026-07-01T05:00:00+09:00",
        keyword="MSD",
        discovery_channel="naver_news_api",
        source_name="일반매체",
        source_tier="unregistered",
        source_weight=1.0,
        ma_depth=0,
        novelty=0,
        volume=0,
        score=1.0,
    )

    html = render_daily_draft_html(items=[top, watch], keywords=["약평위", "MSD"])

    assert "Top Monitoring Highlights (1)" in html
    assert "Watchlist (1)" in html
    assert html.index("Top Monitoring Highlights (1)") < html.index("약평위 급여 적정성 결과 공개") < html.index("Watchlist (1)") < html.index("MSD 임직원 사회공헌 캠페인")


class FakeDraftCreate:
    def __init__(self):
        self.body = None

    def create(self, userId, body):
        self.body = body
        return self

    def execute(self):
        assert self.body and self.body["message"]["raw"]
        return {"id": "draft-1", "message": {"id": "msg-1", "threadId": "thr-1"}}


class FakeUsers:
    def __init__(self):
        self.drafts_obj = FakeDraftCreate()

    def drafts(self):
        return self.drafts_obj


class FakeGmailService:
    def __init__(self):
        self.users_obj = FakeUsers()

    def users(self):
        return self.users_obj


def test_gmail_delivery_creates_draft_not_live_send(tmp_path):
    token = tmp_path / "google_token.json"
    token.write_text(json.dumps({"scopes": ["https://www.googleapis.com/auth/gmail.send"]}), encoding="utf-8")
    assert gmail_configured(token)

    result = create_gmail_draft(
        recipients=["yo.seop.jeon@msd.com"],
        subject="[MA Daily] test",
        body_html="<p>hello</p>",
        body_text="hello",
        token_path=token,
        service=FakeGmailService(),
    )

    assert result["draft_created"] is True
    assert result["gmail_draft_id"] == "draft-1"
    assert result["recipients"] == ["yo.seop.jeon@msd.com"]


def test_quality_gate_blocks_one_article_smoke_test_from_send():
    item = NewsDiscoveryItem(
        title="약평위 급여 적정성 결과 공개",
        publisher_url="https://www.dailypharm.com/user/news/1",
        naver_url="",
        description="약평위 급여 적정성과 평가금액 이하 수용 조건 기사",
        published_at="2026-07-01T05:00:00+09:00",
        keyword="약평위",
        discovery_channel="naver_news_api",
        source_name="데일리팜",
        source_tier="media_tier_A",
        source_weight=3.0,
        ma_depth=5,
        novelty=4,
        volume=5,
        score=7.0,
    )

    report = evaluate_draft_quality([item], min_total_articles=3, min_top_signals=2)

    assert report.status == "draft_only_insufficient_quality"
    assert report.live_send_allowed is False
    assert "insufficient_coverage" in report.blocking_reasons
    assert "insufficient_top_signals" in report.blocking_reasons


def test_boilerplate_implication_is_warning_only_until_editorial_gate_decided():
    items = []
    for i in range(3):
        items.append(NewsDiscoveryItem(
            title=f"약가협상 급여 적정성 기사 {i}",
            publisher_url=f"https://www.dailypharm.com/user/news/{i}",
            naver_url="",
            description="약가협상과 급여 적정성 관련 기사",
            published_at="2026-07-01T05:00:00+09:00",
            keyword="급여",
            discovery_channel="naver_news_api",
            source_name="데일리팜",
            source_tier="media_tier_A",
            source_weight=3.0,
            ma_depth=5,
            novelty=4,
            volume=5,
            score=7.0,
        ))

    report = evaluate_draft_quality(items, min_total_articles=3, min_top_signals=2)

    assert report.status == "quality_gated_draft"
    assert "duplicate_implication_boilerplate" in report.warnings
    assert "editorial_quality_review_required" not in report.blocking_reasons
    assert report.live_send_allowed is False


def test_writer_calibration_prompt_treats_biospectator_as_style_not_live_source():
    assert "calibration references" in CALIBRATION_SYSTEM_PROMPT
    assert "Do not copy" in CALIBRATION_SYSTEM_PROMPT
    assert ARTICLE_CARD_SCHEMA["category"] == "Top Signal|Watchlist"
