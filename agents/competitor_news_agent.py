"""경쟁사 뉴스 아카이브 — Tier 1 전문지 6개월 수집 + 1년 누적.

기존 competitor_trends_agent (주 1회 LLM 큐레이션 → competitor_trend 카드) 와 보완 관계:
  - 이 모듈은 **원문 메타데이터 + 링크를 그대로 누적** (LLM 미사용 → 저비용·전수).
  - Tier 는 매체 도메인 기반 (config/media_tiers.json). 현재 수집은 T1(제약·의료 전문지) 만.
  - 보존: pub_date + 365일 (expires_at). scan 시 만료분 자동 정리.
  - Competitor Trends 탭: 카드(company) ↔ 뉴스(company/brand) 연결 + 브랜드·Tier 필터 아카이브.

데이터 정직성: 원문 제목·링크·발행일만 저장. 요약/판단 가공 없음 (description 은 Naver 제공 발췌 원문).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from agents.naver_news import NaverNewsClient, NewsItem
from agents.scrapers import tier1_news_sites as _t1sites

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"
TIERS_PATH = BASE_DIR / "config" / "media_tiers.json"

RETENTION_DAYS = 365
DEFAULT_LOOKBACK_DAYS = 183  # ~6개월

# ── 추적 브랜드 레지스트리 (2026-06 사용자 확정) ──────────────────────────────
#   kind: competitor=경쟁 브랜드 / msd_asset=MSD 자체 자산 모니터
#   anchor: 연관 MSD 자산 또는 경쟁 클래스 (메타데이터 — 그룹핑용)
COMPETITOR_BRANDS: list[dict[str, str]] = [
    # PD-(L)1 면역항암 (vs 키트루다)
    {"query": "옵디보",   "company": "BMS Korea",            "anchor": "키트루다 / PD-(L)1", "kind": "competitor", "logo": "BMS", "color": "#3B82F6"},
    {"query": "티쎈트릭", "company": "Roche Korea",          "anchor": "키트루다 / PD-(L)1", "kind": "competitor", "logo": "RC",  "color": "#EF4444"},
    {"query": "임핀지",   "company": "AstraZeneca Korea",    "anchor": "키트루다 / PD-(L)1", "kind": "competitor", "logo": "AZ",  "color": "#00E5CC"},
    {"query": "바벤시오", "company": "Merck/Pfizer Korea",   "anchor": "키트루다 / PD-(L)1", "kind": "competitor", "logo": "MK",  "color": "#8B5CF6"},
    {"query": "리브타요", "company": "Sanofi Korea",         "anchor": "키트루다 / PD-(L)1", "kind": "competitor", "logo": "SF",  "color": "#7C3AED"},
    {"query": "테빔브라", "company": "BeiGene Korea",        "anchor": "키트루다 / PD-(L)1", "kind": "competitor", "logo": "BG",  "color": "#06B6D4"},
    # EGFR 폐암 표적 (vs 키트루다 폐암)
    {"query": "타그리소", "company": "AstraZeneca Korea",    "anchor": "키트루다 / EGFR 폐암", "kind": "competitor", "logo": "AZ", "color": "#00E5CC"},
    # ADC
    {"query": "엔허투",   "company": "Daiichi Sankyo / AZ",  "anchor": "ADC", "kind": "competitor", "logo": "DS", "color": "#F59E0B"},
    {"query": "트로델비", "company": "Gilead Korea",         "anchor": "ADC", "kind": "competitor", "logo": "GL", "color": "#10B981"},
    {"query": "다트로웨이","company": "Daiichi Sankyo / AZ",  "anchor": "ADC", "kind": "competitor", "logo": "DS", "color": "#F59E0B"},
    # Others
    {"query": "빌로이",   "company": "Astellas Korea",       "anchor": "Others / 위암 CLDN18.2", "kind": "competitor", "logo": "AS", "color": "#EC4899"},
    {"query": "파드셉",   "company": "Astellas/Pfizer Korea","anchor": "Others / 요로상피암 ADC", "kind": "competitor", "logo": "AS", "color": "#EC4899"},
    # MSD 자체 자산 모니터 (경쟁 브랜드 미지정 — 자체 신규 자산 동향 추적)
    {"query": "윈레브에어","company": "MSD Korea",            "anchor": "MSD 자산 / PAH", "kind": "msd_asset", "logo": "MSD", "color": "#00857C"},
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS competitor_news (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash      TEXT UNIQUE NOT NULL,
    brand         TEXT NOT NULL,
    company       TEXT,
    anchor        TEXT,
    kind          TEXT,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    naver_link    TEXT,
    source_domain TEXT,
    source_name   TEXT,
    tier          INTEGER,
    description   TEXT,
    pub_date      TEXT,
    trend_id      INTEGER REFERENCES competitor_trend(id),
    collected_via TEXT,
    fetched_at    TEXT,
    expires_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_cn_brand ON competitor_news(brand);
CREATE INDEX IF NOT EXISTS idx_cn_company ON competitor_news(company);
CREATE INDEX IF NOT EXISTS idx_cn_tier ON competitor_news(tier);
CREATE INDEX IF NOT EXISTS idx_cn_pubdate ON competitor_news(pub_date);
CREATE INDEX IF NOT EXISTS idx_cn_expires ON competitor_news(expires_at);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        # 멱등 보강 (기존 테이블에 collected_via 없을 때)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(competitor_news)")}
        if "collected_via" not in cols:
            conn.execute("ALTER TABLE competitor_news ADD COLUMN collected_via TEXT")
        conn.commit()


_TIERS_CACHE: Optional[dict] = None


def _load_tiers() -> dict:
    global _TIERS_CACHE
    if _TIERS_CACHE is None:
        _TIERS_CACHE = json.loads(TIERS_PATH.read_text(encoding="utf-8"))
    return _TIERS_CACHE


def _domain(url: str) -> str:
    if not url:
        return ""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def classify_tier(url: str) -> tuple[Optional[int], Optional[str]]:
    """publisher 도메인 → (tier, 매체명). 미매핑 → (3, None)."""
    dom = _domain(url)
    if not dom:
        return None, None
    tiers = _load_tiers()
    # 정확/서픽스 매칭 (biz.chosun.com 등 서브도메인 흡수)
    for t_key, t_num in (("tier1", 1), ("tier2", 2)):
        table = tiers.get(t_key, {})
        if dom in table:
            return t_num, table[dom]
        for d, name in table.items():
            if dom.endswith("." + d) or dom == d:
                return t_num, name
    return 3, None


def _canonical_url(url: str) -> str:
    """scheme/www/쿼리 차이를 흡수해 Naver original_link ↔ 사이트 직접 URL 중복 제거.
    newsmaster articleView 는 idxno 기준으로 정규화."""
    if not url:
        return ""
    try:
        pr = urllib.parse.urlparse(url.strip())
        host = pr.netloc.lower()
        host = host[4:] if host.startswith("www.") else host
        if "articleview" in pr.path.lower():
            qs = urllib.parse.parse_qs(pr.query)
            idx = (qs.get("idxno") or [""])[0]
            if idx:
                return f"{host}/news/articleView.html?idxno={idx}"
        return f"{host}{pr.path}".rstrip("/")
    except Exception:
        return url.strip().lower()


def _url_hash(url: str) -> str:
    return hashlib.sha1(_canonical_url(url).encode("utf-8")).hexdigest()


def _is_relevant(it: NewsItem, query: str) -> bool:
    """관련성 게이트 — 브랜드명이 제목/발췌에 실제 등장해야 함.
    Naver 전수검색은 본문 스치듯 언급(예: 타사 IR 기사의 파이프라인 나열)도 잡으므로
    제목+발췌(description) 표면에 브랜드 토큰이 없으면 무관 기사로 제외."""
    surface = f"{it.title} {it.description}"
    return query in surface


def _fetch_brand(client: NaverNewsClient, query: str, lookback_days: int) -> list[NewsItem]:
    """단일 브랜드 lookback 기간 기사 수집 (Naver 최대 1,000건 캡)."""
    cutoff = datetime.now() - timedelta(days=lookback_days)
    items: list[NewsItem] = []
    for page in range(10):  # 100 × 10 = 1,000 (API start ≤ 1000)
        start = 1 + page * 100
        if start > 1000:
            break
        batch = client.search(query, display=100, start=start, sort="date")
        if not batch:
            break
        items.extend(batch)
        if batch[-1].pub_date < cutoff:
            break
    return [it for it in items if it.pub_date >= cutoff]


def _upsert_record(conn: sqlite3.Connection, meta: dict, *, title: str, url: str,
                   naver_link: Optional[str], description: str, pub_date: datetime,
                   tier: int, source_name: Optional[str], collected_via: str) -> bool:
    """정규화된 뉴스 1건 upsert. 신규 INSERT 시 True, 중복(canonical url) 이면 False."""
    uh = _url_hash(url)
    if conn.execute("SELECT 1 FROM competitor_news WHERE url_hash = ?", (uh,)).fetchone():
        return False
    now = datetime.now()
    expires = (pub_date + timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    conn.execute(
        """INSERT INTO competitor_news
           (url_hash, brand, company, anchor, kind, title, url, naver_link,
            source_domain, source_name, tier, description, pub_date, trend_id,
            collected_via, fetched_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uh, meta["query"], meta["company"], meta.get("anchor"), meta.get("kind"),
         title, url, naver_link, _domain(url), source_name, tier,
         description, pub_date.strftime("%Y-%m-%d"), None,
         collected_via, now.isoformat(timespec="seconds"), expires),
    )
    return True


