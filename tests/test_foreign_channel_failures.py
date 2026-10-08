"""다채널 국가의 채널 실패는 경고로 삼키지 않고 분류-오류 경로로 흐른다 + 동기 검색 경로가 .errors 를 버리지 않는다 (P1.6 Item 6).

감사: `search_one_country` 가 IT CSV 채널 실패를 warning 로그로만 삼키고 TNF 로 계속 갔다 — TNF 도 0건이면 "검색 결과 없음"
이 돼 화면은 '제도상 비공개' 문구, 원장은 공백. FR BDPM/Vidal 도 같은 구조. 또 `GET /api/foreign/cached?use_cache=false`
(동기 경로)는 `SearchResults.errors` 를 읽지 않아 로그인 실패가 응답 어디에도 없었다.

수정: 채널 실패를 모아 ① 결과가 하나도 없으면 첫 실패를 **예외로 올린다**(search_all → errors[cc], 원장 foreign_search:{cc}:{분류})
② 다른 채널 결과가 있으면 `channel_failed` 경고(분류 포함)로 내리고 **같은 분류 키**로 원장에 남긴다. 동기 경로는 응답에
`country_results`(status/error_label/warnings) + `search_errors` 를 싣는다. 네트워크 금지 — 채널은 가짜.

실행: .venv/bin/python -m pytest tests/test_foreign_channel_failures.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import foreign_price_agent as fpa
from agents.agent_tasks.store import list_tasks


def _rec(**kw) -> dict:
    base = {"searched_at": "x", "query_name": "keytruda", "country": "IT", "product_name": "KEYTRUDA", "ingredient": "Pembrolizumab",
            "dosage_strength": "1 flaconcino", "dosage_form": "", "package_unit": "", "local_price": 3428.0, "currency": "EUR",
            "exchange_rate": None, "exchange_rate_from": None, "exchange_rate_to": None, "factory_price_krw": None, "vat_rate": None,
            "distribution_margin": None, "adjusted_price_krw": None, "source_url": "", "source_label": "x", "raw_data": "{}",
            "form_type": "injection"}
    return {**base, **kw}


class _Chan:
    """채널 가짜 — rows 를 돌려주거나 exc 를 던진다."""
    FALLBACK_TO_INN = False

    def __init__(self, country, rows=None, exc=None, **kw):
        self.COUNTRY, self.rows, self.exc, self.warnings = country, rows or [], exc, []

    def _resolve_form_type(self, item):
        return "injection"

    async def run(self, term):
        if self.exc:
            raise self.exc
        return [_rec(country=self.COUNTRY, **r) for r in self.rows]


def _agent(monkeypatch, tmp_path, primary, secondary_attr, secondary):
    (tmp_path / "data" / "db").mkdir(parents=True)
    agent = fpa.ForeignPriceAgent(tmp_path)
    monkeypatch.setattr(agent, "_build_scraper", lambda c: primary)
    monkeypatch.setattr(fpa, secondary_attr, lambda **kw: secondary)
    monkeypatch.setattr(agent.rate_fetcher, "get_36m_average", lambda *a, **k: None)
    monkeypatch.setattr(agent, "_normalize_doses_across_countries", lambda q, r: None)
    return agent


def _ledger():
    return {t["source_ref"]: t for t in list_tasks(db_path=Path(os.environ["AGENT_TASK_DB"]))}


# ── IT ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_it_csv_failure_with_tnf_rows_is_a_classified_warning_on_the_same_ledger_key(monkeypatch, tmp_path):
    csv = _Chan("IT", exc=TimeoutError("AIFA page timed out"))
    tnf = _Chan("IT", rows=[{"local_price": 3798.34, "source_label": "AIFA TrovaNorme (Determina prezzo, ex-factory)"}])
    agent = _agent(monkeypatch, tmp_path, csv, "ItAifaTnfScraper", tnf)
    res = asyncio.run(agent.search_all("keytruda", ["IT"]))
    assert len(res["IT"]) == 1 and res["IT"][0]["local_price"] == 3798.34 and "IT" not in res.errors
    (w,) = res.warnings["IT"]
    assert w["warning_class"] == "channel_failed" and w["channel"] == "aifa_csv" and w["error_class"] == "timeout"
    assert "TimeoutError" in w["message"] and "exc" not in w
    assert set(_ledger()) == {"IT:timeout"}                            # 전체 실패와 같은 분류 키


def test_it_csv_failure_and_tnf_empty_is_a_scraper_error_not_no_result(monkeypatch, tmp_path):
    csv = _Chan("IT", exc=ConnectionError("AIFA SSL: CERTIFICATE_VERIFY_FAILED"))
    tnf = _Chan("IT", rows=[])
    agent = _agent(monkeypatch, tmp_path, csv, "ItAifaTnfScraper", tnf)
    res = asyncio.run(agent.search_all("januvia", ["IT"]))
    assert res["IT"] == [] and res.errors["IT"]["error_class"] == "other" and "CERTIFICATE_VERIFY_FAILED" in res.errors["IT"]["message"]
    assert set(_ledger()) == {"IT:other"}
    import api.server as s
    it = s._compute_country_results("januvia", ["IT"], res)["IT"]
    assert it["status"] == "scraper_error" and "비공개" not in it["reason"]


def test_it_tnf_failure_with_csv_rows_is_a_warning(monkeypatch, tmp_path):
    csv = _Chan("IT", rows=[{}])
    tnf = _Chan("IT", exc=RuntimeError("HTTP 403 Forbidden"))
    agent = _agent(monkeypatch, tmp_path, csv, "ItAifaTnfScraper", tnf)
    res = asyncio.run(agent.search_all("keytruda", ["IT"]))
    assert len(res["IT"]) == 1 and "IT" not in res.errors
    (w,) = res.warnings["IT"]
    assert w["channel"] == "tnf" and w["error_class"] == "http_403"
    assert set(_ledger()) == {"IT:http_403"}


def test_it_both_channels_failing_raises_the_primary_error(monkeypatch, tmp_path):
    csv = _Chan("IT", exc=TimeoutError("csv timeout"))
    tnf = _Chan("IT", exc=RuntimeError("tnf 500"))
    agent = _agent(monkeypatch, tmp_path, csv, "ItAifaTnfScraper", tnf)
    res = asyncio.run(agent.search_all("keytruda", ["IT"]))
    assert res["IT"] == [] and res.errors["IT"]["error_class"] == "timeout" and "csv timeout" in res.errors["IT"]["message"]


def test_it_both_channels_empty_without_errors_is_plain_no_result(monkeypatch, tmp_path):
    agent = _agent(monkeypatch, tmp_path, _Chan("IT"), "ItAifaTnfScraper", _Chan("IT"))
    res = asyncio.run(agent.search_all("zzq", ["IT"]))
    assert res["IT"] == [] and res.errors == {} and res.warnings == {} and _ledger() == {}


# ── FR ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_fr_vidal_failure_with_bdpm_rows_is_a_warning(monkeypatch, tmp_path):
    bdpm = _Chan("FR", rows=[{"local_price": None, "currency": "EUR"}])
    vidal = _Chan("FR", exc=RuntimeError("Vidal login failed: 'Please login'"))
    agent = _agent(monkeypatch, tmp_path, bdpm, "FrVidalScraper", vidal)
    res = asyncio.run(agent.search_all("keytruda", ["FR"]))
    assert len(res["FR"]) == 1 and "FR" not in res.errors
    (w,) = res.warnings["FR"]
    assert w["channel"] == "vidal" and w["error_class"] == "login"
    assert set(_ledger()) == {"FR:login"}


def test_fr_bdpm_failure_and_vidal_empty_is_a_scraper_error(monkeypatch, tmp_path):
    bdpm = _Chan("FR", exc=ConnectionError("bdpm download failed"))
    vidal = _Chan("FR", rows=[])
    agent = _agent(monkeypatch, tmp_path, bdpm, "FrVidalScraper", vidal)
    res = asyncio.run(agent.search_all("keytruda", ["FR"]))
    assert res["FR"] == [] and res.errors["FR"]["error_class"] == "other"


# ── 단일 채널 국가는 그대로 전파 (회귀) ───────────────────────────────────────────────────────────────

def test_single_channel_country_failure_still_propagates(monkeypatch, tmp_path):
    (tmp_path / "data" / "db").mkdir(parents=True)
    agent = fpa.ForeignPriceAgent(tmp_path)
    monkeypatch.setattr(agent, "_build_scraper", lambda c: _Chan("UK", exc=RuntimeError("Please login")))
    monkeypatch.setattr(agent, "_normalize_doses_across_countries", lambda q, r: None)
    res = asyncio.run(agent.search_all("keytruda", ["UK"]))
    assert res.errors["UK"]["error_class"] == "login" and res.warnings == {}


# ── 동기 경로: GET /api/foreign/cached?use_cache=false ─────────────────────────────────────────────────

def test_sync_refresh_endpoint_carries_country_results_and_search_errors(monkeypatch):
    import api.server as s

    async def _stub(query, countries=None, identity=None):
        out = fpa.SearchResults({c: [] for c in fpa.AVAILABLE_COUNTRIES})
        out["IT"] = [{"local_price": 3428.0}]
        out.errors["US"] = {"error_class": "login", "message": "RuntimeError: Micromedex login failed: redirected to 'Please Login' page"}
        out.errors["UK"] = {"error_class": "config", "message": "ScraperConfigError: MIMS 자격증명 없음"}
        out.warnings["IT"] = [{"warning_class": "stale_cache", "channel": "aifa_csv", "message": "40일 된 파일 사용"}]
        return out
    monkeypatch.setattr(s.foreign_agent, "search_all", _stub)
    monkeypatch.setattr(s.foreign_agent, "get_cached_results", lambda q: {})
    monkeypatch.setattr(s.foreign_agent, "get_formulation_groups", lambda q, by_country=None: {})
    monkeypatch.setattr(s.db, "log_search", lambda *a, **k: None)
    c = s.app.test_client()
    body = c.get("/api/foreign/cached?q=keytruda&use_cache=false").get_json()
    assert body["refreshed"] is True
    cr = body["country_results"]
    assert cr["US"]["status"] == "scraper_error" and cr["US"]["error_label"] == "로그인 오류" and "Please Login" in cr["US"]["error"]
    assert cr["UK"]["status"] == "scraper_error" and cr["UK"]["error_label"] == "설정 오류"
    assert cr["IT"]["status"] == "priced" and cr["IT"]["warnings"][0]["warning_label"] == "캐시 만료(갱신 실패)"
    assert cr["FR"]["status"] == "no_result"
    assert set(body["search_errors"]) == {"US", "UK"} and body["search_errors"]["US"]["error_class"] == "login"


def test_cache_only_endpoint_has_no_search_fields(monkeypatch):
    import api.server as s
    monkeypatch.setattr(s.foreign_agent, "get_cached_results", lambda q: {})
    monkeypatch.setattr(s.foreign_agent, "get_formulation_groups", lambda q, by_country=None: {})
    monkeypatch.setattr(s.db, "log_search", lambda *a, **k: None)
    body = s.app.test_client().get("/api/foreign/cached?q=keytruda").get_json()
    assert body["refreshed"] is False and "country_results" not in body and "search_errors" not in body
