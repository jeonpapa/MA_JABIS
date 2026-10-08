"""병합(combined) D+1 보고서 — 매니페스트 검증은 다운로드·LLM 보다 먼저, 연결 규칙은 UI 와 에이전트 팀이 공유한다.

재현 (헤르메스 커밋 cc25e4ff, 2026-10-02): reports_manifest.json 에 committee="combined" 항목이 들어왔는데
reimb_reports.committee CHECK 는 cancer|evaluation 만 허용 → 02:00 reimb_data_sync 가 PDF 다운로드 + LLM 분석 뒤
IntegrityError. 행이 안 써지니 file_hash 도 안 남아 **매일 재다운로드·재분석 후 재실패** (비용 누수) + 잡 ok=False 과제.
프로덕션 테이블의 CHECK 는 못 바꾼다(SQLite 는 CHECK 변경 = 테이블 재생성) → committee=NULL + committee_scope='combined'.

네트워크·LLM·실제 DB 금지: _download/_analyze_llm/_extract_pdf_text 를 스텁, DB 는 tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

import agents.reimb_reports as rr
import agents.reimb_pipeline as rp
from agents.agent_tasks import freshness as fr
from tests.test_reimb_pipeline_expectations import _FrozenDate, _make_db

def _stub_pdf(url: str) -> bytes:
    return b"%PDF-stub " + url.encode()


def _h(url: str) -> str:
    """매니페스트 file_hash = 실제 PDF sha1 — 스텁 바이트로 같은 계약을 지킨다 (2차 sync 가 hash 로 skip 하는지 보려면 필수)."""
    return hashlib.sha1(_stub_pdf(url)).hexdigest()


COMBINED = {
    "file_name": "2026-10-02_약평위10차_암질심8차_d_plus_1.pdf",
    "file_hash": _h("https://raw.example/combined.pdf"),
    "url": "https://raw.example/combined.pdf",
    "committee": "combined", "report_type": "post", "year": 2026, "cycle": 10,
    "session_date": "2026-10-01",
    "title": "2026년 10차 약제급여평가위원회·8차 암질환심의위원회 결과 리뷰 보고",
}
EVAL_ONLY = {
    "file_name": "2026-06-05_yakpyungwi-6_d_plus_1_v2.pdf",
    "file_hash": _h("https://raw.example/eval.pdf"),
    "url": "https://raw.example/eval.pdf",
    "committee": "evaluation", "report_type": "post", "year": 2026, "cycle": 6,
    "session_date": "2026-06-04", "title": "2026년 6차 약제급여평가위원회 결과 리뷰 보고",
}
BAD = {
    "file_name": "2026-09-05_bogus.pdf", "file_hash": "0" * 40, "url": "https://raw.example/bogus.pdf",
    "committee": "joint", "report_type": "post", "year": 2026, "cycle": 9,
    "session_date": "2026-09-03", "title": "잘못된 committee 값",
}


@pytest.fixture
def rdb(tmp_path, monkeypatch) -> Path:
    """reimb_reports 를 프로덕션과 같은 CHECK 로 만든 임시 DB (ensure_schema 가 committee_scope 를 보강)."""
    path = tmp_path / "reports.db"
    monkeypatch.setattr(rr, "DB_PATH", path)
    monkeypatch.setattr(rr, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(rr, "ARCHIVE_DIR", tmp_path / "archive")
    rr.ensure_schema()
    return path


@pytest.fixture
def stubs(monkeypatch):
    """다운로드·PDF 추출·LLM 을 기록하는 스텁. 호출 여부를 단언한다."""
    calls = {"download": [], "llm": []}

    def _download(url):
        calls["download"].append(url)
        return _stub_pdf(url)

    def _llm(text, hints, file_name):
        calls["llm"].append(file_name)
        return {"summary": "요약", "highlights": ["h1"]}

    monkeypatch.setattr(rr, "_download", _download)
    monkeypatch.setattr(rr, "_analyze_llm", _llm)
    monkeypatch.setattr(rr, "_extract_pdf_text", lambda p: ("본문", 3))
    return calls


def _rows(path: Path) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT file_name, committee, committee_scope, report_type, session_date, year, cycle, source "
            "FROM reimb_reports ORDER BY id")]
    finally:
        conn.close()


# ── 1. 검증은 비용보다 먼저 ───────────────────────────────────────────────────

def test_validate_manifest_entry_rejects_unknown_committee_and_bad_fields():
    assert rr.validate_manifest_entry(BAD)[1] and "committee" in rr.validate_manifest_entry(BAD)[1]
    meta, err = rr.validate_manifest_entry(COMBINED)
    assert err is None and meta["committee"] is None and meta["committee_scope"] == "combined"   # CHECK 호환 + 표식
    assert meta["year"] == 2026 and meta["cycle"] == 10
    meta_e, _ = rr.validate_manifest_entry(EVAL_ONLY)
    assert meta_e["committee"] == "evaluation" and meta_e["committee_scope"] == "evaluation"
    assert rr.normalize_report_meta(meta)[0] == meta                        # 멱등 — ingest_from_url 이 다시 정규화해도 같다
    for broken, key in (({**EVAL_ONLY, "report_type": "d+1"}, "report_type"),
                        ({**EVAL_ONLY, "session_date": "2026/06/04"}, "session_date"),
                        ({**EVAL_ONLY, "url": "ftp://x"}, "url"),
                        ({**EVAL_ONLY, "cycle": "six"}, "cycle"),
                        ({k: v for k, v in EVAL_ONLY.items() if k != "committee"}, "committee")):
        _, err = rr.validate_manifest_entry(broken)
        assert err and key in err, (broken, err)


def test_invalid_entry_costs_nothing_and_does_not_abort_the_others(rdb, stubs):
    res = rr.sync_reports({"schema_version": 1, "reports": [BAD, EVAL_ONLY, COMBINED]})
    assert res["ingested"] == [EVAL_ONLY["file_name"], COMBINED["file_name"]]
    assert len(res["errors"]) == 1 and res["errors"][0]["file"] == BAD["file_name"] and "combined" in res["errors"][0]["error"]
    assert stubs["download"] == [EVAL_ONLY["url"], COMBINED["url"]]           # 잘못된 항목은 다운로드 안 함
    assert stubs["llm"] == [EVAL_ONLY["file_name"], COMBINED["file_name"]]    # LLM 도 안 부름
    assert [r["file_name"] for r in _rows(rdb)] == [EVAL_ONLY["file_name"], COMBINED["file_name"]]


def test_ingest_from_url_validates_before_download(rdb, stubs):
    res = rr.ingest_from_url(BAD["url"], {k: v for k, v in BAD.items() if k != "url"})
    assert res["status"] == "error" and "committee" in res["error"]
    assert stubs["download"] == [] and stubs["llm"] == []


def test_per_entry_exception_is_collected_not_raised(rdb, stubs, monkeypatch):
    def _boom(url, meta):
        if meta["committee_scope"] == "combined":
            raise RuntimeError("disk full")
        return {"status": "ingested", "id": 1, "file": meta["file_name"], "analyzed": True, "error": None}
    monkeypatch.setattr(rr, "ingest_from_url", _boom)
    res = rr.sync_reports({"schema_version": 1, "reports": [COMBINED, EVAL_ONLY]})
    assert res["ingested"] == [EVAL_ONLY["file_name"]]
    assert res["errors"] == [{"file": COMBINED["file_name"], "url": COMBINED["url"], "error": "RuntimeError: disk full"}]


# ── 2. combined 는 CHECK 를 그대로 둔 채 저장된다 ─────────────────────────────

def test_combined_entry_is_stored_with_null_committee_and_scope_marker(rdb, stubs):
    conn = sqlite3.connect(rdb)
    assert "CHECK (committee IS NULL OR committee IN ('cancer','evaluation'))" in \
        conn.execute("SELECT sql FROM sqlite_master WHERE name='reimb_reports'").fetchone()[0]
    conn.close()
    res = rr.sync_reports({"schema_version": 1, "reports": [COMBINED, EVAL_ONLY]})
    assert res["errors"] == [] and len(res["ingested"]) == 2
    rows = {r["file_name"]: r for r in _rows(rdb)}
    c = rows[COMBINED["file_name"]]
    assert c["committee"] is None and c["committee_scope"] == "combined" and c["source"] == "git_sync"
    assert c["session_date"] == "2026-10-01" and c["cycle"] == 10
    e = rows[EVAL_ONLY["file_name"]]
    assert e["committee"] == "evaluation" and e["committee_scope"] == "evaluation"
    # 두 번째 sync: file_hash 가 있으니 다운로드도 LLM 도 다시 안 한다 (비용 누수 재현 방지)
    stubs["download"].clear(); stubs["llm"].clear()
    res2 = rr.sync_reports({"schema_version": 1, "reports": [COMBINED, EVAL_ONLY]})
    assert res2["skipped"] == 2 and res2["ingested"] == [] and stubs["download"] == [] and stubs["llm"] == []
    assert res["warnings"] == [] and res2["warnings"] == []
    listed = {r["file_name"]: r for r in rr.list_reports()}
    assert listed[COMBINED["file_name"]]["committee_scope"] == "combined"


def test_manifest_hash_mismatch_is_reported_as_warning(rdb, stubs):
    """매니페스트 file_hash ≠ 실제 PDF sha1 이면 적재는 되지만(내용 hash 로 dedup) 매니페스트 변경 때마다 재다운로드 → 경고."""
    res = rr.sync_reports({"schema_version": 1, "reports": [{**EVAL_ONLY, "file_hash": "f" * 40}]})
    assert res["ingested"] == [EVAL_ONLY["file_name"]] and res["errors"] == []
    assert len(res["warnings"]) == 1 and "file_hash" in res["warnings"][0]["warning"] and res["warnings"][0]["file"] == EVAL_ONLY["file_name"]


def test_ensure_schema_adds_committee_scope_to_existing_production_table(tmp_path, monkeypatch):
    """프로덕션처럼 이미 CHECK 가 박힌 테이블(committee_scope 없음)에 ALTER 만으로 보강 — 재생성 없음."""
    path = tmp_path / "prod.db"
    conn = sqlite3.connect(path)
    conn.executescript(rr._SCHEMA)
    conn.execute("INSERT INTO reimb_reports (file_hash, file_name, pdf_path, committee, report_type, session_date) "
                 "VALUES ('h1','old.pdf','p','evaluation','post','2026-06-04')")
    conn.commit(); conn.close()
    monkeypatch.setattr(rr, "DB_PATH", path)
    monkeypatch.setattr(rr, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(rr, "ARCHIVE_DIR", tmp_path / "archive")
    rr.ensure_schema()
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(reimb_reports)")}
    assert "committee_scope" in cols and "pdf_blob" in cols
    assert rr.report_scope({"committee": "evaluation", "committee_scope": None}) == "evaluation"   # 구 행은 committee 로


# ── 3. 연결 규칙 하나 — _linked_report 와 committee_reports 가 같은 함수를 쓴다 ──

def test_report_covers_session_rule():
    combined = {"committee": None, "committee_scope": "combined", "session_date": "2026-10-01", "year": 2026, "cycle": 10}
    legacy_null = {"committee": None, "committee_scope": None, "session_date": "2026-10-01", "year": 2026, "cycle": 10}
    cancer = {"committee": "cancer", "committee_scope": "cancer", "session_date": "2026-09-30", "year": 2026, "cycle": 8}
    yak = {"committee": "YAKPYUNGWI", "session_date": "2026-10-01", "year": 2026, "cycle": 10}
    amj = {"committee": "AMJILSIM", "session_date": "2026-09-30", "year": 2026, "cycle": 8}
    amj_far = {"committee": "cancer", "session_date": "2026-11-11", "year": 2026, "cycle": 9}
    amj_cycle10 = {"committee": "cancer", "session_date": "2026-12-23", "year": 2026, "cycle": 10}
    assert rr.report_covers_session(combined, yak) and rr.report_covers_session(combined, amj)
    assert rr.report_covers_session(legacy_null, yak) and rr.report_covers_session(legacy_null, amj)
    assert not rr.report_covers_session(combined, amj_far)
    assert not rr.report_covers_session(combined, amj_cycle10)          # combined 는 (연도+차수) 매칭 금지 — 차수는 한 위원회 것
    assert rr.report_covers_session(cancer, amj) and not rr.report_covers_session(cancer, yak)
    assert rr.report_covers_session({"committee": "cancer", "session_date": None, "year": 2026, "cycle": 8}, amj)   # 연도+차수
    assert rr.COMBINED_LINK_WINDOW_DAYS >= 1


@pytest.fixture
def pipeline_db(tmp_path, monkeypatch) -> Path:
    path = _make_db(tmp_path / "reimb.db")
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE reimb_reports ADD COLUMN committee_scope TEXT")
    conn.executemany("INSERT INTO amjilsim_sessions (session_id, year, ordinal_assumed, ordinal_official, session_date, status, "
                     "committee_type) VALUES (?,?,?,?,?,?,?)",
                     [(7, 2026, 8, 8, "2026-09-30", "COMPLETED", "AMJILSIM"), (19, 2026, 10, None, "2026-10-01", "COMPLETED", "YAKPYUNGWI"),
                      (9, 2026, 10, 10, "2026-12-23", "SCHEDULED", "AMJILSIM")])
    conn.executemany("INSERT INTO reimb_reports (id, title, summary, highlights_json, report_type, committee, committee_scope, "
                     "year, cycle, session_date, analyzed) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     [(1, "6차 약평위 결과", "s6", '["a"]', "post", "evaluation", "evaluation", 2026, 6, "2026-06-04", 1),
                      (2, "10차 약평위·8차 암질심 결과 리뷰", "s10", '["b"]', "post", None, "combined", 2026, 10, "2026-10-01", 1),
                      (3, "10차 약평위 사전", "pre", "[]", "pre", "evaluation", "evaluation", 2026, 10, "2026-10-01", 1)])
    conn.commit(); conn.close()
    monkeypatch.setattr(rp, "DB_PATH", path)
    monkeypatch.setattr(rp, "date", _FrozenDate)
    return path


def test_linked_report_links_combined_report_to_both_sessions(pipeline_db):
    yak = rp.get_meeting_results(19)["report"]
    amj = rp.get_meeting_results(7)["report"]
    assert yak and yak["id"] == 2 and yak["summary"] == "s10"
    assert amj and amj["id"] == 2
    assert rp.get_meeting_results(9)["report"] is None          # 암질심 10차(12-23) — 차수 10 이 같아도 combined 는 안 붙는다
    assert rp.get_meeting_results(15)["report"]["id"] == 1       # 위원회 지정 보고서는 제 위원회에만
    assert rp.get_meeting_results(16)["report"] is None          # 7차 약평위(07-02) — 아무것도 없음


def test_committee_specific_report_wins_over_combined_for_its_own_session(pipeline_db):
    conn = sqlite3.connect(pipeline_db)
    conn.execute("INSERT INTO reimb_reports (id, title, summary, highlights_json, report_type, committee, committee_scope, "
                 "year, cycle, session_date, analyzed) VALUES (4,'8차 암질심 결과','s8','[]','post','cancer','cancer',2026,8,'2026-09-30',1)")
    conn.commit(); conn.close()
    assert rp.get_meeting_results(7)["report"]["id"] == 4
    assert rp.get_meeting_results(19)["report"]["id"] == 2


def _freshness_ctx(tmp_path, with_scope_column: bool) -> fr.Ctx:
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE amjilsim_sessions (session_id INTEGER, committee_type TEXT, status TEXT, session_date TEXT);
        INSERT INTO amjilsim_sessions VALUES (18,'YAKPYUNGWI','SCHEDULED','2026-09-03'), (7,'AMJILSIM','COMPLETED','2026-09-30'),
                                             (19,'YAKPYUNGWI','COMPLETED','2026-10-01');
    """)
    if with_scope_column:
        conn.executescript("""
            CREATE TABLE reimb_reports (id INTEGER, committee TEXT, committee_scope TEXT, report_type TEXT, session_date TEXT, title TEXT);
            INSERT INTO reimb_reports VALUES (2, NULL, 'combined', 'post', '2026-10-01', '10차 약평위·8차 암질심 결과 리뷰');
        """)
    else:   # 구 스키마(committee_scope 없음) — NULL committee 는 combined 로 읽는다
        conn.executescript("""
            CREATE TABLE reimb_reports (id INTEGER, committee TEXT, report_type TEXT, session_date TEXT, title TEXT);
            INSERT INTO reimb_reports VALUES (2, NULL, 'post', '2026-10-01', '10차 약평위·8차 암질심 결과 리뷰');
        """)
    conn.commit(); conn.close()
    return fr.Ctx(db_path=db, base_dir=tmp_path, today=date(2026, 10, 6), policy_root=tmp_path / "pi")


