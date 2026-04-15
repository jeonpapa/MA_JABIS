# Scraper 공통 규칙

## 필수 구현 사항

### 클래스 변수
```python
COUNTRY: str        # ISO 2자리 (JP/IT/FR/CH/UK/DE/US)
CURRENCY: str       # ISO 3자리 (JPY/EUR/CHF/GBP/USD)
SOURCE_LABEL: str   # 출처 명칭 (예: "MIMS online (UK public price)")
REQUIRES_LOGIN: bool
SOURCE_TYPE: str    # (선택) factory_ratio 적용 시 — "vidal", "compendium"
```

### 반환 형식 (search 메서드)
```python
[{
    "product_name":    str,   # 해당국 제품명 (H1 또는 공식 명칭)
    "ingredient":      str,   # INN (성분명)
    "dosage_strength": str,   # 용량+포장 규격 (예: "100mg/4ml conc, 2 vials")
    "dosage_form":     str,   # 제형 (주사, 정제 등)
    "package_unit":    str,   # 포장 단위 (PZN, NDC 등 국가 코드)
    "local_price":     float | None,  # 현지 통화 가격. None = 비급여
    "source_url":      str,   # 실제 접근한 URL
    "extra":           dict,  # 국가별 추가 정보 (company, 급여상태 등)
}]
```

## 국가별 접근 방식 요약

| 국가 | 소스 | 로그인 | 방식 | 가격 노출 |
|------|------|--------|------|-----------|
| JP | MHLW | 불필요 | CSV 다운로드 | 공개 |
| IT | AIFA | 불필요 | CSV 다운로드 | 공개 (ex-factory) |
| FR | Vidal | 필요 | Playwright | 로그인 후 |
| CH | Compendium | 불필요 | requests API | 공개 |
| UK | MIMS | 불필요 | Google Referer + Playwright | 공개 경유 |
| DE | Rote Liste | DocCheck | requests + Playwright | DocCheck 후 |
| US | Micromedex | 필요 | Playwright | 로그인 후 |

## Playwright 실행 표준

```python
# 기본 (대부분 국가)
browser = await pw.chromium.launch(headless=True)

# UK MIMS, US Micromedex
browser = await pw.chromium.launch(
    headless=True,
    args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
)

# DE Rote Liste
browser = await pw.chromium.launch(
    headless=True,
    args=["--no-sandbox", "--disable-setuid-sandbox",
          "--disable-dev-shm-usage", "--disable-http2"]
)
# DE는 wait_until='commit' + 충분한 wait_for_timeout(5000ms) 사용
```

## 오류 처리 원칙

- 네트워크 오류 → 빈 리스트 반환 (예외 raise 금지, 로그만)
- 로그인 실패 → 계속 진행, local_price=None 반환
- 가격 파싱 실패 → 해당 항목 None 처리, 나머지는 계속
- 0이하 가격 → None 처리 후 WARNING 로그

## 금지 사항

- `msd_only` 필터를 True로 하드코딩 금지
- 가격 임의값(0, -1 등) 반환 금지
- URL 하드코딩 없이 동적 탐색 필요한 경우 (AIFA CSV URL 등) 자동 탐색 구현
- 캐시 파일을 git에 커밋 금지 (`data/` 디렉터리 전체 gitignore)
