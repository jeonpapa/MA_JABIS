# ForeignPriceAgent 규칙

## 역할
국가별 스크레이퍼를 실행하고 환율 계산 후 DB에 저장.

## 구현된 국가 (AVAILABLE_COUNTRIES)
JP, IT, FR, CH, UK, DE, CA (US는 구독 확인 필요)

## 핵심 원칙: 제형(form_type) 단위 저장

동일 약제라도 국가별로 oral(경구제) 과 injection(주사제) 의 가격이 **다르다**.
브랜드(=query_name) 단위로 합쳐 저장하면 A8 비교가 깨진다.

- **모든 스크레이퍼는 검색 결과 각 row 에 `form_type` 을 붙여야 한다** (oral | injection | unknown)
- `BaseScraper._resolve_form_type()` 가 item 의 `dosage_form` / `product_name` / `extra` 를
  `agents/scrapers/formulation.py::detect_form()` 에 태워 자동 판정
- 스크레이퍼가 item 에 `form_type` 을 직접 넣으면 그 값 우선 (normalize_form_type 적용)
- 제형별 가격 차이가 큰 약제(예: Prevymis — UK oral tab, DE injection vial): 반드시 제형 분리 유지
- 대쉬보드는 `form_type` 필터(전체/oral/injection) 로 A8 비교 제공

### 신규 국가 추가 시 form_type 체크리스트
1. 해당국 DB 의 제형 컬럼을 item["dosage_form"] 에 채운다
2. 로컬 약어(예: DE "Filmtabl", "Inf Konz") 를 `formulation.py` 의 ORAL_KEYWORDS / INJECTION_KEYWORDS 에 추가
3. Prevymis 로 검증 — oral/injection 각각 정확히 분류되는지 확인

## 국가별 소스 매핑 (2026-04 기준)

| 국가 | 소스 | 로그인 | 제형 커버리지 |
|------|------|--------|---------------|
| JP | MHLW 薬価基準 Excel | 불필요 | 内用薬/注射薬/外用薬 별 워크북 분리 → oral/injection 각각 |
| IT | AIFA Class A (retail) + Class H (hospital) CSV | 불필요 | Class A=prezzo al pubblico(oral), Class H=ex-factory(injection) |
| FR | base-donnees-publique.medicaments.gouv.fr (BDPM) | 불필요 | CIS_bdpm.txt + CIS_CIP_bdpm.txt. 주사제는 CIP 공시가 자주 없음 (None 허용) |
| CH | Swissmedic Compendium | 불필요 | Filmtabl/Gran(oral) + Inf Konz(injection) 혼재 |
| UK | MIMS | 필요 | 경구제 중심, injection 없으면 결과 1건 |
| DE | Rote Liste | 필요 | Pharmazie 리스트, 제형 기본 injection |
| CA | Ontario EAP HTML 테이블 | 불필요 | Tab(oral) + Inj Sol(injection) 혼재. 단일 연방 DB 없음 — 온타리오주 대표 |
| US | Micromedex (RedBook) | 필요/미구현 | 구독 승인 후 추가 |

## 새 국가 추가 시 필수 작업
1. `agents/scrapers/{cc}_{source}.py` 생성 (BaseScraper 상속)
2. `foreign_price_agent.py` 에 import + `_build_scraper()` 케이스 추가
3. `AVAILABLE_COUNTRIES` 리스트에 추가
4. `base.py` `ENV_KEY_MAP` 에 자격증명 키 추가 (로그인 필요 시)
5. `config/.env` 템플릿에 키 추가
6. 제형 약어 확인 → `formulation.py` 업데이트
7. Prevymis + Keytruda 로 validation 테스트

## source_type 처리
```python
# scraper에서 SOURCE_TYPE 읽기 (raw_data JSON 문자열 아님)
source_type = getattr(scraper, "SOURCE_TYPE", None)
# PriceCalculator에 전달
calc = self.calculator.calculate_adjusted_price(
    ..., source_type=source_type
)
```

factory_ratio 매핑:
- CH compendium → 0.65
- FR vidal → 0.65 (현재 BDPM 사용으로 source_type=None, 기본 ratio)
- IT Class H ex-factory → `aifa_exfactory` (이미 공장도가 → ratio=1.0)
- 그 외 → None (기본 ratio)

## 환율 적용 원칙
- KEB하나은행 36개월 평균 환율
- 환율 조회 실패 시 → adjusted_price_krw = None (DB 저장은 진행)
- EUR 은 FR/IT/DE 공용

## DB 저장 시 필수 필드
`searched_at`, `query_name`, `country`, `product_name`, `ingredient`,
`dosage_strength`, `dosage_form`, `local_price`, `currency`,
`source_url`, `source_label`, `raw_data`, `form_type`
