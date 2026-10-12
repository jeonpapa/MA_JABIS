"""T5 — 소스 repo git-sync(blob sha 대조·허용목록·fail-closed) + 스냅샷 멱등 import + 무결성 + CLI.

네트워크 없음: source_dir 모드(로컬 디렉터리를 원격으로 간주) 또는 fetch_tree/fetch_blob 주입.
DB 는 tmp_path 만 쓴다.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from agents.stakeholder import approval, store, sync

FIX = Path(__file__).parent / "fixtures" / "stakeholder_src_mini"
TODAY = date(2026, 10, 12)


def _run(tmp_path, src=FIX, today=TODAY):
    root, db = tmp_path / "root", tmp_path / "s.db"
    return sync.sync(root, db, source_dir=src, today=today), root, db


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@pytest.fixture
def fixed_today(monkeypatch):
    """approve() 의 효력일(익일)을 2026-10-13 으로 고정."""
    monkeypatch.setattr(store, "today_kst", lambda: TODAY)


# ── 계획 Step 1 의 10개 ────────────────────────────────────────────────────────────

def test_first_sync_mirrors_allowed_files_only(tmp_path):
    res, root, db = _run(tmp_path)
    assert res["ok"] is True and res["errors"] == []
    assert (root / "sources" / "fake_20260903_hong.txt").exists() and (root / "source_manifest.json").exists()
    assert not (root / "dashboard-sketches").exists() and not (root / "03-Work-Logs").exists() \
        and not (root / ".env.example").exists() and not (root / "_build.py").exists()
    assert res["counts"]["downloaded"] == res["counts"]["listed"] and res["counts"]["rejected"] >= 3
    # 바이트 단위 미러
    for rel in ("source_manifest.json", "sources/user_input/list.png", "monitoring-runs/2026-10-10/raw/00_example_bing.xml"):
        assert (root / rel).read_bytes() == (FIX / rel).read_bytes()
    assert len(store.get_sync_index(db_path=db)) == res["counts"]["listed"]


def test_second_sync_is_noop_by_blob_sha(tmp_path):
    _, root, db = _run(tmp_path)
    res2 = sync.sync(root, db, source_dir=FIX, today=TODAY)
    assert res2["counts"]["downloaded"] == 0 and res2["counts"]["canonical_reparsed"] == 0 and res2["ok"]
    assert res2["counts"]["unchanged"] == res2["counts"]["listed"]
    assert res2["counts"]["changes"] == 0                               # 두 번째 import: 변경 0


def test_import_counts_match_fixture(tmp_path):
    _, root, db = _run(tmp_path)
    c = store.counts(db_path=db)
    m = json.loads((FIX / "source_manifest.json").read_text(encoding="utf-8"))
    assert c["sv_source"] == len(m["sources"]) and c["sv_artifact"] == len(m["artifacts"])
    assert c["sv_person"] == 3 and c["sv_source_domain"] == 4 and c["sv_audit_proposal"] == 3
    assert c["sv_monitoring_run"] == 2 and c["sv_audit_run"] == 1 and c["sv_event"] == 2
    assert all(s["integrity_status"] in ("verified", "no_local") for s in store.list_sources(db_path=db))
    run = store.list_runs("audit", db_path=db)[0]
    assert run["claim"] and run["run_status"] == "degraded"


def test_integrity_mismatch_is_flagged_not_fatal(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    (src / "sources" / "fake_20260903_hong.txt").write_text("변조", encoding="utf-8")
    res, root, db = _run(tmp_path, src=src)
    st = {s["text_path"]: s["integrity_status"] for s in store.list_sources(db_path=db)}
    assert st["sources/fake_20260903_hong.txt"] == "mismatch" and res["ok"] is True
    summary = store.get_meta("integrity_summary_json", db_path=db)
    assert summary and "mismatch" in summary
    assert json.loads(summary)["sources"]["mismatch"] == 1


def test_resync_never_overwrites_operator_decision(tmp_path, fixed_today):
    _, root, db = _run(tmp_path)
    pid = [p for p in store.list_proposals(db_path=db) if p["proposal_type"] == "role_change"][0]["proposal_id"]
    approval.approve(pid, reviewer="admin@x", reason="확인", db_path=db)
    store.set_person_status([p for p in store.list_people(db_path=db)][0]["person_id"], "paused", actor="admin@x",
                            db_path=db)
    before = {p["person_id"]: p for p in store.list_people(db_path=db)}
    res = sync.sync(root, db, source_dir=FIX, today=TODAY)
    assert store.get_proposal(pid, db_path=db)["status"] == "scheduled"
    assert store.get_proposal(pid, db_path=db)["reviewer"] == "admin@x"
    assert {p["person_id"]: p for p in store.list_people(db_path=db)} == before
    assert res["counts"]["registry_new"] == 0 and res["counts"]["registry_existing"] == 10


def test_apply_scheduled_runs_inside_sync(tmp_path, fixed_today):
    _, root, db = _run(tmp_path)
    pid = [p for p in store.list_proposals(db_path=db) if p["proposal_type"] == "role_change"][0]["proposal_id"]
    approval.approve(pid, reviewer="admin@x", reason="확인", db_path=db)          # effective 2026-10-13
    res = sync.sync(root, db, source_dir=FIX, today=date(2026, 10, 13))
    assert res["counts"]["scheduled_applied"] == 1 and store.get_proposal(pid, db_path=db)["status"] == "applied"


def test_canonical_reparse_only_when_sha_changes_and_keeps_dashboard_rows(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    _, root, db = _run(tmp_path, src=src)
    md = src / "2026-10-08_주제별_인물발언_타임라인_정본.md"
    md.write_text(md.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    assert res["counts"]["canonical_reparsed"] == 1 and store.counts(db_path=db)["sv_event"] == 2
    assert res["counts"]["downloaded"] == 1


def test_parser_failure_keeps_previous_rows_and_reports_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    _, root, db = _run(tmp_path, src=src)
    before = store.counts(db_path=db)
    sha_before = store.get_meta("canonical_timeline_sha256", db_path=db)
    md = src / "2026-10-08_주제별_인물발언_타임라인_정본.md"
    md.write_text(md.read_text(encoding="utf-8") + "\n#", encoding="utf-8")
    monkeypatch.setattr(sync.canonical_parse, "parse_timeline",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("깨짐")))
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    assert res["ok"] is False and any("깨짐" in e for e in res["errors"]) and store.counts(db_path=db)["sv_quote"] >= 1
    after = store.counts(db_path=db)
    assert all(after[t] == before[t] for t in ("sv_event", "sv_statement", "sv_quote"))
    assert store.get_meta("canonical_timeline_sha256", db_path=db) == sha_before     # 다음 실행이 다시 시도


def test_production_without_token_is_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "_require_auth", lambda: True)
    monkeypatch.delenv("STAKEHOLDER_SYNC_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    called = []
    res = sync.sync(tmp_path / "root", tmp_path / "s.db", today=TODAY,
                    fetch_tree=lambda *a: called.append(a) or {"tree": []})
    assert res["ok"] is False and res["error_class"] == "config" and res["error"].startswith("auth required")
    assert res["errors"] and res["errors"][0].startswith("auth required")
    assert called == [] and not (tmp_path / "s.db").exists()               # 원격 호출·DB 생성 없음


def test_remote_mode_uses_injected_fetchers(tmp_path):
    manifest = (FIX / "source_manifest.json").read_bytes()
    tree = {"sha": "T", "truncated": False,
            "tree": [{"path": "source_manifest.json", "type": "blob", "sha": _blob_sha(manifest), "size": len(manifest)},
                     {"path": "sources", "type": "tree", "sha": "d"},
                     {"path": "dashboard-sketches/x.html", "type": "blob", "sha": "b", "size": 10},
                     {"path": "sources/../x", "type": "blob", "sha": "c", "size": 1}]}
    seen = []

    def fetch_blob(repo, path, branch, token):
        seen.append((repo, path, branch, token))
        return manifest

    res = sync.sync(tmp_path / "root", tmp_path / "s.db", repo="o/r", token="t", today=TODAY,
                    fetch_tree=lambda repo, branch, token: tree, fetch_blob=fetch_blob)
    assert res["counts"]["listed"] == 1 and res["counts"]["rejected"] == 2
    assert (tmp_path / "root" / "source_manifest.json").exists()
    assert seen == [("o/r", "source_manifest.json", "main", "t")]
    assert store.get_meta("last_tree_sha", db_path=tmp_path / "s.db") == "T"


# ── 추가: 원격 안전장치 ─────────────────────────────────────────────────────────────

def test_remote_blob_sha_mismatch_is_not_written(tmp_path):
    good = (FIX / "source_manifest.json").read_bytes()
    tree = {"sha": "T", "tree": [{"path": "source_manifest.json", "type": "blob", "sha": _blob_sha(good),
                                  "size": len(good)}]}
    res = sync.sync(tmp_path / "root", tmp_path / "s.db", repo="o/r", token="t", today=TODAY,
                    fetch_tree=lambda *a: tree, fetch_blob=lambda *a: good + b" ")
    assert res["ok"] is False and res["counts"]["downloaded"] == 0 and res["counts"]["fetch_failed"] == 1
    assert any("blob sha" in e for e in res["errors"])
    assert not (tmp_path / "root" / "source_manifest.json").exists()
    assert store.get_sync_index(db_path=tmp_path / "s.db") == {}


def test_remote_oversized_blob_rejected_and_total_cap_defers(tmp_path, monkeypatch):
    a, b = b"x" * 100, b"y" * 100
    tree = {"sha": "T", "tree": [
        {"path": "sources/a.txt", "type": "blob", "sha": _blob_sha(a), "size": 100},
        {"path": "sources/b.txt", "type": "blob", "sha": _blob_sha(b), "size": 100},
        {"path": "sources/huge.bin", "type": "blob", "sha": "h", "size": sync.paths.MAX_BLOB_BYTES + 1}]}
    blobs = {"sources/a.txt": a, "sources/b.txt": b}
    monkeypatch.setattr(sync, "MAX_SYNC_BYTES", 150)
    res = sync.sync(tmp_path / "root", tmp_path / "s.db", repo="o/r", token="t", today=TODAY,
                    fetch_tree=lambda *a: tree, fetch_blob=lambda repo, path, branch, token: blobs[path])
    c = res["counts"]
    assert c["listed"] == 2 and c["rejected"] == 1 and c["downloaded"] == 1 and c["deferred"] == 1
    assert (tmp_path / "root" / "sources" / "a.txt").exists() and not (tmp_path / "root" / "sources" / "b.txt").exists()


@pytest.mark.parametrize("code,cls", [(401, "config"), (404, "config"), (502, "transient")])
def test_remote_tree_http_error_classified(tmp_path, code, cls):
    def boom(*a):
        raise urllib.error.HTTPError("https://api.github.com/x", code, "err", {}, io.BytesIO(b""))

    res = sync.sync(tmp_path / "root", tmp_path / "s.db", repo="o/r", token="t", today=TODAY, fetch_tree=boom)
    assert res["ok"] is False and res["error_class"] == cls and str(code) in res["error"]


def test_token_from_env_and_repo_default(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "_require_auth", lambda: True)
    monkeypatch.delenv("STAKEHOLDER_SYNC_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "gh")
    monkeypatch.delenv("STAKEHOLDER_SYNC_REPO", raising=False)
    seen = []
    res = sync.sync(tmp_path / "root", tmp_path / "s.db", today=TODAY,
                    fetch_tree=lambda repo, branch, token: seen.append((repo, branch, token)) or {"sha": "T", "tree": []})
    assert seen == [(sync.DEFAULT_REPO, "main", "gh")]
    monkeypatch.setenv("STAKEHOLDER_SYNC_TOKEN", "sv")
    sync.sync(tmp_path / "root", tmp_path / "s.db", today=TODAY,
              fetch_tree=lambda repo, branch, token: seen.append((repo, branch, token)) or {"sha": "T", "tree": []})
    assert seen[-1][2] == "sv"
    assert res["counts"]["listed"] == 0


def test_default_http_request_headers(monkeypatch):
    captured = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        captured.append((req, timeout))
        return _Resp(b'{"sha": "T", "tree": []}')

    monkeypatch.setattr(sync.urllib.request, "urlopen", fake_urlopen)
    assert sync.fetch_tree_github("o/r", "main", "tok") == {"sha": "T", "tree": []}
    sync.fetch_blob_github("o/r", "sources/한글 파일.txt", "main", "tok")
    (t_req, t_to), (b_req, _) = captured
    assert t_req.full_url == "https://api.github.com/repos/o/r/git/trees/main?recursive=1" and t_to == 30
    assert t_req.get_header("Authorization") == "Bearer tok"
    assert t_req.get_header("Accept") == "application/vnd.github+json"
    assert t_req.get_header("X-github-api-version") == "2022-11-28"
    assert b_req.get_header("Accept") == "application/vnd.github.raw"
    assert b_req.full_url.startswith("https://api.github.com/repos/o/r/contents/sources/")
    assert "%ED%95%9C" in b_req.full_url and b_req.full_url.endswith("?ref=main")


# ── 추가: 미러 의미론 ──────────────────────────────────────────────────────────────

def test_removed_remote_file_is_kept_as_orphan(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    _, root, db = _run(tmp_path, src=src)
    (src / "sources" / "raw" / "fake_20260619_rwe.html").unlink()
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    assert res["counts"]["orphaned"] == 1 and (root / "sources" / "raw" / "fake_20260619_rwe.html").exists()


def test_locally_deleted_mirror_file_is_refetched(tmp_path):
    _, root, db = _run(tmp_path)
    (root / "sources" / "fake_20260903_hong.txt").unlink()
    res = sync.sync(root, db, source_dir=FIX, today=TODAY)
    assert res["counts"]["downloaded"] == 1 and (root / "sources" / "fake_20260903_hong.txt").exists()


def test_tampered_mirror_file_is_healed(tmp_path):
    _, root, db = _run(tmp_path)
    f = root / "sources" / "fake_20260903_hong.txt"
    orig = f.read_bytes()
    f.write_bytes(bytes([orig[0] ^ 1]) + orig[1:])                       # 같은 크기, 다른 내용
    res = sync.sync(root, db, source_dir=FIX, today=TODAY)
    assert res["counts"]["downloaded"] == 1 and f.read_bytes() == orig
    assert {s["source_id"]: s["integrity_status"] for s in store.list_sources(db_path=db)}[
        "SRC-FAKE-20260903-HONG"] == "verified"


def test_dry_run_writes_nothing(tmp_path):
    root, db = tmp_path / "root", tmp_path / "s.db"
    res = sync.sync(root, db, source_dir=FIX, today=TODAY, dry_run=True)
    assert res["ok"] and res["dry_run"] is True and res["counts"]["would_download"] == res["counts"]["listed"] > 0
    assert not root.exists() and not db.exists()


def test_concurrent_sync_is_refused(tmp_path):
    root, db = tmp_path / "root", tmp_path / "s.db"
    with sync._sync_lock(root) as got:
        assert got
        res = sync.sync(root, db, source_dir=FIX, today=TODAY)
    assert res["ok"] is False and res["error_class"] == "transient" and "already running" in res["error"]
    assert sync.sync(root, db, source_dir=FIX, today=TODAY)["ok"]


# ── 추가: import 결합 ──────────────────────────────────────────────────────────────

def test_canonical_rows_link_person_and_source(tmp_path):
    _, root, db = _run(tmp_path)
    hong = [s for s in store.list_statements(db_path=db) if s["speaker_name"] == "홍길동"]
    assert hong and all(s["person_id"] == "P-FAKE-GOV-HONG" for s in hong)
    assert all(s["source_ids"] == ["SRC-FAKE-20260903-HONG"] for s in hong)
    multi = [s for s in store.list_statements(db_path=db) if s["multi_speaker"]]
    assert multi and multi[0]["person_id"] is None and multi[0]["source_ids"] == ["SRC-FAKE-20260619-RWE"]
    q = store.list_quotes(person_id="P-FAKE-GOV-HONG", db_path=db)
    assert len(q) == 1 and q[0]["source_id"] == "SRC-FAKE-20260903-HONG" and q[0]["locator_match"] == "range"
    ev = {e["event_date"]: e for e in store.list_events(db_path=db)}
    assert ev["2026-09-03"]["source_ids"] == ["SRC-FAKE-20260903-HONG"]
    assert store.get_meta("canonical_timeline_sha256", db_path=db) == \
        hashlib.sha256((FIX / "2026-10-08_주제별_인물발언_타임라인_정본.md").read_bytes()).hexdigest()


def test_importance_and_meta_imported(tmp_path):
    _, root, db = _run(tmp_path)
    people = {p["name"]: p for p in store.list_people(db_path=db)}
    assert people["홍길동"]["importance_score"] == 5 and people["홍길동"]["importance"]["score_total"] == 95
    assert people["박가상"]["importance"]["work_provisional"] is True
    assert store.get_meta("project", db_path=db) == "합성-Stakeholder-픽스처"
    assert json.loads(store.get_meta("withheld_claims_json", db_path=db))[0]["claim"]
    assert json.loads(store.get_meta("evidence_grade_vocabulary_json", db_path=db))[0] == "A"
    assert store.get_meta("scope_start_kst", db_path=db) == "2025-10-08"
    assert store.get_meta("manifest_sha256", db_path=db) == \
        hashlib.sha256((FIX / "source_manifest.json").read_bytes()).hexdigest()
    assert "저작권" in store.get_meta("copyright_notice", db_path=db)
    assert store.get_meta("last_sync_at", db_path=db)
    arts = {a["path"]: a["integrity_status"] for a in store.list_artifacts(db_path=db)}
    assert set(arts.values()) == {"verified"}


def test_person_link_follows_later_registry_rows(tmp_path):
    """정본 sha 가 그대로여도 나중에 등록된 인물에 발언이 결합된다(파생 컬럼 재결합)."""
    _, root, db = _run(tmp_path)
    with store.transaction(db) as c:
        c.execute("UPDATE sv_statement SET person_id=NULL")
        c.execute("UPDATE sv_quote SET person_id=NULL")
    res = sync.sync(root, db, source_dir=FIX, today=TODAY)
    assert res["counts"]["canonical_reparsed"] == 0 and res["counts"]["person_links"] >= 2
    assert all(s["person_id"] == "P-FAKE-GOV-HONG" for s in store.list_statements(db_path=db)
               if s["speaker_name"] == "홍길동")


def test_registry_proposals_get_import_event_and_dup_dedupe_is_skipped(tmp_path):
    _, root, db = _run(tmp_path)
    ev = store.list_proposal_events("AUD-F1A000000001", db_path=db)
    assert [e["event"] for e in ev] == ["imported"]
    # 같은 dedupe_key 로 다른 id 가 이미 열려 있으면(에이전트 API 경로) import 는 건너뛴다 — 오류 아님
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    data = json.loads((src / "data" / "audit_proposals.json").read_text(encoding="utf-8"))
    dup = dict(data["proposals"][0], proposal_id="AUD-F1A0000000FF")
    data["proposals"].append(dup)
    (src / "data" / "audit_proposals.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    assert res["ok"] is True and res["counts"]["registry_skipped"] == 1
    assert store.get_proposal("AUD-F1A0000000FF", db_path=db) is None and res["warnings"]


def test_pending_role_change_imported_as_scheduled_history(tmp_path):
    """소스 레지스트리에서 이미 승인된(scheduled) 직책 변경 → role_history scheduled 행(proposal_id 결합) → 반영 가능."""
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    props = json.loads((src / "data" / "audit_proposals.json").read_text(encoding="utf-8"))
    rc = next(p for p in props["proposals"] if p["proposal_type"] == "role_change")
    rc.update(status="scheduled", effective_from="2026-10-13", reviewer="조셉 (운영자 인증)",
              reviewed_at="2026-10-12T09:00:00+09:00", decision_reason="공식 인사자료 확인")
    (src / "data" / "audit_proposals.json").write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
    reg = json.loads((src / "data" / "people_registry.json").read_text(encoding="utf-8"))
    park = next(p for p in reg["people"] if p["person_id"] == "P-FAKE-INST-PARK")
    park["pending_role_changes"] = [{"proposal_id": rc["proposal_id"], "organization": "가상원",
                                     "department": "가상관리실", "role": "부장", "effective_from": "2026-10-13",
                                     "evidence": rc["evidence"], "status": "scheduled",
                                     "approved_at": "2026-10-12T09:00:00+09:00"},
                                    {"proposal_id": "AUD-NOT-IN-DB", "role": "x", "effective_from": "2026-10-13",
                                     "status": "scheduled"}]
    (src / "data" / "people_registry.json").write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")

    res, root, db = _run(tmp_path, src=src)
    hist = store.list_role_history("P-FAKE-INST-PARK", db_path=db)
    assert [(h["status"], h["proposal_id"], h["role"]) for h in hist] == [("scheduled", rc["proposal_id"], "부장")]
    assert any("AUD-NOT-IN-DB" in w for w in res["warnings"])               # 결합 제안 없는 대기 행은 건너뜀
    res2 = sync.sync(root, db, source_dir=src, today=date(2026, 10, 13))
    assert res2["counts"]["scheduled_applied"] == 1 and res2["counts"]["scheduled_failed"] == 0
    p = store.get_person("P-FAKE-INST-PARK", db_path=db)
    assert p["role"] == "부장" and p["verification_status"] == "verified"
    assert len(store.list_role_history("P-FAKE-INST-PARK", status="scheduled", db_path=db)) == 0   # 재-sync 중복 없음


def test_bad_run_json_reported_and_others_imported(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    (src / "monitoring-runs" / "2026-10-11" / "daily_monitoring.json").write_text("{깨진", encoding="utf-8")
    res, root, db = _run(tmp_path, src=src)
    assert res["ok"] is False and any("2026-10-11" in e for e in res["errors"])
    assert store.counts(db_path=db)["sv_monitoring_run"] == 1 and store.counts(db_path=db)["sv_person"] == 3


def test_import_snapshot_writes_only_inside_transactions():
    """완료 기준: import_snapshot 의 직접 SQL 쓰기는 모두 `with store.transaction` 블록 안."""
    src = Path(sync.__file__).read_text(encoding="utf-8")
    assert "sqlite3.connect" not in src and "store.connect(" not in src


# ── CLI ───────────────────────────────────────────────────────────────────────────

def test_cli_source_dir_and_dry_run(tmp_path, capsys):
    root, db = tmp_path / "root", tmp_path / "s.db"
    rc = sync.main(["--source-dir", str(FIX), "--root", str(root), "--db", str(db), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "would_download=" in out and not db.exists()
    rc = sync.main(["--source-dir", str(FIX), "--root", str(root), "--db", str(db), "--today", "2026-10-12"])
    out = capsys.readouterr().out
    assert rc == 0 and "listed=" in out and "integrity:" in out and "verified=" in out
    assert store.counts(db_path=db)["sv_person"] == 3


def test_cli_requires_mode(tmp_path, capsys):
    with pytest.raises(SystemExit):
        sync.main(["--root", str(tmp_path / "r"), "--db", str(tmp_path / "s.db")])
