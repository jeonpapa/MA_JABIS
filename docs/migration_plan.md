# Readdy 목업 → MA AI Dossier v2 마이그레이션 플랜

**작성일**: 2026-04-18
**목적**: readdy.ai 목업(`_resource/project-8497617.zip`)을 기반으로 `data/dashboard_v2/` 에 React SPA 를 신규 구축. v1 HTML 은 cutover 전까지 병행 유지.

---

## 1. 최종 결정 사항

### 1.1 런타임
- **Vite + React 19 + react-router-dom v7 + Tailwind + recharts + lucide-react**
- `data/dashboard_v2/` 에 프로젝트 신규 생성, 목업 파일을 최대한 그대로 활용
- v1 HTML(`data/dashboard/*.html`) 은 P5 cutover 완료 전까지 존치

### 1.2 Flask 역할 재정의
- `api/server.py` 는 **JSON API 전용** (`/api/*`)
- 정적 페이지 서빙은 dev 환경에서만 유지, prod 는 Vercel 이 React 빌드 서빙
- Python 에이전트 파이프라인은 그대로 유지 (변경 없음)

### 1.3 라우트 구조
| 경로 | 용도 | 상태 |
|---|---|---|
| `/login` | admin 발급 계정 로그인 | 신규 |
| `/` | Dashboard Overview (MSD 카드 3종 + 키워드클라우드 + 변동Top10) | 신규 |
| `/domestic-pricing` | 국내약가 (검색·리스트·상세·Waterfall·이력) | 기존 2개 페이지 통합 |
| `/international-pricing` | A8 약가 / HTA / 허가 3탭 | 기존 2개 페이지 통합 |
| `/market-share` | Korean Market (Excel 드롭 기반) | 신규 |
| `/daily-mailing` | 뉴스 메일링 구독 설정 | 신규 |
| `/admin/users` | admin 전용 유저 관리 | 신규 (목업 Sidebar 팝업 그대로) |
| `/admin/pipeline` | MSD 파이프라인 CRUD | 신규 |
| `/admin/market-share` | 분기별 Excel 업로드 이력 | 신규 |
| `/workbench` | 협상 시나리오 빌더 | **P6 에서 포팅** |

### 1.4 데이터 gap 해결 방식
- **Korean Market**: 사용자가 `data/market_share/` 폴더에 분기별 Excel 드롭 → 파서가 unit/revenue 컬럼 읽어서 DB 저장 → 조회 시 점유율 계산
- **MSD 파이프라인**: 사용자 제공 데이터 → `/admin/pipeline` 에서 CRUD 관리 → `data/db/msd_pipeline.db` 저장
- **브랜드 트래픽 인덱스**: `agents/market_intelligence/media.py` 의 뉴스 수집 결과에 daily count aggregate 추가 → 7일 스파크라인 계산
- **Daily Mailing**: `mailing_configs` 테이블 신규 + `scheduler.py` 에 mailing job 추가 + smtplib 발송

### 1.5 인증
- **admin 발급 credentials 방식**: admin 이 유저 이메일+비번 생성 → 사용자가 팀원에게 수동 전달
- 목업의 Sidebar 설정팝업 (account/users 탭) 기능을 그대로 활용
- localStorage 아닌 서버 측 `users` 테이블 + bcrypt 해시 + JWT 토큰
- OAuth 사용 안 함

### 1.6 배포
- 프론트: Vercel (React 빌드)
- 백: Fly.io 또는 Railway (Flask + agents)
- TLS/SNI 이슈 회피 (`project_tls_remediation.md` 참조)

---

## 2. 기능 매핑: 현재 앱 vs 목업

### 2.1 바로 API 랩핑 가능 (이미 데이터 있음)

| 목업 기능 | 현재 에이전트/DB | 비고 |
|---|---|---|
| 국내약가 리스트/검색 | `agents/db/prices.py` | 검색 인덱스만 추가 |
| 국내약가 가격 변동 이력 | `agents/db/prices.py` (price_history 테이블) | Waterfall 계산은 프론트에서 |
| 해외 A8 급여약가 | `agents/db/foreign.py` + `exchange_rate.py` | 환율 변환 백엔드에서 처리 |
| HTA 현황 (NICE/CADTH/PBAC/SMC) | `agents/hta_scrapers/{uk_nice,canada_cadth,australia_pbac,scotland_smc}.py` | fullText 는 이미 저장 |
| 제외국 허가 현황 | `agents/foreign_approval/agent.py` + 10개 hta_scraper | indication-level 그대로 |
| Keytruda 급여/비급여 적응증 수 | `agents/db/indications.py` + `hira_sop` | 집계 쿼리만 추가 |
| 아날로그 약제 비교 | `agents/db/prices.py` (same ingredient) | 최대 3개 선택 |
| 최초 허가일 / RSA / 약평위 문서 | `agents/db/prices.py` + `hira_sop` | 상세정보 패널 |

