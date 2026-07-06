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

# 추적 브랜드 — competitor_news_agent 와 단일 소스 공유 (13 브랜드, 2026-06 사용자 확정)
# COMPETITOR_BRANDS 는 DB(competitor_brand) 가 비어있을 때의 폴백 + seed 소스로 계속 보존.
from agents.competitor_news_agent import (  # noqa: E402,F401
    COMPETITOR_BRANDS,
    _url_hash,
    classify_tier,
)
from agents.editable_factors import (  # noqa: E402
    get_competitor_brands,
    get_competitor_relevance_terms,
)

DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

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

**클러스터링 규칙 (같은 이벤트 = 하나의 item)**:
  - **같은 이벤트**(동일 승인·급여 결정·임상 결과 발표 등)를 다룬 기사들은 반드시
    **하나의 item 으로 묶고** news_indexes 에 해당 기사 index 를 전부 나열.
  - 서로 다른 이벤트는 별도 item. 하나의 기사 index 를 두 item 에 중복 배정 금지.

**충실성 규칙 (반드시 준수)**:
  - 해당 브랜드가 기사의 **주요 주제**가 아니라 단순 비교·맥락으로만 언급된 경우 → drop.
    (예: '렉라자 매출 급증' 기사에서 타그리소가 비교로만 나오면 타그리소 카드 만들지 말 것)
  - headline·detail 은 **나열된 기사들에 실제로 쓰인 내용만** 반영. 기사에 없는 수치·결론·일자 추측/생성 금지.
  - news_indexes 는 반드시 같은 이벤트를 다룬 기사들의 정확한 index. 다른 이벤트 기사를 섞지 말 것.

반드시 JSON 만 출력. 다른 텍스트 금지.

