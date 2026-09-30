"""Daily Mailing LLM 단계 — 관련성 판정 · 브리핑 작성 · 인용 검증 · 시사점 지원 검증.

설계 §5 ⑤⑦. 모델은 EngineConfig(settings.json daily_mailing) 에서 주입.
- 관련성: 스콥 항목을 '주제로 다루는' 기사만 통과, matched_scope_terms 는 주어진 항목 중에서만.
- 작성: 본문 근거 요약 2~3개 + MA 시사점 1문장 + 본문 원문 인용. 인용은 verify_quote 로 코드 검증.
- 시사점 (2026-09-19 파일럿 이후): 독자는 한국MSD MA 팀. 시사점은 'MSD 의 한국 접근 업무에 무엇을 뜻하는가' 만,
  근거 없으면 빈 문자열이 정답. 코드 가드(reject_implication_style: 제목 재진술·옹호 표현) →
  인용 검증(verify_quote) → 지원 검증(verify_implication: 인용이 시사점을 '뒷받침' 하는지 2차 LLM) 순으로 통과해야 렌더.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable, Protocol

from .cluster import normalize_title

logger = logging.getLogger(__name__)

MIN_QUOTE_CHARS = 10
RELEVANCE_TEXT_CHARS = 2000
WRITER_BODY_CHARS = 6000
SUPPORT_BODY_CHARS = 4000
TITLE_RESTATEMENT_THRESHOLD = 0.8     # 정규화 제목↔시사점 SequenceMatcher ratio 초과 시 '제목 재진술'
# 기사·단체의 주장을 옮긴 옹호 표현 — 시사점은 사실 기반 결과만 (공백 제거 후 비교)
ADVOCACY_MARKERS = ("해야한다", "필요하다", "촉구", "요구된다")

RELEVANCE_SYSTEM = """너는 한국 제약 Market Access 뉴스 모니터링 편집자다.
구독자의 모니터링 스콥(키워드·브랜드·회사·질환·정책토픽)과 기사 1건이 주어진다.
기사가 스콥 항목 중 하나 이상을 '주제로 다루는지' 판정한다.
- 스콥 항목이 기사의 핵심 사건·주체·정책이면 relevant=true.
- 스콥 항목이 나열·배경·광고·인사 동정으로 스쳐 언급만 되면 relevant=false.
- 스콥과 무관한 다른 약제·질환·정책 기사는 relevant=false.
반드시 JSON 객체 하나만 출력한다:
{"relevant": true 또는 false, "matched_scope_terms": ["주어진 스콥 항목 원문"], "reason": "한국어 한 문장 근거"}
matched_scope_terms 는 입력 scope_terms 의 문자열 중에서만 고른다."""

WRITER_SYSTEM = """너는 한국MSD(MSD Korea) Market Access(MA) 팀을 위한 아침 브리핑 작성자다.
독자는 한국MSD 의 급여·약가·등재 담당자다. 기사 본문(article_body)만 근거로 쓴다. 본문에 없는 사실·수치·전망을 만들지 않는다.
반드시 JSON 객체 하나만 출력한다:
{"summary_bullets": ["...", "..."], "ma_implication": "...", "evidence_quote": "..."}
- summary_bullets: 2~3개. 각 60자 안팎의 사실 요약(누가·무엇을·언제). 본문 사실만. 기사나 단체의 주장·요구는 '~라고 주장했다'처럼 출처를 밝혀 사실로 적는다.
- ma_implication: 한 문장, 구체적으로. 이 기사가 한국MSD 의 한국 시장 접근 업무(급여·약가·등재)에 무엇을 뜻하는지만 쓴다 —
  (1) 자사 자산(키트루다·가다실·린파자·웰리렉·브리디온 등)의 기회 또는 위협, (2) 경쟁 구도 변화, (3) 급여·약가·등재 절차·일정 영향 중 하나.
  관점은 항상 한국MSD 다. 경쟁사 약제의 호재(임상 우위·급여·허가)는 MSD 에게 위협 또는 경쟁 심화로 쓴다. 경쟁사 입장에서 '긍정적'이라고 쓰지 않는다.
  기사·단체의 주장·촉구·요구를 그대로 옮기지 않는다(옹호·의견 금지: '~해야 한다', '~가 필요하다' 금지). 본문을 넘어선 추측(예상·가능성·전망) 금지.
  빈 문자열이 기본값이다. 한국MSD 의 MA 업무에 근거 있는 구체적 결과를 말할 수 없으면 빈 문자열을 낸다. 일상적인 업계 뉴스, MSD 자산·경쟁 구도와
  무관한 기사, '급여 결정에 영향' 같은 일반론만 가능한 기사는 빈 문자열이 올바르고 선호되는 출력이다. 억지로 채우지 않는다.
