"""Daily Mailing 스콥 OUTBOUND export (agents/ingest/daily_mailing_scope_export) 테스트.

- 네트워크 미접촉: GitHub contents 레이어(_gh_*)는 전부 fake 로 monkeypatch.
- 토큰 없음 → local-only degrade / active 만 export / test_request 지속·소비(run import) /
  멱등 PUT 생략 / stale 원격 prune.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents.ingest.daily_mailing_scope_export as sx
from agents.daily_mailing.subscription_bridge import ensure_test_request_column
from agents.ingest.daily_mailing_scope_export import build_active_scopes, export_scopes
from agents.ingest.daily_mailing_sync import sync_daily_mailing_runs

_TOKEN_ENVS = ("DAILY_MAILING_SCOPES_TOKEN", "DAILY_MAILING_RUNS_TOKEN", "GITHUB_TOKEN")


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "t.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE mail_subscription (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_email TEXT NOT NULL, name TEXT NOT NULL,
                keywords_json TEXT NOT NULL, media_json TEXT NOT NULL,
                schedule TEXT NOT NULL, time TEXT NOT NULL, week_day TEXT,
                emails_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                companies_json TEXT NOT NULL DEFAULT '[]',
                brands_json TEXT NOT NULL DEFAULT '[]',
                policy_topics_json TEXT NOT NULL DEFAULT '[]',
                disease_areas_json TEXT NOT NULL DEFAULT '[]',
                custom_sources_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_sent_at TEXT
            )
            """
        )
        for i, (name, active) in enumerate(
            [("스콥A", 1), ("스콥B", 1), ("비활성", 0)], start=1
        ):
            conn.execute(
                "INSERT INTO mail_subscription (owner_email, name, keywords_json, media_json,"
                " schedule, time, emails_json, active, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("j@msd.com", name, json.dumps(["키트루다"]), json.dumps(["dailypharm"]),
                 "Daily", "08:00", json.dumps([f"u{i}@msd.com"]), active,
                 "2026-07-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00"),
            )
        conn.commit()
    return db


def _no_tokens(monkeypatch):
    for env in _TOKEN_ENVS:
        monkeypatch.delenv(env, raising=False)


def test_export_local_only_active_only(tmp_path, monkeypatch):
    _no_tokens(monkeypatch)
    db = _make_db(tmp_path)
    root = tmp_path / "scopes"
    res = export_scopes(db, publish=False, scope_root=root)
    assert res["active"] == 2 and res["local"] == 2
    assert res["published"] == 0 and res["channel"] == "local-only"
    assert (root / "1.json").is_file() and (root / "2.json").is_file()
    assert not (root / "3.json").exists()  # inactive 제외
    index = json.loads((root / "scopes_index.json").read_text(encoding="utf-8"))
    assert [e["subscription_id"] for e in index] == ["1", "2"]
    assert all(e["has_test_request"] is False for e in index)
    scope1 = json.loads((root / "1.json").read_text(encoding="utf-8"))
    assert scope1["keywords"] == ["키트루다"] and scope1["recipients"] == ["u1@msd.com"]
    assert "test_request" not in scope1


def test_export_publish_without_token_degrades(tmp_path, monkeypatch):
    _no_tokens(monkeypatch)
    db = _make_db(tmp_path)
    res = export_scopes(db, publish=True, scope_root=tmp_path / "scopes")
    assert res["channel"] == "local-only" and res["published"] == 0 and res["local"] == 2


