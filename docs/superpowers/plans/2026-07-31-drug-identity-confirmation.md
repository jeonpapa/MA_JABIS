# 검색 전 약제 정체성 확정 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 브랜드/성분 어느 쪽으로 입력하든 검색 전에 확정된 `{brand, INN}` 정체성으로 해석·확인하고, 각 해외 소스에 맞는 term 으로 검색해 "가격이 있는데 못 가져오는" 오류를 줄인다.

**Architecture:** 백엔드 `resolve_identity`(alias맵→MFDS 폴백) + `GET /api/foreign/resolve`. 프론트 타입어헤드가 후보를 확정 → `searchForeignLive(identity)` → `search_all(identity)` 가 스크레이퍼에 brand term 전달(0건 시 US 는 INN 폴백), DB `query_name` 은 canonical slug 로 통일.

**Tech Stack:** Python(Flask, pytest), 기존 `agents/db/drug_aliases.py`·`agents/scrapers/kr_mfds_permit.py`, React/TypeScript(Vite).

**Spec:** `docs/superpowers/specs/2026-07-31-drug-identity-confirmation-design.md`

---

## File Structure

- Create: `agents/drug_identity.py` — `resolve_identity(q)` 후보 리스트
- Create: `tests/test_drug_identity.py`
- Modify: `api/server.py` — `GET /api/foreign/resolve`, `POST /api/foreign/search` 가 `identity` 수용→`search_all`
- Modify: `tests/test_foreign_search_job.py` — resolve 엔드포인트 + identity 전달 테스트
- Modify: `agents/scrapers/base.py` — `FALLBACK_TO_INN = False` 클래스 속성
- Modify: `agents/scrapers/us_micromedex.py` — `FALLBACK_TO_INN = True`
- Modify: `agents/foreign_price_agent.py` — `search_all(..., identity=None)`, `search_one_country(..., identity=None)`: brand term + canonical query_name + INN 폴백
- Modify: `frontend/src/api/foreign.ts` — `Candidate` 타입, `resolveIdentity(q)`, `searchForeignLive(query, countries?, onProgress?, identity?)`
- Modify: `frontend/src/pages/international-pricing/page.tsx` — 타입어헤드 후보 드롭다운 + 확정 identity 상태 + 검색에 전달

---

## Task 1: `agents/drug_identity.py` — resolve_identity

**Files:**
- Create: `agents/drug_identity.py`
- Test: `tests/test_drug_identity.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_drug_identity.py`:
```python
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import drug_identity as di


def test_brand_input_resolves_to_brand_and_inn():
    cands = di.resolve_identity("keytruda")
    assert cands, "keytruda 는 alias 맵에 있어 후보가 나와야 함"
    top = cands[0]
    assert top["slug"] == "keytruda"
    assert top["inn"] == "pembrolizumab"
    assert top["matched_as"] == "brand"
    assert top["source"] == "alias"


def test_ingredient_input_resolves_same_slug():
    cands = di.resolve_identity("pembrolizumab")
    assert cands and cands[0]["slug"] == "keytruda"
    assert cands[0]["matched_as"] == "ingredient"


def test_substring_prefix_matches():
    cands = di.resolve_identity("keytru")
    assert any(c["slug"] == "keytruda" for c in cands)


def test_blank_returns_empty():
    assert di.resolve_identity("") == []
    assert di.resolve_identity(" ") == []


def test_unknown_falls_back_to_mfds(monkeypatch):
    # alias 미스 → MFDS 폴백 호출. lookup_permit 을 모킹.
    def _fake_lookup(item_name=None, ingredient=None, edi_code=None):
        return {"source": "api",
                "summary": {"item_eng_name": "Faketril", "main_ingr_eng": "faketinib"}}
    monkeypatch.setattr(di, "lookup_permit", _fake_lookup)
    cands = di.resolve_identity("zzq-unknown-xyz")
    assert cands and cands[0]["source"] == "mfds"
    assert cands[0]["inn"] == "faketinib"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_drug_identity.py -q`
Expected: FAIL — `No module named 'agents.drug_identity'`

- [ ] **Step 3: 구현**

