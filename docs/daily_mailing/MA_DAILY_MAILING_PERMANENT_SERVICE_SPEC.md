# MA Daily Mailing Permanent Service Spec

작성일: 2026-07-02
대상: MA_JABIS / MA AI Dashboard Daily Market Access Intelligence Mailing
상태: Permanent service blueprint / draft-only default

## 1. 서비스 정의

MA Daily Mailing은 단순 뉴스 클리핑이 아니라, MA AI Dashboard에서 관리하는 관심 영역·키워드·소스 범위를 기반으로 매일 06:00 KST에 최근 24시간의 주요 제약·바이오·정책 뉴스를 선별하고, Korea Market Access 관점의 insight를 생성하는 전문 briefing 서비스다.

핵심 목적은 “많은 기사 수집”이 아니라, 국내 급여·약가·HIRA/HTA·정책·경쟁품 접근성·MSD watchpoint에 실제로 의미 있는 신호를 골라내는 것이다.

## 2. 절대 원칙

1. 기본 모드는 live-send가 아니라 Gmail draft / preview 생성이다.
2. 승인 전 실발송은 불가능해야 한다.
3. BioSpectator 및 포워딩된 기존 monitoring email은 calibration input이며 live source candidate가 아니다.
4. 모든 수집·API·draft·delivery path에 previous-24h filter가 적용되어야 한다.
5. Korea MA implication은 방어 가능한 경우에만 작성한다.
6. MSD 또는 제품명 단순 언급은 Top Signal이 아니라 Watchlist 또는 제외로 처리한다.
7. artifact는 항상 상태를 가진다: pilot draft / quality-gated draft / reviewed draft / approved final.

## 3. 에이전트 구성

### 3.1 Scope Agent
- Dashboard의 관심 영역, 키워드, source registry, recipient group, lookback window를 읽는다.
- 실행 시점 config snapshot을 run artifact에 저장한다.

### 3.2 Source Intake Agent
- Tier 1 source registry: HIRA, MOHW, MFDS, 공식 보도자료, 주요 제약·바이오 전문지, 주요 경제/종합 매체.
- Naver News API/search는 discovery channel로 사용하고, 원문 domain 기준으로 authority를 평가한다.
- BioSpectator/forwarded email은 calibration_only로 분류하고 live candidate에서 제외한다.

### 3.3 Triage & Verification Agent
- 중복 제거, 원문 링크 정규화, 발행시각 검증, previous-24h filter 적용.
- source_status 부여: official_verified / publisher_verified / media_report_only / calibration_only / excluded.
- quality_flags 부여: official_cross_check_required, publisher_verified_required, low_value_msd_mention, calibration_source_not_live_candidate 등.

### 3.4 MA Analyst Agent
- 기사별 MA relevance를 0–5로 점수화한다.
- Top Signal은 원칙적으로 ma_relevance >= 3만 허용한다.
- 국내 급여·약가·위원회·정책·경쟁품 접근성·환자 접근성 관점의 실질적 함의만 유지한다.

### 3.5 Writer Agent
기사별 작성 순서:
1. 주요 내용: 최대 3개 factual bullet
2. Insight: 기사-specific MA 해석
3. Korea MA implication: 방어 가능할 때만 작성, 아니면 생략
4. MSD Watchpoint: 직접 관련 있을 때만 작성

금지:
- generic implication 반복
- 모든 기사에 억지 implication 부여
- 공식 사실과 언론 추정을 혼합
- BioSpectator/포워딩 메일 문구 복사

### 3.6 Reviewer Agent
- implication defensibility, boilerplate similarity, source eligibility, Top Signal 적합성을 검토한다.
- 실패 시 review_status를 needs_review/rejected/excluded로 둔다.

### 3.7 Delivery Agent
- Gmail API/OAuth 기반 draft 생성.
- gmail_draft_id, gmail_message_id/thread_id, subject/body checksum, artifact path, recipient group, approval_status를 저장한다.
- live-send는 approved final + allowlist + pre-send QA 통과 후 별도 action에서만 가능하다.

## 4. Review Board Lanes

