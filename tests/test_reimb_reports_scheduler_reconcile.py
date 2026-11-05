from __future__ import annotations

import hashlib
import json

import scheduler as sch
from agents import reimb_reports as rr


def test_reports_sync_reconciles_db_even_when_manifest_hash_is_unchanged(tmp_path, monkeypatch):
    manifest = {"schema_version": 1, "reports": []}
    digest = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    hash_file = tmp_path / "data" / "reimb" / ".last_reports_hash"
    hash_file.parent.mkdir(parents=True)
    hash_file.write_text(digest)
    called = []

    monkeypatch.setattr(sch, "BASE_DIR", tmp_path)
    monkeypatch.delenv("REPORTS_DATA_URL", raising=False)
    monkeypatch.setattr(rr, "load_reports_manifest", lambda source=None: manifest)
    monkeypatch.setattr(
        rr,
        "sync_reports",
        lambda value: called.append(value) or {
            "reports_in_manifest": 0,
            "ingested": [],
            "metadata_updated": [],
            "skipped": 0,
            "errors": [],
            "warnings": [],
        },
    )

    result = sch._reimb_reports_sync()

    assert called == [manifest]
    assert result["counts"]["metadata_updated"] == 0
