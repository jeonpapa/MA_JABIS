"""home_brand — Home 브랜드 언급 카드용 DEFAULT_BRANDS DB화 로더 검증.

핵심 계약: DB 가 비어있거나 접근 불가(bad path)여도 항상 상수(DEFAULT_BRANDS)로
폴백해 get_brand_traffic() 이 절대 깨지지 않는다. Naver 연관검색어로 제안된
brand 후보는 active=0 **대기 큐** 로 들어가며, 승인 시 독립 브랜드가 아니라
원본 시드의 related_terms_json 보조 검색어로 편입된다 (집계는 시드 하나로 합산).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.media_intelligence import DEFAULT_BRANDS
from agents.editable_factors import (
    add_related_candidates,
    approve_related_candidate,
    ensure_schema,
    get_home_brand_groups,
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


def test_related_candidates_stay_out_of_flat_brand_list(tmp_path: Path):
    """related 후보는 대기 큐 전용 — get_home_brands() 집계에 절대 포함되지 않는다."""
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


def test_approve_moves_candidate_into_seed_related_terms(tmp_path: Path):
    """승인 = 시드의 related_terms_json 에 편입 + 후보 행 삭제 (독립 브랜드 승격 아님)."""
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    seed_home_brands(db_path)
    add_related_candidates(
        db_path,
        [{"brand": "펨브롤리주맙", "related_from": "키트루다"}],
    )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cand_id = conn.execute(
            "SELECT id FROM home_brand WHERE brand = ?", ("펨브롤리주맙",)
        ).fetchone()["id"]
    finally:
        conn.close()

    res = approve_related_candidate(cand_id, db_path)
    assert res["seed"] == "키트루다"
    assert res["term"] == "펨브롤리주맙"
    assert "펨브롤리주맙" in res["related_terms"]

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # 후보 행 삭제됨
        assert conn.execute(
            "SELECT COUNT(*) FROM home_brand WHERE brand = ?", ("펨브롤리주맙",)
        ).fetchone()[0] == 0
        seed_row = conn.execute(
            "SELECT related_terms_json FROM home_brand WHERE brand = ?", ("키트루다",)
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(seed_row["related_terms_json"]) == ["펨브롤리주맙"]

    # flat 브랜드 목록은 그대로 (독립 브랜드가 늘지 않음)
    invalidate_cache()
    brands = get_home_brands(db_path)
    assert "펨브롤리주맙" not in brands
    assert len(brands) == len(DEFAULT_BRANDS)

    # 그룹 로더에는 시드 아래 보조 검색어로 노출
    groups = {g["brand"]: g["terms"] for g in get_home_brand_groups(db_path)}
    assert groups["키트루다"] == ["펨브롤리주맙"]


def test_approve_rejects_non_related_row_and_missing_row(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    seed_home_brands(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        seed_id = conn.execute(
            "SELECT id FROM home_brand WHERE brand = ?", (DEFAULT_BRANDS[0],)
        ).fetchone()["id"]
    finally:
        conn.close()
    with pytest.raises(ValueError):
        approve_related_candidate(seed_id, db_path)  # seed 행은 승인 대상 아님
    with pytest.raises(LookupError):
        approve_related_candidate(999999, db_path)


def test_get_home_brand_groups_returns_seeds_with_terms(tmp_path: Path):
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    groups = get_home_brand_groups(db_path)
    assert {g["brand"] for g in groups} == set(DEFAULT_BRANDS)
    assert all(g["terms"] == [] for g in groups)


def test_get_home_brand_groups_bad_path_falls_back_to_constant(tmp_path: Path):
    invalidate_cache()
    bad_path = tmp_path / "no_such_dir" / "nested" / "factors.db"
    groups = get_home_brand_groups(bad_path)
    assert {g["brand"] for g in groups} == set(DEFAULT_BRANDS)
    assert all(g["terms"] == [] for g in groups)


def test_migration_folds_promoted_related_rows_into_seed(tmp_path: Path):
    """구 방식으로 승격된 행(source='related', active=1)은 ensure_schema 마이그레이션이
    시드의 related_terms_json 으로 접어넣고 행을 삭제한다."""
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    seed_home_brands(db_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO home_brand
               (brand, therapeutic_area, source, related_from, active, created_at, updated_at)
               VALUES (?,NULL,'related',?,1,?,?)""",
            ("키트루다주", "키트루다", now, now),
        )
        # 시드가 사라진 고아 승격 행 — 데이터 보존을 위해 seed 로 전환되어야 함
        conn.execute(
            """INSERT INTO home_brand
               (brand, therapeutic_area, source, related_from, active, created_at, updated_at)
               VALUES (?,NULL,'related',?,1,?,?)""",
            ("고아브랜드", "없어진시드", now, now),
        )
        conn.commit()
    finally:
        conn.close()

    ensure_schema(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM home_brand WHERE brand = ?", ("키트루다주",)
        ).fetchone()[0] == 0
        seed_row = conn.execute(
            "SELECT related_terms_json FROM home_brand WHERE brand = ?", ("키트루다",)
        ).fetchone()
        orphan = conn.execute(
            "SELECT source, active FROM home_brand WHERE brand = ?", ("고아브랜드",)
        ).fetchone()
    finally:
        conn.close()
    assert "키트루다주" in json.loads(seed_row["related_terms_json"])
    assert orphan["source"] == "seed" and orphan["active"] == 1


