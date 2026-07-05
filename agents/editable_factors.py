"""Editable competitor 브랜드 / 뉴스 키워드 팩터 — DB 로더 + 상수 폴백.

기존에 하드코딩되어 있던
  - `agents.competitor_news_agent.COMPETITOR_BRANDS`
  - `agents.gov_policy_news.GOV_AGENCIES` / `_CONTEXT_ANCHORS`
를 admin CRUD 로 편집 가능하게 하되, **DB 가 비어있거나 접근 실패해도 항상 원본
상수로 폴백**해 기존 크롤이 절대 깨지지 않도록 하는 로더 계층.

패턴: seed-if-empty → SELECT active rows → (모든 예외) 상수 폴백.
in-process 캐시(TTL) 로 크롤 1회 실행 중 반복 DB 조회를 피하고,
admin CRUD 쓰기 이후에는 `invalidate_cache()` 로 즉시 반영한다.

상수 모듈(agents.competitor_news_agent / agents.gov_policy_news)을 이 모듈 상단에서
import 하면 순환참조가 생기므로(두 모듈이 이 모듈을 가져다 쓰기 때문), 상수는
함수 내부에서 지연 import 한다.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

# 인-프로세스 캐시 TTL (초) — admin 쓰기 후에는 invalidate_cache() 로 즉시 무효화
_CACHE_TTL_SECONDS = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS competitor_brand (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL UNIQUE,
    company TEXT NOT NULL,
    anchor TEXT,
    kind TEXT NOT NULL DEFAULT 'competitor',
    logo TEXT,
    color TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS news_keyword_factor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    agency TEXT,
    term TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_factor_scope ON news_keyword_factor(scope, kind);
CREATE TABLE IF NOT EXISTS home_brand (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT NOT NULL UNIQUE,
    therapeutic_area TEXT,
    source TEXT NOT NULL DEFAULT 'seed',
    related_from TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_home_brand_active ON home_brand(active);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_path(db_path: Optional[Path]) -> Path:
    return Path(db_path) if db_path else DEFAULT_DB_PATH


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(_resolve_path(db_path)))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(db_path: Optional[Path] = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


# ── in-process 캐시 ──────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, object]] = {}


def _cache_key(prefix: str, db_path: Optional[Path]) -> str:
    return f"{prefix}:{_resolve_path(db_path)}"


def invalidate_cache() -> None:
    """admin CRUD 쓰기 직후 호출 — 다음 조회부터 DB 최신 값을 반영."""
    _cache.clear()


def _get_cached(key: str):
    entry = _cache.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        return None
    return value


def _set_cached(key: str, value) -> None:
    _cache[key] = (time.time(), value)


# ── seed (DB 비어있을 때만, 최초 1회) ─────────────────────────────────────────

def seed_editable_factors(db_path: Optional[Path] = None) -> dict:
    """competitor_brand / news_keyword_factor 가 비어있으면 상수에서 최초 1회 seed.
    이미 행이 있으면 아무것도 하지 않는다 (idempotent)."""
    ensure_schema(db_path)
    now = _now()
    seeded = {"competitor_brand": 0, "news_keyword_factor": 0}
    with _connect(db_path) as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM competitor_brand").fetchone()[0]
        if cnt == 0:
            from agents.competitor_news_agent import COMPETITOR_BRANDS

            for b in COMPETITOR_BRANDS:
                conn.execute(
                    """INSERT OR IGNORE INTO competitor_brand
                       (query, company, anchor, kind, logo, color, active, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,1,?,?)""",
                    (
                        b["query"], b["company"], b.get("anchor"),
                        b.get("kind", "competitor"), b.get("logo"), b.get("color"),
                        now, now,
                    ),
                )
                seeded["competitor_brand"] += 1

        cnt2 = conn.execute("SELECT COUNT(*) FROM news_keyword_factor").fetchone()[0]
        if cnt2 == 0:
            from agents.gov_policy_news import GOV_AGENCIES, _CONTEXT_ANCHORS

            for ag in GOV_AGENCIES:
                agency = ag["agency"]
                for q in ag["queries"]:
                    conn.execute(
                        """INSERT INTO news_keyword_factor
                           (scope, kind, agency, term, active, created_at, updated_at)
                           VALUES ('gov','gov_seed',?,?,1,?,?)""",
                        (agency, q, now, now),
                    )
                    seeded["news_keyword_factor"] += 1
            for term in _CONTEXT_ANCHORS:
                conn.execute(
                    """INSERT INTO news_keyword_factor
                       (scope, kind, agency, term, active, created_at, updated_at)
                       VALUES ('gov','context_anchor',NULL,?,1,?,?)""",
                    (term, now, now),
                )
                seeded["news_keyword_factor"] += 1
        conn.commit()
    return seeded


# ── 로더 (seed-if-empty → SELECT → 예외 시 상수 폴백) ─────────────────────────

def get_competitor_brands(db_path: Optional[Path] = None) -> list[dict]:
    """활성 competitor_brand 행 목록. 실패(DB 없음/손상 등) 시 COMPETITOR_BRANDS 상수 폴백."""
    key = _cache_key("competitor_brands", db_path)
    cached = _get_cached(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        seed_editable_factors(db_path)
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT query, company, anchor, kind, logo, color FROM competitor_brand "
                "WHERE active = 1 ORDER BY id"
            ).fetchall()
        items = [dict(r) for r in rows]
        if not items:
            raise ValueError("competitor_brand empty after seed")
    except Exception as e:
        logger.warning("[editable_factors] get_competitor_brands DB 조회 실패, 상수 폴백: %s", e)
        from agents.competitor_news_agent import COMPETITOR_BRANDS

        items = [dict(b) for b in COMPETITOR_BRANDS]

    _set_cached(key, items)
    return items


def get_gov_agencies(db_path: Optional[Path] = None) -> list[dict]:
    """news_keyword_factor(scope='gov', kind='gov_seed') 에서 기관별 queries 재구성.
    실패 시 GOV_AGENCIES 상수 폴백."""
    key = _cache_key("gov_agencies", db_path)
    cached = _get_cached(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        seed_editable_factors(db_path)
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT agency, term FROM news_keyword_factor "
                "WHERE scope = 'gov' AND kind = 'gov_seed' AND active = 1 ORDER BY id"
            ).fetchall()
        grouped: dict[str, list[str]] = {}
        order: list[str] = []
        for r in rows:
            agency = r["agency"]
            if agency not in grouped:
                grouped[agency] = []
                order.append(agency)
            grouped[agency].append(r["term"])
        items = [{"agency": a, "queries": grouped[a]} for a in order]
        if not items:
            raise ValueError("news_keyword_factor(gov_seed) empty after seed")
    except Exception as e:
        logger.warning("[editable_factors] get_gov_agencies DB 조회 실패, 상수 폴백: %s", e)
        from agents.gov_policy_news import GOV_AGENCIES

        items = [dict(a) for a in GOV_AGENCIES]

    _set_cached(key, items)
    return items


def get_context_anchors(db_path: Optional[Path] = None) -> tuple:
    """news_keyword_factor(scope='gov', kind='context_anchor') term 목록.
    실패 시 _CONTEXT_ANCHORS 상수 폴백."""
    key = _cache_key("context_anchors", db_path)
    cached = _get_cached(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        seed_editable_factors(db_path)
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT term FROM news_keyword_factor "
                "WHERE scope = 'gov' AND kind = 'context_anchor' AND active = 1 ORDER BY id"
            ).fetchall()
        items: tuple = tuple(r["term"] for r in rows)
        if not items:
            raise ValueError("news_keyword_factor(context_anchor) empty after seed")
    except Exception as e:
        logger.warning("[editable_factors] get_context_anchors DB 조회 실패, 상수 폴백: %s", e)
        from agents.gov_policy_news import _CONTEXT_ANCHORS

        items = _CONTEXT_ANCHORS

    _set_cached(key, items)
    return items


# ── Home 브랜드 (home_brand) — DEFAULT_BRANDS DB화 + Naver 연관검색어 확장 후보 ──────

def seed_home_brands(db_path: Optional[Path] = None) -> int:
    """home_brand 가 비어있으면 agents.media_intelligence.DEFAULT_BRANDS 에서 최초 1회 seed.
    이미 행이 있으면 아무것도 하지 않는다 (idempotent)."""
    ensure_schema(db_path)
    now = _now()
    seeded = 0
    with _connect(db_path) as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM home_brand").fetchone()[0]
        if cnt == 0:
            from agents.media_intelligence import DEFAULT_BRANDS

            for brand in DEFAULT_BRANDS:
                conn.execute(
                    """INSERT OR IGNORE INTO home_brand
                       (brand, therapeutic_area, source, related_from, active, created_at, updated_at)
                       VALUES (?,NULL,'seed',NULL,1,?,?)""",
                    (brand, now, now),
                )
                seeded += 1
        conn.commit()
    return seeded


def get_home_brands(db_path: Optional[Path] = None) -> list:
    """활성 home_brand.brand 문자열 목록. 실패(DB 없음/손상/빈 결과 등) 시
    agents.media_intelligence.DEFAULT_BRANDS 상수로 폴백."""
    key = _cache_key("home_brands", db_path)
    cached = _get_cached(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        seed_home_brands(db_path)
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT brand FROM home_brand WHERE active = 1 ORDER BY id"
            ).fetchall()
        items = [r["brand"] for r in rows]
        if not items:
            raise ValueError("home_brand empty after seed")
    except Exception as e:
        logger.warning("[editable_factors] get_home_brands DB 조회 실패, 상수 폴백: %s", e)
        from agents.media_intelligence import DEFAULT_BRANDS

        items = list(DEFAULT_BRANDS)

    _set_cached(key, items)
    return items


def add_related_candidates(db_path: Optional[Path], candidates: list) -> int:
    """Naver 연관검색어 확장 후보 등록 — source='related', active=0 (admin 승인 전까지
    get_home_brands() 집계에서 제외). candidates = [{"brand": ..., "related_from": ...}].
    이미 존재하는 brand(UNIQUE) 는 INSERT OR IGNORE 로 건너뛴다. 반환값: 실제 추가된 행 수."""
    ensure_schema(db_path)
    now = _now()
    added = 0
    with _connect(db_path) as conn:
        for c in candidates:
            brand = (c.get("brand") or "").strip()
            if not brand:
                continue
            cur = conn.execute(
                """INSERT OR IGNORE INTO home_brand
                   (brand, therapeutic_area, source, related_from, active, created_at, updated_at)
                   VALUES (?,NULL,'related',?,0,?,?)""",
                (brand, c.get("related_from"), now, now),
            )
            if cur.rowcount:
                added += 1
        conn.commit()
    return added
