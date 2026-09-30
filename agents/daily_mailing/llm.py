"""Daily Mailing LLM 단계 — 관련성 판정 · 브리핑 작성 · 인용 검증 · 시사점 지원 검증.

설계 §5 ⑤⑦. 모델은 EngineConfig(settings.json daily_mailing) 에서 주입.
- 관련성: 스콥 항목을 '주제로 다루는' 기사만 통과, matched_scope_terms 는 주어진 항목 중에서만.
- 작성: 본문 근거 요약 2~3개 + MA 시사점 1문장 + 본문 원문 인용. 인용은 verify_quote 로 코드 검증.
- 시사점 (2026-09-19 파일럿 이후): 독자는 한국MSD MA 팀. 시사점은 'MSD 의 한국 접근 업무에 무엇을 뜻하는가' 만,
  근거 없으면 빈 문자열이 정답. 코드 가드(reject_implication_style: 제목 재진술·옹호 표현) →
  인용 검증(verify_quote) → 지원 검증(verify_implication, 2차 LLM 1회) 순으로 통과해야 렌더.
- MSD 구체 연결 (2026-09-30 파일럿 이후): 스타틴 복합제 인하 기사에 'MSD 의 시장 접근 전략에 위협' 같은 상투구가 붙었다 —
  인용은 '경쟁 심화' 를 뒷받침하므로 지원 검증만으로는 못 거른다. 시사점은 특정 MSD 자산(키트루다·가다실·린파자·웰리렉·
  브리디온·에멘드·박스뉴반스·윈레브에어 등) 또는 한국MSD 포트폴리오에 실제로 닿는 정책·절차 기전을 이름으로 적어야 하고,
  verify_implication 은 같은 호출에서 supported 와 msd_specific 을 함께 판정한다. 렌더는 둘 다 True 일 때만.
- MA 컨텍스트 팩 (2026-09-30 파일럿 2차): '키트루다·테빔브라 사용범위 확대, RSA 재계약' 기사에 '키트루다에 긍정적' 이 붙었다.
  실제 논점은 같은 PD-1 테빔브라가 같은 급여 적응증을 훨씬 낮은 약가로 가져가면 키트루다 RSA 조건이 압박받는다는 것 —
  본문만으로는 말할 수 없다. 작성자는 이제 본문 사실 + ma_context(포트폴리오·경쟁약 약가·적응증·RSA, ma_context.py) 를 결합하고
  impact(threat|opportunity|neutral|none) 와 affected_asset 을 낸다. 코드 검증: 시사점의 숫자는 본문·팩에 표기 그대로 있어야
  (unverified_numbers), affected_asset 은 팩의 MSD 자산이어야 (엔진 ContextPack.resolve_msd_asset). 실패 시 시사점 삭제.
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
from .ma_context import extract_number_tokens

logger = logging.getLogger(__name__)

MIN_QUOTE_CHARS = 10
RELEVANCE_TEXT_CHARS = 2000
WRITER_BODY_CHARS = 6000
SUPPORT_BODY_CHARS = 4000
TITLE_RESTATEMENT_THRESHOLD = 0.8     # 정규화 제목↔시사점 SequenceMatcher ratio 초과 시 '제목 재진술'
# 기사·단체의 주장을 옮긴 옹호 표현 — 시사점은 사실 기반 결과만 (공백 제거 후 비교)
ADVOCACY_MARKERS = ("해야한다", "필요하다", "촉구", "요구된다")
IMPACTS = ("threat", "opportunity", "neutral")          # 시사점이 있을 때의 impact. 비면 "none"
# 시사점 숫자 검증: 영문자·하이픈에 붙은 숫자(PD-1, MK-3475, PCV15)는 식별자, 한 자리 서수(1차·3상·2기·1L)는 검증 대상 아님
_IMPLICATION_NUMBER = re.compile(r"(?<![A-Za-z\-\d])(\d+(?:[.,]\d+)*)")
ORDINAL_SUFFIXES = ("차", "상", "기", "세대", "등급", "라인", "L", "군", "군")

RELEVANCE_SYSTEM = """너는 한국 제약 Market Access 뉴스 모니터링 편집자다.
구독자의 모니터링 스콥(키워드·브랜드·회사·질환·정책토픽)과 기사 1건이 주어진다.
기사가 스콥 항목 중 하나 이상을 '주제로 다루는지' 판정한다.
- 스콥 항목이 기사의 핵심 사건·주체·정책이면 relevant=true.
- 스콥 항목이 나열·배경·광고·인사 동정으로 스쳐 언급만 되면 relevant=false.
- 스콥과 무관한 다른 약제·질환·정책 기사는 relevant=false.
반드시 JSON 객체 하나만 출력한다:
{"relevant": true 또는 false, "matched_scope_terms": ["주어진 스콥 항목 원문"], "reason": "한국어 한 문장 근거"}
matched_scope_terms 는 입력 scope_terms 의 문자열 중에서만 고른다."""

WRITER_SYSTEM = """너는 한국MSD(MSD Korea) Market Access(MA) 팀의 아침 브리핑 분석가다.
독자는 한국MSD 의 급여·약가·등재 담당자다. 입력은 기사 본문(article_body)과 MA 컨텍스트(ma_context) 다.
ma_context 는 한국MSD 포트폴리오와 직접 경쟁약의 로컬 DB 사실이다 — 약가(상한금액·최소단위·적용일), 허가·급여 적응증 수, RSA 여부, 경쟁 계열.
요약은 본문만 근거로 쓴다. 시사점은 본문 사실과 ma_context 사실을 결합해 쓴다. 본문에도 ma_context 에도 없는 사실·수치·전망을 만들지 않는다.
반드시 JSON 객체 하나만 출력한다:
{"summary_bullets": ["...", "..."], "ma_implication": "...", "evidence_quote": "...", "impact": "opportunity|threat|neutral|none", "affected_asset": "키트루다"}
- summary_bullets: 2~3개. 각 60자 안팎의 사실 요약(누가·무엇을·언제). 본문 사실만. 기사나 단체의 주장·요구는 '~라고 주장했다'처럼 출처를 밝혀 사실로 적는다.
- ma_implication: 한 문장. 인용(evidence_quote)으로 뒷받침되는 '기사 사실'에서 출발해 ma_context 의 사실(경쟁약 약가·급여 적응증·RSA)과 결합하고,
  한국MSD 의 '특정 자산' 에 무엇을 뜻하는지 방향과 함께 쓴다. 규칙:
  (a) 영향받는 MSD 자산을 이름으로 적는다 — ma_context 의 [한국MSD 포트폴리오] 에 있는 자산만(키트루다·가다실·린파자·웰리렉·브리디온·에멘드·
      박스뉴반스·윈레브에어·엔플론시아 등). affected_asset 과 같은 이름.
  (b) 시사점의 모든 숫자·가격·날짜는 본문 또는 ma_context 의 표기 그대로 옮긴다. 계산한 배수·비율·반올림·추정 금지('약 57%', '2배', '210만원' 금지).
      두 약가를 비교할 때는 두 값을 그대로 나란히 적는다(예: 테빔브라 1,206,000원/100mg vs 키트루다 2,100,556원/100mg).
  (c) 방향을 분명히 하고 impact 로 낸다. threat(위협) — 경쟁약의 급여 진입·확대·더 낮은 약가, 자사 약가 인하 기전, RSA·협상 조건 압박.
      opportunity(기회) — 자사 급여·허가 확대, 경쟁약 제한·철수. neutral(참고) — 자사에 닿지만 방향이 정해지지 않은 절차·일정.
  (d) 관점은 항상 한국MSD 다. 경쟁약의 호재(급여·허가·임상 우위)는 '어느 MSD 자산에 대한 위협' 으로 쓴다 — 경쟁사 입장에서 '긍정적' 이라고 쓰지 않고,
      자사 자산과 경쟁약이 함께 나오는 기사를 자사에 '긍정적' 으로 뭉뚱그리지 않는다.
      예: 같은 PD-1 계열 테빔브라가 키트루다와 같은 급여 적응증을 더 낮은 약가(ma_context)로 가져가면 → 키트루다 RSA 재계약 조건·약가 협상에
      인하 압박 → impact threat, affected_asset 키트루다.
  (e) 정책·절차 기전(제네릭 45% 인하 개편, 기등재 의약품 재평가, 암질심·약평위 일정 변경 등)은 그 기전이 실제로 닿는 자산을 포트폴리오에서 골라 이름을 적는다.
  (f) 금지: 자산 이름 없는 상투구('MSD 의 시장 접근 전략에 위협', '경쟁력에 영향', '급여 결정에 영향', '경쟁 심화는 위협') — 'MSD' 라는 이름을 넣는다고
      구체적 연결이 되지 않는다. 기사·단체의 주장·촉구·요구 옮기기(옹호·의견 금지: '~해야 한다', '~가 필요하다'). 본문·ma_context 를 넘어선 추측(예상·가능성·전망).
  (g) 빈 문자열이 기본값이자 선호되는 출력이다. 기사가 포트폴리오 자산·그 직접 경쟁약·같은 적응증·경쟁 구도·자사에 닿는 기전 어디에도 닿지 않으면
      (MSD 에 제품이 없는 시장의 약가·경쟁 뉴스, 일상적인 업계·실적·인사 뉴스, 일반론만 가능한 기사) 빈 문자열 + impact "none" + affected_asset "".
      대부분의 업계 뉴스에는 시사점이 없다. 억지로 채우지 않는다.
