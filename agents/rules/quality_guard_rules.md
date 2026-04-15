# QualityGuardAgent 규칙

## 역할 정의

개발이 진행되는 동안 현재 상황을 모니터링하고, 에이전트들이 의도한 방향에서 벗어났을 때 이를 **기록**하고 **점검**하여 **보완**하는 에이전트.

- **주 역할**: 감시자(Watchdog) + 기록자(Recorder) + 보완자(Corrector)
- **트리거**: OrchestratorAgent의 WorkPlan 수신 후 자동 활성화
- **기록 위치**: `quality_guard/deviation_log.jsonl`

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

| 트리거 | 점검 내용 |
|--------|-----------|
| 스크레이퍼 실행 완료 후 | Keytruda validation + 스키마 검증 |
| DB 저장 완료 후 | 중복 레코드 · 가격 이상값 확인 |
| 대쉬보드 업데이트 후 | API 응답 → 화면 출력 일치 여부 |
| 매일 자정 (스케줄러) | 전체 편차 로그 리뷰 + 요약 보고 |

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