`agents/drug_identity.py`:
```python
"""자유 텍스트 약제 입력 → 확정 정체성 후보(brand + INN) 해석.

우선순위:
  1) drug_aliases (product_alias_map + BRAND_TO_MOLECULE) — substring 매칭, 즉시
  2) 미스 시 kr_mfds_permit.lookup_permit — 제품명↔주성분(영문), 권위 소스

후보: {slug, brand, inn, display, matched_as('brand'|'ingredient'), source('alias'|'mfds')}
검색 파이프라인은 이 후보의 brand/inn/slug 만 소비한다(단방향).
"""
from __future__ import annotations

import logging

from agents.db import drug_aliases
from agents.scrapers.kr_mfds_permit import lookup_permit

logger = logging.getLogger(__name__)


def _alias_candidates(q: str) -> list[dict]:
    """alias 맵 키(slug·inn·brand_aliases)에 substring 매칭 → slug 기준 dedupe."""
    key = q.strip().lower()
    if not key:
        return []
    cache = drug_aliases._load_db_cache()          # {alias_lower: {slug, inn, brand_aliases}}
    by_slug: dict[str, dict] = {}
    # 1) DB 맵
    for alias_lower, entry in cache.items():
        if key in alias_lower:
            slug = entry["slug"]
            inn = entry.get("inn") or ""
            matched_as = "ingredient" if (inn and key in inn.lower()) else "brand"
            prev = by_slug.get(slug)
            # 더 정확한(=exact) 매칭 또는 brand 매칭을 우선 표시
            if prev is None or (alias_lower == key):
                by_slug[slug] = {
                    "slug": slug,
                    "brand": drug_aliases.display_name(slug),
                    "inn": inn,
                    "display": drug_aliases.display_name(slug),
                    "matched_as": matched_as,
                    "source": "alias",
                }
    # 2) in-memory BRAND_TO_MOLECULE 보충 (DB 미적용 환경)
    for brand, mol in drug_aliases.BRAND_TO_MOLECULE.items():
        if key in brand or key in mol:
            slug = brand
            if slug in by_slug:
                continue
            by_slug[slug] = {
                "slug": slug, "brand": brand, "inn": mol, "display": brand,
                "matched_as": "ingredient" if key in mol else "brand",
                "source": "alias",
            }
    # exact 매칭을 앞으로 정렬
    out = list(by_slug.values())
    out.sort(key=lambda c: (key not in c["slug"].lower(), c["slug"]))
    return out


def _mfds_candidate(q: str) -> list[dict]:
    """alias 미스 시 MFDS 허가 API 로 제품명↔주성분(영문) 후보 1건."""
    try:
        res = lookup_permit(item_name=q) or {}
    except Exception as e:            # 키 미설정·네트워크 실패 → 후보 없음(호출측이 raw 폴백)
        logger.info("[identity] MFDS 폴백 실패: %s", e)
        return []
    summary = res.get("summary") or {}
    brand = (summary.get("item_eng_name") or summary.get("item_name") or "").strip()
    inn = (summary.get("main_ingr_eng") or "").strip()
    if not brand and not inn:
        return []
    slug = (brand or inn).lower()
    return [{
        "slug": slug, "brand": brand or slug, "inn": inn,
        "display": brand or slug, "matched_as": "brand", "source": "mfds",
    }]


def resolve_identity(q: str, limit: int = 6) -> list[dict]:
    """자유 텍스트 → 확정 후보 리스트. alias 우선, 미스 시 MFDS 폴백."""
    if not q or not q.strip():
        return []
    cands = _alias_candidates(q)
    if cands:
        return cands[:limit]
    return _mfds_candidate(q)[:limit]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_drug_identity.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add agents/drug_identity.py tests/test_drug_identity.py
git commit -m "feat(identity): resolve_identity — alias맵→MFDS 폴백 약제 정체성 해석"
```

---

## Task 2: `GET /api/foreign/resolve` 엔드포인트

**Files:**
- Modify: `api/server.py` (기존 `@app.post("/api/foreign/search")` 근처)
- Test: `tests/test_foreign_search_job.py`

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_foreign_search_job.py` 하단에 추가:
```python
def test_resolve_endpoint_returns_candidates():
    c = s.app.test_client()
    r = c.get("/api/foreign/resolve?q=keytruda")
    assert r.status_code == 200
    body = r.get_json()
    cands = body.get("candidates") or []
    assert any(x["slug"] == "keytruda" and x["inn"] == "pembrolizumab" for x in cands)


