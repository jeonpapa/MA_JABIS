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

import json
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
    related_terms_json TEXT,
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
        # 기존 DB 마이그레이션 — home_brand.related_terms_json (승인된 보조 검색어 JSON 배열).
        # _SCHEMA 는 CREATE TABLE IF NOT EXISTS 라 기존 테이블에는 ALTER 가 필요하다.
        try:
            conn.execute("ALTER TABLE home_brand ADD COLUMN related_terms_json TEXT")
        except sqlite3.OperationalError:
            pass  # 이미 존재
        _fold_promoted_related_rows(conn)
        conn.commit()


def _fold_promoted_related_rows(conn: sqlite3.Connection) -> int:
    """과거 승인 방식(source='related' 행을 active=1 로 승격)으로 독립 브랜드가 된 행을
    원본 시드(related_from)의 related_terms_json 보조 검색어로 접어넣고 행을 삭제한다.

    시드 행을 찾을 수 없으면(삭제됨/related_from 없음) 데이터 보존을 위해 해당 행을
    source='seed' 독립 브랜드로 전환한다. idempotent — 대상 행이 없으면 no-op."""
    try:
        rows = conn.execute(
            "SELECT id, brand, related_from FROM home_brand "
            "WHERE source = 'related' AND active = 1"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    folded = 0
    for r in rows:
        seed = None
        if r["related_from"]:
            seed = conn.execute(
                "SELECT id, related_terms_json FROM home_brand WHERE brand = ?",
                (r["related_from"],),
            ).fetchone()
        if seed is not None:
            try:
                terms = json.loads(seed["related_terms_json"] or "[]")
            except Exception:
                terms = []
            if r["brand"] not in terms:
                terms.append(r["brand"])
            conn.execute(
                "UPDATE home_brand SET related_terms_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(terms, ensure_ascii=False), _now(), seed["id"]),
            )
            conn.execute("DELETE FROM home_brand WHERE id = ?", (r["id"],))
            folded += 1
            logger.info(
                "[editable_factors] 승격된 related 행 '%s' → 시드 '%s' 보조 검색어로 이관",
                r["brand"], r["related_from"],
            )
        else:
            conn.execute(
                "UPDATE home_brand SET source = 'seed', updated_at = ? WHERE id = ?",
                (_now(), r["id"]),
            )
            logger.warning(
                "[editable_factors] related 행 '%s' 의 시드('%s') 부재 — 독립 seed 로 전환",
                r["brand"], r["related_from"],
            )
    return folded


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

        # ── S4 소스 확장 업그레이드 시드 (국회·환자단체·의료진/학회) ──────────
        # 위 블록은 테이블이 비어있을 때만 동작하므로, 이미 seed 된 기존 DB 에는
        # 신규 agency 의 gov_seed 가 영원히 추가되지 않는다. S4 agency 태그별로
        # "해당 agency 행이 0개일 때만" 상수에서 시드해 기존 DB 를 업그레이드한다.
        # (agency 행이 1개라도 있으면 no-op — admin 이 개별 term 을 삭제/수정해도
        #  되살리지 않는다. agency 전체 비활성화는 active=0 PATCH 로.)
        seeded["news_keyword_factor_s4"] = _ensure_s4_gov_seeds(conn, now)
        conn.commit()
    return seeded


def _ensure_s4_gov_seeds(conn: sqlite3.Connection, now: str) -> int:
    """S4 확장 agency(gov_seed) 를 기존 DB 에 idempotent 하게 추가. 반환: 추가 행 수."""
    from agents.gov_policy_news import GOV_AGENCIES, S4_AGENCY_TAGS

    added = 0
    for ag in GOV_AGENCIES:
        agency = ag["agency"]
        if agency not in S4_AGENCY_TAGS:
            continue
        cnt = conn.execute(
            "SELECT COUNT(*) FROM news_keyword_factor "
            "WHERE scope = 'gov' AND kind = 'gov_seed' AND agency = ?",
            (agency,),
        ).fetchone()[0]
        if cnt:
            continue
        for q in ag["queries"]:
            conn.execute(
                """INSERT INTO news_keyword_factor
                   (scope, kind, agency, term, active, created_at, updated_at)
                   VALUES ('gov','gov_seed',?,?,1,?,?)""",
                (agency, q, now, now),
            )
            added += 1
        logger.info("[editable_factors] S4 gov_seed 업그레이드: %s (%d terms)",
                    agency, len(ag["queries"]))
    return added


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


def get_home_brand_groups(db_path: Optional[Path] = None) -> list:
    """활성 시드 브랜드 + 승인된 보조 검색어 그룹 목록.

    반환: [{"brand": "키트루다", "terms": ["펨브롤리주맙", ...]}, ...]
    보조 검색어(terms)는 해당 시드의 네이버 검색을 넓히는 하위 질의로, 집계는
    시드 brand 하나로 합산된다(독립 브랜드 아님). 실패 시 DEFAULT_BRANDS 폴백
    (terms 는 빈 배열)."""
    key = _cache_key("home_brand_groups", db_path)
    cached = _get_cached(key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        seed_home_brands(db_path)
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT brand, related_terms_json FROM home_brand "
                "WHERE active = 1 ORDER BY id"
            ).fetchall()
        items = []
        for r in rows:
            try:
                terms = json.loads(r["related_terms_json"] or "[]")
            except Exception:
                terms = []
            items.append({
                "brand": r["brand"],
                "terms": [t for t in terms if isinstance(t, str) and t.strip()],
            })
        if not items:
            raise ValueError("home_brand empty after seed")
    except Exception as e:
        logger.warning("[editable_factors] get_home_brand_groups DB 조회 실패, 상수 폴백: %s", e)
        from agents.media_intelligence import DEFAULT_BRANDS

        items = [{"brand": b, "terms": []} for b in DEFAULT_BRANDS]

    _set_cached(key, items)
    return items


def add_related_candidates(db_path: Optional[Path], candidates: list) -> int:
    """Naver 연관검색어 확장 후보 등록 — source='related', active=0 **대기 큐 전용**
    (절대 active=1 로 승격하지 않음 — 승인은 approve_related_candidate() 가 시드의
    related_terms_json 에 보조 검색어로 편입). candidates = [{"brand": ..., "related_from": ...}].
    이미 존재하는 brand(UNIQUE) 또는 이미 승인된 보조 검색어는 건너뛴다.
    반환값: 실제 추가된 행 수."""
    ensure_schema(db_path)
    now = _now()
    added = 0
    with _connect(db_path) as conn:
        # 이미 승인된 보조 검색어 전체 (재제안 방지)
        approved: set = set()
        for r in conn.execute(
            "SELECT related_terms_json FROM home_brand WHERE related_terms_json IS NOT NULL"
        ).fetchall():
            try:
                approved.update(t.lower() for t in json.loads(r["related_terms_json"] or "[]"))
            except Exception:
                continue
        for c in candidates:
            brand = (c.get("brand") or "").strip()
            if not brand or brand.lower() in approved:
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


def approve_related_candidate(candidate_id: int, db_path: Optional[Path] = None) -> dict:
    """related 후보 승인 — 후보의 brand 를 원본 시드(related_from)의
    related_terms_json 에 보조 검색어로 추가하고 후보 행을 삭제한다.

    반환: {"seed": 시드브랜드, "term": 승인된 검색어, "related_terms": [...전체]}
    예외: LookupError(후보 없음) / ValueError(related 후보 아님 · 시드 부재)."""
    ensure_schema(db_path)
    with _connect(db_path) as conn:
        cand = conn.execute(
            "SELECT id, brand, source, related_from FROM home_brand WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if cand is None:
            raise LookupError(f"home_brand id={candidate_id} not found")
        if cand["source"] != "related":
            raise ValueError("related 후보 행이 아님 — 승인 대상은 source='related' 만")
        seed_brand = cand["related_from"]
        seed = None
        if seed_brand:
            seed = conn.execute(
                "SELECT id, brand, related_terms_json FROM home_brand "
                "WHERE brand = ? AND source != 'related'",
                (seed_brand,),
            ).fetchone()
        if seed is None:
            raise ValueError(f"원본 시드 브랜드('{seed_brand}') 를 찾을 수 없음")
        try:
            terms = json.loads(seed["related_terms_json"] or "[]")
        except Exception:
            terms = []
        if cand["brand"] not in terms:
            terms.append(cand["brand"])
        now = _now()
        conn.execute(
            "UPDATE home_brand SET related_terms_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(terms, ensure_ascii=False), now, seed["id"]),
        )
        conn.execute("DELETE FROM home_brand WHERE id = ?", (candidate_id,))
        conn.commit()
    invalidate_cache()
    return {"seed": seed["brand"], "term": cand["brand"], "related_terms": terms}
