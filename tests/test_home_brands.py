"""home_brand — Home 브랜드 언급 카드용 DEFAULT_BRANDS DB화 로더 검증.

핵심 계약: DB 가 비어있거나 접근 불가(bad path)여도 항상 상수(DEFAULT_BRANDS)로
폴백해 get_brand_traffic() 이 절대 깨지지 않는다. Naver 연관검색어로 제안된
brand 후보는 active=0(비활성)로 들어가 admin 승인 전까지는 집계에 포함되지 않는다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agents.media_intelligence import DEFAULT_BRANDS
from agents.editable_factors import (
    add_related_candidates,
    get_home_brands,
    invalidate_cache,
    seed_home_brands,
)


def test_seed_populates_home_brand_from_constant(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    seed_home_brands(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM home_brand").fetchone()[0]
    finally:
        conn.close()
    assert count == len(DEFAULT_BRANDS)


def test_seed_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    seed_home_brands(db_path)
    seed_home_brands(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM home_brand").fetchone()[0]
    finally:
        conn.close()
    assert count == len(DEFAULT_BRANDS)


def test_get_home_brands_returns_seeded_brand_strings(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    brands = get_home_brands(db_path)
    assert len(brands) == len(DEFAULT_BRANDS)
    assert set(brands) == set(DEFAULT_BRANDS)
    assert all(isinstance(b, str) for b in brands)


def test_get_home_brands_bad_path_falls_back_to_constant(tmp_path: Path):
    invalidate_cache()
    bad_path = tmp_path / "no_such_dir" / "nested" / "factors.db"
    brands = get_home_brands(bad_path)
    assert len(brands) == len(DEFAULT_BRANDS)
    assert set(brands) == set(DEFAULT_BRANDS)


def test_add_related_candidates_inserted_inactive(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    seed_home_brands(db_path)
    added = add_related_candidates(
        db_path,
        [{"brand": "신약후보A", "related_from": "키트루다"}],
    )
    assert added == 1
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM home_brand WHERE brand = ?", ("신약후보A",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["source"] == "related"
    assert row["active"] == 0
    assert row["related_from"] == "키트루다"


def test_get_home_brands_excludes_inactive_candidates_until_activated(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    seed_home_brands(db_path)
    add_related_candidates(
        db_path,
        [{"brand": "신약후보B", "related_from": "렌비마"}],
    )
    invalidate_cache()
    brands = get_home_brands(db_path)
    assert "신약후보B" not in brands
    assert len(brands) == len(DEFAULT_BRANDS)

    # admin 승인 시뮬레이션 — active=1 로 PATCH
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE home_brand SET active = 1, updated_at = ? WHERE brand = ?",
            (now, "신약후보B"),
        )
        conn.commit()
    finally:
        conn.close()

    invalidate_cache()
    brands_after = get_home_brands(db_path)
    assert "신약후보B" in brands_after
    assert len(brands_after) == len(DEFAULT_BRANDS) + 1


def test_add_related_candidates_ignores_duplicate_brand(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    seed_home_brands(db_path)
    # 이미 seed 로 들어간 브랜드는 INSERT OR IGNORE 로 무시
    added = add_related_candidates(
        db_path,
        [{"brand": DEFAULT_BRANDS[0], "related_from": "테스트"}],
    )
    assert added == 0


def test_invalidate_cache_picks_up_new_home_brand(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    initial = get_home_brands(db_path)
    assert len(initial) == len(DEFAULT_BRANDS)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO home_brand
               (brand, therapeutic_area, source, related_from, active, created_at, updated_at)
               VALUES (?,?,?,?,1,?,?)""",
            ("테스트브랜드", None, "seed", None, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    still_cached = get_home_brands(db_path)
    assert len(still_cached) == len(DEFAULT_BRANDS)

    invalidate_cache()
    updated = get_home_brands(db_path)
    assert len(updated) == len(DEFAULT_BRANDS) + 1
    assert "테스트브랜드" in updated


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_related_keywords_parses_autocomplete_json(monkeypatch):
    from agents import naver_related

    payload = (
        '{"query":"키트루다","items":[[["키트루다 급여"],["키트루다 부작용"],'
        '["키트루다 가격"]]]}'
    ).encode("utf-8")

    def fake_urlopen(req, timeout=10):
        return _FakeResponse(payload)

    monkeypatch.setattr(naver_related, "_urlopen", fake_urlopen)
    result = naver_related.related_keywords("키트루다", limit=10)
    assert "키트루다 급여" in result
    assert "키트루다 부작용" in result


def test_related_keywords_returns_empty_on_network_error(monkeypatch):
    from agents import naver_related

    def fake_urlopen(req, timeout=10):
        raise OSError("network down")

    monkeypatch.setattr(naver_related, "_urlopen", fake_urlopen)
    result = naver_related.related_keywords("키트루다")
    assert result == []
