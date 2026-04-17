# ForeignApprovalAgent 규칙 — 해외허가사항 적응증 단위 수집·구조화

> **구현체**: `agents/foreign_approval_agent.py`
> **연계 모듈**: `agents/hta_scrapers/{us_fda,eu_ema,jp_pmda,kr_mfds,uk_mhra,au_tga}.py`, `agents/research/indication_structurer.py`, `agents/db.py`
> **DB 테이블**: `indications_master` + `indications_by_agency` (+`date_source` 컬럼)
> **연관 규칙**: MFDS 공식 승인일 결정은 `agents/rules/kr_mfds_approval_agent_rules.md` 에서 별도 관리

---

## 0. 에이전트 역할

해외 규제기관(FDA/EMA/PMDA) 및 국내 규제기관(MFDS/식약처) 의 라벨에 명시된 적응증을 **brand 단위가 아닌 indication 단위**로 수집·구조화·정합한다. KR 급여 협상 시 "FDA 와 EMA 의 허가범위 차이" 를 즉시 비교 가능하도록 anchor 매칭으로 동일 indication 은 1개 master row 에 여러 agency variant 로 묶어 저장한다.

본 에이전트가 만드는 데이터는 후속 단계 (HTA 평가 매칭 / 가격 협상 시뮬레이션) 의 **anchor** 가 되므로 **데이터 무결성** 이 최우선 원칙.

---

## 1. 핵심 데이터 모델

### `indications_master` (anchor)
```
indication_id (PK)   — slug: <product>_<disease>_<lot>_<stage>_<bio>_<combo>_<trial>
product
pivotal_trial        — KEYNOTE-XXX 등. 본문에 없으면 None (EMA SmPC 는 거의 None)
disease              — 약어 (NSCLC, RCC, MEL, HNSCC, UC, CRC, TNBC, EC, HCC, BTC,
                       MCC, CC, MPM, GC, cHL, cSCC ...)
stage                — metastatic / advanced / locally advanced / unresectable /
                       resectable / adjuvant / neoadjuvant / recurrent
line_of_therapy      — 1L / 2L / 3L+ / adjuvant / neoadjuvant / perioperative
population           — adult / pediatric / adult_and_pediatric (연령 cutoff 괄호)
biomarker_class      — msi_h / tmb_h / pdl1_50 / pdl1_10 / pdl1_1 / pdl1_pos /
                       her2_pos / her2_neg / her2_low / egfr_mut / alk_pos /
                       ros1_pos / ntrk_pos / braf_v600 / kras_g12c /
                       brca_mut / hrd_pos / hrr_mut / all_comers / null
                       (BRCA/HRD/HRR 은 PARP inhibitor 핵심 바이오마커)
title
fda_indication_code  — FDA 라벨 1.x (또는 sub-split 시 1.x_a)
```

### `indications_by_agency` (variant)
```
indication_id (FK)
agency               — FDA / EMA / PMDA / MHRA / ...
biomarker_label      — 라벨 원문 그대로 ("PD-L1 TPS ≥50%", "CPS ≥10" 등)
combination_label    — "monotherapy" / "in combination with axitinib" 등
approval_date
label_excerpt        — 본문 sub-block (검증·재현용)
label_url
restriction_note     — "following complete resection" 등
raw_source           — JSON {source_code, label_header, source_agency}
```

---

## 2. 파이프라인

```
drug
  ↓
[USFDAScraper.search]      openFDA Drug Label API
  ↓
[_split_indications]       1.x 섹션 분리 + 각 섹션 내 "BRAND ... is indicated" sub-split (1.x_a)
  ↓
[structure_indication]     LLM 6-anchor + variant JSON
  ↓
[upsert_master + agency]   FDA row 적재
  ↓
[EUEMAScraper.search]      EPAR HTML → SmPC PDF → section 4.1
  ↓
[_split_indications]       disease header 기준 분리
  ↓
[structure_indication]     LLM (agency="EMA")
  ↓
[find_matching_indication] 기존 FDA master 와 anchor 매칭
  ├─ 매칭됨 → agency row 만 추가 (MATCH)
  └─ 미매칭 → master + agency 둘 다 추가 (NEW)
  ↓
[JPPMDAScraper.search]     YJ 코드 → GeneralList → 添付文書 PDF → section 4 「効能又は効果」
  ↓
[_extract_section_4]       2컬럼 page.crop + ○/〇 bullet split (noise marker 로 truncate)
  ↓
[_split_indications]       줄 단위 PMDAIndication (일본어 본문)
  ↓
[structure_indication]     LLM (agency="PMDA", 일본어 prompt 인식)
  ↓
[find_matching_indication] 기존 FDA/EMA master 와 anchor 매칭 → MATCH 또는 NEW
```

---

## 3. anchor 매칭 규칙 (`db.find_matching_indication`)

**목적**: 동일 indication 의 FDA/EMA variant 가 같은 master 에 모이도록.

### 필수 anchor
- **disease**: 반드시 일치 (대소문자 무시)
- **biomarker_class**: 반드시 일치 (`pdl1_50` ≠ `pdl1_1` ≠ `all_comers`)

