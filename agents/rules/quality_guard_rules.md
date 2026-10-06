# QualityGuardAgent 규칙

## 역할 정의

개발·운영 전 주기에 걸쳐 **지속적으로** 코드베이스와 데이터를 감시하고, 규칙·코드 drift·데이터 회귀·구조 개선 기회를 발견하여 사용자에게 제안하는 에이전트.

- **주 역할**: 감시자(Watchdog) + 기록자(Recorder) + 보완자(Corrector) + **리뷰어(Reviewer) + 제안자(Advisor)**
- **트리거**:
  - 파이프라인 실행 중 — OrchestratorAgent WorkPlan 수신 직후
  - **매일 06:00 Asia/Seoul** — `scheduler.quality_guard_review_job` (업무 시작 전 리뷰 배치)
  - 수동 — `python -m agents.quality_guard review` 또는 `python scheduler.py --review-now`
- **기록 위치**:
  - `quality_guard/deviation_log.jsonl` (편차 스트림)
  - `quality_guard/daily_report_YYYY-MM-DD.md` (편차 일일 요약)
  - `quality_guard/review_YYYY-MM-DD.md` (코드베이스 종합 리뷰 + 제안)

---

## 모니터링 항목

### 코드 레벨 체크
| 항목 | 기대값 | 편차 조건 |
|------|--------|-----------|
| `local_price` 타입 | `float` 또는 `None` | 문자열 또는 0 반환 |
| `dosage_strength` | 규격 포함 문자열 | 빈 문자열 또는 None |
| `country` | ISO 2자리 코드 | 소문자·오타 |
| `currency` | ISO 3자리 코드 | 없거나 오타 |
| `source_url` | 실제 접근된 URL | 빈 문자열 |
| `BaseScraper` 상속 | 모든 스크레이퍼 | 직접 구현 시 경고 |

### 데이터 품질 체크
- Keytruda 가격 validation: 새 스크레이퍼 완성 시 자동 테스트
- 가격이 0원 또는 음수인 경우 → 즉시 기록 + 에러
- 동일 약제 동일 국가에서 ±30% 이상 가격 변동 → 경고
- 환율 36개월 평균 미적용 시 → 기록

### 개발 방향 체크
- MSD 전용 필터 재등장 여부 감지
- 웹 배포 코드가 기능 완성 전 추가되는지 감지
- `config/.env` 외 자격증명 하드코딩 감지
- Playwright args 미적용 상태로 특정 사이트 접근 시 감지

---

## 편차 기록 형식

```jsonl
{
  "timestamp": "2026-04-14T10:30:00",
  "severity": "WARNING | ERROR | INFO",
  "agent": "ForeignPriceAgent",
  "file": "agents/scrapers/uk_mims.py",
  "deviation_type": "missing_dosage_strength",
  "description": "UK MIMS 스크레이퍼가 dosage_strength 없이 가격만 반환",
  "expected": "용량별 개별 레코드",
  "actual": "단일 레코드, dosage_strength=''",
  "corrective_action": "price_lines 파싱 로직 보완 필요",
  "resolved": false
}
```

---

## 자동 보완 트리거 조건

### 즉시 보완 (자동)
- 스크레이퍼 반환값에 필수 키 누락 (`product_name`, `local_price`, `source_url`)
- DB 저장 실패
- 환율 조회 실패 → 캐시된 마지막 환율로 대체 후 기록

### 보고 후 대기 (사용자 확인 필요)
- 새 스크레이퍼에서 Keytruda 가격이 0 또는 None
- 기존 국가에서 가격이 ±50% 이상 변동
- DocCheck / MIMS 구독 만료 감지

### 기록만 (자동 처리 없음)
- 로그인 실패 (비급여 처리로 계속 진행)
- 검색 결과 0건 (정상 비급여 처리)
- 네트워크 일시 오류

---

## 점검 주기

