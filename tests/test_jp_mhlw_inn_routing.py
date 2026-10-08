"""JP MHLW: INN(성분명) 검색이 브랜드 일본어 표기로 라우팅된다 + 최신 파일 탐색 + 캐시 신선도 (P1.6 수집 가능성, Item 4).

감사: `EN_JP_NAME_MAP` 이 브랜드 전용(keytruda→キイトルーダ)이라 `pembrolizumab` 은 항상 0건 — 로그의 JP 검색 39/39 이 0.
수정: 두 번째 손관리 표를 만들지 않고 **이미 있는 것**으로 INN → 일본어 표기를 푼다 —
  ① product_alias_map.agency_brand_overrides["PMDA"] (drug_aliases.agency_brand)
  ② drug_aliases.aliases(query)(slug·inn·brand_aliases, 없으면 BRAND_TO_MOLECULE 역조회) → EN_JP_NAME_MAP
  ③ 원문 (영문 표기가 섞인 행 대비)
또 ④ 캐시 디렉터리에 여러 공시본이 있으면 카테고리별 **최신 1본만** 병합(이전엔 전부 concat → 구 가격 중복),
⑤ 페이지 URL 을 2025/04 고정 대신 캐시 파일명(tpYYYYMMDD)에서 유도한 페이지 → 환경변수 → 상수 순으로 탐색하고 가장
최신 날짜의 xlsx 를 고른다, ⑥ 캐시가 TTL(90일) 을 넘으면 갱신을 시도하고 실패/불가 시 stale_cache 경고를 남긴다.

fixture: tests/fixtures/jp_mhlw/tp20260318-01_0{1,2,3}.xlsx — 실제 캐시(시트 'ＨＰ用', 헤더 1행)에서 추적 약제 행 + 선두
decoy 3행만 같은 구조로 추림. 네트워크 금지.

실행: .venv/bin/python -m pytest tests/test_jp_mhlw_inn_routing.py -q
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

from agents.db import drug_aliases
from agents.scrapers import jp_mhlw
from agents.scrapers.jp_mhlw import JpMhlwScraper

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jp_mhlw"
REAL_CACHE = Path(__file__).resolve().parent.parent / "data" / "foreign" / "jp"
FILES = ["tp20260318-01_01.xlsx", "tp20260318-01_02.xlsx", "tp20260318-01_03.xlsx"]
DAY = 86400


def _cache(tmp_path: Path, *, age_days: float = 0.0, names=FILES) -> Path:
    d = tmp_path / "jp"
    d.mkdir(exist_ok=True)
    for n in names:
        src = FIXTURES / (n if (FIXTURES / n).exists() else FILES[int(n[-7:-5]) - 1])
        shutil.copy(src, d / n)
        ts = time.time() - age_days * DAY
        os.utime(d / n, (ts, ts))
    return d


@pytest.fixture(autouse=True)
def _in_memory_aliases(monkeypatch):
    """DB alias 캐시를 비워 BRAND_TO_MOLECULE 경로를 고정 (워크트리 stub DB 는 product_alias_map 이 비어 있다)."""
    monkeypatch.setattr(drug_aliases, "_db_cache", {})


# ── ① ② ③ 검색 키워드 해석 ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query,jp", [
    ("pembrolizumab", "キイトルーダ"),
    ("Pembrolizumab", "キイトルーダ"),
    ("belzutifan", "ウェリレグ"),
    ("letermovir", "プレバイミス"),
    ("sitagliptin", "ジャヌビア"),
    ("keytruda", "キイトルーダ"),          # 기존 브랜드 경로 유지
    ("Keytruda", "キイトルーダ"),
])
def test_inn_and_brand_both_resolve_to_japanese_brand(query, jp):
    s = JpMhlwScraper.__new__(JpMhlwScraper)
    kws = s._resolve_jp_name(query)
    assert kws[0] == jp and query in kws and len(kws) == len(set(kws)), kws


def test_unknown_name_keeps_only_the_original():
    s = JpMhlwScraper.__new__(JpMhlwScraper)
    assert s._resolve_jp_name("zzq-unknown") == ["zzq-unknown"]


def test_pmda_override_from_product_alias_map_wins_without_en_jp_map(monkeypatch):
    """product_alias_map(agency_brand_overrides.PMDA) 만으로도 푼다 — EN_JP_NAME_MAP 은 보조."""
    entry = {"slug": "keytruda", "inn": "pembrolizumab", "brand_aliases": ["Keytruda", "키트루다"],
             "agency_brand_overrides": {"EMA": "Keytruda", "PMDA": "キイトルーダ"}}
    monkeypatch.setattr(drug_aliases, "_db_cache", {k: entry for k in ("keytruda", "pembrolizumab", "키트루다")})
    monkeypatch.setattr(jp_mhlw, "EN_JP_NAME_MAP", {})
    assert drug_aliases.agency_brand("pembrolizumab", "PMDA") == "キイトルーダ"
    assert drug_aliases.agency_brand("pembrolizumab", "NICE") is None
    s = JpMhlwScraper.__new__(JpMhlwScraper)
    assert s._resolve_jp_name("pembrolizumab") == ["キイトルーダ", "pembrolizumab"]


def test_db_alias_cache_loads_agency_overrides(tmp_path, monkeypatch):
    """_load_db_cache 가 agency_brand_overrides_json 을 함께 싣는다 (실제 DB 의 PMDA 표기가 그대로 쓰인다)."""
    import json
    import sqlite3
    db = tmp_path / "a.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE product_alias_map (product_slug TEXT PRIMARY KEY, inn TEXT, brand_aliases_json TEXT, "
                  "agency_brand_overrides_json TEXT, updated_at TEXT)")
        c.execute("INSERT INTO product_alias_map VALUES (?,?,?,?,?)",
                  ("welireg", "belzutifan", json.dumps(["Welireg", "웰리렉"]), json.dumps({"EMA": "Welireg", "PMDA": "ウェリレグ"}), "x"))
    monkeypatch.setattr(drug_aliases, "_DB_PATH", db)
    monkeypatch.setattr(drug_aliases, "_db_cache", None)
    assert drug_aliases.agency_brand("belzutifan", "PMDA") == "ウェリレグ"
    assert drug_aliases.agency_brand("웰리렉", "PMDA") == "ウェリレグ"
    assert drug_aliases._load_db_cache()["welireg"]["agency_brand_overrides"]["PMDA"] == "ウェリレグ"


# ── fixture xlsx 로 오프라인 검색 ──────────────────────────────────────────────────────────────────────

def _search(tmp_path, query, **kw):
    s = JpMhlwScraper(cache_dir=_cache(tmp_path, **kw))
    return s, asyncio.run(s.search(query, page=None))


def test_pembrolizumab_resolves_to_keytruda_injection_row(tmp_path):
    s, rows = _search(tmp_path, "pembrolizumab")
    assert [(r["product_name"], r["local_price"], r["dosage_form"]) for r in rows] == [("キイトルーダ点滴静注１００ｍｇ", 199462.0, "注射薬")]
    assert rows[0]["ingredient"] == "ペムブロリズマブ（遺伝子組換え）" and rows[0]["dosage_strength"] == "１００ｍｇ４ｍＬ１瓶"
    assert rows[0]["extra"]["company"] == "ＭＳＤ"
    assert s.warnings == []


def test_belzutifan_resolves_to_welireg_oral_row(tmp_path):
    _, rows = _search(tmp_path, "belzutifan")
    assert [(r["product_name"], r["local_price"], r["dosage_form"]) for r in rows] == [("ウェリレグ錠４０ｍｇ", 21916.8, "内用薬")]


def test_letermovir_resolves_to_prevymis_oral_and_injection_rows(tmp_path):
    _, rows = _search(tmp_path, "letermovir")
    got = sorted((r["product_name"], r["local_price"], r["dosage_form"]) for r in rows)
    assert got == sorted([("プレバイミス顆粒分包２０ｍｇ", 3025.6, "内用薬"), ("プレバイミス顆粒分包１２０ｍｇ", 10006.2, "内用薬"),
                          ("プレバイミス錠２４０ｍｇ", 14645.5, "内用薬"), ("プレバイミス点滴静注２４０ｍｇ", 18228.0, "注射薬")])


def test_sitagliptin_routes_to_januvia_not_every_sitagliptin_listing(tmp_path):
    _, rows = _search(tmp_path, "sitagliptin")
    names = {r["product_name"] for r in rows}
    assert names == {"ジャヌビア錠２５ｍｇ", "ジャヌビア錠５０ｍｇ", "ジャヌビア錠１００ｍｇ", "ジャヌビア錠１２．５ｍｇ"}


def test_unknown_inn_returns_empty_not_error(tmp_path):
    _, rows = _search(tmp_path, "zzq-unknown")
    assert rows == []


# ── ④ 카테고리별 최신 공시본만 병합 ──────────────────────────────────────────────────────────────────

def test_only_the_latest_file_per_category_is_merged(tmp_path):
    d = _cache(tmp_path, names=FILES + ["tp20250401-01_01.xlsx", "tp20250401-01_02.xlsx"])
    s = JpMhlwScraper(cache_dir=d)
    latest = s._latest_files_by_category()
    assert {k: v.name for k, v in latest.items()} == {"内用薬": "tp20260318-01_01.xlsx", "注射薬": "tp20260318-01_02.xlsx",
                                                      "外用薬": "tp20260318-01_03.xlsx"}
    rows = asyncio.run(s.search("pembrolizumab", page=None))
    assert len(rows) == 1                                   # 구 공시본의 같은 행이 중복되지 않는다


# ── ⑤ 페이지 탐색: 캐시 파일명 → 페이지, 가장 최신 날짜의 xlsx ─────────────────────────────────────────

HTML = """
<a href="/topics/2025/04/xlsx/tp20250401-01_01.xlsx">内用薬</a> <a href="/topics/2025/04/xlsx/tp20250401-01_02.xlsx">注射薬</a>
<a href="/topics/2026/03/xlsx/tp20260318-01_01.xlsx">内用薬</a> <a href="/topics/2026/03/xlsx/tp20260318-01_02.xlsx">注射薬</a>
<a href="/topics/2026/03/xlsx/tp20260318-01_03.xlsx">外用薬</a> <a href="/topics/2026/03/xlsx/tp20260318-01_04.xlsx">참고</a>
"""


def test_pick_latest_xlsx_urls_by_date_not_order():
    urls = JpMhlwScraper._pick_latest_xlsx_urls(HTML)
    assert urls == {"内用薬": "https://www.mhlw.go.jp/topics/2026/03/xlsx/tp20260318-01_01.xlsx",
                    "注射薬": "https://www.mhlw.go.jp/topics/2026/03/xlsx/tp20260318-01_02.xlsx",
                    "外用薬": "https://www.mhlw.go.jp/topics/2026/03/xlsx/tp20260318-01_03.xlsx"}
    assert JpMhlwScraper._pick_latest_xlsx_urls("<p>no links</p>") == {}


def test_candidate_pages_prefer_cache_derived_then_env_then_constant(tmp_path, monkeypatch):
    s = JpMhlwScraper(cache_dir=_cache(tmp_path))
    monkeypatch.delenv("JP_MHLW_PAGE_URL", raising=False)
    assert s._page_url_from_cache() == "https://www.mhlw.go.jp/topics/2026/03/tp20260318-01.html"
    assert s._candidate_page_urls() == ["https://www.mhlw.go.jp/topics/2026/03/tp20260318-01.html", jp_mhlw.MHLW_PAGE_URL]
    monkeypatch.setenv("JP_MHLW_PAGE_URL", "https://www.mhlw.go.jp/topics/2026/09/tp20260901-01.html")
    assert s._candidate_page_urls()[0] == "https://www.mhlw.go.jp/topics/2026/09/tp20260901-01.html"
    empty = JpMhlwScraper(cache_dir=tmp_path / "none")
    monkeypatch.delenv("JP_MHLW_PAGE_URL", raising=False)
    assert empty._page_url_from_cache() is None and empty._candidate_page_urls() == [jp_mhlw.MHLW_PAGE_URL]


class _Page:
    """page.goto/content 가짜 — URL 별 HTML 또는 예외."""

    def __init__(self, pages: dict):
        self.pages, self.visited, self._html = pages, [], ""

    async def goto(self, url, **kw):
        self.visited.append(url)
        res = self.pages.get(url)
        if isinstance(res, Exception):
            raise res
        self._html = res or ""

    async def content(self):
        return self._html


def test_find_excel_urls_falls_through_candidates_and_picks_newest(tmp_path, monkeypatch):
    s = JpMhlwScraper(cache_dir=_cache(tmp_path))
    monkeypatch.delenv("JP_MHLW_PAGE_URL", raising=False)
    page = _Page({"https://www.mhlw.go.jp/topics/2026/03/tp20260318-01.html": TimeoutError("net::ERR_TIMED_OUT"),
                  jp_mhlw.MHLW_PAGE_URL: HTML})
    urls = asyncio.run(s._find_excel_urls(page))
    assert page.visited == ["https://www.mhlw.go.jp/topics/2026/03/tp20260318-01.html", jp_mhlw.MHLW_PAGE_URL]
    assert urls["注射薬"].endswith("tp20260318-01_02.xlsx")
    with pytest.raises(RuntimeError):
        asyncio.run(s._find_excel_urls(_Page({})))          # 어느 페이지에도 링크가 없으면 실패를 올린다


# ── ⑥ 캐시 신선도: TTL 초과 → 갱신 시도, 불가/실패면 stale_cache 경고 ────────────────────────────────

def test_ttl_is_quarterly():
    """薬価基準 은 4월 전면개정 + 분기 신약 수재(2·5·8·11월) — 90일이면 한 수재 주기 이상 뒤처지지 않는다."""
    assert JpMhlwScraper.XLSX_CACHE_TTL_DAYS == 90


def test_stale_cache_without_page_warns_and_still_answers(tmp_path):
    s, rows = _search(tmp_path, "pembrolizumab", age_days=120)
    assert rows and rows[0]["local_price"] == 199462.0
    assert [w["warning_class"] for w in s.warnings] == ["stale_cache"]
    assert "120일" in s.warnings[0]["message"] and "tp20260318" in s.warnings[0]["message"] and s.warnings[0]["channel"] == "mhlw_xlsx"


def test_stale_cache_refresh_failure_warns_with_the_error(tmp_path, monkeypatch):
    s = JpMhlwScraper(cache_dir=_cache(tmp_path, age_days=120))

    async def _boom(page):
        raise ConnectionError("mhlw.go.jp blocked datacenter IP")
    monkeypatch.setattr(s, "download_all", _boom)
    rows = asyncio.run(s.search("pembrolizumab", page=object()))
    assert rows and rows[0]["local_price"] == 199462.0
    assert [w["warning_class"] for w in s.warnings] == ["stale_cache"] and "ConnectionError" in s.warnings[0]["message"]


def test_stale_cache_successful_refresh_uses_new_file_without_warning(tmp_path, monkeypatch):
    d = _cache(tmp_path, age_days=120)
    s = JpMhlwScraper(cache_dir=d)

    async def _ok(page):
        for n in FILES:
            new = d / n.replace("tp20260318", "tp20260901")
            shutil.copy(d / n, new)
        return {}
    monkeypatch.setattr(s, "download_all", _ok)
    rows = asyncio.run(s.search("pembrolizumab", page=object()))
    assert len(rows) == 1 and s.warnings == []
    assert {v.name for v in s._latest_files_by_category().values()} == {n.replace("tp20260318", "tp20260901") for n in FILES}


def test_fresh_cache_never_tries_to_download(tmp_path, monkeypatch):
    s = JpMhlwScraper(cache_dir=_cache(tmp_path, age_days=10))

    async def _never(page):
        raise AssertionError("신선한 캐시인데 다운로드를 시도했다")
    monkeypatch.setattr(s, "download_all", _never)
    assert asyncio.run(s.search("pembrolizumab", page=object()))


# ── 실제 캐시 파일(레포에 추적됨)로 검증 — 없으면 skip ───────────────────────────────────────────────

@pytest.mark.skipif(not (REAL_CACHE / "tp20260318-01_02.xlsx").exists(), reason="실제 MHLW 캐시 없음")
def test_real_cached_mhlw_xlsx_resolves_inn_queries():
    s = JpMhlwScraper(cache_dir=REAL_CACHE)
    inj = s._parse_excel(REAL_CACHE / "tp20260318-01_02.xlsx", "注射薬")
    hit = s._to_results(s._search_df(inj, "pembrolizumab"))
    assert [(r["product_name"], r["local_price"]) for r in hit] == [("キイトルーダ点滴静注１００ｍｇ", 199462.0)]
    oral = s._parse_excel(REAL_CACHE / "tp20260318-01_01.xlsx", "内用薬")
    assert [(r["product_name"], r["local_price"]) for r in s._to_results(s._search_df(oral, "belzutifan"))] == [("ウェリレグ錠４０ｍｇ", 21916.8)]
    let = s._to_results(s._search_df(oral, "letermovir"))
    assert {r["product_name"] for r in let} == {"プレバイミス顆粒分包２０ｍｇ", "プレバイミス顆粒分包１２０ｍｇ", "プレバイミス錠２４０ｍｇ"}
