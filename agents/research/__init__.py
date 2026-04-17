"""MA AI Dossier — Research 모듈

Tier 3 AI 교차검증 파이프라인.
- HTA 평가사항, 적응증 급여 범위, 약가 근거 조사
- 3개 LLM 독립 조사 + 필드 단위 교차검증

클라이언트:
  - ask_gemini_grounded   : Gemini 2.5-pro + Google Search grounding
  - ask_perplexity        : Perplexity sonar-pro (native citations)
  - ask_openai            : OpenAI GPT-5 (training knowledge baseline)

오케스트레이터:
  - cross_validate        : 3 LLM 병렬 질의 + 필드 비교
"""

from .clients import ask_gemini_grounded, ask_openai, ask_perplexity

__all__ = ["ask_gemini_grounded", "ask_openai", "ask_perplexity"]
