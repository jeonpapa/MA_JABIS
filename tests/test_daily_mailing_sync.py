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
