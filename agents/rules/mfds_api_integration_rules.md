# MFDS Public Data API 통합 규칙 (2026-04-27 신규)

## 역할

식약처가 공공데이터포털을 통해 공개하는 두 API 를 **모든 국내 약가 enrichment 의 1차 권위 소스**로 통합한다. Perplexity LLM 추정·휴리스틱은 fallback.

## 통합 대상 API

### 1. 의약품 제품 허가정보
- **Service**: `apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService07`
- **Operation**: `getDrugPrdtPrmsnDtlInq06`
- **모듈**: `agents/scrapers/kr_mfds_permit.py`
- **테이블**: `mfds_permit_cache` (item_seq PK, 30일 TTL)
- **공급 데이터**: 허가일, 용법용량(UD_DOC), 효능(EE_DOC), 주의사항(NB_DOC), ATC, 분류, 보관, 포장, 성상, 재심사, 희귀의약품 여부

### 2. 의약품 특허정보
- **Service**: `apis.data.go.kr/1471000/MdcinPatentInfoService2`
- **Operation**: `getMdcinPatentInfoList2`
- **모듈**: `agents/scrapers/kr_mfds_patent.py`
- **테이블**: `mfds_patent_cache` (item_name+patent_no+page_gb_nm PK, 90일 TTL)
- **공급 데이터**: 특허번호, 등록상태(등록/소멸), 만료일(DOMESTIC_END_DATE), 권리자, 발명명, PATENT_GB_CODE

## 인증

- **환경변수**: `config/.env` 의 `MFDS_PATENT_API_KEY`
- 양 API 가 같은 키 공유 (data.go.kr 동일 활용신청에서 발급)
- **MUST NOT**: 키 하드코딩 / git 커밋

## 적용 우선순위 (모든 enrichment 단계 공통)

```
1. MFDS API (실측·권위)
   ├─ approval_date  ← ITEM_PERMIT_DATE
   ├─ usage_text     ← UD_DOC_DATA (XML → text)
   ├─ atc_code, etc_otc, pack_unit, storage 등
   └─ patent_status, patent_expiry_date, substance/secondary 분류

2. drug_enrichment (Perplexity)
   ├─ MFDS 미매칭 시 fallback
   └─ dose_schedule, daily_dose_units, cycle_days 보조

3. inherited_generic / default_heuristic
   └─ 동일 성분 다른 SKU 상속 또는 1정/일 추정

4. price_history 추정 (loe_pattern, KR-RULE-009)
   └─ patent_status 만 fallback. approval/usage 는 fallback 안함.
```

## 변형 검색 정책 (정확매칭 한계 대응)

MFDS API 는 정확매칭 — DB product_name 과 MFDS ITEM_NAME 사이 표기 차이로 0건 반환되는 케이스가 흔함. 두 모듈 모두 동일한 `_name_variants()` + `_try_variants()` 전략 적용:

| 우선순위 | 변형 | 예시 |
|---|---|---|
| 1 | 괄호 이전 prefix | `'옵디보주100mg(니볼루맙)_(0.1g/10mL)'` → `'옵디보주100mg'` |
| 2 | 한글 변형 | `밀리그람 ↔ 밀리그램` |
| 3 | 원본 풀 표기 | (그대로) |
| 4 | 함량 단위 직전 | `'옵디보주'` |
| 5 | brand prefix only | `'옵디보'` |
| 6 | ingredient fallback | `main_item_ingr=ingredient` (permit), `ingr_name=ingredient` (patent) |

**EDI 코드** (insurance_code) 가 있으면 1순위로 시도 — 가장 정확한 매칭.

## 캐시 의미론 — 실패 vs 빈 결과 (2026-10-01)

**사고**: `fetch_raw` 가 전송 실패·JSON 파싱 실패·resultCode≠00 을 모두 `[]` 로 돌려 '품목 없음' 과 구분이 없었다. `lookup_permit` 은 그 `[]` 에 30일 부정 sentinel(`__EMPTY__:<이름>`) 을 썼고, 읽기는 ORDER BY 없는 `LIMIT 1` 이라 sentinel 이 실측 행을 가릴 수 있었다 (키트루다 `201501487` 2026-06-24 실측 ↔ 2026-10-01 13:00 실패 실행이 쓴 sentinel 이 같은 item_name 으로 공존). 특허 모듈도 같은 패턴(`patent_no='__EMPTY__'`, 90일). 07:50 스모크가 `/api/domestic/price-changes?q=키트루다` 로 매일 이 경로를 밟는다.

**규칙** — 두 모듈이 공통 `agents/scrapers/mfds_api.py::request_items` 를 쓴다:

