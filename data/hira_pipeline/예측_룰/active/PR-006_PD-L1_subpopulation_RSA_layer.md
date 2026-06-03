---
rule_id: PR-006
name: "PD-L1 subpopulation 한정 + 기존 RSA 위 추가 layer → 통과"
category: 통과_예측
established_at: 2026-05-07
last_calibrated: 2026-06-03
evidence_count: 1
weight: 0.78
condition: "PD-L1 cutoff(예: 50%+) subpopulation 한정 + 기존 RSA 약제의 사용범위 확대(adjuvant·1차 등) 트랙"
prediction: "RSA 확대 통과 가능성 ↑"
status: ACTIVE
---

# PR-006 — PD-L1 subpopulation 한정 + 기존 RSA layer 패턴

## 정의

면역항암제(PD-1·PD-L1·CTLA-4 등)가 새 적응증 확대 시 PD-L1 발현 비율 cutoff을 적용한 좁은 subpopulation으로 진입하고 기존 RSA framework 위에 추가 layer 형식으로 등재되는 패턴.

## Hit history

### 2026-05-07 5차 약평위 — TP ✓
- **티쎈트릭주** (아테졸리주맙) — 한국로슈
- 확대 적응증: PD-L1 ≥50% subpopulation 한정, 병기 II~IIIA 초기 NSCLC adjuvant
- 기존 진행성 NSCLC 1차(2021 급여) RSA 위에 adjuvant 영역만 추가
- HIRA brdBltNo=11793 RSA 확대 적정성 인정

## MSD 자산 영향

키트루다 KEYNOTE-091 adjuvant 등재 협상의 직접 reference. 동일 logic 적용 가능성 ↑.

## 적용 시 검증

다음 조합 시 본 룰 적용:
1. 약제가 기존 RSA 등재 상태
2. 새 적응증이 PD-L1 cutoff 적용 가능 (50%+ 또는 1%+ 등)
3. early-stage 또는 adjuvant 영역 추가
