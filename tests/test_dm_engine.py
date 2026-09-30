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
from agents.daily_mailing.llm import RELEVANCE_SYSTEM, SUPPORT_SYSTEM, LLMError
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
    def __init__(self, quote_mode="verbatim", fail_relevance=False, fail_writer=False,
                 support_mode="ok", implication=None):
        self.quote_mode = quote_mode
        self.fail_relevance = fail_relevance
        self.fail_writer = fail_writer
        self.support_mode = support_mode      # ok | reject | not_msd | legacy | error
        self.implication = implication        # None → 제목과 구별되는 기본 시사점
        self.support_calls = []               # (model, implication)

    def complete_json(self, *, system, user, model):
        data = json.loads(user)
        if system == RELEVANCE_SYSTEM:
            if self.fail_relevance:
                raise LLMError("down")
            hits = [t for t in data["scope_terms"] if t in data["title"]]
            return {"relevant": bool(hits), "matched_scope_terms": hits, "reason": "제목 주제" if hits else "무관"}
        if system == SUPPORT_SYSTEM:
            self.support_calls.append((model, data["implication"]))
            if self.support_mode == "error":
                raise LLMError("down")
            if self.support_mode == "legacy":          # msd_specific 이 없는 구 계약 응답
                return {"supported": True, "reason": "검증"}
            return {"supported": self.support_mode in ("ok", "not_msd"),
                    "msd_specific": self.support_mode in ("ok", "reject"), "reason": "검증"}
        if self.fail_writer:
            raise LLMError("down")
        quote = data["article_body"][:40] if self.quote_mode == "verbatim" else "본문에 없는 인용 문장입니다 정말로"
        implication = self.implication if self.implication is not None else f"{data['title']} → 한국MSD 경쟁 구도 영향"
        return {"summary_bullets": [f"{data['title']} 요약"], "ma_implication": implication,
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


def test_quote_rejected_flags_article_card():
    naver = FakeNaver({"파드셉": [PADCEV]})
    result = generate_run(_scope(), deps=_deps(naver, FakeLLM(quote_mode="fake")), name="파드셉", now=NOW)
    card = result.articles[0]
    assert card["selected_for_draft"] is True
    assert "quote_unverified" in card["quality_flags"]
    assert card["verification_method"] == "llm_relevance+quote_rejected"
    ok = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]})), name="파드셉", now=NOW)
    assert "quote_unverified" not in ok.articles[0]["quality_flags"]
    assert ok.articles[0]["verification_method"] == "llm_relevance+quote_check+support_check"


def test_generate_run_completes_with_unparseable_url_candidate():
    naver = FakeNaver({"파드셉": [PADCEV, _raw("파드셉 이상 URL", "http://[bad/1")]})
    result = generate_run(_scope(), deps=_deps(naver), name="파드셉", now=NOW)
    assert result.status == "ready"
    assert "파드셉 요로상피암 급여 확대" in [i.title for i in result.items]


def test_relevance_circuit_breaker_stops_after_three_consecutive_failures():
    class AlwaysDownLLM:
        def __init__(self):
            self.relevance_calls = 0

        def complete_json(self, *, system, user, model):
            if system == RELEVANCE_SYSTEM:
                self.relevance_calls += 1
            raise LLMError("down")

    naver = FakeNaver({"파드셉": [_raw(f"파드셉 기사 {i}", f"https://www.yakup.com/p{i}") for i in range(10)]})
    llm = AlwaysDownLLM()
    result = generate_run(_scope(), deps=_deps(naver, llm), name="파드셉", now=NOW)
    assert llm.relevance_calls == 3
    assert result.stats["relevance_circuit_open"] is True
    assert result.stats["relevance_llm_errors"] == 3
    assert result.status == "held"
    assert "llm_unavailable" in result.quality_report["blocking_reasons"]