| 응답 | 의미 | 처리 |
|---|---|---|
| resultCode `00` + items | 실측 | 적재 (`source='mfds_api'`) |
| resultCode `00` + items 없음, 또는 `03`(포털 표준 NODATA_ERROR) | **API 가 답했고 0건** | 부정 캐시 기록 (`source='miss'`) |
| 전송 실패 / 본문이 JSON 아님(게이트웨이 XML 오류 봉투·HTML) / `header.resultCode` 없음 / 그 외 코드 | **실패** | `MfdsApiError(kind=transport\|parse\|gateway\|result_code)` — **캐시에 아무것도 쓰지 않는다** |

- `lookup_permit` 실패 시: TTL 지난 실측 행이 있으면 `source='cache_stale'` (+`error`, `error_kind`) 로 그 행을 돌려준다 — 허가일은 불변인 권위 데이터라 추정값보다 낫다. 없으면 `source='error'`. **`miss` 와 `error` 는 다른 뜻** — 호출자는 `has_permit_data()` / `permit_status()` 로 구분하고, `/api/domestic/price-changes` 는 product 에 `mfds_permit_status` (`ok|stale|miss|error`) 를 올린다. `error` 를 '허가 없음' 으로 표시하지 않는다.
- `lookup_patent` 실패 시: 실측 행 있으면 `source='cache_stale'` 로 판정, 없으면 `source='error'`, `status='unknown'`, `judgment_basis='api_error'`.
- 변형 체인은 **첫 실패에서 중단** — 전송 실패는 나머지 변형도 실패하고(스모크 타임아웃 보호), 실패 뒤의 0건은 '품목 없음' 의 근거가 못 된다.
- 부정 캐시 행은 `raw_json={"negative": true, "tried": [...]}` 마커를 가진다. 구 포맷(`'{}'`) sentinel 은 실패/빈결과 구분이 불가하므로 **부정 캐시로 인정하지 않고 재조회** (`kind='legacy_empty'`). 라이브 DB 의 기존 sentinel(permit 186 / patent 430) 은 정리 전까지 무해하다.
- 읽기 순서 (permit): `ORDER BY (item_seq 가 sentinel 접두사) ASC, fetched_at DESC LIMIT 1` — **실측 행 > sentinel, 같은 급이면 최신**. patent: 실측 행이 하나라도 있으면 sentinel 무시.
- 부정 캐시 TTL = 실측과 같은 30일 (`NEGATIVE_CACHE_TTL_DAYS`). 0건의 주된 원인은 HIRA 표기 ↔ MFDS ITEM_NAME 차이라 시간이 풀어주지 않고, 재조회 1회 = 변형 최대 7회 호출이라 짧게 잡으면 포털 일일 쿼터만 소모한다. 뒤늦게 MFDS DB 에 실리는 신규 허가 품목은 `refresh=True`. 값을 바꾸려면 이 표부터 고친다.
- 검증: `tests/test_mfds_permit_cache.py` (키트루다 실측+sentinel 공존 픽스처 — 네트워크 없음) / `tests/test_mfds_patent_cache.py`. 라이브 확인은 네트워크 + `MFDS_PATENT_API_KEY` 필요: `python -m agents.scrapers.kr_mfds_permit "키트루다주(펨브롤리주맙,유전자재조합)_(0.1g/4mL)" --refresh` → `item_seq=201501487`, `permit_date=2015.03.20`, `source=mfds_api`; 그리고 존재하지 않는 이름으로 `source=miss` + `{"negative": true}` 행이 쓰이는지, 실제 0건 응답이 `00`/`03` 중 무엇인지 로그로 확인.

## 특허 분류 규칙 (LOE 판정용)

### Core Substance (LOE 결정)
**조건**: `PATENT_GB_CODE` 에 `'물질'` 키워드 포함 + secondary modifier 부재

- `물질`, `물질(염)`, `물질(기타)`, `물질물질(염)` 등 → core
- `'조성'/'용도'/'제법'/'결합'/'접합'/'결정형'/'제제'` modifier 가 함께 있으면 → secondary

### 후속 특허로 자동 reclassify (secondary)

| 사유 | 판정 |
|---|---|
| `academic_patentee` | PATENTEE 가 학술기관 (대학교/산학협력단/연구소/Foundation/Institute) |
| `follow_on_modality` | INVN_NAME 에 ADC/biosimilar/이중특이/항체-약물 접합체 키워드 |

### PATENT_GB_CODE 빈 값 fallback (글리벡 등 구약 케이스)

- MFDS DB 에 `PATENT_GB_CODE` 가 비어있는 row 가 존재 (글리벡 100% 빈 값)
- `PAGE_GB_NM='제품특허'` AND `gb_code=''` 인 row 들의 status 만으로 판정
- 모두 소멸 → `만료` (`empty_gb_code_all_expired`)
- 등록 1건이라도 → `유효` (`empty_gb_code_active_product_patent`)

### `PAGE_GB_NM` 필터링 금지

트라스투주맙처럼 원개발사(제넨테크) 가 `기타특허` 로 등재된 활성성분도 LOE 결정에 포함되어야 함. PAGE_GB_NM 은 표시용으로만 사용.

### LOE 판정 결과 분류

