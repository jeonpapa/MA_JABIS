# 홈 · Competitor Trends · Access Insight 강화 — 실행 PLAN (2026-07-06)

단일 소스. 구현 fable-5 서브에이전트 + **plan 준수 검수** fable-5 서브에이전트가 이 문서를 기준으로 판정.
배포는 사용자 직접([[feedback_deploy_confirm_gate]]). 모든 구현·검증 fable-5([[feedback_fable5_subagents]]).

---

## GROUP HOME (A1 + A2) — 독립

### A1 — 정부기관별 최근 7일 누적 워드클라우드 (빈도 기반)
- 데이터: `competitor_news` where `kind='gov_policy'`, `brand`=기관명, `pub_date`, `title`+`description`. 이미 존재 — 크롤 변경 불필요(daily 06:30 3d + weekly 31d).
- 신규 백엔드: 기관별 최근 7일(`pub_date >= date('now','-7 day')`) `title+description` 토큰화 → 빈도 카운트 → `{agency:[{text,count}]}`. **LLM 아님, 순수 빈도.** 한국어 토큰: 2+글자 명사류 정규식 + 불용어(조사/일반동사/숫자) 제거. keep/stop 힌트 = `editable_factors.get_context_anchors()`. 기관당 상위 ~30단어.
- 기관 목록: `editable_factors.get_gov_agencies()`(news_keyword_factor gov_seed) + 상수 폴백. S4 영문태그(NATIONAL_ASSEMBLY/PATIENT_GROUP/MEDICAL_SOCIETY)는 **국회/환자단체/의료진**으로 한글 표기.
- 신규 엔드포인트 `GET /api/home/gov-agency-clouds` → `{generated_at, window_days:7, agencies:[{agency, label, keywords:[{text,count}], newsByKeyword?}]}`. 일자 캐시(`data/cache/gov_summary/gov_agency_clouds_{today}.json`). 기존 `/api/home/government-keyword-summary`(LLM 31d)는 보존 or 대체 — **대체**(정부 위젯 목적이 약함): 홈은 신규 per-agency 클라우드를 1차로.
- 프론트: `KeywordCloud.tsx` 좌측 gov 컬럼을 **기관 탭(또는 스택형 미니 클라우드)** 로 재구성, `getCloudStyle(weight)` 재사용(weight=count 정규화). `home.ts` 신규 raw/adapter. 정적 부제(L151) 동적화. 근거뉴스 드로어 유지 가능 시 유지.

### A2 — 브랜드 언급 스파크라인 + '상승' 기준 = 이전 7일
- 14일 일별 카운트 필요(현재 7 + 이전 7). `media_intelligence.get_brand_traffic` 기본 window를 **14일**로(현 `days_in_last_month()`). 캐시 파일명에 `days` 포함(현재 미포함 → 당일 반영 안 됨).
- 스파크라인 = 최근 7일 slice. 상승 = `sum(last7) vs sum(prev7)` 증감율. `home.ts computeChange`(현 전/후반 반반, L303-311) → 7v7 교체. 사용처 L346.
- 라벨: `KeywordCloud.tsx` "전반기 대비"(L382)→"이전 7일 대비", periodLabel(L95) "이전 7일 기준". `trafficIndex/total_count` 의미는 7일 총계로 통일.

### A3 — 뉴스: 데일리 최신 유지 (변경 없음)
- `/api/home/brand-news`(live sort=date, window·캐시 없음) 독립 — A1/A2 무영향. 손대지 않음.

---

## GROUP COMPETITOR (B1 + B2 + B3) — competitor_trends 계열