| 트리거 | 점검 내용 | 구현 함수 |
|--------|-----------|-----------|
| 스크레이퍼 실행 완료 후 | 스키마 검증 | `validate_scraper_results` |
| **매일 06:00 (review_codebase 안)** | **해외약가 저장 행 데이터 검사** — injection 분모가 per-mL 농도인 행 · 국가/통화 코드 · pack_count 미확인 행 요약 | `checks.check_foreign_price_data` / `checks.summarize_unverified_pack_rows` |
| 대쉬보드 업데이트 후 | API 응답 → 화면 출력 일치 여부 | 수동 |
| 매일 00:00 | Git 자동 백업 | `scheduler.git_backup_job` |
| **매일 06:00** | **코드베이스 종합 리뷰 + 회귀 탐지 + 제안 생성** | `review_codebase` |
| 수동 | 편차 요약 / 코드 패턴 스캔 | `generate_daily_report` / `scan_codebase` |

---

## 종합 리뷰 (`review_codebase`)

매일 06:00 또는 수동 호출 시 다음 4단계를 수행하고 `quality_guard/review_YYYY-MM-DD.md` 생성.

### 1) 코드 패턴 위반 스캔 (`scan_codebase`)
- `msd_only=True` 기본값
- 자격증명 하드코딩
- `BaseScraper` 미상속
- 웹 배포용 코드가 기능 완성 전 추가되는지
- **해외 약가 최소단위 원칙 회귀** (2026-04-22 추가, `foreign_agent_rules.md §최소단위`):
  - (a) `us_micromedex.py` 에서 `local_price = awp_*` 직접 대입 (WAC 우선 누락) → factory_ratio 0.74 와 double-count
  - (b) `total_mg/unit_mg` ratio 를 pack_count 로 사용하면서 `form_type == 'oral'` guard 없음 → injection 은 농도×volume/농도=volume 이라 pack count 가 아님
  - (c) `_populate_daily_cost` 가 unit_mg 분모로 `_extract_mg` 사용 → per-mL 농도가 들어가 injection daily_cost 왜곡. `_extract_per_unit_mg(form_type, ...)` 사용 필수

### 2) 규칙 ↔ 코드 Drift 탐지 (`scan_rule_drift`)
- MFDS `approval_date` 를 `date_source` 판단 없이 사용하는 파일
- (향후) CLAUDE.md 절대금지 항목 vs 코드 자동 매핑

### 2b) 해외약가 **데이터** 검사 (`checks.check_foreign_price_data`, 2026-10 추가)
코드 grep 만으로는 간접 fallback 을 못 잡았다 — FR Vidal "25 mg"(per-mL 농도) 가 `return _extract_mg(s)` 를 타고 daily_cost 분모가 돼 4× 였는데 (c) 패턴 검사는 통과했다. 그래서 **저장된 값**을 본다 (DB read-only):
- `injection_concentration_as_unit` (ERROR): priced injection 행의 `unit_strength_mg` 가 문맥의 농도(N mg/ml 의 N)와 같거나, form-aware `_extract_per_unit_mg` 재계산과 다르거나, per-vial mg 불명인데 `daily_cost_krw` 가 있다
- `invalid_country_code` / `invalid_currency_code` (ERROR): ISO 집합 밖 (구 `check_db_records` 승계 — 그 함수는 어디서도 호출되지 않던 죽은 코드라 삭제)
- 결과는 `review_codebase()` 가 `rule_drifts` 에 합쳐 돌려준다(항목에 `kind="data"`, `check`, `row_id`, `detail`) — `signals.record_quality_review` 가 이미 읽는 형태라 **ops_incident(priority 2)** 로 원장에 간다. `file` 의 숫자는 내용 키에서 지워지므로 같은 (국가, 약제) 는 하루 1건·다음 날 재발도 같은 행. 보고서에는 "해외약가 데이터 검사" 섹션.
- `summarize_unverified_pack_rows`: pack_count 미확인(default) 으로 A8 평균에서 빠진 pack 국가 행을 국가·약제별 건수로 제안(🧮) 1줄 → improvement 그룹.
- 죽은 안전장치 정리: `validate_keytruda`/`run_keytruda_validation` 도 삭제 — Keytruda 검증은 `tests/test_keytruda_injection_baseline.py`(per-vial 조정가·일일비용 고정) 와 위 데이터 검사가 맡는다. `agents/pipeline.py` 는 아무도 import 하지 않는다(QG 호출 없음, 그대로 둠).
- 오프라인 실행: `QG_SKIP_NETWORK_CHECKS=1` 이면 제안 6a(DocCheck 실제 로그인) 를 건너뛴다. `QUALITY_GUARD_DIR` 로 산출물 경로 오버라이드.