1. Dashboard Scope
2. Source Intake
3. Triage / Verify
4. Writer Agent
5. Review Board
6. Delivery / History

각 card 필수 필드:
- title, url, source_name, source_tier
- published_at, discovered_at
- source_status, verification_method
- ma_relevance, priority
- review_status
- quality_flags
- selected_for_draft
- excluded_reason
- next_action

## 5. Quality Gates

### Gate 1. Source Eligibility
Sendable item은 아래 중 하나를 만족해야 한다.
- official source verified
- Tier A/B publisher page verified
- media_report_only는 needs_review 또는 Watchlist
- calibration_only는 live candidate 제외

### Gate 2. Previous-24h Window
- collection, digest API, dashboard preview, Gmail draft 생성, cron path 모두 동일하게 적용.

### Gate 3. MA Relevance
- 5: 직접적인 국내 급여/약가/위원회/정책/협상 영향
- 4: 강한 payer/access/HTA/pricing consequence
- 3: 향후 등재/재평가/access consequence 가능
- 2: 임상/허가 신호이나 Korea MA 연결 약함
- 1: 회사명/제품명 단순 언급
- 0: 제외

### Gate 4. Implication Defensibility
- 기사-specific 명사/환자군/비교약제/위원회 단계/가격·급여 기준/후속 milestone이 없으면 implication을 작성하지 않는다.

### Gate 5. Boilerplate Similarity
- 여러 기사에서 같은 generic 문장이 반복되면 pre-send QA 실패.

### Gate 6. Approval & Send
- approval_status != approved_final이면 live-send 금지.
- recipient allowlist 통과 필요.
- pre-send QA 통과 필요.

## 6. 운영 기본값

- schedule: 매일 06:00 KST
- mode: draft-only
- live_send_allowed: false
- auth: Gmail API/OAuth
- cron: idempotency key `ma_daily:{YYYY-MM-DD}:kst:{recipient_group}`
- failure handling: QA/API/source/draft 저장 실패 시 Slack operational alert
- success handling: draft 생성 결과와 review board link/path만 요약

## 7. Run Artifact

권장 파일/DB 구조:
- runs/YYYY-MM-DD.json
- sources/YYYY-MM-DD.json
- review_boards/YYYY-MM-DD.json
- renders/YYYY-MM-DD.html
- qa/YYYY-MM-DD.json

DB 저장 필수:
- run_id, started_at_utc/kst, completed_at_utc/kst
- mode, lookback_hours, dashboard_scope_snapshot
- delivery_status, approval_status
- gmail_draft_id, gmail_message_id, sent_at
- artifact paths, source counts, selected counts, excluded counts
- qa results, failure logs, retry count

## 8. 우선 보강 과제

1. official Tier 1 live collector 명확화/추가.
2. Gmail draft ID와 approval/send state를 DB persist path에 완전 저장.
3. approval_status != approved_final이면 live-send 함수가 절대 호출되지 않는 테스트 추가.
4. cron/CI에서 `PYTHONPATH=.` 또는 packaging 설정 보장.
5. 24h filter가 모든 delivery path에서 강제되는 테스트 추가.
6. low-value MSD mention suppression 테스트 유지/강화.

## 9. 이메일 포맷

Subject 예시:
`[MA Daily Briefing] YYYY-MM-DD Korea Market Access Signals`

본문 구조:
1. Header: 기준일, 수집기간, artifact status
2. Executive Summary: Top Signal 2–3개, Watchlist count, no-major-signal note if applicable
3. Top Signals: ma_relevance >= 3 중심
4. Watchlist: 모니터링 필요 항목
5. Excluded Summary: 제외 사유 count 중심
6. Source & QA Note: source registry, verification caveat, draft/review status

## 10. 실수 방지 메모

- “최종본”이라고 부르려면 approved final 상태와 QA 통과가 필요하다.
- draft가 존재한다고 발송 가능한 것은 아니다.
- 포워딩 이메일/BioSpectator는 writer calibration이지 live source가 아니다.
- MSD mention은 relevance 신호일 수 있지만 Top Signal 조건은 아니다.
- defensible implication이 없으면 과감히 생략한다.
