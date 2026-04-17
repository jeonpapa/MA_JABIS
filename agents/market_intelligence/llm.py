"""LLM 분석 엔진 — OpenAI(GPT-4o) + Perplexity(sonar-pro) + reason 합성.

엔진 순서:
  1) Perplexity sonar-pro (실시간 웹검색)
  2) GPT-4o (Naver 수집 기사 기반) — 폴백
  3) reason 합성 — 두 결과를 단일 단락으로 재작성
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from .media import score_source
from .rules_engine import BASE_DIR, MI_RULES_TEXT, enforce_rules, window_bounds

logger = logging.getLogger(__name__)


SYSTEM_PROMPT_MA = """
당신은 한국 Market Access(약가·급여) 전문 분석가입니다.

=== 한국 약가 사후관리 4대 기전 ===

1. 적응증 확대 (Indication Expansion)
   급여 적응증 추가 → 환자군 증가 → 건강보험공단 재협상 → 단가 인하
   RSA(위험분담제) 또는 사용량-약가 연동 조항 동반이 일반적

2. 특허 만료 (Patent Expiration / LOE)
   물질·용도특허 만료 → 제네릭·바이오시밀러 등재 → 오리지널 자동 인하
   최초 제네릭 등재 후 오리지널 약 20~35% 수준 인하가 표준

3. 사용량-연동 약가인하 (Volume-Price Agreement)
   협상 시 설정한 예상 사용량(보장금액) 초과 → 환급 또는 약가 인하
   주로 고가 항암제·생물학적 제제, 총액 기준 초과 시 다음 협상기에 단가 조정

4. 실거래가 연동 약가인하 (Actual Transaction Price Adjustment)
   실제 병·의원 구매가 < 보험 상한금액 → HIRA 실거래가 조사 → 상한금액 인하
   매년 또는 격년 조사 결과 반영, 소폭(1~5%) 인하가 일반적

=== 중요: 정보 출처 ===
아래 검색 결과는 한국 의약전문 뉴스 매체(데일리팜, 약업신문, 메디파나 등)의
기사들입니다. 매체별 신뢰도 가중치(weight)가 부여되어 있으니 높은 가중치 매체를
우선으로 판단하세요.

