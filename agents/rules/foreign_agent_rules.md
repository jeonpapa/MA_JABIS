# ForeignPriceAgent 규칙

## 역할
국가별 스크레이퍼를 실행하고 환율 계산 후 DB에 저장.

## 구현된 국가 (AVAILABLE_COUNTRIES)
JP, IT, FR, CH, UK, DE (US는 구독 확인 필요)

## 새 국가 추가 시 필수 작업
1. `agents/scrapers/{cc}_{source}.py` 생성 (BaseScraper 상속)
2. `foreign_price_agent.py`에 import + `_build_scraper()` 케이스 추가
3. `AVAILABLE_COUNTRIES` 리스트에 추가
4. `base.py` `ENV_KEY_MAP`에 자격증명 키 추가
5. `config/.env` 템플릿에 키 추가
6. Keytruda로 validation 테스트

## source_type 처리
```python
# scraper에서 SOURCE_TYPE 읽기 (raw_data JSON 문자열 아님)
source_type = getattr(scraper, "SOURCE_TYPE", None)
# PriceCalculator에 전달
calc = self.calculator.calculate_adjusted_price(
    ..., source_type=source_type
)
```

## 환율 적용 원칙
- KEB하나은행 36개월 평균 환율
- 환율 조회 실패 시 → adjusted_price_krw = None (DB 저장은 진행)
- EUR은 FR/IT/DE 공용

## DB 저장 시 필수 필드
`searched_at`, `query_name`, `country`, `product_name`, `ingredient`,
`dosage_strength`, `local_price`, `currency`, `source_url`, `source_label`, `raw_data`