### 우선순위 (tier)

상위 tier 에서 **정확히 1건** 매칭되면 그 indication_id 채택. **0건 또는 2건 이상**이면 다음 tier 로.

| Tier | 조건 |
|------|------|
| 1 | disease + biomarker + line_of_therapy + stage |
| 2 | disease + biomarker + line_of_therapy |
| 3 | disease + biomarker + stage (LoT 둘 다 없을 때만) |
| 4 | disease + biomarker (LoT 없을 때만 — 가장 약한 매칭) |

### LoT 하드 제약
EMA 가 `adjuvant` 인데 FDA 가 `1L` 인 indication 이면 절대 매칭하지 않는다. 임상적으로 다른 setting.

---

## 4. slug 규칙 (`indication_structurer.make_indication_id`)

```
slug = <product>_<disease>_<line_of_therapy>_<stage>_<bio>_<combo>_<trial>
```

- 빈 필드는 건너뜀
- `bio` 가 `all_comers` / `null` / None 이면 slug 에 미포함
- `combo` 는 `combination_label` 을 정규화한 토큰 (`mono`, `axitinib`, `lenvatinib`, `trastuzumab`, `carbo_pacl`, `gem_cis`, `pemetrexed`, `crt`, `chemo`, `ev`, `bevacizumab` ...)
- 동일 anchor + 다른 병용약은 별개 indication 이 되어야 하므로 `combo` 는 slug 충돌 회피 필수

---

## 5. 절대 금지

- **slug 에 anchor/combination 없이 product+code 만 사용** — 충돌 시 데이터 손실
- **anchor 매칭 시 LoT 무시** — `adjuvant` 와 `metastatic` 이 합쳐지면 협상 근거가 무너짐
- **LLM 응답에서 라벨 본문에 없는 값을 추론** — 모든 anchor 는 본문 명시만
- **biomarker 가 본문에 없는데 임의로 `all_comers` 로 추측** — 본문에 PD-L1/MSI 등 표현이 있으면 반드시 분류
- **단일 LLM 호출에서 multi-sub-indication 통합 추출** — 각 sub-indication 단위로 분리 후 호출 (FDA `_split_subindications`, EMA `_split_indications` 사용)
- **wipe 없이 slug 규칙 변경 후 재빌드** — 구슬릭 row 가 orphan 으로 남음
- **FDA 만 적재된 상태에서 매트릭스 출력** — 비교 가치 0. 최소 EMA 까지 묶어서 보고
- **MFDS `approval_date` 를 공식값으로 신뢰** — `_build_mfds` 가 저장하는 값은 브랜드 최초 허가일 추정. 반드시 `scripts/apply_mfds_official_dates` 로 적응증별 공식일로 교체할 것. 교체 전 data 는 `date_source='unverified_estimate'` 로 표시되어야 함

---

## 6. CLI 사용법

```bash
# 전체 빌드 (FDA + EMA)
.venv/bin/python -m agents.foreign_approval_agent build pembrolizumab keytruda

# 특정 기관만
.venv/bin/python -m agents.foreign_approval_agent build pembrolizumab keytruda --agencies EMA

# 깨끗한 재빌드
.venv/bin/python -m agents.foreign_approval_agent build pembrolizumab keytruda --wipe

# 일부 적응증만
.venv/bin/python -m agents.foreign_approval_agent build pembrolizumab keytruda --codes 1.4_a,1.4_c

# 커버리지 매트릭스
.venv/bin/python -m agents.foreign_approval_agent matrix keytruda
.venv/bin/python -m agents.foreign_approval_agent matrix keytruda --format json
```

기존 스크립트 (`scripts/build_indications.py`, `scripts/build_ema_indications.py`) 는 본 에이전트의 단일 기관 호출 형태이며 그대로 유지 (단발 디버깅 용).

---

## 7. 검증 기준 (종양학 baseline)

신규 기능 추가 / 프롬프트 수정 후 다음을 확인:

### Keytruda (pembrolizumab, product_slug=keytruda)
1. **FDA**: 39개 sub-indication 모두 ok, 0건 실패 (1.1_a, 1.1_b, ..., 1.19)
2. **EMA**: 31개 indication 모두 ok, 0건 실패
3. **slug 충돌**: master row 수 = 기관 indication 수 합 (현재 51 = FDA 39 + EMA 31 - 매칭 19)
4. **매칭률**: EMA 기준 ≥ 50% (현재 19/31 = 61%)

### Welireg (belzutifan, product_slug=welireg)
- FDA 3 + EMA 2 → masters=3 / both=2 / FDA-only=1
- VHL 이 syndrome 으로 disease 분류돼 FDA+EMA 매칭되어야 함 (biomarker 로 빠지면 회귀)