def test_partial_writer_failure_plus_dropped_item_is_not_held():
    class WriterFailsForTitle(FakeLLM):
        def __init__(self, fail_title):
            super().__init__()
            self.fail_title = fail_title

        def complete_json(self, *, system, user, model):
            data = json.loads(user)
            if system != RELEVANCE_SYSTEM and data["title"] == self.fail_title:
                raise LLMError("down")
            return super().complete_json(system=system, user=user, model=model)

    no_desc = dict(_raw("파드셉 본문없음", "https://www.yakup.com/p2"), description="")
    naver = FakeNaver({"파드셉": [PADCEV, no_desc]})
    registry = SourceRegistry([SourceInfo(name="약업신문", domains=("yakup.com",), source_tier="media_tier_A")])
    deps = EngineDeps(
        naver=naver, llm=WriterFailsForTitle("파드셉 요로상피암 급여 확대"), registry=registry,
        fetch_body=lambda p, n: ArticleBody("failed", "", p) if p.endswith("/p2") else ArticleBody("ok", BODY, p),
    )
    result = generate_run(_scope(), deps=deps, name="파드셉", now=NOW)
    assert result.stats["writer_llm_errors"] == 1
    assert result.stats["dropped_empty_summary"] == 1
    assert result.items == []
    assert result.status == "no_news"
    assert "llm_unavailable" not in result.quality_report["blocking_reasons"]


def test_generate_top_ma_news_logs_stats_for_diagnosis(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="agents.daily_mailing.engine"):
        items = generate_top_ma_news(deps=_deps(FakeNaver({})), now=NOW)
    assert items == []
    assert "주요 MA 뉴스 stats" in caplog.text
    assert "'discovered': 0" in caplog.text


# ---- 부분 실행 가시화 (item D) ----
def test_partial_relevance_warning_when_some_judgments_failed():
    class RelevanceFailsForTitle(FakeLLM):
        def __init__(self, fail_title):
            super().__init__()
            self.fail_title = fail_title

        def complete_json(self, *, system, user, model):
            data = json.loads(user)
            if system == RELEVANCE_SYSTEM and data["title"] == self.fail_title:
                raise LLMError("down")
            return super().complete_json(system=system, user=user, model=model)

    other = _raw("파드셉 두번째 기사", "https://www.yakup.com/p2")
    naver = FakeNaver({"파드셉": [PADCEV, other]})
    result = generate_run(_scope(), deps=_deps(naver, RelevanceFailsForTitle("파드셉 두번째 기사")),
                          name="파드셉", now=NOW)
    assert result.status == "ready"
    assert [i.title for i in result.items] == ["파드셉 요로상피암 급여 확대"]
    assert result.stats["relevance_llm_errors"] == 1
    assert "partial_relevance" in result.quality_report["warnings"]


def test_partial_relevance_warning_when_circuit_opens_after_some_judged():
    class FailAfterFirst(FakeLLM):
        def __init__(self):
            super().__init__()
            self.n = 0

        def complete_json(self, *, system, user, model):
            if system == RELEVANCE_SYSTEM:
                self.n += 1
                if self.n > 1:
                    raise LLMError("down")
            return super().complete_json(system=system, user=user, model=model)

    naver = FakeNaver({"파드셉": [PADCEV] + [_raw(f"파드셉 기사 {i}", f"https://www.yakup.com/p{i}") for i in range(5)]})
    result = generate_run(_scope(), deps=_deps(naver, FailAfterFirst()), name="파드셉", now=NOW)
    assert result.stats["relevance_circuit_open"] is True
    assert result.status == "ready"
    assert "partial_relevance" in result.quality_report["warnings"]


def test_naver_rate_limited_and_query_cap_warnings():
    from urllib.error import HTTPError

    class RateLimitedNaver(FakeNaver):
        def search(self, query, display=20, sort="date"):
            self.queries.append(query)
            if query == "파드셉":
                raise HTTPError("https://openapi.naver.com", 429, "Too Many Requests", None, None)
            return self.by_query.get(query, [])

    result = generate_run(_scope(), deps=_deps(RateLimitedNaver({})), name="파드셉", now=NOW)
    assert result.stats["naver"]["rate_limited"] is True
    assert "naver_rate_limited" in result.quality_report["warnings"]
    assert result.status == "no_news"
    capped = generate_run(_scope(keywords=("a", "b", "c"), brands=()), deps=_deps(FakeNaver({})),
                          config=EngineConfig(max_naver_queries=2), name="n", now=NOW)
    assert "naver_query_cap_reached" in capped.quality_report["warnings"]
    clean = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]})), name="파드셉", now=NOW)
    assert clean.quality_report["warnings"] == []


def test_payload_stats_is_json_serialisable():
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]})), name="파드셉", now=NOW)
    decoded = json.loads(json.dumps(result.payload, ensure_ascii=False))
    assert decoded["stats"]["naver"]["queries"] == 2
    assert decoded["stats"]["scope_terms"] == ["약가 인하", "파드셉"]
    assert decoded["stats"]["recent"] == 1