### 2.2 신규 개발 필요

| 기능 | 신규 생성 파일 | 작업량 |
|---|---|---|
| Korean Market Excel 파서 | `agents/market_share/excel_ingester.py` | 중 |
| Korean Market DB 스키마 | `agents/db/market_share.py` | 소 |
| 시장 점유율 계산 API | `api/server.py` → `/api/market-share` | 소 |
| MSD 파이프라인 DB | `agents/db/msd_pipeline.py` | 소 |
| MSD 파이프라인 CRUD API | `api/server.py` → `/api/admin/pipeline` | 소 |
| 브랜드 트래픽 daily count | `agents/market_intelligence/daily_aggregate.py` | 중 |
| Daily Mailing 스키마 | `agents/db/mailing.py` (`mailing_configs`, `mailing_logs`) | 소 |
| Daily Mailing 발송 job | `scheduler.py` 확장 + `agents/mailing/sender.py` | 중 |
| 유저 인증 스키마 | `agents/db/users.py` (bcrypt + JWT) | 중 |
| 인증 미들웨어 | `api/auth.py` (JWT verify) | 소 |

---

## 3. Phase 별 실행 플랜

### Phase 0 — API 계약 설계 (0.5~1일)
**목표**: 목업 mock 데이터 shape 을 그대로 읽어서 Flask `/api/*` 응답 스펙 정의
- [ ] `docs/api_contract.md` 작성: 각 라우트별 request/response JSON shape
- [ ] 각 shape 이 현재 `agents/db` 에서 어떻게 생산되는지 매핑
- [ ] OpenAPI 스펙으로 변환 (선택)

**산출물**: `docs/api_contract.md`

### Phase 1 — React 앱 부트스트랩 (3~5일)
**목표**: React 앱 기본 골격 + 인증 + 레이아웃
- [ ] `data/dashboard_v2/` 에 Vite 프로젝트 생성 (목업 파일 복사)
- [ ] 의존성 설치 (package.json 유지), TypeScript strict 설정
- [ ] `agents/db/users.py` + bcrypt + JWT 구현
- [ ] `api/server.py` 에 `/api/auth/login`, `/api/auth/me` 추가
- [ ] React 측 AuthGuard 를 서버 토큰 방식으로 수정 (localStorage 만 쓰는 목업 로직 교체)
- [ ] Sidebar + Layout + 라우팅 6개 스텁 페이지
- [ ] `/login` + `/admin/users` (목업 팝업 기능 그대로)

**산출물**: 로그인 가능한 빈 SPA, admin 이 유저 생성 가능

### Phase 2 — 기존 데이터 연결 (5~7일)
**목표**: 현재 구축된 데이터를 목업 UI 에 연결
- [ ] `/api/domestic-pricing/list`, `/api/domestic-pricing/{id}` → `/domestic-pricing` 페이지
- [ ] PriceWaterfall 차트: price_history 에서 계산
- [ ] 아날로그 비교 API: 동일성분 검색
- [ ] `/api/international-pricing/search`, `/api/international-pricing/{id}` → `/international-pricing` 3탭
- [ ] A8 급여약가 탭: foreign.py 데이터 + 환율 변환
- [ ] HTA 탭: 4개 기관 (NICE/CADTH/PBAC/SMC) fullText 포함
- [ ] 허가 탭: 10개 기관 indication-level 데이터
- [ ] Home 의 "한국MSD 급여 약제 수" + "Keytruda 적응증" 카드 연결

**산출물**: `/domestic-pricing`, `/international-pricing`, Home 카드 2개 실데이터

### Phase 3 — 신규 데이터 파이프라인 (5~7일)
**목표**: 신규 기능 + 데이터 수집 파이프라인
- [ ] **Korean Market**:
  - [ ] `data/market_share/` 폴더 규칙 정의
  - [ ] `agents/market_share/excel_ingester.py` (unit/revenue 파서)
  - [ ] `agents/db/market_share.py` 스키마
  - [ ] `/admin/market-share` 업로드 이력 페이지
  - [ ] `/market-share` 도넛 + 트렌드 차트
- [ ] **MSD 파이프라인**:
  - [ ] `agents/db/msd_pipeline.py` 스키마
  - [ ] `/api/admin/pipeline` CRUD
  - [ ] `/admin/pipeline` 페이지 (추가/수정/삭제)
  - [ ] Home 의 "New Pipeline 현황" 카드 연결
