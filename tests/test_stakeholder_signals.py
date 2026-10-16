"""정책 시그널(data/policy_signals.json) + 허용목록 밖 manifest 항목(excluded).

소스 stakeholder_service.py 의 PolicySignalStore 스키마 1:1 검증 · 읽기 전용 미러(내용 바뀌면 교체, 무효 파일은 이전 정상본
유지) · 근거 locator 판정 · GET /api/stakeholder/signals 모양 · excluded 무결성 상태.
합성 픽스처만 쓴다(실제 기사 문장 없음). 네트워크·LLM 없음. DB 는 tmp_path.
"""
from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from flask import Flask

from agents.stakeholder import signals, store, sync
from api.auth import _issue_token
from api.stakeholder_routes import build_stakeholder_blueprint

FIX = Path(__file__).parent / "fixtures" / "stakeholder_src_mini"
INVALID = FIX / "_variants" / "policy_signals_invalid.json"
TODAY = date(2026, 10, 12)
NEWER, OLDER = "SIG-FAKE-NEWER-20261015", "SIG-FAKE-OLDER-20261001"
EXCLUDED_ARTS = {"stakeholder_service.py", "dashboard-sketches/001-leadership-timeline/index.html"}
FORBIDDEN_USER_KEYS = {"photo_url", "local_path", "text_path", "photo_path"}


def _copy_fix(tmp_path) -> Path:
    src = tmp_path / "src"
    shutil.copytree(FIX, src)
    return src


def _sync(tmp_path, src=FIX, *, ok=True):
    root, db = tmp_path / "root", tmp_path / "s.db"
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    if ok:
        assert res["ok"], res["errors"]
    return res, root, db


def _payload() -> dict:
    return json.loads((FIX / "data" / "policy_signals.json").read_text(encoding="utf-8"))


def _write_signals(src: Path, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    (src / "data" / "policy_signals.json").write_text(text, encoding="utf-8")


def _keys_deep(o) -> set:
    if isinstance(o, dict):
        return set(o) | set().union(*(_keys_deep(v) for v in o.values())) if o else set()
    if isinstance(o, list):
        return set().union(*(_keys_deep(v) for v in o)) if o else set()
    return set()


# ── 스키마 1:1 ────────────────────────────────────────────────────────────────────

def test_schema_accepts_fixture_and_rejects_unknown_keys_and_out_of_range():
    v = signals.validate_payload(_payload())
    assert [s.signal_id for s in v.signals] == [OLDER, NEWER]
    bad = json.loads(INVALID.read_text(encoding="utf-8"))
    with pytest.raises(signals.SignalSchemaError) as ei:
        signals.validate_payload(bad)
    msg = str(ei.value)
    assert "probability" in msg and "direction_confidence" in msg


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["signals"][0].pop("thesis"), "thesis"),
    (lambda d: d["signals"][0]["confirmed_evidence"][0].pop("locator"), "locator"),
    (lambda d: d["signals"][0]["confirmed_evidence"][0].update(extra="x"), "extra"),
    (lambda d: d["signals"][0]["analyst_inference"].update(leak="x"), "leak"),
    (lambda d: d["signals"][0]["confidence_methodology"].pop("limitation"), "limitation"),
    (lambda d: d["signals"][0].update(implementation_model_confidence=-0.1), "implementation_model_confidence"),
    (lambda d: d.update(note="x"), "note"),
    (lambda d: d["signals"].append(copy.deepcopy(d["signals"][0])), "signal_id 중복"),
])
def test_schema_strictness(mutate, needle):
    d = _payload()
    mutate(d)
    with pytest.raises(signals.SignalSchemaError) as ei:
        signals.validate_payload(d)
    assert needle in str(ei.value)


def test_confidence_bounds_inclusive():
    d = _payload()
    d["signals"][0]["direction_confidence"] = 0
    d["signals"][0]["implementation_model_confidence"] = 1
    signals.validate_payload(d)


# ── sync: 적재 · 무손실 · 멱등 ────────────────────────────────────────────────────────

