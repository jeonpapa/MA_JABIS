# DrugEnrichmentAgent 규칙

## 역할
국내 약제의 **허가일**, **용법용량**, **RSA(위험분담제) 여부**, **ATC 코드**, **분류(전문/일반/희귀)**, **포장단위**, **보관조건** 등 권위 데이터를 공식 출처에서 수집하고, 현재 상한금액과 결합해 **일/월/연 치료비용**을 산출한다. 결과는 `drug_enrichment` 테이블에 캐싱.

## 데이터 소스 우선순위 (2026-04-27 개정)

**1차: 식약처 공공데이터 API** (실측·권위)
- `agents/scrapers/kr_mfds_permit.py` — 의약품 제품 허가정보 (`DrugPrdtPrmsnInfoService07/getDrugPrdtPrmsnDtlInq06`)
- `agents/scrapers/kr_mfds_patent.py` — 의약품 특허정보 (`MdcinPatentInfoService2/getMdcinPatentInfoList2`)
- 환경변수: `MFDS_PATENT_API_KEY` (data.go.kr 발급)

**2차: drug_enrichment (Perplexity sonar / health.kr)**
- 1차에서 미매칭 (negative test) 시 fallback
- dose_schedule, daily_dose_units, cycle_days 등 산출 보조

**3차: inherited_generic / default_heuristic**
- 동일 성분 다른 SKU 의 enrichment 상속 또는 1정/일 추정

## 필수 준수 규칙

- **MUST**: `approval_date`, `usage_text` 는 MFDS Permit API 결과가 있으면 우선 채택. drug_enrichment 의 Perplexity 결과는 보조.
- **MUST**: `enrichment_source` 라벨로 출처 투명화
  - `mfds_permit` — MFDS API 1차
  - `mfds_permit + direct` — MFDS + drug_enrichment 둘 다
  - `direct` — drug_enrichment 만
  - `inherited_generic:<donor>` — 동일 성분 다른 SKU 상속
  - `default_heuristic` — 1정/일 추정
- **MUST**: RSA 판정은 `agents/kr_rsa_registry.py` (curated JSON) 우선, drug_enrichment 의 Perplexity 추정은 fallback.
- **MUST NOT**: 확인되지 않은 RSA 여부를 추측으로 `is_rsa=1` 처리. 불확실하면 NULL.
- **MUST NOT**: 허가일을 추측. MFDS API + drug_enrichment 모두 없으면 null.
- **MUST NOT**: 용법용량이 "필요시 투여"(as needed)인 약제에 연간 치료비를 계산.

## MFDS Permit API 응답 활용 매핑

| API 필드 | 매핑 | 비고 |
|---|---|---|
| `ITEM_PERMIT_DATE` | `approval_date` (YYYY.MM.DD) | 식약처 공식 허가일 |
| `UD_DOC_DATA` (XML) | `usage_text` (parse_doc_xml) | 용법용량 권위 텍스트 |
| `EE_DOC_DATA` | `mfds_permit.effect_text` | 효능·효과 |
| `NB_DOC_DATA` | `mfds_permit.caution_text` | 사용상 주의사항 |
| `ATC_CODE` | `mfds_permit.atc_code` | WHO ATC 분류 |
| `ETC_OTC_CODE` | `mfds_permit.etc_otc` | '전문의약품'/'일반의약품' |
| `MAIN_ITEM_INGR` | `mfds_permit.main_ingr` | 주성분 (M코드 prefix 포함) |
| `MATERIAL_NAME` | `mfds_permit.material_name` | 총량/분량/규격 |
| `STORAGE_METHOD` | `mfds_permit.storage_method` | 보관조건 |
| `PACK_UNIT` | `mfds_permit.pack_unit` | 포장단위 |
| `RARE_DRUG_YN` | `mfds_permit.rare_drug_yn` | 희귀의약품 |
| `NEWDRUG_CLASS_NAME` | `mfds_permit.newdrug_class` | 신약 분류 |
| `REEXAM_TARGET/DATE` | `mfds_permit.reexam_*` | 재심사 |
| `CHANGE_DATE` | `mfds_permit.change_date` | 최종 허가변경일 |
| `ENTP_NAME` | `mfds_permit.permit_holder` | 허가권자 |
| `EE/UD/NB_DOC_ID` | `mfds_permit.*_doc_url` | PDF 다운로드 |

## 변형 검색 (MFDS API 정확매칭 한계 대응)

MFDS API 는 정확매칭 — 한글 표기/괄호/함량 표기 차이로 0건 반환되는 경우 다단계 fallback. 모듈 내장.

**우선순위** (hit 률 높은 순, 첫 hit 즉시 break):
1. **괄호 이전 prefix** — `'옵디보주100mg(니볼루맙)_(0.1g/10mL)'` → `'옵디보주100mg'`
2. **한글 변형** — `밀리그람 ↔ 밀리그램`, `그람 ↔ 그램`
3. **원본 풀 표기** (괄호 포함)
4. **함량 단위 직전 prefix** — `'옵디보주'`
5. **brand-only 한글 prefix** — `'옵디보'`
6. **ingredient fallback** — `main_item_ingr=ingredient` 로 조회

**EDI 코드 우선** — `lookup_permit(item_name, edi_code=insurance_code)` 시 EDI 코드 정확매칭 먼저 시도.

## 캐싱 정책

- **MFDS Permit cache** (`mfds_permit_cache`): item_seq PK, item_name 인덱스, 30일 TTL
- **MFDS Patent cache** (`mfds_patent_cache`): item_name+patent_no+page_gb_nm PK, 90일 TTL
- **drug_enrichment** (Perplexity): normalized_name PK, 30일 TTL
- 빈 결과도 sentinel row 로 캐싱하여 negative case 에서 재조회 폭주 방지

## RSA 4대 유형 (분류 키)

| rsa_type | 의미 |
|---|---|
| `expenditure_cap` | 총액제한형 (예상 매출액 초과 시 환급) |
| `refund` | 환급형 (일정액 초과 매출 환급) |
| `utilization` | 사용량-약가 연동형 |
| `conditional` | 조건부 급여 (성과기반·근거생성) |
| `combined` | 복합 유형 |

## 용법용량 파싱 스키마

```json
{
  "dose_schedule": "continuous | cycle | as_needed",
  "daily_dose_units": 1.0,
  "cycle_days": 21,
  "doses_per_cycle": 1.0
}
```

### 계산 공식

- `continuous`: 일치료비 = current_price × daily_dose_units
- `cycle`: 주기치료비 = current_price × doses_per_cycle, 연치료비 = 주기치료비 × (365 / cycle_days)
- `as_needed`: 계산 스킵, `"-"` 표시
- **체중·BSA 기반 (mg/kg, mg/m²)**: `_compute_weight_bsa_daily_cost()` — 표준 환자 (60kg, 1.7m² DuBois) 기준

## 절대 금지

- MFDS API 키를 코드 하드코딩 (`config/.env` 의 `MFDS_PATENT_API_KEY` 만 사용)
- 허가일 추측 (없으면 null + UI 에서 '—' 표시)
- RSA 근거 없는 단정
- usage_text 가 적응증 텍스트 (효능 정보) 인지 미검증 — `_is_dosing_text()` 휴리스틱 필수 적용