# ---- 스토리 클러스터링 (2026-09-19 파일럿: 같은 사건 매체별 반복) ----
USAGE_LINK_TITLES = [
    "듀카브·램시마 등 80개 품목 약가 평균 4.6% 인하…339억원 절감",
    "건보공단, '사용량-약가 연동 협상'으로 80개 품목 약가 인하",
    "사용량 늘어난 의약품 80개 약가 평균 4.6% 인하",
]


def _multi_registry():
    return SourceRegistry([
        SourceInfo(name="약업신문", domains=("yakup.com",), source_tier="media_tier_A"),
        SourceInfo(name="데일리팜", domains=("dailypharm.com",), source_tier="media_tier_A"),
        SourceInfo(name="메디파나뉴스", domains=("medipana.com",), source_tier="media_tier_A"),
    ])


def test_same_story_across_outlets_collapses_to_one_item_with_also_reported_by():
    raws = [_raw(USAGE_LINK_TITLES[0], "https://www.yakup.com/u1"),
            _raw(USAGE_LINK_TITLES[1], "https://www.dailypharm.com/u2"),
            _raw(USAGE_LINK_TITLES[2], "https://www.medipana.com/u3"),
            _raw("파드셉 요로상피암 급여 확대", "https://www.yakup.com/p1")]
    naver = FakeNaver({"약가": raws})
    deps = EngineDeps(naver=naver, llm=FakeLLM(), registry=_multi_registry(),
                      fetch_body=lambda p, n: ArticleBody("ok", BODY, p))
    result = generate_run(_scope(keywords=("약가", "파드셉"), brands=()), deps=deps, name="n", now=NOW)
    titles = [i.title for i in result.items]
    assert len(titles) == 2
    assert "파드셉 요로상피암 급여 확대" in titles
    kept = next(i for i in result.items if i.title != "파드셉 요로상피암 급여 확대")
    assert kept.title in USAGE_LINK_TITLES
    assert len(kept.also_reported_by) == 2
    assert {e["url"] for e in kept.also_reported_by} <= {"https://www.yakup.com/u1", "https://www.dailypharm.com/u2",
                                                          "https://www.medipana.com/u3"}
    assert all(e["source_name"] for e in kept.also_reported_by)
    assert kept.publisher_url not in {e["url"] for e in kept.also_reported_by}
    assert "외 2개 매체" in result.html and "외 2개 매체" in result.markdown
    assert result.stats["story_clusters"] == 2
    assert result.stats["clustered_duplicates"] == 2
    # 중복 기사 카드: 선정되지 않았지만 '제외' 가 아니라 동일 사건 표시
    dup_cards = [a for a in result.articles if a["title"] in USAGE_LINK_TITLES and not a["selected_for_draft"]]
    assert len(dup_cards) == 2
    for card in dup_cards:
        assert "duplicate_story" in card["quality_flags"]
        assert kept.title in card["reviewer_note"]
        assert card["review_status"] == "needs_review"
    assert result.payload["items"][0]["also_reported_by"] == [dict(e) for e in result.items[0].also_reported_by]


def test_freed_slots_are_filled_with_next_distinct_story():
    raws = [_raw(t, f"https://www.yakup.com/u{i}") for i, t in enumerate(USAGE_LINK_TITLES)]
    raws += [_raw("약가 제도 개편안 행정예고", "https://www.yakup.com/d1"),
             _raw("약가 협상 결렬 통보 논란", "https://www.yakup.com/d2")]
    naver = FakeNaver({"약가": raws})
    result = generate_run(_scope(keywords=("약가",), brands=()), deps=_deps(naver),
                          config=EngineConfig(brief_limit=3), name="n", now=NOW)
    titles = [i.title for i in result.items]
    assert len(titles) == 3                                         # 묶인 3건이 슬롯 1개만 차지
    assert sum(1 for t in titles if t in USAGE_LINK_TITLES) == 1
    assert {"약가 제도 개편안 행정예고", "약가 협상 결렬 통보 논란"} <= set(titles)


def test_cluster_representative_prefers_item_with_body():
    raws = [_raw(USAGE_LINK_TITLES[0], "https://www.yakup.com/u1"),      # 랭킹 1위지만 본문 실패
            _raw(USAGE_LINK_TITLES[2], "https://www.medipana.com/u3")]   # 본문 확보
    naver = FakeNaver({"약가": raws})
    deps = EngineDeps(naver=naver, llm=FakeLLM(), registry=_multi_registry(),
                      fetch_body=lambda p, n: ArticleBody("ok", BODY, p) if p.endswith("/u3")
                      else ArticleBody("failed", "", p))
    result = generate_run(_scope(keywords=("약가",), brands=()), deps=deps, name="n", now=NOW)
    assert [i.title for i in result.items] == [USAGE_LINK_TITLES[2]]
    assert result.items[0].body_status == "ok"
    assert [e["url"] for e in result.items[0].also_reported_by] == ["https://www.yakup.com/u1"]


