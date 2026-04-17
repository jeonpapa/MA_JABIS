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
| 스크레이퍼 실행 완료 후 | Keytruda validation + 스키마 검증 | `validate_scraper_results` / `run_keytruda_validation` |
| DB 저장 완료 후 | 중복 레코드 · 가격 이상값 확인 | `validate_db_records` |
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

### 2) 규칙 ↔ 코드 Drift 탐지 (`scan_rule_drift`)
- MFDS `approval_date` 를 `date_source` 판단 없이 사용하는 파일
- (향후) CLAUDE.md 절대금지 항목 vs 코드 자동 매핑

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