- evidence_quote: ma_implication 이 출발하는 '기사 사실' 의 본문 문장을 글자 그대로 복사(20~200자). ma_implication 이 빈 문자열이면 빈 문자열.
- impact: threat | opportunity | neutral 중 하나. ma_implication 이 빈 문자열이면 "none".
- affected_asset: 시사점이 지목한 MSD 자산의 한글 브랜드명(ma_context 포트폴리오 표기 그대로, 하나만). ma_implication 이 빈 문자열이면 ""."""

SUPPORT_SYSTEM = """너는 한국MSD Market Access 브리핑의 사실 검증자다.
입력: 기사 본문(article_body), 본문에서 인용한 문장(quote), MA 컨텍스트(ma_context: 한국MSD 포트폴리오·직접 경쟁약의 약가·허가/급여 적응증 수·RSA 여부 —
로컬 DB 사실), 작성자가 쓴 MA 시사점(implication). 시사점은 '기사 사실' 에서 출발해 ma_context 의 사실과 결합한 추론일 수 있다.
두 가지를 독립적으로 판정한다.
[1] supported — 시사점이 출발하는 기사 사실이 quote(필요하면 article_body 로 보충)에서 직접 따라 나오고, 나머지 사실은 ma_context 에 있는가?
'둘 다 존재한다'가 아니라, 인용의 기사 사실 + ma_context 의 사실에서 시사점의 결론이 따라 나오는지만 본다.
- 시사점이 출발하는 기사 사실이 인용·본문에 없으면 supported=false.
- ma_context 의 약가·적응증·RSA 사실을 결합한 것은 '더한 사실' 이 아니다 — ma_context 에 그대로 있으면 인정한다.
- 인용·본문·ma_context 어디에도 없는 사실·수치(계산한 배수·비율 포함)·전망을 더하면 supported=false.
- 시사점이 기사·단체의 주장·촉구를 사실처럼 옮긴 것이면 supported=false.
- 시사점의 관점이 한국MSD 가 아니거나(예: 경쟁사 입장에서 '긍정적'), 방향이 본문·ma_context 와 다르면 supported=false.
- 인용이 시사점과 무관한 문장이면 supported=false.
- 시사점이 한국MSD 의 급여·약가·등재 업무와 무관한 일반론이면 supported=false.
[2] msd_specific — 시사점이 한국MSD 포트폴리오와의 '구체적 연결' 을 이름으로 적고, 그 연결이 본문(과 ma_context)에서 확인되는가?
인정하는 연결은 둘뿐이다:
- 특정 MSD 자산·파이프라인 지목: ma_context [한국MSD 포트폴리오] 의 자산(키트루다·가다실·린파자·웰리렉·브리디온·에멘드·박스뉴반스·윈레브에어 등).
  본문이 그 자산, 그 자산의 직접 경쟁약(ma_context 경쟁 계열), 또는 같은 적응증을 실제로 다뤄야 한다.