def test_test_request_persisted_then_cleared_by_test_run_import(tmp_path, monkeypatch):
    _no_tokens(monkeypatch)
    db = _make_db(tmp_path)
    root = tmp_path / "scopes"
    with sqlite3.connect(db) as conn:
        ensure_test_request_column(conn)
        conn.execute(
            "UPDATE mail_subscription SET test_request_json=? WHERE id=1",
            (json.dumps({"requested_at": "2026-07-07T00:00:00+00:00", "requested_by": "j@msd.com"}),),
        )
        conn.commit()

    export_scopes(db, publish=False, scope_root=root)
    scope1 = json.loads((root / "1.json").read_text(encoding="utf-8"))
    assert scope1["test_request"]["requested_by"] == "j@msd.com"
    index = json.loads((root / "scopes_index.json").read_text(encoding="utf-8"))
    assert next(e for e in index if e["subscription_id"] == "1")["has_test_request"] is True

    runs = tmp_path / "runs"
    runs.mkdir()

    def _write_test_run(name: str, generated_at: str):
        (runs / name).write_text(json.dumps({
            "is_test": True,
            "payload": {"run_id": name[:-5], "generated_at": generated_at,
                        "subscription_id": 1, "keywords": ["키트루다"],
                        "recipients": ["u1@msd.com"], "status": "quality_gated_draft"},
            "articles": [],
        }, ensure_ascii=False), encoding="utf-8")

    # (a) 요청보다 OLD 인 [TEST] run (generated_at < requested_at) 은 fresh 플래그를 소비하지 않음.
    _write_test_run("T_old.json", "2026-07-06T09:00:00+00:00")
    res_old = sync_daily_mailing_runs(source_dir=runs, db_path=db)
    assert res_old["imported"] == 1 and res_old["test_requests_cleared"] == 0
    assert "test_request" in build_active_scopes(db)[0]

    # (b) 요청보다 NEW 인 [TEST] run (generated_at >= requested_at) → 플래그 소비.
    _write_test_run("T_new.json", "2026-07-07T08:00:00+00:00")
    res_new = sync_daily_mailing_runs(source_dir=runs, db_path=db)
    # 두 번들 모두 재import 되지만(runs_index 전량), 최신(T_new)이 요청보다 새로워 1건 소비.
    assert res_new["imported"] == 2 and res_new["test_requests_cleared"] == 1

    export_scopes(db, publish=False, scope_root=root)
    scope1 = json.loads((root / "1.json").read_text(encoding="utf-8"))
    assert "test_request" not in scope1
    index = json.loads((root / "scopes_index.json").read_text(encoding="utf-8"))
    assert next(e for e in index if e["subscription_id"] == "1")["has_test_request"] is False