### Lynparza (olaparib, product_slug=lynparza)
- FDA 8 + EMA 6 → matched ≥ 4 (≥67%)
- biomarker_class 가 `brca_mut` / `hrd_pos` / `hrr_mut` 으로 분류되어야 함 (null/all_comers 면 회귀)
- disease 가 `OC` / `BC` / `PAAD` / `mCRPC` 표준 약어로 통일 (풀네임 섞이면 회귀)

### Lenvima (lenvatinib, product_slug=lenvima)
- FDA 5 + EMA 3 → matched ≥ 2
- `kisplyx` (RCC 전용 EU 브랜드) 는 별도 product_slug 로 관리 — Lenvima EMA 에 RCC 없음이 정상

### 회귀 감지 체크리스트
1. **biomarker 정확도**: 라벨에 PD-L1/MSI/BRCA 명시된 sub-indication 은 `all_comers` 로 떨어지면 안 됨
2. **anchor 매칭의 hard fail**: `adjuvant` 와 `metastatic` 같은 master 에 들어가면 즉시 회귀
3. **disease 정규화**: `_DISEASE_ALIASES` 에 있는 풀네임이 그대로 disease 에 저장되면 회귀 (indication_structurer 의 `normalize_disease` 실행 확인)
4. **FDA 브랜드 매칭**: sitagliptin 처럼 같은 generic 에 여러 NDA(JANUVIA/JANUMET/ZITUVIMET) 가 있는 경우 `foreign_approval_agent._build_fda` 가 product_slug 일치 레코드를 우선 선택

### 알려진 한계
- **openFDA drug label** 은 백신/biologics 미포함 (Gardasil 등 FDA 검색 0건)
- **비-종양학 라벨**: T2DM/CVD 등은 EMA SmPC 불릿 구조가 달라 disease 추출 품질 저하 — 현 버전은 종양학 최적화

---

## 8. 향후 확장

- ~~**PMDA**: 일본 PMDA 添付文書~~ → **구현 완료** (`agents/hta_scrapers/jp_pmda.py`).
    - YJ 코드 기반 URL 해결 (`iyakuDetail/GeneralList/<YJ>` → `ResultDataSetPDF/...`)
    - 添付文書 2컬럼 PDF → `page.crop(left/right)` 로 컬럼 분리 후 ○/〇 bullet split
    - 일본어 disease / biomarker 는 LLM prompt 의 일본어 매핑표 + `_DISEASE_ALIASES` 로 정규화
- **Health Canada**: Product Monograph
- **MHRA**: post-Brexit UK SmPC (현재 EMA 와 동일 본문이지만 향후 분기)
- **TGA**: 호주 PI

기관 추가 시: scraper 신설 → `_build_<agency>` 메서드 추가 → `SUPPORTED` 튜플 갱신 → 본 룰 §1·§2 표 갱신.

### PMDA 운영 노트
- YJ 코드 신규 제품 추가: PMDA 검색(`https://www.pmda.go.jp/PmdaSearch/iyakuSearch/`) → 販売名 조회 → YJコード 컬럼 확인 → `PMDA_YJ_CODES` dict 에 매핑.
- 2컬럼 PDF 는 linear 추출 시 좌/우 교차 interleave 로 section 4 가 파편화됨. 반드시 `page.crop` 로 컬럼 분리.
- 承認年月日 은 西暦 또는 和暦(令和/平成) 두 형식 모두 대응. 일부 제품은 PDF 내 날짜가 누락 → `None` 허용.

### MFDS 운영 노트 (중요 — 현재 수동 단계 존재)
- **현행 라벨 스냅샷 적재만 자동**: `_build_mfds` 는 `MFDS_ITEM_SEQ` 하드코딩 dict 기반. 신규 약물 추가 시 itemSeq 를 `https://nedrug.mfds.go.kr/searchDrug` 에서 수동 조회하여 `kr_mfds.py:MFDS_ITEM_SEQ` 에 추가해야 한다.
- **공식 승인일 교체는 별도 스크립트**: `_build_mfds` 내부에서 `apply_mfds_official_dates` 를 호출하지 않는다. 빌드 후 반드시 다음 수동 실행 필요:
  ```bash
  python -m scripts.apply_mfds_official_dates --product <slug> --apply
  ```
- **키워드 사전 커버리지 확인**: 신규 product 의 `indications_master.disease` 값이 `kr_mfds_indication_mapper.DISEASE_KR` 에 없으면 매칭률 0. 빌드 직후 sanity check 필수.
- 상세 매칭 알고리즘 및 실수 사례: `agents/rules/kr_mfds_approval_agent_rules.md`

### 알려진 자동화 gap (TODO)
- `ForeignApprovalAgent.build()` 가 MFDS 단계 완료 후 자동으로 공식일 교체를 호출하도록 통합 — 현재 수동
- 신규 약물의 MFDS itemSeq 자동 해결 (nedrug 검색 API/HTML 분석)
- `DISEASE_KR` / `COMBO_KR` / `BIOMARKER_KR` 누락 키워드 LLM 자동 제안 + 검증 로그
- `apply_mfds_official_dates` 가 확정 못 한 `unverified_estimate` row 는 QualityGuard 큐에 적재하여 다음 사이클 재시도
