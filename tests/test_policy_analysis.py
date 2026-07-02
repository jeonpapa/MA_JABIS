from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import policy_analysis as pa


def _make_event(root: Path) -> dict:
    folder = root / "raw" / "gmail" / "evt1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "body.txt").write_text("약가 유연계약제 시행 안내 본문", encoding="utf-8")
    raw = b"raw-eml-bytes-evt1"
    (folder / "message_sha256.txt").write_text(hashlib.sha256(raw).hexdigest() + "\n", encoding="utf-8")
    text_path = root / "extracted" / "evt1" / "doc.txt"
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("첨부 본문: 유연계약 후보 품목", encoding="utf-8")
    return {
        "event_id": "evt1",
        "subject": "Fw: [KRPIA] 약가 유연계약제 안내",
        "raw_folder": str(folder),
        "documents": [{"filename": "a.pdf", "sha256": "docsha1", "text_path": str(text_path)}],
    }


def test_fingerprint_is_deterministic_and_content_sensitive(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    event = _make_event(root)
    fp1 = pa.content_fingerprint(event, root)
    fp2 = pa.content_fingerprint(event, root)
    assert fp1 == fp2 and len(fp1) == 64
    event2 = json.loads(json.dumps(event))
    event2["documents"][0]["sha256"] = "docsha2"
    assert pa.content_fingerprint(event2, root) != fp1


def test_load_and_valid_gate(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    event = _make_event(root)
    fp = pa.content_fingerprint(event, root)
    good = {
        "event_id": "evt1", "content_fingerprint": fp,
        "summary": "유연계약제 접수 안내", "severity": "high",
        "msd_implication": {"rationale": "r", "next_action": "n"},
    }
    pa.analysis_path("evt1", root).parent.mkdir(parents=True, exist_ok=True)
    pa.analysis_path("evt1", root).write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    loaded = pa.load_analysis("evt1", root)
    assert pa.analysis_valid(loaded, event, root) is True
    stale = dict(good, content_fingerprint="deadbeef")
    assert pa.analysis_valid(stale, event, root) is False
    assert pa.load_analysis("nope", root) is None
    assert pa.analysis_valid(None, event, root) is False