@pytest.mark.parametrize("with_scope_column", [True, False])
def test_committee_reports_stops_flagging_both_sessions_covered_by_combined(tmp_path, with_scope_column):
    r = fr.check_committee_reports(_freshness_ctx(tmp_path, with_scope_column))
    missing_part = r.evidence.split("없음")[1].split(";")[0]
    assert "#18=2026-09-03" in missing_part                       # 9차 약평위는 여전히 누락
    assert "#7=2026-09-30" not in missing_part and "#19=2026-10-01" not in missing_part
    assert r.stale is True and "없음 1건" in r.evidence and "combined" in r.evidence


def test_committee_reports_specific_report_does_not_cover_the_other_committee(tmp_path):
    db = tmp_path / "d.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE amjilsim_sessions (session_id INTEGER, committee_type TEXT, status TEXT, session_date TEXT);
        INSERT INTO amjilsim_sessions VALUES (7,'AMJILSIM','COMPLETED','2026-09-30'), (19,'YAKPYUNGWI','COMPLETED','2026-10-01');
        CREATE TABLE reimb_reports (id INTEGER, committee TEXT, committee_scope TEXT, report_type TEXT, session_date TEXT, title TEXT);
        INSERT INTO reimb_reports VALUES (6, 'evaluation', 'evaluation', 'post', '2026-10-01', '10차 약평위 결과');
    """)
    conn.commit(); conn.close()
    r = fr.check_committee_reports(fr.Ctx(db_path=db, base_dir=tmp_path, today=date(2026, 10, 6), policy_root=tmp_path / "pi"))
    missing_part = r.evidence.split("없음")[1].split(";")[0]
    assert "#7=2026-09-30" in missing_part and "#19=2026-10-01" not in missing_part and "없음 1건" in r.evidence


# ── 4. 실제 매니페스트 드라이런 — combined 항목이 이제 들어간다 ──────────────────

def test_real_manifest_dry_run_ingests_combined_entry(rdb, stubs):
    manifest = json.loads(rr.MANIFEST_PATH.read_text(encoding="utf-8"))
    combined = [e for e in manifest["reports"] if e["committee"] == "combined"]
    assert combined, "매니페스트에 combined 항목이 있어야 재현된다 (cc25e4ff)"
    res = rr.sync_reports(manifest)
    assert res["errors"] == [] and combined[0]["file_name"] in res["ingested"]
    row = next(r for r in _rows(rdb) if r["file_name"] == combined[0]["file_name"])
    assert row["committee"] is None and row["committee_scope"] == "combined"