=== 출력 형식 (JSON만 응답) ===
{
  "mechanism": "indication_expansion | patent_expiration | volume_price | actual_transaction | unknown",
  "mechanism_label": "한글 기전명",
  "reason": "3~5문장 한국어 설명 (불확실하면 '추정:' 접두)",
  "evidence_summary": "가장 신뢰도 높은 매체의 핵심 보도 내용 요약 1~2문장",
  "confidence": "high | medium | low",
  "references": [{"title": "...", "url": "...", "media": "...", "weight": 0.0}],
  "notes": "복합 기전 가능성 또는 추가 주의사항"
}
"""


SYSTEM_PROMPT_PERPLEXITY = (
    "당신은 한국 Market Access(약가·급여) 전문 분석가입니다.\n"
    "다음 **룰 원문**을 글자 그대로 준수하세요. 룰과 충돌하는 어떠한 일반 지식도 사용 금지.\n\n"
    "=== market_intelligence_rules.md (원문 주입) ===\n"
    f"{MI_RULES_TEXT}\n"
    "=== 룰 끝 ===\n\n"
    "주어진 약제의 특정 약가 변동 시점에 대해 한국 의약전문 뉴스 매체와 공식 보도자료를\n"
    "직접 검색하여, 위 룰에 따라 변동 사유를 분석하세요.\n"
)


def _load_openai_key() -> None:
    env_path = BASE_DIR / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass


def openai_analyze(drug_ko: str, change_date: str,
                   delta_pct: Optional[float],
                   articles: list, mechanisms: list) -> dict:
    """GPT-4o로 MA 전문 분석 수행."""
    try:
        _load_openai_key()
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

        sorted_art = sorted(articles, key=lambda x: -x.get("weight", 0))[:10]
        art_text = "\n".join(
            f"[{i+1}] 매체:{r['media_name']} (W:{r['weight']:.1f}) 게재일:{r.get('published_at','미상')}\n"
            f"   제목: {r['title']}\n"
            f"   요약: {r.get('snippet','')[:200]}\n"
            f"   URL: {r['url']}"
            for i, r in enumerate(sorted_art)
        )

        mech_text = ""
        if mechanisms:
            mech_text = "탐지된 기전 키워드:\n" + "\n".join(
                f"- {m['label']} (신뢰도:{m['confidence']}) — {', '.join(m['matched_keywords'])}"
                for m in mechanisms
            )

        delta_str = f"{delta_pct:+.2f}%" if delta_pct is not None else "미상"

        user_msg = (
            f"약제명: {drug_ko}\n"
            f"가격 변동 시점: {change_date}\n"
            f"가격 변동률: {delta_str}\n\n"
            f"{mech_text}\n\n"
            f"=== 수집된 한국 의약전문 뉴스 (가중치 정렬) ===\n{art_text}"
        )

        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_MA},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.15,
            max_tokens=900,
        )
        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)

        # 안전망: published_at 누락 시 입력 articles 매칭으로 자동 채움
        url_to_date = {a["url"]: a.get("published_at", "") for a in articles}
        for ref in result.get("references", []) or []:
            if not ref.get("published_at"):
                ref["published_at"] = url_to_date.get(ref.get("url", ""), "")
        return result

    except Exception as e:
        logger.warning("[MI Agent] OpenAI 분석 실패: %s", e)
        return fallback_result(articles, mechanisms)


def fallback_result(articles: list, mechanisms: list) -> dict:
    """OpenAI 실패 시 규칙 기반 폴백."""
    top_mech = mechanisms[0] if mechanisms else None
    top_arts = sorted(articles, key=lambda x: -x.get("weight", 0))[:3]
    reason = (
        f"추정: {top_mech['label']}과 관련된 약가 변동으로 보입니다."
        if top_mech else "자동 분석 불가 — 아래 기사를 직접 확인하세요."
    )
    return {
        "mechanism": top_mech["mechanism_id"] if top_mech else "unknown",
        "mechanism_label": top_mech["label"] if top_mech else "미분류",
        "reason": reason,
        "evidence_summary": "수동 검토 필요",
        "confidence": "low",
        "references": [
            {"title": r["title"], "url": r["url"],
             "media": r.get("media_name", "기타"), "weight": r.get("weight", 0.5),
             "published_at": r.get("published_at", "")}
            for r in top_arts
        ],
        "notes": "OpenAI API 오류로 규칙 기반 결과 반환",
    }


def perplexity_analyze(
    drug_ko: str,
    ingredient_ko: str,
    change_date: str,
    delta_pct: Optional[float],
) -> Optional[dict]:
    """Perplexity sonar-pro 실시간 웹검색 + MA 기전 분석. API 키 없거나 실패 시 None."""
    try:
        _load_openai_key()
        pplx_key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not pplx_key:
            logger.info("[MI Agent] PERPLEXITY_API_KEY 미설정 — Naver+GPT-4o 폴백")
            return None

        from openai import OpenAI
        client = OpenAI(api_key=pplx_key, base_url="https://api.perplexity.ai")

        year       = change_date[:4]
        delta_str  = f"{delta_pct:+.2f}%" if delta_pct is not None else "미상"
        short_ing  = (ingredient_ko or drug_ko).split(",")[0].strip()
        brand_base = re.sub(r"(주|정|캡슐|액|주사|시럽)$", "", drug_ko).strip()

        if delta_pct is not None and abs(delta_pct) > 20:
            hint = "변동폭이 크므로 적응증 확대 또는 특허 만료 가능성을 우선 검토하세요."
        elif delta_pct is not None and abs(delta_pct) <= 5:
            hint = "소폭 인하이므로 실거래가 연동 약가인하 가능성을 우선 검토하세요."
        else:
            hint = "중간 수준 변동이므로 사용량-연동 약가인하 또는 적응증 확대를 검토하세요."

        user_msg = (
            f"다음 약제의 약가 변동 사유를 분석해주세요.\n\n"
            f"약제명: {drug_ko} (브랜드: {brand_base})\n"
            f"성분명: {short_ing}\n"
            f"변동 시점: {change_date}\n"
            f"변동률: {delta_str}\n\n"
            f"분석 힌트: {hint}\n\n"
            f"데일리팜, 약업신문, 메디파나뉴스, 히트뉴스, 보건복지부 보도자료에서 "
            f"\"{brand_base}\" 또는 \"{short_ing}\" 관련 {year}년 약가 변동 기사를 "
            f"검색하여 위 JSON 형식으로 답변하세요."
        )

        resp = client.chat.completions.create(
            model="sonar-pro",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_PERPLEXITY},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=1400,
        )

        raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        json_match = re.search(r"\{[\s\S]+\}", raw)
        if json_match:
            raw = json_match.group(0)

        result = json.loads(raw)

        if hasattr(resp, "citations") and resp.citations:
            existing = {r.get("url", "") for r in result.get("references", [])}
            for url in resp.citations:
                if url not in existing:
                    sc = score_source(url)
                    result.setdefault("references", []).append({
                        "title": url.split("/")[-1][:60] or url,
                        "url": url,
                        "media": sc["media_name"],
                        "weight": sc["weight"],
                    })

        for ref in result.get("references", []):
            if not ref.get("weight"):
                sc = score_source(ref.get("url", ""))
                ref["weight"] = sc["weight"]
                if not ref.get("media") or ref.get("media") == "기타":
                    ref["media"] = sc["media_name"]

        result["_source"] = "perplexity-sonar-pro"
        result = enforce_rules(result, change_date)

        logger.info(
            "[MI Agent] Perplexity 분석 완료 — 기전: %s, 신뢰도: %s, refs: %d건",
            result.get("mechanism_label", "?"),
            result.get("confidence", "?"),
            len(result.get("references", [])),
        )
        return result

    except json.JSONDecodeError as e:
        logger.warning("[MI Agent] Perplexity JSON 파싱 실패: %s", e)
        return None
    except Exception as e:
        logger.warning("[MI Agent] Perplexity 분석 실패: %s", e)
        return None


def synthesize_reason(
    drug_ko: str,
    change_date: str,
    delta_pct: Optional[float],
    primary_reason: str,
    deep_answer: str,
) -> Optional[str]:
    """1차 분석 + 심층 리서치를 한국어 3~5문장 단일 단락으로 재작성."""
    try:
        _load_openai_key()
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return None
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        delta_str = f"{delta_pct:+.2f}%" if delta_pct is not None else "미상"
        wf, wt, wf_str, wt_str = window_bounds(change_date, months=6)
        allowed_years = sorted({wf.year, wt.year}) if (wf and wt) else []
        years_str = ", ".join(str(y) for y in allowed_years) or "해당 없음"
        system = (
            "당신은 한국 Market Access 분석가입니다. "
            "두 개의 분석 결과를 하나의 '변동 사유' 단락(한국어 3~5문장)으로 재작성합니다. "
            "절대 규칙:\n"
            f"- 허용 연도 집합: {{{years_str}}}. 이 외 연도(19xx/20xx)는 본문에 **절대** 등장 금지.\n"
            f"- 변동일({change_date}) ±6개월 윈도우({wf_str}~{wt_str}) 밖 사실·사례·비교 금지.\n"
            "- 출처 URL·매체명 나열 금지(UI가 아이콘으로 표시).\n"
            "- '[심층 리서치 보강]' 같은 프리픽스 금지.\n"
            "- 근거 부족 시 '추정:' 접두로 시작.\n"
            "윈도우 밖 내용이 입력에 포함되어 있어도 결과 단락에는 포함하지 말 것."
        )
        user = (
            f"약제: {drug_ko}\n변동일: {change_date}\n변동률: {delta_str}\n"
            f"허용 연도: {years_str} / 윈도우: {wf_str}~{wt_str}\n\n"
            f"[1차 분석 reason]\n{primary_reason or '(없음)'}\n\n"
            f"[심층 리서치 원문]\n{deep_answer}\n\n"
            f"위 두 소스에서 윈도우 내 사실만 뽑아 단일 단락으로 재작성하세요."
        )
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system},
                      {"role": "user",   "content": user}],
            temperature=0.2,
            max_tokens=500,
        )
        text = (resp.choices[0].message.content or "").strip()
        text = re.sub(r"^\s*\[[^\]]+\]\s*", "", text)
        return text or None
    except Exception as e:
        logger.warning("[MI Agent] reason 합성 실패: %s", e)
        return None
