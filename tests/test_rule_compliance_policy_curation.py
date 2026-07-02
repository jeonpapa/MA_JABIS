from __future__ import annotations
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.rule_compliance.checks import check_policy_curation_grounding, CHECKS, CheckResult
from agents import policy_analysis as pa


def _build(root: Path):
    folder = root / "raw" / "gmail" / "evt1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "body.txt").write_text("유연계약제 접수 본문", encoding="utf-8")
    (folder / "message_sha256.txt").write_text(hashlib.sha256(b"eml").hexdigest() + "\n", encoding="utf-8")
    manifest = {"events": [{
        "event_id": "evt1", "received_utc": "2026-07-01T00:00:00+00:00",
        "subject": "Fw: [KRPIA] 약가 유연계약제 안내", "topic": "약가 유연계약제",
        "agencies": ["KRPIA"], "raw_folder": str(folder), "documents": []}]}
    md = root / "manifests"; md.mkdir(parents=True, exist_ok=True)
    (md / "gmail_krpia_20260701_000000.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest["events"][0]


def test_registered_in_checks():
    assert "project_hermes_krpia_curation" in CHECKS
    assert CHECKS["project_hermes_krpia_curation"] is check_policy_curation_grounding


def test_skip_when_no_manifests(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICY_INTELLIGENCE_ROOT", str(tmp_path / "policy_intelligence"))
    r = check_policy_curation_grounding("project_hermes_krpia_curation", tmp_path)
    assert isinstance(r, CheckResult) and r.status == "skip"


def test_pass_with_grounded_sidecar(tmp_path, monkeypatch):
    root = tmp_path / "policy_intelligence"
    monkeypatch.setenv("POLICY_INTELLIGENCE_ROOT", str(root))
    ev = _build(root)
    a = {"event_id": "evt1", "content_fingerprint": pa.content_fingerprint(ev, root),
         "summary": "s", "severity": "high",
         "msd_implication": {"rationale": "r", "next_action": "n"},
         "evidence_quotes": [{"quote": "유연계약제 접수 본문", "source": "body"}]}
    p = pa.analysis_path("evt1", root); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    r = check_policy_curation_grounding("project_hermes_krpia_curation", tmp_path)
    assert r.status == "pass"
    assert r.metrics["curated"] == 1 and r.metrics["pending_analysis_count"] == 0


def test_fail_with_ungrounded_sidecar(tmp_path, monkeypatch):
    root = tmp_path / "policy_intelligence"
    monkeypatch.setenv("POLICY_INTELLIGENCE_ROOT", str(root))
    ev = _build(root)
    a = {"event_id": "evt1", "content_fingerprint": pa.content_fingerprint(ev, root),
         "summary": "s", "severity": "high",
         "msd_implication": {"rationale": "r", "next_action": "n"},
         "evidence_quotes": [{"quote": "소스에 없는 문장", "source": "body"}]}
    p = pa.analysis_path("evt1", root); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    r = check_policy_curation_grounding("project_hermes_krpia_curation", tmp_path)
    assert r.status == "fail"
    assert r.metrics["grounding_problems"] == 1
