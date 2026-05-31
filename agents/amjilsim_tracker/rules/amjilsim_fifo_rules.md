# KR-RULE-030 : 암질심 FIFO 큐 이송 원칙

## 대상 위원회
중증(암)질환심의위원회(암질심) — 항암제·중증희귀질환 요양급여 사전심의.
**약제급여평가위원회(약평위)와는 별개 위원회**이며, 본 룰은 암질심 절차만 다룬다.

## Rule
암질심은 제약사 신청일 기준 **FIFO**로 이송된다. **"안건 미상정 ≠ 실패."**

모든 약물 상태는 다음 **5단계**로만 분류한다:

| 코드 | 상태 | 정의 | 다음 단계 |
|---|---|---|---|
| `QUEUE_PENDING` | Queued | 신청서 제출 + 안건 미상정 | 신청일 FIFO 순서로 자동 차수 지정 |
| `QUEUE_PROCESSED` | On Agenda | 큐 상위 도달, 차수 안건 포함 | 심의 진행 |
| `APPROVED` | Approved | 안건 상정 + 급여기준 충족 | 약가협상 → 공단 통보 |
| `REJECTED_REQUEUE` | Rejected | 안건 상정 + 급여기준 미충족 | 신청 취소 → 보완 → 재신청 → 큐 최후위로 다시 진입 |
| `WITHDRAWN` | Withdrawn | 회사 측 자발적 취소 | 재신청 시 신규로 진입 |

## Why
2025/27 케이스에서 림카토(휴럼)가 5개 매체에 거명되었음에도 안건 상정조차 되지 않은 사례를 "예측 실패"로 분류한 것은 잘못이다. 안건 미상정은 큐 후순위라는 의미이지 실패가 아니다. FIFO 원칙을 무시하면 모든 사전 신호 분석이 오류 신호를 양산한다.

## How to apply
- DB의 `amjilsim_drug_queue_status.queue_state`는 위 5코드 외 입력 금지. CHECK constraint로 강제.
- D-1 보고서에서 안건 미예측 약물을 "예측 실패"로 표현 금지. "큐 후순위 추정"으로 표기.
- D+1 confusion matrix는 (예측: ON_AGENDA | QUEUED) × (실제: ON_AGENDA | QUEUED)만 계산. APPROVED/REJECTED 결과는 별도 trace.
- 재신청 약물은 `n_th_attempt`가 증가. 평균 재진입 주기 6~12개월 (사용자 데이터 누적 후 갱신).

## Cross-reference
- `signature_lexicon.md` (KR-RULE-029): "사재청구" / "보완자료 요청" / "재상정" 등 큐 이동 신호 단어
- `media_tier_taxonomy.md`: 메디칼옵저버 = 큐 inventory snapshot, 메디칼타임즈 = 사전 안건 leak (본질 차이)

## Authority source
- `_resource/약제의 요양급여대상여부 등의 평가기준 및 절차 등에 관한 규정(2025년도 3월 개정).pdf` (요양급여 일반 규정)
- `data/hira_sop/full_text.txt` (HIRA SOP)
- **TODO**: 중증암질환심의위원회 운영 세칙·공식 SOP 별도 확보 필요 (약평위 규정과 구분)
