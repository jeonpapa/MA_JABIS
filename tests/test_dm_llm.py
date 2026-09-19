from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents.daily_mailing.llm import (
    RELEVANCE_SYSTEM,
    WRITER_SYSTEM,
    LLMError,
    OpenAIJsonLLM,
    judge_relevance,
    verify_quote,
    write_brief,
)


class RecordingLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, *, system, user, model):
        self.calls.append({"system": system, "user": json.loads(user), "model": model})
        return self.response


def test_relevance_keeps_only_known_scope_terms():
    llm = RecordingLLM({"relevant": True, "matched_scope_terms": ["파드셉", "존재하지않는용어"], "reason": "주제"})
    v = judge_relevance(title="파드셉 급여", text="본문", scope_terms=["파드셉", "약가 인하"], llm=llm, model="m")
    assert v.relevant is True
    assert v.matched_scope_terms == ("파드셉",)
    assert llm.calls[0]["system"] == RELEVANCE_SYSTEM
    assert llm.calls[0]["model"] == "m"
    assert llm.calls[0]["user"]["scope_terms"] == ["파드셉", "약가 인하"]


def test_relevance_requires_a_matched_term():
    llm = RecordingLLM({"relevant": True, "matched_scope_terms": [], "reason": "?"})
    v = judge_relevance(title="t", text="b", scope_terms=["파드셉"], llm=llm, model="m")
    assert v.relevant is False


def test_relevance_truncates_article_text():
    llm = RecordingLLM({"relevant": False, "matched_scope_terms": [], "reason": "무관"})
    judge_relevance(title="t", text="가" * 5000, scope_terms=["x"], llm=llm, model="m")
    assert len(llm.calls[0]["user"]["article_text"]) == 2000


def test_write_brief_parses_and_limits_bullets():
    llm = RecordingLLM({"summary_bullets": ["a", " ", "b", "c", "d"], "ma_implication": " 시사점 ",
                        "evidence_quote": " 인용 "})
    d = write_brief(title="t", body="본문", scope_terms=["x"], matched_scope_terms=["x"], llm=llm, model="w")
    assert d.summary_bullets == ("a", "b", "c")
    assert d.ma_implication == "시사점"
    assert d.evidence_quote == "인용"
    assert llm.calls[0]["system"] == WRITER_SYSTEM


@pytest.mark.parametrize("quote,expected", [
    ("요로상피암 1차 치료로 급여 확대", True),
    ("요로상피암   1차 치료로\n급여 확대", True),     # 공백 정규화
    ("“요로상피암 1차 치료로 급여 확대”", True),      # 따옴표 제거
    ("본문에 없는 문장입니다 정말로", False),
    ("짧음", False),                                  # 최소 길이 미달
    ("", False),
])
def test_verify_quote(quote, expected):
    body = "파드셉 병용요법이 요로상피암 1차 치료로 급여 확대 논의에 들어갔다."
    assert verify_quote(quote, body) is expected


def _fake_openai(content=None, exc=None):
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if exc is not None and calls["n"] == 1:
            raise exc
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, calls


def test_openai_wrapper_returns_dict_and_retries_once():
    client, calls = _fake_openai(content='{"ok": 1}', exc=RuntimeError("timeout"))
    llm = OpenAIJsonLLM(client=client, max_retries=1)
    assert llm.complete_json(system="s", user="u", model="m") == {"ok": 1}
    assert calls["n"] == 2


def test_openai_wrapper_raises_llm_error_after_retries():
    client, _ = _fake_openai(content="not json")
    llm = OpenAIJsonLLM(client=client, max_retries=1)
    with pytest.raises(LLMError):
        llm.complete_json(system="s", user="u", model="m")


def test_openai_client_constructed_with_bounded_timeout_and_no_sdk_retries(monkeypatch):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    OpenAIJsonLLM(api_key="k")
    assert captured == {"api_key": "k", "timeout": 30.0, "max_retries": 0}
    captured.clear()
    OpenAIJsonLLM(api_key="k", timeout=12.5)
    assert captured["timeout"] == 12.5 and captured["max_retries"] == 0


def test_openai_wrapper_does_not_retry_non_transient_errors():
    import httpx
    import openai

    auth_error = openai.AuthenticationError(
        "bad key", response=httpx.Response(401, request=httpx.Request("POST", "https://x")), body=None)
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise auth_error

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    llm = OpenAIJsonLLM(client=client, max_retries=3)
    with pytest.raises(LLMError):
        llm.complete_json(system="s", user="u", model="m")
    assert calls["n"] == 1
