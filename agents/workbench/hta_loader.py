"""HTA Loader — Tier-3 교차검증 캐시 → 워크벤치 payload

`scripts/tier3_hta_multi_agency.py` 가 생성한 JSON 캐시를 읽어
workbench 세션의 `hta` 필드 그대로 주입 가능한 형태로 반환.

Phase 2 에서는 즉시 조회 (캐시). Phase 3 에서는 재조회 스케줄링.
"""

from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = BASE_DIR / "data" / "design_panel"

# 제품 slug → 캐시 파일 매핑. 신규 제품 추가 시 여기에 등록.
PRODUCT_CACHE: dict[str, str] = {
    "keytruda":      "tier3_multi_hta_keytruda.json",
    "pembrolizumab": "tier3_multi_hta_keytruda.json",
}


def _slugify(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "_")


def list_available_products() -> list[str]:
    """캐시가 존재하는 제품 slug 목록."""
    return sorted(
        {slug for slug, fname in PRODUCT_CACHE.items() if (CACHE_DIR / fname).exists()}
    )


def load_hta_for_product(product_name: str) -> dict | None:
    """
    제품명 (EN 또는 KR slug) 으로 HTA 교차검증 결과 로드.

    Returns:
      - dict: 4-agency cross-validation payload (nice/pbac/has/gba)
      - None: 캐시 없음
    """
    if not product_name:
        return None

    slug = _slugify(product_name)
    fname = PRODUCT_CACHE.get(slug)
    if not fname:
        # 느슨한 매칭 시도 (키트루다 등)
        for key, cache_file in PRODUCT_CACHE.items():
            if key in slug or slug in key:
                fname = cache_file
                break

    if not fname:
        return None

    path = CACHE_DIR / fname
    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def summarize_hta(hta_data: dict | None) -> dict:
    """UI 미리보기용 초간단 요약."""
    if not hta_data:
        return {"agencies": [], "total_fields": 0, "agree": 0, "conflict": 0}

    agencies = []
    total_agree = total_conflict = total_fields = 0
    for code, body in hta_data.items():
        s = body.get("summary", {}) or {}
        agency_name = (body.get("agency", {}) or {}).get("name", code.upper())
        country = (body.get("agency", {}) or {}).get("country", "")
        agencies.append({
            "code":      code,
            "name":      agency_name,
            "country":   country,
            "agree":     s.get("agree_count", 0),
            "conflict":  s.get("conflict_count", 0),
            "single":    s.get("single_source", 0),
            "narrative": s.get("narrative_count", 0),
            "missing":   s.get("missing_count", 0),
        })
        total_agree += s.get("agree_count", 0)
        total_conflict += s.get("conflict_count", 0)
        total_fields += s.get("total_fields", 0)

    return {
        "agencies":     agencies,
        "total_fields": total_fields,
        "agree":        total_agree,
        "conflict":     total_conflict,
    }
