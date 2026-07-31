from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import drug_identity as di


def test_brand_input_resolves_to_brand_and_inn():
    cands = di.resolve_identity("keytruda")
    assert cands, "keytruda 는 alias 맵에 있어 후보가 나와야 함"
    top = cands[0]
    assert top["slug"] == "keytruda"
    assert top["inn"] == "pembrolizumab"
    assert top["matched_as"] == "brand"
    assert top["source"] == "alias"


def test_ingredient_input_resolves_same_slug():
    cands = di.resolve_identity("pembrolizumab")
    assert cands and cands[0]["slug"] == "keytruda"
    assert cands[0]["matched_as"] == "ingredient"


def test_substring_prefix_matches():
    cands = di.resolve_identity("keytru")
    assert any(c["slug"] == "keytruda" for c in cands)


def test_blank_returns_empty():
    assert di.resolve_identity("") == []
    assert di.resolve_identity(" ") == []


def test_unknown_falls_back_to_mfds(monkeypatch):
    # alias 미스 → MFDS 폴백 호출. lookup_permit 을 모킹.
    def _fake_lookup(item_name=None, ingredient=None, edi_code=None):
        return {"source": "api",
                "summary": {"item_eng_name": "Faketril", "main_ingr_eng": "faketinib"}}
    monkeypatch.setattr(di, "lookup_permit", _fake_lookup)
    cands = di.resolve_identity("zzq-unknown-xyz")
    assert cands and cands[0]["source"] == "mfds"
    assert cands[0]["inn"] == "faketinib"
