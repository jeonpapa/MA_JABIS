from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from agents.stakeholder import paths, store


def _db(tmp_path):
    return tmp_path / "s.db"


# ── 계획서 T1 Step 1 (7) ────────────────────────────────────────────────────────

def test_ensure_tables_is_idempotent_and_lazy(tmp_path):
    db = _db(tmp_path)
    store.ensure_tables(db); store.ensure_tables(db)
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sv_%'")}
    assert {"sv_person", "sv_person_role_history", "sv_source_domain", "sv_source", "sv_artifact", "sv_event",
            "sv_statement", "sv_quote", "sv_monitoring_run", "sv_audit_run", "sv_audit_proposal",
            "sv_audit_proposal_event", "sv_meta", "sv_sync_file"} <= names


def test_person_upsert_insert_only_and_unique_name_org(tmp_path):
    db = _db(tmp_path)
    p = {"person_id": "P-1", "name": "강준혁", "organization": "보건복지부", "organization_code": "MOHW",
         "department": "보험약제과", "role": "과장", "status": "active", "verification_status": "verified",
         "effective_from": "2026-10-09", "aliases": [], "topic_tags": ["약가"], "photo_url": None, "photo_path": None,
         "created_at": "2026-10-08T00:00:00+09:00", "updated_at": "2026-10-08T00:00:00+09:00"}
    assert store.insert_person_if_absent(p, origin="import", db_path=db) is True
    p2 = dict(p, role="국장")
    assert store.insert_person_if_absent(p2, origin="import", db_path=db) is False      # 기존 행 보존
    assert store.get_person("P-1", db_path=db)["role"] == "과장"
    with pytest.raises(store.ConflictError):
        store.create_person({**p, "person_id": "P-2"}, actor="a@x", db_path=db)        # 같은 기관 동명