def test_resolve_blank_returns_empty():
    c = s.app.test_client()
    r = c.get("/api/foreign/resolve?q=")
    assert r.status_code == 200 and r.get_json()["candidates"] == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_foreign_search_job.py::test_resolve_endpoint_returns_candidates -q`
Expected: FAIL (404)

- [ ] **Step 3: 엔드포인트 구현**

`api/server.py` 의 `@app.post("/api/foreign/search")` 정의 **직전**에 추가:
```python
@app.get("/api/foreign/resolve")
def foreign_resolve():
    """자유 텍스트 → 확정 약제 후보(brand+INN). 타입어헤드용.
    GET /api/foreign/resolve?q=keytru
    → { query, candidates: [{slug, brand, inn, display, matched_as, source}, ...] }
    """
    from agents.drug_identity import resolve_identity
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"query": q, "candidates": []})
    try:
        cands = resolve_identity(q)
    except Exception as e:
        logger.warning("resolve 실패(빈 후보 반환): %s", e)
        cands = []
    return jsonify({"query": q, "candidates": cands})
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_foreign_search_job.py -q`
Expected: PASS (기존 + 신규 2건)

- [ ] **Step 5: 커밋**

```bash
git add api/server.py tests/test_foreign_search_job.py
git commit -m "feat(identity): GET /api/foreign/resolve — 타입어헤드 후보 엔드포인트"
```

---

## Task 3: 스크레이퍼 term 라우팅 + INN 폴백 (백엔드 agent)

**Files:**
- Modify: `agents/scrapers/base.py` (클래스 속성)
- Modify: `agents/scrapers/us_micromedex.py` (플래그)
- Modify: `agents/foreign_price_agent.py:search_all` / `search_one_country`
- Test: `tests/test_identity_search_routing.py` (신규)

- [ ] **Step 1: base 에 FALLBACK_TO_INN 기본값 추가**

`agents/scrapers/base.py` — `BaseScraper` 클래스 본문의 다른 클래스 속성(`COUNTRY` 등) 근처에 추가:
```python
    # brand 검색이 0건일 때 identity.inn 으로 1회 재시도할지 (기본 off).
    # 소스의 검색 모드가 제품명 위주여서 INN 입력을 놓치는 경우만 opt-in.
    FALLBACK_TO_INN = False
```

- [ ] **Step 2: US 스크레이퍼 opt-in**

`agents/scrapers/us_micromedex.py` — 클래스 속성부(`COUNTRY = "US"` 등) 근처에 추가:
```python
    FALLBACK_TO_INN = True   # RED BOOK 제품명 모드 → INN 입력 시 brand 로 재시도
```

- [ ] **Step 3: 실패 테스트 작성**

`tests/test_identity_search_routing.py`:
```python
from __future__ import annotations
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.foreign_price_agent import ForeignPriceAgent


class _StubScraper:
    COUNTRY = "US"; CURRENCY = "USD"; SOURCE_TYPE = None
    FALLBACK_TO_INN = True
    def __init__(self): self.calls = []
    def _resolve_form_type(self, item): return "injection"
    async def run(self, term):
        self.calls.append(term)
        # brand("keytruda") 는 0건, INN("pembrolizumab") 은 1건 → 폴백 검증
        if term == "pembrolizumab":
            return [{"query_name": term, "country": "US", "local_price": 100.0,
                     "ingredient": "pembrolizumab", "form_type": "injection"}]
        return []


def test_brand_term_then_inn_fallback(monkeypatch):
    agent = ForeignPriceAgent()
    stub = _StubScraper()
    monkeypatch.setattr(agent, "_build_scraper", lambda c: stub)
    monkeypatch.setattr(agent.rate_fetcher, "get_36m_average", lambda *a, **k: None)
    monkeypatch.setattr(agent.db, "save_foreign_price", lambda item: 1)
    identity = {"slug": "keytruda", "brand": "keytruda", "inn": "pembrolizumab"}
    rows = asyncio.run(agent.search_one_country("keytruda", "US", identity=identity))
    # brand 먼저, 0건이라 INN 폴백
    assert stub.calls == ["keytruda", "pembrolizumab"]
    # 저장 query_name 은 canonical slug 로 통일
    assert rows and rows[0]["query_name"] == "keytruda"