- 한국MSD 포트폴리오에 실제로 닿는 정책·절차 기전: 예) 제네릭 45% 인하 → 자사 오리지널의 LOE 후 가격 경쟁,
  기등재 재평가 → 자사 등재 품목 인하 리스크. 본문의 정책·절차가 MSD 품목에도 적용된다는 것이 본문에서 따라 나와야 한다.
- 'MSD' 라는 이름만 있고 자산도 기전도 없는 문장('시장 접근 전략에 위협', '경쟁력에 영향', '경쟁 심화는 위협')은 msd_specific=false.
- MSD 에 제품이 없는 시장(예: 스타틴 복합제·고혈압 제네릭 약가 인하)의 경쟁·가격 뉴스는 msd_specific=false.
- 확신이 없으면 msd_specific=false. 대부분의 일상적인 업계 뉴스는 false 다.
반드시 JSON 객체 하나만 출력한다: {"supported": true 또는 false, "msd_specific": true 또는 false, "reason": "한국어 한 문장"}"""


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
    impact: str = "none"            # threat | opportunity | neutral | none (시사점이 비면 항상 none)
    affected_asset: str = ""        # 작성자가 지목한 MSD 자산 (엔진이 팩의 brand_kr 로 정규화·검증)


@dataclass(frozen=True)
class ImplicationSupport:
    """verify_implication 결과 — 같은 1회 호출에서 나온 두 판정. 렌더는 supported 와 msd_specific 이 모두 True 일 때만."""
    supported: bool        # 인용(과 본문)에서 시사점의 결론이 직접 따라 나오는가
    msd_specific: bool     # 시사점이 MSD 자산 또는 한국MSD 포트폴리오에 닿는 기전을 이름으로 적고 본문이 그것을 확인하는가
    reason: str = ""


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
                matched_scope_terms: Iterable[str], llm: JsonLLM, model: str,
                context_block: str = "") -> BriefDraft:
    """context_block = ma_context.render_context_block(...) — 포트폴리오·경쟁약 사실 블록 (빈 문자열이면 본문만)."""
    user = json.dumps({"scope_terms": list(scope_terms),
                       "matched_scope_terms": list(matched_scope_terms),
                       "title": title,
                       "article_body": (body or "")[:WRITER_BODY_CHARS],
                       "ma_context": context_block or ""}, ensure_ascii=False)
    data = llm.complete_json(system=WRITER_SYSTEM, user=user, model=model)
    bullets = tuple(str(b).strip() for b in (data.get("summary_bullets") or []) if str(b).strip())[:3]
    implication = str(data.get("ma_implication") or "").strip()
    impact = str(data.get("impact") or "").strip().lower()
    if not implication:
        impact, asset = "none", ""                       # 시사점이 없으면 영향도·자산도 없다
    else:
        impact = impact if impact in IMPACTS else "neutral"   # 누락·이상값 → '참고'
        asset = str(data.get("affected_asset") or "").strip()
    return BriefDraft(
        summary_bullets=bullets,
        ma_implication=implication,
        evidence_quote=str(data.get("evidence_quote") or "").strip(),
        impact=impact,
        affected_asset=asset,
    )


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def verify_quote(quote: str, body: str) -> bool:
    """인용문이 본문에 (공백 정규화 후) 그대로 존재하는지. 따옴표는 양끝에서 제거."""
    q = _norm((quote or "").strip().strip("\"'“”‘’"))
    return len(q) >= MIN_QUOTE_CHARS and q in _norm(body)


def unverified_numbers(implication: str, *sources: str | set[str]) -> list[str]:
    """시사점의 숫자 토큰 중 본문·제목·컨텍스트 팩(sources: 텍스트 또는 미리 뽑은 토큰 집합) 어디에도 없는 것.
    쉼표·앞자리 0 은 무시하고 비교한다. 식별자 안 숫자(PD-1, MK-3475)와 한 자리 서수(1차·3상)는 검증하지 않는다.
    계산한 배수·비율·반올림('2배'·'약 57%'·'2,100,000원')은 어디에도 없으므로 걸린다 — 프롬프트 (b) 를 코드로 강제."""
    allowed: set[str] = set()
    for src in sources:
        allowed |= set(src) if isinstance(src, (set, frozenset)) else extract_number_tokens(src)
    bad: list[str] = []
    for m in _IMPLICATION_NUMBER.finditer(implication or ""):
        token = m.group(1)
        plain = token.replace(",", "")
        tail = (implication or "")[m.end():m.end() + 2]
        if len(plain) == 1 and tail and any(tail.startswith(sfx) for sfx in ORDINAL_SUFFIXES):
            continue
        if plain in allowed or (plain.lstrip("0") or "0") in allowed:
            continue
        bad.append(token)
    return bad


def verify_implication(implication: str, quote: str, body: str, *, llm: JsonLLM, model: str,
                       context_block: str = "") -> ImplicationSupport:
    """2차 LLM(JSON) 1회 호출로 두 가지를 묻는다 — 인용(과 본문, 그리고 ma_context 팩)이 시사점을 '뒷받침' 하는지(supported) 와
    시사점이 MSD 자산·기전으로 한국MSD 포트폴리오에 구체적으로 닿는지(msd_specific).
    bool 값만 신뢰한다 (문자열·누락 → False, fail-closed). LLMError 는 호출측이 fail-closed(시사점 삭제) 로 처리한다."""
    user = json.dumps({"implication": implication, "quote": quote,
                       "article_body": (body or "")[:SUPPORT_BODY_CHARS],
                       "ma_context": context_block or ""}, ensure_ascii=False)
    data = llm.complete_json(system=SUPPORT_SYSTEM, user=user, model=model)
    return ImplicationSupport(
        supported=data.get("supported") is True,
        msd_specific=data.get("msd_specific") is True,
        reason=str(data.get("reason") or "")[:300],
    )


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
