"""뉴스 텍스트 ↔ 약제(drug_id) 매핑 — Access Insight S1.

`amjilsim_drugs` (brand_kr/brand_en/ingredient_inn) 와 `product_alias_map`
(brand_aliases_json) 를 합쳐 alias(소문자) → drug_id 인덱스를 빌드하고,
자유 텍스트(뉴스 제목·발췌) 안에서 alias 가 substring 으로 등장하면 해당
drug_id 로 역추론한다.

READ-ONLY: 이 모듈은 DB 에 쓰지 않는다 (조회 전용).
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Union

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BASE_DIR / "data" / "db" / "drug_prices.db"

_MIN_ALIAS_LEN = 2

# 제형 접미 — 브랜드명에 공백 없이 바로 붙는 경우가 많다 (예: '파드셉주', '웰리렉정').
# 길이가 긴 것부터 검사해 부분 접미 오매칭을 방지.
_FORM_SUFFIXES = (
    "주사액", "필름코팅정", "장용정", "서방정", "건조시럽",
    "캡슐", "시럽", "과립", "액", "정", "주",
)

# 문자열 말미의 함량/단위 노이즈 제거 (예: '파드셉주 20·30mg' -> '파드셉주').
_DIGIT_UNIT_SUFFIX_RE = re.compile(
    r"[\s]*[0-9][0-9.·,/~\-]*\s*(mg|mcg|g|ml|iu|호)?\s*$",
    re.IGNORECASE,
)

_index_cache: dict[str, dict[str, int]] = {}
_index_lock = threading.Lock()

PathLike = Union[str, Path]


def invalidate_index_cache() -> None:
    """테스트/재시딩 후 캐시 무효화."""
    with _index_lock:
        _index_cache.clear()


def _clean_brand_candidates(raw: str) -> list[str]:
    """brand_kr 원본 문자열에서 substring-matching 용 clean alias 후보를 뽑는다.

    - '외 N품목' 다품목 나열은 통째로 skip (예: '베오바정 50mg 외 1품목').
    - '+' 로 이어진 병용 표기는 성분별로 분리 (예: '옵디보 + 여보이' -> ['옵디보','여보이']).
    - 말미 함량/단위 숫자 노이즈 제거 (예: '파드셉주 20·30mg' -> '파드셉주').
    - 제형 접미(정/주/캡슐 등) 제거한 짧은 변형도 함께 후보에 추가 (예: '파드셉주' -> '파드셉').
    """
    if not raw:
        return []
    if "외" in raw:
        return []

    out: list[str] = []
    for part in raw.split("+"):
        part = part.strip()
        if not part:
            continue
        part = _DIGIT_UNIT_SUFFIX_RE.sub("", part).strip()
        if not part or re.search(r"[0-9]", part):
            continue
        if len(part) >= _MIN_ALIAS_LEN:
            out.append(part)
        for suf in _FORM_SUFFIXES:
            if part.endswith(suf) and len(part) - len(suf) >= _MIN_ALIAS_LEN:
                out.append(part[: -len(suf)])
                break
    return out


def _load_alias_map_bridges(conn: sqlite3.Connection) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """product_alias_map 을 (slug→aliases, inn→aliases) 두 lookup 으로 반환."""
    try:
        rows = conn.execute(
            "SELECT product_slug, inn, brand_aliases_json FROM product_alias_map"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}, {}

    by_slug: dict[str, list[str]] = {}
    by_inn: dict[str, list[str]] = {}
    for slug, inn, ba_json in rows:
        try:
            aliases = [a for a in (json.loads(ba_json or "[]") or []) if a]
        except Exception:
            aliases = []
        slug_key = (slug or "").strip().lower()
        inn_key = (inn or "").strip().lower()
        if slug_key:
            by_slug[slug_key] = aliases
        if inn_key:
            by_inn[inn_key] = aliases
    return by_slug, by_inn


def build_alias_index(db_path: Optional[PathLike] = None) -> dict[str, int]:
    """alias(소문자) → drug_id 인덱스를 빌드 (in-process 캐시)."""
    path = str(db_path or DEFAULT_DB_PATH)
    with _index_lock:
        cached = _index_cache.get(path)
    if cached is not None:
        return cached

    conn = sqlite3.connect(path)
    try:
        drugs = conn.execute(
            "SELECT drug_id, product_slug, brand_kr, brand_en, ingredient_inn FROM amjilsim_drugs"
        ).fetchall()
        alias_by_slug, alias_by_inn = _load_alias_map_bridges(conn)
    finally:
        conn.close()

    index: dict[str, int] = {}

    def _add(alias: Optional[str], drug_id: int) -> None:
        if not alias:
            return
        alias = alias.strip()
        if len(alias) < _MIN_ALIAS_LEN:
            return
        # 동일 alias 가 여러 drug_id 에 매핑되면 먼저 등록된(작은 drug_id) 값을 유지.
        index.setdefault(alias.lower(), drug_id)

    for drug_id, product_slug, brand_kr, brand_en, ingredient_inn in drugs:
        for cand in _clean_brand_candidates(brand_kr or ""):
            _add(cand, drug_id)
        _add(brand_en, drug_id)
        _add(ingredient_inn, drug_id)

        slug_key = (product_slug or "").strip().lower()
        inn_key = (ingredient_inn or "").strip().lower()
        bridged = alias_by_slug.get(slug_key) if slug_key else None
        if bridged is None and inn_key:
            bridged = alias_by_inn.get(inn_key)
        if bridged:
            for a in bridged:
                _add(a, drug_id)

    with _index_lock:
        _index_cache[path] = index
    return index


def resolve_drug(
    text: str,
    index: Optional[dict[str, int]] = None,
    db_path: Optional[PathLike] = None,
) -> Optional[int]:
    """text 안에서 alias 가 substring 으로 등장하는 drug_id 를 반환.

    여러 alias 가 매치되면 가장 긴 alias 를 우선한다 (짧은 alias 의 우발적
    substring 오탐 — 예: '액' — 을 피하기 위함). 매치 없으면 None.
    """
    if not text:
        return None
    if index is None:
        index = build_alias_index(db_path)
    lowered = text.lower()

    best_alias = ""
    best_drug_id: Optional[int] = None
    for alias, drug_id in index.items():
        if len(alias) <= len(best_alias):
            continue
        if alias in lowered:
            best_alias = alias
            best_drug_id = drug_id
    return best_drug_id