### B1 — 유사 기사 한 카드로 클러스터
- `competitor_news.trend_id` 컬럼 존재하나 항상 NULL — 이걸 카드↔기사 링크로 활용. 카드 = 이벤트 단위, N개 매체 표시.
- 클러스터 지점: `competitor_trends_agent.promote_from_archive()`(및 `run()`). LLM 계약을 `news_index:int` → `news_indexes:[int]`(같은 이벤트 묶음)로 변경, GPT-4o-mini가 배치 내 그룹핑. `_upsert_trend()`는 이벤트 대표(최고신뢰=최저 tier) URL을 primary로, 멤버 기사는 `competitor_news.trend_id` UPDATE.
- 카드 모델: `_competitor_row_to_dict`에 `sources:[{name,url,tier,pub_date}], source_count`. 프론트 카드 푸터 "N개 매체". `competitorTrends.ts` 인터페이스 확장.

### B2 — 고신뢰(매체 weight) 기사 노출
- weight=tier(`config/media_tiers.json`: tier1=전문지 24, tier2=종합 25, 미등록=3). `classify_tier(url)`.
- `run()` 경로는 현재 tier 분류 없음 → `classify_tier` 적용, tier3 제외/강등.
- `competitor_trend`에 `source_tier INTEGER` 추가(schema + `_COMPETITOR_COLS` + row_to_dict + TS). `list_news()` ORDER BY `tier ASC, pub_date DESC`. 클러스터 대표 = 최저 tier(최고 신뢰).

### B3 — admin = 동향 추가가 아닌 스크래핑 필터·키워드 관리
- Phase 2 자산 재사용: `competitor_brand`(검색 브랜드/쿼리) + `news_keyword_factor`(relevance 키워드) + 에디터 `CompetitorBrandsEditor.tsx`/`NewsFactorsEditor.tsx`.
- `admin/competitor-trends/page.tsx`를 **두 에디터 + 크롤 트리거** 조합으로 재작성. 수동 카드 추가/편집 UI 제거(엔드포인트·`source_type='manual'` 보존 로직은 유지 — 백필/교정용). 스테일 브랜드 문구(L172) 수정.
- **배선 갭 수정**: `news_keyword_factor(scope='competitor',kind='relevance')`는 편집되나 런타임 소비자 없음. `editable_factors.get_competitor_relevance_terms()` 로더 신설 → `_is_relevant`(competitor_news_agent) / 제목 가드(competitor_trends_agent)에 배선 → admin 키워드가 실제 필터 제어.

---

## GROUP AI-BACKEND (B7 + B6 flag + B5 committee logic)

### B7 — 신호 라벨 별도 DB (선행)
- `amjilsim_signature_lexicon`(존재·빈·미사용) 활용. 컬럼 보강: `priority INTEGER, is_active INTEGER DEFAULT 1, match_mode TEXT DEFAULT 'substring'`(기존 token/category/signal_type/weight 유지). 멱등 ALTER.
- 큐레이션 시드: classify `_KEYWORDS` 이관 + 오분류 콜리전 교정(`의원`→`국회의원`/`의원 발의`, `통과`→`약평위 통과`/`급여 통과`, `실적/매출` 범위 축소, `교수/전문가`는 인용 맥락 한정, `예정` 제거/약화). match_mode word-boundary 옵션.
- `classify.classify_signal_type()`가 DB 로더(캐시, priority 정렬) 우선, 상수 폴백. fallback 전량-IR_RELEASE 완화(미매칭은 신뢰 낮은 `UNCLASSIFIED` 또는 kind 기반).
- 관리 CRUD `GET/POST/PATCH/DELETE /api/admin/signal-lexicon`.
- **재분류 잡**(INSERT-only 예외, 명시적): 기존 rows `signal_type/weight/signal_phrases` UPDATE. CLI `--reclassify-signals-now` + 로더 캐시 무효화. 재분류는 삭제 없이 UPDATE만.

