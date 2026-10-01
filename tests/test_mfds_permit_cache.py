"""MFDS 허가 API 캐시 — 실패와 빈 결과를 구분한다 (P1.5 검증에서 드러난 데이터 정합성 결함).

재현됐던 사고: `fetch_raw` 가 전송 실패·JSON 파싱 실패·resultCode≠00 을 모두 `[]` 로 돌려 '품목 없음' 과 구분이 없었고,
`lookup_permit` 이 그 `[]` 에 30일짜리 부정 캐시 sentinel(`__EMPTY__:<이름>`) 을 썼다. 읽기는 ORDER BY 없는 LIMIT 1 이라
sentinel 이 실측 행(키트루다 201501487)을 가릴 수 있었다. 07:50 스모크가 매일 이 경로를 밟으므로 API 장애 한 번이 권위 캐시를 오염시킨다.

네트워크·실제 DB 금지: urllib.request.urlopen 스텁 + tmp_path 캐시 DB (모듈 DB_PATH 를 monkeypatch).
Keytruda baseline(CLAUDE.md): 실측 행 + sentinel 두 개가 공존하는 라이브 DB 와 같은 행을 픽스처로 복제해 검증한다.
라이브 API 검증(네트워크 + MFDS_PATENT_API_KEY)은 여기서 하지 않는다.
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

from agents.scrapers import kr_mfds_permit as permit
from agents.scrapers.mfds_api import MfdsApiError

# ── 라이브 DB 에 실재하는 키트루다 행 (2026-10-01 읽기 전용으로 확인) ──────────────────────────
LONG_NAME = "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)"
SHORT_NAME = "키트루다주"
REAL_SEQ = "201501487"
REAL_FETCHED_AT = "2026-06-24T01:36:45"
LONG_SENTINEL_AT = "2026-10-01T13:00:44"      # 오늘 실패한 실행이 쓴 sentinel — 실측 행보다 '새롭다'
SHORT_SENTINEL_AT = "2026-06-13T07:04:37"

KEYTRUDA_RAW = {
    "ITEM_SEQ": REAL_SEQ,
    "ITEM_NAME": "키트루다주(펨브롤리주맙,유전자재조합)",
    "ITEM_ENG_NAME": "KEYTRUDA",
    "ENTP_NAME": "한국엠에스디(주)",
    "ITEM_PERMIT_DATE": "20150320",
    "CANCEL_NAME": "정상",
    "ETC_OTC_CODE": "전문의약품",
    "ATC_CODE": "L01XC18",
    "EDI_CODE": "655501901",
    "MAIN_ITEM_INGR": "[M261748]펨브롤리주맙",
    "MAIN_INGR_ENG": "Pembrolizumab",
    "PACK_UNIT": "1바이알/상자[4.0ml/바이알],2바이알/상자[4.0ml/바이알]",
    "UD_DOC_DATA": "<DOC><SECTION title=\"\"><ARTICLE title=\"용법용량\"><PARAGRAPH>1회 200mg 을 3주 간격 정맥점적</PARAGRAPH></ARTICLE></SECTION></DOC>",
}


# ── 스텁 ────────────────────────────────────────────────────────────────────────────────────
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
    """responder(url) -> bytes | Exception. 호출된 URL 을 기록해 돌려준다."""
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
    body = {"pageNo": 1, "totalCount": len(items), "numOfRows": 10}
    if items:
        body["items"] = items
    return json.dumps({"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."}, "body": body},
                      ensure_ascii=False).encode("utf-8")


def _code(code: str, msg: str) -> bytes:
    return json.dumps({"header": {"resultCode": code, "resultMsg": msg}, "body": {}}).encode("utf-8")


GATEWAY_XML = (b"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
               b"<returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg>"
               b"<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>")


# ── 픽스처 ───────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def permit_db(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "permit_cache.db"
    monkeypatch.setattr(permit, "DB_PATH", path)
    monkeypatch.setenv("MFDS_PATENT_API_KEY", "test-key")
    permit._ensure_table()
    return path


_COLS = ("item_seq, item_name, edi_code, permit_date, cancel_status, etc_otc, atc_code, main_ingr, ingr_name, "
         "storage_method, valid_term, pack_unit, rare_drug, reexam_target, reexam_date, fetched_at, raw_json")


def _insert(db: Path, item_seq: str, item_name: str, fetched_at: str, raw: dict) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            f"INSERT INTO mfds_permit_cache ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item_seq, item_name, raw.get("EDI_CODE"), raw.get("ITEM_PERMIT_DATE"), raw.get("CANCEL_NAME"),
             raw.get("ETC_OTC_CODE"), raw.get("ATC_CODE"), raw.get("MAIN_ITEM_INGR"), None, None, None,
             raw.get("PACK_UNIT"), None, None, None, fetched_at, json.dumps(raw, ensure_ascii=False)),
        )


def _seed_live_keytruda(db: Path, *, real_at: str = REAL_FETCHED_AT, long_sentinel_at: str = LONG_SENTINEL_AT,
                        short_sentinel_at: str = SHORT_SENTINEL_AT) -> None:
    """라이브 DB 와 같은 세 행. sentinel 을 먼저 넣어 삽입 순서가 읽기를 돕지 못하게 한다."""
    _insert(db, f"__EMPTY__:{LONG_NAME}", LONG_NAME, long_sentinel_at, {})
    _insert(db, REAL_SEQ, LONG_NAME, real_at, KEYTRUDA_RAW)
    _insert(db, f"__EMPTY__:{SHORT_NAME}", SHORT_NAME, short_sentinel_at, {})


def _rows(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return sorted(conn.execute("SELECT item_seq, item_name, fetched_at, raw_json FROM mfds_permit_cache").fetchall())


def _iso(delta: timedelta) -> str:
    return (datetime.now() - delta).isoformat(timespec="seconds")


# ── 1. fetch_raw: '답' 과 '실패' 를 구분한다 ─────────────────────────────────────────────────
def test_fetch_raw_returns_items_on_normal_response(permit_db, monkeypatch):
    calls = _stub_urlopen(monkeypatch, lambda url: _ok([KEYTRUDA_RAW]))
    items = permit.fetch_raw(item_name="키트루다주")
    assert [i["ITEM_SEQ"] for i in items] == [REAL_SEQ]
    assert len(calls) == 1 and "serviceKey=test-key" in calls[0]


def test_fetch_raw_zero_items_is_a_genuine_empty_answer(permit_db, monkeypatch):
    _stub_urlopen(monkeypatch, lambda url: _ok([]))
    assert permit.fetch_raw(item_name="없는약정") == []


def test_fetch_raw_nodata_result_code_03_is_a_genuine_empty_answer(permit_db, monkeypatch):
    """공공데이터포털 표준 코드 03 = NODATA_ERROR — 서비스 장애가 아니라 '데이터 없음' 이라는 답이다."""
    _stub_urlopen(monkeypatch, lambda url: _code("03", "NODATA_ERROR"))
    assert permit.fetch_raw(item_name="없는약정") == []


@pytest.mark.parametrize("responder, kind", [
    (lambda url: urllib.error.URLError("nodename nor servname provided, or not known"), "transport"),
    (lambda url: TimeoutError("timed out"), "transport"),
    (lambda url: b"<html><body>502 Bad Gateway</body></html>", "parse"),
    (lambda url: b'{"body": {"items": []}}', "parse"),                  # header 없음 — '답' 으로 인정하지 않는다
    (lambda url: _code("99", "UNKNOWN_ERROR"), "result_code"),
    (lambda url: _code("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"), "result_code"),
])
def test_fetch_raw_raises_on_every_failure_mode(permit_db, monkeypatch, responder, kind):
    _stub_urlopen(monkeypatch, responder)
    with pytest.raises(MfdsApiError) as ei:
        permit.fetch_raw(item_name="키트루다주")
    assert ei.value.kind == kind
    assert isinstance(ei.value, RuntimeError)      # 기존 `except Exception`/`RuntimeError` 호출자와 호환


def test_fetch_raw_gateway_xml_envelope_reports_portal_reason_code(permit_db, monkeypatch):
    """data.go.kr 게이트웨이는 쿼터 초과·키 오류를 type=json 에도 XML 로 돌려준다 — 사유 코드를 살려서 올린다."""
    _stub_urlopen(monkeypatch, lambda url: GATEWAY_XML)
    with pytest.raises(MfdsApiError) as ei:
        permit.fetch_raw(item_name="키트루다주")
    assert ei.value.kind == "gateway" and ei.value.code == "22"
    assert "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR" in str(ei.value)


# ── 2. 읽기: 실측 행 > sentinel, 같은 급이면 최신 ──────────────────────────────────────────
def test_cache_read_prefers_real_keytruda_row_over_newer_sentinel(permit_db):
    """라이브 DB 와 같은 행: 실측 201501487(06-24) 과 오늘(10-01) 쓰인 sentinel 이 같은 이름으로 공존한다."""
    _seed_live_keytruda(permit_db)
    row = permit._cache_read_by_name(LONG_NAME)
    assert row is not None and row.kind == "item"
    assert row.raw["ITEM_SEQ"] == REAL_SEQ
    assert row.fetched_at == datetime(2026, 6, 24, 1, 36, 45)
    # 결정적이어야 한다 — SQLite 가 어느 행을 먼저 주든 상관없이
    assert all(permit._cache_read_by_name(LONG_NAME).raw["ITEM_SEQ"] == REAL_SEQ for _ in range(25))


def test_cache_read_legacy_empty_sentinel_is_not_a_negative_answer(permit_db):
    """구 포맷 sentinel(raw_json='{}') 은 실패와 빈 결과를 구분할 수 없으므로 부정 캐시로 인정하지 않는다."""
    _seed_live_keytruda(permit_db)
    row = permit._cache_read_by_name(SHORT_NAME)
    assert row is not None and row.kind == "legacy_empty"
    assert permit._cache_read_by_name("아예없는이름") is None


def test_cache_read_newest_wins_among_real_rows(permit_db):
    older = {**KEYTRUDA_RAW, "ITEM_SEQ": "199900001"}
    _insert(permit_db, "199900001", LONG_NAME, "2026-01-01T00:00:00", older)
    _insert(permit_db, REAL_SEQ, LONG_NAME, REAL_FETCHED_AT, KEYTRUDA_RAW)
    assert permit._cache_read_by_name(LONG_NAME).raw["ITEM_SEQ"] == REAL_SEQ


# ── 3. lookup_permit — Keytruda baseline ────────────────────────────────────────────────────
def test_keytruda_fresh_real_row_wins_without_touching_network(permit_db, monkeypatch):
    _seed_live_keytruda(permit_db, real_at=_iso(timedelta(days=1)), long_sentinel_at=_iso(timedelta(0)))
    calls = _stub_urlopen(monkeypatch, lambda url: AssertionError("네트워크를 건드리면 안 된다"))
    res = permit.lookup_permit(LONG_NAME, ingredient="펨브롤리주맙", edi_code="655501901")
    assert res["source"] == "cache"
    assert res["item_seq"] == REAL_SEQ and res["permit_date"] == "2015.03.20" and res["atc_code"] == "L01XC18"
    assert calls == []


def test_keytruda_stale_real_row_survives_api_failure_and_cache_is_untouched(permit_db, monkeypatch):
    """라이브 상태 그대로: 실측 행은 99일 지나 stale, sentinel 은 오늘. API 가 죽어도 sentinel 을 새로 쓰지 않고
    마지막 실측을 cache_stale 로 돌려준다 — 허가일은 바뀌지 않는 권위 데이터라 추정값보다 낫다."""
    _seed_live_keytruda(permit_db, real_at=_iso(timedelta(days=99)), long_sentinel_at=_iso(timedelta(0)),
                        short_sentinel_at=_iso(timedelta(days=110)))
    before = _rows(permit_db)
    _stub_urlopen(monkeypatch, lambda url: urllib.error.URLError("hotspot down"))
    res = permit.lookup_permit(LONG_NAME, ingredient="펨브롤리주맙", edi_code="655501901")
    assert res["source"] == "cache_stale"
    assert res["item_seq"] == REAL_SEQ and res["permit_date"] == "2015.03.20"
    assert res["error_kind"] == "transport" and "hotspot down" in res["error"]
    assert _rows(permit_db) == before


@pytest.mark.parametrize("responder, kind", [
    (lambda url: urllib.error.URLError("hotspot down"), "transport"),
    (lambda url: b"<html>502</html>", "parse"),
    (lambda url: _code("99", "UNKNOWN_ERROR"), "result_code"),
    (lambda url: GATEWAY_XML, "gateway"),
])
def test_api_failure_without_cache_returns_error_and_writes_nothing(permit_db, monkeypatch, responder, kind):
    _stub_urlopen(monkeypatch, responder)
    res = permit.lookup_permit("신약정100밀리그램", ingredient="신성분", edi_code="123456789")
    assert res["source"] == "error" and res["error_kind"] == kind and res["error"]
    assert "permit_date" not in res
    assert _rows(permit_db) == []


def test_first_failure_aborts_the_variant_chain(permit_db, monkeypatch):
    """전송 실패는 변형 6단계를 더 돌려도 똑같이 실패한다 — 첫 실패에서 끝내 스모크 타임아웃을 지킨다."""
    calls = _stub_urlopen(monkeypatch, lambda url: urllib.error.URLError("down"))
    res = permit.lookup_permit("신약정100밀리그램", ingredient="신성분", edi_code="123456789")
    assert res["source"] == "error" and len(calls) == 1


def test_genuine_zero_result_writes_negative_sentinel_and_is_honored(permit_db, monkeypatch):
    calls = _stub_urlopen(monkeypatch, lambda url: _ok([]))
    res1 = permit.lookup_permit("없는약정100밀리그램", ingredient="없는성분", edi_code="000000000")
    assert res1["source"] == "miss" and res1.get("cached") is not True
    rows = _rows(permit_db)
    assert len(rows) == 1 and rows[0][0] == "__EMPTY__:없는약정100밀리그램"
    marker = json.loads(rows[0][3])
    assert marker["negative"] is True
    assert "edi:000000000" in marker["tried"] and "없는약정100밀리그램" in marker["tried"]
    n = len(calls)
    assert n >= 2                                          # EDI + 변형들을 실제로 다 시도했다
    res2 = permit.lookup_permit("없는약정100밀리그램", ingredient="없는성분", edi_code="000000000")
    assert res2["source"] == "miss" and res2["cached"] is True
    assert len(calls) == n                                 # 부정 캐시가 API 호출을 막았다


def test_nodata_03_is_cached_as_negative_too(permit_db, monkeypatch):
    _stub_urlopen(monkeypatch, lambda url: _code("03", "NODATA_ERROR"))
    res = permit.lookup_permit("없는약정100밀리그램")
    assert res["source"] == "miss"
    assert _rows(permit_db)[0][0] == "__EMPTY__:없는약정100밀리그램"


def test_negative_sentinel_expires_after_its_ttl(permit_db, monkeypatch):
    _insert(permit_db, "__EMPTY__:없는약정", "없는약정", _iso(timedelta(days=permit.NEGATIVE_CACHE_TTL_DAYS + 1)),
            {"negative": True, "tried": ["없는약정"]})
    calls = _stub_urlopen(monkeypatch, lambda url: _ok([]))
    assert permit.lookup_permit("없는약정")["source"] == "miss"
    assert calls                                           # TTL 만료 → 재조회


def test_legacy_empty_sentinel_is_ignored_and_replaced_by_real_data(permit_db, monkeypatch):
    _insert(permit_db, "__EMPTY__:구약정", "구약정", _iso(timedelta(0)), {})
    calls = _stub_urlopen(monkeypatch, lambda url: _ok([KEYTRUDA_RAW]))
    res = permit.lookup_permit("구약정")
    assert res["source"] == "mfds_api" and res["item_seq"] == REAL_SEQ and calls
    assert permit._cache_read_by_name("구약정").kind == "item"


def test_success_prefers_cancel_status_normal_and_writes_real_row(permit_db, monkeypatch):
    cancelled = {**KEYTRUDA_RAW, "ITEM_SEQ": "200000001", "CANCEL_NAME": "취하"}
    _stub_urlopen(monkeypatch, lambda url: _ok([cancelled, KEYTRUDA_RAW]))
    res = permit.lookup_permit(LONG_NAME, edi_code="655501901")
    assert res["source"] == "mfds_api" and res["item_seq"] == REAL_SEQ
    assert [r[0] for r in _rows(permit_db)] == [REAL_SEQ]


# ── 4. 호출자가 쓰는 분류 헬퍼 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("res, expected", [
    (None, False), ({}, False),
    ({"source": "miss"}, False), ({"source": "error", "error": "x"}, False),
    ({"source": "mfds_api"}, True), ({"source": "cache"}, True), ({"source": "cache_stale"}, True),
])
def test_has_permit_data(res, expected):
    assert permit.has_permit_data(res) is expected


@pytest.mark.parametrize("res, expected", [
    ({"source": "mfds_api"}, "ok"), ({"source": "cache"}, "ok"), ({"source": "cache_stale"}, "stale"),
    ({"source": "miss"}, "miss"), ({"source": "error"}, "error"), (None, "error"),
])
def test_permit_status(res, expected):
    assert permit.permit_status(res) == expected
