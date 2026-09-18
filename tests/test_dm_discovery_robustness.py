from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from agents.daily_mailing.discovery import (
    NaverNewsClient,
    NewsDiscoveryItem,
    discover_naver_news,
    select_daily_items,
)
from agents.daily_mailing.source_registry import SourceInfo, SourceRegistry


class FakeResponse:
    def __init__(self, items):
        self._items = items

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps({"items": self._items}).encode("utf-8")


def _raw(title, url):
    return {"title": title, "originallink": url, "link": url, "description": title,
            "pubDate": "Fri, 18 Sep 2026 06:00:00 +0900"}


def _registry():
    return SourceRegistry([SourceInfo(name="약업신문", domains=("yakup.com",), source_tier="media_tier_A")])


def test_throttle_sleeps_between_calls():
    sleeps = []
    client = NaverNewsClient("id", "secret", opener=lambda req, timeout: FakeResponse([]),
                             min_interval=0.15, sleep=sleeps.append, clock=lambda: 100.0)
    client.search("a")
    client.search("b")
    assert sleeps == [pytest.approx(0.15)]


def test_429_retries_with_exponential_backoff_then_succeeds():
    calls = {"n": 0}

    def opener(req, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise HTTPError(req.full_url, 429, "Too Many Requests", None, None)
        return FakeResponse([_raw("t", "https://www.yakup.com/1")])

    ticks = iter(range(1000))
    sleeps = []
    client = NaverNewsClient("id", "secret", opener=opener, sleep=sleeps.append,
                             clock=lambda: float(next(ticks)))
    items = client.search("q")
    assert len(items) == 1
    assert sleeps == [1.0, 2.0]


def test_429_exhausted_raises():
    def opener(req, timeout):
        raise HTTPError(req.full_url, 429, "Too Many Requests", None, None)

    ticks = iter(range(1000))
    client = NaverNewsClient("id", "secret", opener=opener, sleep=lambda s: None,
                             clock=lambda: float(next(ticks)), max_retries=3)
    with pytest.raises(HTTPError):
        client.search("q")


class ScriptedClient:
    def __init__(self, by_query=None, fail=None):
        self.by_query = by_query or {}
        self.fail = fail or {}
        self.queries = []

    def search(self, query, display=20, sort="date"):
        self.queries.append(query)
        if query in self.fail:
            raise HTTPError("https://openapi.naver.com", self.fail[query], "err", None, None)
        return self.by_query.get(query, [])


def test_discover_respects_max_queries_and_reports_stats():
    client = ScriptedClient({"a": [_raw("a1", "https://www.yakup.com/a1")]})
    stats = {}
    items = discover_naver_news(["a", "b", "c"], client=client, registry=_registry(),
                                max_queries=2, stats=stats)
    assert client.queries == ["a", "b"]
    assert stats["queries"] == 2
    assert stats["query_cap_reached"] is True
    assert [i.title for i in items] == ["a1"]


def test_discover_stops_on_rate_limit_and_keeps_collected_items():
    client = ScriptedClient({"a": [_raw("a1", "https://www.yakup.com/a1")]}, fail={"b": 429})
    stats = {}
    items = discover_naver_news(["a", "b", "c"], client=client, registry=_registry(), stats=stats)
    assert client.queries == ["a", "b"]
    assert stats["rate_limited"] is True
    assert [i.title for i in items] == ["a1"]


def test_discover_skips_non_429_http_error_and_continues():
    client = ScriptedClient({"c": [_raw("c1", "https://www.yakup.com/c1")]}, fail={"b": 500})
    stats = {}
    items = discover_naver_news(["b", "c"], client=client, registry=_registry(), stats=stats)
    assert [i.title for i in items] == ["c1"]
    assert stats["errors"] == ["b: 500"]


def _item(title, url):
    return NewsDiscoveryItem(
        title=title, publisher_url=url, naver_url="", description=title,
        published_at="2026-09-18T06:00:00+09:00", keyword="파드셉", discovery_channel="naver_news_api",
        source_name="약업신문", source_tier="media_tier_A", source_weight=1.0,
        ma_depth=0, novelty=0, volume=0,
    )


def test_select_can_disable_forced_msd_representative():
    padcev = _item("파드셉 요로상피암 급여 확대", "https://www.yakup.com/p1")
    msd = _item("한국MSD 엔플론시아 RSV 예방", "https://www.yakup.com/m1")
    forced = select_daily_items([padcev, msd], ["파드셉"], limit=1)
    assert any("MSD" in i.title for i in forced)  # legacy behaviour unchanged
    plain = select_daily_items([padcev, msd], ["파드셉"], limit=1, force_msd_representative=False)
    assert [i.title for i in plain] == ["파드셉 요로상피암 급여 확대"]
