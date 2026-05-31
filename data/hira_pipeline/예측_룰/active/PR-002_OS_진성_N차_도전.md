---
rule_id: PR-002
name: "OS 7년+ 진성 데이터 추가 후 N차 도전 → 통과"
category: 통과_예측
established_at: 2026-05-27
last_calibrated: 2026-05-30
evidence_count: 1
weight: 0.80
condition: "이전 차수 미설정 약물이 ESMO/ASCO 등 주요 학회에서 OS 7년+ 데이터 추가 후 N차 도전 시"
prediction: "통과 가능성 ↑"
status: ACTIVE
---

# PR-002 — OS 7년+ 진성 데이터 추가 후 N차 도전

## 정의

이전 차수에 미설정된 약물이 주요 학회(ESMO·ASCO 등)에서 **OS 7년+ 진성 데이터를 추가 확보**한 후 차수 직전에 도전하는 패턴.

PFS·DFS 단독 update는 본 룰 적용 X. **OS 단독 진성성**이 핵심.

## Hit history

### 2026-05-27 5차 암질심 — TP ✓
- **약물**: 버제니오정 (abemaciclib) — 한국릴리
- **이전 history**: 1차·2차·3차 시도 모두 미설정
- **신규 evidence**: ESMO 2025 monarchE 7년 OS 데이터 추가 (2025-04 → 5/27 6주 전)
- **실제 결과**: HIRA brdBltNo=11808 APPROVED ✓

## Miss history

(없음)

## 운영 누적

- **TP**: 1건
- **FP**: 0건
- **hit rate**: 100% (1/1)
- **현재 weight**: 0.80

## 검증 보류

본 룰은 1회 hit만 있어 통계적 의미가 제한적. 다음 6~12개월 내 동일 패턴 약물 등장 시 검증:
- 키트루다 후속 적응증 확대 + OS update 시
- 옵디보+여보이 적응증별 OS update 시

## 다음 차수 적용

후속 차수에서 다음 약물 도전 시 가중치 ↑ 적용:
- 동일 클래스 비교 약물 OS 미성숙 시 (PR-003과 연동)

## 관련 노트
- [[PR-003_동일클래스_OS_우위]]
- [[2026-05-27_암질심_5차]]
