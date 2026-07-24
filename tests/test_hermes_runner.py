import importlib.util
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
