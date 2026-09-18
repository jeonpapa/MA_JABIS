from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agents.daily_mailing.article_body import ArticleBody
from agents.daily_mailing.brief import BriefItem
from agents.daily_mailing.dashboard_scope import DashboardScope
from agents.daily_mailing.engine import (
    EngineConfig,
    EngineDeps,
    generate_run,
    generate_top_ma_news,
    top_ma_news_terms,
)
from agents.daily_mailing.llm import RELEVANCE_SYSTEM, LLMError
from agents.daily_mailing.source_registry import SourceInfo, SourceRegistry

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 18, 8, 0, tzinfo=KST)
BODY = ("파드셉과 키트루다 병용요법이 요로상피암 1차 치료로 급여 확대 논의에 들어갔다. "
        "심평원 암질심은 다음 달 안건으로 상정할 예정이다. ") * 4


def _raw(title, url):
    return {"title": title, "originallink": url, "link": url, "description": f"{title} 설명",
            "pubDate": "Fri, 18 Sep 2026 06:00:00 +0900"}


class FakeNaver:
    def __init__(self, by_query):
        self.by_query = by_query
        self.queries = []

    def search(self, query, display=20, sort="date"):
        self.queries.append(query)
        return self.by_query.get(query, [])


class FakeLLM:
    def __init__(self, quote_mode="verbatim", fail_relevance=False, fail_writer=False):
        self.quote_mode = quote_mode
        self.fail_relevance = fail_relevance
        self.fail_writer = fail_writer

    def complete_json(self, *, system, user, model):
        data = json.loads(user)
        if system == RELEVANCE_SYSTEM:
            if self.fail_relevance:
                raise LLMError("down")
            hits = [t for t in data["scope_terms"] if t in data["title"]]
            return {"relevant": bool(hits), "matched_scope_terms": hits, "reason": "제목 주제" if hits else "무관"}
        if self.fail_writer:
            raise LLMError("down")
        quote = data["article_body"][:40] if self.quote_mode == "verbatim" else "본문에 없는 인용 문장입니다 정말로"
        return {"summary_bullets": [f"{data['title']} 요약"], "ma_implication": f"{data['title']} 시사점",
                "evidence_quote": quote}


def _deps(naver, llm=None, body_status="ok"):
    registry = SourceRegistry([SourceInfo(name="약업신문", domains=("yakup.com",), source_tier="media_tier_A")])
    text = BODY if body_status == "ok" else ""
    return EngineDeps(naver=naver, llm=llm or FakeLLM(), registry=registry,
                      fetch_body=lambda p, n: ArticleBody(body_status, text, p))


def _scope(**kw):
    base = dict(subscription_id="19", owner_email="o@msd.com", recipients=("r@msd.com",),
                keywords=("약가 인하",), brands=("파드셉",))
    base.update(kw)
    return DashboardScope(**base)


PADCEV = _raw("파드셉 요로상피암 급여 확대", "https://www.yakup.com/p1")
ENFLON = _raw("한국MSD 엔플론시아 RSV 예방", "https://www.yakup.com/m1")


def test_scope_only_queries_and_relevant_items_only():
    naver = FakeNaver({"파드셉": [PADCEV, ENFLON], "약가 인하": [ENFLON]})
    result = generate_run(_scope(), deps=_deps(naver), name="파드셉", now=NOW)
    assert naver.queries == ["약가 인하", "파드셉"]               # 페르소나 기본 키워드 합산 없음
    assert [i.title for i in result.items] == ["파드셉 요로상피암 급여 확대"]  # MSD 대표 기사 강제 삽입 없음
    item = result.items[0]
    assert item.quote_verified is True and item.ma_implication
    assert result.status == "ready"
    assert result.subject == "[MA Daily] 파드셉 · 9/18(금)"
    assert result.run_date == "2026-09-18"
    assert result.run_id.startswith("dm_19_20260918_080000")


