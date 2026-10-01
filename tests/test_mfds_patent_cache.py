"""MFDS 특허 API 캐시 — 허가 모듈과 같은 결함(실패=빈 결과 → 90일 sentinel) 을 같은 방식으로 고친다.

네트워크·실제 DB 금지: urllib.request.urlopen 스텁 + tmp_path 캐시 DB.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.scrapers import kr_mfds_patent as patent
from agents.scrapers.mfds_api import MfdsApiError

NAME = "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)"
CORE_PATENT_RAW = {
    "ITEM_SEQ": "201501487",
    "ITEM_NAME": "키트루다주(펨브롤리주맙,유전자재조합)",
    "INGR_NAME": "펨브롤리주맙",
    "PAGE_GB_NM": "제품특허",
    "PATENT_GB_CODE": "물질",
    "DOMESTIC_PATENT_NO": "10-2861021",
    "DOMESTIC_PATENT_STATUS": "등록",
    "DOMESTIC_END_DATE": "2035-08-17",
    "DOMESTIC_INVN_NM": "인간 PD-1 항체",
    "PATENTEE": "머크 샤프 앤드 돔 엘엘씨",
}


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub_urlopen(monkeypatch, responder):
    calls: list[str] = []

    def fake(url, timeout=None):
        calls.append(url)
        out = responder(url)
        if isinstance(out, BaseException):
            raise out
        return _Resp(out)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


def _ok(items: list[dict]) -> bytes:
    body = {"pageNo": 1, "totalCount": len(items), "numOfRows": 50}
    if items:
        body["items"] = items
    return json.dumps({"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."}, "body": body},
                      ensure_ascii=False).encode("utf-8")


def _code(code: str, msg: str) -> bytes:
    return json.dumps({"header": {"resultCode": code, "resultMsg": msg}, "body": {}}).encode("utf-8")


@pytest.fixture
def patent_db(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "patent_cache.db"
    monkeypatch.setattr(patent, "DB_PATH", path)
    monkeypatch.setenv("MFDS_PATENT_API_KEY", "test-key")
    patent._ensure_table()
    return path


def _insert(db: Path, item_name: str, patent_no: str | None, fetched_at: str, raw: dict, page_gb: str = "제품특허") -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO mfds_patent_cache (item_seq, item_name, ingredient, page_gb_nm, patent_gb_code, patent_no, "
            "patent_status, patent_end_date, invn_name, patentee, fetched_at, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (raw.get("ITEM_SEQ"), item_name, raw.get("INGR_NAME"), page_gb, raw.get("PATENT_GB_CODE"), patent_no,
             raw.get("DOMESTIC_PATENT_STATUS"), raw.get("DOMESTIC_END_DATE"), raw.get("DOMESTIC_INVN_NM"),
             raw.get("PATENTEE"), fetched_at, json.dumps(raw, ensure_ascii=False)),
        )


def _rows(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return sorted(conn.execute("SELECT item_name, patent_no, fetched_at, raw_json FROM mfds_patent_cache").fetchall(),
                      key=lambda r: (r[0], r[1] or "", r[2]))


def _iso(delta: timedelta) -> str:
    return (datetime.now() - delta).isoformat(timespec="seconds")


@pytest.mark.parametrize("responder, kind", [
    (lambda url: urllib.error.URLError("down"), "transport"),
    (lambda url: b"<html>502</html>", "parse"),
    (lambda url: _code("99", "UNKNOWN_ERROR"), "result_code"),
])
def test_fetch_raw_raises_on_failure(patent_db, monkeypatch, responder, kind):
    _stub_urlopen(monkeypatch, responder)
    with pytest.raises(MfdsApiError) as ei:
        patent.fetch_raw(item_name="키트루다주")
    assert ei.value.kind == kind


def test_fetch_raw_zero_and_nodata_are_genuine_empty(patent_db, monkeypatch):
    _stub_urlopen(monkeypatch, lambda url: _ok([]))
    assert patent.fetch_raw(item_name="없는약") == []
    _stub_urlopen(monkeypatch, lambda url: _code("03", "NODATA_ERROR"))
    assert patent.fetch_raw(item_name="없는약") == []


def test_lookup_failure_writes_no_sentinel_and_reports_error(patent_db, monkeypatch):
    _stub_urlopen(monkeypatch, lambda url: urllib.error.URLError("down"))
    res = patent.lookup_patent("신약정100밀리그램", ingredient="신성분")
    assert res["source"] == "error" and res["error_kind"] == "transport" and res["error"]
    assert res["status"] == "unknown" and res["judgment_basis"] == "api_error"
    assert _rows(patent_db) == []


def test_lookup_stale_real_rows_are_returned_on_failure_and_left_untouched(patent_db, monkeypatch):
    _insert(patent_db, NAME, "10-2861021", _iso(timedelta(days=100)), CORE_PATENT_RAW)
    _insert(patent_db, NAME, "__EMPTY__", _iso(timedelta(0)), {}, page_gb="")      # 오늘 실패가 남긴 구 sentinel
    before = _rows(patent_db)
    _stub_urlopen(monkeypatch, lambda url: urllib.error.URLError("down"))
    res = patent.lookup_patent(NAME, ingredient="펨브롤리주맙")
    assert res["source"] == "cache_stale" and res["status"] == "유효" and res["expiry_date"] == "2035-08-17"
    assert res["error_kind"] == "transport"
    assert _rows(patent_db) == before


def test_lookup_fresh_real_rows_ignore_newer_sentinel_and_skip_network(patent_db, monkeypatch):
    _insert(patent_db, NAME, "10-2861021", _iso(timedelta(days=1)), CORE_PATENT_RAW)
    _insert(patent_db, NAME, "__EMPTY__", _iso(timedelta(0)), {}, page_gb="")
    calls = _stub_urlopen(monkeypatch, lambda url: AssertionError("no network"))
    res = patent.lookup_patent(NAME)
    assert res["source"] == "cache" and res["status"] == "유효" and calls == []


def test_lookup_genuine_zero_writes_negative_sentinel_and_is_honored(patent_db, monkeypatch):
    calls = _stub_urlopen(monkeypatch, lambda url: _ok([]))
    res1 = patent.lookup_patent("없는약정100밀리그램", ingredient="없는성분")
    assert res1["source"] == "api" and res1["status"] == "unknown"
    rows = _rows(patent_db)
    assert len(rows) == 1 and rows[0][1] == "__EMPTY__" and json.loads(rows[0][3])["negative"] is True
    n = len(calls)
    res2 = patent.lookup_patent("없는약정100밀리그램", ingredient="없는성분")
    assert res2["source"] == "cache" and res2["status"] == "unknown" and len(calls) == n


def test_legacy_empty_sentinel_is_not_honored(patent_db, monkeypatch):
    _insert(patent_db, "구약정", "__EMPTY__", _iso(timedelta(0)), {}, page_gb="")
    calls = _stub_urlopen(monkeypatch, lambda url: _ok([CORE_PATENT_RAW]))
    res = patent.lookup_patent("구약정")
    assert res["source"] == "api" and res["status"] == "유효" and calls
    # 실측 적재가 구 sentinel 을 치웠다 (같은 item_name 의 기존 행 DELETE 후 적재)
    assert all(r[1] != "__EMPTY__" for r in _rows(patent_db))
