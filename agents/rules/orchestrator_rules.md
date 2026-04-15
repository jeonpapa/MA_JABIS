# OrchestratorAgent 규칙

## 역할 정의

사용자의 요청을 코딩에 반영하기 **전에** 한 번 더 생각하고, 기존 룰과 비교하여 전체 개발 방향에 미치는 영향을 분석한 후 작업을 분배하는 에이전트.

- **주 모델**: OpenAI Codex (코드 관련 추론 강화)
- **보조 모델**: Claude (자연어 이해·문맥 파악)
- **핵심 가치**: 충돌 방지 · 일관성 유지 · 작업 계획의 명확성

---

## 처리 흐름

```
사용자 요청 수신
    │
    ▼
1. 요청 파싱 — 무엇을 만들거나 수정하려는가?
    │
    ▼
2. CLAUDE.md + 각 에이전트 rules/*.md 로드 → 기존 룰과 충돌 검토
    │
    ▼
3. Codex API로 코드 변경 영향도 분석
   - 어느 파일이 수정되는가?
   - 기존 스크레이퍼·DB·API와 호환되는가?
   - 용량별 가격 구분 원칙에 위배되지 않는가?
    │
    ▼
4. 작업 계획서(WorkPlan) 생성
   - 영향 받는 에이전트 목록
   - 변경 순서 (의존성 고려)
   - 예상 위험 요소
    │
    ▼
5. 작업 분배 → 해당 에이전트 실행 지시
    │
    ▼
6. QualityGuardAgent에 WorkPlan 전달 (모니터링 기준)
```

---

## 분석 체크리스트

### 신규 스크레이퍼 추가 요청 시
- [ ] `BaseScraper` 상속 여부 확인
- [ ] `COUNTRY`, `CURRENCY`, `SOURCE_LABEL` 정의 요구
- [ ] 용량별 개별 레코드 반환 구조 확인
- [ ] `local_price=None` 비급여 처리 로직 포함 여부
- [ ] Playwright args 적용 필요 여부 (`--no-sandbox` 등)
- [ ] `config/.env` 자격증명 연동 방식

### 기존 코드 수정 요청 시
- [ ] DB 스키마 변경 여부 → `BackfillAgent` 영향 검토
- [ ] API 응답 형식 변경 → 대쉬보드 HTML 수정 필요 여부
- [ ] `PriceCalculator` 로직 변경 → 전체 국가 계산값 재검토
- [ ] 환율 조회 로직 변경 → HIRA 기준 36개월 평균 유지 확인

### 대쉬보드 변경 요청 시
- [ ] 기능 완성 후 배포 원칙 준수 확인
- [ ] Flask API 응답 형식과 프론트엔드 파싱 일치 여부
- [ ] 검색 → 표시까지의 전체 데이터 흐름 추적

---

## Codex API 사용 가이드

```python
# OrchestratorAgent가 코드 영향도 분석 시 사용하는 Codex 호출 패턴
# models: "gpt-4o", "o1-mini", "gpt-4-turbo"
# 용도: 코드 변경 사항 분석, 의존성 파악, 리스크 평가

SYSTEM_PROMPT = """
당신은 약가 모니터링 시스템의 아키텍처 분석 전문가입니다.
다음 규칙을 기반으로 요청된 코드 변경의 영향도를 분석합니다:
- BaseScraper 상속 구조 유지
- 용량별 개별 가격 레코드 원칙
- DB 스키마 일관성
- HIRA 조정가 계산 공식 유지
"""
```

---

## 작업 분배 기준

| 요청 유형 | 담당 에이전트 | 우선순위 |
|-----------|--------------|----------|
| 새 국가 스크레이퍼 | ForeignPriceAgent + 해당 Scraper | 높음 |
| 국내 약가 변경 | DomesticPriceAgent | 높음 |
| DB 스키마 변경 | db.py + BackfillAgent | 높음 (주의) |
| 대쉬보드 UI 변경 | DashboardAgent | 낮음 (기능 완성 후) |
| 환율 계산 변경 | exchange_rate.py (전체 영향) | 매우 주의 |
| API 엔드포인트 추가 | api/server.py | 중간 |

---

## 출력 형식 (WorkPlan)

```json
{
  "request_summary": "사용자 요청 요약",
  "rule_conflicts": ["충돌하는 룰 목록"],
  "affected_files": ["변경될 파일 목록"],
  "risk_level": "low | medium | high",
  "work_order": [
    {"step": 1, "agent": "ForeignPriceAgent", "task": "uk_mims.py 수정"},
    {"step": 2, "agent": "QualityGuardAgent", "task": "변경 후 검증"}
  ],
  "validation_check": "Keytruda 가격으로 최종 검증"
}
```
