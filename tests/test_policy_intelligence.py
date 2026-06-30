from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.policy_intelligence import load_policy_intelligence


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_load_policy_intelligence_builds_dashboard_sections(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    text_path = root / "extracted" / "text" / "event1" / "doc.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("약가유연계약제 시행 관련 안내", encoding="utf-8")

    manifest = {
        "created_at": "2026-06-29T10:18:37+00:00",
        "event_count": 1,
        "events": [
            {
                "event_id": "evt-1",
                "received_utc": "2026-06-29T10:03:59+00:00",
                "subject": "Fw: [KRPIA-NHIS] 약가 유연계약제-기등재 접수 시작안내(4/30~)",
                "topic": "약가 유연계약제",
                "agencies": ["공단", "KRPIA"],
                "deadline_hint_from_subject": None,
                "email_body_chars": 1200,
                "attachment_count_total": 2,
                "extractable_document_count": 1,
                "extracted_document_chars": 15,
                "raw_folder": str(root / "raw" / "gmail" / "event1"),
                "documents": [
                    {
                        "filename": "안내.pdf",
                        "status": "ok",
                        "chars": 15,
                        "text_path": str(text_path),
                    }
                ],
            }
        ],
    }
    _write_json(root / "manifests" / "pilot.json", manifest)

    dashboard = load_policy_intelligence(root=root, manifest_path=root / "manifests" / "pilot.json")

    assert dashboard["overview"]["event_count"] == 1
    assert dashboard["overview"]["document_count"] == 1
    assert dashboard["overview"]["high_impact_count"] == 1
    assert dashboard["events"][0]["status"] == "implementation"
    assert dashboard["events"][0]["severity"] == "High"
    assert dashboard["topics"][0]["topic"] == "약가 유연계약제"
    assert dashboard["topics"][0]["event_count"] == 1
    assert dashboard["documents"][0]["source_kind"] == "attachment"
    assert dashboard["impact_candidates"][0]["priority"] == 2
    assert "실제가" in dashboard["impact_candidates"][0]["rationale"]


def test_load_policy_intelligence_keeps_raw_paths_internal(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    manifest = {
        "created_at": "2026-06-29T10:18:37+00:00",
        "events": [
            {
                "event_id": "evt-2",
                "received_utc": "2026-06-29T10:00:00+00:00",
                "subject": "Fw: [KRPIA] MOHW 6/18 민관협의체- 기등재 재평가 관련 협회의견서 공유",
                "topic": "기등재 약제 재평가·약가조정",
                "agencies": ["복지부", "KRPIA"],
                "deadline_hint_from_subject": None,
                "raw_folder": str(root / "raw" / "gmail" / "event2"),
                "documents": [],
            }
        ],
    }
    _write_json(root / "manifests" / "pilot.json", manifest)

    dashboard = load_policy_intelligence(root=root, manifest_path=root / "manifests" / "pilot.json")

    event = dashboard["events"][0]
    assert event["severity"] == "Very High"
    assert event["status"] == "high-risk pending"
    assert "raw_folder" not in event
    assert dashboard["impact_candidates"][0]["priority"] == 1

    payload = json.dumps(dashboard, ensure_ascii=False)
    assert str(root) not in payload
    assert "source_manifest" not in dashboard["overview"]
    assert dashboard["overview"]["source_batch_id"] == "pilot"


def test_missing_manifest_is_explicit_configuration_error(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    try:
        load_policy_intelligence(root=root, manifest_path=root / "manifests" / "missing.json")
    except FileNotFoundError as exc:
        assert "Policy intelligence manifest not found" in str(exc)
    else:
        raise AssertionError("missing manifest should not silently return empty dashboard data")


def test_load_policy_intelligence_defaults_to_latest_gmail_manifest(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    old_manifest = {
        "created_at": "2026-06-29T10:18:37+00:00",
        "events": [
            {
                "event_id": "old",
                "received_utc": "2026-06-29T00:00:00+00:00",
                "subject": "old",
                "topic": "기타",
                "documents": [],
            }
        ],
    }
    latest_manifest = {
        "created_at": "2026-06-30T10:18:37+00:00",
        "events": [
            {
                "event_id": "latest",
                "received_utc": "2026-06-30T00:00:00+00:00",
                "subject": "Fw: [KRPIA-HIRA] 약가 유연계약제 안내",
                "topic": "약가 유연계약제",
                "documents": [],
            }
        ],
    }
    _write_json(root / "manifests" / "pilot_krpia_20260629.json", old_manifest)
    _write_json(root / "manifests" / "gmail_krpia_20260630_090000.json", latest_manifest)
    _write_json(
        root / "manifests" / "latest_ingest_status.json",
        {"manifest_name": "gmail_krpia_20260630_090000.json"},
    )

    dashboard = load_policy_intelligence(root=root)

    assert dashboard["overview"]["source_batch_id"] == "gmail_krpia_20260630_090000"
    assert dashboard["events"][0]["id"] == "latest"
