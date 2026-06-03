# 예측 룰 자가 학습 시스템 — Prediction Rules

> **목적**: 매 차수 D+1 시점에 사전 예측과 실제 결과를 자동 비교(audit)해 룰의 weight를 보정하거나 신규 룰을 등록한다. 차수가 누적될수록 예측 정확도가 자동으로 향상된다.

## 자가 학습 사이클

```
D-2 (사전 예측 D-2 보고서) — 각 예측 약물에 적용된 룰 ID 명시
   ↓
D+0 (HIRA 공식 결과 공개)
   ↓
D+1 (자동 audit — routine이 cloud에서 실행):
  1. 사전 예측 보고서 load + 룰 list 추출
  2. 예측 vs 실제 비교 → TP/FP/FN 분류
  3. 룰별 hit/miss 자동 갱신 (frontmatter)
     • TP 룰: evidence_count +1, weight +0.02 (max 0.95)
     • FP 룰: weight -0.05, 누적 FP ≥ 3 → retired/로 자동 이동
  4. FN 발견 시 신규 패턴 분석 → candidate/PR-NEW-*.md 등록
  5. CANDIDATE 룰: 2회 TP → ACTIVE 승격, 2회 FP → 폐기
  6. audit_log.md 자동 갱신 (precision/recall/룰 조정 list)
```

## 활성 룰 (16개)

### 통과 예측 룰 (11개)

| ID | 이름 | weight | evidence | 비고 |
|---|---|---|---|---|
| PR-001 | 정부 D-14 사무관 거명 | 0.85 | 2 | 강한 신호 |
| PR-002 | OS 7년+ N차 도전 + update | 0.80 | 1 | 버제니오 monarchE |
| PR-003 | 동일 클래스 동일 차수 OS 우위 | 0.75 | 1 | 버제니오 vs 키스칼리 |
| PR-004 | 약평위 재심의 → 1~2차수 후 재상정 | 0.70 | 2 | 임핀지·이뮤도 |
| PR-005 | 암질심 통과 후 3~6개월 약평위 transition | 0.60 | 1 | 예스카타 |
| **PR-006** | **PD-L1 subpopulation + RSA layer → 통과** | **0.78** | **1** | **티쎈트릭 (NEW)** |
| **PR-007** | **단독품목 + 가속승인 + small subpop → 무조건 통과** | **0.80** | **1** | **레테브모 (NEW)** |
| **PR-008** | **20+ 년 만의 신약 + 단회 투여 → 무조건 통과** | **0.75** | **1** | **메탈라제 (NEW)** |
| **PR-009** | **NCCN 동등 + 저가 포지셔닝 → 통과 ↑** | **0.65** | **0** | **테빔브라 검증 대기 (NEW)** |
| **PR-013** | **평가금액 mismatch → 재심의 → 1~2차수 후 재상정** | **0.65** | **0** | **리브리반트 검증 대기 (NEW)** |
| **PR-014** | **평가가 이하 수용 조건부 표준** | **0.75** | **4** | **빈도 높음 (NEW)** |

### 미통과 예측 룰 (5개)

| ID | 이름 | weight | evidence |
|---|---|---|---|
| PR-101 | adjuvant 장기 투여 BIA 부담 | 0.70 | 1 |
| PR-102 | 국산 ATMP 첫 등재 표준 부재 | 0.75 | 1 |
| PR-103 | 동일 차수 동일 클래스 OS 열위 | 0.70 | 1 |
| PR-104 | EGFR+ NSCLC 한국 보수성 | 0.65 | 4 |
| **PR-105** | **FDA 안전성 경고 + 시민단체 제기 → NHIS 지연** | **0.70** | **1** | **타브너스 진행 중 (NEW)** |

**활성 총 16개** (통과 11 + 미통과 5)

## 룰 자동 보정 algorithm (D+1 routine prompt에 명시)

```python
# 매 차수 D+1 08:00 KST routine 실행
for rule in d_minus_2_report.applied_rules:
    actual = hira_official_result.find(rule.predicted_drug)
    if actual == rule.predicted_outcome:
        rule.evidence_count += 1
        rule.weight = min(0.95, rule.weight + 0.02)
        rule.last_calibrated = today
        log_TP(rule)
    else:
        rule.weight -= 0.05
        rule.fp_count += 1
        log_FP(rule)
        if rule.fp_count >= 3:
            move(rule, 'active/' → 'retired/')
            rule.status = 'RETIRED'

# FN 발견 시 신규 패턴 분석
for actual_drug in hira_official_result.drugs:
    if actual_drug not in d_minus_2_report.predicted:
        log_FN(actual_drug)
        pattern = analyze_media_for(actual_drug)
        create('candidate/PR-NEW-*.md', pattern, weight=0.50, status='CANDIDATE')

# CANDIDATE 룰 검증 — 2회 누적 결과로 승격/폐기
for candidate in candidate_rules:
    if candidate.tp_count >= 2:
        promote(candidate, 'candidate/' → 'active/')
        candidate.status = 'ACTIVE'
    elif candidate.fp_count >= 2:
        delete(candidate)
```

## 차수 누적 효과 (예상)

| 시점 | 활성 룰 | 평균 precision |
|---|---|---|
| 현재 (16개) | 16 | ~0.55 |
| N+5차 | ~18 | ~0.65 |
| N+10차 | ~20 | ~0.75 |
| N+20차 | ~24 | ~0.80+ |

## 6/4 6차 첫 라이브 audit 검증 대상

D+1 시점(6/5 08:00 KST) D+1 routine이 자동 검증 + commit + push + 이메일:

1. **PR-009 (NCCN 동등 + 저가)** — 테빔브라 통과 시 weight 0.65 → 0.75 자동 상향
2. **PR-013 (평가금액 mismatch 재상정)** — 리브리반트 6/4 재상정 결과로 첫 검증
3. **PR-014 (평가가 이하 조건부)** — 본 차수에서 조건부 통과 발생 시 evidence_count +1
4. **PR-004 (재심의 → 재상정)** — 리브리반트 재상정 시 강화

## 디렉토리 구조

```
data/hira_pipeline/예측_룰/
├── README.md          # 본 문서
├── audit_log.md       # 차수별 누적 audit
├── active/            # 활성 룰 (현재 11개 통과 + 5개 미통과)
├── candidate/         # 검증 대기 (자동 생성)
└── retired/           # 폐기 룰 (누적 FP ≥ 3)
```

## 룰 추가 history

| 일자 | 차수 | 신규 룰 | 사유 |
|---|---|---|---|
| 2026-05-30 | 시스템 시작 | PR-001~005, PR-101~104 (9개) | 5/27 5차 암질심 baseline |
| 2026-06-03 | 6/4 6차 baseline | **PR-006·PR-007·PR-008·PR-009·PR-013·PR-014·PR-105 (7개)** | **4·5차 약평위 통과 약제 분석 결과 도출** |
