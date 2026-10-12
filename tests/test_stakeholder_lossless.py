"""T10 — 손실 없음 검증(scripts/stakeholder_verify_import.py).

픽스처(합성)는 항상 돈다. 실데이터(소스 repo 체크아웃)는 STAKEHOLDER_SRC_DIR 가 있을 때만 — 임시 root·임시 DB.
네트워크·LLM 없음. 실제 DB 는 쓰지 않는다(conftest 가드).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from agents.stakeholder import approval, store, sync
from scripts import stakeholder_verify_import as v

FIX = Path(__file__).parent / "fixtures" / "stakeholder_src_mini"
TODAY = date(2026, 10, 12)
CANON = "2026-10-08_주제별_인물발언_타임라인_정본.md"


def _sync(tmp_path, src=FIX):
    root, db = tmp_path / "root", tmp_path / "s.db"
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    assert res["ok"], res["errors"]
    return root, db


def _copy_fix(tmp_path) -> Path:
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    return src


def _starting(rep, prefix: str) -> list[str]:
    return [f for f in rep.failures if f.startswith(prefix)]


def _sql(db, sql, args=()):
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(store, "today_kst", lambda: TODAY)


# ── 기본: 픽스처 sync → verify → 실패 0, 수치는 구조에서 ─────────────────────────────

def test_fixture_import_is_lossless(tmp_path):
    root, db = _sync(tmp_path)
    rep = v.verify(root, db, source_dir=FIX)
    assert rep.failures == []
    c = rep.counts
    m = json.loads((FIX / "source_manifest.json").read_text(encoding="utf-8"))
    assert c["people"] == 3 and c["source_domains"] == 4 and c["proposals"] == 3
    assert c["sources"] == len(m["sources"]) and c["artifacts"] == len(m["artifacts"])
    assert c["monitoring_runs"] == 2 and c["audit_runs"] == 1
    assert c["events"] == 2 and c["statements"] == 3
    assert c["quote_blocks_md"] == 2 and c["quote_blocks_excluded"] == 0 and c["quotes_db"] == 1
    assert c["locator_match"]["none"] == 0
    assert c["mirror_mismatch"] == 0 and c["mirror_missing"] == 0 and c["mirrored_files"] == c["sync_index_files"]
    assert c["integrity"]["sources"]["mismatch"] == 0 and c["integrity"]["sources"]["missing"] == 0
    # 근거(basis)가 모든 수치에 붙는다
    for k in ("people", "sources", "events", "statements", "quote_blocks_md", "quotes_db", "mirrored_files"):
        assert rep.basis.get(k), k
    # 허용목록 밖은 비교 대상이 아니라 명시적 제외로 열거된다
    rejected = {e["path"] for e in rep.exclusions["rejected_paths"]}
    assert {".env.example", "03-Work-Logs/x.md", "dashboard-sketches/README.md", "_build.py"} <= rejected


def test_verify_without_source_dir_uses_sync_index(tmp_path):
    root, db = _sync(tmp_path)
    rep = v.verify(root, db)
    assert rep.failures == []
    assert rep.counts["mirrored_files"] is None and rep.counts["sync_index_files"] > 0


# ── verify_repository.py 이식 (문구 유지) ─────────────────────────────────────────

def test_tampered_volume_file_reports_one_sha_mismatch(tmp_path):
    root, db = _sync(tmp_path)
    (root / "sources" / "fake_20260903_hong.txt").write_text("변조", encoding="utf-8")
    rep = v.verify(root, db, source_dir=FIX)
    assert len(_starting(rep, "SHA-256 mismatch: sources/fake_20260903_hong.txt")) == 1
    assert len([f for f in rep.failures if f.startswith("SHA-256 mismatch")]) == 1
    # 같은 변조가 미러·색인·무결성 판정 쪽에서도 잡힌다(다른 문구)
    assert _starting(rep, "mirror 바이트 불일치: sources/fake_20260903_hong.txt")
    assert _starting(rep, "integrity verdict 불일치")


def test_missing_manifest_file_and_artifact_bytes(tmp_path):
    root, db = _sync(tmp_path)
    (root / "sources" / "fake_20260619_rwe.txt").unlink()
    art = root / "scripts" / "verify_repository.py"
    art.write_bytes(art.read_bytes() + b" ")
    rep = v.verify(root, db)
    assert _starting(rep, "missing manifest file: sources/fake_20260619_rwe.txt")
    assert _starting(rep, "artifact SHA-256 mismatch: scripts/verify_repository.py")
    assert _starting(rep, "artifact byte-size mismatch: scripts/verify_repository.py")


def test_invalid_json_on_volume(tmp_path):
    root, db = _sync(tmp_path)
    (root / "monitoring-runs" / "2026-10-10" / "raw" / "search_results.json").write_text("{깨짐", encoding="utf-8")
    rep = v.verify(root, db)
    assert _starting(rep, "invalid JSON: monitoring-runs/2026-10-10/raw/search_results.json")


# ── 정본 인용 ────────────────────────────────────────────────────────────────────

def test_deleted_quote_row_reports_canonical_block_missing_once(tmp_path):
    root, db = _sync(tmp_path)
    _sql(db, "DELETE FROM sv_quote")
    rep = v.verify(root, db, source_dir=FIX)
    # 픽스처의 인용 1개가 두 주제에 교차 표시(블록 2개) — 미반영은 인용 단위 1건(줄 번호 둘 다 표기)
    missing = _starting(rep, "정본 인용 블록 미반영")
    assert len(missing) == 1 and "L21" in missing[0] and "L35" in missing[0]


def test_quote_text_must_be_byte_exact(tmp_path):
    root, db = _sync(tmp_path)
    _sql(db, "UPDATE sv_quote SET quote_text = replace(quote_text, '”며', '” 며')")
    rep = v.verify(root, db)
    assert _starting(rep, "DB 인용이 정본에 없음")
    assert _starting(rep, "정본 인용 블록 원문 불일치")      # 정규화로는 같지만 바이트가 다르다


def test_editorial_blockquote_outside_topics_is_enumerated_exclusion(tmp_path):
    src = _copy_fix(tmp_path)
    md = src / CANON
    text = md.read_text(encoding="utf-8").replace(
        "# 1. 전체 행사 마스터 타임라인", "> 편집 주석: 같은 행사 여러 보도는 하나로 센다.\n\n# 1. 전체 행사 마스터 타임라인", 1)
    md.write_text(text, encoding="utf-8")
    root, db = _sync(tmp_path, src)
    rep = v.verify(root, db, source_dir=src)
    # 정본 MD 를 바꿨으므로 manifest 의 artifact 해시만 어긋난다(소스 상태) — 인용 검사 결과만 본다
    assert not [f for f in rep.failures if "인용" in f]
    assert rep.counts["quote_blocks_excluded"] == 1
    ex = rep.exclusions["excluded_blockquotes"]
    assert len(ex) == 1 and ex[0]["line"] == 8 and ex[0]["text"].startswith("편집 주석")


def test_locator_none_fails_unless_known_source_discrepancy(tmp_path, monkeypatch):
    root, db = _sync(tmp_path)
    _sql(db, "UPDATE sv_quote SET locator_match='none'")
    rep = v.verify(root, db)
    assert len(_starting(rep, "locator_match=none")) == 1

    q = store.list_quotes(db_path=db)[0]
    known = ({"kind": "locator_match_none", "speaker_name": q["speaker_name"],
              "quote_sha256": hashlib.sha256(q["quote_text"].encode("utf-8")).hexdigest(),
              "reason": "테스트용 알려진 소스 결함"},)
    monkeypatch.setattr(v, "KNOWN_SOURCE_DISCREPANCIES", known)
    rep2 = v.verify(root, db)
    assert not _starting(rep2, "locator_match=none")
    assert rep2.counts["quotes_locator_none_known"] == 1 and rep2.counts["quotes_locator_none_unknown"] == 0
    assert rep2.known_discrepancies and rep2.known_discrepancies[0]["speaker_name"] == q["speaker_name"]


def test_unused_known_discrepancy_is_a_note_not_failure(tmp_path):
    root, db = _sync(tmp_path)
    rep = v.verify(root, db)
    assert rep.failures == []
    assert any("알려진 소스 결함" in n for n in rep.notes)          # 실데이터용 항목이 픽스처엔 없다 → 메모


def test_event_and_statement_counts_come_from_md_structure(tmp_path):
    root, db = _sync(tmp_path)
    _sql(db, "DELETE FROM sv_event WHERE event_date='2026-06-19'")
    _sql(db, "DELETE FROM sv_statement WHERE speaker_name='김가상·박가상'")
    rep = v.verify(root, db)
    assert _starting(rep, "정본 행사 수 불일치")
    assert _starting(rep, "정본 발언블록 수 불일치")


# ── 레지스트리 · 런 · 메타 왕복 ────────────────────────────────────────────────────

def test_registry_field_drift_is_failure(tmp_path):
    root, db = _sync(tmp_path)
    pid = store.list_people(db_path=db)[0]["person_id"]
    _sql(db, "UPDATE sv_person SET photo_url='https://x.test/p.jpg' WHERE person_id=?", (pid,))
    _sql(db, "UPDATE sv_audit_proposal SET evidence_json='[]' WHERE rowid=1")
    _sql(db, "DELETE FROM sv_source_domain WHERE rowid=1")
    rep = v.verify(root, db)
    assert any(f.startswith("레지스트리 필드 불일치: people_registry") and "photo_url" in f for f in rep.failures)
    assert any(f.startswith("레지스트리 필드 불일치: audit_proposals") and "evidence" in f for f in rep.failures)
    assert _starting(rep, "레지스트리 레코드 미반영: source_registry")


def test_unmapped_registry_field_is_failure(tmp_path):
    src = _copy_fix(tmp_path)
    f = src / "data" / "source_registry.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    d["sources"][0]["notes"] = "DB 에 갈 곳이 없는 필드"
    f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    root, db = _sync(tmp_path, src)
    rep = v.verify(root, db, source_dir=src)
    assert any(f.startswith("레지스트리 필드 매핑 없음: source_registry") and "notes" in f for f in rep.failures)


def test_operator_decisions_are_notes_not_failures(tmp_path, fixed_today):
    root, db = _sync(tmp_path)
    prop = next(p for p in store.list_proposals(db_path=db) if p["proposal_type"] == "role_change")
    approval.approve(prop["proposal_id"], reviewer="admin@x", reason="확인", db_path=db)
    pid = store.list_people(db_path=db)[0]["person_id"]
    store.set_person_status(pid, "paused", actor="admin@x", db_path=db)
    rep = v.verify(root, db)
    assert rep.failures == []
    assert any(prop["proposal_id"] in n and "status" in n for n in rep.notes)
    assert any(pid in n and "status" in n for n in rep.notes)
    # 운영자 결정이 있어도 불변 필드(근거 등)가 바뀌면 여전히 실패
    _sql(db, "UPDATE sv_audit_proposal SET summary='바뀜' WHERE proposal_id=?", (prop["proposal_id"],))
    rep2 = v.verify(root, db)
    assert any(prop["proposal_id"] in f and "summary" in f for f in rep2.failures)


def test_run_payload_must_round_trip_bytes_exact(tmp_path):
    root, db = _sync(tmp_path)
    f = root / "monitoring-runs" / "2026-10-10" / "daily_monitoring.json"
    reformatted = json.dumps(json.loads(f.read_text(encoding="utf-8")), ensure_ascii=False)
    _sql(db, "UPDATE sv_monitoring_run SET payload_json=? WHERE md_path LIKE 'monitoring-runs/2026-10-10/%'",
         (reformatted,))
    _sql(db, "DELETE FROM sv_audit_run")
    rep = v.verify(root, db)
    assert any(f.startswith("런 payload 바이트 불일치: monitoring-runs/2026-10-10/daily_monitoring.json")
               for f in rep.failures)
    assert _starting(rep, "런 미반영: audit-runs/2026-10-10/coverage_audit.json")


def test_manifest_meta_and_raw_round_trip(tmp_path):
    root, db = _sync(tmp_path)
    _sql(db, "UPDATE sv_meta SET value='[]' WHERE key='withheld_claims_json'")
    sid = store.list_sources(db_path=db)[0]["source_id"]
    _sql(db, "UPDATE sv_source SET raw_json='{}' WHERE source_id=?", (sid,))
    rep = v.verify(root, db)
    assert _starting(rep, "sv_meta 불일치: withheld_claims_json")
    assert _starting(rep, f"manifest source 필드 불일치: {sid}")


# ── 미러 (--source-dir) ───────────────────────────────────────────────────────────

def test_files_outside_allowlist_are_not_compared(tmp_path):
    src = _copy_fix(tmp_path)
    root, db = _sync(tmp_path, src)
    (src / "dashboard-sketches" / "new.html").write_text("<html></html>", encoding="utf-8")
    (src / "requirements.txt").write_text("x\n", encoding="utf-8")
    rep = v.verify(root, db, source_dir=src)
    assert rep.failures == []
    rejected = {e["path"] for e in rep.exclusions["rejected_paths"]}
    assert {"dashboard-sketches/new.html", "requirements.txt"} <= rejected


def test_allowed_source_file_missing_from_mirror_is_failure(tmp_path):
    src = _copy_fix(tmp_path)
    root, db = _sync(tmp_path, src)
    (src / "sources" / "new_after_sync.txt").write_text("새 원문", encoding="utf-8")
    rep = v.verify(root, db, source_dir=src)
    assert _starting(rep, "mirror 누락: sources/new_after_sync.txt")
    assert rep.counts["mirror_missing"] == 1


# ── CLI ───────────────────────────────────────────────────────────────────────────

def test_cli_json_and_exit_code(tmp_path, capsys):
    root, db = _sync(tmp_path)
    assert v.main(["--root", str(root), "--db", str(db), "--source-dir", str(FIX), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["failures"] == [] and out["counts"]["people"] == 3
    assert {"counts", "basis", "exclusions", "known_discrepancies", "notes"} <= set(out)
    (root / "sources" / "fake_20260903_hong.txt").write_text("변조", encoding="utf-8")
    assert v.main(["--root", str(root), "--db", str(db)]) == 1
    text = capsys.readouterr().out
    assert text.startswith("stakeholder verify: FAIL") and "SHA-256 mismatch" in text


def test_missing_db_is_failure_not_crash(tmp_path):
    rep = v.verify(tmp_path / "root", tmp_path / "none.db")
    assert rep.failures and rep.failures[0].startswith("DB 없음")


# ── 실데이터 (STAKEHOLDER_SRC_DIR) ─────────────────────────────────────────────────

_REAL_SRC = os.environ.get("STAKEHOLDER_SRC_DIR")


@pytest.mark.skipif(not _REAL_SRC or not (Path(_REAL_SRC or ".") / "source_manifest.json").exists(),
                    reason="STAKEHOLDER_SRC_DIR(소스 repo 체크아웃) 가 없으면 실데이터 검증을 건너뛴다")
def test_real_source_import_is_lossless(tmp_path):
    src = Path(_REAL_SRC)
    root, db = _sync(tmp_path, src)
    rep = v.verify(root, db, source_dir=src)
    assert rep.failures == [], rep.failures
    c = rep.counts
    # 구조 검사로 대조된 수치(실패 0 이 곧 일치) + 2026-10-12 스냅샷 실측값
    assert (c["people"], c["source_domains"], c["proposals"]) == (11, 32, 6)
    assert (c["sources"], c["artifacts"], c["monitoring_runs"], c["audit_runs"]) == (22, 10, 3, 2)
    assert c["integrity"]["sources"] == {"verified": 21, "mismatch": 0, "missing": 0, "no_local": 1, "unchecked": 0}
    assert (c["events"], c["statements"], c["quote_blocks_md"], c["quote_blocks_excluded"]) == (12, 33, 51, 1)
    assert c["quotes_db"] == 47 and c["locator_match"]["none"] == 1
    assert c["quotes_locator_none_known"] == 1 and c["quotes_locator_none_unknown"] == 0
    assert c["mirrored_files"] == 374 and c["mirror_mismatch"] == 0 and len(rep.exclusions["rejected_paths"]) == 10
    assert [e["source_id"] for e in rep.exclusions["no_local_sources"]] == ["SRC-BOK-NHIS-20251211-YT"]