### 3) MFDS 공식일 회귀 탐지 (`check_mfds_baseline`)
`kr_mfds_approval_agent_rules.md` §9 의 **8개 baseline indication** 이 DB 의 `approval_date` 와 일치하는지 확인. 한 건이라도 불일치 시 ERROR 기록 — peri/adj/neo 매칭 로직 회귀 신호.

| indication_id | 기대 날짜 |
|---------------|-----------|
| keytruda_nsclc_adjuvant_resectable_mono | 2024-05-14 |
| keytruda_nsclc_perioperative_resectable_mono | 2023-12-19 |
| keytruda_tnbc_perioperative_mono | 2022-07-13 |
| keytruda_hnscc_perioperative_resectable_pdl1_1_mono | 2025-10-02 |
| keytruda_mel_adjuvant_adjuvant_mono | 2019-05-13 |
| keytruda_rcc_adjuvant_adjuvant_mono | 2022-08-22 |
| lynparza_bc_adjuvant_adjuvant_brca_mut_mono | 2023-02-23 |
| welireg_vhl_mono | 2023-05-23 |

baseline 업데이트 시 `QualityGuardAgent.MFDS_OFFICIAL_BASELINE` 와 `kr_mfds_approval_agent_rules.md §9` 를 **동시에** 수정할 것.

### 4) 개선 제안 생성 (`generate_suggestions`)
- **700 줄 초과 파일**: 모듈 분리 권장 (scraper/parser/business logic 레이어 구분)
- **미해결 편차 누적 (>20건)**: 트리아지 권장
- **MFDS 자동화 gap**: `ForeignApprovalAgent._build_mfds` → `apply_mfds_official_dates` 통합 제안
- **DISEASE_KR 미커버**: `indications_master.disease` 중 `kr_mfds_indication_mapper.DISEASE_KR` 에 없는 질환 검출 → 신규 약물 추가 시 회귀 방지
- **drug_enrichment 커버리지** (2026-04-23 추가): `drug_enrichment` row 수 < 30 이면 경고. 비교약제 선택 시 일일투약비·허가일·용법용량 공백의 근본 원인. 동일 generic 상속 (`_extract_generic_key`) 은 보완책이며, 근본 해소는 TOP-N 배치 enrich.
- **price-change-reason evidence 품질** (2026-04-23 추가): 최근 30건 `reason_cache` 중 n_refs=0 비율 >50% 또는 confidence=low 비율 >70% 면 경고. 근본 원인: `enforce_rules` 가 `published_at` 없는 perplexity citations 를 드롭하면 refs=0 함정에 빠짐. URL 에서 date 추출 + `date_unknown` 마킹 보존 경로(`agents/market_intelligence/rules_engine.py`) 점검 필수.

### 출력 형식 (`review_YYYY-MM-DD.md`)
```
# QualityGuard 코드베이스 리뷰 — 2026-04-17

## 요약
- 코드 패턴 위반: Xx건
- 규칙↔코드 drift: Xx건
- MFDS baseline 회귀: Xx건
- 개선 제안: Xx건

## ❌ MFDS 공식일 회귀 (있을 때만)
## ⚠️ 규칙 ↔ 코드 Drift
## 🔎 코드 패턴 위반
## 💡 개선 제안
```

사용자는 매일 아침 이 파일 하나만 보면 상태 파악 가능.

---

## 보고 형식

```
[QualityGuard 일일 보고] 2026-04-14

✅ 정상: JP(Keytruda ¥830,310), IT(€3,428), CH(CHF4,294), FR(로그인 필요)
⚠️  경고: UK MIMS - dosage_strength 빈 값 2건 (uk_mims.py:142)
❌ 오류: DE Rote Liste - DocCheck 접근 차단 (login.doccheck.com 타임아웃)

미해결 편차: 3건
오늘 신규 편차: 1건
```
