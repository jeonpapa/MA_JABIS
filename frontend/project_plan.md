# Market Intelligence Dashboard

## 1. Project Description
Market Access 전문가를 위한 맞춤형 Market Intelligence 관리 및 분석 대시보드.
국내약가, 해외약가, 시장 점유율, 경쟁사 동향, 제품별 매출 추이 등을 인터랙티브 차트와 테이블로 시각화하여 한눈에 파악할 수 있는 SaaS 스타일 대시보드.

## 2. Page Structure
- `/` - Dashboard Overview (KPI 요약, 주요 지표 한눈에 보기)
- `/domestic-pricing` - Domestic Pricing (국내약가 테이블 및 분석)
- `/international-pricing` - International Pricing (해외약가 비교)
- `/market-share` - Market Share (시장 점유율 차트)
- `/competitor-trends` - Competitor Trends (경쟁사 동향 카드)
- `/product-sales` - Product Sales (제품별 매출 추이)

## 3. Core Features
- [x] 사이드바 네비게이션 (로고 + 메뉴)
- [x] Dashboard Overview (KPI 카드 + 요약 차트)
- [x] 국내약가 테이블 (검색/필터/정렬)
- [x] 해외약가 비교 테이블 (국가별 비교)
- [x] 시장 점유율 도넛 차트
- [x] 경쟁사 동향 카드 그리드
- [x] 제품별 매출 추이 라인 차트
- [x] 인터랙티브 차트 (recharts 라이브러리)
- [x] Mock 데이터 기반 시각화

## 4. Data Model Design
(Supabase 미연결 - Mock 데이터 사용)

### Mock Data
- 국내약가 데이터 (제품명, 성분, 약가, 보험코드, 변경일)
- 해외약가 데이터 (제품명, 미국/유럽/일본/중국 약가)
- 시장 점유율 데이터 (제품명, 점유율 %)
- 경쟁사 동향 데이터 (회사명, 동향 유형, 내용, 날짜)
- 제품별 매출 데이터 (월별, 제품별 매출액)

## 5. Backend / Third-party Integration Plan
- Supabase: 미연결 (향후 실제 데이터 연동 가능)
- Shopify: 불필요
- Stripe: 불필요

## 6. Development Phase Plan

### Phase 1: 전체 대시보드 UI 구현
- Goal: 6개 섹션 모두 Mock 데이터 기반으로 완성
- Deliverable: 완전히 작동하는 인터랙티브 대시보드