- [ ] **브랜드 트래픽**:
  - [ ] `agents/market_intelligence/daily_aggregate.py` (news count by brand)
  - [ ] `/api/home/brand-traffic` 7일 스파크라인
  - [ ] Home 의 KeywordCloud + BrandTraffic 컴포넌트 연결
- [ ] **Daily Mailing**:
  - [ ] `mailing_configs` + `mailing_logs` 스키마
  - [ ] `agents/mailing/sender.py` (smtplib)
  - [ ] `scheduler.py` 에 mailing job 추가
  - [ ] `/daily-mailing` 페이지 설정 저장/로드 API

**산출물**: 6개 페이지 모두 실데이터, Home 모든 컴포넌트 live

### Phase 4 — QA & QualityGuard 통합 (2~3일)
**목표**: 회귀 확인 + Playwright sweep 적응
- [ ] `QualityGuardAgent` 에 v2 API 응답 스키마 검증 추가
- [ ] Playwright sweep 을 v2 SPA 로 확장 (`verify_all_dashboard_v2.py`)
- [ ] Keytruda baseline 회귀 확인 (8개 MFDS 케이스)
- [ ] 성능: 초기 로딩 < 2s, API p95 < 500ms

**산출물**: 일일 자동 sweep 통과

### Phase 5 — Cutover (1~2일)
**목표**: v1 → v2 전환
- [ ] Flask routes 분리: `/api/*` 만 유지, `/dashboard/*` 는 dev-only
- [ ] Vercel + Fly.io 배포 설정
- [ ] 팀원에게 계정 발급 + URL 공유
- [ ] v1 HTML 파일 제거 (`data/dashboard/` 삭제)
- [ ] v1 테스트 스크립트 (`verify_all_dashboard.py`) 제거

**산출물**: 단일 v2 앱, 팀 실사용

### Phase 6 — Workbench 포팅 (2~3일)
**목표**: 협상 시나리오 빌더를 6번째 메뉴로 추가
- [ ] 기존 `data/dashboard/workbench.html` 로직을 React 로 포팅
- [ ] `agents/workbench/*` 백엔드는 그대로 활용
- [ ] `/workbench` 라우트 + 메뉴 추가
- [ ] xlsx 다운로드는 기존 `exporter.py` API 호출

**산출물**: 완전한 v2 앱 (7개 메뉴)

---

## 4. 현재 남아 있는 사용자 제공 필요 데이터

구현 진행하며 다음 항목은 **사용자에게 요청**합니다:

1. **MSD 파이프라인 정보** (Phase 3)
   - 연도별 예상 허가 일정 (올해 / +1 / +2)
   - 파이프라인 제품명 · 적응증 · Phase
   - → `/admin/pipeline` 페이지로 직접 입력 가능하게 UI 먼저 만듦

2. **Korean Market Excel 샘플** (Phase 3 초기)
   - 파일 1개만 있으면 파서 스키마 확정 가능
   - 컬럼명·시트 구조 확인용
   - → `_resource/` 에 샘플 파일 요청 예정

3. **SMTP 계정 정보** (Phase 3)
   - Daily Mailing 발송용 SMTP 서버 / 계정 / 비번
   - 또는 SendGrid / SES 같은 외부 서비스 API 키
   - → `config/.env` 에 추가

4. **배포 서비스 계정** (Phase 5)
   - Vercel / Fly.io 계정 연결 준비

5. **브랜드 트래픽 대상 리스트** (Phase 3)
   - 현재 `market_intelligence` 가 수집하는 브랜드 외 추가할 것 있는지
   - 현재 확인 가능한 것: Keytruda, Opdivo, Tagrisso, Enhertu, Imfinzi

---

## 5. 총 예상 일정

| Phase | 기간 | 누적 |
|---|---|---|
| P0 API 계약 | 0.5~1일 | 1일 |
| P1 부트스트랩 | 3~5일 | 6일 |
| P2 기존 데이터 연결 | 5~7일 | 13일 |
| P3 신규 파이프라인 | 5~7일 | 20일 |
| P4 QA | 2~3일 | 23일 |
| P5 Cutover | 1~2일 | 25일 |
| P6 Workbench | 2~3일 | 28일 |

**총 4주 목표**. 각 Phase 완료 시 사용자 확인 후 다음 단계 진입.

---

## 6. 회귀 방지 체크리스트

- [ ] QualityGuard 일일 리뷰 유지 (스키마/가격 이상값/환율)
- [ ] MFDS 8개 baseline 회귀 체크 유지 (2026-04-17 NSCLC adj 사례)
- [ ] Keytruda baseline 최종 검증 (모든 신규 기능)
- [ ] 적응증 단위 수집 원칙 유지 (브랜드 단위 금지)
- [ ] 단방향 데이터 흐름 유지 (v2 SPA 도 read-only)