def test_sync_imports_signals_lossless_and_separated(tmp_path):
    res, root, db = _sync(tmp_path)
    assert res["counts"]["signals_changed"] == 2 and res["counts"]["signals"] == 2
    rows = store.list_policy_signals(db_path=db)
    src = _payload()
    assert [r["signal_id"] for r in rows] == [OLDER, NEWER]                    # 파일 순서(position)
    for r, obj in zip(rows, src["signals"]):
        assert r["raw"] == obj                                                 # 원문 객체 그대로
        assert r["analyst_inference"] == obj["analyst_inference"] and r["not_confirmed"] == obj["not_confirmed"]
        assert [e["raw"] for e in r["evidence"]] == obj["confirmed_evidence"]
        for e, eo in zip(r["evidence"], obj["confirmed_evidence"]):
            assert e["claim"].encode("utf-8") == eo["claim"].encode("utf-8")
            # 근거 행에 추론·미확인이 섞이지 않는다
            assert not {"analyst_inference", "not_confirmed", "thesis"} & set(e["raw"])
    assert store.get_meta("policy_signals_schema_version", db_path=db) == "1.0"
    assert store.get_meta("policy_signals_updated_at", db_path=db) == src["updated_at"]
    st = json.loads(store.get_meta("policy_signals_status_json", db_path=db))
    assert st["state"] == "ok" and st["error"] is None
    res2 = sync.sync(root, db, source_dir=FIX, today=TODAY)
    assert res2["counts"]["signals_changed"] == 0 and res2["counts"]["changes"] == 0


def test_locator_match_per_evidence(tmp_path):
    _, _, db = _sync(tmp_path)
    ev = {(e["signal_id"], e["idx"]): e for r in store.list_policy_signals(db_path=db) for e in r["evidence"]}
    assert ev[(OLDER, 0)]["locator_match"] == "range"
    assert ev[(NEWER, 0)]["locator_match"] == "range"
    assert ev[(NEWER, 1)]["locator_match"] == "file" and "벗어남" in ev[(NEWER, 1)]["locator_detail"]
    assert ev[(NEWER, 2)]["locator_match"] == "none" and "text_path" in ev[(NEWER, 2)]["locator_detail"]


def test_locator_status_rules(tmp_path):
    root = tmp_path
    (root / "sources").mkdir()
    (root / "sources" / "a.txt").write_text("첫줄\n\n셋째\n", encoding="utf-8")
    src = {"source_id": "S", "text_path": "sources/a.txt"}
    assert signals.locator_status(root, src, "sources/a.txt:1-3") == ("range", None)
    assert signals.locator_status(root, src, "sources/a.txt:1, 3")[0] == "range"
    assert signals.locator_status(root, src, "sources/a.txt")[0] == "file"
    assert signals.locator_status(root, src, "sources/a.txt:2")[0] == "file"            # 빈 줄뿐
    assert signals.locator_status(root, src, "sources/a.txt:3-9")[0] == "file"
    assert signals.locator_status(root, src, "sources/b.txt:1")[0] == "none"
    assert signals.locator_status(root, None, "sources/a.txt:1")[0] == "none"
    assert signals.locator_status(root, src, "sources/a.txt:x")[0] == "none"
    assert signals.locator_status(root, {"text_path": "sources/gone.txt"}, "sources/gone.txt:1")[0] == "unchecked"
    assert signals.locator_status(root, {"text_path": "../etc/passwd"}, "../etc/passwd:1")[0] == "none"
    assert (root / "sources" / "a.txt").read_text(encoding="utf-8") == "첫줄\n\n셋째\n"   # 고치지 않는다


def test_content_change_replaces_and_removed_signal_disappears(tmp_path):
    src = _copy_fix(tmp_path)
    _, root, db = _sync(tmp_path, src)
    d = _payload()
    d["signals"] = [d["signals"][1]]
    d["signals"][0]["thesis"] = "합성 명제 개정판"
    _write_signals(src, d)
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    assert res["ok"] and res["counts"]["signals_changed"] == 2                 # 1 변경 + 1 삭제
    rows = store.list_policy_signals(db_path=db)
    assert [r["signal_id"] for r in rows] == [NEWER] and rows[0]["thesis"] == "합성 명제 개정판"


