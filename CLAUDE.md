# MA AI Dossier — 진입점

MSD Korea MA 팀의 국내·해외 약가·허가·HTA 모니터링 자동화 플랫폼.
상세 규칙은 각 `agents/rules/*.md` 참조. 이 파일은 **링크 맵 + 최소 원칙** 만 유지한다.

---

## 에이전트 아키텍처

```
사용자 / 스케줄러
    │
    ▼
[OrchestratorAgent] ─── 요청 분석 · 룰 비교 · 작업 분배
    │
    ├─▶ [DomesticPriceAgent]       국내 약가 (HIRA)
    ├─▶ [HiraAgent]                급여 SOP 평가
    ├─▶ [ForeignPriceAgent]        해외 약가 (JP/IT/FR/CH/UK/DE/US)
    ├─▶ [ForeignApprovalAgent]     FDA/EMA/PMDA/MFDS/MHRA/TGA 적응증 단위 허가
    │     └─▶ [KR MFDS 공식일 교체]  변경이력 diff — itemSeq 자동조회 + 캐시
    ├─▶ [DrugEnrichmentAgent]      성분·ATC·mechanism 보강
    ├─▶ [MarketIntelligenceAgent]  뉴스·컨센서스 수집
    ├─▶ [ReviewAgent]              LLM 리뷰 (다수결)
    └─▶ [DashboardAgent]           HTML 대쉬보드 · 워크벤치 생성
    │
    ▼
[QualityGuardAgent] ── 상시 감시 + 일일 리뷰 + 회귀 탐지 + 개선 제안
[RuleComplianceAgent] ── 메모리 ↔ 런타임 증거 대조 (매일 05:30, QG 직전)
```

---

## 규칙 맵 (권위 소스)

| 영역 | 파일 |
|------|------|
| Orchestrator / 작업 분배 | `agents/rules/orchestrator_rules.md` |
| Quality Guard (감시·리뷰·제안) | `agents/rules/quality_guard_rules.md` |
| Rule Compliance (메모리 ↔ 런타임 감사) | `agents/rules/rule_compliance_rules.md` |
| 스크레이퍼 공통 | `agents/rules/scraper_rules.md` |
| 국내 약가 (HIRA Excel) | `agents/rules/domestic_agent_rules.md` |
| 해외 약가 | `agents/rules/foreign_agent_rules.md` |
| HIRA 급여 SOP | `agents/rules/hira_agent_rules.md` |
| 해외 허가 (적응증 단위) | `agents/rules/foreign_approval_agent_rules.md` |
| MFDS 공식 승인일 파이프라인 | `agents/rules/kr_mfds_approval_agent_rules.md` |
| MFDS 공공데이터 API 통합 (특허/허가) | `agents/rules/mfds_api_integration_rules.md` |
| 성분 enrichment | `agents/rules/drug_enrichment_rules.md` |
| Market Intelligence | `agents/rules/market_intelligence_rules.md` |
| Competitor Trends (주 1회 자동 크롤 + LLM 필터) | `agents/rules/competitor_trends_rules.md` |
| Review (LLM 다수결) | `agents/rules/review_agent_rules.md` |

---

## 최소 원칙 (모든 에이전트 공통)

- **단방향 데이터 흐름**: 스크레이퍼 → DB → 대쉬보드. 대쉬보드는 DB만 읽음
- **적응증 단위 수집**: 허가는 브랜드 단위 금지. FDA 1.x / EMA 4.1 / MFDS 번호블록 sub-split 후 anchor(disease+LoT+stage+biomarker+combo+trial) 로 master 통합
- **데이터 출처 구분 필수**: MFDS `approval_date` 는 `date_source` 컬럼으로 `mfds_official`/`unverified_estimate` 명시. 비급여는 `local_price=None` 명시
- **자격증명**: `config/.env` 외 어디에도 하드코딩 금지
- **LLM 판단 애매 시**: 단독 결정 대신 `ReviewAgent` 다수결
- **배포 순서**: 기능 완성 → 로컬 검증 → 웹 배포 (역순 금지)
- **Keytruda baseline**: 모든 신규 스크레이퍼/구조화 로직은 Keytruda 로 최종 검증

