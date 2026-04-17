"""MarketIntelligence 패키지 — 약가 변동 사유 분석.

구성:
    media.py        — MEDIA_DB + score_source
    mechanisms.py   — 4대 기전 분류기
    naver.py        — Naver 뉴스 수집
    rules_engine.py — 윈도우 계산 + MI rules v3 하드 enforcement
    llm.py          — OpenAI(GPT-4o) / Perplexity(sonar-pro) / reason 합성
    agent.py        — MarketIntelligenceAgent 메인 클래스

공용 API:
    from agents.market_intelligence import (
        MarketIntelligenceAgent, MEDIA_DB, MI_RULES_TEXT,
    )

CLI:
    python -m agents.market_intelligence [leaderboard | <drug> <ingredient> <date> <delta>]

규칙: agents/rules/market_intelligence_rules.md
"""
from .agent import MarketIntelligenceAgent, apply_calibrated_weights
from .media import MEDIA_DB, score_source
from .mechanisms import PRICE_MECHANISMS, classify_mechanism
from .rules_engine import MI_RULES_TEXT, enforce_rules, window_bounds

# 하위 호환: 기존 `_apply_calibrated_weights` 이름으로도 노출
_apply_calibrated_weights = apply_calibrated_weights

__all__ = [
    "MarketIntelligenceAgent",
    "MEDIA_DB",
    "MI_RULES_TEXT",
    "PRICE_MECHANISMS",
    "apply_calibrated_weights",
    "_apply_calibrated_weights",
    "classify_mechanism",
    "enforce_rules",
    "score_source",
    "window_bounds",
]
