"""Stakeholder API Blueprint (T6) — 인증 행렬 · 접근 등급(발췌 ≤600자, 전문/원본 admin) · 사진 WEBP 정규화 ·
경로 탈출 방어 · 응답 모양(T7a 프론트 계약) · admin 운영 · agent 채널.

Blueprint 는 독립 Flask() 에 등록한다(api.server import 는 무겁다 — 등록 확인 1건만 예외). DB·볼륨 root 는 tmp_path.
T5(sync) 가 병렬 구현 중이라 픽스처 적재는 store/canonical_parse 로 직접 한다(T5 의 import_snapshot 과 같은 결과 모양).
네트워크·LLM 없음.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import types
from datetime import date
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from agents.stakeholder import canonical_parse, photos, store
from api.auth import _issue_token
from api import stakeholder_routes as sr
from api.stakeholder_routes import build_stakeholder_blueprint

FIX = Path(__file__).parent / "fixtures" / "stakeholder_src_mini"
TIMELINE = "2026-10-08_주제별_인물발언_타임라인_정본.md"
HONG_SRC = "SRC-FAKE-20260903-HONG"
FORBIDDEN_USER_KEYS = {"photo_url", "local_path", "text_path", "photo_path"}
COPYRIGHT = "원문 저작권은 각 발행기관·언론사에 있으며 내부 검증·provenance 보존 목적으로만 보관한다. (합성)"


def H(role: str, email: str | None = None) -> dict:
    return {"Authorization": f"Bearer {_issue_token(email or f'{role}@x.test', role)}"}


# ── 픽스처 적재 (T5 import_snapshot 과 같은 결과) ───────────────────────────────────

def _seed(root: Path, db: Path, *, topics_meta: bool = True, md_path_meta: bool = True) -> None:
    shutil.copytree(FIX, root)
    m = json.loads((root / "source_manifest.json").read_text(encoding="utf-8"))
    for s in m["sources"]:
        store.upsert_source(s, integrity_status="verified" if s.get("text_path") else "no_local", db_path=db)
    for a in m["artifacts"]:
        store.upsert_artifact(a, integrity_status="verified", db_path=db)
    store.upsert_artifact({"path": "relationship.svg", "name": "relationship_map_svg", "sha256": "0" * 64,
                           "bytes": 10}, integrity_status="verified", db_path=db)
    store.set_meta("copyright_notice", COPYRIGHT, db_path=db)
    store.set_meta("scope_start_kst", m["scope_start_kst"], db_path=db)
    store.set_meta("scope_end_kst", m["scope_end_kst"], db_path=db)
    store.set_meta("last_sync_at", "2026-10-12T08:20:00+09:00", db_path=db)
    store.set_meta("integrity_summary_json", {"sources": {"verified": 2, "mismatch": 0, "missing": 0, "no_local": 0,
                                                          "unchecked": 0}, "artifacts": {}, "problems": [],
                                              "checked_at": "2026-10-12T08:20:00+09:00"}, db_path=db)
    for p in json.loads((root / "data/people_registry.json").read_text(encoding="utf-8"))["people"]:
        store.insert_person_if_absent(p, origin="import", db_path=db)
    for s in json.loads((root / "data/source_registry.json").read_text(encoding="utf-8"))["sources"]:
        store.insert_source_domain_if_absent(s, origin="import", db_path=db)
    for p in json.loads((root / "data/audit_proposals.json").read_text(encoding="utf-8"))["proposals"]:
        store.insert_proposal_if_absent(p, origin="import", db_path=db)
    for d in sorted((root / "monitoring-runs").iterdir()):
        store.upsert_monitoring_run((d / "daily_monitoring.json").read_bytes(),
                                    md_path=f"monitoring-runs/{d.name}/daily_monitoring.md", db_path=db)
    for d in sorted((root / "audit-runs").iterdir()):
        store.upsert_audit_run((d / "coverage_audit.json").read_bytes(),
                               md_path=f"audit-runs/{d.name}/coverage_audit.md", db_path=db)
    store.set_person_importance("홍길동", 5, {"총점": 95}, db_path=db)
    res = canonical_parse.parse_timeline(root / TIMELINE, root=root)
    if topics_meta:      # T5 와 같은 키: canonical_stats_json.topics = parser meta.topics
        store.set_meta("canonical_stats_json", {"events": 2, "topics": res.meta["topics"]}, db_path=db)
    if md_path_meta:
        store.set_meta("canonical_timeline_path", TIMELINE, db_path=db)
    people = {p["name"]: p["person_id"] for p in store.list_people(db_path=db)}
    by_text = {s["text_path"]: s["source_id"] for s in store.list_sources(db_path=db) if s.get("text_path")}
    sts = []
    for st in res.statements:
        d = st.__dict__.copy()
        d["person_id"] = people.get(st.speaker_name)
        d["source_ids"] = list(dict.fromkeys(by_text[lc["path"]] for lc in st.locators if lc["path"] in by_text))
        sts.append(d)
    qs = []
    for q in res.quotes:
        d = q.__dict__.copy()
        d["person_id"] = people.get(q.speaker_name)
        d["source_id"] = by_text.get((q.locator or "").split(":")[0])
        qs.append(d)
    with store.transaction(db) as conn:
        store.replace_canonical(res.events, sts, qs, "sha-fixture", conn)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root, db = tmp_path / "root", tmp_path / "s.db"
    monkeypatch.setenv("STAKEHOLDER_ROOT", str(root))
    _seed(root, db)
    app = Flask(__name__)
    app.register_blueprint(build_stakeholder_blueprint(db_path=db))
    return types.SimpleNamespace(c=app.test_client(), db=db, root=root, app=app)


def _forbidden_keys(obj, path="$") -> list[str]:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in FORBIDDEN_USER_KEYS:
                hits.append(f"{path}.{k}")
            hits += _forbidden_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _forbidden_keys(v, f"{path}[{i}]")
    return hits


def _statement_id(env, speaker="홍길동", topic="price_reform") -> str:
    return [s for s in store.list_statements(db_path=env.db)
            if s["speaker_name"] == speaker and s["topic"] == topic][0]["statement_id"]


def _png(size=(32, 20), color=(200, 30, 30), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def _jpeg_with_exif(size=(60, 30)) -> bytes:
    img = Image.new("RGB", size, (10, 120, 200))
    exif = Image.Exif()
    exif[0x0112] = 6                       # Orientation: 90° CW 회전 필요
    exif[0x013B] = "Secret Photographer"   # Artist — 제거돼야 할 메타
    exif[0x010F] = "FakeCam"               # Make
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes(), quality=90)
    return buf.getvalue()


# ── 인증 행렬 ──────────────────────────────────────────────────────────────────

USER_GETS = ["/api/stakeholder/overview", "/api/stakeholder/events", "/api/stakeholder/statements",
             "/api/stakeholder/people", "/api/stakeholder/people/P-FAKE-GOV-HONG",
             "/api/stakeholder/people/P-FAKE-GOV-HONG/photo", "/api/stakeholder/sources",
             f"/api/stakeholder/sources/{HONG_SRC}/excerpt?locator=sources/fake_20260903_hong.txt:3-5",
             "/api/stakeholder/artifacts/relationship-map.svg", "/api/stakeholder/runs?kind=monitoring",
             "/api/stakeholder/runs/monitoring/stakeholder-daily-2026-10-11"]
ADMIN_ROUTES = [("GET", f"/api/stakeholder/sources/{HONG_SRC}/text"),
                ("GET", f"/api/stakeholder/sources/{HONG_SRC}/raw"),
                ("POST", "/api/admin/stakeholder/people"),
                ("PUT", "/api/admin/stakeholder/people/P-FAKE-GOV-HONG"),
                ("PATCH", "/api/admin/stakeholder/people/P-FAKE-GOV-HONG/status"),
                ("POST", "/api/admin/stakeholder/people/P-FAKE-GOV-HONG/photo"),
                ("DELETE", "/api/admin/stakeholder/people/P-FAKE-GOV-HONG/photo"),
                ("GET", "/api/admin/stakeholder/proposals"),
                ("GET", "/api/admin/stakeholder/proposals/AUD-F1A000000001"),
                ("POST", "/api/admin/stakeholder/proposals/AUD-F1A000000001/approve"),
                ("POST", "/api/admin/stakeholder/proposals/AUD-F1A000000001/reject"),
                ("POST", "/api/admin/stakeholder/proposals/AUD-F1A000000001/conflicts/0/ack"),
                ("GET", "/api/admin/stakeholder/proposals/AUD-F1A000000001/conflict-acks"),
                ("GET", "/api/admin/stakeholder/source-domains"),
                ("GET", "/api/admin/stakeholder/integrity"),
                ("POST", "/api/admin/stakeholder/sync")]
AGENT_ROUTES = [("GET", "/api/agent/stakeholder/registry"),
                ("GET", "/api/agent/stakeholder/proposals?audit_run_id=coverage-audit-2026-10-10"),
                ("POST", "/api/agent/stakeholder/proposals")]


def _call(c, method, path, headers=None):
    return c.open(path, method=method, headers=headers or {}, json={} if method != "GET" else None)


def test_auth_matrix_user_routes(env):
    for path in USER_GETS:
        assert env.c.get(path).status_code == 401, path
        assert env.c.get(path, headers=H("agent")).status_code == 403, path       # agent 는 user 라우트 금지
        assert env.c.get(path, headers=H("user")).status_code in (200, 404), path
        assert env.c.get(path, headers=H("admin")).status_code in (200, 404), path


def test_auth_matrix_admin_routes(env, monkeypatch):
    monkeypatch.setattr(sr, "_resolve_sync_runner", lambda: (lambda db, root: {"ok": True, "summary": "stub"}))
    for method, path in ADMIN_ROUTES:
        assert _call(env.c, method, path).status_code == 401, path
        assert _call(env.c, method, path, H("user")).status_code == 403, path
        assert _call(env.c, method, path, H("agent")).status_code == 403, path
        assert _call(env.c, method, path, H("admin")).status_code not in (401, 403), path


def test_auth_matrix_agent_routes(env):
    for method, path in AGENT_ROUTES:
        assert _call(env.c, method, path).status_code == 401, path
        assert _call(env.c, method, path, H("user")).status_code == 403, path
        assert _call(env.c, method, path, H("agent")).status_code not in (401, 403), path
        assert _call(env.c, method, path, H("admin")).status_code not in (401, 403), path


def test_no_person_delete_route(env):
    r = env.c.delete("/api/admin/stakeholder/people/P-FAKE-GOV-HONG", headers=H("admin"))
    assert r.status_code in (404, 405)
    assert store.get_person("P-FAKE-GOV-HONG", db_path=env.db) is not None


# ── 사용자 응답: 금지 키 · 전문 비노출 ──────────────────────────────────────────────

def test_user_responses_never_expose_paths_or_photo_url(env):
    sid = _statement_id(env)
    paths = [p for p in USER_GETS if not p.endswith(("/photo", ".svg"))] + [f"/api/stakeholder/statements/{sid}",
                                                                              "/api/stakeholder/people/P-FAKE-INST-KIM",
                                                                              "/api/stakeholder/runs?kind=audit"]
    for path in paths:
        r = env.c.get(path, headers=H("user"))
        assert r.status_code == 200, path
        assert _forbidden_keys(r.get_json()) == [], path
        body = r.get_data(as_text=True)
        assert "cdn.example-news.test" not in body, path       # 언론 CDN 사진 링크 비노출


def test_user_never_gets_full_text(env):
    full = (env.root / "sources/fake_20260903_hong.txt").read_text(encoding="utf-8")
    sid = _statement_id(env)
    d = env.c.get(f"/api/stakeholder/statements/{sid}", headers=H("user")).get_json()
    assert d["excerpt"]["text"] != full and "제목" not in d["excerpt"]["text"]   # 1행은 창 밖
    assert env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/text", headers=H("user")).status_code == 403
    assert env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/raw", headers=H("user")).status_code == 403


def test_admin_sources_list_includes_paths_user_does_not(env):
    u = env.c.get("/api/stakeholder/sources", headers=H("user")).get_json()["items"]
    a = env.c.get("/api/stakeholder/sources", headers=H("admin")).get_json()["items"]
    assert u and all("local_path" not in s and "text_path" not in s and "raw" not in s for s in u)
    hong = [s for s in a if s["source_id"] == HONG_SRC][0]
    assert hong["text_path"] == "sources/fake_20260903_hong.txt" and hong["local_path"].startswith("sources/raw/")


# ── overview ────────────────────────────────────────────────────────────────────

def test_overview_shape(env):
    d = env.c.get("/api/stakeholder/overview", headers=H("user")).get_json()
    assert d["scope"] == {"start": "2025-10-08", "end": "2026-10-08"}
    k = d["kpi"]
    assert set(k) >= {"events", "statements", "quotes", "speakers_observed", "people_active", "people_priority",
                      "people_candidates", "pending_proposals"}
    assert k["events"] == 2 and k["statements"] == 3 and k["quotes"] == 1
    assert k["speakers_observed"] == 3                       # 홍길동 + 김가상·박가상(복수 발언자 분할)
    assert k["people_active"] == 3 and k["people_priority"] == 1 and k["people_candidates"] == 1
    assert k["pending_proposals"] == 3
    mr = d["latest_monitoring_run"]
    assert mr["run_date"] == "2026-10-11" and mr["run_status"] == "no_change" and mr["saturation_status"] == "met"
    assert "new_quotes" in mr and mr["run_id"] == "stakeholder-daily-2026-10-11"
    ar = d["latest_audit_run"]
    assert ar["run_status"] == "degraded" and ar["claim"].startswith("합성 감사") and ar["why_this_matters"]
    assert ar["run_date"] == "2026-10-10"
    ev = d["recent_events"]
    assert 1 <= len(ev) <= 3 and ev[0]["event_date"] == "2026-09-03"
    assert set(ev[0]) >= {"event_id", "event_date", "title", "speakers", "topics"}
    assert d["integrity"] == {"sources_verified": 2, "sources_total": 2, "mismatch": 0, "missing": 0,
                              "last_sync_at": "2026-10-12T08:20:00+09:00"}
    assert d["copyright_notice"] == COPYRIGHT
    assert [t["slug"] for t in d["topics"]] == ["price_reform", "access", "rwe"]
    assert d["topics"][0]["title"] == "약가제도 개편"


@pytest.mark.parametrize("md_meta,titled", [(True, True), (False, False)])
def test_overview_topics_fallbacks(tmp_path, monkeypatch, md_meta, titled):
    """canonical_stats_json 없음 → 볼륨 정본 MD 헤딩(제목 포함) → 그것도 없으면 발언 slug(제목 None)."""
    root, db = tmp_path / "root", tmp_path / "s.db"
    monkeypatch.setenv("STAKEHOLDER_ROOT", str(root))
    _seed(root, db, topics_meta=False, md_path_meta=md_meta)
    app = Flask(__name__)
    app.register_blueprint(build_stakeholder_blueprint(db_path=db))
    d = app.test_client().get("/api/stakeholder/overview", headers=H("user")).get_json()
    assert [t["slug"] for t in d["topics"]] == ["price_reform", "access", "rwe"]
    assert (d["topics"][0]["title"] == "약가제도 개편") if titled else (d["topics"][0]["title"] is None)


def test_overview_topics_md_path_traversal_ignored(tmp_path, monkeypatch):
    root, db = tmp_path / "root", tmp_path / "s.db"
    monkeypatch.setenv("STAKEHOLDER_ROOT", str(root))
    _seed(root, db, topics_meta=False, md_path_meta=False)
    store.set_meta("canonical_timeline_path", "../../etc/passwd", db_path=db)
    app = Flask(__name__)
    app.register_blueprint(build_stakeholder_blueprint(db_path=db))
    d = app.test_client().get("/api/stakeholder/overview", headers=H("user")).get_json()
    assert [t["title"] for t in d["topics"]] == [None, None, None]


def test_overview_on_empty_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STAKEHOLDER_ROOT", str(tmp_path / "root"))
    app = Flask(__name__)
    app.register_blueprint(build_stakeholder_blueprint(db_path=tmp_path / "empty.db"))
    d = app.test_client().get("/api/stakeholder/overview", headers=H("user")).get_json()
    assert d["kpi"]["events"] == 0 and d["latest_monitoring_run"] is None and d["latest_audit_run"] is None
    assert d["recent_events"] == [] and d["topics"] == [] and d["integrity"]["sources_total"] == 0


# ── statements ───────────────────────────────────────────────────────────────────

def test_statements_list_shape_and_order(env):
    items = env.c.get("/api/stakeholder/statements", headers=H("user")).get_json()["items"]
    assert len(items) == 3
    assert [i["event"]["event_date"] for i in items] == sorted([i["event"]["event_date"] for i in items], reverse=True)
    hong = [i for i in items if i["speaker_name"] == "홍길동" and i["topic"] == "price_reform"][0]
    assert set(hong) >= {"statement_id", "event", "topic", "speaker_name", "speaker_role_at_event", "person",
                         "evidence_grade", "grade_class", "headline_quote", "summary_text", "quotes_count", "source"}
    assert set(hong["event"]) == {"event_id", "event_date", "title"}
    assert hong["person"] == {"person_id": "P-FAKE-GOV-HONG", "name": "홍길동", "photo": False}
    assert hong["grade_class"] == "c" and hong["quotes_count"] == 1
    hq = hong["headline_quote"]
    assert set(hq) == {"quote_id", "quote_text", "locator_match"} and hq["quote_text"].startswith("“신약 보상")
    assert hong["source"] == {"source_id": HONG_SRC, "publisher_title": "가상 협회 약가 토론 가상 기사",
                              "url": "https://example-news.test/news/1", "publication_date": "2026-09-03",
                              "integrity_status": "verified"}
    multi = [i for i in items if i["speaker_name"] == "김가상·박가상"][0]
    assert multi["person"] is None and multi["grade_class"] == "b" and multi["headline_quote"] is None
    assert multi["quotes_count"] == 0


def test_statements_filters(env):
    g = lambda qs: env.c.get(f"/api/stakeholder/statements?{qs}", headers=H("user")).get_json()["items"]
    assert {i["topic"] for i in g("topic=rwe")} == {"rwe"}
    assert len(g("grade=C")) == 2 and len(g("grade=B")) == 1 and g("grade=A") == []
    assert len(g("grade=B-summary")) == 1 and len(g("grade=C-summary")) == 1
    assert len(g("person_id=P-FAKE-GOV-HONG")) == 2
    assert len(g("q=레지스트리")) == 1
    assert len(g("limit=1")) == 1
    assert g("org=보건복지부") == [] and g("org=심평원") == [] and g("org=공단") == []
    assert env.c.get("/api/stakeholder/statements?org=외계기관", headers=H("user")).status_code == 400
    assert env.c.get("/api/stakeholder/statements?limit=abc", headers=H("user")).status_code == 400


def test_org_patterns_match_role_or_person_org():
    assert sr.matches_org("보건복지부", "보건복지부 보험약제과장", None)
    assert sr.matches_org("보건복지부", "복지부 과장", None)
    assert sr.matches_org("심평원", "건강보험심사평가원 약제관리실장", None)
    assert sr.matches_org("심평원", None, "심평원")
    assert sr.matches_org("공단", "국민건강보험공단 급여상임이사", None)
    assert sr.matches_org("공단", None, "건보공단")
    assert not sr.matches_org("공단", "가상부 가상과장", "가상부")


def test_statement_detail_shape(env):
    sid = _statement_id(env)
    d = env.c.get(f"/api/stakeholder/statements/{sid}", headers=H("user")).get_json()
    assert d["statement_id"] == sid and d["event_date"] == "2026-09-03" and d["event_title"]
    assert d["person"]["person_id"] == "P-FAKE-GOV-HONG" and d["person"]["photo"] is False
    assert d["grade_class"] == "c" and d["analysis_text"]
    q = d["quotes"][0]
    assert q["source_id"] == HONG_SRC and q["evidence_grade"] and q["locator_match"] == "range"
    assert q["locator"] == "sources/fake_20260903_hong.txt:3-5"
    pv = d["provenance"][0]
    assert set(pv) >= {"source_id", "title", "url", "source_type", "evidence_grade", "publication_date", "sha256",
                       "text_sha256", "integrity_status", "last_live_check"}
    assert pv["source_id"] == HONG_SRC and pv["integrity_status"] == "verified" and len(pv["sha256"]) == 64
    assert pv["last_live_check"]["http_status"] == 200 and pv["last_live_check"]["checked_at"]
    ex = d["excerpt"]
    assert ex["source_id"] == HONG_SRC and ex["locator"] == "sources/fake_20260903_hong.txt:3-5"
    assert "상충하지 않는다" in ex["text"] and len(ex["text"]) <= 600
    assert env.c.get("/api/stakeholder/statements/nope", headers=H("user")).status_code == 404


def test_statement_detail_file_level_locator_excerpt(env):
    sid = _statement_id(env, speaker="김가상·박가상", topic="rwe")
    d = env.c.get(f"/api/stakeholder/statements/{sid}", headers=H("user")).get_json()
    assert d["person"] is None and d["quotes"] == []
    assert d["provenance"][0]["source_id"] == "SRC-FAKE-20260619-RWE"
    assert d["excerpt"]["text"].startswith("요약만 있는 보도자료")


# ── events · people · runs · relationship map ────────────────────────────────────

def test_events_list(env):
    items = env.c.get("/api/stakeholder/events", headers=H("user")).get_json()["items"]
    assert [e["event_date"] for e in items] == ["2026-09-03", "2026-06-19"]
    e = items[0]
    assert e["statements_count"] == 2 and e["headline"]["speaker_name"] == "홍길동"
    assert e["headline"]["person"]["person_id"] == "P-FAKE-GOV-HONG" and e["headline"]["quote_text"]
    rwe = env.c.get("/api/stakeholder/events?topic=rwe", headers=H("user")).get_json()["items"]
    assert [x["event_date"] for x in rwe] == ["2026-06-19"]
    assert env.c.get("/api/stakeholder/events?from=2026-07-01", headers=H("user")).get_json()["items"][0][
        "event_date"] == "2026-09-03"
    assert len(env.c.get("/api/stakeholder/events?from=2026-07-01", headers=H("user")).get_json()["items"]) == 1


def test_people_list_and_detail(env):
    d = env.c.get("/api/stakeholder/people", headers=H("user")).get_json()
    assert d["active_effective_count"] == 3 and d["updated_at"]
    hong = [p for p in d["items"] if p["person_id"] == "P-FAKE-GOV-HONG"][0]
    assert hong["has_photo"] is False and hong["statements_count"] == 2 and hong["importance_score"] == 5
    kim_u = env.c.get("/api/stakeholder/people/P-FAKE-INST-KIM", headers=H("user")).get_json()
    assert "photo_url" not in kim_u and "role_history" in kim_u and "recent_statements" in kim_u
    kim_a = env.c.get("/api/stakeholder/people/P-FAKE-INST-KIM", headers=H("admin")).get_json()
    assert kim_a["photo_url"] == "https://cdn.example-news.test/photo/fake_kim.jpg"   # admin 메타 텍스트
    assert "photo_path" not in kim_a
    hd = env.c.get("/api/stakeholder/people/P-FAKE-GOV-HONG", headers=H("user")).get_json()
    assert len(hd["recent_statements"]) == 2 and hd["recent_statements"][0]["statement_id"]
    assert env.c.get("/api/stakeholder/people/NOPE", headers=H("user")).status_code == 404
    assert env.c.get("/api/stakeholder/people?status=paused", headers=H("user")).get_json()["items"] == []
    assert env.c.get("/api/stakeholder/people/P-FAKE-GOV-HONG/photo", headers=H("user")).status_code == 404


def test_runs_list_and_detail(env):
    items = env.c.get("/api/stakeholder/runs?kind=monitoring", headers=H("user")).get_json()["items"]
    assert [r["run_date"] for r in items] == ["2026-10-11", "2026-10-10"] and "payload_json" not in items[0]
    assert len(env.c.get("/api/stakeholder/runs?kind=monitoring&limit=1", headers=H("user")).get_json()["items"]) == 1
    a = env.c.get("/api/stakeholder/runs?kind=audit", headers=H("user")).get_json()["items"]
    assert a[0]["audit_run_id"] == "coverage-audit-2026-10-10"
    d = env.c.get("/api/stakeholder/runs/audit/coverage-audit-2026-10-10", headers=H("user")).get_json()
    assert d["payload"]["claim"] and isinstance(d["md"], str) and d["md"]
    # 런 payload 의 scope 스냅숏은 photo_url·photo_path·local_path 를 품는다 — user 에겐 걸러서, admin 에겐 원본 그대로
    mon_u = env.c.get("/api/stakeholder/runs/monitoring/stakeholder-daily-2026-10-11", headers=H("user")).get_json()
    mon_a = env.c.get("/api/stakeholder/runs/monitoring/stakeholder-daily-2026-10-11", headers=H("admin")).get_json()
    assert _forbidden_keys(mon_u) == [] and _forbidden_keys(mon_a) != []
    assert mon_u["payload"]["run_id"] == "stakeholder-daily-2026-10-11"
    assert env.c.get("/api/stakeholder/runs?kind=bogus", headers=H("user")).status_code == 400
    assert env.c.get("/api/stakeholder/runs/bogus/x", headers=H("user")).status_code == 400
    assert env.c.get("/api/stakeholder/runs/audit/nope", headers=H("user")).status_code == 404


def test_run_md_path_traversal_is_not_read(env):
    with store.transaction(env.db) as conn:
        conn.execute("UPDATE sv_audit_run SET md_path='../../etc/passwd'")
    d = env.c.get("/api/stakeholder/runs/audit/coverage-audit-2026-10-10", headers=H("user")).get_json()
    assert d["md"] is None


def test_relationship_map_svg(env):
    r = env.c.get("/api/stakeholder/artifacts/relationship-map.svg", headers=H("user"))
    assert r.status_code == 200 and r.mimetype == "image/svg+xml" and b"<svg" in r.data
    assert "default-src 'none'" in r.headers.get("Content-Security-Policy", "")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    with store.transaction(env.db) as conn:
        conn.execute("DELETE FROM sv_artifact WHERE path LIKE '%.svg'")
    assert env.c.get("/api/stakeholder/artifacts/relationship-map.svg", headers=H("user")).status_code == 404


# ── 발췌 (locator 창) · 경로 탈출 ─────────────────────────────────────────────────

def test_excerpt_window_and_shape(env):
    r = env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/excerpt?locator=sources/fake_20260903_hong.txt:3-5",
                  headers=H("user"))
    d = r.get_json()
    assert r.status_code == 200 and d["start"] == 3 and d["end"] == 5 and d["chars"] == len(d["text"])
    assert d["text"].splitlines()[0].startswith("홍길동 과장은") and d["text"].splitlines()[-1] == "끝"
    one = env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/excerpt?locator=sources/fake_20260903_hong.txt:4",
                    headers=H("user")).get_json()
    assert one["start"] == one["end"] == 4 and one["text"].startswith("“하반기")


def test_excerpt_capped_at_600_chars(env):
    p = env.root / "sources/fake_20260903_hong.txt"
    p.write_text("\n".join("가" * 300 for _ in range(10)), encoding="utf-8")
    d = env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/excerpt?locator=sources/fake_20260903_hong.txt:1-10",
                  headers=H("user")).get_json()
    assert len(d["text"]) == 600 and d["chars"] == 600 and d["truncated"] is True
    whole = env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/excerpt?locator=sources/fake_20260903_hong.txt",
                      headers=H("user")).get_json()
    assert len(whole["text"]) == 600


@pytest.mark.parametrize("locator", [
    "sources/fake_20260619_rwe.txt:1-2",          # 다른 source 의 파일
    "sources/../source_manifest.json:1-2",
    "../../../etc/passwd:1-2",
    "/etc/passwd:1-2",
    "sources/fake_20260903_hong.txt/../../x:1",
    "sources/fake_20260903_hong.txt:abc",
    "sources/fake_20260903_hong.txt:5-3",
    "sources/fake_20260903_hong.txt:0-2",
    "sources/fake_20260903_hong.txt:3-5;sources/x.txt:1",
    "sources/fake_20260903_hong.txt:1-2,4-5",
    "",
])
def test_excerpt_rejects_bad_or_foreign_locators(env, locator):
    r = env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/excerpt", query_string={"locator": locator},
                  headers=H("user"))
    assert r.status_code == 400, locator
    assert "root:" not in r.get_data(as_text=True)


def test_excerpt_unknown_source_and_missing_file(env):
    assert env.c.get("/api/stakeholder/sources/NOPE/excerpt?locator=sources/a.txt:1",
                     headers=H("user")).status_code == 404
    (env.root / "sources/fake_20260903_hong.txt").unlink()
    assert env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/excerpt?locator=sources/fake_20260903_hong.txt:1",
                     headers=H("user")).status_code == 404


def test_poisoned_manifest_paths_never_escape_root(env, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    store.upsert_source({"source_id": "SRC-EVIL", "title": "evil", "text_path": "../secret.txt",
                         "local_path": "../secret.txt"}, db_path=env.db)
    for path in ("/api/stakeholder/sources/SRC-EVIL/text", "/api/stakeholder/sources/SRC-EVIL/raw"):
        r = env.c.get(path, headers=H("admin"))
        assert r.status_code in (400, 404) and b"TOP-SECRET" not in r.data, path
    r = env.c.get("/api/stakeholder/sources/SRC-EVIL/excerpt?locator=../secret.txt:1", headers=H("user"))
    assert r.status_code == 400 and b"TOP-SECRET" not in r.data
    # 심볼릭 링크 탈출
    link = env.root / "sources" / "link.txt"
    link.symlink_to(secret)
    store.upsert_source({"source_id": "SRC-LINK", "title": "l", "text_path": "sources/link.txt"}, db_path=env.db)
    r = env.c.get("/api/stakeholder/sources/SRC-LINK/excerpt?locator=sources/link.txt:1", headers=H("user"))
    assert r.status_code == 400 and b"TOP-SECRET" not in r.data
    assert b"TOP-SECRET" not in env.c.get("/api/stakeholder/sources/SRC-LINK/text", headers=H("admin")).data


def test_admin_text_and_raw(env):
    t = env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/text", headers=H("admin"))
    assert t.status_code == 200 and t.mimetype == "text/plain" and "제목" in t.get_data(as_text=True)
    raw = env.c.get(f"/api/stakeholder/sources/{HONG_SRC}/raw", headers=H("admin"))
    assert raw.status_code == 200 and raw.headers["Content-Disposition"].startswith("attachment")
    assert "fake_20260903_hong.html" in raw.headers["Content-Disposition"]
    assert raw.headers.get("X-Content-Type-Options") == "nosniff"
    assert env.c.get("/api/stakeholder/sources/NOPE/text", headers=H("admin")).status_code == 404


# ── 사진 파이프라인 ─────────────────────────────────────────────────────────────

def test_normalize_strips_metadata_rotates_and_crops():
    out = photos.normalize(_jpeg_with_exif((60, 30)))
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP" and img.size == (640, 640)
    assert len(img.getexif()) == 0 and not img.info.get("exif") and not img.info.get("icc_profile")
    assert b"Secret Photographer" not in out and b"FakeCam" not in out


def test_normalize_transparent_png_and_rejects_bad_input():
    out = photos.normalize(_png((20, 20), (0, 0, 0, 0), mode="RGBA"))
    img = Image.open(io.BytesIO(out)).convert("RGB")
    assert img.getpixel((320, 320)) == pytest.approx((255, 255, 255), abs=3)   # 투명 → 흰 배경
    with pytest.raises(photos.PhotoError) as e:
        photos.normalize(b"not an image at all")
    assert e.value.status == 415
    gif = io.BytesIO()
    Image.new("RGB", (4, 4)).save(gif, "GIF")
    with pytest.raises(photos.PhotoError) as e:
        photos.normalize(gif.getvalue())
    assert e.value.status == 415
    with pytest.raises(photos.PhotoError) as e:
        photos.normalize(b"x" * (photos.MAX_BYTES + 1))
    assert e.value.status == 413


def test_normalize_rejects_decompression_bomb(monkeypatch):
    monkeypatch.setattr(photos, "MAX_PIXELS", 100)
    with pytest.raises(photos.PhotoError) as e:
        photos.normalize(_png((20, 20)))
    assert e.value.status == 413


def test_save_and_delete_stay_under_photos_dir(tmp_path):
    rel = photos.save_person_photo("P-X/../../evil", _png(), tmp_path)
    assert rel.startswith("photos/") and rel.endswith(".webp") and ".." not in rel
    assert (tmp_path / rel).exists()
    assert photos.delete_photo("../outside.webp", tmp_path) is False
    (tmp_path / "keep.txt").write_text("k")
    assert photos.delete_photo("keep.txt", tmp_path) is False and (tmp_path / "keep.txt").exists()
    assert photos.delete_photo(rel, tmp_path) is True and not (tmp_path / rel).exists()


def test_photo_upload_roundtrip(env):
    pid = "P-FAKE-GOV-HONG"
    up = env.c.post(f"/api/admin/stakeholder/people/{pid}/photo", headers=H("admin", "boss@x.test"),
                    data={"photo": (io.BytesIO(_jpeg_with_exif()), "me.jpg", "image/jpeg")},
                    content_type="multipart/form-data")
    assert up.status_code == 200, up.get_json()
    body = up.get_json()
    assert body["has_photo"] is True and "photo_path" not in body and body["updated_by"] == "boss@x.test"
    first = store.get_person(pid, db_path=env.db)["photo_path"]
    assert first.startswith("photos/") and (env.root / first).exists()
    g = env.c.get(f"/api/stakeholder/people/{pid}/photo", headers=H("user"))
    assert g.status_code == 200 and g.mimetype == "image/webp" and Image.open(io.BytesIO(g.data)).size == (640, 640)
    assert "private" in g.headers.get("Cache-Control", "")
    lst = env.c.get("/api/stakeholder/statements?person_id=" + pid, headers=H("user")).get_json()["items"]
    assert lst[0]["person"]["photo"] is True
    # 교체 → 구 파일 삭제
    up2 = env.c.post(f"/api/admin/stakeholder/people/{pid}/photo", headers=H("admin"),
                     data={"photo": (io.BytesIO(_png()), "b.png", "image/png")}, content_type="multipart/form-data")
    assert up2.status_code == 200
    second = store.get_person(pid, db_path=env.db)["photo_path"]
    assert second != first and not (env.root / first).exists() and (env.root / second).exists()
    # 삭제
    de = env.c.delete(f"/api/admin/stakeholder/people/{pid}/photo", headers=H("admin"))
    assert de.status_code == 200 and de.get_json()["has_photo"] is False
    assert not (env.root / second).exists()
    assert env.c.get(f"/api/stakeholder/people/{pid}/photo", headers=H("user")).status_code == 404


def test_photo_upload_limits(env):
    pid = "P-FAKE-GOV-HONG"
    url = f"/api/admin/stakeholder/people/{pid}/photo"
    big = env.c.post(url, headers=H("admin"), content_type="multipart/form-data",
                     data={"photo": (io.BytesIO(b"\xff" * (6 * 1024 * 1024)), "big.jpg", "image/jpeg")})
    assert big.status_code == 413 and big.get_json()["code"]
    txt = env.c.post(url, headers=H("admin"), content_type="multipart/form-data",
                     data={"photo": (io.BytesIO(b"hello"), "a.txt", "text/plain")})
    assert txt.status_code == 415
    fake = env.c.post(url, headers=H("admin"), content_type="multipart/form-data",
                      data={"photo": (io.BytesIO(b"<svg/>"), "a.png", "image/png")})
    assert fake.status_code == 415
    missing = env.c.post(url, headers=H("admin"), content_type="multipart/form-data", data={})
    assert missing.status_code == 422
    nobody = env.c.post("/api/admin/stakeholder/people/NOPE/photo", headers=H("admin"),
                        content_type="multipart/form-data",
                        data={"photo": (io.BytesIO(_png()), "a.png", "image/png")})
    assert nobody.status_code == 404
    assert not list((env.root / "photos").glob("*")) if (env.root / "photos").exists() else True
    assert store.get_person(pid, db_path=env.db)["photo_path"] is None


def test_photo_route_refuses_paths_outside_photos(env):
    with store.transaction(env.db) as conn:
        conn.execute("UPDATE sv_person SET photo_path='sources/fake_20260903_hong.txt' WHERE person_id='P-FAKE-GOV-HONG'")
        conn.execute("UPDATE sv_person SET photo_path='../../etc/passwd' WHERE person_id='P-FAKE-INST-KIM'")
    assert env.c.get("/api/stakeholder/people/P-FAKE-GOV-HONG/photo", headers=H("user")).status_code == 404
    assert env.c.get("/api/stakeholder/people/P-FAKE-INST-KIM/photo", headers=H("user")).status_code == 404


# ── admin 인물 CRUD ─────────────────────────────────────────────────────────────

def test_admin_people_crud(env):
    c, h = env.c, H("admin", "boss@x.test")
    r = c.post("/api/admin/stakeholder/people", headers=h,
               json={"name": "이가상", "organization": "보건복지부", "department": "보험약제과", "role": "사무관",
                     "verification_status": "verified", "photo_url": "https://cdn.x/y.jpg"})
    assert r.status_code == 201, r.get_json()
    p = r.get_json()
    assert p["verification_status"] == "candidate" and p["updated_by"] == "boss@x.test"
    assert store.get_person(p["person_id"], db_path=env.db)["photo_url"] is None
    dup = c.post("/api/admin/stakeholder/people", headers=h,
                 json={"name": "이가상", "organization": "보건복지부", "department": "x", "role": "y"})
    assert dup.status_code == 409 and dup.get_json()["code"] == "DUPLICATE_PERSON" and dup.get_json()["error"]
    bad = c.post("/api/admin/stakeholder/people", headers=h, json={"name": "이"})
    assert bad.status_code == 422 and bad.get_json()["code"].startswith("BAD_")
    assert c.post("/api/admin/stakeholder/people", headers=h, data="nope",
                  content_type="text/plain").status_code == 422
    up = c.put(f"/api/admin/stakeholder/people/{p['person_id']}", headers=h,
               json={"name": "이가상", "organization": "보건복지부", "department": "보험약제과", "role": "서기관"})
    assert up.status_code == 200 and up.get_json()["role"] == "서기관"
    prot = c.put("/api/admin/stakeholder/people/P-FAKE-GOV-HONG", headers=h,
                 json={"name": "홍길동", "organization": "가상부", "department": "가상과", "role": "국장"})
    assert prot.status_code == 409 and prot.get_json()["code"] == "VERIFIED_PROTECTED"
    st = c.patch("/api/admin/stakeholder/people/P-FAKE-GOV-HONG/status", headers=h, json={"status": "paused"})
    assert st.status_code == 200 and st.get_json()["status"] == "paused"
    assert c.patch("/api/admin/stakeholder/people/P-FAKE-GOV-HONG/status", headers=h,
                   json={"status": "deleted"}).status_code == 422
    assert c.patch("/api/admin/stakeholder/people/NOPE/status", headers=h, json={"status": "paused"}).status_code == 404
    assert c.put("/api/admin/stakeholder/people/NOPE", headers=h, json={"name": "아무개"}).status_code == 404


# ── admin 제안 · 충돌 확인 ──────────────────────────────────────────────────────

def test_admin_proposals_list_and_detail(env):
    d = env.c.get("/api/admin/stakeholder/proposals", headers=H("admin")).get_json()
    assert d["pending_count"] == 3 and d["updated_at"] and len(d["proposals"]) == 3
    p1 = [p for p in d["proposals"] if p["proposal_id"] == "AUD-F1A000000001"][0]
    assert p1["conflicts_status"][0]["acknowledged"] is False and p1["conflicts_unacknowledged"] == [0]
    assert isinstance(p1["events"], list)
    t = env.c.get("/api/admin/stakeholder/proposals?type=role_change", headers=H("admin")).get_json()
    assert [p["proposal_type"] for p in t["proposals"]] == ["role_change"]
    s = env.c.get("/api/admin/stakeholder/proposals?status=rejected", headers=H("admin")).get_json()
    assert s["proposals"] == [] and s["pending_count"] == 3
    one = env.c.get("/api/admin/stakeholder/proposals/AUD-F1A000000003", headers=H("admin")).get_json()
    assert one["proposal_id"] == "AUD-F1A000000003" and "conflicts_status" in one and "events" in one
    assert env.c.get("/api/admin/stakeholder/proposals/NOPE", headers=H("admin")).status_code == 404


def test_admin_approve_reject_and_conflict_ack(env, monkeypatch):
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    c, h = env.c, H("admin", "boss@x.test")
    gated = c.post("/api/admin/stakeholder/proposals/AUD-F1A000000001/approve", headers=h, json={"reason": "확인함"})
    assert gated.status_code == 422 and gated.get_json()["code"] == "CONFLICTS_UNACKNOWLEDGED"
    assert gated.get_json()["details"] == {"unacknowledged": [0]}
    assert c.post("/api/admin/stakeholder/proposals/AUD-F1A000000001/conflicts/x/ack", headers=h,
                  json={"reason": "확인"}).status_code == 422
    assert c.post("/api/admin/stakeholder/proposals/AUD-F1A000000001/conflicts/-1/ack", headers=h,
                  json={"reason": "확인"}).status_code == 422
    assert c.post("/api/admin/stakeholder/proposals/AUD-F1A000000001/conflicts/5/ack", headers=h,
                  json={"reason": "확인"}).get_json()["code"] == "BAD_CONFLICT_INDEX"
    assert c.post("/api/admin/stakeholder/proposals/AUD-F1A000000001/conflicts/0/ack", headers=h,
                  json={"reason": "x"}).get_json()["code"] == "BAD_REASON"
    ack = c.post("/api/admin/stakeholder/proposals/AUD-F1A000000001/conflicts/0/ack", headers=h,
                 json={"reason": "보도 독립성만 인정 — 확인"})
    assert ack.status_code == 200 and ack.get_json()["conflicts_unacknowledged"] == []
    assert ack.get_json()["conflicts_status"][0]["by"] == "boss@x.test"
    again = c.post("/api/admin/stakeholder/proposals/AUD-F1A000000001/conflicts/0/ack", headers=h,
                   json={"reason": "다시"})
    assert again.status_code == 409 and again.get_json()["code"] == "ALREADY_ACKNOWLEDGED"
    acks = c.get("/api/admin/stakeholder/proposals/AUD-F1A000000001/conflict-acks", headers=h).get_json()["items"]
    assert acks[0]["acknowledged_by"] == "boss@x.test" and acks[0]["conflict_index"] == 0
    assert c.post("/api/admin/stakeholder/proposals/NOPE/conflicts/0/ack", headers=h,
                  json={"reason": "확인"}).status_code == 404
    ok = c.post("/api/admin/stakeholder/proposals/AUD-F1A000000002/approve", headers=h, json={"reason": "공식 인사"})
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["status"] == "scheduled" and ok.get_json()["reviewer"] == "boss@x.test"
    assert ok.get_json()["effective_from"] == "2026-10-13"
    twice = c.post("/api/admin/stakeholder/proposals/AUD-F1A000000002/approve", headers=h, json={"reason": "다시"})
    assert twice.status_code == 409
    nr = c.post("/api/admin/stakeholder/proposals/AUD-F1A000000003/reject", headers=h, json={})
    assert nr.status_code == 422 and nr.get_json()["code"] == "BAD_REASON"
    rj = c.post("/api/admin/stakeholder/proposals/AUD-F1A000000003/reject", headers=h, json={"reason": "분류 부적합"})
    assert rj.status_code == 200 and rj.get_json()["status"] == "rejected" and rj.get_json()["reviewer"] == "boss@x.test"
    ev = store.list_proposal_events("AUD-F1A000000003", db_path=env.db)
    assert ev[-1]["actor"] == "boss@x.test" and ev[-1]["event"] == "rejected"
    assert c.post("/api/admin/stakeholder/proposals/NOPE/approve", headers=h,
                  json={"reason": "확인"}).status_code == 404


def test_admin_source_domains_and_integrity(env):
    d = env.c.get("/api/admin/stakeholder/source-domains", headers=H("admin")).get_json()
    assert len(d["items"]) == 4 and d["effective_count"] == 4
    store.set_source_integrity(HONG_SRC, "mismatch", db_path=env.db)
    i = env.c.get("/api/admin/stakeholder/integrity", headers=H("admin")).get_json()
    assert i["summary"]["sources"]["verified"] == 2 and i["counts"]["mismatch"] == 1 and i["last_sync_at"]
    assert [x["source_id"] for x in i["problems"]] == [HONG_SRC] and i["problems"][0]["text_path"]


# ── admin 수동 sync ─────────────────────────────────────────────────────────────

def test_admin_sync_prefers_registered_runbook(env, monkeypatch):
    from agents.agent_tasks import runbooks
    calls = []
    monkeypatch.setitem(runbooks.RUNBOOKS, "stakeholder_data_sync",
                        lambda: calls.append(1) or {"ok": True, "summary": "sync ok", "detail": {}})
    r = env.c.post("/api/admin/stakeholder/sync", headers=H("admin"))
    assert r.status_code == 200 and r.get_json()["ok"] is True and calls == [1]


def test_admin_sync_falls_back_to_t5_sync_module(env, monkeypatch):
    from agents.agent_tasks import runbooks
    monkeypatch.delitem(runbooks.RUNBOOKS, "stakeholder_data_sync", raising=False)
    seen = {}

    def fake_sync_now(*, root=None, db_path=None, today=None, dry_run=False):
        seen.update(root=Path(root), db=Path(db_path), dry_run=dry_run)
        return {"ok": True, "counts": {"downloaded": 0}, "errors": [], "warnings": [], "error_class": None}

    fake = types.ModuleType("agents.stakeholder.sync")
    fake.sync_now = fake_sync_now
    monkeypatch.setitem(sys.modules, "agents.stakeholder.sync", fake)
    r = env.c.post("/api/admin/stakeholder/sync", headers=H("admin"))
    assert r.status_code == 200 and r.get_json()["ok"] is True and r.get_json()["detail"]["counts"] == {"downloaded": 0}
    assert seen["root"] == env.root and seen["db"] == env.db and seen["dry_run"] is False


def _fake_sync_module(monkeypatch, result: dict):
    from agents.agent_tasks import runbooks
    monkeypatch.delitem(runbooks.RUNBOOKS, "stakeholder_data_sync", raising=False)
    fake = types.ModuleType("agents.stakeholder.sync")
    fake.sync_now = lambda **kw: result
    monkeypatch.setitem(sys.modules, "agents.stakeholder.sync", fake)


def test_admin_sync_lock_held_is_409(env, monkeypatch):
    msg = "stakeholder sync already running (lock held)"
    _fake_sync_module(monkeypatch, {"ok": False, "counts": {}, "errors": [msg], "error": msg,
                                    "error_class": "transient"})
    r = env.c.post("/api/admin/stakeholder/sync", headers=H("admin"))
    assert r.status_code == 409 and r.get_json()["code"] == "SYNC_RUNNING" and "lock held" in r.get_json()["error"]


def test_admin_sync_config_error_is_503(env, monkeypatch):
    msg = "auth required: STAKEHOLDER_SYNC_TOKEN missing (fail-closed in production)"
    _fake_sync_module(monkeypatch, {"ok": False, "counts": {}, "errors": [msg], "error": msg, "error_class": "config"})
    r = env.c.post("/api/admin/stakeholder/sync", headers=H("admin"))
    assert r.status_code == 503 and r.get_json()["code"] == "SYNC_CONFIG" and "auth required" in r.get_json()["error"]


def test_admin_sync_runbook_config_failure_maps_from_detail(env, monkeypatch):
    from agents.agent_tasks import runbooks
    monkeypatch.setitem(runbooks.RUNBOOKS, "stakeholder_data_sync", lambda: {
        "ok": False, "summary": "stakeholder sync 실패 — None; 오류: auth required",
        "detail": {"ok": False, "errors": ["auth required"], "error_class": "config"}})
    r = env.c.post("/api/admin/stakeholder/sync", headers=H("admin"))
    assert r.status_code == 503 and r.get_json()["code"] == "SYNC_CONFIG"


def test_admin_sync_failure_is_502_and_missing_is_503(env, monkeypatch):
    monkeypatch.setattr(sr, "_resolve_sync_runner", lambda: (lambda db, root: {"ok": False, "summary": "auth required"}))
    r = env.c.post("/api/admin/stakeholder/sync", headers=H("admin"))
    assert r.status_code == 502 and r.get_json()["ok"] is False
    monkeypatch.setattr(sr, "_resolve_sync_runner", lambda: None)
    r = env.c.post("/api/admin/stakeholder/sync", headers=H("admin"))
    assert r.status_code == 503 and r.get_json()["code"] == "RUNBOOK_MISSING"


# ── agent 채널 ──────────────────────────────────────────────────────────────────

def _agent_body(**kw):
    body = {"proposal_type": "source_expansion", "dedupe_key": "source_expansion:new.example-news.test",
            "priority": "normal", "summary": "가상 신규 매체 추가 제안", "rationale": "합성 근거 3건",
            "proposed_change": {"domain": "new.example-news.test", "name": "신규가상", "source_group": "healthcare_trade"},
            "evidence": [{"url": "https://new.example-news.test/a", "publisher": "신규가상", "evidence_level": "E2",
                          "locator": "본문 2문단"}],
            "audit_run_id": "coverage-audit-2026-10-12", "status": "applied", "reviewer": "hacker"}
    body.update(kw)
    return body


def test_agent_registry_and_proposals(env, monkeypatch):
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    reg = env.c.get("/api/agent/stakeholder/registry", headers=H("agent")).get_json()
    assert reg == store.registry_snapshot(today=date(2026, 10, 12), db_path=env.db)
    assert len(reg["people"]) == 3 and len(reg["sources"]) == 4 and reg["people_sha256"]
    assert _forbidden_keys(reg) == []
    r = env.c.post("/api/agent/stakeholder/proposals", headers=H("agent", "auditor@svc"), json=_agent_body())
    assert r.status_code == 201, r.get_json()
    p = r.get_json()
    assert p["status"] == "needs_review" and p["reviewer"] is None and p["origin"] == "agent_api"
    assert store.list_proposal_events(p["proposal_id"], db_path=env.db)[0]["actor"] == "auditor@svc"
    dup = env.c.post("/api/agent/stakeholder/proposals", headers=H("agent"), json=_agent_body())
    assert dup.status_code == 409 and dup.get_json()["code"] == "DUPLICATE" and p["proposal_id"] in dup.get_json()["error"]
    bad = env.c.post("/api/agent/stakeholder/proposals", headers=H("agent"), json=_agent_body(proposal_type="delete"))
    assert bad.status_code == 422 and bad.get_json()["code"] == "BAD_TYPE"
    notjson = env.c.post("/api/agent/stakeholder/proposals", headers=H("agent"), data="x", content_type="text/plain")
    assert notjson.status_code == 422
    by_run = env.c.get("/api/agent/stakeholder/proposals?audit_run_id=coverage-audit-2026-10-12",
                       headers=H("agent")).get_json()["items"]
    assert [x["proposal_id"] for x in by_run] == [p["proposal_id"]]
    old = env.c.get("/api/agent/stakeholder/proposals?audit_run_id=coverage-audit-2026-10-10",
                    headers=H("agent")).get_json()["items"]
    assert len(old) == 3


def test_agent_cannot_approve_or_reach_user_routes(env):
    for method, path in ADMIN_ROUTES:
        assert _call(env.c, method, path, H("agent")).status_code == 403, path
    for path in USER_GETS:
        assert env.c.get(path, headers=H("agent")).status_code == 403, path


# ── 서버 등록 ───────────────────────────────────────────────────────────────────

def test_blueprint_registered_on_server():
    import api.server as s
    rules = {r.rule for r in s.app.url_map.iter_rules()}
    assert {"/api/stakeholder/overview", "/api/admin/stakeholder/proposals", "/api/agent/stakeholder/registry",
            "/api/stakeholder/sources/<source_id>/excerpt"} <= rules