def cleanup_expired() -> int:
    """expires_at < 오늘 인 행 삭제. 반환: 삭제 건수."""
    today = datetime.now().strftime("%Y-%m-%d")
    with _connect() as conn:
        cur = conn.execute("DELETE FROM competitor_news WHERE expires_at < ?", (today,))
        conn.commit()
        return cur.rowcount


def crawl(lookback_days: int = DEFAULT_LOOKBACK_DAYS, t1_only: bool = True,
          brands: Optional[list[str]] = None) -> dict:
    """전체 브랜드 수집. 기본 T1 만 저장. 반환: 브랜드별 통계 + 만료 정리."""
    ensure_schema()
    client = NaverNewsClient()
    if not client.is_configured:
        return {"error": "Naver API 키 미설정", "results": []}

    targets = COMPETITOR_BRANDS if not brands else [b for b in COMPETITOR_BRANDS if b["query"] in brands]
    results = []
    with _connect() as conn:
        for meta in targets:
            q = meta["query"]
            stored_naver = stored_site = skipped_tier = skipped_irrel = dup = 0

            # ── 축 1: Naver News API (전 매체 → T1 필터) ──
            try:
                fetched = _fetch_brand(client, q, lookback_days)
            except Exception as e:
                logger.warning("[competitor_news] %s Naver 수집 실패: %s", q, e)
                fetched = []
            for it in fetched:
                if not _is_relevant(it, q):
                    skipped_irrel += 1
                    continue
                url = it.original_link or it.link
                tier, name = classify_tier(url)
                if t1_only and tier != 1:
                    skipped_tier += 1
                    continue
                if _upsert_record(conn, meta, title=it.title, url=url, naver_link=it.link,
                                  description=it.description, pub_date=it.pub_date,
                                  tier=tier or 3, source_name=name, collected_via="naver"):
                    stored_naver += 1
                else:
                    dup += 1

            # ── 축 2: T1 전문지 직접 검색 (Naver 미인덱싱 갭필러, 예: 뉴스더보이스) ──
            try:
                site_items = _t1sites.search_all_sites(q, lookback_days)
            except Exception as e:
                logger.warning("[competitor_news] %s 사이트 수집 실패: %s", q, e)
                site_items = []
            for si in site_items:
                if q not in (si.title + " " + si.description):
                    skipped_irrel += 1
                    continue
                if _upsert_record(conn, meta, title=si.title, url=si.url, naver_link=None,
                                  description=si.description, pub_date=si.pub_date,
                                  tier=1, source_name=si.source_name, collected_via="site"):
                    stored_site += 1
                else:
                    dup += 1  # Naver 와 동일 기사 → canonical dedup

            conn.commit()
            results.append({"brand": q, "company": meta["company"],
                            "fetched_naver": len(fetched), "fetched_site": len(site_items),
                            "stored_naver": stored_naver, "stored_site": stored_site,
                            "stored": stored_naver + stored_site,
                            "skipped_tier": skipped_tier, "skipped_irrelevant": skipped_irrel,
                            "dup": dup})
    expired = cleanup_expired()
    return {"lookback_days": lookback_days, "t1_only": t1_only,
            "results": results, "expired_removed": expired,
            "total_stored": sum(r["stored"] for r in results),
            "total_via_site": sum(r["stored_site"] for r in results)}