{
  "items": [
    {
      "news_indexes": [<int, 입력 배열 인덱스>, ...],
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
# 클러스터링 헬퍼 (B1/B2)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_source_tier(db: DrugPriceDB) -> None:
    """competitor_trend.source_tier 멱등 마이그레이션 (기존 DB ALTER)."""
    try:
        with db._connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(competitor_trend)")}
            if cols and "source_tier" not in cols:
                conn.execute("ALTER TABLE competitor_trend ADD COLUMN source_tier INTEGER")
                conn.commit()
                logger.info("[CompetitorTrends] competitor_trend.source_tier 추가")
    except Exception as e:
        logger.warning("[CompetitorTrends] source_tier 마이그레이션 실패: %s", e)


def _parse_news_indexes(it: dict[str, Any]) -> list[int]:
    """LLM item 의 news_indexes:[int] 파싱. 구 계약(news_index:int)도 흡수."""
    raw = it.get("news_indexes")
    if raw is None and "news_index" in it:
        raw = [it.get("news_index")]
    if isinstance(raw, (int, float)):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for v in raw:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv not in out:
            out.append(iv)
    return out


def _passes_relevance_guard(brand: str, title: str, description: str = "") -> bool:
    """제목 관련성 가드 (오귀속 방지) — 브랜드가 제목에 있으면 통과.
    없더라도 admin relevance 키워드(B3)가 제목에 있고 브랜드가 표면(제목+발췌)에
    존재하면 이벤트 기사로 인정. 그 외는 '본문 스치는 언급'으로 drop."""
    title = title or ""
    if brand in title:
        return True
    if brand not in f"{title} {description or ''}":
        return False
    try:
        terms = get_competitor_relevance_terms()
    except Exception:
        terms = ()
    return any(t in title for t in terms if t)


def _pick_representative(members: list[dict[str, Any]]) -> dict[str, Any]:
    """클러스터 대표 기사 — 최저 tier(최고 신뢰) 우선, 동률이면 최신 발행일."""
    best_tier = min((m.get("tier") or 3) for m in members)
    cands = [m for m in members if (m.get("tier") or 3) == best_tier]
    return max(cands, key=lambda m: m.get("date") or "")


def _lookup_news_ids(db: DrugPriceDB, urls: list[str]) -> list[int]:
    """기사 URL → competitor_news.id (canonical url_hash 매칭). 아카이브 미보유분은 skip."""
    hashes = [_url_hash(u) for u in urls if u]
    if not hashes:
        return []
    try:
        with db._connect() as conn:
            qmarks = ",".join("?" for _ in hashes)
            rows = conn.execute(
                f"SELECT id FROM competitor_news WHERE url_hash IN ({qmarks})", hashes
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# DB UPSERT (url UNIQUE 기반)
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_trend(db: DrugPriceDB, row: dict[str, Any],
                  source_type: str = "auto_naver",
                  member_news_ids: list[int] | None = None) -> bool:
    """url 이 있으면 unique index 로 dedup. 이미 manual 로 저장된 url 은 touch X.

    source_type: 'auto_naver'(주간 크롤) | 'promoted'(아카이브 승격).
    우선순위 manual > (promoted/auto_naver). manual 은 절대 덮어쓰지 않음.
    B1: member_news_ids 에 클러스터 멤버 기사(competitor_news.id)를 넘기면
    trend_id 역링크를 UPDATE (카드↔기사 N:1). row['source_tier'] = 대표 tier.
    Returns True if inserted/updated, False if skipped (manual collision).
    """
    _ensure_source_tier(db)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    url = row.get("url")
    source_tier = row.get("source_tier")
    with db._connect() as conn:
        trend_id: int | None = None
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
                           source_type=?, importance=?, source_tier=?, updated_at=?
                     WHERE id=?
                    """,
                    (row["company"], row["logo"], row["color"], row["badge"],
                     row["badge_color"], row["headline"], row["detail"], row["date"],
                     row["source"], source_type, row["importance"], source_tier,
                     now, existing[0]),
                )
                trend_id = existing[0]
        if trend_id is None:
            cur = conn.execute(
                """
                INSERT INTO competitor_trend
                    (company, logo, color, badge, badge_color, headline, detail,
                     date, source, url, source_type, importance, source_tier,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (row["company"], row["logo"], row["color"], row["badge"],
                 row["badge_color"], row["headline"], row["detail"], row["date"],
                 row["source"], url, source_type, row["importance"], source_tier,
                 now, now),
            )
            trend_id = cur.lastrowid
        # B1: 멤버 기사 → 카드 역링크 (competitor_news.trend_id)
        if member_news_ids and trend_id:
            try:
                qmarks = ",".join("?" for _ in member_news_ids)
                conn.execute(
                    f"UPDATE competitor_news SET trend_id = ? WHERE id IN ({qmarks})",
                    [trend_id, *member_news_ids],
                )
            except Exception as e:
                logger.warning("[CompetitorTrends] trend_id 역링크 실패 (trend=%s): %s",
                               trend_id, e)
        conn.commit()
    return True


def _archive_to_newsitems(rows: list[dict]) -> list[NewsItem]:
    """competitor_news 아카이브 dict → NewsItem (LLM 필터 입력 형태로 변환)."""
    from datetime import datetime as _dt
    out = []
    for r in rows:
        pd_str = (r.get("pub_date") or "")[:10]
        try:
            pd = _dt.strptime(pd_str, "%Y-%m-%d")
        except Exception:
            pd = _dt.now()
        url = r.get("url") or ""
        out.append(NewsItem(
            title=r.get("title") or "",
            link=url,
            original_link=url,
            description=r.get("description") or "",
            pub_date=pd,
            source=r.get("source_name") or "",
        ))
    return out


def promote_from_archive(days: int = 1, dry_run: bool = False,
                         model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """아카이브(competitor_news) 최근 N일 뉴스 → 동향 카드 자동 승격 (매일).

    신규 Naver 크롤 없이 이미 수집·보존된 아카이브를 소스로 사용. LLM 필터(badge/
    importance)로 의미있는 기사만 competitor_trend 에 source_type='promoted' 로 UPSERT.
    manual 카드는 보존. run()(주간 신규 크롤)과 상보적.
    """
    env_path = BASE_DIR / "config" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass
    from agents.competitor_news_agent import list_news

    db = DrugPriceDB(DB_PATH)
    results: list[CrawlResult] = []

    for meta in get_competitor_brands():
        brand = meta["query"]
        rows = list_news(brand=brand, days=days, limit=30)
        if not rows:
            results.append(CrawlResult(brand, meta["company"], 0, 0, 0, 0, []))
            continue
        news = _archive_to_newsitems(rows)
        llm_items = _llm_filter(news, brand, model)
        accepted = upserted = 0
        errors: list[str] = []
        for it in llm_items:
            try:
                badge = it.get("badge", "")
                if badge not in ALLOWED_BADGES:
                    continue
                headline = (it.get("headline") or "").strip()
                detail = (it.get("detail") or "").strip()
                importance = it.get("importance", "moderate")
                if importance not in ("critical", "moderate") or not headline or not detail:
                    continue
                # B1: 같은 이벤트 기사 묶음 → 멤버 수집 (관련성 가드 통과분만)
                members: list[dict[str, Any]] = []
                for idx in _parse_news_indexes(it):
                    if idx < 0 or idx >= len(rows):
                        continue
                    r = rows[idx]
                    if not _passes_relevance_guard(brand, r.get("title") or "",
                                                   r.get("description") or ""):
                        continue
                    members.append({
                        "news_id": r.get("id"),
                        "tier": r.get("tier"),
                        "url": r.get("url") or "",
                        "date": (r.get("pub_date") or "")[:10],
                        "source_name": r.get("source_name") or "",
                    })
                if not members:
                    continue
                rep = _pick_representative(members)   # 최저 tier = 최고 신뢰 매체
                row = {
                    "company": meta["company"], "logo": meta["logo"], "color": meta["color"],
                    "badge": badge, "badge_color": BADGE_COLOR.get(badge, ""),
                    "headline": headline[:120], "detail": detail[:500],
                    "date": rep["date"] or datetime.now().strftime("%Y-%m-%d"),
                    "source": rep["source_name"] or "네이버뉴스",
                    "url": rep["url"], "importance": importance,
                    "source_tier": min((m.get("tier") or 3) for m in members),
                }
                member_ids = [m["news_id"] for m in members if m.get("news_id")]
                accepted += 1
                if dry_run:
                    logger.info("[DRY-PROMOTE] %s | %s | %s (%d개 매체)",
                                brand, badge, headline, len(members))
                elif _upsert_trend(db, row, source_type="promoted",
                                   member_news_ids=member_ids):
                    upserted += 1
            except Exception as e:
                errors.append(str(e))
        results.append(CrawlResult(brand, meta["company"], len(news), accepted,
                                   len(news) - len(llm_items), upserted, errors))
        logger.info("[Promote] %s: archive=%d accepted=%d upserted=%d",
                    brand, len(news), accepted, upserted)

    return {
        "ok": True, "dry_run": dry_run, "days": days, "model": model, "source": "archive",
        "totals": {
            "archive": sum(r.fetched for r in results),
            "accepted": sum(r.accepted for r in results),
            "upserted": sum(r.upserted for r in results),
        },
    }


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

    db = DrugPriceDB(DB_PATH)
    cutoff = datetime.now() - timedelta(days=days)
    results: list[CrawlResult] = []

    for meta in get_competitor_brands():
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

        # B2: 매체 tier 분류 — tier3(미등록/저신뢰 매체)는 LLM 입력에서 제외
        batch: list[tuple[NewsItem, int, str | None]] = []
        skipped_tier3 = 0
        for n in fetched:
            tier, tier_name = classify_tier(n.original_link or n.link)
            if (tier or 3) >= 3:
                skipped_tier3 += 1
                continue
            batch.append((n, tier, tier_name))
        batch = batch[:30]  # LLM payload 상한
        news = [b[0] for b in batch]

        if not news:
            results.append(CrawlResult(brand, meta["company"], 0, 0, skipped_tier3, 0, []))
            continue

        llm_items = _llm_filter(news, brand, model)
        accepted = 0
        skipped_low = skipped_tier3 + (len(news) - len(llm_items))
        upserted = 0
        errors: list[str] = []

        for it in llm_items:
            try:
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

                # B1: 같은 이벤트 기사 묶음 → 멤버 수집 (관련성 가드 통과분만).
                # 가드 — 브랜드가 기사 제목에 없으면 '본문 스치는 언급'(비교·맥락)일
                # 확률이 높아 헤드라인 오귀속을 유발 → drop. (relevance 키워드로 보완)
                members: list[dict[str, Any]] = []
                for idx in _parse_news_indexes(it):
                    if idx < 0 or idx >= len(batch):
                        continue
                    n, tier, tier_name = batch[idx]
                    if not _passes_relevance_guard(brand, n.title, n.description):
                        continue
                    members.append({
                        "news_id": None,
                        "tier": tier,
                        "url": n.original_link or n.link,
                        "date": n.date_str,
                        "source_name": n.source or tier_name or "",
                    })
                if not members:
                    continue
                rep = _pick_representative(members)   # 최저 tier = 최고 신뢰 매체
                row = {
                    "company": meta["company"],
                    "logo": meta["logo"],
                    "color": meta["color"],
                    "badge": badge,
                    "badge_color": BADGE_COLOR.get(badge, ""),
                    "headline": headline[:120],
                    "detail": detail[:500],
                    "date": rep["date"],
                    "source": rep["source_name"] or "네이버뉴스",
                    "url": rep["url"],
                    "importance": importance,
                    "source_tier": min((m.get("tier") or 3) for m in members),
                }
                # 아카이브(competitor_news)에 이미 수집된 기사면 trend_id 역링크
                member_ids = _lookup_news_ids(db, [m["url"] for m in members])
                accepted += 1
                if dry_run:
                    logger.info("[DRY] %s | %s | %s (%d개 매체)",
                                brand, badge, headline, len(members))
                else:
                    if _upsert_trend(db, row, member_news_ids=member_ids):
                        upserted += 1
            except Exception as e:
                errors.append(str(e))

        results.append(CrawlResult(brand, meta["company"], len(news), accepted, skipped_low, upserted, errors))
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
