"""매일 07:50 스모크가 플랫폼 DB 에 쓰지 않는다 (P1.5 검증 결함 C).

재현됐던 부수효과(GET 라우트): /api/foreign/cached → search_log INSERT, /api/hta/approvals → search_log + data_freshness,
/api/admin/daily-mailing/kanban → 만료 purge DELETE, /api/home/government-keyword-summary → 아카이브가 비면 **동기** 크롤
(competitor_news 쓰기) + 실패 결과(error 키)까지 일자 캐시에 저장(하루 종일 실패가 캐시된다 — 스모크와 무관한 버그).
수정: 스모크는 모든 요청에 X-MA-Smoke: 1 을 붙이고, 서버는 그 요청에서 부수효과만 건너뛴다 — 읽기 판정(상태·JSON)은 그대로.
네트워크·실제 DB 금지: Flask test_client + 임시 DrugPriceDB + 가짜 에이전트.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.server as s
from agents.db import DrugPriceDB
from api.auth import _issue_token
from scripts import smoke

SMOKE = {smoke.SMOKE_HEADER: "1"}


def _admin():
    return {"Authorization": f"Bearer {_issue_token('admin@msd.com', 'admin')}"}


@pytest.fixture
def api_db(monkeypatch, tmp_path) -> Path:
    """server.db 를 스키마만 있는 임시 DB 로 — 라우트가 남기는 행을 여기서 센다."""
    path = tmp_path / "api.db"
    monkeypatch.setattr(s, "db", DrugPriceDB(path))
    return path


def _count(db: Path, sql: str) -> int:
    return sqlite3.connect(db).execute(sql).fetchone()[0]


def test_header_constant_shared_between_smoke_and_server():
    assert smoke.SMOKE_HEADER == s.SMOKE_HEADER == "X-MA-Smoke"


def test_smoke_client_sends_header_on_every_request():
    seen: list[str | None] = []

    def opener(req, timeout=10):
        seen.append(req.get_header(smoke.SMOKE_HEADER.capitalize()) or req.get_header(smoke.SMOKE_HEADER))
        out = io.BytesIO(b"{}")
        out.status = 200
        return out
    smoke.run_smoke("http://test", lambda role: "tok" if role else None, opener=opener)
    assert seen and all(v == "1" for v in seen)


def test_foreign_cached_skips_search_log_for_smoke(api_db, monkeypatch):
    monkeypatch.setattr(s.foreign_agent, "get_cached_results", lambda q: {})
    monkeypatch.setattr(s.foreign_agent, "get_formulation_groups", lambda q, by_country=None: {})
    c = s.app.test_client()
    assert c.get("/api/foreign/cached?q=keytruda", headers=SMOKE).status_code == 200      # 읽기 판정은 그대로
    assert _count(api_db, "SELECT COUNT(*) FROM search_log") == 0
    assert c.get("/api/foreign/cached?q=keytruda").status_code == 200
    assert _count(api_db, "SELECT COUNT(*) FROM search_log") == 1                          # 일반 요청은 기록


def test_hta_indication_matrix_skips_search_log_and_freshness_for_smoke(api_db, monkeypatch):
    """스모크가 치는 /api/hta/approvals 자체는 기록하지 않는다 — 같은 패턴(search_log + data_freshness)을 가진 자매 라우트를 같이 막는다."""
    monkeypatch.setattr(s._hta_agent, "get_indication_matrix",
                        lambda drug, force_refresh=False: {"drug": drug, "indications": [{"code": "1.1"}], "unmatched": {}})
    c = s.app.test_client()
    assert c.get("/api/hta/indication-matrix?drug=pembrolizumab", headers=SMOKE).status_code == 200
    assert _count(api_db, "SELECT COUNT(*) FROM search_log") == 0
    assert _count(api_db, "SELECT COUNT(*) FROM data_freshness") == 0
    assert c.get("/api/hta/indication-matrix?drug=pembrolizumab").status_code == 200
    assert _count(api_db, "SELECT COUNT(*) FROM search_log") == 1
    assert _count(api_db, "SELECT COUNT(*) FROM data_freshness") == 5


def test_daily_mailing_kanban_skips_purge_for_smoke(monkeypatch, tmp_path):
    from agents.daily_mailing import storage
    db = tmp_path / "dm.db"
    # 라우트는 load_admin_kanban 의 기본 db_path(정의 시점에 묶인 실제 DB)를 쓴다 — 임시 DB 로 감싼다
    monkeypatch.setattr(s, "_daily_mailing_admin_kanban",
                        lambda limit_runs=20, purge=True: storage.load_admin_kanban(db, limit_runs=limit_runs, purge=purge))
    purges: list[Path] = []
    monkeypatch.setattr(storage, "purge_expired_daily_mailing_rows", lambda p, **kw: purges.append(Path(p)) or {})
    c = s.app.test_client()
    r = c.get("/api/admin/daily-mailing/kanban", headers={**_admin(), **SMOKE})
    assert r.status_code == 200 and "runs" in r.get_json() and purges == []
    assert c.get("/api/admin/daily-mailing/kanban", headers=_admin()).status_code == 200
    assert purges == [db]


def test_government_summary_route_disables_fetch_for_smoke(monkeypatch):
    import agents.government_keyword_summary as gov
    calls: list[dict] = []

    def fake(refresh=False, allow_fetch=True):
        calls.append({"refresh": refresh, "allow_fetch": allow_fetch})
        return {"markdown": "", "keywords": []}
    monkeypatch.setattr(gov, "get_government_summary", fake)
    c = s.app.test_client()
    assert c.get("/api/home/government-keyword-summary", headers=SMOKE).status_code == 200
    assert c.get("/api/home/government-keyword-summary").status_code == 200
    assert calls == [{"refresh": False, "allow_fetch": False}, {"refresh": False, "allow_fetch": True}]


def _no_llm(monkeypatch, gov):
    monkeypatch.setattr(gov, "_call_openai", lambda prompt, n: (None, []))
    monkeypatch.setattr(gov, "_call_gemini", lambda prompt, n: (None, []))
    monkeypatch.setattr(gov, "_load_env", lambda: None)


def test_government_summary_no_crawl_and_no_live_fallback_when_fetch_disallowed(monkeypatch, tmp_path):
    import agents.government_keyword_summary as gov
    import agents.gov_policy_news as gpn
    monkeypatch.setattr(gov, "CACHE_DIR", tmp_path)
    _no_llm(monkeypatch, gov)
    monkeypatch.setattr(gpn, "list_archive", lambda days=31, limit=200: [])
    monkeypatch.setattr(gpn, "crawl", lambda **kw: (_ for _ in ()).throw(AssertionError("동기 크롤 금지")))
    monkeypatch.setattr(gov, "_collect_news_live", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live 검색 금지")))
    res = gov.get_government_summary(allow_fetch=False)
    assert "error" in res and res["keywords"] == [] and res["sources"] == []
    assert list(tmp_path.glob("*.json")) == []                                  # 실패 결과는 캐시하지 않는다


def test_government_summary_error_result_is_never_cached(monkeypatch, tmp_path):
    """스모크와 무관한 버그: 실패(error 키) 결과가 일자 캐시에 저장돼 그날 내내 빈 위젯이 됐다."""
    import agents.government_keyword_summary as gov
    import agents.gov_policy_news as gpn
    monkeypatch.setattr(gov, "CACHE_DIR", tmp_path)
    _no_llm(monkeypatch, gov)
    archive: list[dict] = []
    monkeypatch.setattr(gpn, "list_archive", lambda days=31, limit=200: list(archive))
    monkeypatch.setattr(gpn, "crawl", lambda **kw: {"total_stored": 0})
    monkeypatch.setattr(gov, "_collect_news_live", lambda *a, **k: [])
    assert "error" in gov.get_government_summary() and list(tmp_path.glob("*.json")) == []
    # 아카이브는 있는데 두 리뷰어 모두 실패 → 'OpenAI / Gemini 모두 응답 실패' 도 캐시하지 않는다
    archive.append({"title": "오늘의 날씨", "naver_link": "u", "url": "u", "source_name": "s", "pub_date": "2026-09-30",
                    "description": "", "brand": ""})        # seed 키워드 폴백에도 안 걸리는 제목 → 키워드 0 → error
    res = gov.get_government_summary()
    assert res.get("error") and list(tmp_path.glob("*.json")) == []
    # 리뷰어가 살아나면 그 결과는 캐시된다 (회귀 방지)
    monkeypatch.setattr(gov, "_call_openai", lambda prompt, n: ("요약", [{"text": "날씨 정책", "articles": [0]}]))
    res = gov.get_government_summary()
    assert "error" not in res and len(list(tmp_path.glob("*.json"))) == 1
