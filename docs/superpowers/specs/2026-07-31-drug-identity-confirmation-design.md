# 검색 전 약제 정체성 확정 (Drug Identity Confirmation) — 설계

**작성일**: 2026-07-31
**영역**: 해외 약가 (International Pricing) 검색 입력 · 스크레이퍼 term 라우팅

---

## 1. 문제 (Why)

해외 약가 검색에서 사용자는 **브랜드명**(keytruda)으로도, **성분/INN**(pembrolizumab)으로도 입력한다.
현재는 입력한 raw 문자열이 그대로 모든 스크레이퍼로 전달된다. 스크레이퍼마다 검색 모드가 다르므로
(예: US Micromedex RED BOOK 은 **제품명 모드** `redbookProductName` 하드코딩) 입력 종류와 소스 모드가
어긋나면 **가격이 실제 존재하는데도 못 가져오는** 오류가 난다.

실측: keytruda 를 US RED BOOK 에서 제품명으로 검색하면 pembrolizumab WAC(6136.00 등) 7건이 뜨지만,
성분/제품명 모드가 어긋나면 0건. 사용자 관찰: "제품명을 입력하고 성분 필터가 켜진 경우 못 찾는다."

## 2. 목표 (What)

검색 실행 **전에** 자유 텍스트 입력을 확정된 `{브랜드, 성분(INN)}` 정체성으로 해석하고, 사용자가
타입어헤드 드롭다운에서 **어떤 약제인지 확인(클릭)** 하게 한 뒤, 그 확정 identity 로 각 소스에 맞는
term 을 보내 오류를 줄인다.

**성공 기준**: `keytruda`(브랜드) 입력과 `pembrolizumab`(성분) 입력이 **동일 identity** 로 확정되고,
US 가격이 양쪽 모두 정상 수집된다.

## 3. 데이터 흐름

```
사용자 입력(영어 자유텍스트)
  └▶ [타입어헤드, 300ms debounce]  GET /api/foreign/resolve?q=<text>
        └▶ resolve_identity(q):
             1) drug_aliases (product_alias_map + BRAND_TO_MOLECULE)  — 즉시, 포트폴리오/경쟁사
             2) 미스 시 kr_mfds_permit.lookup_permit  — 제품명↔주성분(영문), 권위 소스
             → 후보[{slug, brand, inn, display, matched_as, source}]
  └▶ 드롭다운에 후보 표시 → 클릭 = 확정 identity
        └▶ searchForeignLive(identity)
             └▶ POST /api/foreign/search { query, identity:{slug,brand,inn} }
                  └▶ search_all(query, identity) → 스크레이퍼별 term:
                       · 기본: brand 로 검색
                       · brand 0건 & 스크레이퍼가 opt-in 이면 inn 으로 1회 재시도
```

## 4. 컴포넌트

### 4.1 `agents/drug_identity.py` (신규)

```python
def resolve_identity(q: str, limit: int = 6) -> list[dict]:
    """자유 텍스트 → 확정 후보 리스트.
    후보: {slug, brand, inn, display, matched_as, source}
      - matched_as: 'brand' | 'ingredient'  (입력이 무엇으로 매칭됐는지)
      - source: 'alias' | 'mfds'
    1) alias 맵(product_alias_map + BRAND_TO_MOLECULE) substring 매칭 — 즉시
    2) 결과 없으면 kr_mfds_permit.lookup_permit(item_name=q) → main_ingr_eng 로 후보 1건
    """
```

- alias 매칭: `drug_aliases._load_db_cache()` 의 키(slug·inn·brand_aliases 소문자)에 대해 **substring**
  매칭 → 각 매칭을 하나의 canonical entry 로 dedupe(같은 slug 는 1개). `matched_as` 는 매칭된 키가
  inn 이면 `ingredient`, 아니면 `brand`.
- MFDS 폴백: alias 미스일 때만. `lookup_permit` 결과의 `item_eng_name`(브랜드) + `main_ingr_eng`(INN)
  로 후보 구성, `source='mfds'`. 실패 시 빈 리스트(프론트는 "입력어 그대로" 후보를 자체 생성 — 4.4).
- 성능: alias 는 in-memory 캐시(기존). MFDS 는 sqlite 캐시(permit 30일 TTL, 기존). debounce 로 호출 억제.

### 4.2 `api/server.py` — `GET /api/foreign/resolve`

