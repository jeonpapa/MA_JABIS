"""약제 등재 아날로그 검색 — 태그/동의어 온톨로지 시드.

`analog_ontology.json` (137 concept · 85 relation) 을 로드해서 검색·태깅에 쓰는
정규화된 인덱스를 제공한다. 동의어 클러스터의 권위 소스.

설계 원칙
  - 한 report 는 **구조화 필드에서 실제로 evidence 가 있는 concept 만** 태깅한다
    (grounded tagging). related_concept 자동 확장 금지 — drift 방지.
  - 검색 시에는 매칭된 concept 의 **alias 전량**으로 쿼리를 확장한다 (동의어 확장).
  - 정규화: 소문자 + 공백 제거. "PCSK9 억제제" == "pcsk9억제제".

핵심 사용 예 — 같은 의미의 서로 다른 표기가 같은 결과를 반환:
  "고지혈증 주사제"  → [disease-dyslipidemia, form-injection]
  "이상지질혈증 주사제" → [disease-dyslipidemia, form-injection]
  "PCSK9 주사제"     → [target-pcsk9, class-pcsk9-inhibitor, form-injection]
  레파타/프랄런트 report 는 위 concept 를 모두 보유 → 세 쿼리 모두 동일 약제 retrieval.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_ONTOLOGY_PATH = Path(__file__).resolve().parent / "analog_ontology.json"


def normalize(s: str | None) -> str:
    """매칭용 정규화: 소문자 + 모든 공백 제거."""
    if not s:
        return ""
    return re.sub(r"\s+", "", s).lower()


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(_ONTOLOGY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def concepts() -> list[dict]:
    """concept 시드 — analog_concepts 테이블 적재용."""
    return _load().get("concepts", [])


@lru_cache(maxsize=1)
def relations() -> list[dict]:
    return _load().get("relations", [])


@lru_cache(maxsize=1)
def concept_by_id() -> dict[str, dict]:
    return {c["concept_id"]: c for c in concepts()}


@lru_cache(maxsize=1)
def alias_rows() -> list[tuple[str, str, str]]:
    """(normalized_alias, concept_id, raw_alias) — analog_concept_aliases 적재용.

    canonical_ko / canonical_en 도 alias 로 포함. 중복 제거.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for c in concepts():
        cid = c["concept_id"]
        forms = list(c.get("aliases", []))
        for extra in (c.get("canonical_ko"), c.get("canonical_en")):
            if extra:
                # canonical_en 이 "Dyslipidemia / Hyperlipidemia" 처럼 슬래시 복합일 수 있음
                forms.extend(p.strip() for p in re.split(r"[/]", extra))
        for raw in forms:
            n = normalize(raw)
            if not n or (n, cid) in seen:
                continue
            seen.add((n, cid))
            out.append((n, cid, raw))
    return out


@lru_cache(maxsize=1)
def alias_index() -> dict[str, list[str]]:
    """normalized_alias → [concept_id, ...] (한 표기가 여러 concept 에 매핑 가능)."""
    idx: dict[str, list[str]] = {}
    for n, cid, _raw in alias_rows():
        idx.setdefault(n, []).append(cid)
    return idx


@lru_cache(maxsize=1)
def class_target_links() -> dict[str, list[str]]:
    """drug_class ↔ target 양방향 링크 (has_target relation, 동일 도메인만).

    PCSK9 검색 시 target-pcsk9 ↔ class-pcsk9-inhibitor 를 같은 의미로 묶기 위함.
    disease↔target 같은 느슨한 링크는 제외 (drift 방지)."""
    cby = concept_by_id()
    out: dict[str, list[str]] = {}
    safe = {"drug_class", "target"}
    for rel in relations():
        if rel.get("relation") != "has_target":
            continue
        a, b = rel.get("from_concept_id"), rel.get("to_concept_id")
        if cby.get(a, {}).get("type") in safe and cby.get(b, {}).get("type") in safe:
            out.setdefault(a, []).append(b)
            out.setdefault(b, []).append(a)
    return out


@lru_cache(maxsize=1)
def aliases_of() -> dict[str, list[str]]:
    """concept_id → [raw_alias, ...] (쿼리 확장용 — 원문 표기 보존)."""
    out: dict[str, list[str]] = {}
    for _n, cid, raw in alias_rows():
        out.setdefault(cid, []).append(raw)
    return out


# ── 자유텍스트(효능효과) 질환 스캔용 ─────────────────────────────────────────────
# 짧은 alias 의 오탐 방지: 길이 3자 이상, 긴 것 우선 매칭.
@lru_cache(maxsize=1)
def disease_scan_terms() -> list[tuple[str, str, str]]:
    """(raw_alias, normalized, concept_id) — disease 계열만, len>=3, 긴 순 정렬."""
    out: list[tuple[str, str, str]] = []
    target_types = {"disease", "drug_class", "target"}
    for c in concepts():
        if c.get("type") not in target_types:
            continue
        cid = c["concept_id"]
        for raw in c.get("aliases", []):
            if len(raw.strip()) >= 3:
                out.append((raw.strip(), normalize(raw), cid))
    out.sort(key=lambda t: -len(t[1]))
    return out


# ── 제형(form) — 브랜드명 접미사 기반 탐지 ──────────────────────────────────────
# alias("주사")로는 "키트루다주"를 못 잡으므로 접미사 규칙으로 보강.
_FORM_SUFFIX_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(점안|안약)"), "form-eye-drop"),
    (re.compile(r"(흡입|엘립타|레스피맷|에어로|디스커스)"), "form-inhaler"),
    (re.compile(r"(패치|패취|경피)"), "form-patch"),
    (re.compile(r"(주사|시린지|프리필드|펜주|주$|주\b)"), "form-injection"),
    (re.compile(r"(정$|정\b|캡슐|캅셀|연질캡|서방정|장용정|필름코팅정|구강붕해정)"), "form-oral"),
]


def detect_form(brand_name: str | None) -> str | None:
    """브랜드명 접미사로 제형 concept_id 추정. 없으면 None."""
    if not brand_name:
        return None
    name = brand_name.strip()
    for pat, cid in _FORM_SUFFIX_RULES:
        if pat.search(name):
            return cid
    return None