def test_payload_contract_for_slice_b():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver), name="파드셉", now=NOW)
    p = result.payload
    for key in ("run_id", "generated_at", "window_label", "lookback_hours", "keywords", "media",
                "subscription_id", "owner_email", "recipients", "discovered_count", "recent_count",
                "selected_count", "status", "quality_report", "personas", "items", "dashboard_scope",
                "run_date", "is_test", "email_subject", "email_html", "delivery_status", "approval_status"):
        assert key in p, key
    assert p["subscription_id"] == 19
    assert p["recipients"] == ["r@msd.com"]
    assert p["email_html"] == result.html
    assert p["items"][0]["evidence_quotes"]
    card_by_title = {a["title"]: a for a in result.articles}
    assert card_by_title["파드셉 요로상피암 급여 확대"]["selected_for_draft"] is True
    assert "관련성 통과" in card_by_title["파드셉 요로상피암 급여 확대"]["reviewer_note"]


def test_no_relevant_articles_is_no_news():
    naver = FakeNaver({"파드셉": [ENFLON]})
    result = generate_run(_scope(), deps=_deps(naver), name="파드셉", now=NOW)
    assert result.status == "no_news"
    assert "특이사항 없음" in result.html
    assert result.articles[0]["review_status"] == "excluded"


def test_unverified_quote_drops_implication_but_keeps_item():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver, FakeLLM(quote_mode="fake")), name="파드셉", now=NOW)
    item = result.items[0]
    assert item.ma_implication == "" and item.quote_verified is False
    assert result.stats["quote_rejected"] == 1
    assert result.status == "ready"


def test_body_missing_uses_snippet_without_implication():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver, body_status="failed"), name="파드셉", now=NOW)
    item = result.items[0]
    assert item.body_status == "snippet"
    assert item.summary_bullets == ("파드셉 요로상피암 급여 확대 설명",)
    assert item.ma_implication == ""


def test_llm_down_holds_run():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver, FakeLLM(fail_relevance=True)), name="파드셉", now=NOW)
    assert result.status == "held"
    assert "llm_unavailable" in result.quality_report["blocking_reasons"]


def test_empty_scope_holds_without_calls():
    naver = FakeNaver({})
    result = generate_run(_scope(keywords=(), brands=()), deps=_deps(naver), name="빈 스콥", now=NOW)
    assert result.status == "held"
    assert "empty_scope" in result.quality_report["blocking_reasons"]
    assert naver.queries == []


def test_top_ma_news_appended_deduped_and_optional():
    naver = FakeNaver({"파드셉": [PADCEV]})
    top = [BriefItem(title="약평위 결과", publisher_url="https://x/1", summary_bullets=("s",),
                     matched_scope_terms=("약평위",), section="top_ma_news"),
           BriefItem(title="dup", publisher_url="https://www.yakup.com/p1", summary_bullets=("s",),
                     matched_scope_terms=("급여",), section="top_ma_news")]
    result = generate_run(_scope(), deps=_deps(naver), name="파드셉", now=NOW, top_ma_news=top)
    assert [i.title for i in result.top_ma_news] == ["약평위 결과"]
    assert "오늘의 주요 MA 뉴스" in result.html
    off = generate_run(_scope(include_top_ma_signals=False), deps=_deps(FakeNaver({"파드셉": [PADCEV]})),
                       name="파드셉", now=NOW, top_ma_news=top)
    assert off.top_ma_news == []


def test_test_run_subject_and_id():
    result = generate_run(_scope(), deps=_deps(FakeNaver({})), name="파드셉", now=NOW, is_test=True)
    assert result.subject.startswith("[TEST] ")
    assert result.run_id.endswith("_test")
    assert result.payload["is_test"] is True


