# DomesticPriceAgent 규칙

## 역할
HIRA(건강보험심사평가원) 약가고시 Excel을 다운로드하고 DB에 적재.

## 데이터 소스
- URL: HIRA 약가고시현황 게시판
- 파일 형식: Excel (.xlsx)
- 갱신 주기: 월 1회 (매월 1일 전후)

## 처리 흐름
1. HIRA 게시판 접속 → 최신 게시물 Excel URL 탐색
2. 파일명·날짜로 신규 여부 확인 (중복 다운로드 방지)
3. `data/raw/`에 저장 → pandas로 파싱
4. `DrugPriceDB.save_domestic_prices()` 호출
5. 변동 내역(신규/삭제/가격변동) 감지 → 대쉬보드 소스 파일 생성

## DB 저장 시 필수 필드
`insurance_code`, `drug_name`, `ingredient`, `manufacturer`,
`package_unit`, `price_krw`, `apply_date`, `source_file`

## 변동 감지 원칙
- 같은 보험코드(insurance_code)로 이전 가격과 비교
- 신규 등재: 이전 기록 없음
- 삭제: 이번 Excel에 없는 이전 코드
- 가격변동: ±1원 이상 차이
