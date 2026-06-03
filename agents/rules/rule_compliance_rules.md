# RuleComplianceAgent 규칙

## 역할 정의

사용자-Claude 대화에서 합의된 **메모리 항목(feedback/project/reference)** 이 실제 런타임에서 지켜지는지 매일 자동 감사하는 에이전트.

- **주 역할**: 메모리 ↔ 런타임 대조자 (Auditor)
- **트리거**:
  - 매일 **05:30 Asia/Seoul** — `scheduler.rule_compliance_audit_job` (QG 06:00 리뷰 30분 전)
  - 수동 — `python -m agents.rule_compliance --write-report` / `python scheduler.py --compliance-now`
- **소스 오브 트루스**: `~/.claude/projects/-Users-kimjeong-ae-MA-AI-Dossier/memory/MEMORY.md` (index) + 개별 memory 파일
- **기록 위치**: `quality_guard/compliance_YYYY-MM-DD.md`

---

## 설계 원칙

1. **메모리가 진실의 소스** — repo 안의 룰 파일이 아닌, 사용자와 합의된 메모리가 우선. 룰 파일 drift 는 QG `scan_rule_drift` 가 별도 담당.
2. **증거 기반** — 각 체크는 수치(row count, ratio, file count)를 메트릭으로 반환. "룰이 있다" 가 아닌 "증거가 이렇다".
3. **자동화 불가는 명시적 SKIP** — 개발 관행·프로세스 상태 메모리는 묵시적 통과 대신 SKIP + 사유 기록.
4. **회귀 시 명확한 root cause 힌트** — FAIL 시 `detail` 에 "~ 경로 점검", "~ 스크립트 재실행" 같은 다음 액션 포함.

---

## 체크 레지스트리 (`agents/rule_compliance/checks.py`)

| 메모리 | 체크 함수 | 신호 |
|--------|-----------|------|
| `project_comparator_drug_structure` | `check_comparator_completeness` | `drug_latest.ingredient` 채움율 ≥ 30% |
| `project_price_change_reason_quality` | `check_reason_evidence_quality` | `reason_cache` 최근 30건 중 n_refs=0 < 50% |
| `project_indication_level_approval` | `check_indication_decomposition` | `indications_master.product='keytruda'` row ≥ 20 |
| `project_mfds_official_date_pipeline` | `check_mfds_official_dates` | `indications_by_agency` MFDS 공식일 ≥ 50% |
| `project_foreign_scraper_form_type` | `check_foreign_form_type` | `foreign_drug_prices.form_type` 채움율 ≥ 90% |
| `feedback_foreign_daily_cost_total_mg` | `check_foreign_daily_cost_sanity` | `daily_cost_krw > ₩10M` 0건 |
| `feedback_cache_db_first` | `check_cache_db_first` | `reason_cache` + `gov_summary` 파일 합 > 0 |
| `feedback_mfds_pattern_matching` | `check_mfds_baseline_8` | QG baseline 8건 100% 일치 |

신규 메모리 추가 시:
1. `CHECKS` 딕셔너리에 체크 함수 등록, 또는
2. `SKIP_WITH_REASON` 에 명시적 SKIP 사유 기입

둘 다 안 넣으면 기본 SKIP + "신규 메모리 — 체크 함수 추가 검토" 로 리포트에 경고.

---

## 출력 형식 (`compliance_YYYY-MM-DD.md`)

```
# Rule Compliance 감사 — YYYY-MM-DD HH:MM

## 요약
- ✅ PASS: N건
- ❌ FAIL: M건
- ⏭ SKIP: K건 (런타임 검증 불가)

## ❌ FAIL — 즉시 확인 필요
- **메모리 제목** — 사유 + 다음 액션 힌트
  - 수치: `{metrics}`

## ✅ PASS — 실행 증명 확보
- **메모리 제목** — 증거 요약

## ⏭ SKIP — 런타임 신호 없음
- _메모리 제목_ — SKIP 사유
```

---

## 다른 에이전트와의 관계

| 에이전트 | 담당 |
|----------|------|
| `QualityGuardAgent` | 코드 패턴 / 규칙 drift / 스크레이퍼 편차 — **코드와 repo 룰** 감시 |
| `RuleComplianceAgent` | 메모리 ↔ 런타임 증거 — **합의 사항** 감시 |

두 에이전트는 독립 실행되며 리포트도 분리. 겹치는 항목(예: MFDS baseline)은 양쪽에서 각자 수집한다 (교차 검증).