| status | 조건 | judgment_basis |
|---|---|---|
| `만료` | core substance 1건 이상 + 모두 소멸 | `all_core_substance_expired` |
| `유효` | core substance 등록 row 1건 이상 + 만료일 미래 | `active_core_substance_patent` |
| `만료` | core 부재 + 빈 GB 코드 제품특허 모두 소멸 | `empty_gb_code_all_expired` |
| `유효` | 빈 GB 코드 제품특허 등록 row | `empty_gb_code_active_product_patent` |
| `unknown` | core 부재 + 빈 GB 코드 row 없음 | `no_core_substance_patent` |

## API 응답 구조

### `/api/domestic/price-changes` 응답 product 객체에 추가된 필드

```json
{
  "patent_status": "만료" | "유효" | null,
  "patent_expiry_date": "2026-07-24" | null,
  "patent_loe_date_inferred": "2015.12.30" | null,  // price_history fallback 시
  "patent_source": "mfds_api" | "price_history" | null,
  "patent_source_note": "MFDS 실측 — 물질특허 N건 활성/M건 만료",
  "patent_substance_patents": [...],   // core substance 상세
  "patent_secondary_patents": [...],   // ADC/조성/학술기관 등 (collapsible)
  "mfds_permit": {
    "permit_date": "2007.09.21",
    "atc_code": "A10BH01",
    "etc_otc": "전문의약품",
    "permit_holder": "(주)종근당",
    "pack_unit": "28정(14정/PTP x 2)",
    "storage_method": "밀폐용기, 실온(1-25℃)보관",
    "rare_drug_yn": "N",
    "newdrug_class": "신약",
    "ud_doc_url": "HTTPS://NEDRUG.MFDS.GO.KR/PBP/CMN/PDFDOWNLOAD/.../UD",
    ...
  } | null
}
```

## UI 노출 정책 (대쉬보드)

- 상세 정보 패널에 **MFDS 실측 배지** + ATC, 분류, 허가권자, 포장, 보관 row 노출
- 특허 상태 row: `MFDS 실측` (파란) / `가격 history 추정` (앰버) source 라벨
- 핵심 물질특허 5건 + 후속 특허 collapsible (분류 사유 라벨 포함)
- `MFDS 조회 ↗` 외부 링크: `https://nedrug.mfds.go.kr/searchPatent?itemName=<제품명>`

## CLI

```bash
# 단일 약 patent 조회
python -m agents.scrapers.kr_mfds_patent "키트루다주" --ingredient 펨브롤리주맙
python -m agents.scrapers.kr_mfds_patent "허셉틴주150밀리그람" --refresh

# 단일 약 permit 조회
python -m agents.scrapers.kr_mfds_permit "자누비아정100밀리그램"
python -m agents.scrapers.kr_mfds_permit "자누비아정100밀리그램" --raw   # 원본 응답
```

## 테스트 시 검증 baseline (2026-04-27 기준)

| 약 | patent_status | patent_expiry_date | permit_date | ATC |
|---|---|---|---|---|
| 자누비아정100mg | 만료 | None (모두 소멸) | 2007.09.21 | A10BH01 |
| 글리벡필름코팅정100mg | 만료 (empty_gb_code) | None | 2006.11.30 | L01EA01 |
| 키트루다주 | 유효 | 2035-08-17 | 2015.03.20 | L01XC18 |
| 옵디보주100mg | 유효 | 2026-07-24 (임박) | 2015.03.20 | L01XC17 |
| 허셉틴주150 | 만료 | None (트라스투주맙 만료) | 2014.02.04 | L01FD01 |
| 빅타비정 | 유효 | — | 2019.01.18 | J05AR20 |
| 린파자정100mg | 만료 | — | 2019.10.29 | L01XK01 |
| 오로살탄정10/160mg | 만료 | None | — | C09DB01 (예상) |

**회귀 시 점검**: `agents/scrapers/kr_mfds_patent.py::summarize` 의 분류 로직 (core/secondary/empty_gb fallback) 변경 여부 확인.

## 절대 금지

- API 키 하드코딩
- `PAGE_GB_NM='제품특허'` 만 필터하여 기타특허로 등재된 원개발사 활성성분 누락
- `_is_core_substance` 에서 `_SECONDARY_MODIFIERS` 기반 키워드 검출 무시 (false positive 발생)
- 정확매칭 실패 시 즉시 unknown 처리 (variant fallback 6단계 모두 시도 필수)
- MFDS API 미매칭 시 approval_date / usage_text 를 추정값으로 채움
- API 실패(전송/파싱/resultCode≠00·03)를 0건으로 취급해 부정 sentinel 기록 — 실패는 `MfdsApiError`, 캐시 미기록
- 캐시 읽기에서 sentinel 이 실측 행을 가리게 두기 (ORDER BY 없는 `LIMIT 1`)
- `source='error'` 를 `'miss'` 와 같은 뜻('허가 없음') 으로 소비
