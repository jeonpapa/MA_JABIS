# DrugEnrichmentAgent 규칙

## 역할
국내 약제의 **RSA(위험분담제) 여부**, **허가일**, **용법용량** 을 공식 출처에서 수집하고,
현재 상한금액과 결합해 **일/월/연 치료비용**을 산출한다.
결과는 `drug_enrichment` 테이블에 캐싱 (기본 TTL 30일).

## 필수 준수 규칙

- **MUST**: 출처는 다음 중 하나 이상 — HIRA(hira.or.kr), MFDS 의약품안전나라(nedrug.mfds.go.kr),
  약학정보원(health.kr), 보건복지부(mohw.go.kr), 법제처(law.go.kr), 의약전문지(dailypharm·yakup·medipana).
- **MUST**: RSA 판정은 HIRA 위험분담제 공식 목록 또는 복지부 고시 근거만 인정.
  의약전문지 보도는 보조 근거로만 사용, 단독 판정 금지.
- **MUST**: 용법용량은 식약처 허가사항의 성인 표준 용량을 채택. 소아/특수집단은 `notes`에 기재.
- **MUST NOT**: 확인되지 않은 RSA 여부를 추측으로 `is_rsa=1` 처리. 불확실하면 `NULL` + `confidence=low`.
- **MUST NOT**: 용법용량이 "필요시 투여"(as needed)인 약제에 연간 치료비를 계산. 대신 `dose_schedule=as_needed` + 계산 스킵.

## RSA 4대 유형 (분류 키)

| rsa_type | 의미 |
|---|---|
| `expenditure_cap` | 총액제한형 (예상 매출액 초과 시 환급) |
| `refund` | 환급형 (일정액 초과 매출 환급) |
| `utilization` | 사용량-약가 연동형 |
| `conditional` | 조건부 급여 (성과기반·근거생성) |
| `combined` | 복합 유형 |

## 용법용량 파싱 스키마

에이전트는 자연어 용법용량에서 계산 가능한 수치를 추출해야 함:

```json
{
  "dose_schedule": "continuous | cycle | as_needed",
  "daily_dose_units": 1.0,          // 정/바이알/mL 단위. 1일 2정이면 2.0
  "cycle_days": 21,                  // 주기형일 때만 (3주 1회 → 21)
  "doses_per_cycle": 1.0             // 주기당 투여 단위 수
}
```

### 계산 공식 (API 단 계산, DB는 원자재만 저장)

- `dose_schedule == "continuous"`:
  - 일치료비 = current_price × daily_dose_units
  - 월치료비 = 일치료비 × 30
  - 연치료비 = 일치료비 × 365
- `dose_schedule == "cycle"`:
  - 주기치료비 = current_price × doses_per_cycle
  - 연치료비 = 주기치료비 × (365 / cycle_days)
  - 월치료비 = 연치료비 / 12
  - 일치료비 = 연치료비 / 365 (참고값)
- `dose_schedule == "as_needed"`: 계산 스킵, `"-"` 표시

## 출력 JSON 스키마 (에이전트 응답)

```json
{
  "is_rsa": true | false | null,
  "rsa_type": "expenditure_cap | refund | utilization | conditional | combined | null",
  "rsa_note": "간략 특이사항 또는 빈 문자열",
  "approval_date": "YYYY.MM.DD | null",
  "usage_text": "식약처 허가 용법용량 요약 (한국어, 100자 이내)",
  "daily_dose_units": 1.0,
  "dose_schedule": "continuous | cycle | as_needed",
  "cycle_days": 21 | null,
  "doses_per_cycle": 1.0 | null,
  "sources": [{"url": "...", "title": "...", "media": "..."}],
  "confidence": "high | medium | low",
  "notes": ""
}
```

## 캐싱 정책

- 키: `normalized_name` (브랜드+용량 정규화. 예: `자누비아정100mg`)
- TTL: 30일 (RSA/허가일은 거의 변하지 않음)
- Hit: `fetched_at + ttl_days` 이내이면 외부 호출 없이 DB 반환
- Miss: Perplexity 1회 호출 → 결과 저장

## 절대 금지
- 허가일을 추측 (없으면 null)
- RSA 근거 없는 단정
- 용법용량이 추출 불가능한 경우 임의 dose_schedule 배정 (대신 `as_needed`)