# ── 조회 (API 용) ────────────────────────────────────────────────────────────

def list_news(brand: Optional[str] = None, company: Optional[str] = None,
              tier: Optional[int] = None, days: Optional[int] = None,
              limit: int = 100) -> list[dict]:
    ensure_schema()
    where, params = [], []
    if brand:
        where.append("brand = ?"); params.append(brand)
    if company:
        where.append("company = ?"); params.append(company)
    if tier:
        where.append("tier = ?"); params.append(tier)
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where.append("pub_date >= ?"); params.append(cutoff)
    sql = "SELECT * FROM competitor_news"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pub_date DESC, id DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def brand_registry() -> list[dict]:
    """프론트 필터용 — 추적 브랜드 + 회사 + anchor + 보유 뉴스 수."""
    ensure_schema()
    with _connect() as conn:
        counts = {r["brand"]: r["n"] for r in conn.execute(
            "SELECT brand, COUNT(*) n FROM competitor_news GROUP BY brand")}
    return [{"query": b["query"], "company": b["company"], "anchor": b.get("anchor"),
             "kind": b.get("kind"), "logo": b.get("logo"), "color": b.get("color"),
             "news_count": counts.get(b["query"], 0)} for b in COMPETITOR_BRANDS]


def stats() -> dict:
    ensure_schema()
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM competitor_news").fetchone()[0]
        by_source = [dict(r) for r in conn.execute(
            "SELECT source_name, source_domain, COUNT(*) n FROM competitor_news "
            "WHERE tier=1 GROUP BY source_domain ORDER BY n DESC")]
        date_range = conn.execute(
            "SELECT MIN(pub_date) mn, MAX(pub_date) mx FROM competitor_news").fetchone()
    return {"total": total, "by_source": by_source,
            "earliest": date_range["mn"], "latest": date_range["mx"]}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    look = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOOKBACK_DAYS
    print(json.dumps(crawl(lookback_days=look), ensure_ascii=False, indent=2))
