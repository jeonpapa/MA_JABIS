"""IT AIFA Class A/H CSV 캐시 TTL + 갱신 실패 시 '구 파일 사용' 을 숨기지 않는다 (P1.6 수집 가능성).

감사가 본 결함: `_ensure_class_rows` 가 캐시 파일이 있으면 영원히 재사용 — `Classe_*_30-09-2025.csv` 가 12개월째 39회
로드됐다. "현 시점의 가격" 원칙에 어긋난다. 수정: ① 캐시 나이가 `CSV_CACHE_TTL_DAYS`(30일) 를 넘으면 AIFA 페이지에서
최신 CSV 를 다시 찾는다 — 같은 파일명이면 touch 만(재다운로드 없음), 새 파일명이면 다운로드, ② 갱신이 실패하면 구 파일로
계속 가되 `scraper.warnings` 에 `stale_cache` 를 남겨 search_all → SearchResults.warnings → 원장(foreign_search:IT:stale_cache)
→ country_results[IT].warnings 로 보이게 한다, ③ 캐시가 아예 없고 갱신도 실패하면 예외(수집 실패) 를 올린다.

네트워크 금지: fixture CSV 는 tests/fixtures/it_aifa (실제 2025-09-30 공시 파일에서 추적 약제 행만 추림, latin-1 ';').

실행: .venv/bin/python -m pytest tests/test_it_aifa_csv_ttl.py -q
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import foreign_price_agent as fpa
from agents.scrapers.it_aifa import AIFA_DOC_BASE, ItAifaScraper

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "it_aifa"
A_NAME = "Classe_A_per_nome_commerciale_30-09-2025.csv"
H_NAME = "Classe_H_per_nome_commerciale_30-09-2025.csv"
DAY = 86400


def _cache(tmp_path: Path, *, age_days: float = 0.0) -> Path:
    d = tmp_path / "it"
    d.mkdir()
    for n in (A_NAME, H_NAME):
        shutil.copy(FIXTURES / n, d / n)
        ts = time.time() - age_days * DAY
        os.utime(d / n, (ts, ts))
    return d


def _no_network(monkeypatch, scraper: ItAifaScraper, *, find=None, download=None):
    def _find():
        raise AssertionError("네트워크 금지 — 신선한 캐시인데 AIFA 페이지를 조회했다")

    def _dl(url):
        raise AssertionError(f"네트워크 금지 — CSV 를 다운로드하려 했다: {url}")
    monkeypatch.setattr(scraper, "_find_csv_urls_requests", find or _find)
    monkeypatch.setattr(scraper, "_download_csv", download or _dl)


def test_ttl_constant_is_monthly_and_under_freshness_threshold():
    """AIFA 리스트는 월 단위로 재공시(파일명 30-09-2025, 헤더 'lista di trasparenza 15/9/2025'); foreign_price_age 는 45일에
    stale 로 본다 → 30일이면 한 공시 주기 이상 뒤처지지 않고 신선도 판정보다 먼저 갱신된다."""
    assert ItAifaScraper.CSV_CACHE_TTL_DAYS == 30
    assert 0 < ItAifaScraper.CSV_CACHE_TTL_DAYS < 45


def test_fresh_cache_is_served_without_touching_the_network(monkeypatch, tmp_path):
    s = ItAifaScraper(cache_dir=_cache(tmp_path, age_days=1))
    _no_network(monkeypatch, s)
    rows = asyncio.run(s.search("keytruda", page=None))
    assert len(rows) == 1 and rows[0]["product_name"].startswith("KEYTRUDA*1 flaconcino")
    assert rows[0]["local_price"] == 3428.0 and rows[0]["extra"]["aifa_class"] == "H"
    assert s.warnings == []


def test_stale_cache_refresh_failure_falls_back_and_surfaces_stale_warning(monkeypatch, tmp_path):
    s = ItAifaScraper(cache_dir=_cache(tmp_path, age_days=40))

    def _find():
        raise ConnectionError("AIFA SSL: CERTIFICATE_VERIFY_FAILED")
    _no_network(monkeypatch, s, find=_find)
    rows = asyncio.run(s.search("januvia", page=None))
    assert [r["product_name"] for r in rows] == ["JANUVIA*28 cpr rivestite 100 mg", "JANUVIA*28 cpr rivestite 25 mg",
                                                 "JANUVIA*28 cpr rivestite 50 mg"]
    classes = {w["warning_class"] for w in s.warnings}
    assert classes == {"stale_cache"}
    msgs = " | ".join(w["message"] for w in s.warnings)
    assert "Class A" in msgs and "Class H" in msgs and "30-09-2025" in msgs and "40일" in msgs
    assert "ConnectionError" in msgs and "CERTIFICATE_VERIFY_FAILED" in msgs
    assert all(w["channel"] == "aifa_csv" for w in s.warnings)


def test_stale_cache_same_published_file_is_touched_not_redownloaded(monkeypatch, tmp_path):
    d = _cache(tmp_path, age_days=40)
    s = ItAifaScraper(cache_dir=d)
    _no_network(monkeypatch, s, find=lambda: {"A": f"{AIFA_DOC_BASE}/documents/x/{A_NAME}",
                                              "H": f"{AIFA_DOC_BASE}/documents/x/{H_NAME}"})
    before = (d / A_NAME).stat().st_mtime
    rows = asyncio.run(s.search("prevymis", page=None))
    assert len(rows) == 4                                            # oral A ×2 + injection H ×2
    assert (d / A_NAME).stat().st_mtime > before + 30 * DAY          # touch → 다음 30일은 네트워크 없이
    assert s.warnings == []


def test_stale_cache_new_published_file_is_downloaded_and_preferred(monkeypatch, tmp_path):
    d = _cache(tmp_path, age_days=40)
    s = ItAifaScraper(cache_dir=d)
    new_a = "Classe_A_per_nome_commerciale_15-10-2025.csv"
    new_h = "Classe_H_per_nome_commerciale_15-10-2025.csv"
    downloaded: list[str] = []

    def _dl(url):
        name = url.rsplit("/", 1)[-1]
        downloaded.append(name)
        src = FIXTURES / (A_NAME if name.startswith("Classe_A") else H_NAME)
        text = src.read_bytes().decode("latin-1").replace("3428,00", "3500,00")   # 새 공시: Keytruda ex-factory 변경
        (d / name).write_bytes(text.encode("latin-1"))
        return d / name
    _no_network(monkeypatch, s, find=lambda: {"A": f"{AIFA_DOC_BASE}/documents/x/{new_a}",
                                              "H": f"{AIFA_DOC_BASE}/documents/x/{new_h}"}, download=_dl)
    rows = asyncio.run(s.search("keytruda", page=None))
    assert sorted(downloaded) == sorted([new_a, new_h])
    assert rows[0]["local_price"] == 3500.0 and s.warnings == []
    # 파일명 날짜(DD-MM-YYYY)는 사전순이 아니라 날짜순 — 15-10-2025 가 30-09-2025 보다 최신
    assert s._load_latest_cache("A").name == new_a and s._load_latest_cache("H").name == new_h


def test_no_cache_and_refresh_failure_raises_instead_of_empty(monkeypatch, tmp_path):
    s = ItAifaScraper(cache_dir=tmp_path / "empty")

    def _find():
        raise ConnectionError("AIFA unreachable")
    _no_network(monkeypatch, s, find=_find)
    with pytest.raises(ConnectionError):
        asyncio.run(s.search("keytruda", page=None))
    assert fpa.classify_scrape_error(ConnectionError("AIFA unreachable")) == "other"


def test_aifa_page_is_queried_once_for_both_classes(monkeypatch, tmp_path):
    s = ItAifaScraper(cache_dir=_cache(tmp_path, age_days=40))
    calls = []

    def _find():
        calls.append(1)
        return {"A": f"{AIFA_DOC_BASE}/documents/x/{A_NAME}", "H": f"{AIFA_DOC_BASE}/documents/x/{H_NAME}"}
    _no_network(monkeypatch, s, find=_find)
    asyncio.run(s.search("keytruda", page=None))
    assert len(calls) == 1


# ── 에이전트·서버: 경고가 SearchResults.warnings → 원장 → country_results 로 흐른다 ─────────────────────────

def _record(**kw) -> dict:
    """BaseScraper.run() 이 돌려주는 DB 저장형 레코드 (save_foreign_price 의 바인딩 키 전부)."""
    base = {"searched_at": "2026-10-06T00:00:00", "query_name": "keytruda", "country": "IT", "product_name": "KEYTRUDA*1 flaconcino",
            "ingredient": "Pembrolizumab", "dosage_strength": "KEYTRUDA*1 flaconcino infus EV 4 ml 25 mg/ml", "dosage_form": "",
            "package_unit": "", "local_price": 3428.0, "currency": "EUR", "exchange_rate": None, "exchange_rate_from": None,
            "exchange_rate_to": None, "factory_price_krw": None, "vat_rate": None, "distribution_margin": None,
            "adjusted_price_krw": None, "source_url": "", "source_label": "AIFA Lista Classe H (Prezzo Ex-factory)",
            "raw_data": "{}", "form_type": "injection"}
    return {**base, **kw}


class _StubIt:
    COUNTRY = "IT"; CURRENCY = "EUR"; SOURCE_TYPE = "aifa_exfactory"; FALLBACK_TO_INN = False

    def __init__(self):
        self.warnings = [{"warning_class": "stale_cache", "channel": "aifa_csv",
                          "message": "AIFA Class H CSV 갱신 실패(ConnectionError: x) — 40일 된 Classe_H_...csv 사용"}]

    def _resolve_form_type(self, item):
        return "injection"

    async def run(self, term):
        return [_record(query_name=term)]


class _NoTnf:
    def __init__(self, **kw):
        pass

    async def run(self, term):
        return []


def _agent(monkeypatch, tmp_path) -> fpa.ForeignPriceAgent:
    (tmp_path / "data" / "db").mkdir(parents=True)
    agent = fpa.ForeignPriceAgent(tmp_path)
    monkeypatch.setattr(agent, "_build_scraper", lambda c: _StubIt())
    monkeypatch.setattr(fpa, "ItAifaTnfScraper", _NoTnf)
    monkeypatch.setattr(agent.rate_fetcher, "get_36m_average", lambda *a, **k: None)
    monkeypatch.setattr(agent, "_normalize_doses_across_countries", lambda q, r: None)
    return agent


def test_search_all_carries_scraper_warning_to_results_and_ledger(monkeypatch, tmp_path):
    from agents.agent_tasks.store import list_tasks
    agent = _agent(monkeypatch, tmp_path)
    res = asyncio.run(agent.search_all("keytruda", ["IT"]))
    assert res["IT"] and res["IT"][0]["local_price"] == 3428.0 and res.errors == {}
    assert [w["warning_class"] for w in res.warnings["IT"]] == ["stale_cache"]
    tasks = {t["source_ref"]: t for t in list_tasks(db_path=Path(os.environ["AGENT_TASK_DB"]))}
    assert set(tasks) == {"IT:stale_cache"}
    t = tasks["IT:stale_cache"]
    assert t["source"] == "foreign_search" and t["kind"] == "ops_incident" and "캐시 만료" in t["title"]
    assert "keytruda" in t["detail"] and "40일" in t["detail"]


def test_country_results_keep_priced_status_but_expose_warning():
    import api.server as s
    results = fpa.SearchResults({"IT": [{"local_price": 3428.0}]})
    results.warnings["IT"] = [{"warning_class": "stale_cache", "channel": "aifa_csv", "message": "갱신 실패 — 40일 된 파일 사용"}]
    cr = s._compute_country_results("keytruda", ["IT"], results)
    assert cr["IT"]["status"] == "priced" and cr["IT"]["has_price"] is True
    assert cr["IT"]["warnings"] == [{"warning_class": "stale_cache", "warning_label": "캐시 만료(갱신 실패)",
                                     "channel": "aifa_csv", "message": "갱신 실패 — 40일 된 파일 사용"}]
    assert "warnings" not in s._compute_country_results("keytruda", ["IT"], fpa.SearchResults({"IT": [{"local_price": 1}]}))["IT"]
