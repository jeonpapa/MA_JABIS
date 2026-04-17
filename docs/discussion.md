# 설계 논의 기록 — 일관성 보존용

각 결정의 **확정일 / 사유 / 적용 범위** 를 기록한다. 이후 작업이 이 문서와 충돌하면 먼저 이 문서를 확인하고, 필요 시 갱신한 뒤 진행한다.

---

## 2026-04-16 — 허가사항(Approval & HTA) 데이터 모델: 적응증(indication) 단위

### 결론

**브랜드 단위가 아닌 적응증 단위로 수집·평가한다.**

키트루다처럼 single brand에 다수 적응증을 가진 제품은, 적응증별로 허가 시점·근거 시험·HTA 평가 결론이 모두 다르다. 한국 급여 런칭 협상의 핵심 근거는 "A8 국가에서 **해당 적응증이** 어떻게 허가받고 어떤 평가를 받았는가" 이므로, 브랜드로 뭉치면 협상 논리가 깨진다.

---

## 2026-04-16 — 적응증 식별: FDA Indications and Usage 마스터 + 검색 시 구조화

### 결론

ICD-10은 line-of-therapy / biomarker / 병기 같은 임상 맥락을 표현하지 못하므로 적응증 식별자로 부적합.

대신 **FDA "Indications and Usage" 라벨을 검색 시점에 LLM으로 구조화하여 별도 DB에 적재**한다. FDA 라벨이 가장 정형화돼 있고 line-of-therapy / biomarker / histology가 명시되어 마스터로 적합. 다음 검색부터는 DB hit (cache-as-DB 원칙).

---

## 2026-04-16 — 매칭 키와 가변 필드 분리 (anchor / variant 2-테이블)

### 결론

**같은 임상이라도 국가별로 허가 범위가 다르다** (예: FDA = PD-L1 all comer 승인, EMA = PD-L1 TPS≥1% 로 좁혀 승인). 따라서 단일 테이블에 FDA 스키마를 그대로 넣고 다른 국가에 강제 매핑하면 안 된다.

**Anchor 테이블 (`indications_master`) — 적응증의 본질, 국가 무관:**
- `indication_id`, `product`
- `disease`, `stage`, `line_of_therapy`, `population`
- `pivotal_trial`

**Variant 테이블 (`indications_by_agency`) — 국가별 좁힘/넓힘 기록:**
- `indication_id`, `agency` (FDA / EMA / NICE / PMDA / ...)
- `biomarker_label` (FDA: "PD-L1 all comers" / EMA: "PD-L1 TPS ≥1%")
- `combination_label`
- `approval_date`, `label_excerpt`, `label_url`
- `restriction_note` (좁혀진 사유 — CHMP opinion 등)

### 매칭 anchor (5개)

다음 5개 필드가 모두 일치하면 같은 `indication_id` 로 묶는다:

1. `pivotal_trial`
2. `disease`
3. `stage`
4. `line_of_therapy`
5. `population`

→ biomarker / combination이 국가별로 달라도 anchor 5개가 같으면 동일 적응증으로 본다.
→ 차이는 variant row에 그대로 보존되어, 협상에서 "FDA vs EMA가 어디서 갈렸나" 가 한눈에 보인다.

### 명시적 제외

- `dose`, `treatment_duration` 은 variant에 넣지 **않는다** (예: Keytruda 200mg q3w vs 400mg q6w). 이번 범위에서 제외.

---

## 2026-04-16 — Anchor 5 → 6개로 확장 (biomarker_class 추가)

### 결론

Phase 0 검증 결과, **biomarker만 다른 별 적응증이 동일 anchor 5개로 분류되어 충돌**하는 사례 발견 (FDA Keytruda: MSI-H solid tumor vs TMB-H solid tumor — disease/stage/LoT/population 모두 동일). 이는 tissue-agnostic 적응증의 본질적 특성이다.

따라서 anchor 를 5 → 6개로 확장한다. **`biomarker_class` 를 6번째 anchor 로 추가.**

### 6번째 anchor: `biomarker_class`

대분류 카테고리 (LLM 추출 + 정규화):

- `msi_h` — MSI-H 또는 dMMR
- `tmb_h` — TMB-H (≥10 mut/Mb)
- `pdl1_50` — PD-L1 TPS ≥50% (or CPS ≥50)
- `pdl1_1`  — PD-L1 TPS ≥1% (or CPS ≥1)
- `pdl1_pos` — 임계값 미명시 PD-L1 양성
- `her2_pos` / `her2_low` / `egfr_mut` / `alk_pos` / `ntrk_pos` / `braf_v600` 등
- `all_comers` — biomarker 무관 승인
- `null` — biomarker 표현이 본문에 없음 (드물게 발생)

`biomarker_label` (variant 테이블) 은 라벨 원문 그대로 유지. `biomarker_class` 는 매칭·집계용 정규화 키.

### 갱신된 매칭 anchor (6개)

1. `pivotal_trial`
2. `disease`
3. `stage`
4. `line_of_therapy`
5. `population`
6. `biomarker_class` ← **신규**

→ tissue-agnostic 적응증의 별개성을 보존하면서, 같은 임상이라도 국가별 좁힘은 여전히 variant 에 그대로 기록.

---

## 진행 보류 / 명시적 제외

- **시나리오 빌더 (Workbench Phase 1 의 협상 시나리오 A안/B안/C안 기능)** — 2026-04-16 부로 추가 빌딩 보류. 허가사항 데이터 모델 확정 우선.