```

- [ ] **Step 4: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_identity_search_routing.py -q`
Expected: FAIL — `search_one_country() got an unexpected keyword argument 'identity'`

- [ ] **Step 5: search_all / search_one_country 에 identity 배선**

`agents/foreign_price_agent.py`:

(a) `search_all` 시그니처와 호출부:
```python
    async def search_all(
        self, query: str, countries: list[str] = None,
        reference_date: date = None, identity: dict = None
    ) -> dict:
```
루프 내 호출을 identity 전달로 교체:
```python
                results[country] = await self.search_one_country(
                    query, country, reference_date, identity=identity
                )
```

(b) `search_one_country` 시그니처:
```python
    async def search_one_country(
        self, query: str, country: str, reference_date: date = None,
        identity: dict = None
    ) -> list[dict]:
```

(c) 스크레이퍼 실행부 — brand term + INN 폴백 + canonical query_name.
기존:
```python
        scraper = self._build_scraper(country)
        source_type = getattr(scraper, "SOURCE_TYPE", None)
        try:
            raw_results = await scraper.run(query)
        except Exception as e:
            if country == "IT":
                logger.warning("[IT] CSV 채널 실패(TNF 로 계속): %s", e)
                raw_results = []
            else:
                raise
```
교체:
```python
        scraper = self._build_scraper(country)
        source_type = getattr(scraper, "SOURCE_TYPE", None)
        # identity 있으면 brand 로 검색(대부분 소스가 브랜드 인덱스), 저장/집계는 slug 로 통일.
        ident = identity or {}
        search_term = ident.get("brand") or query
        canonical_name = ident.get("slug") or query
        fallback_inn = ident.get("inn")
        try:
            raw_results = await scraper.run(search_term)
        except Exception as e:
            if country == "IT":
                logger.warning("[IT] CSV 채널 실패(TNF 로 계속): %s", e)
                raw_results = []
            else:
                raise
        # brand 0건 & 스크레이퍼 opt-in & INN 존재 → INN 으로 1회 재시도
        if (not raw_results and getattr(scraper, "FALLBACK_TO_INN", False)
                and fallback_inn and fallback_inn.lower() != str(search_term).lower()):
            logger.info("[%s] brand 0건 → INN 폴백 검색: %s", country, fallback_inn)
            try:
                raw_results = await scraper.run(fallback_inn)
            except Exception as e:
                logger.warning("[%s] INN 폴백 실패: %s", country, e)
```

(d) IT/FR 병행 채널의 `.run(query)` 두 곳을 `.run(search_term)` 으로 교체
(`tnf.run(query)` → `tnf.run(search_term)`, `vidal.run(query)` → `vidal.run(search_term)`).

(e) 저장 직전 query_name 을 canonical 로 통일 — `if not raw_results:` return 뒤,
form_type fallback 루프 **앞**에 추가:
```python
        # 브랜드/성분 어느 입력이든 같은 캐시 레코드로 집계 → query_name = canonical slug
        for item in raw_results:
            item["query_name"] = canonical_name
```

(f) dosing 조회의 `query` 인자를 `canonical_name` 으로 교체:
```python
        dosing_map = self._load_dosing_map(canonical_name, ingredients)
```
그리고 저장 루프 내:
```python
            dosing = self._match_dosing(canonical_name, item.get("ingredient", ""), dosing_map)
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_identity_search_routing.py -q`
Expected: PASS

- [ ] **Step 7: 회귀 — identity 없이 기존 동작**

Run: `.venv/bin/python -m pytest tests/ -q -k "foreign or identity"`
Expected: 기존 테스트 PASS (identity=None → search_term=query 동일 동작)

- [ ] **Step 8: 커밋**

```bash
git add agents/scrapers/base.py agents/scrapers/us_micromedex.py agents/foreign_price_agent.py tests/test_identity_search_routing.py
git commit -m "feat(identity): search_all identity term 라우팅 + brand→INN 폴백(US) + canonical query_name"
```

---

## Task 4: `POST /api/foreign/search` 가 identity 수용

