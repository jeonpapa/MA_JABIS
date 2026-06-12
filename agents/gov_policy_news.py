"""정부·보건당국 정책 뉴스 아카이브 — competitor_news 인프라 재사용.

kind='gov_policy' 로 보건복지부/심평원/건보공단/식약처 + 약가정책 테마 뉴스를
competitor_news 테이블에 영구 아카이브한다 (canonical dedup·365일 보존·tier 분류 동일).
정부 키워드 클라우드는 이 아카이브에서 생성된다 (government_keyword_summary).

핵심: 클라우드 키워드 ← 아카이브 기사에서 추출 → 키워드마다 출처 기사가 보장됨.
(이전: 키워드를 먼저 만들고 뉴스를 사후 문자열 매칭 → 빈 키워드 발생)

- brand = 기관명(보건복지부/심평원/건보공단/식약처/정책일반), anchor = 검색 seed, kind='gov_policy'.
- 다운로드/upsert/dedup/보존/tier 분류는 competitor_news_agent 의 검증된 primitive 재사용.
- 정책 뉴스는 전문지 외 일반지에도 실리므로 Tier 제한 없이 저장(tier 컬럼엔 분류 기록).

실행: python -m agents.gov_policy_news [lookback_days]
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from agents.naver_news import NaverNewsClient
from agents import competitor_news_agent as _cn

logger = logging.getLogger(__name__)

KIND = "gov_policy"
DEFAULT_LOOKBACK_DAYS = 31

# 기관(brand 로 저장) + 대표 검색 seed (anchor 로 저장). LLM 이 아카이브에서 실제 테마를 추출.
GOV_AGENCIES = [
    {"agency": "보건복지부",
     "queries": ["보건복지부 약가", "복지부 건강보험 약제", "보건복지부 급여", "복지부 제약"]},
    {"agency": "건강보험심사평가원",
     "queries": ["심사평가원 약제 급여", "심평원 약가", "심평원 급여기준", "심평원 약제급여평가위원회"]},
    {"agency": "국민건강보험공단",
     "queries": ["건강보험공단 약가협상", "건보공단 약제 협상", "공단 위험분담", "건보공단 약가"]},
    {"agency": "식품의약품안전처",
     "queries": ["식품의약품안전처 신약 허가", "식약처 의약품 허가", "식약처 품목허가", "식약처 약제"]},
    {"agency": "정책일반",
     "queries": ["건강보험정책심의위원회 약가", "약제급여평가위원회", "약가 인하 정책",
                 "급여 적정성 재평가", "위험분담제 약가", "사용량 약가 연동",
                 "2026 약가제도 개편", "선별급여", "신약 급여 등재", "실거래가 약가"]},
]

# 관련성 게이트용 보건/약가 맥락어 — 표면에 최소 1개 있어야 정책뉴스로 인정 (일반어 오수집 방지)
_CONTEXT_ANCHORS = (
    "약가", "약제", "급여", "허가", "건강보험", "심평원", "심사평가원", "복지부",
    "건보", "공단", "식약처", "등재", "보험", "RSA", "위험분담", "제약", "약평위",
    "건정심", "재평가", "선별급여",
)


def _gov_relevant(surface: str, query: str) -> bool:
    """관련성: 쿼리 토큰(2자+) 1개 이상 표면 등장 AND 보건/약가 맥락어 1개 이상."""
    toks = [t for t in query.split() if len(t) >= 2]
    if not any(t in surface for t in toks):
        return False
    return any(a in surface for a in _CONTEXT_ANCHORS)


def crawl(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """정책 뉴스를 competitor_news(kind='gov_policy') 로 아카이브. 만료분 자동 정리."""
    _cn.ensure_schema()
    client = NaverNewsClient()
    if not client.is_configured:
        return {"error": "Naver API 키 미설정", "results": []}

    results = []
    with _cn._connect() as conn:
        for ag in GOV_AGENCIES:
            agency = ag["agency"]
            stored = dup = irrel = 0
            for q in ag["queries"]:
                try:
                    fetched = _cn._fetch_brand(client, q, lookback_days)
                except Exception as e:
                    logger.warning("[gov_policy] %s 수집 실패: %s", q, e)
                    continue
                for it in fetched:
                    surface = f"{it.title} {it.description}"
                    if not _gov_relevant(surface, q):
                        irrel += 1
                        continue
                    url = it.original_link or it.link
                    tier, name = _cn.classify_tier(url)
                    meta = {"query": agency, "company": agency, "anchor": q, "kind": KIND}
                    if _cn._upsert_record(conn, meta, title=it.title, url=url,
                                          naver_link=it.link, description=it.description,
                                          pub_date=it.pub_date, tier=tier or 3,
                                          source_name=name, collected_via="naver_gov"):
                        stored += 1
                    else:
                        dup += 1
            conn.commit()
            results.append({"agency": agency, "stored": stored, "dup": dup,
                            "skipped_irrelevant": irrel})
            logger.info("[gov_policy] %s: 신규 %d · 중복 %d · 무관 %d",
                        agency, stored, dup, irrel)
    expired = _cn.cleanup_expired()
    return {"kind": KIND, "lookback_days": lookback_days, "results": results,
            "total_stored": sum(r["stored"] for r in results),
            "expired_removed": expired}


def list_archive(days: int = 31, limit: int = 200) -> list[dict]:
    """gov_policy 아카이브 조회 (키워드 클라우드 입력). 최신순."""
    _cn.ensure_schema()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _cn._connect() as conn:
        rows = conn.execute(
            "SELECT id, brand, anchor, title, url, naver_link, source_name, "
            "source_domain, tier, description, pub_date FROM competitor_news "
            "WHERE kind = ? AND pub_date >= ? ORDER BY pub_date DESC, id DESC LIMIT ?",
            (KIND, cutoff, max(1, min(limit, 500)))).fetchall()
    return [dict(r) for r in rows]


def archive_count(days: int = 31) -> int:
    _cn.ensure_schema()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _cn._connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM competitor_news WHERE kind = ? AND pub_date >= ?",
            (KIND, cutoff)).fetchone()[0]


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    look = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOOKBACK_DAYS
    print(json.dumps(crawl(look), ensure_ascii=False, indent=2))
