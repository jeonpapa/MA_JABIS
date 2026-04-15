"""
MarketIntelligenceAgent — 약가 변동 사유 분석 에이전트

역할:
  약가 변동 시점 전후 3개월의 한국 의약전문 뉴스 매체를 검색하고,
  한국 약가 사후관리 기전(적응증 확대 / 특허 만료 / 사용량-연동 /
  실거래가 연동)에 근거하여 가격 변동 사유를 분석한다.

주요 소스 (한국 의약전문 뉴스 매체):
  약가 변동 사유는 학술지가 아니라, 한국 의약전문 기자들이 취재해서
  인터넷 뉴스로 보도하는 것이 주요 정보원이다.
  - 데일리팜, 약업신문, 메디파나뉴스, 히트뉴스 등 MA 전문 매체 우선
  - 보건복지부·HIRA 공식 보도자료 병행 검색

매체 신뢰도 가중치 체계 (3축):
  1) 포스팅 빈도 (Volume)   — 약가·급여 관련 기사 게재량
  2) 신규성 (Novelty)       — 타 매체 대비 속보성, 단독 취재 비중
  3) MA 인사이트 (MA Depth) — Market Access 관점 심층 보도 수준
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# 1) 한국 의약전문 뉴스 매체 데이터베이스
#    평가 기준: volume(포스팅 빈도) / novelty(신규성) / ma_depth(MA 인사이트)
#    각 항목 1~5점, weight = 가중 합산
# ─────────────────────────────────────────────────────────────────────────────

MEDIA_DB = {
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
        "weight": 3.0,   # 공식 발표이므로 최상위 가중치
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

# 도메인 → 매체명 역조회
_DOMAIN_TO_MEDIA: dict = {info["domain"]: name for name, info in MEDIA_DB.items()}

# Tier A+B+C site: 검색 필터 문자열
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


# ─────────────────────────────────────────────────────────────────────────────
# 2) 한국 약가 사후관리 기전 분류기
# ─────────────────────────────────────────────────────────────────────────────

PRICE_MECHANISMS = {
    "indication_expansion": {
        "label": "적응증 확대",
        "description": (
            "급여 적응증이 추가·확대되면서 대상 환자군이 증가하고, "
            "국민건강보험공단과의 약가 재협상을 통해 가격이 인하됨. "
            "위험분담제(RSA) 또는 사용량-약가 연동 조항이 동반되는 경우가 많음."
        ),
        "keywords": [
            "적응증 확대", "급여 확대", "추가 적응증", "새 적응증",
            "보험 급여", "급여 기준 변경", "위험분담", "RSA",
            "적응증 추가", "indication", "재협상", "약가 협상",
        ],
    },
    "patent_expiration": {
        "label": "특허 만료",
        "description": (
            "오리지널 의약품의 물질특허·용도특허 만료 후 제네릭 또는 바이오시밀러가 등재되면, "
            "오리지널 약가가 자동으로 인하됨. "
            "이후 추가 제네릭 진입 시 오리지널도 추가 인하 가능."
        ),
        "keywords": [
            "특허 만료", "특허 종료", "제네릭 등재", "바이오시밀러",
            "오리지널 가격 인하", "후발 의약품", "특허 만료 후",
            "복제약", "동등생물의약품", "첫 번째 제네릭",
        ],
    },
    "volume_price": {
        "label": "사용량-연동 약가인하",
        "description": (
            "건강보험 약가 협상 시 설정된 '예상 사용량(보장금액)'을 초과했을 경우, "
            "초과분에 대해 제약사가 환급하거나 상한금액이 인하됨. "
            "주로 고가 항암제·생물학적 제제에 적용."
        ),
        "keywords": [
            "사용량 연동", "약가 연동", "사용량 초과", "환급",
            "약가 조정", "예상 사용량", "보장금액", "사용량-약가",
            "총액 초과", "청구액 초과", "사후 약가 조정",
        ],
    },
    "actual_transaction": {
        "label": "실거래가 연동 약가인하",
        "description": (
            "병·의원이 실제로 구매하는 가격(실거래가)이 보험 상한금액보다 낮을 경우 "
            "실거래가 조사를 통해 상한금액을 현실에 맞게 인하함. "
            "매년 실거래가 조사 결과를 반영하여 약가 고시 갱신."
        ),
        "keywords": [
            "실거래가", "거래가 조사", "실거래가 인하", "약가 현실화",
            "공급가 조정", "시장 거래가", "약가 고시 갱신", "실판매가",
        ],
    },
}


def classify_mechanism(text: str) -> list:
    """텍스트에서 약가 인하 기전을 탐지, 신뢰도 포함 반환."""
    text_lower = text.lower()
    results = []
    for mech_id, mech in PRICE_MECHANISMS.items():
        matched = [kw for kw in mech["keywords"] if kw.lower() in text_lower]
        if matched:
            confidence = min(1.0, len(matched) / 3)
            results.append({
                "mechanism_id": mech_id,
                "label": mech["label"],
                "description": mech["description"],
                "confidence": round(confidence, 2),
                "matched_keywords": matched[:5],
            })
    results.sort(key=lambda x: -x["confidence"])
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 3) 매체 신뢰도 스코어링
# ─────────────────────────────────────────────────────────────────────────────

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



# ─────────────────────────────────────────────────────────────────────────────
# 4) Naver 뉴스 검색 (DuckDuckGo 대체 — 한국 의약 기사에 최적화)
# ─────────────────────────────────────────────────────────────────────────────

_NAVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://search.naver.com/",
}

# Naver에서 표시되는 매체명 → MEDIA_DB 키 매핑
_PRESS_NAME_MAP = {
    "데일리팜":           "데일리팜",
    "약업신문":           "약업신문",
    "메디파나뉴스":       "메디파나뉴스",
    "히트뉴스":           "히트뉴스",
    "청년의사":           "청년의사",
    "메디칼타임즈":       "메디칼타임즈",
    "뉴스더보이스":       "뉴스더보이스",
    "팜뉴스":             "팜뉴스",
    "메디게이트뉴스":     "메디게이트뉴스",
    "보건복지부":         "보건복지부",
    "건강보험심사평가원": "건강보험심사평가원",
    "국민건강보험공단":   "국민건강보험공단",
    "헬스조선":           "헬스조선",
    "헬스경향":           "헬스경향",
    "메디컬월드뉴스":     "메디컬월드뉴스",
}


def _naver_search(query: str, max_results: int = 8) -> list:
    """
    Naver 뉴스 HTML 검색 → [{title, url, snippet, media_name, weight, tier}].
    - sort=1: 최신순
    - URL 도메인으로 매체 판별, 실패 시 Naver 표시 매체명으로 폴백
    """
    from urllib.parse import urlencode
    params = {"where": "news", "query": query, "sort": "1"}
    search_url = "https://search.naver.com/search.naver?" + urlencode(params)

    try:
        resp = requests.get(search_url, headers=_NAVER_HEADERS, timeout=12)
        if resp.status_code != 200:
            logger.warning("Naver 검색 HTTP %d (쿼리: %s)", resp.status_code, query[:40])
            return []

        html = resp.text
        results: list = []
        seen: set = set()

        # ── 제목 + URL 추출 ───────────────────────────────────────────────────
        tit_re = re.compile(
            r'<a[^>]+class="[^"]*news_tit[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        # ── 매체명 추출 (Naver 표시 이름) ────────────────────────────────────
        press_re = re.compile(
            r'<a[^>]+class="[^"]*info press[^"]*"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        # ── 스니펫 추출 ───────────────────────────────────────────────────────
        snip_re = re.compile(
            r'<div[^>]+class="[^"]*dsc_txt[^"]*"[^>]*>(.*?)</div>',
            re.DOTALL,
        )

        titles_urls = [
            (m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip())
            for m in tit_re.finditer(html)
        ]
        press_names = [
            re.sub(r"<[^>]+>", "", m.group(1)).strip()
            for m in press_re.finditer(html)
        ]
        snippets = [
            re.sub(r"<[^>]+>", "", m.group(1)).strip()[:250]
            for m in snip_re.finditer(html)
        ]

        for i, (url, title) in enumerate(titles_urls):
            if not title or url in seen:
                continue
            seen.add(url)

            # 1) URL 도메인으로 매체 판별
            score = score_source(url)

            # 2) 도메인 불일치(기타)이면 Naver 표시 매체명으로 폴백
            if score["tier"] == "other" and i < len(press_names):
                media_key = _PRESS_NAME_MAP.get(press_names[i])
                if media_key and media_key in MEDIA_DB:
                    m_info = MEDIA_DB[media_key]
                    score = {
                        "media_name": media_key,
                        "weight": m_info["weight"],
                        "tier": m_info["tier"],
                        "desc": m_info["desc"],
                    }

            results.append({
                "title":      title,
                "url":        url,
                "snippet":    snippets[i] if i < len(snippets) else "",
                "media_name": score["media_name"],
                "weight":     score["weight"],
                "tier":       score["tier"],
            })
            if len(results) >= max_results:
                break

        logger.info("Naver 뉴스: %d건 수집 (쿼리: %s)", len(results), query[:50])
        return results

    except Exception as e:
        logger.warning("Naver 검색 실패 (%s): %s", query[:40], e)
        return []

# ─────────────────────────────────────────────────────────────────────────────
# 5) 검색 파이프라인
#    변동 시점 전후 3개월을 커버하는 여러 쿼리로 한국 의약전문 매체를 탐색
# ─────────────────────────────────────────────────────────────────────────────

def _collect_news(drug_ko: str, ingredient_ko: str, change_date: datetime) -> list:
    """
    Naver 뉴스로 한국 의약전문 기사 수집.
    약제명/성분명 + 약가 키워드 조합으로 다각도 검색.
    site: 필터 없이 단순 쿼리 → Naver 자체 랭킹 활용 후 도메인으로 매체 판별.
    """
    short_ing  = (ingredient_ko or drug_ko).split(",")[0].strip()
    brand_base = re.sub(r"(주|정|캡슐|액|주사|시럽)$", "", drug_ko).strip()
    year       = change_date.year

    queries = [
        f"{brand_base} 약가 인하 {year}",
        f"{brand_base} 보험급여 {year}",
        f"{short_ing} 약가 {year}",
        f"{brand_base} 적응증 확대 {year}",
        f"{brand_base} 특허 만료 {year}",
        f"{brand_base} 사용량 연동 {year}",
        f"{short_ing} 급여 확대 {year}",
        f"{brand_base} 약가 협상 {year}",
    ]

    articles: list = []
    seen_urls: set = set()

    for q in queries:
        for r in _naver_search(q, max_results=6):
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                articles.append(r)
        time.sleep(0.5)   # Naver rate limit

    logger.info("[MI Agent] 수집 기사: %d건 (고유 URL)", len(articles))
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# 6) OpenAI 분석 (MA 전문 프롬프트)
# ─────────────────────────────────────────────────────────────────────────────

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


def _load_openai_key() -> None:
    env_path = BASE_DIR / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass


def _openai_analyze(drug_ko: str, change_date: str,
                    delta_pct: Optional[float],
                    articles: list, mechanisms: list) -> dict:
    """GPT-4o로 MA 전문 분석 수행."""
    try:
        _load_openai_key()
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

        # 가중치 정렬 상위 10개
        sorted_art = sorted(articles, key=lambda x: -x.get("weight", 0))[:10]
        art_text = "\n".join(
            f"[{i+1}] 매체:{r['media_name']} (W:{r['weight']:.1f})\n"
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
        return json.loads(raw)

    except Exception as e:
        logger.warning("[MI Agent] OpenAI 분석 실패: %s", e)
        return _fallback_result(articles, mechanisms)


def _fallback_result(articles: list, mechanisms: list) -> dict:
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
             "media": r.get("media_name", "기타"), "weight": r.get("weight", 0.5)}
            for r in top_arts
        ],
        "notes": "OpenAI API 오류로 규칙 기반 결과 반환",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6-b) Perplexity sonar-pro — 실시간 웹검색 + MA 분석 (1차 엔진)
# ─────────────────────────────────────────────────────────────────────────────

# ── 룰 로더: agents/rules/market_intelligence_rules.md 를 프롬프트에 원문 주입 ──
_RULES_PATH = BASE_DIR / "agents" / "rules" / "market_intelligence_rules.md"

def _load_mi_rules() -> str:
    try:
        return _RULES_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[MI Agent] 룰 파일 로드 실패 (%s) — 임베디드 폴백 사용", e)
        return ""

MI_RULES_TEXT = _load_mi_rules()


def _window_bounds(change_date: str, months: int = 6):
    """change_date 기준 ±months 윈도우의 (from_dt, to_dt, from_str, to_str) 반환."""
    try:
        dt = datetime.strptime(change_date, "%Y.%m.%d")
    except Exception:
        try:
            dt = datetime.strptime(change_date[:7], "%Y.%m")
        except Exception:
            return None, None, "", ""
    # relativedelta 없이 단순 월 연산
    y, m = dt.year, dt.month
    fm = m - months
    fy = y
    while fm <= 0:
        fm += 12
        fy -= 1
    tm = m + months
    ty = y
    while tm > 12:
        tm -= 12
        ty += 1
    from datetime import datetime as _dt
    wf = _dt(fy, fm, 1)
    # to 말일
    import calendar as _cal
    wt = _dt(ty, tm, _cal.monthrange(ty, tm)[1])
    return wf, wt, f"{fy}.{fm:02d}", f"{ty}.{tm:02d}"


def _enforce_rules(result: dict, change_date: str) -> dict:
    """
    market_intelligence_rules.md v3 의 하드 규칙을 후처리로 강제:
    (a) published_at 없거나 YYYY.MM.DD 아님 → 참조 제거
    (b) published_at 이 윈도우 밖 → 참조 제거
    (c) reason 본문에서 허용 연도 집합 밖 연도가 포함된 **문장** 전체 삭제
    (d) 윈도우 내 refs=0 → mechanism=unknown / confidence=low 강제, references=[]
    (e) window 필드를 결과에 기록, enforcement 로그는 notes 에 누적
    """
    mech = (result.get("mechanism") or "").lower()
    months = 12 if mech == "patent_expiration" else 6
    wf, wt, wf_str, wt_str = _window_bounds(change_date, months=months)
    result["window"] = {"from": wf_str, "to": wt_str, "months": months}

    enforcement_log = []

    # (a)+(b) references 필터 — published_at 필수 + 윈도우 체크
    kept, dropped_missing_date, dropped_out_of_window = [], 0, 0
    for r in result.get("references", []) or []:
        pub = (r.get("published_at") or "").strip()
        if not pub:
            dropped_missing_date += 1
            continue
        try:
            pd = datetime.strptime(pub[:10].replace("-", "."), "%Y.%m.%d")
        except Exception:
            dropped_missing_date += 1
            continue
        if wf and wt and (pd < wf or pd > wt):
            dropped_out_of_window += 1
            continue
        kept.append(r)
    result["references"] = kept
    if dropped_missing_date:
        enforcement_log.append(f"published_at 누락/불량 {dropped_missing_date}건 제거")
    if dropped_out_of_window:
        enforcement_log.append(f"윈도우 외 references {dropped_out_of_window}건 제거")

    # (c) reason 본문의 연도 게이트 — 허용 집합 밖 연도를 가진 문장을 통째 삭제
    if wf and wt:
        allowed_years = {wf.year, wt.year}
        reason = (result.get("reason") or "").strip()
        if reason:
            # 문장 분리: '.', '!', '?', '。' 또는 줄바꿈 기준
            sentences = re.split(r"(?<=[.!?。])\s+|\n+", reason)
            cleaned, stripped = [], 0
            for sent in sentences:
                years = set(int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", sent))
                if years and not years.issubset(allowed_years):
                    stripped += 1
                    continue
                cleaned.append(sent.strip())
            cleaned_reason = " ".join(s for s in cleaned if s).strip()
            if stripped:
                enforcement_log.append(f"reason 문장 {stripped}개 삭제(윈도우 외 연도)")
                result["reason"] = cleaned_reason or f"추정: 윈도우({wf_str}~{wt_str}) 내 확인 가능한 공개 보도 없음."

    # (d) 남은 refs=0 → 기전 하향 + 명시적 unknown 메시지
    if not result.get("references"):
        if (result.get("mechanism") or "").lower() not in ("unknown", ""):
            result["mechanism"] = "unknown"
            result["mechanism_label"] = "미분류"
        result["confidence"] = "low"
        current = (result.get("reason") or "").strip()
        fallback = f"추정: 윈도우({wf_str}~{wt_str}) 내 확인 가능한 공개 보도 없음."
        if not current or len(current) < 10:
            result["reason"] = fallback
        elif not current.lstrip().startswith("추정"):
            result["reason"] = "추정: " + current

    if enforcement_log:
        existing = (result.get("notes") or "").strip()
        joined = " · ".join(enforcement_log)
        result["notes"] = f"{existing} · [enforce] {joined}".strip(" ·") if existing else f"[enforce] {joined}"

    return result

SYSTEM_PROMPT_PERPLEXITY = (
    "당신은 한국 Market Access(약가·급여) 전문 분석가입니다.\n"
    "다음 **룰 원문**을 글자 그대로 준수하세요. 룰과 충돌하는 어떠한 일반 지식도 사용 금지.\n\n"
    "=== market_intelligence_rules.md (원문 주입) ===\n"
    f"{MI_RULES_TEXT}\n"
    "=== 룰 끝 ===\n\n"
    "주어진 약제의 특정 약가 변동 시점에 대해 한국 의약전문 뉴스 매체와 공식 보도자료를\n"
    "직접 검색하여, 위 룰에 따라 변동 사유를 분석하세요.\n"
)

# 폴백 상세 프롬프트 (룰 파일 로드 실패 시에만 사용)
_LEGACY_SYSTEM_PROMPT_PERPLEXITY = """
당신은 한국 Market Access(약가·급여) 전문 분석가입니다.
주어진 약제의 특정 약가 변동 시점에 대해 한국 의약전문 뉴스 매체와 공식 보도자료를
직접 검색하여, 변동 사유를 아래 4대 기전 중 하나로 분류하고 구체적으로 설명하세요.

