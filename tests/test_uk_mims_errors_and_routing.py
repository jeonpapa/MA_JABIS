"""UK MIMS 는 조용히 빈 결과를 내지 않는다 + INN 검색은 identity 로 브랜드 페이지에 간다 (P1.6 수집 가능성, Item 5).

감사: `uk_mims._scrape_sync` 가 자격증명 없음·로그인벽 잔존에서 `[]` 를 돌려줘 화면이 "MIMS 구독 필요" 정책 문구로 감쌌고
원장에는 아무것도 없었다(관측 슬라이스는 ImportError/timeout 만 올렸다). 또 INN(`pembrolizumab`) 검색은 `/drugs/pembrolizumab/`
처럼 Price 섹션이 없는 페이지에 닿았다(약제당 4회).

수정: ① `ScraperConfigError`(자격증명 미설정 → config) · `ScraperLoginError`(로그인벽 잔존 → login) 를 올린다 —
`classify_scrape_error` 가 타입으로 먼저 분류하고 원장 `foreign_search:UK:config|login` + 화면 "수집 실패(설정 오류|로그인 오류)".
② `drug_identity.derive_identity_offline`(정확 일치, 네트워크 없음) 로 identity 가 없을 때도 brand 로 검색한다 — JP 와 같은
자원(product_alias_map / BRAND_TO_MOLECULE). 네트워크·scrapling 금지 — 세션은 가짜 모듈.

실행: .venv/bin/python -m pytest tests/test_uk_mims_errors_and_routing.py -q
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import drug_identity as di
from agents import foreign_price_agent as fpa
from agents.db import drug_aliases
from agents.scrapers.base import ScraperConfigError, ScraperLoginError
from agents.scrapers.uk_mims import LOGIN_URL, MIMS_BASE, UkMimsScraper

LOGIN_WALL = "<html><body><h1>Log in or register</h1> to view prices</body></html>"
KEYTRUDA_PAGE = ("<html><body><h1>Keytruda</h1><p>Manufacturer: MSD</p><p>Price:</p><p>100mg/4ml conc for soln for inf in vial, 2=£5260.00.</p>"
                 "<p>Indications: Melanoma</p><p>How Supplied: Pembrolizumab</p></body></html>")
KEYTRUDA_URL = f"{MIMS_BASE}/drugs/cancer/antineoplastics/keytruda"


class _Resp:
    def __init__(self, html, url=""):
        self.html_content, self.url = html, url


def _fake_scrapling(monkeypatch, pages: dict, *, login_html="<html>ok</html>"):
    """scrapling.fetchers.StealthySession 가짜 — fetch(url) 이 pages 의 HTML/URL 을 돌려준다. 로그인은 login_html."""
    calls = []

    class StealthySession:
        def __init__(self, **kw):
            self.kw = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def fetch(self, url, page_action=None, **kw):
            calls.append(url)
            if url == LOGIN_URL:
                if page_action:
                    page_action(_FakePage())
                return _Resp(login_html, url)
            html, landed = pages.get(url, ("", url))
            return _Resp(html, landed)

    class _FakePage:
        def wait_for_timeout(self, ms): pass
        def click(self, sel, timeout=None): pass
        def fill(self, sel, val, timeout=None): pass

    mod = types.ModuleType("scrapling.fetchers")
    mod.StealthySession = StealthySession
    pkg = types.ModuleType("scrapling")
    pkg.fetchers = mod
    monkeypatch.setitem(sys.modules, "scrapling", pkg)
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", mod)
    return calls


# ── ① 자격증명 없음 → config, 로그인벽 잔존 → login ──────────────────────────────────────────────────

def test_missing_credentials_raise_config_error_not_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("MIMS_UK_PROXY", raising=False)
    s = UkMimsScraper(cache_dir=tmp_path, credentials={})
    with pytest.raises(ScraperConfigError) as ei:
        asyncio.run(s.run("keytruda"))
    assert "MIMS_UK_USERNAME" in str(ei.value) and "MIMS_UK_PASSWORD" in str(ei.value)
    assert fpa.classify_scrape_error(ei.value) == "config"


def test_login_wall_after_login_raises_login_error(tmp_path, monkeypatch):
    monkeypatch.delenv("MIMS_UK_PROXY", raising=False)
    calls = _fake_scrapling(monkeypatch, {}, login_html=LOGIN_WALL)
    s = UkMimsScraper(cache_dir=tmp_path, credentials={"username": "u", "password": "p"})
    with pytest.raises(ScraperLoginError) as ei:
        asyncio.run(s.run("keytruda"))
    assert "로그인벽" in str(ei.value) and calls == [LOGIN_URL]
    assert fpa.classify_scrape_error(ei.value) == "login"


def test_logged_in_search_still_parses_prices(tmp_path, monkeypatch):
    """회귀 방지 — 예외를 올리는 변경이 정상 경로(302 → 약제 페이지 → Price 섹션)를 깨지 않는다."""
    monkeypatch.delenv("MIMS_UK_PROXY", raising=False)
    search_url = f"{MIMS_BASE}/search/drugs?keywords=keytruda"
    _fake_scrapling(monkeypatch, {search_url: (KEYTRUDA_PAGE, KEYTRUDA_URL)})
    s = UkMimsScraper(cache_dir=tmp_path, credentials={"username": "u", "password": "p"})
    rows = asyncio.run(s.run("keytruda"))
    assert len(rows) == 1 and rows[0]["local_price"] == 5260.0 and rows[0]["product_name"] == "Keytruda"
    assert rows[0]["dosage_strength"] == "100mg/4ml conc for soln for inf in vial, 2" and rows[0]["source_url"] == KEYTRUDA_URL


@pytest.mark.parametrize("exc, expected", [
    (ScraperConfigError("MIMS 자격증명 없음 — config/.env MIMS_UK_USERNAME/MIMS_UK_PASSWORD 미설정"), "config"),   # '자격증명' 힌트보다 타입 우선
    (ScraperLoginError("MIMS 로그인 실패 (로그인벽 잔존)"), "login"),
    (RuntimeError("Micromedex login failed"), "login"),                                                        # 기존 힌트 경로 유지
])
def test_classifier_prefers_error_type_over_message_hints(exc, expected):
    assert fpa.classify_scrape_error(exc) == expected
    assert "config" in fpa.SCRAPE_ERROR_CLASSES and fpa.SCRAPE_ERROR_LABEL_KR["config"] == "설정 오류"


def test_country_results_label_config_failure(monkeypatch):
    import api.server as s
    results = fpa.SearchResults({"UK": []})
    results.errors["UK"] = {"error_class": "config", "message": "ScraperConfigError: MIMS 자격증명 없음 — MIMS_UK_USERNAME 미설정"}
    uk = s._compute_country_results("keytruda", ["UK"], results)["UK"]
    assert uk["status"] == "scraper_error" and uk["error_label"] == "설정 오류" and uk["reason"].startswith("수집 실패(설정 오류)")
    assert "구독" not in uk["reason"]


def test_search_all_records_uk_config_failure_in_ledger(monkeypatch, tmp_path):
    import os
    from agents.agent_tasks.store import list_tasks
    monkeypatch.delenv("MIMS_UK_PROXY", raising=False)
    (tmp_path / "data" / "db").mkdir(parents=True)
    agent = fpa.ForeignPriceAgent(tmp_path)
    monkeypatch.setattr(agent, "_build_scraper", lambda c: UkMimsScraper(cache_dir=tmp_path, credentials={}))
    monkeypatch.setattr(agent, "_normalize_doses_across_countries", lambda q, r: None)
    res = asyncio.run(agent.search_all("keytruda", ["UK"]))
    assert res["UK"] == [] and res.errors["UK"]["error_class"] == "config"
    tasks = {t["source_ref"]: t for t in list_tasks(db_path=Path(os.environ["AGENT_TASK_DB"]))}
    assert set(tasks) == {"UK:config"} and "설정 오류" in tasks["UK:config"]["title"]


# ── ② INN → 브랜드 페이지: identity 유도(정확 일치·오프라인) ───────────────────────────────────────────

@pytest.fixture(autouse=True)
def _in_memory_aliases(monkeypatch):
    monkeypatch.setattr(drug_aliases, "_db_cache", {})


@pytest.mark.parametrize("q, expected", [
    ("pembrolizumab", {"slug": "keytruda", "brand": "keytruda", "inn": "pembrolizumab"}),
    ("Pembrolizumab ", {"slug": "keytruda", "brand": "keytruda", "inn": "pembrolizumab"}),
    ("keytruda", {"slug": "keytruda", "brand": "keytruda", "inn": "pembrolizumab"}),
    ("belzutifan", {"slug": "welireg", "brand": "welireg", "inn": "belzutifan"}),
    ("sitagliptin", {"slug": "januvia", "brand": "januvia", "inn": "sitagliptin"}),
    ("nivolumab", {"slug": "opdivo", "brand": "opdivo", "inn": "nivolumab"}),      # 오타 alias(obdivo) 가 아니라 첫 브랜드
    ("pembro", None),                                                               # 부분 일치는 쓰지 않는다 (애매)
    ("zzq-unknown", None),
    ("", None),
])
def test_derive_identity_offline_exact_match_only(q, expected):
    got = di.derive_identity_offline(q)
    assert (got and {k: got[k] for k in ("slug", "brand", "inn")}) == expected if expected else got is None


def test_derive_identity_offline_uses_db_alias_map_first(monkeypatch):
    entry = {"slug": "keytruda", "inn": "pembrolizumab", "brand_aliases": ["Keytruda", "키트루다", "MK-3475"], "agency_brand_overrides": {}}
    monkeypatch.setattr(drug_aliases, "_db_cache", {k: entry for k in ("keytruda", "pembrolizumab", "키트루다", "mk-3475")})
    got = di.derive_identity_offline("MK-3475")
    assert got["slug"] == "keytruda" and got["brand"] == "keytruda" and got["inn"] == "pembrolizumab" and got["source"] == "alias"


def test_derive_identity_offline_never_calls_mfds(monkeypatch):
    def _boom(**kw):
        raise AssertionError("네트워크 금지 — MFDS 폴백을 호출했다")
    monkeypatch.setattr(di, "lookup_permit", _boom)
    assert di.derive_identity_offline("zzq-unknown") is None


class _Capture:
    COUNTRY = "UK"; CURRENCY = "GBP"; SOURCE_TYPE = None; FALLBACK_TO_INN = False

    def __init__(self):
        self.terms, self.warnings = [], []

    def _resolve_form_type(self, item):
        return "injection"

    async def run(self, term):
        self.terms.append(term)
        return []


def _agent(monkeypatch, tmp_path, stub):
    (tmp_path / "data" / "db").mkdir(parents=True)
    agent = fpa.ForeignPriceAgent(tmp_path)
    monkeypatch.setattr(agent, "_build_scraper", lambda c: stub)
    return agent


def test_inn_query_without_identity_is_searched_as_brand(monkeypatch, tmp_path):
    stub = _Capture()
    agent = _agent(monkeypatch, tmp_path, stub)
    asyncio.run(agent.search_one_country("pembrolizumab", "UK"))
    assert stub.terms == ["keytruda"]


def test_explicit_identity_still_wins(monkeypatch, tmp_path):
    stub = _Capture()
    agent = _agent(monkeypatch, tmp_path, stub)
    asyncio.run(agent.search_one_country("pembrolizumab", "UK", identity={"slug": "keytruda", "brand": "Keytruda", "inn": "pembrolizumab"}))
    assert stub.terms == ["Keytruda"]


def test_unknown_query_is_searched_verbatim(monkeypatch, tmp_path):
    stub = _Capture()
    agent = _agent(monkeypatch, tmp_path, stub)
    asyncio.run(agent.search_one_country("zzq-unknown", "UK"))
    assert stub.terms == ["zzq-unknown"]


def test_result_urls_prefer_brand_page_over_inn_index():
    html = ('<a href="/drugs/pembrolizumab/">pembrolizumab</a> <a href="/drugs/cancer/antineoplastics/keytruda">Keytruda</a>'
            '<a href="/drugs/cancer/">cancer</a>')
    urls = UkMimsScraper._pick_result_urls(html, "keytruda")
    assert urls[0] == KEYTRUDA_URL