### B6 — 항암/일반 플래그 (데이터 신설)
- 신뢰 플래그 없음. `amjilsim_drugs`에 `is_oncology INTEGER`(또는 `drug_class TEXT CHECK('oncology','general')`) 신설. 파생 캐스케이드: ① ATC L01/L02 ② efficacy_group='항악성종양제' ③ analog_reports.disease_category='항암'(brand join) ④ indication 암키워드 휴리스틱 ⑤ 잔여 수동(≈65행, 1회). 백필 스크립트 + 멱등.
- API: leaderboard/drugs/journey에 `is_oncology` 필드 노출. leaderboard `?class=oncology|general` 필터(WHERE 또는 응답 태깅).

### B5 — 비항암 위원회 + (프론트) 리더보드 검색
- 위원회 배정 drug-type-aware: `backfill.nearest_session_id()`를 committee-aware로(약제 예상 위원회로 필터). 항암→AMJILSIM(암질심), 비항암→급여기준소위(신규). `amjilsim_sessions.committee_type` CHECK에 `BENEFIT_SUBCOMMITTEE` 추가(멱등 migration). 급여기준소위 세션 일정 데이터 없으면 라벨만(날짜 세션 강제 배정 금지).
- 표기 매핑(프론트, GROUP AI-FRONTEND): AMJILSIM→**DREC**, YAKPYUNGWI→**ODAC**, BENEFIT_SUBCOMMITTEE→**급여기준소위(BSC)**. 비항암 약제의 pre-committee는 DREC 아닌 급여기준소위로.

---

## GROUP AI-FRONTEND (B4 graph/labels + B5 search + B6 filter) — AI-BACKEND API 이후

### B4 — 그래프/분류/주단위/표기
- `AccessInsightView.tsx buildChart()`: 월별→**주별**, 12개월→**최근 6개월(182일)**. `monthFloor/addMonths`→`weekFloor/addWeeks`(~26버킷). `domain[0]`을 첫 버킷 시작에 정렬(또는 categorical band)로 **축/버킷 정렬 깨짐 수정**. tick formatter/tooltip labelFormatter/헤더 문구("최근 6개월 주별") 갱신.
- 위원회 표기: `COMMITTEE_LABELS={AMJILSIM:'DREC',YAKPYUNGWI:'ODAC',BENEFIT_SUBCOMMITTEE:'BSC'}` 맵을 렌더 5개 지점(ReferenceLine L168, 배지 L270, 헤더 L497, 마일스톤 L52-53) 적용. DB enum 불변.
- 분류: B7 재분류 반영(신호 색/라벨 SIGNAL_COLORS/LABELS 정합).

### B5 — 리더보드 검색
- '약제 선택' `<select>`(L400-412) 제거 → **검색 input**(brand_kr substring 필터). 기존 `/api/access-insight/drugs` 페이로드로 클라이언트 검색.

### B6 — 항암/일반 필터
- 리더보드/그래프에 **항암제/일반약제** 토글. `is_oncology` 기반. `?class=` 파라미터 또는 클라이언트 필터.

---

## 시퀀싱
- Wave 1(병렬): HOME, COMPETITOR, AI-BACKEND. server.py는 각기 다른 리전(home ~2780 / competitor ~5215-5378 / access-insight ~6243) — Edit 공존. schema.py 다른 테이블. 컨트롤러가 통합 컴파일 검증.
- Wave 2: AI-FRONTEND(AI-BACKEND의 is_oncology·committee API 확정 후).
- 각 Wave 후 **plan 준수 검수 fable-5 서브에이전트**: 이 문서 대비 항목별 이행/누락/이탈 판정 + 테스트/빌드 확인. 이탈분 수정 후 커밋.

## 검증 공통
- `.venv` pytest 관련 서브셋 + `cd frontend && npm run build`/`tsc --noEmit`.
- 대표 검증: A1 기관별 7일 클라우드 실데이터 단어, A2 7v7 상승, B1 클러스터 N매체, B7 재분류 후 분포(IR 편중 완화), B4 주별 그래프 렌더, B6 필터 분할.
- git: 서브에이전트는 커밋 금지(컨트롤러가 리전만 스테이징). `.claude/settings.json`·`data/foreign/*` 제외.