**Files:**
- Modify: `api/server.py` (`foreign_search` + `_run`)
- Test: `tests/test_foreign_search_job.py`

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_foreign_search_job.py`:
```python
def test_search_forwards_identity(monkeypatch):
    seen = {}
    async def _stub(query, countries=None, identity=None):
        seen["identity"] = identity
        return {c: [] for c in (countries or [])}
    monkeypatch.setattr(s.foreign_agent, "search_all", _stub)
    c = s.app.test_client()
    r = c.post("/api/foreign/search", json={
        "query": "keytruda", "countries": ["US"], "use_cache": False,
        "identity": {"slug": "keytruda", "brand": "keytruda", "inn": "pembrolizumab"},
    })
    _poll(c, r.get_json()["job_id"])
    assert seen["identity"] and seen["identity"]["inn"] == "pembrolizumab"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/python -m pytest tests/test_foreign_search_job.py::test_search_forwards_identity -q`
Expected: FAIL (identity 전달 안 됨 → KeyError/None)

- [ ] **Step 3: 구현**

`api/server.py foreign_search()`:
- body 파싱부에 추가: `identity = body.get("identity") or None`
- job dict 에 `"identity": identity` 저장(다른 필드 옆).
- `_run` 시그니처에 identity 추가 + `search_all` 호출에 전달:
```python
    def _run(job_id: str, query: str, supported: list, identity=None):
        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(
                foreign_agent.search_all(query, countries=supported, identity=identity)
            )
```
- Thread 시작부: `args=(job_id, query, supported, identity)`.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/python -m pytest tests/test_foreign_search_job.py -q`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add api/server.py tests/test_foreign_search_job.py
git commit -m "feat(identity): POST /api/foreign/search 가 identity 를 search_all 로 전달"
```

---

## Task 5: 프론트 API — resolveIdentity + searchForeignLive identity

**Files:**
- Modify: `frontend/src/api/foreign.ts`

- [ ] **Step 1: Candidate 타입 + resolveIdentity fetcher 추가**

`foreign.ts` 상단 타입 근처:
```typescript
export interface Candidate {
  slug: string;
  brand: string;
  inn: string;
  display: string;
  matched_as: 'brand' | 'ingredient';
  source: 'alias' | 'mfds';
}

export async function resolveIdentity(q: string): Promise<Candidate[]> {
  const t = q.trim();
  if (!t) return [];
  const res = await api.get<{ candidates?: Candidate[] }>(
    `/api/foreign/resolve?q=${encodeURIComponent(t)}`);
  return res?.candidates ?? [];
}
```

- [ ] **Step 2: searchForeignLive 에 identity 파라미터 추가**

시그니처와 POST body 수정:
```typescript
export async function searchForeignLive(
  query: string,
  countries?: string[],
  onProgress?: (elapsedSec: number) => void,
  identity?: { slug: string; brand: string; inn: string },
): Promise<LiveSearchOutcome | null> {
  const started = await api.post<{ job_id?: string; status?: string }>('/api/foreign/search', {
    query,
    countries: countries ?? undefined,
    use_cache: false,
    identity: identity ?? undefined,
  });
```
(나머지 폴링 로직 불변)

- [ ] **Step 3: 빌드 확인**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 빌드 성공

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/api/foreign.ts
git commit -m "feat(identity): 프론트 resolveIdentity + searchForeignLive identity 파라미터"
```

---

## Task 6: 프론트 타입어헤드 UI (page.tsx)

**Files:**
- Modify: `frontend/src/pages/international-pricing/page.tsx`

- [ ] **Step 1: import 확장**

`@/api/foreign` import 에 `resolveIdentity` 추가, type import 에 `Candidate` 추가.
`Selection` 인터페이스에 identity 필드 추가:
```typescript
interface Selection {
  query: string;
  canonical: string;
  countryCount?: number;
  identity?: { slug: string; brand: string; inn: string };
}
```

- [ ] **Step 2: 상태 + debounce 해석 effect 추가**

`newSearchQuery` 상태 근처:
```typescript
  const [identityCandidates, setIdentityCandidates] = useState<Candidate[]>([]);
```
검색어 debounce 해석 effect (기존 useEffect 근처):
```typescript
  useEffect(() => {
    const q = newSearchQuery.trim();
    if (!q) { setIdentityCandidates([]); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const cands = await resolveIdentity(q);
        if (!cancelled) setIdentityCandidates(cands);
      } catch { if (!cancelled) setIdentityCandidates([]); }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
  }, [newSearchQuery]);
```

- [ ] **Step 3: 확정 핸들러 추가**

`handleSelectHistory` 근처:
```typescript
  const handleSelectIdentity = (c: Candidate) => {
    handleSelect({
      query: c.slug,
      canonical: c.inn || '',
      identity: { slug: c.slug, brand: c.brand, inn: c.inn },
    });
  };
```
`handleNewSearch` 수정 — 후보 있으면 첫 후보 확정 우선:
```typescript
  const handleNewSearch = () => {
    const q = newSearchQuery.trim();
    if (!q) return;
    if (identityCandidates.length > 0) handleSelectIdentity(identityCandidates[0]);
    else if (searchResults.length > 0) handleSelectHistory(searchResults[0]);
    else handleSelect({ query: q.toLowerCase(), canonical: '' });
  };
```

- [ ] **Step 4: 드롭다운에 후보 섹션 추가**

기존 `{searchDropdown && newSearchQuery && ( ... )}` 드롭다운 내부, 이력 목록 **위**에 해석 후보 섹션 삽입:
```tsx
{identityCandidates.length > 0 && (
  <div className={`border-b ${divider}`}>
    {identityCandidates.map(c => (
      <button key={c.slug} onClick={() => handleSelectIdentity(c)}
        className={`w-full flex items-center gap-2 px-4 py-2.5 text-left hover:${isDark ? 'bg-[#1E2530]' : 'bg-gray-50'} transition-colors`}>
        <i className="ri-checkbox-circle-line text-teal-500"></i>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${textMain}`}>{c.display || c.brand}</p>
          <p className={`text-xs ${textSub}`}>
            {c.inn ? `성분 ${c.inn} · ` : ''}{c.matched_as === 'ingredient' ? '성분명 입력' : '브랜드명 입력'}
            {c.source === 'mfds' ? ' · MFDS' : ''}
          </p>
        </div>
      </button>
    ))}
  </div>
)}
```

- [ ] **Step 5: runLiveSearch / researchCountry 가 identity 전달**

`runLiveSearch` 의 `searchForeignLive(selected.query)` →
```typescript
      await searchForeignLive(selected.query, undefined, undefined, selected.identity);
