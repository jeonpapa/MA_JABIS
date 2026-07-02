from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.policy_intelligence import (
    load_policy_intelligence,
    load_event_detail,
    resolve_report_artifact_path,
    IMPACT_TEMPLATE_NAME,
)
from agents import policy_analysis as pa


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
    assert dashboard["topic_ledgers"][0]["topic_name"] == "약가 유연계약제"
    assert dashboard["topic_ledgers"][0]["events"] == ["evt-1"]
    assert dashboard["documents"][0]["source_kind"] == "attachment"
    assert "report_artifacts" in dashboard
    assert all("/" not in artifact["id"] for artifact in dashboard["report_artifacts"])
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


def test_general_media_mailing_events_are_excluded_from_policy_lane(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    manifest = {
        "created_at": "2026-06-30T10:18:37+00:00",
        "events": [
            {
                "event_id": "policy",
                "received_utc": "2026-06-30T00:00:00+00:00",
                "subject": "Fw: [KRPIA-HIRA] 약가 유연계약제 안내",
                "topic": "약가 유연계약제",
                "documents": [],
            },
            {
                "event_id": "prain",
                "received_utc": "2026-06-30T00:10:00+00:00",
                "subject": "Fw: (Prain_KEYTRUDA) 20260604 기사 공유",
                "topic": "기타",
                "documents": [],
            },
            {
                "event_id": "daily-mailing",
                "received_utc": "2026-06-30T00:20:00+00:00",
                "subject": "MA AI DOSSIER · DAILY MAILING DRAFT 주요 뉴스 &amp; Market Insight",
                "topic": "기타",
                "documents": [],
            },
        ],
    }
    _write_json(root / "manifests" / "pilot.json", manifest)

    dashboard = load_policy_intelligence(root=root, manifest_path=root / "manifests" / "pilot.json")

    assert dashboard["overview"]["event_count"] == 1
    assert dashboard["overview"]["excluded_general_media_event_count"] == 2
    assert [event["id"] for event in dashboard["events"]] == ["policy"]


def test_topic_ledgers_include_change_records_for_cumulative_history(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    manifest = {
        "created_at": "2026-07-02T00:00:00+00:00",
        "events": [
            {
                "event_id": "first",
                "received_utc": "2026-04-28T00:00:00+00:00",
                "subject": "Fw: [KRPIA] UPDATE: 약가제도 개편 특허만료 오리지널 규정해석-의견 요청(~4/28 오전 10시)",
                "topic": "기등재 약제 재평가·약가조정",
                "agencies": ["KRPIA"],
                "documents": [],
            },
            {
                "event_id": "latest",
                "received_utc": "2026-06-18T00:00:00+00:00",
                "subject": "Fw: [KRPIA] MOHW 6/18 민관협의체- 기등재 재평가 관련 협회의견서 공유",
                "topic": "기등재 약제 재평가·약가조정",
                "agencies": ["KRPIA", "복지부"],
                "documents": [],
            },
        ],
    }
    _write_json(root / "manifests" / "pilot.json", manifest)

    dashboard = load_policy_intelligence(root=root, manifest_path=root / "manifests" / "pilot.json")

    ledger = dashboard["topic_ledgers"][0]
    assert ledger["events"] == ["first", "latest"]
    assert ledger["latest_change"]["event_id"] == "latest"
    assert ledger["latest_change"]["change_type"] == "updated"
    assert "민관협의체" in ledger["latest_change"]["after"]
    assert dashboard["change_records"][0]["change_type"] == "new_topic"
    assert dashboard["change_records"][1]["change_type"] == "updated"
    assert all("raw_folder" not in json.dumps(record, ensure_ascii=False) for record in dashboard["change_records"])


def test_report_artifacts_include_downloadable_template_without_private_paths(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    _write_json(root / "manifests" / "pilot.json", {"created_at": "2026-07-02T00:00:00+00:00", "events": []})
    template = root / "reports" / IMPACT_TEMPLATE_NAME
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_bytes(b"xlsx bytes")

    dashboard = load_policy_intelligence(root=root, manifest_path=root / "manifests" / "pilot.json")
    template_artifact = next(a for a in dashboard["report_artifacts"] if a["filename"] == IMPACT_TEMPLATE_NAME)

    assert template_artifact["available"] is True
    assert template_artifact["format"] == "xlsx"
    assert template_artifact["download_url"].startswith("/api/policy-intelligence/reports/")
    assert str(root) not in json.dumps(template_artifact, ensure_ascii=False)
    assert resolve_report_artifact_path(template_artifact["id"], root=root) == template


def test_curation_overrides_rule_with_fallback(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    folder = root / "raw" / "gmail" / "evt1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "body.txt").write_text("유연계약제 접수 본문", encoding="utf-8")
    (folder / "message_sha256.txt").write_text(hashlib.sha256(b"eml").hexdigest() + "\n", encoding="utf-8")
    manifest = {"created_at": "2026-07-01T00:00:00+00:00", "event_count": 1, "events": [{
        "event_id": "evt1", "received_utc": "2026-07-01T00:00:00+00:00",
        "subject": "Fw: [KRPIA] 약가 유연계약제 안내", "topic": "약가 유연계약제",
        "agencies": ["KRPIA"], "email_body_chars": 10, "attachment_count_total": 0,
        "raw_folder": str(folder), "documents": []}]}
    mdir = root / "manifests"; mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "gmail_krpia_20260701_000000.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    event = manifest["events"][0]
    data = load_policy_intelligence(root=root)
    ev = data["events"][0]
    assert ev["curation_source"] == "rule_fallback"
    assert ev["severity"] == "High"
    assert data["overview"]["pending_analysis_count"] == 1
    assert data["overview"]["curated_event_count"] == 0

    a = {"event_id": "evt1", "content_fingerprint": pa.content_fingerprint(event, root),
         "summary": "유연계약제 접수 시작(사용자 확인 요망)", "severity": "medium", "status": "진행중",
         "msd_implication": {"rationale": "실제가 노출 리스크", "next_action": "SOP 점검"}}
    p = pa.analysis_path("evt1", root); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    data2 = load_policy_intelligence(root=root)
    ev2 = data2["events"][0]
    assert ev2["curation_source"] == "hermes"
    assert ev2["severity"] == "medium"
    assert ev2["summary"].startswith("유연계약제 접수 시작")
    assert data2["overview"]["curated_event_count"] == 1
    assert data2["overview"]["pending_analysis_count"] == 0
    ledger = data2["topic_ledgers"][0]
    assert ledger["msd_implication_latest"]["rationale"] == "실제가 노출 리스크"


def test_event_detail_includes_curation(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    folder = root / "raw" / "gmail" / "evt1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "body.txt").write_text("유연계약제 접수 본문", encoding="utf-8")
    (folder / "message_sha256.txt").write_text(hashlib.sha256(b"eml").hexdigest() + "\n", encoding="utf-8")
    manifest = {"created_at": "t", "events": [{
        "event_id": "evt1", "received_utc": "2026-07-01T00:00:00+00:00",
        "subject": "Fw: [KRPIA] 약가 유연계약제 안내", "topic": "약가 유연계약제",
        "agencies": ["KRPIA"], "raw_folder": str(folder), "documents": []}]}
    mdir = root / "manifests"; mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "gmail_krpia_20260701_000000.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    a = {"event_id": "evt1", "content_fingerprint": pa.content_fingerprint(manifest["events"][0], root),
         "summary": "S", "severity": "low", "status": "모니터링",
         "msd_implication": {"rationale": "R", "next_action": "N"},
         "evidence_quotes": [{"quote": "유연계약제 접수 본문", "source": "body"}]}
    p = pa.analysis_path("evt1", root); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    detail = load_event_detail("evt1", root=root)
    assert detail["curation_source"] == "hermes"
    assert detail["msd_implication"]["rationale"] == "R"
    assert detail["evidence_quotes"][0]["quote"] == "유연계약제 접수 본문"


def test_high_impact_counts_hermes_lowercase_high(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    folder = root / "raw" / "gmail" / "evt1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "body.txt").write_text("본문", encoding="utf-8")
    (folder / "message_sha256.txt").write_text(hashlib.sha256(b"eml").hexdigest() + "\n", encoding="utf-8")
    manifest = {"created_at": "t", "events": [{
        "event_id": "evt1", "received_utc": "2026-07-01T00:00:00+00:00",
        "subject": "Fw: [KRPIA] 약가 유연계약제 안내", "topic": "약가 유연계약제",
        "agencies": ["KRPIA"], "raw_folder": str(folder), "documents": []}]}
    mdir = root / "manifests"; mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "gmail_krpia_20260701_000000.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    a = {"event_id": "evt1", "content_fingerprint": pa.content_fingerprint(manifest["events"][0], root),
         "summary": "S", "severity": "high",
         "msd_implication": {"rationale": "R", "next_action": "N"}}
    p = pa.analysis_path("evt1", root); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    data = load_policy_intelligence(root=root)
    assert data["overview"]["high_impact_count"] == 1  # lowercase 'high' counted
