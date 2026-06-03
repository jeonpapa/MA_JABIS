"""한국 위험분담제(RSA) 적용 약제 curated registry — JSON 외부화 + admin 등록 지원.

권위 소스 (source 필드로 표기):
  1. skill msd_assets.md (KPAD v3 ground-truth) — MSD Tier 1 자산
  2. drug_enrichment 기존 (검증)               — Perplexity 사실 검증된 항목
  3. curated                                    — 사용자/도메인 지식 기반 등록
  4. user_added (admin endpoint)                — 대쉬보드 admin UI 로 등록
  5. perplexity_candidate (auto-flag)           — MI agent 변동사유 분석에서 자동 flagging (검증 전)

저장: `data/curated/rsa_registry.json`
운영:
  - 부팅 시 JSON 로드 → in-memory dict
  - `add_or_update_rsa(brand_key, ...)` 호출 시 in-memory + JSON 양쪽 업데이트 (file lock)
  - POST /api/admin/rsa-registry endpoint 가 wrapper

신규 RSA 등록 흐름:
  1. MI agent 의 RSA 키워드 자동 검출 → perplexity_candidate 상태로 등록
  2. RuleComplianceAgent 일일 리포트가 후보 surface
  3. 사용자 검증 후 대쉬보드 "RSA 등록" UI 또는 직접 JSON 편집 → user_added 로 승격
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "curated" / "rsa_registry.json"
_lock = threading.Lock()
_cache: dict | None = None


def _load() -> dict:
    """JSON 로드 (캐시). 파일 없으면 빈 registry 로 시작."""
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        if not _REGISTRY_PATH.exists():
            logger.warning("[rsa_registry] %s 미존재 — 빈 registry 로 시작", _REGISTRY_PATH)
            _cache = {"_meta": {"schema_version": 1}, "registry": {}}
        else:
            try:
                with _REGISTRY_PATH.open(encoding="utf-8") as f:
                    _cache = json.load(f)
            except Exception as e:
                logger.error("[rsa_registry] JSON 파싱 실패: %s", e)
                _cache = {"_meta": {"schema_version": 1}, "registry": {}}
        return _cache


def _save() -> None:
    """in-memory dict → JSON. atomic write (tmp → rename)."""
    if _cache is None:
        return
    tmp = _REGISTRY_PATH.with_suffix(".json.tmp")
    with _lock:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
        tmp.replace(_REGISTRY_PATH)
    logger.info("[rsa_registry] 저장: %d entries", len(_cache.get("registry") or {}))


def _normalize_brand_key(name: str) -> str:
    """matching 용 brand 핵심부 추출. '키트루다주', '키트루다정100mg' 등 모두 '키트루다'."""
    if not name:
        return ""
    cleaned = re.sub(
        r"(정|주|캡슐|액|주사|시럽|서방정|필름코팅정)?"
        r"\s*\d[\d./]*\s*(mg|밀리그램|㎎|g|그램|㎍|ug|mcg|mL|밀리리터)?.*$",
        "",
        name,
    ).strip()
    return re.sub(r"(주|정|캡슐|액|주사|시럽)$", "", cleaned).strip() or cleaned


def lookup_rsa(brand_name: str) -> dict | None:
    """brand_name 으로 RSA registry 조회. Returns: {is_rsa, rsa_type, rsa_note, source} or None."""
    if not brand_name:
        return None
    key = _normalize_brand_key(brand_name)
    if not key:
        return None
    reg = _load().get("registry") or {}
    if key in reg:
        return dict(reg[key])
    for reg_key, info in reg.items():
        if key.startswith(reg_key) or reg_key in key:
            return dict(info)
    return None


def add_or_update_rsa(
    brand_key: str,
    is_rsa: int,
    rsa_type: str | None = None,
    rsa_note: str = "",
    source: str = "user_added (admin endpoint)",
) -> dict:
    """registry 추가/수정. JSON 영구 저장.

    Returns: 새로 추가된 entry dict.
    Raises: ValueError if invalid input.
    """
    key = _normalize_brand_key(brand_key) or brand_key.strip()
    if not key:
        raise ValueError("brand_key 필수")
    if is_rsa not in (0, 1):
        raise ValueError("is_rsa 는 0 또는 1")
    valid_types = {None, "refund", "expenditure_cap", "utilization", "conditional", "combined"}
    if rsa_type not in valid_types:
        raise ValueError(f"rsa_type 은 {valid_types - {None}} 중 하나 또는 None")

    entry = {
        "is_rsa": is_rsa,
        "rsa_type": rsa_type,
        "rsa_note": rsa_note,
        "source": source,
    }
    reg = _load().get("registry")
    if reg is None:
        _cache["registry"] = {}
        reg = _cache["registry"]
    reg[key] = entry
    _save()
    logger.info("[rsa_registry] %s 등록: is_rsa=%s, type=%s, source=%s", key, is_rsa, rsa_type, source)
    return entry


def remove_rsa(brand_key: str) -> bool:
    """registry 에서 entry 제거. Returns: 제거 성공 여부."""
    key = _normalize_brand_key(brand_key) or brand_key.strip()
    reg = _load().get("registry") or {}
    if key in reg:
        del reg[key]
        _save()
        logger.info("[rsa_registry] %s 제거", key)
        return True
    return False


def list_all() -> dict:
    """전체 registry dict 반환 (source 별 그룹핑은 호출자가)."""
    return dict(_load().get("registry") or {})


def get_all_rsa_drugs() -> list[str]:
    """is_rsa=1 인 자산 brand 명 리스트."""
    reg = _load().get("registry") or {}
    return [k for k, v in reg.items() if v.get("is_rsa") == 1]
