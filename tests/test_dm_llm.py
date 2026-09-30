from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents.daily_mailing.llm import (
    RELEVANCE_SYSTEM,
    SUPPORT_BODY_CHARS,
    SUPPORT_SYSTEM,
    WRITER_SYSTEM,
    ImplicationSupport,
    LLMError,
    OpenAIJsonLLM,
    judge_relevance,
    reject_implication_style,
    verify_implication,
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
    llm = OpenAIJsonLLM(client=client, max_retries=1, sleep=lambda s: None)
    assert llm.complete_json(system="s", user="u", model="m") == {"ok": 1}
    assert calls["n"] == 2


def test_openai_wrapper_raises_llm_error_after_retries():
    client, _ = _fake_openai(content="not json")
    llm = OpenAIJsonLLM(client=client, max_retries=1, sleep=lambda s: None)
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


def test_openai_wrapper_sleeps_before_retrying_transient_error():
    client, calls = _fake_openai(content='{"ok": 1}', exc=RuntimeError("timeout"))
    sleeps = []
    llm = OpenAIJsonLLM(client=client, max_retries=1, retry_backoff_seconds=2.0, sleep=sleeps.append)
    assert llm.complete_json(system="s", user="u", model="m") == {"ok": 1}
    assert calls["n"] == 2
    assert sleeps == [2.0]


def test_openai_wrapper_default_backoff_is_two_seconds_and_not_after_last_attempt():
    client, calls = _fake_openai(content="not json")
    sleeps = []
    llm = OpenAIJsonLLM(client=client, max_retries=2, sleep=sleeps.append)
    with pytest.raises(LLMError):
        llm.complete_json(system="s", user="u", model="m")
    assert calls["n"] == 3
    assert sleeps == [2.0, 2.0]          # 마지막 시도 뒤에는 대기하지 않는다


def test_openai_wrapper_no_sleep_for_non_retryable_error():
    import httpx
    import openai

    def create(**kwargs):
        raise openai.AuthenticationError(
            "bad key", response=httpx.Response(401, request=httpx.Request("POST", "https://x")), body=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    sleeps = []
    llm = OpenAIJsonLLM(client=client, max_retries=3, sleep=sleeps.append)
    with pytest.raises(LLMError):
        llm.complete_json(system="s", user="u", model="m")
    assert sleeps == []


# ---- MA 시사점 품질 (2026-09-19 파일럿: 경쟁사 관점·일반론·기사 주장 옮김) ----
def test_writer_prompt_frames_reader_as_korea_msd_market_access_and_prefers_empty():
    assert "한국MSD" in WRITER_SYSTEM and "Market Access" in WRITER_SYSTEM
    for phrase in ("위협", "경쟁 구도", "빈 문자열", "추측", "옹호"):
        assert phrase in WRITER_SYSTEM, phrase
    # JSON 계약 불변
    assert '{"summary_bullets": ["...", "..."], "ma_implication": "...", "evidence_quote": "..."}' in WRITER_SYSTEM


# ---- MSD 구체 연결 (2026-09-30 파일럿: 스타틴 복합제 인하 기사 → 'MSD 의 시장 접근 전략에 위협' 상투구) ----
def test_writer_prompt_requires_named_msd_asset_or_mechanism_and_bans_vague_forms():
    for asset in ("키트루다", "가다실", "린파자", "웰리렉", "브리디온", "에멘드", "박스뉴반스", "윈레브에어"):
        assert asset in WRITER_SYSTEM, asset
    for phrase in ("시장 접근 전략에 위협", "경쟁력에 영향", "기전", "45%", "재평가", "빈 문자열"):
        assert phrase in WRITER_SYSTEM, phrase
    assert '{"summary_bullets": ["...", "..."], "ma_implication": "...", "evidence_quote": "..."}' in WRITER_SYSTEM


def test_support_prompt_judges_msd_link_in_the_same_call():
    assert '{"supported": true 또는 false, "msd_specific": true 또는 false, "reason": "한국어 한 문장"}' in SUPPORT_SYSTEM
    for phrase in ("msd_specific", "키트루다", "시장 접근 전략에 위협", "기전"):
        assert phrase in SUPPORT_SYSTEM, phrase


def test_verify_implication_asks_support_model_once_and_returns_support_plus_msd_link():
    llm = RecordingLLM({"supported": True, "msd_specific": True, "reason": "키트루다 급여 경쟁에 직접 닿음"})
    res = verify_implication("경쟁 심화", "인용 문장", "본문 " * 10, llm=llm, model="gpt-4o-mini")
    assert res == ImplicationSupport(supported=True, msd_specific=True, reason="키트루다 급여 경쟁에 직접 닿음")
    assert len(llm.calls) == 1                                   # 지원 + MSD 연결 판정은 같은 1회 호출
    call = llm.calls[0]
    assert call["system"] == SUPPORT_SYSTEM and call["model"] == "gpt-4o-mini"
    assert call["user"] == {"implication": "경쟁 심화", "quote": "인용 문장", "article_body": ("본문 " * 10)[:SUPPORT_BODY_CHARS]}
    # 2026-09-30 파일럿: 인용은 '경쟁 심화' 를 뒷받침하지만 MSD 자산·기전이 없는 상투구
    res = verify_implication("x", "q", "b", llm=RecordingLLM({"supported": True, "msd_specific": False, "reason": "MSD 제품 없음"}),
                             model="m")
    assert res.supported is True and res.msd_specific is False and res.reason == "MSD 제품 없음"
    assert verify_implication("x", "q", "b", llm=RecordingLLM({"supported": False, "msd_specific": True}), model="m") \
        == ImplicationSupport(supported=False, msd_specific=True, reason="")
    # bool 만 신뢰 — 문자열·누락은 모두 False (fail-closed)
    assert verify_implication("x", "q", "b", llm=RecordingLLM({"supported": "yes", "msd_specific": "yes"}), model="m") \
        == ImplicationSupport(False, False, "")
    assert verify_implication("x", "q", "b", llm=RecordingLLM({}), model="m") == ImplicationSupport(False, False, "")
    # 구 계약 응답(msd_specific 누락) 은 MSD 연결 미확인으로 취급
    legacy = verify_implication("x", "q", "b", llm=RecordingLLM({"supported": True, "reason": "r"}), model="m")
    assert legacy.supported is True and legacy.msd_specific is False


def test_verify_implication_truncates_body_and_propagates_llm_error():
    llm = RecordingLLM({"supported": True, "msd_specific": True})
    verify_implication("x", "q", "가" * 10000, llm=llm, model="m")
    assert len(llm.calls[0]["user"]["article_body"]) == SUPPORT_BODY_CHARS

    class Down:
        def complete_json(self, **kw):
            raise LLMError("down")

    with pytest.raises(LLMError):
        verify_implication("x", "q", "b", llm=Down(), model="m")


@pytest.mark.parametrize("implication,title,expected", [
    ("", "듀카브 등 80개 품목 약가 인하", None),
    ("듀카브 등 80개 품목 약가 인하.", "듀카브 등 80개 품목 약가 인하", "restates_title"),
    ("듀카브 등 80개 품목 약가가 인하됐다", "듀카브·램시마 등 80개 품목 약가 평균 4.6% 인하…339억원 절감", None),
    ("사용량 연동 인하가 MSD 다품목에도 적용될 수 있어 자사 인하 리스크 점검이 필요하다", "80개 품목 약가 인하", "advocacy"),
    ("복지부는 신속히 추진해야 한다", "약사회 성분명 처방 촉구", "advocacy"),
    ("복지부는 신속히 추진해야한다", "약사회 성분명 처방 촉구", "advocacy"),
    ("약사회가 성분명 처방 도입을 촉구했다", "약사회 성분명 처방 촉구 집회", "advocacy"),
    ("제도 보완이 요구된다", "제도 개편안", "advocacy"),
    ("경쟁 약제의 1차 급여 진입으로 키트루다의 요로상피암 급여 협상 입지가 좁아진다", "파드셉 급여 확대", None),
])
def test_reject_implication_style(implication, title, expected):
    assert reject_implication_style(implication, title) == expected