=== 한국 약가 사후관리 4대 기전 ===

1. 적응증 확대 (indication_expansion)
   급여 적응증 추가 → 환자군 증가 → 건강보험공단 약가 재협상 → 단가 인하
   RSA(위험분담제)·사용량-약가 연동 조항이 함께 적용되는 경우 많음
   인하폭: 협상에 따라 5~30% 다양

2. 특허 만료 (patent_expiration)
   물질·용도특허 만료 → 제네릭 또는 바이오시밀러 첫 등재 → 오리지널 자동 인하
   첫 제네릭 등재 후 오리지널은 오리지널가 59.5% 수준으로 자동 인하
   바이오시밀러 등재 시 오리지널 약 70% 수준

3. 사용량-연동 약가인하 (volume_price)
   약가 협상 시 설정한 예상 사용량(보장금액) 초과 → 다음 협상기에 단가 인하 또는 환급
   주로 고가 항암제·생물학적 제제, 총액 기준 초과 시 연동 인하

4. 실거래가 연동 약가인하 (actual_transaction)
   실제 병·의원 구매가 < 보험 상한금액 → HIRA 실거래가 조사 → 상한금액 하향 조정
   매년 또는 격년 조사, 소폭(1~5%) 인하가 일반적

=== 검색 우선 매체 (반드시 이 매체 중심으로 검색) ===
데일리팜(dailypharm.com), 약업신문(yakup.com), 메디파나뉴스(medipana.com),
히트뉴스(hitnews.co.kr), 보건복지부(mohw.go.kr), 건강보험심사평가원(hira.or.kr),
청년의사(docdocdoc.co.kr), 메디칼타임즈(medicaltimes.com), 뉴스더보이스(newsthevoice.com)

