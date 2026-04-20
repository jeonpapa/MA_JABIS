"""CompetitorTrendsAgent — 경쟁 브랜드 뉴스 자동 크롤 + LLM 중요도 필터.

플로우:
  1) COMPETITOR_BRANDS 별로 Naver News 지난 N 일 (기본 7일) 기사 수집
  2) GPT-4o 에 batch 로 넘겨 importance + badge + headline/detail 구조화
  3) importance ∈ {critical, moderate} 이고 badge 가 허용 목록에 속하면
     competitor_trend 테이블에 source_type='auto_naver' 로 UPSERT (url UNIQUE)
  4) manual 로 저장된 카드는 절대 덮어쓰지 않음 (source_type 조건)

사용:
  CLI:   PYTHONPATH=. python agents/competitor_trends_agent.py [--days 7] [--dry-run]
  API:   POST /api/admin/competitor-trends/refresh (admin)

주 1회 cron 예시 (user crontab):
  0 7 * * MON  cd /path/to/MA_AI_Dossier && PYTHONPATH=. python3 agents/competitor_trends_agent.py >> logs/competitor_trends.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agents.db import DrugPriceDB
from agents.naver_news import NewsItem, get_client

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# 경쟁 브랜드 → 회사/로고/색상 메타
# ─────────────────────────────────────────────────────────────────────────────

COMPETITOR_BRANDS: list[dict[str, str]] = [
    {"query": "옵디보",   "company": "BMS Korea",          "logo": "BMS", "color": "#3B82F6"},
    {"query": "타그리소", "company": "AstraZeneca Korea",  "logo": "AZ",  "color": "#00E5CC"},
    {"query": "임핀지",   "company": "AstraZeneca Korea",  "logo": "AZ",  "color": "#00E5CC"},
    {"query": "린파자",   "company": "AstraZeneca Korea",  "logo": "AZ",  "color": "#00E5CC"},
    {"query": "테쎈트릭", "company": "Roche Korea",        "logo": "RC",  "color": "#EF4444"},
    {"query": "레블리미드","company": "BMS Korea",         "logo": "BMS", "color": "#3B82F6"},
    {"query": "다잘렉스", "company": "Janssen Korea",      "logo": "JNJ", "color": "#F59E0B"},
]

ALLOWED_BADGES = ["신규 출시", "가격 변동", "임상 진행", "급여 등재", "파이프라인", "전략 변화"]
BADGE_COLOR = {
    "신규 출시":   "bg-emerald-500/20 text-emerald-400",
    "가격 변동":   "bg-amber-500/20 text-amber-400",
    "임상 진행":   "bg-violet-500/20 text-violet-400",
    "급여 등재":   "bg-emerald-500/20 text-emerald-400",
    "파이프라인": "bg-blue-500/20 text-blue-400",
    "전략 변화":   "bg-rose-500/20 text-rose-400",
}

DEFAULT_MODEL = "gpt-4o-mini"


# ─────────────────────────────────────────────────────────────────────────────
# LLM 프롬프트
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 한국 Market Access 담당자를 위한 경쟁사 뉴스 큐레이터입니다.
입력으로 받는 뉴스 기사 목록을 분석해, MA 업무에 **의미있는 항목만** 구조화하세요.

중요도 기준 (우선순위 순):
  critical = 허가/승인 · 급여 등재/가격 · 적응증 확대 · 주요 임상 결과 발표
  moderate = 국내 마케팅 제휴 · 파이프라인 신규 단계 진입 · 가이드라인 반영
  low      = 일반 매출, 주가, 마케팅 행사, 단순 홍보 — 결과에서 제외

badge 값은 아래 6개 중 하나만 사용. 해당 없으면 해당 item 은 drop.
  "신규 출시" | "가격 변동" | "임상 진행" | "급여 등재" | "파이프라인" | "전략 변화"

반드시 JSON 만 출력. 다른 텍스트 금지.

{
  "items": [
    {
      "news_index": <int, 입력 배열 인덱스>,
      "importance": "critical" | "moderate",
      "badge": "...",
      "headline": "15~30자 간결 요약",
      "detail": "2~3문장, MA 담당자가 즉시 이해할 수 있는 핵심 팩트 + 시사점"
    }
  ]
}
"""


@dataclass
class CrawlResult:
    brand: str
    company: str
    fetched: int
    accepted: int
    skipped_low: int
    upserted: int
    errors: list[str]


# ─────────────────────────────────────────────────────────────────────────────
# LLM 필터
# ─────────────────────────────────────────────────────────────────────────────

def _llm_filter(news: list[NewsItem], brand: str, model: str) -> list[dict[str, Any]]:
    """뉴스 배치 → LLM 이 구조화한 카드 목록. 실패 시 빈 리스트."""
    if not news:
        return []
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("[CompetitorTrends] OPENAI_API_KEY 없음 — LLM 필터 skip")
        return []

    try:
        from openai import OpenAI
    except ImportError:
        logger.error("[CompetitorTrends] openai 패키지 미설치")
        return []

    payload = [{
        "index": i,
        "title": n.title,
        "description": n.description[:500],
        "source": n.source,
        "date": n.date_str,
    } for i, n in enumerate(news)]

    user_msg = (
        f"브랜드: {brand}\n"
        f"뉴스 {len(news)}건 (최신순):\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "위 원칙에 따라 importance ∈ {critical, moderate} 만 반환. low 는 전부 drop."
    )
    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=1500,
        )
        raw = resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error("[CompetitorTrends] LLM 호출 실패 (%s): %s", brand, e)
        return []

    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    m = re.search(r"\{[\s\S]+\}", raw)
    if m:
        raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("[CompetitorTrends] LLM JSON 파싱 실패 (%s): %s", brand, e)
        return []

    items = data.get("items", []) if isinstance(data, dict) else []
    return [it for it in items if isinstance(it, dict)]