- evidence_quote: ma_implication 의 근거가 되는 본문 문장을 글자 그대로 복사(20~200자). ma_implication 이 빈 문자열이면 빈 문자열."""

SUPPORT_SYSTEM = """너는 한국MSD Market Access 브리핑의 사실 검증자다.
기사 본문(article_body), 본문에서 인용한 문장(quote), 작성자가 쓴 MA 시사점(implication)이 주어진다.
질문: quote(필요하면 article_body 로 보충)가 implication 을 뒷받침하는가?
'둘 다 존재한다'가 아니라, 인용 내용에서 시사점의 결론이 직접 따라 나오는지만 본다.
- 시사점이 인용·본문에 없는 사실·수치·전망을 더하면 supported=false.
- 시사점이 기사·단체의 주장·촉구를 사실처럼 옮긴 것이면 supported=false.
- 시사점의 관점이 한국MSD 가 아니거나(예: 경쟁사 입장에서 '긍정적'), 방향이 본문과 다르면 supported=false.
- 인용이 시사점과 무관한 문장이면 supported=false.
- 시사점이 한국MSD 의 급여·약가·등재 업무와 무관한 일반론이면 supported=false.
반드시 JSON 객체 하나만 출력한다: {"supported": true 또는 false, "reason": "한국어 한 문장"}"""


class LLMError(RuntimeError):
    """LLM 호출/파싱 실패 (재시도 소진)."""


class JsonLLM(Protocol):
    def complete_json(self, *, system: str, user: str, model: str) -> dict: ...


NON_RETRYABLE_ERROR_NAMES = ("AuthenticationError", "PermissionDeniedError", "BadRequestError", "NotFoundError")


def _non_retryable_errors() -> tuple[type[BaseException], ...]:
    """인증·권한·요청형식·모델없음 오류는 재시도해도 결과가 같으므로 즉시 실패시킨다.

    openai 를 지연 임포트하고, 임포트 실패 시 빈 튜플(모든 오류를 일시 오류로 취급)."""
    try:
        import openai
    except Exception:
        return ()
    return tuple(t for t in (getattr(openai, name, None) for name in NON_RETRYABLE_ERROR_NAMES)
                 if isinstance(t, type) and issubclass(t, BaseException))


class OpenAIJsonLLM:
    """OpenAI chat.completions JSON 모드 래퍼.

    시간·비용 상한: 요청 timeout 기본 30s, SDK 자체 재시도는 0 (재시도는 이 래퍼가 max_retries 로 소유).
    일시 오류(타임아웃·5xx·JSON 파싱)만 retry_backoff_seconds 대기 후 재시도하고,
    인증·권한·요청형식 오류는 즉시 LLMError.
    """

    def __init__(self, api_key: str | None = None, *, timeout: float = 30.0,
                 max_retries: int = 1, retry_backoff_seconds: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep, client: Any | None = None):
        if client is None:
            from openai import OpenAI

            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise LLMError("OPENAI_API_KEY is required")
            client = OpenAI(api_key=key, timeout=timeout, max_retries=0)
        self._client = client
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleep

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
                if isinstance(exc, _non_retryable_errors()):
                    logger.warning("[daily_mailing.llm] 재시도 불가 오류: %s", exc)
                    raise LLMError(f"LLM 호출 실패 (재시도 불가): {exc}") from exc
                logger.warning("[daily_mailing.llm] 호출 실패 (%d/%d): %s",
                               attempt + 1, self._max_retries + 1, exc)
                if attempt < self._max_retries and self._retry_backoff_seconds > 0:
                    self._sleep(self._retry_backoff_seconds)   # 일시 오류만 대기 후 재시도
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


def verify_implication(implication: str, quote: str, body: str, *, llm: JsonLLM, model: str) -> bool:
    """인용(과 본문)이 시사점을 '뒷받침' 하는지 2차 LLM(JSON) 에 묻는다. bool 인 supported 만 신뢰한다.
    LLMError 는 호출측이 fail-closed(시사점 삭제) 로 처리한다."""
    user = json.dumps({"implication": implication, "quote": quote,
                       "article_body": (body or "")[:SUPPORT_BODY_CHARS]}, ensure_ascii=False)
    data = llm.complete_json(system=SUPPORT_SYSTEM, user=user, model=model)
    supported = data.get("supported")
    return supported is True


def title_restatement_ratio(implication: str, title: str) -> float:
    """정규화(구두점·태그·조사 제거) 후 SequenceMatcher ratio."""
    a, b = normalize_title(implication), normalize_title(title)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def reject_implication_style(implication: str, title: str) -> str | None:
    """코드 가드 (프롬프트만 믿지 않는다). 거부 사유 또는 None.
    - restates_title: 제목을 되풀이한 시사점 (정규화 유사도 > TITLE_RESTATEMENT_THRESHOLD)
    - advocacy: 기사·단체의 주장을 옮긴 옹호 표현 ('해야 한다'·'필요하다'·'촉구'·'요구된다')"""
    text = (implication or "").strip()
    if not text:
        return None
    if title_restatement_ratio(text, title) > TITLE_RESTATEMENT_THRESHOLD:
        return "restates_title"
    compact = re.sub(r"\s+", "", text)
    if any(marker in compact for marker in ADVOCACY_MARKERS):
        return "advocacy"
    return None
