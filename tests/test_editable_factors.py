"""editable_factors — competitor 브랜드 / 뉴스 키워드 팩터 DB화 로더 검증.

핵심 계약: DB 가 비어있거나 접근 불가(bad path)여도 항상 상수(COMPETITOR_BRANDS /
GOV_AGENCIES / _CONTEXT_ANCHORS) 로 폴백해 기존 크롤이 절대 깨지지 않는다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agents.competitor_news_agent import COMPETITOR_BRANDS
from agents.gov_policy_news import GOV_AGENCIES, _CONTEXT_ANCHORS
from agents.editable_factors import (
    get_competitor_brands,
    get_context_anchors,
    get_gov_agencies,
    invalidate_cache,
    seed_editable_factors,
)


def test_seed_populates_competitor_brand_from_constant(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    seed_editable_factors(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM competitor_brand").fetchone()[0]
    finally:
        conn.close()
    assert count == len(COMPETITOR_BRANDS)


def test_get_competitor_brands_returns_seeded_rows(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    brands = get_competitor_brands(db_path)
    assert len(brands) == len(COMPETITOR_BRANDS)
    assert {b["query"] for b in brands} == {b["query"] for b in COMPETITOR_BRANDS}
    for b in brands:
        assert set(b.keys()) >= {"query", "company", "anchor", "kind", "logo", "color"}


def test_get_competitor_brands_bad_path_falls_back_to_constant(tmp_path: Path):
    invalidate_cache()
    bad_path = tmp_path / "no_such_dir" / "nested" / "factors.db"
    brands = get_competitor_brands(bad_path)
    assert len(brands) == len(COMPETITOR_BRANDS)
    assert {b["query"] for b in brands} == {b["query"] for b in COMPETITOR_BRANDS}


def test_get_gov_agencies_reconstructs_with_queries(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    agencies = get_gov_agencies(db_path)
    assert len(agencies) == len(GOV_AGENCIES)
    by_name = {a["agency"]: a["queries"] for a in agencies}
    for ag in GOV_AGENCIES:
        assert by_name[ag["agency"]] == ag["queries"]


def test_get_gov_agencies_bad_path_falls_back_to_constant(tmp_path: Path):
    invalidate_cache()
    bad_path = tmp_path / "missing_dir" / "factors.db"
    agencies = get_gov_agencies(bad_path)
    assert len(agencies) == len(GOV_AGENCIES)
    assert {a["agency"] for a in agencies} == {a["agency"] for a in GOV_AGENCIES}


def test_get_context_anchors_returns_terms(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    anchors = get_context_anchors(db_path)
    assert set(anchors) == set(_CONTEXT_ANCHORS)


def test_get_context_anchors_bad_path_falls_back_to_constant(tmp_path: Path):
    invalidate_cache()
    bad_path = tmp_path / "nope" / "factors.db"
    anchors = get_context_anchors(bad_path)
    assert set(anchors) == set(_CONTEXT_ANCHORS)


def test_invalidate_cache_picks_up_new_competitor_brand(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    initial = get_competitor_brands(db_path)
    assert len(initial) == len(COMPETITOR_BRANDS)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO competitor_brand
               (query, company, anchor, kind, logo, color, active, created_at, updated_at)
               VALUES (?,?,?,?,?,?,1,?,?)""",
            ("테스트브랜드", "Test Co", "test-anchor", "competitor", "TB", "#000000", now, now),
        )
        conn.commit()
    finally:
        conn.close()

    # TTL 캐시가 살아있는 동안은 이전 값 반환 (아직 invalidate 안 함)
    still_cached = get_competitor_brands(db_path)
    assert len(still_cached) == len(COMPETITOR_BRANDS)

    invalidate_cache()
    updated = get_competitor_brands(db_path)
    assert len(updated) == len(COMPETITOR_BRANDS) + 1
    assert "테스트브랜드" in {b["query"] for b in updated}
