"""Home 미디어 카드 캐시 오염 — Naver 요청 실패를 '기사 0건' 과 구분한다 (2026-10-01 재현).

재현: 소켓이 막힌 스모크가 하루 첫 호출 → naver_news.search 가 실패를 [] 로 삼킴 → 12개 브랜드 전부 total_count 0 인
집계가 error 키 없이 오늘자 캐시 파일에 기록 → 자정까지 200 OK 로 서빙. tests/fixtures/…poisoned.json 이 그 파일이다.

규칙 (mfds_api 선례와 같다): 빈 결과는 API 가 답했을 때만. 실패는 raise(strict) 로 구분하고, 실패가 섞인 집계는 error 키를
달고 캐시에 쓰지 않는다. 읽기 쪽도 그 모양(error 키 또는 전 브랜드 0건)의 파일은 서빙하지 않고 재수집한다.
네트워크 금지: requests.get 을 가짜로 바꾼다.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.editable_factors as ef  # noqa: E402
import agents.media_intelligence as mi  # noqa: E402
from agents import naver_news  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "brand_traffic_2026-10-01_14d_60e3dc7a.poisoned.json"
BRANDS_12 = ["키트루다", "렌비마", "자누비아", "가다실", "프로리아", "옵디보",
             "타그리소", "임핀지", "테쎈트릭", "레블리미드", "다잘렉스", "린파자"]


def _groups():
    return [{"brand": b, "terms": []} for b in BRANDS_12]


def _blocked_get(*a, **kw):
    raise requests.ConnectionError("network blocked by test guard")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(naver_news.requests, "get", _blocked_get)
    c = naver_news.NaverNewsClient(client_id="id", client_secret="secret", min_delay_ms=0)
    monkeypatch.setattr(naver_news, "get_client", lambda: c)
    monkeypatch.setattr(mi, "get_client", lambda: c)        # media_intelligence 는 import 시점에 바인딩
    return c


# ── 1. 클라이언트: 실패는 raise, 0건은 답이 있을 때만 ────────────────────────

def test_fixture_documents_the_defect_shape():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert "error" not in data
    assert len(data["brands"]) == 12 and all(b["total_count"] == 0 for b in data["brands"])
    assert mi.looks_poisoned(data) is True


def test_search_strict_raises_on_request_failure(client):
    with pytest.raises(naver_news.NaverApiError) as ei:
        client.search("키트루다", strict=True)
    assert ei.value.kind == "transport"


def test_search_non_strict_keeps_returning_empty_but_marks_last_error(client):
    """기존 호출자(Daily Mailing·CompetitorTrends 등)는 [] 를 계속 받는다 — 대신 last_error 로 구분 가능."""
    assert client.search("키트루다") == []
    assert isinstance(client.last_error, naver_news.NaverApiError)


def test_search_strict_raises_when_not_configured(monkeypatch):
    c = naver_news.NaverNewsClient(client_id="", client_secret="")
    with pytest.raises(naver_news.NaverApiError) as ei:
        c.search("키트루다", strict=True)
    assert ei.value.kind == "not_configured"


def test_search_strict_raises_on_non_json_body(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("No JSON")

    monkeypatch.setattr(naver_news.requests, "get", lambda *a, **kw: _Resp())
    c = naver_news.NaverNewsClient(client_id="id", client_secret="secret", min_delay_ms=0)
    with pytest.raises(naver_news.NaverApiError) as ei:
        c.search("키트루다", strict=True)
    assert ei.value.kind == "parse"
    assert c.search("키트루다") == []


def test_genuine_empty_answer_is_not_an_error(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"items": []}

    monkeypatch.setattr(naver_news.requests, "get", lambda *a, **kw: _Resp())
    c = naver_news.NaverNewsClient(client_id="id", client_secret="secret", min_delay_ms=0)
    assert c.search("키트루다", strict=True) == [] and c.last_error is None
    counts, items = c.daily_counts("키트루다", days=14, strict=True)
    assert counts == {} and items == []


def test_daily_counts_strict_propagates(client):
    with pytest.raises(naver_news.NaverApiError):
        client.daily_counts("키트루다", days=14, strict=True)


# ── 2. 집계: 실패한 브랜드는 error 키, 성공 브랜드는 포맷 불변 ─────────────────

def test_aggregate_marks_failed_brand_and_keeps_others(monkeypatch):
    today = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

    class _Client:
        def daily_counts(self, query, days=30, max_pages=10, strict=False):
            if query == "렌비마":
                raise naver_news.NaverApiError("transport", "ConnectionError: boom")
            it = naver_news.NewsItem(title="t", link="https://ex.com/a", original_link="https://ex.com/a",
                                     description="d", pub_date=today)
            return {it.date_str: 1}, [it]

    monkeypatch.setattr(naver_news, "get_client", lambda: _Client())
    out = naver_news.aggregate_brand_traffic(["키트루다", "렌비마"], days=7)
    by = {b["brand"]: b for b in out}
    assert "error" not in by["키트루다"] and by["키트루다"]["total_count"] == 1
    assert by["렌비마"]["total_count"] == 0 and by["렌비마"]["error"].startswith("transport")
    assert set(by["키트루다"].keys()) == {"brand", "total_count", "daily", "sparkline", "latest_news"}


# ── 3. get_brand_traffic: 실패 결과는 error 키 + 캐시 미기록 ───────────────────

def test_failed_fetch_returns_error_and_writes_no_cache(client, monkeypatch, tmp_path):
    monkeypatch.setattr(mi, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ef, "get_home_brand_groups", _groups)
    out = mi.get_brand_traffic()
    assert out["error"] and sorted(out["failed_brands"]) == sorted(BRANDS_12)
    assert len(out["brands"]) == 12 and all(b["total_count"] == 0 for b in out["brands"])
    assert list(tmp_path.glob("brand_traffic_*.json")) == []        # 하루 종일 가는 캐시에 실패를 쓰지 않는다


def test_partial_failure_is_flagged_and_not_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(mi, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ef, "get_home_brand_groups", lambda: [{"brand": "키트루다", "terms": []},
                                                              {"brand": "렌비마", "terms": []}])
    monkeypatch.setattr(mi, "get_client", lambda: type("C", (), {"is_configured": True})())
    monkeypatch.setattr(mi, "aggregate_brand_traffic", lambda groups, days: [
        {"brand": "키트루다", "total_count": 3, "daily": {}, "sparkline": [0] * days, "latest_news": []},
        {"brand": "렌비마", "total_count": 0, "daily": {}, "sparkline": [0] * days, "latest_news": [],
         "error": "transport: boom"},
    ])
    out = mi.get_brand_traffic()
    assert out["failed_brands"] == ["렌비마"] and "렌비마" in out["error"]
    assert out["brands"][0]["total_count"] == 3                       # 살아남은 데이터는 그대로 준다
    assert list(tmp_path.glob("brand_traffic_*.json")) == []


def test_all_zero_without_error_is_not_cached(monkeypatch, tmp_path):
    """읽기 쪽이 서빙을 거부하는 모양을 쓰기 쪽도 만들지 않는다."""
    monkeypatch.setattr(mi, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ef, "get_home_brand_groups", _groups)
    monkeypatch.setattr(mi, "get_client", lambda: type("C", (), {"is_configured": True})())
    monkeypatch.setattr(mi, "aggregate_brand_traffic", lambda groups, days: [
        {"brand": g["brand"], "total_count": 0, "daily": {}, "sparkline": [0] * days, "latest_news": []}
        for g in groups])
    out = mi.get_brand_traffic()
    assert "error" not in out and len(out["brands"]) == 12
    assert list(tmp_path.glob("brand_traffic_*.json")) == []


def test_poisoned_cache_file_is_not_served(monkeypatch, tmp_path):
    """2026-10-01 파일 그대로 — 오늘 이름으로 놓여 있어도 서빙하지 않고 재수집한다 (배포 직후 잔존 파일 대비)."""
    monkeypatch.setattr(mi, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ef, "get_home_brand_groups", _groups)
    monkeypatch.setattr(mi, "get_client", lambda: type("C", (), {"is_configured": True})())
    target = mi.cache_file_for(_groups(), 14)
    shutil.copy(FIXTURE, target)
    calls: list[int] = []

    def good(groups, days):
        calls.append(days)
        return [{"brand": g["brand"], "total_count": 5, "daily": {}, "sparkline": [1] * days, "latest_news": []}
                for g in groups]

    monkeypatch.setattr(mi, "aggregate_brand_traffic", good)
    out = mi.get_brand_traffic()
    assert calls == [14] and "error" not in out and out["brands"][0]["total_count"] == 5
    assert json.loads(target.read_text(encoding="utf-8"))["brands"][0]["total_count"] == 5   # 좋은 결과로 교체
    # 좋은 캐시는 재사용된다 (회귀 방지)
    calls.clear()
    assert mi.get_brand_traffic()["brands"][0]["total_count"] == 5 and calls == []


def test_cached_error_result_is_not_served(monkeypatch, tmp_path):
    monkeypatch.setattr(mi, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ef, "get_home_brand_groups", _groups)
    monkeypatch.setattr(mi, "get_client", lambda: type("C", (), {"is_configured": True})())
    target = mi.cache_file_for(_groups(), 14)
    target.write_text(json.dumps({"updated_at": "x", "days": 14, "brands": [], "error": "old"}), encoding="utf-8")
    monkeypatch.setattr(mi, "aggregate_brand_traffic", lambda groups, days: [
        {"brand": g["brand"], "total_count": 2, "daily": {}, "sparkline": [1] * days, "latest_news": []}
        for g in groups])
    assert mi.get_brand_traffic()["brands"][0]["total_count"] == 2