# ─────────────────────────────────────────────────────────────────────────────
# DB UPSERT (url UNIQUE 기반)
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_trend(db: DrugPriceDB, row: dict[str, Any]) -> bool:
    """url 이 있으면 unique index 로 dedup. 이미 manual 로 저장된 url 은 touch X.

    Returns True if inserted/updated, False if skipped (manual collision).
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    url = row.get("url")
    with db._connect() as conn:
        if url:
            existing = conn.execute(
                "SELECT id, source_type FROM competitor_trend WHERE url = ?",
                (url,),
            ).fetchone()
            if existing and existing[1] == "manual":
                return False
            if existing:
                conn.execute(
                    """
                    UPDATE competitor_trend
                       SET company=?, logo=?, color=?, badge=?, badge_color=?,
                           headline=?, detail=?, date=?, source=?,
                           source_type='auto_naver', importance=?, updated_at=?
                     WHERE id=?
                    """,
                    (row["company"], row["logo"], row["color"], row["badge"],
                     row["badge_color"], row["headline"], row["detail"], row["date"],
                     row["source"], row["importance"], now, existing[0]),
                )
                conn.commit()
                return True
        conn.execute(
            """
            INSERT INTO competitor_trend
                (company, logo, color, badge, badge_color, headline, detail,
                 date, source, url, source_type, importance, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (row["company"], row["logo"], row["color"], row["badge"],
             row["badge_color"], row["headline"], row["detail"], row["date"],
             row["source"], url, "auto_naver", row["importance"], now, now),
        )
        conn.commit()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────────────────────────────────────

def run(days: int = 7, dry_run: bool = False, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """경쟁 브랜드 전체 크롤 + 필터 + DB 반영."""
    env_path = BASE_DIR / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass

    client = get_client()
    if not client.is_configured:
        return {"ok": False, "error": "NAVER_API 키 미설정"}

    db = DrugPriceDB(BASE_DIR / "data" / "db" / "drug_prices.db")
    cutoff = datetime.now() - timedelta(days=days)
    results: list[CrawlResult] = []

    for meta in COMPETITOR_BRANDS:
        brand = meta["query"]
        fetched = []
        try:
            _, fetched = client.daily_counts(brand, days=days, max_pages=3)
        except Exception as e:
            logger.error("[CompetitorTrends] Naver 검색 실패 (%s): %s", brand, e)
            results.append(CrawlResult(brand, meta["company"], 0, 0, 0, 0, [str(e)]))
            continue

        fetched = [n for n in fetched if n.pub_date >= cutoff]
        fetched.sort(key=lambda n: n.pub_date, reverse=True)
        fetched = fetched[:30]  # LLM payload 상한

        if not fetched:
            results.append(CrawlResult(brand, meta["company"], 0, 0, 0, 0, []))
            continue

        llm_items = _llm_filter(fetched, brand, model)
        accepted = 0
        skipped_low = len(fetched) - len(llm_items)
        upserted = 0
        errors: list[str] = []

        for it in llm_items:
            try:
                idx = int(it.get("news_index", -1))
                if idx < 0 or idx >= len(fetched):
                    continue
                badge = it.get("badge", "")
                if badge not in ALLOWED_BADGES:
                    continue
                headline = (it.get("headline") or "").strip()
                detail = (it.get("detail") or "").strip()
                importance = it.get("importance", "moderate")
                if importance not in ("critical", "moderate"):
                    continue
                if not headline or not detail:
                    continue

                src_news = fetched[idx]
                row = {
                    "company": meta["company"],
                    "logo": meta["logo"],
                    "color": meta["color"],
                    "badge": badge,
                    "badge_color": BADGE_COLOR.get(badge, ""),
                    "headline": headline[:120],
                    "detail": detail[:500],
                    "date": src_news.date_str,
                    "source": src_news.source or "네이버뉴스",
                    "url": src_news.original_link or src_news.link,
                    "importance": importance,
                }
                accepted += 1
                if dry_run:
                    logger.info("[DRY] %s | %s | %s", brand, badge, headline)
                else:
                    if _upsert_trend(db, row):
                        upserted += 1
            except Exception as e:
                errors.append(str(e))

        results.append(CrawlResult(brand, meta["company"], len(fetched), accepted, skipped_low, upserted, errors))
        logger.info(
            "[CompetitorTrends] %s: fetched=%d accepted=%d upserted=%d skipped_low=%d",
            brand, len(fetched), accepted, upserted, skipped_low,
        )

    return {
        "ok": True,
        "dry_run": dry_run,
        "days": days,
        "model": model,
        "brands": [
            {
                "brand": r.brand, "company": r.company,
                "fetched": r.fetched, "accepted": r.accepted,
                "skipped_low": r.skipped_low, "upserted": r.upserted,
                "errors": r.errors,
            } for r in results
        ],
        "totals": {
            "fetched": sum(r.fetched for r in results),
            "accepted": sum(r.accepted for r in results),
            "upserted": sum(r.upserted for r in results),
        },
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Competitor Trends 자동 크롤")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    result = run(days=args.days, dry_run=args.dry_run, model=args.model)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
