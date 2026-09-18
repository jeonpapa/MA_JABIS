"""Daily Mailing LLM 단계 — 관련성 판정 · 브리핑 작성 · 인용 검증.

설계 §5 ⑤⑦. 모델은 EngineConfig(settings.json daily_mailing) 에서 주입.
- 관련성: 스콥 항목을 '주제로 다루는' 기사만 통과, matched_scope_terms 는 주어진 항목 중에서만.
- 작성: 본문 근거 요약 2~3개 + MA 시사점 1문장 + 본문 원문 인용. 인용은 verify_quote 로 코드 검증.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

logger = logging.getLogger(__name__)

MIN_QUOTE_CHARS = 10
RELEVANCE_TEXT_CHARS = 2000
WRITER_BODY_CHARS = 6000

RELEVANCE_SYSTEM = """너는 한국 제약 Market Access 뉴스 모니터링 편집자다.
구독자의 모니터링 스콥(키워드·브랜드·회사·질환·정책토픽)과 기사 1건이 주어진다.
기사가 스콥 항목 중 하나 이상을 '주제로 다루는지' 판정한다.
- 스콥 항목이 기사의 핵심 사건·주체·정책이면 relevant=true.
- 스콥 항목이 나열·배경·광고·인사 동정으로 스쳐 언급만 되면 relevant=false.
- 스콥과 무관한 다른 약제·질환·정책 기사는 relevant=false.
반드시 JSON 객체 하나만 출력한다:
{"relevant": true 또는 false, "matched_scope_terms": ["주어진 스콥 항목 원문"], "reason": "한국어 한 문장 근거"}
matched_scope_terms 는 입력 scope_terms 의 문자열 중에서만 고른다."""

WRITER_SYSTEM = """너는 한국 제약 Market Access(MA) 리드를 위한 아침 브리핑 작성자다.
기사 본문(article_body)만 근거로 쓴다. 본문에 없는 사실·수치·전망을 만들지 않는다.
반드시 JSON 객체 하나만 출력한다:
{"summary_bullets": ["...", "..."], "ma_implication": "...", "evidence_quote": "..."}
- summary_bullets: 2~3개. 각 60자 안팎의 사실 요약(누가·무엇을·언제). 본문 사실만.
- ma_implication: 한 문장. 급여·약가·등재·위험분담·경쟁 관점에서 이 기사가 구독 스콥(matched_scope_terms)에 갖는 의미. 본문으로 뒷받침되지 않으면 빈 문자열.
- evidence_quote: ma_implication 의 근거가 되는 본문 문장을 글자 그대로 복사(20~200자). ma_implication 이 빈 문자열이면 빈 문자열."""


class LLMError(RuntimeError):
    """LLM 호출/파싱 실패 (재시도 소진)."""


class JsonLLM(Protocol):
    def complete_json(self, *, system: str, user: str, model: str) -> dict: ...


class OpenAIJsonLLM:
    """OpenAI chat.completions JSON 모드 래퍼. 실패 시 max_retries 회 재시도 후 LLMError."""

    def __init__(self, api_key: str | None = None, *, timeout: float = 60.0,
                 max_retries: int = 1, client: Any | None = None):
        if client is None:
            from openai import OpenAI

            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise LLMError("OPENAI_API_KEY is required")
            client = OpenAI(api_key=key, timeout=timeout)
        self._client = client
        self._max_retries = max_retries

    def complete_json(self, *, system: str, user: str, model: str) -> dict:
        last: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=model,
                    temperature=0.1,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                )
                data = json.loads(resp.choices[0].message.content or "{}")
                if not isinstance(data, dict):
                    raise ValueError("LLM JSON 이 객체가 아님")
                return data
            except Exception as exc:
                last = exc
                logger.warning("[daily_mailing.llm] 호출 실패 (%d/%d): %s",
                               attempt + 1, self._max_retries + 1, exc)
        raise LLMError(f"LLM 호출 실패: {last}")


@dataclass(frozen=True)
class RelevanceVerdict:
    relevant: bool
    matched_scope_terms: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class BriefDraft:
    summary_bullets: tuple[str, ...]
    ma_implication: str
    evidence_quote: str


def judge_relevance(*, title: str, text: str, scope_terms: Iterable[str],
                    llm: JsonLLM, model: str) -> RelevanceVerdict:
    terms = [str(t) for t in scope_terms]
    user = json.dumps({"scope_terms": terms, "title": title,
                       "article_text": (text or "")[:RELEVANCE_TEXT_CHARS]}, ensure_ascii=False)
    data = llm.complete_json(system=RELEVANCE_SYSTEM, user=user, model=model)
    allowed = set(terms)
    matched = tuple(t for t in (data.get("matched_scope_terms") or [])
                    if isinstance(t, str) and t in allowed)
    return RelevanceVerdict(
        relevant=bool(data.get("relevant")) and bool(matched),
        matched_scope_terms=matched,
        reason=str(data.get("reason") or "")[:300],
    )


def write_brief(*, title: str, body: str, scope_terms: Iterable[str],
                matched_scope_terms: Iterable[str], llm: JsonLLM, model: str) -> BriefDraft:
    user = json.dumps({"scope_terms": list(scope_terms),
                       "matched_scope_terms": list(matched_scope_terms),
                       "title": title,
                       "article_body": (body or "")[:WRITER_BODY_CHARS]}, ensure_ascii=False)
    data = llm.complete_json(system=WRITER_SYSTEM, user=user, model=model)
    bullets = tuple(str(b).strip() for b in (data.get("summary_bullets") or []) if str(b).strip())[:3]
    return BriefDraft(
        summary_bullets=bullets,
        ma_implication=str(data.get("ma_implication") or "").strip(),
        evidence_quote=str(data.get("evidence_quote") or "").strip(),
    )


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def verify_quote(quote: str, body: str) -> bool:
    """인용문이 본문에 (공백 정규화 후) 그대로 존재하는지. 따옴표는 양끝에서 제거."""
    q = _norm((quote or "").strip().strip("\"'“”‘’"))
    return len(q) >= MIN_QUOTE_CHARS and q in _norm(body)
