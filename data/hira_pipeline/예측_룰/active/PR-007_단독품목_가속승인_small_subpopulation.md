---
rule_id: PR-007
name: "단독품목 + 글로벌 가속승인 + small subpopulation → 무조건부 통과"
category: 통과_예측
established_at: 2026-05-07
last_calibrated: 2026-06-03
evidence_count: 1
weight: 0.80
condition: "비교약제 부재(단독품목) + FDA/EMA 가속승인 reference + 작은 환자 subpopulation"
prediction: "조건부 단서 없이 무조건부 통과 가능성 ↑ (단독품목 RSA 적용)"
status: ACTIVE
---

# PR-007 — 단독품목 가속승인 small subpopulation 모범 패턴

## 정의

precision oncology 또는 희귀질환 영역에서 다음 3요소 동시 충족 시 한국 약평위에서 단독품목 우대 경로로 무조건부 통과:
1. 비교약제 부재 (단독품목 — KR-RULE-006/007 트랙)
2. FDA·EMA·PMDA 가속승인 또는 정식 승인 reference
3. 작은 환자 subpopulation (mutation-specific·rare cancer 등)

## Hit history

### 2026-05-07 5차 약평위 — TP ✓
- **레테브모캡슐** (셀퍼카티닙) — 한국릴리
- 적응증 3종 동시: RET fusion+ NSCLC, RET-mutant MTC, RET fusion+ 갑상선암
- single-arm trial(LIBRETTO-001) 기반
- FDA 2020-05 가속승인, EMA 2021
- HIRA brdBltNo=11793 모두 급여 적정성 인정 (조건 단서 없이)

## 검증 시 주의

- "단독품목"은 한국 식약처·HIRA가 인정한 단독품목 status. 글로벌 1st-in-class는 별개.
- subpopulation이 너무 광범위(예: 일반 NSCLC 전체)면 본 룰 적용 X.