def test_add_related_candidates_skips_already_approved_terms(tmp_path: Path):
    """이미 승인된 보조 검색어는 후보로 재등록되지 않는다 (무한 재제안 방지)."""
    db_path = tmp_path / "factors.db"
    invalidate_cache()
    seed_home_brands(db_path)
    add_related_candidates(db_path, [{"brand": "펨브로", "related_from": "키트루다"}])
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cand_id = conn.execute(
            "SELECT id FROM home_brand WHERE brand = ?", ("펨브로",)
        ).fetchone()["id"]
    finally:
        conn.close()
    approve_related_candidate(cand_id, db_path)

    added_again = add_related_candidates(
        db_path, [{"brand": "펨브로", "related_from": "키트루다"}]
    )
    assert added_again == 0


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


def test_aggregate_brand_traffic_group_merges_with_url_dedupe(monkeypatch):
    """그룹 입력(시드+보조 검색어)은 multi-query 후 URL dedupe 로 이중 집계를 막고
    시드 brand 하나의 entry 로 합산된다. 출력 포맷은 str 입력과 동일."""
    from datetime import datetime
    from agents import naver_news

    today = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)

    def _item(title, url):
        return naver_news.NewsItem(
            title=title, link=url, original_link=url,
            description="d", pub_date=today,
        )

    shared = _item("공통기사", "https://ex.com/a")

    class _FakeClient:
        def daily_counts(self, query, days=30, max_pages=10):
            if query == "키트루다":
                items = [shared, _item("시드기사", "https://ex.com/b")]
            elif query == "펨브롤리주맙":
                items = [shared, _item("보조기사", "https://ex.com/c")]
            else:
                items = []
            counts = {}
            for it in items:
                counts[it.date_str] = counts.get(it.date_str, 0) + 1
            return counts, items

    monkeypatch.setattr(naver_news, "get_client", lambda: _FakeClient())

    result = naver_news.aggregate_brand_traffic(
        [{"brand": "키트루다", "terms": ["펨브롤리주맙"]}], days=7,
    )
    assert len(result) == 1
    entry = result[0]
    assert entry["brand"] == "키트루다"
    # 공통기사 1건은 한 번만 집계: 총 3건 (a, b, c)
    assert entry["total_count"] == 3
    assert set(entry.keys()) == {"brand", "total_count", "daily", "sparkline", "latest_news"}

    # str 입력 하위호환
    result_str = naver_news.aggregate_brand_traffic(["키트루다"], days=7)
    assert result_str[0]["total_count"] == 2


def test_cleanup_old_cache_tolerates_both_filename_formats(tmp_path: Path, monkeypatch):
    from agents import media_intelligence

    monkeypatch.setattr(media_intelligence, "CACHE_DIR", tmp_path)
    old = "2020-01-01"
    (tmp_path / f"brand_traffic_{old}.json").write_text("{}")
    (tmp_path / f"brand_traffic_{old}_abcd1234.json").write_text("{}")
    today = datetime.now().strftime("%Y-%m-%d")
    (tmp_path / f"brand_traffic_{today}_ffff0000.json").write_text("{}")

    media_intelligence.cleanup_old_cache(keep_days=7)

    remaining = {p.name for p in tmp_path.glob("*.json")}
    assert remaining == {f"brand_traffic_{today}_ffff0000.json"}


def test_invalidate_brand_traffic_cache_removes_today_files(tmp_path: Path, monkeypatch):
    from agents import media_intelligence

    monkeypatch.setattr(media_intelligence, "CACHE_DIR", tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    (tmp_path / f"brand_traffic_{today}.json").write_text("{}")
    (tmp_path / f"brand_traffic_{today}_abcd1234.json").write_text("{}")
    (tmp_path / "brand_traffic_2020-01-01_abcd1234.json").write_text("{}")

    removed = media_intelligence.invalidate_brand_traffic_cache()
    assert removed == 2
    remaining = {p.name for p in tmp_path.glob("*.json")}
    assert remaining == {"brand_traffic_2020-01-01_abcd1234.json"}


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
