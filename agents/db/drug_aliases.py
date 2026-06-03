"""Brand ↔ molecule alias map for foreign price canonicalization.

국내·해외 검색에서 welireg/belzutifan, keytruda/pembrolizumab 등이
동일 레코드로 집계되도록 canonical key (molecule 기준) 를 제공한다.

2-tier 우선순위:
  1) DB `product_alias_map` (실시간 갱신, 시드 스크립트 + admin UI)
  2) BRAND_TO_MOLECULE in-memory (코드 fallback)

신규 약제는 시드 스크립트(`scripts/seed_product_alias_map.py`) 에 추가하거나,
긴급한 경우 BRAND_TO_MOLECULE 한 줄만 추가하면 된다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

# 브랜드(소문자) → molecule(소문자). 새 제품 추가 시 여기만 확장.
BRAND_TO_MOLECULE: dict[str, str] = {
    "keytruda": "pembrolizumab",
    "welireg": "belzutifan",
    "opdivo": "nivolumab",
    "obdivo": "nivolumab",   # 오타/구표기 포용
    "lynparza": "olaparib",
    "lenvima": "lenvatinib",
    "januvia": "sitagliptin",
    "atozet": "ezetimibe_rosuvastatin",
    "repatha": "evolocumab",
    "aflibercept": "aflibercept",
    "gardasil": "human_papillomavirus_vaccine",
    "prevymis": "letermovir",
}


# ── DB-backed alias cache (product_alias_map) ─────────────────────────────
_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"
_db_cache: dict[str, dict] | None = None  # {alias_lower: {slug, inn, brand_aliases}}
_db_cache_lock = threading.Lock()


def _load_db_cache() -> dict[str, dict]:
    """product_alias_map 전체를 lower(name)→entry 단일 dict 로 빌드.
    한 번 로드 후 메모리 캐시. invalidate 는 process restart 또는 명시적 호출.
    """
    global _db_cache
    if _db_cache is not None:
        return _db_cache
    with _db_cache_lock:
        if _db_cache is not None:
            return _db_cache
        cache: dict[str, dict] = {}
        if _DB_PATH.exists():
            try:
                with sqlite3.connect(str(_DB_PATH)) as conn:
                    rows = conn.execute(
                        "SELECT product_slug, inn, brand_aliases_json FROM product_alias_map"
                    ).fetchall()
                for slug, inn, ba_json in rows:
                    try:
                        ba = json.loads(ba_json or "[]")
                    except Exception:
                        ba = []
                    entry = {"slug": slug, "inn": inn, "brand_aliases": ba}
                    keys = {slug.lower()}
                    if inn:
                        keys.add(inn.lower())
                    for x in ba:
                        if x:
                            keys.add(str(x).lower())
                    for k in keys:
                        cache[k] = entry
            except sqlite3.OperationalError:
                # 테이블 없음 — 정상 (Phase 1 미적용 시)
                pass
        _db_cache = cache
        return cache


def invalidate_db_cache() -> None:
    """seed/upsert 후 명시적 무효화."""
    global _db_cache
    with _db_cache_lock:
        _db_cache = None


def canonical(name: str) -> str:
    """입력을 canonical molecule/slug key 로 변환.
    - DB product_alias_map 매칭 시 product_slug 또는 inn 반환 (slug 우선)
    - 미매칭 시 BRAND_TO_MOLECULE → fallback 소문자/strip
    """
    if not name:
        return ""
    key = name.strip().lower()
    db = _load_db_cache()
    entry = db.get(key)
    if entry:
        # canonical = inn (있으면) 아니면 slug
        return entry.get("inn") or entry["slug"]
    return BRAND_TO_MOLECULE.get(key, key)


def aliases(name: str) -> list[str]:
    """canonical 과 동일 canonical 을 가진 모든 이름(브랜드 + molecule) 반환.
    DB-backed entry 우선, in-memory dict 보충.
    """
    if not name:
        return []
    key = name.strip().lower()
    db = _load_db_cache()
    entry = db.get(key)
    out: set[str] = set()
    if entry:
        out.add(entry["slug"].lower())
        if entry.get("inn"):
            out.add(entry["inn"].lower())
        for x in entry.get("brand_aliases") or []:
            if x:
                out.add(str(x).lower())
        return sorted(out)
    # in-memory fallback
    canon = BRAND_TO_MOLECULE.get(key, key)
    out.add(canon)
    for brand, mol in BRAND_TO_MOLECULE.items():
        if mol == canon:
            out.add(brand)
    return sorted(out)


def display_name(canon_or_name: str) -> str:
    """canonical molecule key → 표시용 이름. brand 가 있으면 brand 를 우선.
    DB entry 가 있으면 product_slug 우선.
    """
    if not canon_or_name:
        return ""
    key = canon_or_name.strip().lower()
    db = _load_db_cache()
    entry = db.get(key)
    if entry:
        return entry["slug"]
    canon = BRAND_TO_MOLECULE.get(key, key)
    for brand, mol in BRAND_TO_MOLECULE.items():
        if mol == canon:
            return brand
    return canon
