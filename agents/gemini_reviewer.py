"""
GeminiReviewer — Google Gemini 를 활용한 두 번째 리뷰어

역할: OpenAI GPT-4o 리뷰어와 **독립적으로** 같은 결과를 검토.
패널 합의 (OpenAI AND Gemini) 시에만 approve.

환경변수:
  GEMINI_API_KEY=...

SDK 의존성 없음 — REST API 호출 (urllib) 만 사용.
"""

import json
import logging
import os
import re
import ssl
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

SYSTEM_PROMPT = """당신은 MA AI Dossier 시스템의 독립 검증(Review) 에이전트입니다.
다른 에이전트(MarketIntelligenceAgent)가 생산한 '약가 변동 사유' 결과를,
원래 사용자 요청과 market_intelligence_rules.md 룰에 비추어 판정하세요.

반드시 아래 JSON 형식으로만 응답합니다. 다른 텍스트 금지.

{
  "approved": true | false,
  "score": 0-100,
  "issues": [
    {"axis": "request_alignment | rule_compliance | evidence_quality | consistency",
     "severity": "blocker | major | minor", "detail": "..."}
  ],
  "corrective_actions": ["재시도 시 즉시 적용 가능한 구체 지시"],
  "final_verdict": "한국어 1~2문장 총평",
  "reviewer": "gemini"
}

원칙:
- 의심스러우면 reject. 거짓 답보다 명시적 unknown 이 낫다.
- 윈도우 외 연도가 reason 본문 또는 references.published_at 에 있으면 무조건 blocker.
- references 가 published_at 없이 포함되어 있으면 blocker.
- mechanism 이 unknown 이 아닌데 references 가 비어있으면 major.
"""


def _load_env() -> None:
    env_path = BASE_DIR / "config" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


class GeminiReviewer:
    def __init__(self, model: str = "gemini-2.5-flash"):
        self.model = model
        _load_env()
        self.api_key = os.environ.get("GEMINI_API_KEY", "")
        self.available = bool(self.api_key)

    def review(self, original_request: dict, mi_result: dict, mi_rules: str) -> Optional[dict]:
        """
        Gemini 독립 리뷰. 키 없거나 호출 실패 시 None 반환.
        """
        if not self.available:
            logger.info("[GeminiReviewer] GEMINI_API_KEY 없음 — 패널 합의 단독 OpenAI 의존")
            return None

        user_msg = (
            f"[원 사용자 요청]\n약제: {original_request.get('drug')}\n"
            f"변동일: {original_request.get('date')}\n"
            f"변동률: {original_request.get('delta_pct')}%\n\n"
            f"[MI Agent 결과]\n{json.dumps(mi_result, ensure_ascii=False, indent=2)}\n\n"
            f"[market_intelligence_rules.md 원문]\n{mi_rules}\n\n"
            "위 결과가 원래 요청과 룰에 부합하는지 JSON 으로만 응답하세요."
        )
        body = {
            "systemInstruction": {"role": "system", "parts": [{"text": SYSTEM_PROMPT}]},
            "contents":          [{"role": "user", "parts": [{"text": user_msg}]}],
            "generationConfig":  {
                "temperature": 0.1,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": 0},   # 2.5-flash: thinking 비활성화
            },
        }
        url = GEMINI_ENDPOINT.format(model=self.model, key=self.api_key)
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=45, context=ctx) as resp:
                raw = resp.read().decode("utf-8")
            payload = json.loads(raw)
            text = (
                payload.get("candidates", [{}])[0]
                .get("content", {}).get("parts", [{}])[0]
                .get("text", "")
            ).strip()
            if not text:
                logger.warning("[GeminiReviewer] 빈 응답: %s", payload)
                return None
            if "```" in text:
                text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
            m = re.search(r"\{[\s\S]+\}", text)
            if m:
                text = m.group(0)
            verdict = json.loads(text)
            verdict.setdefault("reviewer", "gemini")
            verdict.setdefault("review_mode", "gemini-llm")
            return verdict
        except Exception as e:
            logger.warning("[GeminiReviewer] 호출 실패: %s", e)
            return None