def test_create_person_sets_candidate_and_tomorrow(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    row = store.create_person({"name": "홍길동", "organization": "보건복지부", "department": "보험약제과", "role": "사무관",
                               "status": "active", "verification_status": "verified", "aliases": [], "topic_tags": []},
                              actor="admin@x", db_path=db)
    assert row["person_id"].startswith("P-USER-") and row["verification_status"] == "candidate"
    assert row["effective_from"] == "2026-10-13" and row["origin"] == "dashboard"


def test_update_verified_person_org_role_is_409(tmp_path):
    db = _db(tmp_path)
    store.insert_person_if_absent({"person_id": "P-1", "name": "a", "organization": "o", "department": "d", "role": "r",
                                   "status": "active", "verification_status": "verified", "effective_from": "2026-01-01",
                                   "aliases": [], "topic_tags": [], "photo_url": None, "photo_path": None,
                                   "created_at": "t", "updated_at": "t"}, origin="import", db_path=db)
    with pytest.raises(store.ConflictError):
        store.update_person("P-1", {"name": "a", "organization": "o", "department": "d", "role": "r2", "status": "active",
                                    "aliases": [], "topic_tags": []}, actor="admin@x", db_path=db)
    row = store.update_person("P-1", {"name": "a", "organization": "o", "department": "d", "role": "r", "status": "paused",
                                      "aliases": ["A"], "topic_tags": []}, actor="admin@x", db_path=db)
    assert row["status"] == "paused" and row["verification_status"] == "verified"


def test_registry_snapshot_sha_is_stable(tmp_path):
    db = _db(tmp_path)
    snap1 = store.registry_snapshot(today=date(2026, 10, 12), db_path=db)
    snap2 = store.registry_snapshot(today=date(2026, 10, 12), db_path=db)
    assert snap1["people_sha256"] == snap2["people_sha256"] and snap1["people"] == []


def test_transaction_rolls_back_on_error(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(RuntimeError):
        with store.transaction(db) as conn:
            conn.execute("INSERT INTO sv_meta (key, value, updated_at) VALUES ('k','v','t')")
            raise RuntimeError("boom")
    assert store.get_meta("k", db_path=db) is None


def test_proposal_open_dedupe_enforced_by_index(tmp_path):
    db = _db(tmp_path)
    base = {"proposal_id": "AUD-1", "proposal_type": "new_person", "dedupe_key": "new_person:x:y:z", "priority": "high",
            "summary": "s", "rationale": "r", "proposed_change": {}, "evidence": [], "conflicts": [], "audit_run_id": None,
            "created_by": "auditor", "status": "needs_review", "effective_from": None, "reviewer": None, "reviewed_at": None,
            "decision_reason": None, "created_at": "t", "updated_at": "t"}
    assert store.insert_proposal_if_absent(base, origin="import", db_path=db)
    with pytest.raises(store.ConflictError):
        store.insert_proposal_if_absent({**base, "proposal_id": "AUD-2"}, origin="agent_api", db_path=db)


# ── 추가 계약 (T2·T5·T6 가 의존하는 표면) ─────────────────────────────────────────

def _person(pid="P-1", name="김가상", org="가상원", **kw):
    base = {"person_id": pid, "name": name, "organization": org, "organization_code": "FAKE", "department": "가상부",
            "role": "실장", "status": "active", "verification_status": "candidate", "effective_from": "2026-10-09",
            "aliases": [], "topic_tags": [], "photo_url": "https://cdn.example.test/p.jpg", "photo_path": None,
            "created_at": "t", "updated_at": "t"}
    return {**base, **kw}


def _proposal(pid="AUD-1", **kw):
    base = {"proposal_id": pid, "proposal_type": "new_person", "dedupe_key": f"new_person:{pid}", "priority": "normal",
            "summary": "요약", "rationale": "근거", "proposed_change": {"name": "가"}, "evidence": [{"url": "https://a.test/1"}],
            "conflicts": [], "audit_run_id": "coverage-audit-2026-10-10", "created_by": "auditor", "status": "needs_review",
            "effective_from": None, "reviewer": None, "reviewed_at": None, "decision_reason": None,
            "created_at": "2026-10-10T07:00:00+09:00", "updated_at": "2026-10-10T07:00:00+09:00"}
    return {**base, **kw}


def test_module_import_and_paths_do_not_touch_real_db():
    # 지연 실행: import 만으로는 연결하지 않는다 — _ENSURED 가 비어 있지 않은 경로는 tmp 경로뿐
    assert all(str(store.DEFAULT_DB_PATH.resolve()) != k for k in store._ENSURED)


def test_paths_root_env_and_sync_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("STAKEHOLDER_ROOT", str(tmp_path / "r"))
    assert paths.stakeholder_root() == tmp_path / "r"
    monkeypatch.delenv("STAKEHOLDER_ROOT")
    assert paths.stakeholder_root() == paths.BASE_DIR / "data" / "stakeholder"
    allowed = ["source_manifest.json", "sources/fake_20260903_hong.txt", "sources/raw/a.hwp",
               "monitoring-runs/2026-10-10/raw/person_00_강준혁_bing.xml", "audit-runs/2026-10-10/coverage_audit.md",
               "data/people_registry.json", "scripts/verify_repository.py", "2026-10-08_약가-건보재정_인물관계도.svg",
               "render_relationship_map.py", "README.md", "sources/user_input/monitoring_list (1).png"]
    rejected = ["dashboard-sketches/001.html", "03-Work-Logs/x.md", ".env.example", ".gitignore", "requirements.txt",
                "stakeholder_service.py", "sources/../x", "/etc/passwd", "sources/.DS_Store", "sources//x.txt",
                "sources/x;rm.txt", "other/x.json", "sources\\x.txt", ""]
    assert [p for p in allowed if not paths.is_sync_allowed(p)] == []
    assert [p for p in rejected if paths.is_sync_allowed(p)] == []
    root = tmp_path / "root"
    assert paths.resolve_under(root, "sources/a.txt") == (root / "sources" / "a.txt").resolve()
    with pytest.raises(ValueError):
        paths.resolve_under(root, "../x")
    with pytest.raises(ValueError):
        paths.resolve_under(root, "/abs/x")


def test_create_and_update_validation_and_source_messages(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    with pytest.raises(store.ValidationError):
        store.create_person({"name": "홍", "organization": "보건복지부", "department": "d", "role": "r"}, actor="a", db_path=db)
    with pytest.raises(store.ValidationError):
        store.create_person({"name": "홍길동", "organization": "보건복지부", "department": "d", "role": "r",
                             "status": "deleted"}, actor="a", db_path=db)
    row = store.create_person({"name": "  홍   길동 ", "organization": "보건복지부", "department": "d", "role": "r"},
                              actor="a@x", db_path=db)
    assert row["name"] == "홍 길동" and row["topic_tags"] == ["약가", "건강보험 재정"] and row["organization_code"] == "USER"
    assert row["updated_by"] == "a@x"
    other = store.create_person({"name": "이가상", "organization": "보건복지부", "department": "d", "role": "r"},
                                actor="a@x", db_path=db)
    with pytest.raises(store.ConflictError) as ei:                                    # 이름 바꿔 동명 만들기 → 409
        store.update_person(other["person_id"], {"name": "홍 길동", "organization": "보건복지부", "department": "d",
                                                 "role": "r"}, actor="a@x", db_path=db)
    assert "동일한 이름" in str(ei.value)
    with pytest.raises(store.ValidationError):                                         # 바뀐 필드는 길이 규칙 적용
        store.update_person(other["person_id"], {"name": "이", "organization": "보건복지부", "department": "d",
                                                 "role": "r"}, actor="a@x", db_path=db)
    with pytest.raises(store.NotFoundError):
        store.update_person("P-NOPE", {"name": "가나", "organization": "기관", "department": "d", "role": "r"},
                            actor="a", db_path=db)


def test_status_photo_and_list_people_order(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    store.insert_person_if_absent(_person("P-B", "나가상", "나기관"), origin="import", db_path=db)
    store.insert_person_if_absent(_person("P-A", "가가상", "가기관", aliases=["별칭X"]), origin="import", db_path=db)
    row = store.set_person_status("P-A", "paused", actor="admin@x", db_path=db)
    assert row["status"] == "paused" and row["effective_from"] == "2026-10-13"
    with pytest.raises(store.ValidationError):
        store.set_person_status("P-A", "gone", actor="admin@x", db_path=db)
    assert [p["person_id"] for p in store.list_people(db_path=db)] == ["P-B", "P-A"]     # active 우선
    assert [p["person_id"] for p in store.list_people(status="paused", db_path=db)] == ["P-A"]
    assert [p["person_id"] for p in store.list_people(q="별칭", db_path=db)] == ["P-A"]
    row = store.set_person_photo("P-B", "photos/P-B-ab.webp", actor="admin@x", db_path=db)
    assert row["photo_path"] == "photos/P-B-ab.webp" and row["photo_url"] == "https://cdn.example.test/p.jpg"
    assert store.set_person_photo("P-B", None, actor="admin@x", db_path=db)["photo_path"] is None
    assert store.set_person_importance("나가상", 5, {"score_total": 95}, db_path=db) == 1
    p = store.get_person("P-B", db_path=db)
    assert p["importance_score"] == 5 and p["importance"] == {"score_total": 95}
    assert store.get_person("P-NONE", db_path=db) is None


def test_insert_person_if_absent_rejects_bad_enum_instead_of_silently_ignoring(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(store.ValidationError):
        store.insert_person_if_absent(_person(status="retired"), origin="import", db_path=db)
    with pytest.raises(store.ValidationError):
        store.insert_person_if_absent(_person(), origin="agent_api", db_path=db)
    # 다른 id·같은 (이름, 기관) 은 기존 행 보존(False)
    assert store.insert_person_if_absent(_person(), origin="import", db_path=db) is True
    assert store.insert_person_if_absent(_person("P-OTHER"), origin="import", db_path=db) is False


def test_conn_level_helpers_share_caller_transaction_and_roll_back(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(RuntimeError):
        with store.transaction(db) as conn:
            store.insert_person_row(conn, _person(), origin="audit")
            store.insert_source_domain_row(conn, {"source_id": "SRC-1", "name": "가", "domain": "a.test",
                                                  "source_group": "official", "status": "approved",
                                                  "effective_from": "2026-10-13"}, origin="audit")
            store.insert_role_history_row(conn, {"person_id": "P-1", "role": "국장", "status": "scheduled",
                                                 "effective_from": "2026-10-13", "evidence": [{"url": "u"}]})
            assert store.get_person("P-1", conn=conn)["name"] == "김가상"
            raise RuntimeError("부수 행 롤백")
    assert store.counts(db_path=db)["sv_person"] == 0 and store.counts(db_path=db)["sv_source_domain"] == 0
    with store.transaction(db) as conn:
        store.insert_person_row(conn, _person(), origin="audit")
        hid = store.insert_role_history_row(conn, {"person_id": "P-1", "role": "국장", "status": "scheduled",
                                                   "effective_from": "2026-10-13", "evidence": [{"url": "u"}]})
        with pytest.raises(store.ConflictError):
            store.insert_person_row(conn, _person("P-2"), origin="audit")             # 같은 기관 동명
    hist = store.list_role_history("P-1", db_path=db)
    assert [h["id"] for h in hist] == [hid] and hist[0]["evidence"] == [{"url": "u"}]
    assert store.list_role_history("P-1", status="applied", db_path=db) == []


def test_source_domains_insert_only_and_strict_row(tmp_path):
    db = _db(tmp_path)
    s = {"source_id": "SRC-HIRA", "name": "심평원", "domain": "hira.or.kr", "source_group": "official",
         "status": "approved", "effective_from": "2026-10-09"}
    assert store.insert_source_domain_if_absent(s, origin="import", db_path=db) is True
    assert store.insert_source_domain_if_absent({**s, "name": "바뀜"}, origin="import", db_path=db) is False
    assert store.insert_source_domain_if_absent({**s, "source_id": "SRC-X"}, origin="import", db_path=db) is False
    with pytest.raises(store.ValidationError):
        store.insert_source_domain_if_absent({**s, "source_id": "SRC-Y", "domain": "y.test", "source_group": "blog"},
                                             origin="import", db_path=db)
    with store.transaction(db) as conn:
        with pytest.raises(store.ConflictError):
            store.insert_source_domain_row(conn, {**s, "source_id": "SRC-Z"}, origin="audit")
    items = store.list_source_domains(db_path=db)
    assert [d["name"] for d in items] == ["심평원"] and items[0]["origin"] == "import"


def test_proposals_listing_events_and_find_open(tmp_path):
    db = _db(tmp_path)
    store.insert_proposal_if_absent(_proposal("AUD-LOW", priority="low"), origin="import", db_path=db)
    store.insert_proposal_if_absent(_proposal("AUD-URG", priority="urgent", audit_run_id="other"), origin="import", db_path=db)
    store.insert_proposal_if_absent(_proposal("AUD-DONE", priority="urgent", status="rejected"), origin="import", db_path=db)
    assert store.insert_proposal_if_absent(_proposal("AUD-LOW", summary="바뀜"), origin="import", db_path=db) is False
    assert store.get_proposal("AUD-LOW", db_path=db)["summary"] == "요약"
    assert [p["proposal_id"] for p in store.list_proposals(db_path=db)] == ["AUD-URG", "AUD-LOW", "AUD-DONE"]
    assert [p["proposal_id"] for p in store.list_proposals(status="rejected", db_path=db)] == ["AUD-DONE"]
    assert [p["proposal_id"] for p in store.list_proposals(audit_run_id="other", db_path=db)] == ["AUD-URG"]
    assert store.list_proposals(type="role_change", db_path=db) == []
    p = store.get_proposal("AUD-LOW", db_path=db)
    assert p["proposed_change"] == {"name": "가"} and p["evidence"] == [{"url": "https://a.test/1"}] and p["conflicts"] == []
    assert p["origin"] == "import"
    assert store.find_open_proposal("new_person:AUD-LOW", db_path=db)["proposal_id"] == "AUD-LOW"
    assert store.find_open_proposal("new_person:AUD-DONE", db_path=db) is None
    store.add_proposal_event("AUD-LOW", actor="admin@x", event="approved", detail="ok", db_path=db)
    with store.transaction(db) as conn:
        store.add_proposal_event_row(conn, "AUD-LOW", actor="sync", event="applied")
    assert [e["event"] for e in store.list_proposal_events("AUD-LOW", db_path=db)] == ["approved", "applied"]
    with pytest.raises(store.ValidationError):
        store.insert_proposal_if_absent(_proposal("AUD-BAD", priority="medium"), origin="import", db_path=db)
    # 닫힌 제안의 dedupe_key 는 다시 열 수 있다 (부분 인덱스)
    assert store.insert_proposal_if_absent(_proposal("AUD-NEW", dedupe_key="new_person:AUD-DONE"), origin="agent_api",
                                           db_path=db)


def _mon(run_id="stakeholder-daily-2026-10-10", run_date="2026-10-10", **kw):
    return {"schema_version": "1.1", "run_id": run_id, "run_date_kst": run_date,
            "executed_at_kst": f"{run_date}T07:02:50+09:00", "window_start_kst": "a", "window_end_kst": "b",
            "run_status": "no_change",
            "scope": {"scope_snapshot_id": "snap", "people_registry_sha256": "p" * 64, "source_registry_sha256": "s" * 64,
                      "active_people": [{"person_id": f"P-{i}"} for i in range(11)],      # schema 1.1 = 목록
                      "approved_sources": [{"domain": f"d{i}.test"} for i in range(32)]},
            "search_summary": {"new_independent_quotes": 2}, "source_saturation": {"status": "met"}, **kw}


def test_runs_upsert_sha_gate_and_listing(tmp_path):
    db = _db(tmp_path)
    assert store.upsert_monitoring_run(_mon(), md_path="monitoring-runs/2026-10-10/daily_monitoring.md",
                                       raw_dir="monitoring-runs/2026-10-10/raw", db_path=db) == "inserted"
    assert store.upsert_monitoring_run(_mon(), db_path=db) == "unchanged"
    raw = json.dumps(_mon(run_status="changed"), ensure_ascii=False).encode()
    assert store.upsert_monitoring_run(raw, db_path=db) == "updated"                  # bytes 입력 = 파일 원문
    legacy = _mon("stakeholder-daily-2026-10-11", "2026-10-11")
    legacy["scope"] = {"active_people": 11, "approved_sources": 32}                     # schema 1.0 = 정수
    store.upsert_monitoring_run(legacy, db_path=db)
    runs = store.list_runs("monitoring", db_path=db)
    assert [r["run_id"] for r in runs] == ["stakeholder-daily-2026-10-11", "stakeholder-daily-2026-10-10"]
    assert runs[0]["active_people"] == 11 and runs[0]["approved_domains"] == 32
    r = runs[1]
    assert "payload_json" not in r and r["run_status"] == "changed" and r["active_people"] == 11
    assert r["approved_domains"] == 32 and r["new_quotes"] == 2 and r["saturation_status"] == "met"
    assert r["people_registry_sha256"] == "p" * 64 and r["md_path"] == "monitoring-runs/2026-10-10/daily_monitoring.md"
    full = store.get_run("monitoring", "stakeholder-daily-2026-10-10", db_path=db)
    assert full["payload"]["run_status"] == "changed"
    assert store.get_run("monitoring", "nope", db_path=db) is None

    audit_old = {"audit_run_id": "coverage-audit-2026-10-09", "run_date_kst": "2026-10-09", "run_status": "degraded",
                 "proposal_counts": {"created": 1}}
    audit_new = {"audit_run_id": "coverage-audit-2026-10-10", "run_date_kst": "2026-10-10", "run_status": "degraded",
                 "executed_at_kst": "e", "completed_at_kst": "c", "claim": "주장", "why_this_matters": "의미",
                 "proposal_summary": {"created": 5}}
    assert store.upsert_audit_run(audit_old, db_path=db) == "inserted"
    assert store.upsert_audit_run(audit_new, db_path=db) == "inserted"
    a = store.list_runs("audit", limit=5, db_path=db)
    assert [x["audit_run_id"] for x in a] == ["coverage-audit-2026-10-10", "coverage-audit-2026-10-09"]
    assert a[0]["claim"] == "주장" and a[0]["proposals_created"] == 5 and a[1]["proposals_created"] == 1
    assert a[1]["claim"] is None
    with pytest.raises(store.ValidationError):
        store.upsert_monitoring_run({"run_status": "x"}, db_path=db)
    with pytest.raises(ValueError):
        store.list_runs("weekly", db_path=db)


def test_manifest_sources_artifacts_lossless_and_integrity(tmp_path):
    db = _db(tmp_path)
    src = {"source_id": "SRC-FAKE-1", "title": "가상 기사", "url": "https://example.test/news/1", "source_type": "news",
           "evidence_grade": "C-quote", "access_status": "acquired", "local_path": "sources/raw/fake.html",
           "sha256": "a" * 64, "text_path": "sources/fake.txt", "text_sha256": "b" * 64,
           "last_live_check": {"checked_at": "t", "http_status": 200}, "evidence_facets": ["quote"],
           "video_limitation": "영상 없음", "future_field": {"x": 1}}
    assert store.upsert_source(src, manifest_sha256="m1", db_path=db) == "inserted"
    s = store.get_source("SRC-FAKE-1", db_path=db)
    assert s["integrity_status"] == "unchecked" and s["last_live_check"]["http_status"] == 200
    assert s["evidence_facets"] == ["quote"] and s["video_limitation"] == "영상 없음"
    assert s["raw"] == src                                                              # 미지 키까지 보존
    store.set_source_integrity("SRC-FAKE-1", "mismatch", db_path=db)
    assert store.upsert_source({**src, "title": "제목2"}, manifest_sha256="m2", db_path=db) == "updated"
    s = store.get_source("SRC-FAKE-1", db_path=db)
    assert s["title"] == "제목2" and s["integrity_status"] == "mismatch"                 # None 이면 판정 유지
    store.upsert_source(src, integrity_status="verified", integrity_checked_at="now", db_path=db)
    assert store.list_sources(db_path=db)[0]["integrity_status"] == "verified"
    with pytest.raises(store.ValidationError):
        store.set_source_integrity("SRC-FAKE-1", "ok", db_path=db)
    art = {"path": "2026-10-08_관계도.svg", "name": "관계도", "description": "d", "sha256": "c" * 64, "bytes": 10}
    assert store.upsert_artifact(art, db_path=db) == "inserted"
    store.set_artifact_integrity("2026-10-08_관계도.svg", "verified", db_path=db)
    arts = store.list_artifacts(db_path=db)
    assert arts[0]["path"] == "2026-10-08_관계도.svg" and arts[0]["integrity_status"] == "verified" and arts[0]["bytes"] == 10


def test_meta_roundtrip_and_sync_index(tmp_path):
    db = _db(tmp_path)
    store.set_meta("project", "가상", db_path=db)
    store.set_meta("integrity_summary_json", {"mismatch": 1}, db_path=db)
    assert store.get_meta("project", db_path=db) == "가상"
    assert json.loads(store.get_meta("integrity_summary_json", db_path=db)) == {"mismatch": 1}
    assert store.get_meta("absent", "기본", db_path=db) == "기본"
    store.upsert_sync_file("sources/a.txt", "blob1", sha256="x", bytes=3, db_path=db)
    store.upsert_sync_file("sources/a.txt", "blob2", sha256="y", bytes=4, db_path=db)
    idx = store.get_sync_index(db_path=db)
    assert idx["sources/a.txt"]["blob_sha"] == "blob2" and idx["sources/a.txt"]["bytes"] == 4


def _canonical(sha="sha1"):
    events = [{"event_id": "2026-09-03-가상-토론", "event_date": "2026-09-03", "title": "가상 토론", "context": "맥락",
               "topics": ["price_reform", "access"], "speakers": ["홍길동"], "grades": ["C-quote"]}]
    statements = [
        {"statement_id": "st1", "event_id": "2026-09-03-가상-토론", "topic": "price_reform", "speaker_name": "홍길동",
         "speaker_role_at_event": "가상과장", "multi_speaker": False, "summary_text": "요약", "analysis_text": "해석",
         "limitation_text": "한계", "evidence_grade": "C-quote/C-summary",
         "locators": [{"path": "sources/fake.txt", "start": 3, "end": 5}], "source_url": "https://example.test/1",
         "headline_quote_id": "q1"},
        {"statement_id": "st2", "event_id": "2026-09-03-가상-토론", "topic": "access", "speaker_name": "홍길동",
         "speaker_role_at_event": "가상과장", "multi_speaker": True, "evidence_grade": "C-quote", "locators": []},
    ]
    quotes = [{"quote_id": "q1", "event_id": "2026-09-03-가상-토론", "speaker_name": "홍길동",
               "quote_text": "“가 나” 다", "segments": ["가 나"], "statement_ids": ["st1", "st2"],
               "topics": ["price_reform", "access"], "locator": {"path": "sources/fake.txt", "start": 3, "end": 5},
               "locators": [{"path": "sources/fake.txt", "start": 3, "end": 5}, {"path": "sources/fake.txt", "start": 1, "end": 1}],
               "locator_match": "range", "evidence_grade": "C-quote"}]
    return events, statements, quotes


def test_replace_canonical_requires_transaction_and_keeps_other_origins(tmp_path):
    db = _db(tmp_path)
    events, statements, quotes = _canonical()
    conn = store.connect(db); store.ensure_tables(db)
    with pytest.raises(RuntimeError):
        store.replace_canonical(events, statements, quotes, "sha1", conn)               # 트랜잭션 밖 호출 금지
    conn.close()
    with store.transaction(db) as conn:
        conn.execute("INSERT INTO sv_event (event_id, event_date, title, origin, created_at, updated_at) "
                     "VALUES ('manual-1','2026-01-01','수동','dashboard','t','t')")
        out = store.replace_canonical(events, statements, quotes, "sha1", conn)
    assert out == {"events": 1, "statements": 2, "quotes": 1}
    with store.transaction(db) as conn:                                                # 재파싱 = 교체(누적 아님)
        store.replace_canonical(events, statements, quotes, "sha2", conn)
    c = store.counts(db_path=db)
    assert c["sv_event"] == 2 and c["sv_statement"] == 2 and c["sv_quote"] == 1
    ev = store.list_events(db_path=db)
    assert [e["event_id"] for e in ev] == ["2026-09-03-가상-토론", "manual-1"]
    assert ev[0]["topics"] == ["price_reform", "access"] and ev[0]["canonical_sha256"] == "sha2"
    assert [e["event_id"] for e in store.list_events(topic="access", db_path=db)] == ["2026-09-03-가상-토론"]
    st = store.get_statement("st1", db_path=db)
    assert st["locators"] == [{"path": "sources/fake.txt", "start": 3, "end": 5}] and st["multi_speaker"] is False
    assert st["event_title"] == "가상 토론" and st["event_date"] == "2026-09-03"
    q = st["quotes"][0]
    assert q["segments"] == ["가 나"] and q["statement_ids"] == ["st1", "st2"] and q["locator"] == "sources/fake.txt:3-5"
    assert q["quote_norm"] == "가나다" and q["locator_match"] == "range"
    assert q["locators"][1] == {"path": "sources/fake.txt", "start": 1, "end": 1}
    assert [s["statement_id"] for s in store.list_statements(topic="access", db_path=db)] == ["st2"]
    assert [s["statement_id"] for s in store.list_statements(grade="C-summary", db_path=db)] == ["st1"]
    assert [s["statement_id"] for s in store.list_statements(q="해석", db_path=db)] == ["st1"]
    assert len(store.list_quotes(statement_id="st2", db_path=db)) == 1
    assert store.get_statement("nope", db_path=db) is None


def test_registry_snapshot_scopes_active_effective_public_fields(tmp_path):
    db = _db(tmp_path)
    store.insert_person_if_absent(_person("P-1", photo_path="photos/x.webp"), origin="import", db_path=db)
    store.insert_person_if_absent(_person("P-2", "미래인", effective_from="2026-10-13"), origin="import", db_path=db)
    store.insert_person_if_absent(_person("P-3", "중지인", status="paused"), origin="import", db_path=db)
    store.insert_source_domain_if_absent({"source_id": "SRC-1", "name": "가", "domain": "a.test", "source_group": "official",
                                          "status": "approved", "effective_from": "2026-10-09"}, origin="import", db_path=db)
    snap = store.registry_snapshot(today=date(2026, 10, 12), db_path=db)
    assert [p["person_id"] for p in snap["people"]] == ["P-1"] and "photo_path" not in snap["people"][0]
    assert "photo_url" not in snap["people"][0] and [s["domain"] for s in snap["sources"]] == ["a.test"]
    assert snap["as_of"] == "2026-10-12" and len(snap["people_sha256"]) == 64
    store.set_person_photo("P-1", "photos/y.webp", actor="a", db_path=db)              # 사진은 스콥 해시에 무관
    assert store.registry_snapshot(today=date(2026, 10, 12), db_path=db)["people_sha256"] == snap["people_sha256"]
    nxt = store.registry_snapshot(today=date(2026, 10, 13), db_path=db)
    assert nxt["people_sha256"] != snap["people_sha256"] and len(nxt["people"]) == 2


def test_public_functions_all_take_db_path():
    import inspect
    missing = []
    for name, fn in inspect.getmembers(store, inspect.isfunction):
        if name.startswith("_") or fn.__module__ != store.__name__:
            continue
        params = inspect.signature(fn).parameters
        if "db_path" not in params and "conn" not in params:
            missing.append(name)
    # 순수 유틸(시간·행 변환)만 예외
    assert set(missing) <= {"now_kst", "today_kst", "tomorrow_kst", "person_row_to_dict", "proposal_row_to_dict",
                            "norm_quote", "validate_person_input"}, missing


def test_concurrent_open_dedupe_admits_exactly_one(tmp_path):
    """소스의 RLock+flock 대체 확인 — 동시에 같은 dedupe_key 로 8건 넣으면 1건만 들어가고 나머지는 409."""
    import threading
    db = _db(tmp_path)
    store.ensure_tables(db)
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        try:
            store.insert_proposal_if_absent(_proposal(f"AUD-T{i}", dedupe_key="new_person:same"), origin="agent_api",
                                            db_path=db)
            out = "ok"
        except store.ConflictError:
            out = "conflict"
        except Exception as exc:  # noqa: BLE001 — 잠금 오류 등은 실패로 드러나야 한다
            out = f"error:{exc}"
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict"] * 7 + ["ok"]
    assert store.counts(db_path=db)["sv_audit_proposal"] == 1