```
GET /api/foreign/resolve?q=keytru
→ 200 { "query": "keytru", "candidates": [ {slug, brand, inn, display, matched_as, source}, ... ] }
```

- q 공백/1자 이하면 `{candidates: []}`.
- 예외/미설정(MFDS 키 없음)이어도 alias 결과는 반환(부분 실패 격리).

### 4.3 프론트 — 타입어헤드 UI (`page.tsx`, `foreign.ts`)

- `foreign.ts`: `resolveIdentity(q) -> Promise<Candidate[]>` fetcher.
- `page.tsx`:
  - 입력 `onChange` → 300ms debounce → `resolveIdentity` 호출 → `identityCandidates` 상태.
  - 기존 "이력 매칭"(searchResults) 드롭다운 **위에** 해석 후보 섹션 추가:
    ```
    ┌───────────────────────────────┐
    │ ✓ KEYTRUDA                    │   ← 후보(해석)
    │   성분 pembrolizumab · 브랜드입력  │
    ├───────────────────────────────┤
    │ 이력: keytruda (7개국) ↵        │   ← 기존 이력
    └───────────────────────────────┘
    ```
  - 후보 클릭 → `handleSelectIdentity(candidate)`: `selected = {query: slug, canonical, identity:{slug,brand,inn}}`
    설정 + 드롭다운 닫기. 확정 identity 는 `selected.identity` 로 보관.
  - Enter/직접검색(handleNewSearch): 후보가 있으면 첫 후보 확정, 없으면 기존처럼 raw 캐시조회
    (하위호환) — 단 이때 `identity` 는 undefined.

### 4.4 검색 plumbing — 확정 identity 로 검색

- `foreign.ts searchForeignLive(query, countries?, onProgress?, identity?)` — `identity?:{slug,brand,inn}`
  추가. POST body 에 `identity` 포함.
- `runLiveSearch` / `researchCountry` 는 `selected.identity` 를 전달.
- `api/server.py POST /api/foreign/search`: body 의 `identity` 를 `search_all` 로 전달.
- `foreign_price_agent.search_all(query, countries, reference_date, identity=None)`:
  - identity 있으면 스크레이퍼 term = `identity.brand`(기본). 없으면 기존 `query`.
  - `search_one_country` → 스크레이퍼 `run(term)`.
  - **brand→INN 폴백**: 스크레이퍼가 0건 반환 & `scraper.FALLBACK_TO_INN` True & `identity.inn` 존재 →
    `run(identity.inn)` 1회 재시도. 기본 US(UsMicromedexScraper.FALLBACK_TO_INN=True) 활성,
    나머지는 False(검증 후 확장). BaseScraper 기본 `FALLBACK_TO_INN=False`.
  - DB 저장의 `query_name` 은 **canonical slug**(identity.slug)로 통일 — 브랜드/성분 입력이 같은
    캐시 레코드로 집계(기존 canonical 정책과 정합).

## 5. 하위 호환 · 엣지

- identity 없는 요청(구버전 프론트, 직접 API): 기존과 동일하게 raw query 로 전 스크레이퍼 검색.
- MFDS 키 미설정/네트워크 실패: alias 결과만. 후보 0건이면 프론트가 "입력어 그대로 검색" 후보를 만들어
  기존 흐름 유지(identity 없음).
- 미등록 약제: MFDS 폴백 후보 표시. 그래도 미스면 raw 검색.
- 성능: resolve 는 debounce + 캐시. 검색 term 폴백은 0건일 때만 → 정상 케이스 지연 없음.

## 6. 범위 밖 (YAGNI)

- 국내 약가 검색창(이 스펙은 해외 약가 한정 — 국내는 별도).
- MFDS 영문 브랜드 완전 매칭 보장(권위 소스 best-effort, 미스 시 raw 폴백).
- 전 국가 brand→INN 폴백(초기엔 US 만; 검증 후 opt-in 확장).

## 7. 테스트

- `agents/drug_identity.py`: keytruda→(brand=keytruda, inn=pembrolizumab), pembrolizumab→동일 slug,
  substring "keytru"→후보 포함, 미등록어→alias 빈 결과(MFDS 는 모킹).
- `GET /api/foreign/resolve`: 200 + candidates 구조, 빈 q → [].
- `search_all(identity=...)`: brand term 전달 확인, 0건 시 INN 폴백 호출(US 스크레이퍼 모킹).
- 회귀: identity 없이 호출 시 기존 동작 유지.
