"""한국 의약전문 뉴스 매체 DB + 신뢰도 스코어링.

평가 기준: volume(포스팅 빈도) / novelty(신규성) / ma_depth(MA 인사이트) — 각 1~5점.
weight = 가중 합산 (수동 보정 후 MediaCalibrator 가 동적 갱신).
"""
from __future__ import annotations


MEDIA_DB: dict = {
    # ── Tier A: 약가·급여 특화, MA 인사이트 최상위 ──────────────────────────
    "데일리팜": {
        "domain": "dailypharm.com",
        "weight": 3.0,
        "tier": "A",
        "volume": 5, "novelty": 4, "ma_depth": 5,
        "desc": "약가·급여·유통 일간 전문지. 사용량 연동·실거래가 인하 보도 최다.",
    },
    "약업신문": {
        "domain": "yakup.com",
        "weight": 2.9,
        "tier": "A",
        "volume": 5, "novelty": 4, "ma_depth": 5,
        "desc": "국내 최장수 제약·약가 전문 신문. 급여 고시·약가 협상 심층 보도.",
    },
    "메디파나뉴스": {
        "domain": "medipana.com",
        "weight": 2.8,
        "tier": "A",
        "volume": 5, "novelty": 4, "ma_depth": 5,
        "desc": "의약·보건 정책 전문. 적응증 확대 후 약가 재협상 보도 강점.",
    },
    "히트뉴스": {
        "domain": "hitnews.co.kr",
        "weight": 2.7,
        "tier": "A",
        "volume": 4, "novelty": 5, "ma_depth": 4,
        "desc": "의약 전문 인터넷 매체. 보험등재·약가 단독 속보 비중 높음.",
    },

    # ── Tier B: 의사·임상 대상, 급여 정책 보도 ──────────────────────────────
    "청년의사": {
        "domain": "docdocdoc.co.kr",
        "weight": 2.4,
        "tier": "B",
        "volume": 4, "novelty": 4, "ma_depth": 3,
        "desc": "의사 대상 전문지. 임상 적응증 확대·급여 결정 보도 활발.",
    },
    "메디칼타임즈": {
        "domain": "medicaltimes.com",
        "weight": 2.3,
        "tier": "B",
        "volume": 4, "novelty": 3, "ma_depth": 3,
        "desc": "의사 대상 온라인 의료지. 보험급여 정책 기사 다수.",
    },
    "뉴스더보이스": {
        "domain": "newsthevoice.com",
        "weight": 2.3,
        "tier": "B",
        "volume": 4, "novelty": 4, "ma_depth": 4,
        "desc": "의약 전문 온라인. 위험분담·급여 확대 보도 심층.",
    },
    "팜뉴스": {
        "domain": "pharmnews.com",
        "weight": 2.2,
        "tier": "B",
        "volume": 4, "novelty": 3, "ma_depth": 4,
        "desc": "제약 마케팅·급여 정책 전문. 사용량 연동 인하 보도.",
    },
    "메디게이트뉴스": {
        "domain": "medigatenews.com",
        "weight": 2.1,
        "tier": "B",
        "volume": 4, "novelty": 3, "ma_depth": 3,
        "desc": "의사·병원 대상 전문 매체. 급여 적용 임상 관련 보도.",
    },

    # ── Tier C: 보건 정책·규제 공식 소스 ────────────────────────────────────
    "보건복지부": {
        "domain": "mohw.go.kr",
        "weight": 3.0,
        "tier": "C",
        "volume": 2, "novelty": 3, "ma_depth": 5,
        "desc": "보건복지부 공식 보도자료. 급여 고시·약가 인하 공식 발표.",
    },
    "건강보험심사평가원": {
        "domain": "hira.or.kr",
        "weight": 2.9,
        "tier": "C",
        "volume": 2, "novelty": 3, "ma_depth": 5,
        "desc": "HIRA 공식 자료. 사용량-연동 결과·급여 기준 변경 고시.",
    },
    "국민건강보험공단": {
        "domain": "nhis.or.kr",
        "weight": 2.8,
        "tier": "C",
        "volume": 2, "novelty": 3, "ma_depth": 4,
        "desc": "NHIS 공식 보도자료. 약가 협상 결과 발표.",
    },

    # ── Tier D: 일반 건강·제약 뉴스 (참고용) ────────────────────────────────
    "헬스조선": {
        "domain": "health.chosun.com",
        "weight": 1.5,
        "tier": "D",
        "volume": 5, "novelty": 2, "ma_depth": 2,
        "desc": "대중 건강 매체. 약가 기사 일부 포함.",
    },
    "헬스경향": {
        "domain": "k-health.com",
        "weight": 1.3,
        "tier": "D",
        "volume": 3, "novelty": 2, "ma_depth": 2,
        "desc": "건강 전문 매체.",
    },
    "메디컬월드뉴스": {
        "domain": "medicalworldnews.co.kr",
        "weight": 1.8,
        "tier": "D",
        "volume": 3, "novelty": 3, "ma_depth": 3,
        "desc": "의약 전문 뉴스.",
    },
}

_DOMAIN_TO_MEDIA: dict = {info["domain"]: name for name, info in MEDIA_DB.items()}

_TIER_ABC_SITES = " OR ".join(
    f"site:{info['domain']}"
    for info in MEDIA_DB.values()
    if info["tier"] in ("A", "B", "C")
)
_TIER_A_SITES = " OR ".join(
    f"site:{info['domain']}"
    for info in MEDIA_DB.values()
    if info["tier"] == "A"
)
_OFFICIAL_SITES = " OR ".join(
    f"site:{info['domain']}"
    for info in MEDIA_DB.values()
    if info["tier"] == "C"
)


def score_source(url: str) -> dict:
    """URL로 매체 신뢰도 가중치 반환."""
    url_lower = (url or "").lower()
    for domain, media_name in _DOMAIN_TO_MEDIA.items():
        if domain in url_lower:
            m = MEDIA_DB[media_name]
            return {
                "media_name": media_name,
                "weight": m["weight"],
                "tier": m["tier"],
                "desc": m["desc"],
            }
    return {"media_name": "기타", "weight": 0.5, "tier": "other", "desc": ""}