---

## 절대 금지

- `msd_only=True` 하드코딩
- `config/.env` git 커밋
- 가격 없을 때 임의 값 반환
- US Micromedex AWP 를 local_price 로 사용 (WAC 만 허용 — factory_ratio 와 double-count)
- injection 에서 `total_mg/unit_mg` ratio 로 pack_count 추론 (농도×volume 이라 volume 반환 → 위험)
- injection 에서 `_extract_mg` (per-mL 농도) 를 daily_cost 분모로 사용 (per-vial 총량 O)
- 기능 미완성 상태에서 웹 배포
- MFDS 변경이력 매칭을 segment-blob / 단순 문자열 매칭으로 처리 (peri/adj/neo 붕괴)
- 허가 master 에 anchor 없이 brand+code 만으로 slug 생성

---

## 과거 실수 (회귀 방지)

- **2026-04-17 MFDS NSCLC adj 오매칭** (2023-12-19 → 2024-05-14): segment-blob 매칭이 peri 문단을 adj 로 인식. 이후 과도 exclude 로 Lynparza BC adj 회귀. 세부: `agents/rules/kr_mfds_approval_agent_rules.md` §8. 회귀 체크는 `QualityGuardAgent.review_codebase()` 가 8개 baseline 자동 검증.
- **MFDS 키워드 누락**: 신규 product 추가 시 `DISEASE_KR` 커버리지 미확인 → disease_layer 비어 매칭 0. 신규 product 추가 시 반드시 `indications_master.disease` 전량을 dict 와 비교.
- **2026-04-21 해외 일일투약비용 분모 오류** (Welireg UK ₩46M/day): `_extract_mg` 가 단위강도(per-tablet 40mg)만 반환, tablet count(90) 무시 → 90× 과대. `_extract_total_pkg_mg(dosage_strength, package_unit)` + sanity cap(₩10M/day) 도입. 세부: `agents/rules/foreign_agent_rules.md` §daily_cost. **신규 스크레이퍼는 Welireg(경구·pack count) + Keytruda(주사·농도×volume) 양쪽 검증 필수**.
- **2026-04-21 A8 조정가 공식 재정영향분석 미일치** (Welireg Excel 대비 -15%~+13% 편차): PriceCalculator 가 국가별 native VAT 제거만 수행, 한국 A8 기준 상수(KR_VAT 10% + KR_DIST_MARGIN 8.69%) uplift 누락. DE 는 특수 공식 사용으로 factory_ratio 불일치. 수정: ① `adjusted_price_krw` 를 per-UNIT KRW 로 통일 (pack 아님), ② `× (1+KR_VAT) × (1+KR_DIST_MARGIN)` 가산, ③ DE factory_ratio=0.6955 로 단순화, ④ pack_count + daily_cost_krw 를 DB 컬럼으로 저장. 회귀 체크: `tests/test_welireg_excel_baseline.py` — Excel 5개국 per-tablet 값과 ±1% 이내 (FX 기간 동일 조건). 세부: `agents/rules/foreign_agent_rules.md` §A8 조정가 공식.
- **2026-04-22 injection 최소단위 오인식 (Keytruda IT/US pack_count 오류)**: `_resolve_pack_count` 가 `total_mg/unit_mg` ratio 로 pack 추론 → injection 에서는 "농도×volume/농도 = volume" 이라 pack_count 가 아닌 volume(예: 4mL)을 반환. IT Keytruda 100mg/4mL 1 vial 이 pack=4 로 등록, per-vial adj 가 1/4 로 과소계상. US 도 `4 ml 2s` → pack=8 (실제 2). 수정: ① `_resolve_pack_count` 에서 ratio 추론을 **form_type=='oral' 에서만 허용**, ② `_extract_per_unit_mg(form_type, ...)` 신규 헬퍼 — injection 은 per-vial 총 mg 복원 (농도×volume), oral 은 per-tablet mg, ③ daily_cost 분모를 form-aware unit_mg 로 교체, ④ 전체 58 row 중 49 row form_type/pack_count/adj/daily 재계산. 원칙: **최소단위는 form_type 이 결정한다** (oral=tablet, injection=vial). 세부: `agents/rules/foreign_agent_rules.md` §최소단위.
- **2026-04-21 US Micromedex AWP 사용 오류** (Welireg US per-tablet +22% 편차): 스크레이퍼가 AWP package(유통 마크업 포함) 를 가져왔는데 factory_ratio 0.74 는 **WAC 기준** → double-count. 수정: `us_micromedex.py` WAC 우선 (SOURCE_TYPE=`redbook_wac`), AWP 는 fallback. 기존 row 는 `raw_data.wac_package` 로 backfill. 검증: US adj_diff 가 FX_diff(+2%) 로 수렴 → 잔여 편차는 KEB 36mo 창 drift 뿐. **원칙: US 는 WAC package 만 사용. AWP 절대 금지 (double-count).**
- **2026-04-27 MFDS 공공데이터 API 1차 권위 소스 통합**: 이전에는 enrichment(허가일/용법)가 Perplexity sonar 추정값에 의존 → 변동성·할루시네이션 (예: 메디컬타임즈 2010년 기사를 2024.09 로 표기). 수정: 식약처 공공데이터 API 두 endpoint (`DrugPrdtPrmsnInfoService07/getDrugPrdtPrmsnDtlInq06` 허가정보, `MdcinPatentInfoService2/getMdcinPatentInfoList2` 특허정보) 를 1차 소스로 통합. drug_enrichment 는 보조. 신규 모듈 `agents/scrapers/kr_mfds_permit.py`, `agents/scrapers/kr_mfds_patent.py` + sqlite 캐시 (permit 30일/patent 90일 TTL) + 변형검색 6단계 (괄호 prefix → 한글 변형 → 원본 → 함량 prefix → brand only → ingredient fallback). 특허 분류: PATENT_GB_CODE='물질*' core / 학술기관·ADC·biosimilar 후속 secondary / 빈 코드 fallback (글리벡 케이스). PAGE_GB_NM 필터 금지 (트라스투주맙 = 제3자 제넨테크 보유 활성성분도 LOE 결정에 포함). 검증: 14/15 약제 매칭 성공 (가다실 제외 — 백신 HIRA 미등재). 세부: `agents/rules/mfds_api_integration_rules.md`.
- **2026-04-27 해외 허가/급여/가격 통합 구조 신설 (pure-napping-goose plan)**: 이전 3계층 분리 (허가 272 ↔ 급여 한국만 10 ↔ 가격 Welireg만 5) 사이 join 불가. axis 신설: `indication_id × country × body`. 신규 테이블 `reimbursement_xnational` + `product_alias_map`. 신규 스크레이퍼 4종 (`uk_nice.py:search_reimbursement` 확장 + `au_pbs.py`/`us_cms.py`/`jp_chuikyo.py` 신규). 통합 API `GET /api/foreign/country-overview` + 신규 UI `<CountryCardGrid>` (한 약 검색 시 6개국 카드, 카드 클릭 시 indication expand). 자격증명 불필요 (모두 공개). product_slug↔INN↔국가별 표기 정규화로 `query_name='pembrolizumab'`↔`product='keytruda'` 브릿지 해소. 한국 `indication_reimbursement` 보존 + API union (KR/HIRA 가상 행). 세부: `agents/rules/foreign_approval_agent_rules.md` §Cross-national reimbursement.

---

## 감시 · 리뷰 · 제안 (QualityGuardAgent)

QualityGuard 는 사후 기록자가 아니라 **상시 감시자 + 제안자**.

- 파이프라인 실행 후 자동 트리거: 스키마 / 가격 이상값 / 환율 검증
- **매일 06:00 코드베이스 리뷰**: 규칙 위반 패턴 스캔 + MFDS 8개 baseline 회귀 체크 + 개선 제안 생성
- 결과: `quality_guard/review_YYYY-MM-DD.md` (사용자 확인용) + `deviation_log.jsonl` (기록)
- 상세: `agents/rules/quality_guard_rules.md`

신규 규칙 추가 / 대규모 변경 시 반드시 `QualityGuardAgent.review_codebase()` 수동 실행으로 회귀 확인.
