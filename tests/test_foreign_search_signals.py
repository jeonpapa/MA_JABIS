"""라이브 해외약가 검색의 국가별 실패 → 원장 + 화면에서 '수집 실패' 와 '제도상 비공개' 분리 (P1.6 관측).

감사가 본 결함: search_all 이 국가별 예외를 로그로만 삼키고 [] 를 돌려주면 서버가 describe_empty_result 의 정책 문구
("Micromedex 구독 필요")로 감싼다 — 로그인이 깨진 것이 제도상 공백처럼 보이고 원장에는 아무것도 남지 않는다.
네트워크·실제 스크레이퍼 금지: search_one_country 를 가짜로 바꾼다. 원장은 conftest 의 AGENT_TASK_DB(임시 파일).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import foreign_price_agent as fpa
from agents.agent_tasks.store import list_events, list_tasks


# ── 분류기 ─────────────────────────────────────────────────────────────────────

class _PlaywrightTimeoutError(Exception):        # playwright.async_api.TimeoutError 와 같은 이름
    pass


_PlaywrightTimeoutError.__name__ = "TimeoutError"


@pytest.mark.parametrize("exc, expected", [
    (ImportError("cannot import name 'BrowserBindResult' from 'scrapling'"), "dependency"),
    (ModuleNotFoundError("No module named 'scrapling'"), "dependency"),
    (asyncio.TimeoutError(), "timeout"),
    (_PlaywrightTimeoutError("Timeout 30000ms exceeded."), "timeout"),
    (RuntimeError("Micromedex login failed: redirected to 'Please Login' page"), "login"),
    (RuntimeError("Micromedex license limit reached: maximum concurrent users logged in"), "login"),
    (RuntimeError("DocCheck 로그인 실패 — 자격증명 오류"), "login"),
    (RuntimeError("HTTP 403 Forbidden"), "http_403"),
    (RuntimeError("Cloudflare challenge not solved"), "http_403"),
    (json.JSONDecodeError("Expecting value", "", 0), "parse"),
    (KeyError("price"), "parse"),
    (RuntimeError("boom"), "other"),
])
def test_classify_scrape_error(exc, expected):
    assert fpa.classify_scrape_error(exc) == expected


# ── search_all: 실패 국가 → 원장 1건/(국가, 분류), 결과는 [] 그대로 ───────────────

class _StubDB:
    def compact_foreign_prices(self, query):
        return 0


def _agent(monkeypatch, failures: dict[str, Exception]) -> fpa.ForeignPriceAgent:
    """스크레이퍼·DB·환율 없이 search_all 만 도는 에이전트. failures 의 국가는 그 예외를 던지고 나머지는 가격 1행."""
    agent = fpa.ForeignPriceAgent.__new__(fpa.ForeignPriceAgent)
    agent.db = _StubDB()

    async def one(query, country, reference_date=None, identity=None):
        if country in failures:
            raise failures[country]
        return [{"query_name": query, "country": country, "local_price": 1.0}]
    monkeypatch.setattr(agent, "search_one_country", one)
    monkeypatch.setattr(agent, "_validate_data_integrity", lambda q, r: None)
    monkeypatch.setattr(agent, "_normalize_doses_across_countries", lambda q, r: None)
    return agent


def test_search_all_records_failure_per_country_and_class_and_keeps_result_shape(monkeypatch):
    db = Path(os.environ["AGENT_TASK_DB"])
    agent = _agent(monkeypatch, {"US": RuntimeError("Micromedex login failed: redirected to 'Please Login' page"),
                                 "UK": ImportError("cannot import name 'BrowserBindResult' from 'scrapling'")})
    res = asyncio.run(agent.search_all("keytruda", ["JP", "US", "UK"]))
    assert isinstance(res, dict) and res["US"] == [] and res["UK"] == [] and res["JP"][0]["local_price"] == 1.0
    assert res.errors["US"]["error_class"] == "login" and res.errors["UK"]["error_class"] == "dependency"
    assert "JP" not in res.errors and "Please Login" in res.errors["US"]["message"]
    tasks = {t["source_ref"]: t for t in list_tasks(db_path=db)}
    assert set(tasks) == {"US:login", "UK:dependency"}
    us = tasks["US:login"]
    assert us["source"] == "foreign_search" and us["kind"] == "ops_incident" and us["priority"] == 2
    assert "로그인 오류" in us["title"] and "keytruda" in us["detail"] and "런북 없음" in us["detail"]
    assert "foreign_config_probe" in us["detail"]                       # 사람이 다음에 볼 곳


def test_search_all_same_failure_again_reuses_the_row(monkeypatch):
    db = Path(os.environ["AGENT_TASK_DB"])
    agent = _agent(monkeypatch, {"US": RuntimeError("Micromedex login failed")})
    asyncio.run(agent.search_all("keytruda", ["US"]))
    asyncio.run(agent.search_all("welireg", ["US"]))
    tasks = list_tasks(db_path=db)
    assert len(tasks) == 1
    assert [e["event"] for e in list_events(tasks[0]["id"], db_path=db)][0] == "created"


def test_search_all_signal_failure_never_breaks_the_search(monkeypatch):
    def boom(**kw):
        raise RuntimeError("원장 DB 잠김")
    monkeypatch.setattr("agents.agent_tasks.signals.record_recurring", boom)
    agent = _agent(monkeypatch, {"US": RuntimeError("Micromedex login failed")})
    res = asyncio.run(agent.search_all("keytruda", ["US", "JP"]))
    assert res["US"] == [] and res["JP"] and res.errors["US"]["error_class"] == "login"


def test_search_all_success_records_nothing(monkeypatch):
    db = Path(os.environ["AGENT_TASK_DB"])
    agent = _agent(monkeypatch, {})
    res = asyncio.run(agent.search_all("keytruda", ["JP", "IT"]))
    assert res.errors == {} and list_tasks(db_path=db) == []


# ── 서버: country_results[cc].status — scraper_error 는 정책 문구(reason)와 분리 ─────

def test_country_results_expose_status_separate_from_policy_reason():
    import api.server as s
    results = fpa.SearchResults({"JP": [{"local_price": 100}], "FR": [], "US": []})
    results.errors["US"] = {"error_class": "login", "message": "Micromedex login failed: redirected to 'Please Login' page"}
    cr = s._compute_country_results("keytruda", ["JP", "FR", "US"], results)
    assert cr["JP"]["status"] == "priced" and cr["JP"]["has_price"] is True and "reason" not in cr["JP"]
    assert cr["FR"]["status"] == "no_result" and cr["FR"]["policy"] is True and "PPH" in cr["FR"]["reason"]
    us = cr["US"]
    assert us["status"] == "scraper_error" and us["has_price"] is False
    assert us["error_class"] == "login" and us["error_label"] == "로그인 오류" and "Please Login" in us["error"]
    assert "구독" not in us["reason"] and us["reason"].startswith("수집 실패(로그인 오류)")


def test_country_results_plain_dict_results_still_work():
    import api.server as s
    cr = s._compute_country_results("keytruda", ["XX"], {"XX": []})
    assert cr["XX"]["status"] == "no_result" and cr["XX"]["policy"] is False and "검색어 확인" in cr["XX"]["reason"]


def test_status_endpoint_carries_scraper_error(monkeypatch):
    import api.server as s

    async def _stub(query, countries=None, identity=None):
        out = fpa.SearchResults({c: [] for c in (countries or [])})
        out.errors["US"] = {"error_class": "dependency", "message": "cannot import name 'BrowserBindResult'"}
        return out
    monkeypatch.setattr(s.foreign_agent, "search_all", _stub)
    c = s.app.test_client()
    r = c.post("/api/foreign/search", json={"query": "Keytruda", "countries": ["US"], "use_cache": False})
    jid = r.get_json()["job_id"]
    final = None
    for _ in range(60):
        time.sleep(0.05)
        final = c.get(f"/api/foreign/search/status/{jid}").get_json()
        if final["status"] != "running":
            break
    assert final["status"] == "done"
    us = final["country_results"]["US"]
    assert us["status"] == "scraper_error" and us["error_class"] == "dependency" and us["error_label"] == "의존성 오류"


# ── UK: scrapling import 실패는 삼키지 않는다 — search_all 이 dependency 로 분류·기록 ───

def test_uk_scrapling_import_failure_propagates(monkeypatch, tmp_path):
    from agents.scrapers.uk_mims import UkMimsScraper
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", None)        # `from scrapling.fetchers import ...` → ImportError
    s = UkMimsScraper(cache_dir=tmp_path, credentials={"username": "u", "password": "p"})
    with pytest.raises(ImportError):
        asyncio.run(s.run("keytruda"))
    assert fpa.classify_scrape_error(ImportError("x")) == "dependency"