def test_distinct_stories_are_not_clustered():
    raws = [_raw("약평위, 엔허투 급여 적정성 인정", "https://www.yakup.com/a1"),
            _raw("약평위, 렉라자 급여 재평가 착수", "https://www.dailypharm.com/a2")]
    result = generate_run(_scope(keywords=("약평위",), brands=()), deps=_deps(FakeNaver({"약평위": raws})),
                          name="n", now=NOW)
    assert len(result.items) == 2
    assert all(i.also_reported_by == () for i in result.items)
    assert result.stats["clustered_duplicates"] == 0


# ---- MA 시사점 검증 (item 2b/2c) ----
def test_support_rejected_drops_implication_keeps_item_and_flags_card():
    llm = FakeLLM(support_mode="reject")
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), llm), name="파드셉", now=NOW)
    item = result.items[0]
    assert item.ma_implication == "" and item.evidence_quote == "" and item.quote_verified is False
    assert result.status == "ready"
    assert result.stats["implication_unsupported"] == 1
    assert "quote_rejected" not in result.stats
    card = result.articles[0]
    assert "implication_unsupported" in card["quality_flags"]
    assert card["verification_method"] == "llm_relevance+quote_check+support_rejected"
    assert "시사점 제외" in card["reviewer_note"]


def test_support_check_uses_config_model_and_is_skipped_for_empty_implication():
    llm = FakeLLM()
    generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), llm), name="파드셉", now=NOW,
                 config=EngineConfig(support_model="gpt-4o-mini-2024"))
    assert llm.support_calls == [("gpt-4o-mini-2024", "파드셉 요로상피암 급여 확대 → 한국MSD 경쟁 구도 영향")]
    empty = FakeLLM(implication="")
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), empty), name="파드셉", now=NOW)
    assert empty.support_calls == []
    assert result.items[0].ma_implication == ""
    assert result.status == "ready" and "implication_unsupported" not in result.stats


def test_support_check_not_called_when_quote_fails_and_llm_error_fails_closed():
    fake_quote = FakeLLM(quote_mode="fake")
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), fake_quote), name="파드셉", now=NOW)
    assert fake_quote.support_calls == []                       # 인용 실패면 지원 검증 호출 없음 (비용)
    assert result.stats["quote_rejected"] == 1
    down = FakeLLM(support_mode="error")
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), down), name="파드셉", now=NOW)
    assert result.items[0].ma_implication == ""                 # fail-closed
    assert result.stats["support_llm_errors"] == 1 and result.stats["implication_unsupported"] == 1
    assert result.status == "ready"
    assert "implication_unsupported" in result.articles[0]["quality_flags"]


def test_style_guard_rejects_title_restatement_and_advocacy_without_support_call():
    restate = FakeLLM(implication="파드셉 요로상피암 급여 확대.")
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), restate), name="파드셉", now=NOW)
    assert result.items[0].ma_implication == "" and result.items[0].summary_bullets
    assert result.stats["implication_rejected_style"] == 1
    assert restate.support_calls == []
    card = result.articles[0]
    assert "implication_style_rejected" in card["quality_flags"]
    assert card["verification_method"] == "llm_relevance+style_rejected"
    advocacy = FakeLLM(implication="복지부는 급여 확대를 신속히 추진해야 한다")
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), advocacy), name="파드셉", now=NOW)
    assert result.items[0].ma_implication == ""
    assert result.stats["implication_rejected_style"] == 1
    assert advocacy.support_calls == []


# ---- MSD 구체 연결 게이트 (2026-09-30 파일럿: 스타틴 복합제 인하 기사 → 'MSD 의 시장 접근 전략에 위협' 상투구) ----
STATIN_BOILERPLATE = "피타바스타틴·에제티미브 저용량 시장의 경쟁 심화는 MSD의 시장 접근 전략에 위협이 될 수 있다."


