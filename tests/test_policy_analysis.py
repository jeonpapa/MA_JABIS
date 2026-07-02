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


def test_validate_grounding_and_terms(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    event = _make_event(root)
    fp = pa.content_fingerprint(event, root)
    base = {
        "event_id": "evt1", "content_fingerprint": fp,
        "summary": "유연계약제 접수 안내", "severity": "high",
        "msd_implication": {"rationale": "r", "next_action": "n"},
    }
    # 근거 없음 + data_gaps 없음 → 경고
    assert any("grounding" in w for w in pa.validate_analysis(base, event, root))
    # 소스에 없는 인용 → 경고
    bad_q = dict(base, evidence_quotes=[{"quote": "존재하지 않는 문장", "source": "body"}])
    assert any("not found in source" in w for w in pa.validate_analysis(bad_q, event, root))
    # 소스에 실재하는 인용 → 통과(경고 0)
    ok_q = dict(base, evidence_quotes=[{"quote": "유연계약 후보 품목", "source": "a.pdf"}])
    assert pa.validate_analysis(ok_q, event, root) == []
    # 금지 토큰 → 경고
    banned = dict(ok_q, summary="Precision 개선 안내")
    assert any("banned token" in w for w in pa.validate_analysis(banned, event, root))
    # '조건부 통과' → 경고
    cond = dict(ok_q, summary="조건부 통과 예상")
    assert any("조건부 통과" in w for w in pa.validate_analysis(cond, event, root))


def test_validate_rejects_degenerate_evidence(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    event = _make_event(root)
    fp = pa.content_fingerprint(event, root)
    base = {"event_id": "evt1", "content_fingerprint": fp, "summary": "s",
            "severity": "high", "msd_implication": {"rationale": "r", "next_action": "n"}}
    # 공백뿐인 인용 → grounding 경고 (실질 근거 0)
    blank = dict(base, evidence_quotes=[{"quote": "   ", "source": "body"}])
    assert any("grounding" in w for w in pa.validate_analysis(blank, event, root))
    # 비-dict 엔트리 → 크래시 대신 경고
    nondict = dict(base, evidence_quotes=["not a dict"])
    warns = pa.validate_analysis(nondict, event, root)
    assert any("evidence_quote" in w for w in warns)  # 예외 없이 경고
    # None → 크래시 대신 경고
    assert pa.validate_analysis(None, event, root)  # 비어있지 않은 경고 리스트


def test_resolve_curation_source(tmp_path: Path):
    root = tmp_path / "policy_intelligence"
    event = _make_event(root)
    # 분석 없음 → rule_fallback
    assert pa.resolve_curation(event, root)["curation_source"] == "rule_fallback"
    # 유효 분석 → hermes + 값 전달
    fp = pa.content_fingerprint(event, root)
    a = {"event_id": "evt1", "content_fingerprint": fp, "summary": "요약X",
         "severity": "high", "status": "진행중",
         "msd_implication": {"rationale": "r", "next_action": "n"}}
    p = pa.analysis_path("evt1", root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    got = pa.resolve_curation(event, root)
    assert got["curation_source"] == "hermes"
    assert got["summary"] == "요약X" and got["severity"] == "high"