def test_query_cap_from_config():
    naver = FakeNaver({})
    generate_run(_scope(keywords=("a", "b", "c"), brands=()), deps=_deps(naver),
                 config=EngineConfig(max_naver_queries=2), name="n", now=NOW)
    assert naver.queries == ["a", "b"]


def test_generate_top_ma_news_uses_persona_terms():
    terms = top_ma_news_terms()
    assert "약평위" in terms and "심평원" in terms and "키트루다" not in terms
    naver = FakeNaver({"약평위": [_raw("약평위 9월 결과 발표", "https://www.yakup.com/t1")]})
    items = generate_top_ma_news(deps=_deps(naver), now=NOW)
    assert [i.title for i in items] == ["약평위 9월 결과 발표"]
    assert items[0].section == "top_ma_news"


def test_engine_config_from_settings_file():
    settings = json.loads((Path(__file__).resolve().parents[1] / "config" / "settings.json").read_text(encoding="utf-8"))
    config = EngineConfig.from_settings(settings)
    assert config.relevance_model == "gpt-4o-mini"
    assert config.writer_model == "gpt-4o"
    assert EngineConfig.from_settings({"daily_mailing": {"brief_limit": 3, "unknown": 1}}).brief_limit == 3


def test_writer_down_holds_run():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver, FakeLLM(fail_writer=True)), name="파드셉", now=NOW)
    assert result.status == "held"
    assert "llm_unavailable" in result.quality_report["blocking_reasons"]
    assert result.stats["writer_llm_errors"] == 1
    assert result.items == []


def test_empty_summary_item_is_dropped_not_held():
    no_desc = dict(PADCEV, description="")
    naver = FakeNaver({"파드셉": [no_desc]})
    result = generate_run(_scope(), deps=_deps(naver, body_status="failed"), name="파드셉", now=NOW)
    assert result.items == []
    assert result.status == "no_news"
    assert result.stats["dropped_empty_summary"] == 1
    assert "empty_summary" not in result.quality_report["blocking_reasons"]


def test_writer_empty_bullets_without_snippet_is_dropped():
    class EmptyWriterLLM(FakeLLM):
        def complete_json(self, *, system, user, model):
            data = super().complete_json(system=system, user=user, model=model)
            if system != RELEVANCE_SYSTEM:
                data["summary_bullets"] = []
            return data

    no_desc = dict(PADCEV, description="")
    naver = FakeNaver({"파드셉": [no_desc]})
    result = generate_run(_scope(), deps=_deps(naver, EmptyWriterLLM()), name="파드셉", now=NOW)
    assert result.items == []
    assert result.status == "no_news"
    assert result.stats["dropped_empty_summary"] == 1


def test_top_ma_news_archive_uses_gov_policy_kind_only(tmp_path):
    import sqlite3

    db = tmp_path / "news.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE competitor_news (id INTEGER PRIMARY KEY, kind TEXT, title TEXT, url TEXT, "
                  "naver_link TEXT, description TEXT, pub_date TEXT, source_name TEXT)")
        c.executemany(
            "INSERT INTO competitor_news (kind, title, url, naver_link, description, pub_date, source_name) "
            "VALUES (?,?,?,?,?,?,?)",
            [("gov_policy", "약평위 정책 기사", "https://www.yakup.com/g1", "", "", "2026-09-18", "약업신문"),
             ("competitor", "약평위 경쟁사 기사", "https://www.yakup.com/c1", "", "", "2026-09-18", "약업신문")],
        )
    deps = _deps(FakeNaver({}))
    deps.archive_db_path = str(db)
    top = generate_top_ma_news(deps=deps, now=NOW)
    assert [i.title for i in top] == ["약평위 정책 기사"]          # §5.1: gov_policy 아카이브만
    run = generate_run(_scope(keywords=("약평위",), brands=()), deps=deps, name="n", now=NOW)
    assert sorted(i.title for i in run.items) == ["약평위 경쟁사 기사", "약평위 정책 기사"]  # 구독 run 은 전체 kind
