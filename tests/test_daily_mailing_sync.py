from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.ingest.daily_mailing_sync import sync_daily_mailing_runs
from agents.daily_mailing.storage import load_admin_kanban


def test_sync_local_run_bundle_imports_to_kanban(tmp_path):
    db = tmp_path / "t.db"
    src = tmp_path / "runs"; src.mkdir()
    bundle = {
        "payload": {"run_id": "R1", "generated_at": "2026-07-04T08:00:00+09:00",
                    "keywords": ["키트루다"], "media": ["dailypharm"], "owner_email": "j@msd.com",
                    "recipients": ["j@msd.com"], "status": "quality_gated_draft",
                    "discovered_count": 5, "recent_count": 3, "selected_count": 2,
                    "delivery_status": "gmail_draft"},
        "articles": [
            {"article_id": "a1", "title": "키트루다 급여 확대", "source_name": "데일리팜",
             "source_tier": "media_tier_A", "source_status": "publisher_verified",
             "priority": "High", "ma_relevance": 4, "review_status": "ready_for_writer",
             "quality_flags": [], "selected_for_draft": True, "score": 9.0,
             "matched_keywords": ["키트루다"]},
        ],
    }
    (src / "R1.json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    res = sync_daily_mailing_runs(source_dir=src, db_path=db)
    assert res["imported"] == 1 and not res.get("errors")
    k = load_admin_kanban(db_path=db)
    assert any(r["run_id"] == "R1" for r in k["runs"])
    # 아티클이 Writer Agent lane(selected_for_draft)에 들어감
    writer = next(l for l in k["lanes"] if l["name"] == "Writer Agent")
    assert any(a["article_id"] == "a1" for a in writer["items"])
    # 멱등 재적재
    assert sync_daily_mailing_runs(source_dir=src, db_path=db)["imported"] == 1
    assert len([r for r in load_admin_kanban(db_path=db)["runs"] if r["run_id"] == "R1"]) == 1


def _hermes_bundle(sub, run_id, generated_at, *, delivery="no_send_quality_hold", msg_id=None):
    payload = {"run_id": run_id, "generated_at": generated_at, "subscription_id": sub,
               "dashboard_scope_snapshot": {"owner_email": "o@msd.com", "recipients": ["r@msd.com"],
                                            "keywords": ["키트루다"]},
               "status": "quality_gated_draft", "delivery_status": delivery, "approval_status": "agent_team",
               "discovered_count": 9, "recent_count": 4, "selected_count": 1}
    if msg_id:
        payload["gmail_message_id"] = msg_id
    return {"subscription_id": sub, "is_test": False, "executed_at": generated_at, "payload": payload,
            "articles": [{"article_id": f"{run_id}-a1", "title": "키트루다 급여 확대", "selected_for_draft": True,
                          "review_status": "ready_for_writer", "quality_flags": [], "score": 1.0}]}


def test_sync_dated_hermes_layout_namespaces_and_records_sent(tmp_path):
    """헤르메스 현행 레이아웃 runs/<YYYY-MM-DD>/<sub>_<run>.json — run_date·run_id·수신자 정규화, 발송 증빙 기록."""
    from agents.daily_mailing.run_store import get_run, list_runs_for_date
    db = tmp_path / "t.db"
    day = tmp_path / "runs" / "2026-10-28"; day.mkdir(parents=True)
    (day / "14_20261028_060010.json").write_text(json.dumps(
        _hermes_bundle(14, "20261028_060010", "2026-10-28T06:00:10+09:00", delivery="sent", msg_id="gm-1"),
        ensure_ascii=False), encoding="utf-8")
    (day / "15_20261028_060030.json").write_text(json.dumps(
        _hermes_bundle(15, "20261028_060030", "2026-10-28T06:00:30+09:00"), ensure_ascii=False), encoding="utf-8")
    res = sync_daily_mailing_runs(source_dir=tmp_path / "runs", db_path=db)
    assert res["imported"] == 2 and not res["errors"] and res["sent_recorded"] == 1
    runs = {r["subscription_id"]: r for r in list_runs_for_date("2026-10-28", db_path=db)}
    assert set(runs) == {14, 15}
    sent = get_run("hermes_14_20261028_060010", db_path=db)
    assert (sent["delivery_status"], sent["gmail_message_id"]) == ("sent", "gm-1")
    assert json.loads(sent["recipients_json"]) == ["r@msd.com"] and sent["owner_email"] == "o@msd.com"
    held = get_run("hermes_15_20261028_060030", db_path=db)
    assert held["delivery_status"] == "no_send_quality_hold" and not held["gmail_message_id"]
    # 멱등 재적재
    assert sync_daily_mailing_runs(source_dir=tmp_path / "runs", db_path=db)["imported"] == 2
    assert len(list_runs_for_date("2026-10-28", db_path=db)) == 2


def test_sync_dated_newer_hermes_rerun_supersedes_older_same_day(tmp_path):
    from agents.daily_mailing.run_store import list_runs_for_date
    db = tmp_path / "t.db"
    day = tmp_path / "runs" / "2026-10-28"; day.mkdir(parents=True)
    (day / "14_20261028_060010.json").write_text(json.dumps(
        _hermes_bundle(14, "20261028_060010", "2026-10-28T06:00:10+09:00")), encoding="utf-8")
    (day / "14_20261028_091500.json").write_text(json.dumps(
        _hermes_bundle(14, "20261028_091500", "2026-10-28T09:15:00+09:00", delivery="sent", msg_id="gm-2")),
        encoding="utf-8")
    sync_daily_mailing_runs(source_dir=tmp_path / "runs", db_path=db)
    runs = list_runs_for_date("2026-10-28", db_path=db)
    assert [r["run_id"] for r in runs] == ["hermes_14_20261028_091500"]
    assert runs[0]["delivery_status"] == "sent"


def test_sync_dated_skips_when_server_run_exists_same_day(tmp_path):
    from agents.daily_mailing.storage import persist_daily_mailing_run
    from agents.daily_mailing.run_store import list_runs_for_date
    db = tmp_path / "t.db"
    persist_daily_mailing_run({"run_id": "dm_14_20261027_080139", "generated_at": "2026-10-27T08:01:39+09:00",
                               "subscription_id": 14, "run_date": "2026-10-27"}, articles=[], db_path=db)
    day = tmp_path / "runs" / "2026-10-27"; day.mkdir(parents=True)
    (day / "14_20261027_060016.json").write_text(json.dumps(
        _hermes_bundle(14, "20261027_060034", "2026-10-27T06:00:34+09:00")), encoding="utf-8")
    res = sync_daily_mailing_runs(source_dir=tmp_path / "runs", db_path=db)
    assert res["imported"] == 0 and res["skipped"] == 1 and len(res["duplicates"]) == 1 and not res["errors"]
    assert [r["run_id"] for r in list_runs_for_date("2026-10-27", db_path=db)] == ["dm_14_20261027_080139"]