=== 분석 지침 ===
- 변동률 > 20%: 적응증 확대 또는 특허 만료 가능성 높음
- 변동률 1~5%: 실거래가 연동 가능성 높음
- 복합 기전도 가능 (notes에 명시)
- 근거 기사를 찾지 못한 경우 reason에 "추정:" 접두사 사용

=== 출력 형식 (JSON만 응답, 다른 텍스트 없이) ===
{
  "mechanism": "indication_expansion | patent_expiration | volume_price | actual_transaction | unknown",
  "mechanism_label": "적응증 확대 | 특허 만료 | 사용량-연동 약가인하 | 실거래가 연동 약가인하 | 미분류",
  "reason": "3~5문장 한국어 설명. 구체적 날짜·수치·인용 출처 포함. 불확실하면 추정: 접두",
  "evidence_summary": "가장 신뢰도 높은 출처의 핵심 보도 내용 1~2문장 (매체명 포함)",
  "confidence": "high | medium | low",
  "references": [
    {"title": "기사 제목", "url": "기사 URL", "media": "매체명", "weight": 2.5}
  ],
  "notes": "복합 기전 가능성, 추가 확인 필요 사항, 또는 빈 문자열"
}
"""


def _perplexity_analyze(
    drug_ko: str,
    ingredient_ko: str,
    change_date: str,
    delta_pct: Optional[float],
) -> Optional[dict]:
    """
    Perplexity sonar-pro로 실시간 웹검색 + MA 기전 분석.
    PERPLEXITY_API_KEY 없거나 실패 시 None 반환 → Naver+GPT-4o 폴백.
    """
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

        # 변동률 힌트
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

        # JSON 블록 추출
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
        json_match = re.search(r"\{[\s\S]+\}", raw)
        if json_match:
            raw = json_match.group(0)

        result = json.loads(raw)

        # Perplexity citations 보강 (sonar-pro가 별도 citations 반환 시)
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

        # references weight 보완
        for ref in result.get("references", []):
            if not ref.get("weight"):
                sc = score_source(ref.get("url", ""))
                ref["weight"] = sc["weight"]
                if not ref.get("media") or ref.get("media") == "기타":
                    ref["media"] = sc["media_name"]

        result["_source"] = "perplexity-sonar-pro"

        # ── 룰 강제: 윈도우 밖 references / reason 연도 후처리 필터 ──
        result = _enforce_rules(result, change_date)

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


# ─────────────────────────────────────────────────────────────────────────────
# 6-c) Reason 합성 — Perplexity + 초기 분석을 단일 톤으로 재작성
# ─────────────────────────────────────────────────────────────────────────────

def _synthesize_reason(
    drug_ko: str,
    change_date: str,
    delta_pct: Optional[float],
    primary_reason: str,
    deep_answer: str,
) -> Optional[str]:
    """
    1차 분석과 Perplexity 심층 결과를 한국어 3~5문장 단일 단락으로 재작성.
    실패 시 None 반환 → 호출부가 폴백 처리.
    """
    try:
        _load_openai_key()
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return None
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        delta_str = f"{delta_pct:+.2f}%" if delta_pct is not None else "미상"
        # 윈도우 연도 집합 (허용 집합)
        wf, wt, wf_str, wt_str = _window_bounds(change_date, months=6)
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
        # 프리픽스/헤더 라인 제거
        text = re.sub(r"^\s*\[[^\]]+\]\s*", "", text)
        return text or None
    except Exception as e:
        logger.warning("[MI Agent] reason 합성 실패: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 7) MarketIntelligenceAgent 메인 클래스
# ─────────────────────────────────────────────────────────────────────────────

def _apply_calibrated_weights() -> None:
    """
    최신 MediaCalibrator 결과가 있으면 MEDIA_DB의 weight를 인메모리에서 업데이트.
    호출 순서: MarketIntelligenceAgent 초기화 시 1회.
    """
    try:
        from agents.media_calibrator import get_calibrated_weights
        calibrated = get_calibrated_weights()
        if not calibrated:
            return
        updated = []
        for name, new_w in calibrated.items():
            if name in MEDIA_DB:
                old_w = MEDIA_DB[name]["weight"]
                if abs(new_w - old_w) > 0.01:
                    MEDIA_DB[name]["weight"] = new_w
                    updated.append(f"{name}: {old_w:.2f}→{new_w:.2f}")
        if updated:
            logger.info("[MI Agent] 캘리브레이션 가중치 적용: %s", ", ".join(updated))
        else:
            logger.debug("[MI Agent] 캘리브레이션 적용 — 변동 없음")
    except Exception as e:
        logger.debug("[MI Agent] 캘리브레이션 로드 건너뜀: %s", e)


class MarketIntelligenceAgent:
    """
    한국 의약전문 뉴스 매체 기반 약가 변동 사유 분석.
    초기화 시 최신 MediaCalibrator 결과를 자동으로 MEDIA_DB에 반영한다.

    사용 예시:
        agent = MarketIntelligenceAgent()
        result = agent.analyze_price_change(
            drug_ko="키트루다주",
            ingredient_ko="펨브롤리주맙,유전자재조합",
            change_date="2022.03.01",
            delta_pct=-25.61,
        )
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or BASE_DIR / "data" / "dashboard" / "reason_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        _apply_calibrated_weights()   # 최신 캘리브레이션 가중치 적용

    def _cache_path(self, drug_ko: str, change_date: str) -> Path:
        key = re.sub(r"[^\w]", "_", f"MI_{drug_ko}_{change_date}")
        return self.cache_dir / f"{key}.json"

    def get_cached(self, drug_ko: str, change_date: str) -> Optional[dict]:
        path = self._cache_path(drug_ko, change_date)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["cached"] = True
            return data
        return None

    def save_cache(self, drug_ko: str, change_date: str, result: dict) -> None:
        path = self._cache_path(drug_ko, change_date)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    def analyze_price_change(
        self,
        drug_ko: str,
        ingredient_ko: str,
        change_date: str,
        delta_pct: Optional[float] = None,
        drug_en: str = "",
        ingredient_en: str = "",
        force_refresh: bool = False,
    ) -> dict:
        """
        주요 진입점 — 2단계 엔진:
        1) Perplexity sonar-pro (실시간 웹검색 + MA 분석) — API 키 있을 때
        2) Naver 뉴스 수집 + GPT-4o 분석 (폴백)
        → 캐시 저장
        """
        if not force_refresh:
            cached = self.get_cached(drug_ko, change_date)
            if cached:
                return cached

        logger.info("[MI Agent] 분석 시작: %s %s (δ%s%%)", drug_ko, change_date, delta_pct)

        # 날짜 파싱
        try:
            dt = datetime.strptime(change_date, "%Y.%m.%d")
        except ValueError:
            try:
                dt = datetime.strptime(change_date[:7], "%Y.%m")
            except ValueError:
                dt = datetime.now()

        # ── 1단계: Perplexity sonar-pro ─────────────────────────────────────
        result = _perplexity_analyze(drug_ko, ingredient_ko, change_date, delta_pct)

        if result:
            refs = result.get("references", [])
            tier_counts: dict = {}
            for ref in refs:
                sc = score_source(ref.get("url", ""))
                t  = sc["tier"]
                tier_counts[t] = tier_counts.get(t, 0) + 1

            result["analysis_meta"] = {
                "source":            "perplexity-sonar-pro",
                "total_articles":    len(refs),
                "tier_a_count":      tier_counts.get("A", 0),
                "tier_b_count":      tier_counts.get("B", 0),
                "tier_c_count":      tier_counts.get("C", 0),
                "detected_mechanisms": (
                    [result["mechanism_label"]] if result.get("mechanism_label") else []
                ),
                "top_media": [
                    {"media": r.get("media", "기타"), "weight": r.get("weight", 0.5)}
                    for r in sorted(refs, key=lambda x: -x.get("weight", 0))[:5]
                ],
            }
            result = self._deep_research_if_low(result, drug_ko, ingredient_ko, change_date, delta_pct)
            result = _enforce_rules(result, change_date)   # 합성 후 재검증 (윈도우 외 연도 재유입 차단)
            result["cached"] = False
            self.save_cache(drug_ko, change_date, result)
            return result

        # ── 2단계: Naver 뉴스 + GPT-4o 폴백 ────────────────────────────────
        logger.info("[MI Agent] Naver+GPT-4o 폴백 실행")
        articles   = _collect_news(drug_ko, ingredient_ko, dt)
        all_text   = " ".join(f"{a['title']} {a.get('snippet','')}" for a in articles)
        mechanisms = classify_mechanism(all_text)
        logger.info("[MI Agent] 탐지 기전: %s", [m["label"] for m in mechanisms])

        result = _openai_analyze(drug_ko, change_date, delta_pct, articles, mechanisms)

        tier_counts = {}
        for a in articles:
            t = a.get("tier", "other")
            tier_counts[t] = tier_counts.get(t, 0) + 1

        result["analysis_meta"] = {
            "source":            "naver+gpt-4o",
            "total_articles":    len(articles),
            "tier_a_count":      tier_counts.get("A", 0),
            "tier_b_count":      tier_counts.get("B", 0),
            "tier_c_count":      tier_counts.get("C", 0),
            "detected_mechanisms": [m["label"] for m in mechanisms],
            "top_media": [
                {"media": a["media_name"], "weight": a["weight"]}
                for a in sorted(articles, key=lambda x: -x.get("weight", 0))[:5]
            ],
        }
        result = _enforce_rules(result, change_date)
        result = self._deep_research_if_low(result, drug_ko, ingredient_ko, change_date, delta_pct)
        result = _enforce_rules(result, change_date)   # 합성 후 재검증
        result["cached"] = False
        self.save_cache(drug_ko, change_date, result)
        return result

    def _deep_research_if_low(
        self,
        result: dict,
        drug_ko: str,
        ingredient_ko: str,
        change_date: str,
        delta_pct: Optional[float],
    ) -> dict:
        """
        1·2단계 결과의 confidence가 'low' 이거나 mechanism이 'unknown'인 경우,
        Perplexity 시장조사 에이전트를 **엄격한 4대 기전 규칙** 하에 호출해
        reason/evidence_summary를 보강한다.

        심층 리서치 규칙 (반드시 준수):
          - 변동 시점(change_date) ±6개월 이내의 사실만 근거로 사용
          - 한국 약가 사후관리 4대 기전 중 하나로 분류
          - 시점과 무관한 연도(예: 다른 해의 사례)는 비교 목적 외 인용 금지
          - 증거 없으면 "추정:" 접두, 꾸며내지 말 것
        """
        confidence = (result.get("confidence") or "").lower()
        mechanism  = (result.get("mechanism") or "").lower()
        if confidence != "low" and mechanism != "unknown":
            return result

        try:
            from agents.perplexity_research_agent import research
            logger.info("[MI Agent] 심층 리서치 에스컬레이션 (confidence=%s, mech=%s)",
                        confidence, mechanism)

            short_ing  = (ingredient_ko or drug_ko).split(",")[0].strip()
            brand_base = re.sub(r"(주|정|캡슐|액|주사|시럽)$", "", drug_ko).strip()
            delta_str  = f"{delta_pct:+.2f}%" if delta_pct is not None else "미상"
            # 시점 윈도우 계산 (±6개월)
            try:
                dt = datetime.strptime(change_date, "%Y.%m.%d")
            except Exception:
                dt = datetime.strptime(change_date[:7], "%Y.%m")
            year  = dt.year
            month = dt.month
            window_from = f"{year}.{max(1, month-6):02d}"
            window_to   = f"{year}.{min(12, month+6):02d}" if month+6 <= 12 else f"{year+1}.{(month+6)%12:02d}"

            strict_query = f"""[조사 대상]
- 약제명: {drug_ko} (브랜드: {brand_base})
- 성분명: {short_ing}
- 약가 변동 시점: {change_date}
- 변동률: {delta_str}
- 국가: 대한민국
- 윈도우: {window_from} ~ {window_to} (이 범위 밖 사실 인용 금지)

[반드시 아래 룰 원문을 그대로 준수할 것]
{MI_RULES_TEXT}

[답변 구조 한국어 500자 이내]
[기전 판정] / [핵심 근거 (윈도우 내, 매체·일자)] / [보완 설명] / [출처 URL]
"""
            deep = research(
                strict_query,
                mode="pro",
                drug_name=drug_ko,
                country="대한민국",
                save=True,
                temperature=0.1,
            )
            deep_answer = (deep.get("answer") or "").strip()
            if not deep_answer:
                return result

            # 합성: 원래 reason + deep_answer 를 단일 단락으로 재작성
            original_reason = result.get("reason", "")
            synthesized = _synthesize_reason(
                drug_ko=drug_ko,
                change_date=change_date,
                delta_pct=delta_pct,
                primary_reason=original_reason,
                deep_answer=deep_answer,
            )
            if synthesized:
                result["reason"] = synthesized
            else:
                result["reason"] = original_reason or deep_answer  # 폴백: 프리픽스 없이 하나만 노출
            result["deep_research"] = {
                "source":   "perplexity-sonar-pro-research",
                "model":    deep.get("model"),
                "citations": deep.get("citations", []),
                "created_at": deep.get("created_at"),
            }
            # 기존 references에 심층 citations 병합
            existing = {r.get("url", "") for r in result.get("references", [])}
            for url in deep.get("citations", []):
                if url and url not in existing:
                    sc = score_source(url)
                    result.setdefault("references", []).append({
                        "title":  url.split("/")[-1][:60] or url,
                        "url":    url,
                        "media":  sc["media_name"],
                        "weight": sc["weight"],
                    })
            # 리서치 보강 후 신뢰도를 medium으로 상향 (최소 증거 확보됨)
            if confidence == "low":
                result["confidence"] = "medium"
            logger.info("[MI Agent] 심층 리서치 완료 — citations: %d건",
                        len(deep.get("citations", [])))
        except Exception as e:
            logger.warning("[MI Agent] 심층 리서치 실패: %s", e)
        return result

    def get_media_leaderboard(self) -> list:
        """매체 신뢰도 리더보드 (가중치 정렬, 캘리브레이션 날짜 포함)."""
        try:
            from agents.media_calibrator import load_latest_calibration
            cal = load_latest_calibration()
            last_calibrated = cal["calibrated_at"][:10] if cal else "미보정"
        except Exception:
            last_calibrated = "미보정"

        rows = sorted(
            [{"media": k, **{f: v for f, v in info.items() if f != "domain"}}
             for k, info in MEDIA_DB.items()],
            key=lambda x: -x["weight"]
        )
        return {"last_calibrated": last_calibrated, "leaderboard": rows}


# ─────────────────────────────────────────────────────────────────────────────
# CLI 테스트
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    agent = MarketIntelligenceAgent()

    if len(sys.argv) > 1 and sys.argv[1] == "leaderboard":
        board = agent.get_media_leaderboard()
        print(f"\n{'매체명':<28} {'가중치':>5}  Tier  V  N  MA  설명")
        print("-" * 90)
        for r in board:
            print(
                f"{r['media']:<28} {r['weight']:>5.1f}  {r['tier']:>4}  "
                f"{r['volume']}  {r['novelty']}  {r['ma_depth']:>2}  {r['desc'][:40]}"
            )
        sys.exit(0)

    drug    = sys.argv[1] if len(sys.argv) > 1 else "키트루다주"
    ing     = sys.argv[2] if len(sys.argv) > 2 else "펨브롤리주맙,유전자재조합"
    date    = sys.argv[3] if len(sys.argv) > 3 else "2022.03.01"
    delta   = float(sys.argv[4]) if len(sys.argv) > 4 else -25.61

    result = agent.analyze_price_change(
        drug_ko=drug, ingredient_ko=ing,
        change_date=date, delta_pct=delta,
        force_refresh=True,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
