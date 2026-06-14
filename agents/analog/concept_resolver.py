"""약제 등재 아날로그 검색 — 쿼리 → concept 동의어 확장.

사용자 쿼리를 tag_seeds 온톨로지로 해석해서
  ① 매칭된 concept_id 집합 (태그 오버랩 스코어링용)
  ② 동의어로 확장된 FTS5 쿼리 문자열 (lexical recall 강화)
를 만든다. DB 불필요 — 온톨로지는 in-memory (137 concept).

예: "PCSK9 주사제"
  matched_concepts = [target-pcsk9, class-pcsk9-inhibitor, form-injection]
  expanded → evolocumab/alirocumab/이상지질혈증/고지혈증/주사/피하주사/... 까지 FTS OR 확장
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from agents.analog import tag_seeds as ts
from agents.analog import domain_terms as dt

_KR = re.compile(r"[가-힣]{2,}")
_EN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]{1,}")


@dataclass
class QueryGroup:
    """AND 결합 단위. 공백으로 구분된 한 검색어 = 한 그룹.

    kind='concept' → 온톨로지 concept (그룹 내 동의어 OR), concept_ids 보유.
    kind='field'   → 도메인 용어 (구조화 필드 술어), domain_key 보유.
    그룹 간에는 AND — 모든 그룹을 충족하는 결과가 상위로 정렬된다.
    """
    label: str
    kind: str                                       # 'concept' | 'field'
    matched_via: str
    concept_ids: list[str] = field(default_factory=list)
    domain_key: str | None = None


@dataclass
class QueryResolution:
    original: str
    matched_concepts: list[dict] = field(default_factory=list)  # {concept_id,type,canonical_ko,matched_via}
    concept_ids: list[str] = field(default_factory=list)
    fts_query: str = '""'
    groups: list[QueryGroup] = field(default_factory=list)       # AND 그룹 (공백 분리)

    @property
    def has_concepts(self) -> bool:
        return bool(self.concept_ids)

    @property
    def hard_groups(self) -> list[QueryGroup]:
        """AND 재정렬 대상 그룹 (concept + field 모두)."""
        return self.groups

    @property
    def is_and_query(self) -> bool:
        return len(self.groups) >= 2


def _tokenize(text: str) -> list[str]:
    low = text.lower()
    toks = _KR.findall(low) + _EN.findall(low)
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _candidate_surfaces(query: str, tokens: list[str]) -> list[str]:
    """alias 매칭 후보 표면형: 전체 쿼리 + 토큰 + 인접 토큰 bigram."""
    cands = [query]
    cands += tokens
    # 인접 토큰 결합 ("면역관문 억제제" → "면역관문억제제")
    for i in range(len(tokens) - 1):
        cands.append(tokens[i] + tokens[i + 1])
    return cands


@lru_cache(maxsize=512)
def resolve_query(query: str) -> QueryResolution:
    query = (query or "").strip()
    if not query:
        return QueryResolution(original=query)

    tokens = _tokenize(query)
    idx = ts.alias_index()
    cby = ts.concept_by_id()

    matched: dict[str, str] = {}  # concept_id → matched_via 표면형
    for surf in _candidate_surfaces(query, tokens):
        n = ts.normalize(surf)
        for cid in idx.get(n, []):
            matched.setdefault(cid, surf)

    # drug_class ↔ target 동일 도메인 확장 (PCSK9 ↔ PCSK9억제제)
    links = ts.class_target_links()
    for cid in list(matched):
        for linked in links.get(cid, []):
            matched.setdefault(linked, matched[cid])

    # ── AND 그룹 빌드 (공백 분리 단위) ──────────────────────────────────────
    groups = _build_groups(query, idx, links)

    # FTS 쿼리 빌드: 원본 토큰 + 매칭 concept 의 모든 alias subtoken
    fts_terms: set[str] = set()
    for t in tokens:
        if len(t) >= 2:
            fts_terms.add(t)
    aliases_of = ts.aliases_of()
    for cid in matched:
        for raw in aliases_of.get(cid, []):
            for sub in (_KR.findall(raw.lower()) + _EN.findall(raw.lower())):
                if len(sub) >= 2:
                    fts_terms.add(sub)

    fts_query = " OR ".join(f'"{t}"*' for t in sorted(fts_terms)) if fts_terms else '""'

    matched_concepts = [
        {
            "concept_id": cid,
            "type": cby.get(cid, {}).get("type"),
            "canonical_ko": cby.get(cid, {}).get("canonical_ko"),
            "canonical_en": cby.get(cid, {}).get("canonical_en"),
            "matched_via": via,
        }
        for cid, via in matched.items()
    ]
    # 도메인(필드) 그룹도 칩으로 노출 — type='field'
    seen_field = set()
    for g in groups:
        if g.kind == "field" and g.domain_key not in seen_field:
            seen_field.add(g.domain_key)
            matched_concepts.append({
                "concept_id": f"field:{g.domain_key}",
                "type": "field",
                "canonical_ko": g.label,
                "canonical_en": None,
                "matched_via": g.matched_via,
            })

    return QueryResolution(
        original=query,
        matched_concepts=matched_concepts,
        concept_ids=list(matched.keys()),
        fts_query=fts_query,
        groups=groups,
    )


def _build_groups(query: str, idx: dict, links: dict) -> list["QueryGroup"]:
    """쿼리를 공백 단위 청크로 쪼개 AND 그룹을 만든다.

    각 청크: ① 온톨로지 concept (그룹 내 동의어 OR + class↔target 확장)
             ② 도메인 용어 (구조화 필드 술어)
             ③ 둘 다 실패 시 인접 청크와 bigram 으로 concept 재시도, 그래도 실패면 무시.
    동일 그룹(같은 concept 집합 / 같은 domain_key)은 dedup.
    """
    chunks = [c for c in re.split(r"\s+", query.strip()) if c]
    if not chunks:
        return []

    groups: list[QueryGroup] = []
    seen_concept: set[frozenset] = set()
    seen_field: set[str] = set()
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        n = ts.normalize(chunk)
        cids = list(idx.get(n, []))
        consumed = 1

        # bigram 보강: 단일 청크 미매칭 시 다음 청크와 결합 시도 ("면역관문 억제제")
        if not cids and not dt.match(n) and i + 1 < len(chunks):
            n2 = ts.normalize(chunk + chunks[i + 1])
            if n2 in idx:
                cids = list(idx[n2])
                consumed = 2
                chunk = chunk + " " + chunks[i + 1]

        if cids:
            # class↔target 확장 (그룹 내 동의어로 묶음)
            expanded = set(cids)
            for c in cids:
                expanded.update(links.get(c, []))
            keyset = frozenset(expanded)
            if keyset not in seen_concept:
                seen_concept.add(keyset)
                groups.append(QueryGroup(
                    label=chunk, kind="concept", matched_via=chunk,
                    concept_ids=sorted(expanded),
                ))
        else:
            term = dt.match(n)
            if term and term.key not in seen_field:
                seen_field.add(term.key)
                groups.append(QueryGroup(
                    label=term.label, kind="field", matched_via=chunk,
                    domain_key=term.key,
                ))
        i += consumed
    return groups
