from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.ingest import policy_analysis_sync as sync


def test_sync_from_local_dir_is_idempotent(tmp_path: Path):
    src = tmp_path / "src" / "policy_intelligence" / "analysis"
    src.mkdir(parents=True, exist_ok=True)
    (src / "evt1.json").write_text(json.dumps({"event_id": "evt1", "content_fingerprint": "fp"}), encoding="utf-8")
    (src / "analysis_manifest.json").write_text(json.dumps({"evt1": {"fingerprint": "fp"}}), encoding="utf-8")
    dst_root = tmp_path / "policy_intelligence"

    r1 = sync.sync_analysis(source_dir=src, root=dst_root)
    assert r1["copied"] == 2
    assert (dst_root / "analysis" / "evt1.json").exists()
    r2 = sync.sync_analysis(source_dir=src, root=dst_root)
    assert r2["copied"] == 0 and r2["skipped"] == 2
    (src / "evt1.json").write_text(json.dumps({"event_id": "evt1", "content_fingerprint": "fp2"}), encoding="utf-8")
    r3 = sync.sync_analysis(source_dir=src, root=dst_root)
    assert r3["copied"] == 1