def test_supported_but_not_msd_specific_drops_implication_keeps_item_and_flags_card():
    llm = FakeLLM(support_mode="not_msd", implication=STATIN_BOILERPLATE)
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), llm), name="파드셉", now=NOW)
    item = result.items[0]
    assert item.ma_implication == "" and item.evidence_quote == "" and item.quote_verified is False
    assert item.summary_bullets                                  # 기사는 유지, 시사점만 제거
    assert result.status == "ready"
    assert result.stats["implication_not_msd_specific"] == 1
    assert "implication_unsupported" not in result.stats and "quote_rejected" not in result.stats
    assert len(llm.support_calls) == 1                          # 지원 + MSD 연결 판정은 support_model 1회 호출
    card = result.articles[0]
    assert card["selected_for_draft"] is True
    assert "implication_not_msd_specific" in card["quality_flags"]
    assert card["verification_method"].endswith("+msd_link_rejected")
    assert "시사점 제외" in card["reviewer_note"] and "MSD" in card["reviewer_note"]
    assert "▶ MA 시사점" not in result.html and STATIN_BOILERPLATE not in result.html


def test_supported_and_msd_specific_renders_implication():
    concrete = "제네릭 45% 인하 개편은 키트루다 LOE 후 자사 오리지널의 가격 경쟁 압력으로 이어진다"
    llm = FakeLLM(support_mode="ok", implication=concrete)
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), llm), name="파드셉", now=NOW)
    item = result.items[0]
    assert item.ma_implication == concrete and item.quote_verified is True
    assert "▶ MA 시사점" in result.html and concrete in result.html
    card = result.articles[0]
    assert card["verification_method"] == "llm_relevance+quote_check+support_check"
    assert "implication_not_msd_specific" not in card["quality_flags"]
    assert "implication_not_msd_specific" not in result.stats


def test_unsupported_takes_precedence_over_msd_link_and_legacy_answer_fails_closed():
    reject = FakeLLM(support_mode="reject")                     # supported=False, msd_specific=True
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), reject), name="파드셉", now=NOW)
    assert result.stats["implication_unsupported"] == 1 and "implication_not_msd_specific" not in result.stats
    assert result.articles[0]["verification_method"] == "llm_relevance+quote_check+support_rejected"
    legacy = FakeLLM(support_mode="legacy")                     # {"supported": true} 만 — MSD 연결 미확인
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [PADCEV]}), legacy), name="파드셉", now=NOW)
    assert result.items[0].ma_implication == ""
    assert result.stats["implication_not_msd_specific"] == 1 and "implication_unsupported" not in result.stats
    assert "implication_not_msd_specific" in result.articles[0]["quality_flags"]


def test_support_model_config_default_and_settings():
    assert EngineConfig().support_model == "gpt-4o-mini"
    settings = json.loads((Path(__file__).resolve().parents[1] / "config" / "settings.json").read_text(encoding="utf-8"))
    assert settings["daily_mailing"]["support_model"] == "gpt-4o-mini"
    assert EngineConfig.from_settings(settings).support_model == "gpt-4o-mini"


# ---- 표시 다듬기 (item 3) ----
def test_unregistered_outlet_shows_domain_in_mail_but_card_keeps_raw_name():
    raws = [_raw("파드셉 요로상피암 급여 확대", "https://www.newsmp.com/news/articleView.html?idxno=1"),
            _raw("파드셉, 요로상피암 급여 확대 확정", "https://www.hankooki.com/news/2")]
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": raws})), name="파드셉", now=NOW)
    assert len(result.items) == 1                                   # 같은 사건 → 1건 + also_reported_by
    item = result.items[0]
    assert item.source_name in {"newsmp.com", "hankooki.com"}
    assert [e["source_name"] for e in item.also_reported_by] in (["hankooki.com"], ["newsmp.com"])
    assert {a["source_name"] for a in result.articles} == {"Unknown publisher"}   # 카드(writer 완전성 검사) 는 원본 유지
    assert "Unknown publisher" not in result.html


def test_snippet_item_uses_cleaned_description_single_line():
    truncated = dict(PADCEV, description="파드셉 병용요법의 급여 확대가 논의된다. 심평원 암질심은 다음 달 안건으로 상정할 예정이며 업계는...")
    result = generate_run(_scope(), deps=_deps(FakeNaver({"파드셉": [truncated]}), body_status="failed"),
                          name="파드셉", now=NOW)
    item = result.items[0]
    assert item.body_status == "snippet"
    assert item.summary_bullets == ("파드셉 병용요법의 급여 확대가 논의된다.",)
    assert "검색 요약: 파드셉 병용요법의 급여 확대가 논의된다." in result.html
    assert "본문 미확보" in result.html
