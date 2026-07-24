import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "hermes_runner", Path(__file__).resolve().parent.parent / "scripts" / "hermes_runner.py")
hermes_runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hermes_runner)
decide_delivery = hermes_runner.decide_delivery


def test_test_request_triggers_delivery_with_test_prefix():
    d = decide_delivery({"delivery_mode": "gmail_draft"}, has_test_request=True)
    assert d == {"deliver": True, "mode": "gmail_draft", "subject_prefix": "[TEST] ", "is_test": True}


def test_normal_edit_does_not_deliver():
    d = decide_delivery({"delivery_mode": "gmail_send"}, has_test_request=False)
    assert d["deliver"] is False and d["is_test"] is False


def test_default_mode_is_gmail_draft():
    d = decide_delivery({}, has_test_request=True)
    assert d["mode"] == "gmail_draft"


def test_load_scope_local_reads_json(tmp_path):
    p = tmp_path / "42.json"
    p.write_text('{"subscription_id": 42, "delivery_mode": "gmail_draft"}', encoding="utf-8")
    scope = hermes_runner.load_scope_local(str(p))
    assert scope["subscription_id"] == 42


def test_fetch_scope_uses_raw_url(monkeypatch):
    calls = {}
    def fake_get(url, token):
        calls["url"] = url
        return b'{"subscription_id": 7}'
    monkeypatch.setattr(hermes_runner, "_http_get_bytes", fake_get)
    scope = hermes_runner.fetch_scope("7", token="tok")
    assert scope["subscription_id"] == 7
    assert calls["url"].endswith("/scopes/7.json")


def test_put_run_bundle_writes_stem_keyed_index(monkeypatch):
    puts = {}
    def fake_req(method, url, token, body=None):
        if method == "GET":
            raise hermes_runner.urllib.error.HTTPError(url, 404, "nf", {}, None)
        puts[url] = body
        return {}
    monkeypatch.setattr(hermes_runner, "_gh_request", fake_req)
    hermes_runner.put_run_bundle(
        "run_20260724_sub7.json",
        {"payload": {"run_id": "20260724", "subscription_id": 7}, "articles": [], "is_test": True},
        token="tok",
    )
    idx_url = next(u for u in puts if u.endswith("/runs/runs_index.json"))
    import base64 as _b64, json as _json
    index = _json.loads(_b64.b64decode(puts[idx_url]["content"]).decode("utf-8"))
    assert isinstance(index, dict)
    assert "run_20260724_sub7" in index            # stem 키 (소비자가 f'{stem}.json' 로 복원)
    assert index["run_20260724_sub7"]["subscription_id"] == 7
    assert index["run_20260724_sub7"]["is_test"] is True


def test_run_pipeline_parses_stdout_and_reads_html(monkeypatch, tmp_path):
    html = tmp_path / "d.html"; html.write_text("<b>x</b>", encoding="utf-8")
    jsonf = tmp_path / "d.json"; jsonf.write_text('{"run_id":"20260724","selected_count":3}', encoding="utf-8")
    summary = {"json": str(jsonf), "html": str(html), "review_board": None,
               "status": "ok", "selected_count": 3}
    class _P:
        returncode = 0
        stdout = json.dumps(summary)
        stderr = ""
    monkeypatch.setattr(hermes_runner.subprocess, "run", lambda *a, **k: _P())
    out = hermes_runner.run_pipeline("scope.json", str(tmp_path), "env", "db.sqlite")
    assert out["payload"]["run_id"] == "20260724"
    assert out["html_body"] == "<b>x</b>"
    assert out["summary"]["status"] == "ok"


def test_process_scope_delivers_on_test_request(monkeypatch):
    scope = {"subscription_id": 7, "name": "S", "delivery_mode": "gmail_draft",
             "emails": ["a@x.com"], "test_request": {"requested_at": "t"}}
    monkeypatch.setattr(hermes_runner, "run_pipeline", lambda *a, **k: {
        "summary": {}, "payload": {"run_id": "R1", "status": "quality_gated_draft"},
        "html_body": "<b>x</b>", "articles": [],
    })
    delivered = {}
    monkeypatch.setattr(hermes_runner, "_deliver", lambda scope, html, subject, decision: delivered.update(subject=subject) or {"ok": True})
    committed = {}
    monkeypatch.setattr(hermes_runner, "put_run_bundle", lambda name, bundle, token: committed.update(name=name, sub=bundle["payload"].get("subscription_id"), is_test=bundle.get("is_test")))
    out = hermes_runner.process_scope(scope, "scope.json", "/tmp/out", "env", "db", token="tok", commit=True)
    assert out["delivered"] is True
    assert delivered["subject"].startswith("[TEST] ")
    assert committed["name"] == "run_R1_sub7.json"
    assert committed["sub"] == 7 and committed["is_test"] is True


def test_process_scope_holds_send_on_insufficient_quality(monkeypatch):
    scope = {"subscription_id": 9, "delivery_mode": "gmail_send",
             "emails": ["a@x.com"], "test_request": {"requested_at": "t"}}
    monkeypatch.setattr(hermes_runner, "run_pipeline", lambda *a, **k: {
        "summary": {}, "payload": {"run_id": "R2", "status": "draft_only_insufficient_quality"},
        "html_body": "<b>x</b>", "articles": [],
    })
    called = {"deliver": False}
    monkeypatch.setattr(hermes_runner, "_deliver", lambda *a, **k: called.update(deliver=True) or {"ok": True})
    monkeypatch.setattr(hermes_runner, "put_run_bundle", lambda name, bundle, token: None)
    out = hermes_runner.process_scope(scope, "s.json", "/tmp/out", "env", "db", token="tok", commit=True)
    assert called["deliver"] is False   # 품질 미달 → 발송 보류
    assert out["held"] is True and out["delivered"] is False