```
`researchCountry` 의 호출 →
```typescript
      const outcome = await searchForeignLive(selected.query, [backendCode], sec => setResearchElapsed(sec), selected.identity);
```

- [ ] **Step 6: 빌드 확인**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 빌드 성공

- [ ] **Step 7: 커밋**

```bash
git add frontend/src/pages/international-pricing/page.tsx
git commit -m "feat(identity): 검색박스 타입어헤드 — 해석 후보 확정 후 identity 로 검색"
```

---

## Task 7: 통합 검증

**Files:** (검증 전용, 코드 변경 없음)

- [ ] **Step 1: 백엔드 해석 일관성 실측**

Run:
```bash
.venv/bin/python -c "
from agents.drug_identity import resolve_identity
for q in ['keytruda','pembrolizumab','keytru']:
    c = resolve_identity(q)
    print(q, '->', c[0]['slug'], c[0]['inn'], c[0]['matched_as'] if c else '(none)')
"
```
Expected: 세 입력 모두 slug=keytruda, inn=pembrolizumab (matched_as 는 brand/ingredient/brand)

- [ ] **Step 2: 전체 관련 테스트**

Run: `.venv/bin/python -m pytest tests/test_drug_identity.py tests/test_foreign_search_job.py tests/test_identity_search_routing.py -q`
Expected: 전부 PASS

- [ ] **Step 3: 프론트 최종 빌드**

Run: `cd frontend && npm run build`
Expected: 성공

- [ ] **Step 4: 최종 리뷰 후 배포 대기 안내**

배포는 사용자 직접. `flyctl deploy` 후: keytruda·pembrolizumab 양쪽 입력 시 동일 후보 확정 → US 가격 정상 확인.

---

## Self-Review 결과

- **Spec 커버리지**: resolve_identity(§4.1)→T1, /resolve(§4.2)→T2, 타입어헤드(§4.3)→T6, search plumbing·INN 폴백(§4.4)→T3·T4·T5. 하위호환(§5)→T3 Step7·T4. 모두 매핑됨.
- **타입 일관성**: Candidate 필드(slug/brand/inn/display/matched_as/source) 백엔드↔프론트 동일. identity {slug,brand,inn} 전 계층 동일.
- **플레이스홀더 없음**: 모든 코드 스텝에 실제 코드 포함.