def test_non_test_run_does_not_clear_flag(tmp_path, monkeypatch):
    _no_tokens(monkeypatch)
    db = _make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        ensure_test_request_column(conn)
        conn.execute(
            "UPDATE mail_subscription SET test_request_json=? WHERE id=2",
            (json.dumps({"requested_at": "x", "requested_by": "j@msd.com"}),),
        )
        conn.commit()
    runs = tmp_path / "runs"
    runs.mkdir()
    bundle = {"payload": {"run_id": "R1", "generated_at": "2026-07-07T08:00:00+09:00",
                          "subscription_id": 2, "keywords": [], "status": "quality_gated_draft"},
              "articles": []}
    (runs / "R1.json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    res = sync_daily_mailing_runs(source_dir=runs, db_path=db)
    assert res["imported"] == 1 and res["test_requests_cleared"] == 0
    scopes = {s["subscription_id"]: s for s in build_active_scopes(db)}
    assert "test_request" in scopes["2"]  # 정규 run 은 플래그를 소비하지 않음


class _FakeRemote:
    """GitHub contents API fake — 네트워크 미접촉."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def get(self, name, token):
        if name not in self.files:
            return None, None
        return f"sha-{name}", self.files[name]

    def put(self, name, content, sha, token):
        self.files[name] = content
        self.puts.append(name)

    def delete(self, name, sha, token):
        self.files.pop(name, None)
        self.deletes.append(name)

    def list_dir(self, token):
        return [{"name": n, "sha": f"sha-{n}"} for n in sorted(self.files)]


def _patch_remote(monkeypatch, remote: _FakeRemote):
    monkeypatch.setattr(sx, "_gh_get_file", remote.get)
    monkeypatch.setattr(sx, "_gh_put_file", remote.put)
    monkeypatch.setattr(sx, "_gh_delete_file", remote.delete)
    monkeypatch.setattr(sx, "_gh_list_dir", remote.list_dir)


def test_publish_idempotent_skip_and_prune(tmp_path, monkeypatch):
    _no_tokens(monkeypatch)
    monkeypatch.setenv("DAILY_MAILING_SCOPES_TOKEN", "fake-token")
    db = _make_db(tmp_path)
    root = tmp_path / "scopes"
    remote = _FakeRemote()
    remote.files["stale_old_sub.json"] = b"{}"  # 채널에만 남은 삭제 구독
    _patch_remote(monkeypatch, remote)

    res1 = export_scopes(db, publish=True, scope_root=root)
    assert res1["channel"] == "github"
    assert res1["published"] == 3  # 1.json + 2.json + scopes_index.json
    assert res1["pruned"] == 1 and remote.deletes == ["stale_old_sub.json"]
    assert set(remote.files) == {"1.json", "2.json", "scopes_index.json"}
    assert not res1["errors"]

    # 내용 불변 재실행 → PUT 0 (멱등, 커밋 churn 없음)
    res2 = export_scopes(db, publish=True, scope_root=root)
    assert res2["published"] == 0 and res2["pruned"] == 0 and not res2["errors"]

    # 구독 2 비활성화 → 원격 2.json prune + index 갱신 발행
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE mail_subscription SET active=0 WHERE id=2")
        conn.commit()
    res3 = export_scopes(db, publish=True, scope_root=root)
    assert res3["active"] == 1
    assert res3["pruned"] == 1 and "2.json" in remote.deletes
    assert res3["published"] == 1  # index 만 변경
    assert set(remote.files) == {"1.json", "scopes_index.json"}
    assert res3["local_pruned"] == 1 and not (root / "2.json").exists()


def test_remote_prune_skipped_on_partial_local_write(tmp_path, monkeypatch):
    """부분 로컬 write 실패 시 원격 DELETE prune 을 건너뛰어 active 원격본/인덱스를 보호."""
    _no_tokens(monkeypatch)
    monkeypatch.setenv("DAILY_MAILING_SCOPES_TOKEN", "fake-token")
    db = _make_db(tmp_path)
    root = tmp_path / "scopes"
    remote = _FakeRemote()
    remote.files["1.json"] = b"stale-remote"       # active 구독 원격본 — 지워지면 안 됨
    remote.files["scopes_index.json"] = b"stale"    # 인덱스 — 절대 prune 금지
    remote.files["dead_sub.json"] = b"{}"           # 진짜 stale (하지만 이번엔 skip)
    _patch_remote(monkeypatch, remote)

    real_write = sx.write_scope_snapshot

    def flaky_write(scope, root=None):
        if str(scope.get("subscription_id")) == "2":
            raise OSError("disk full")  # 구독 2 로컬 write 실패
        return real_write(scope, root=root)

    monkeypatch.setattr(sx, "write_scope_snapshot", flaky_write)

    res = export_scopes(db, publish=True, scope_root=root)
    assert res["active"] == 2 and res["local"] == 1  # 하나 실패 → all-writes-succeeded 아님
    assert res["pruned"] == 0 and remote.deletes == []  # 원격 prune 전체 skip (self-heal 대기)
    assert "1.json" in remote.files and "scopes_index.json" in remote.files
    assert "dead_sub.json" in remote.files  # active 아님에도 이번 사이클엔 보존
    assert res["local_pruned"] == 0  # 로컬 prune 도 동일 가드로 skip


def test_remote_prune_never_targets_index(tmp_path, monkeypatch):
    """keep 은 active 정본 파일명 + index 로 구성 — 인덱스는 어떤 경우에도 DELETE 대상 아님."""
    _no_tokens(monkeypatch)
    monkeypatch.setenv("DAILY_MAILING_SCOPES_TOKEN", "fake-token")
    db = _make_db(tmp_path)
    remote = _FakeRemote()
    remote.files["scopes_index.json"] = b"old-index"
    remote.files["ghost.json"] = b"{}"
    _patch_remote(monkeypatch, remote)
    res = export_scopes(db, publish=True, scope_root=tmp_path / "scopes")
    assert "scopes_index.json" not in remote.deletes
    assert remote.deletes == ["ghost.json"] and res["pruned"] == 1
    assert "scopes_index.json" in remote.files


def test_publish_network_error_collected_not_raised(tmp_path, monkeypatch):
    _no_tokens(monkeypatch)
    monkeypatch.setenv("DAILY_MAILING_SCOPES_TOKEN", "fake-token")
    db = _make_db(tmp_path)

    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(sx, "_gh_get_file", boom)
    monkeypatch.setattr(sx, "_gh_list_dir", boom)
    res = export_scopes(db, publish=True, scope_root=tmp_path / "scopes")
    assert res["local"] == 2 and res["published"] == 0
    assert len(res["errors"]) >= 3  # 파일별 publish 실패 + prune listing 실패
