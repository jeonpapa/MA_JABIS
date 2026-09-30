from __future__ import annotations

import base64
import json
import stat

import pytest

from agents.notify import gmail_delivery as g

TOKEN = {"token": "t", "refresh_token": "r", "scopes": ["https://www.googleapis.com/auth/gmail.send"]}


def test_materialize_from_base64(monkeypatch, tmp_path):
    raw = base64.b64encode(json.dumps(TOKEN).encode()).decode()
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", raw)
    path = g.materialize_token_from_env(tmp_path / "secrets" / "t.json")
    assert json.loads(path.read_text()) == TOKEN
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert g.gmail_configured(path) is True


def test_materialize_from_raw_json(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", json.dumps(TOKEN))
    assert g.materialize_token_from_env(tmp_path / "t.json").exists()


def test_no_env_uses_existing_file_or_none(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_TOKEN_JSON", raising=False)
    assert g.materialize_token_from_env(tmp_path / "missing.json") is None
    existing = tmp_path / "e.json"
    existing.write_text(json.dumps(TOKEN))
    assert g.materialize_token_from_env(existing) == existing


def test_invalid_json_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", "not-json")
    with pytest.raises(ValueError):
        g.materialize_token_from_env(tmp_path / "t.json")


def test_resolve_token_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "x.json"))
    assert g.resolve_token_path() == tmp_path / "x.json"