def test_invalid_file_keeps_previous_good_rows_and_reports(tmp_path):
    src = _copy_fix(tmp_path)
    _, root, db = _sync(tmp_path, src)
    before = store.list_policy_signals(db_path=db)
    shutil.copy(INVALID, src / "data" / "policy_signals.json")
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    assert res["ok"] is False
    err = [e for e in res["errors"] if "policy_signals.json 검증 실패" in e]
    assert err and "이전 정상 시그널 2건 유지" in err[0] and "probability" in err[0]
    assert store.list_policy_signals(db_path=db) == before                      # 지우지 않는다
    st = json.loads(store.get_meta("policy_signals_status_json", db_path=db))
    assert st["state"] == "invalid" and "direction_confidence" in st["error"]
    # 다른 단계는 그대로 돈다(무결성·정본)
    assert store.get_meta("canonical_timeline_sha256", db_path=db)
    # 깨진 JSON 도 같은 처리
    _write_signals(src, "{깨짐")
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    assert not res["ok"] and store.list_policy_signals(db_path=db) == before
    # 고쳐서 커밋하면 다음 sync 가 회복
    shutil.copy(FIX / "data" / "policy_signals.json", src / "data" / "policy_signals.json")
    res = sync.sync(root, db, source_dir=src, today=TODAY)
    assert res["ok"], res["errors"]
    assert json.loads(store.get_meta("policy_signals_status_json", db_path=db))["state"] == "ok"


def test_absent_file_is_not_an_error(tmp_path):
    src = _copy_fix(tmp_path)
    (src / "data" / "policy_signals.json").unlink()
    m = json.loads((src / "source_manifest.json").read_text(encoding="utf-8"))
    m["artifacts"] = [a for a in m["artifacts"] if a["path"] != "data/policy_signals.json"]
    (src / "source_manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    res, _, db = _sync(tmp_path, src)
    assert store.list_policy_signals(db_path=db) == []
    assert json.loads(store.get_meta("policy_signals_status_json", db_path=db))["state"] == "absent"


def test_signal_md_is_artifact_not_statements(tmp_path):
    _, root, db = _sync(tmp_path)
    md = "2026-10-15_정책시그널_합성.md"
    arts = {a["path"]: a["integrity_status"] for a in store.list_artifacts(db_path=db)}
    assert arts[md] == "verified" and (root / md).is_file()
    assert store.get_meta("canonical_timeline_path", db_path=db) != md
    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM sv_quote WHERE quote_text LIKE '%합성 인용처럼%'").fetchone()[0]
    assert n == 0


# ── excluded (허용목록 밖 manifest 항목) ────────────────────────────────────────────

def test_artifacts_outside_allowlist_are_excluded_not_missing(tmp_path):
    res, root, db = _sync(tmp_path)
    arts = {a["path"]: a["integrity_status"] for a in store.list_artifacts(db_path=db)}
    assert {p for p, s in arts.items() if s == "excluded"} == EXCLUDED_ARTS
    assert "missing" not in arts.values()
    assert not (root / "stakeholder_service.py").exists()                       # 허용목록은 넓히지 않았다
    summ = json.loads(store.get_meta("integrity_summary_json", db_path=db))
    assert summ["artifacts"]["excluded"] == 2 and summ["artifacts"]["missing"] == 0
    assert {e["id"] for e in summ["excluded"]} == EXCLUDED_ARTS
    assert not [p for p in summ["problems"] if p["id"] in EXCLUDED_ARTS]


def test_source_with_only_out_of_allowlist_paths_is_excluded(tmp_path):
    src = _copy_fix(tmp_path)
    m = json.loads((src / "source_manifest.json").read_text(encoding="utf-8"))
    m["sources"].append({"source_id": "SRC-FAKE-SKETCH", "title": "스케치 캡처(합성)",
                         "local_path": "dashboard-sketches/001-leadership-timeline/index.html", "sha256": "0" * 64})
    (src / "source_manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    _, _, db = _sync(tmp_path, src)
    assert store.get_source("SRC-FAKE-SKETCH", db_path=db)["integrity_status"] == "excluded"


def test_allowlisted_artifact_missing_is_still_missing(tmp_path):
    src = _copy_fix(tmp_path)
    m = json.loads((src / "source_manifest.json").read_text(encoding="utf-8"))
    m["artifacts"].append({"path": "data/not_committed.json", "sha256": "0" * 64, "bytes": 1})
    (src / "source_manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    _, _, db = _sync(tmp_path, src)
    arts = {a["path"]: a["integrity_status"] for a in store.list_artifacts(db_path=db)}
    assert arts["data/not_committed.json"] == "missing"


def test_legacy_missing_rows_become_excluded_after_migration(tmp_path):
    """프로덕션 상황: 구 스키마(CHECK 에 excluded 없음)에서 두 산출물이 missing 으로 기록된 DB → 새 코드 sync 1회로
    excluded, 무결성 실패 0."""
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    conn.executescript(store.SCHEMA_SQL.replace(",'excluded'", ""))
    for p in EXCLUDED_ARTS:
        conn.execute("INSERT INTO sv_artifact (path, raw_json, integrity_status, imported_at) VALUES (?,?,?,?)",
                     (p, "{}", "missing", "2026-10-16T02:20:00+09:00"))
    conn.commit()
    conn.close()
    store._ENSURED.clear()
    res = sync.sync(tmp_path / "root", db, source_dir=FIX, today=TODAY)
    assert res["ok"], res["errors"]
    arts = {a["path"]: a["integrity_status"] for a in store.list_artifacts(db_path=db)}
    assert all(arts[p] == "excluded" for p in EXCLUDED_ARTS)
    ddl = sqlite3.connect(db).execute("SELECT sql FROM sqlite_master WHERE name='sv_artifact'").fetchone()[0]
    assert "'excluded'" in ddl


# ── API ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def api(tmp_path, monkeypatch):
    _, root, db = _sync(tmp_path)
    monkeypatch.setattr(store, "today_kst", lambda: TODAY)
    app = Flask(__name__)
    app.register_blueprint(build_stakeholder_blueprint(db, root=root))
    return app.test_client(), root, db


def H(role: str) -> dict:
    return {"Authorization": f"Bearer {_issue_token(f'{role}@x.test', role)}"}


def test_signals_route_shape_matches_source_plus_provenance(api):
    client, _, _ = api
    r = client.get("/api/stakeholder/signals", headers=H("user"))
    assert r.status_code == 200
    d = r.get_json()
    assert set(d) == {"schema_version", "updated_at", "signal_count", "signals", "import_status"}
    assert d["schema_version"] == "1.0" and d["signal_count"] == 2
    assert [s["signal_id"] for s in d["signals"]] == [NEWER, OLDER]             # as_of 내림차순
    assert d["import_status"] == {"state": "ok", "error": None}
    s = d["signals"][0]
    # 소스 model_dump 의 전체 키(선택 필드는 null 로 존재) + 근거에만 우리 추가 키 3개
    assert set(s) == set(signals.PolicySignal.model_fields)
    ev = s["confirmed_evidence"]
    assert set(ev[0]) == set(signals.SignalEvidence.model_fields) | {"locator_match", "integrity_status", "source"}
    assert ev[0]["policy_status"] is None and ev[1]["policy_status"] == "planned_policy_pending_final_rules"
    assert [e["locator_match"] for e in ev] == ["range", "file", "none"]
    assert ev[0]["integrity_status"] == "verified"
    assert ev[0]["source"] == {"publisher_title": "가상 협회 약가 토론 가상 기사", "url": "https://example-news.test/news/1",
                               "publication_date": "2026-09-03"}
    # 분리: 근거에 추론·미확인이 없고, 시그널 최상위에 따로 있다
    assert "analyst_inference" not in ev[0] and s["analyst_inference"]["payer_benefits"]
    assert s["status"] == "analyst_hypothesis" and s["not_confirmed"].startswith("합성")
    assert 0 <= s["direction_confidence"] <= 1
    assert not FORBIDDEN_USER_KEYS & _keys_deep(d)


def test_signals_route_auth_and_empty_db(api, tmp_path):
    client, _, _ = api
    assert client.get("/api/stakeholder/signals").status_code == 401
    assert client.get("/api/stakeholder/signals", headers=H("agent")).status_code == 403
    assert client.get("/api/stakeholder/signals", headers=H("admin")).status_code == 200
    app = Flask(__name__)
    app.register_blueprint(build_stakeholder_blueprint(tmp_path / "empty.db", root=tmp_path / "nothing"))
    d = app.test_client().get("/api/stakeholder/signals", headers=H("user")).get_json()
    assert d == {"schema_version": None, "updated_at": None, "signal_count": 0, "signals": [],
                 "import_status": {"state": "absent", "error": None}}


def test_signals_route_serves_last_good_with_invalid_status(tmp_path, monkeypatch):
    src = _copy_fix(tmp_path)
    _, root, db = _sync(tmp_path, src)
    shutil.copy(INVALID, src / "data" / "policy_signals.json")
    sync.sync(root, db, source_dir=src, today=TODAY)
    app = Flask(__name__)
    app.register_blueprint(build_stakeholder_blueprint(db, root=root))
    d = app.test_client().get("/api/stakeholder/signals", headers=H("user")).get_json()
    assert d["signal_count"] == 2 and d["import_status"]["state"] == "invalid"
    assert "direction_confidence" in d["import_status"]["error"]
