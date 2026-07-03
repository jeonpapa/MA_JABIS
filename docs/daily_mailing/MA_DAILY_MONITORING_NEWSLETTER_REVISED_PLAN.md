# MA Daily Monitoring Newsletter — Revised Service Plan

작성일: 2026-07-02
상태: Joseph correction 반영 / monitoring-first model

## 1. 정정된 서비스 정의

이 서비스는 “MA relevance가 높은 기사만 선별하는 briefing”이 아니다.

정정된 목적은 다음과 같다.

> 사용자가 MA AI Dashboard에서 지정한 관심 키워드, 회사명, 브랜드명, 제품명, 질환, 정책 주제를 포함하는 최근 24시간 기사를 수집하고, 이를 읽기 좋은 daily monitoring newsletter로 구성해 매일 아침 06:00 KST에 draft/발송하는 서비스.

따라서 높은 MA 신호만 통과시키는 좁은 gate는 부적절하다. 모니터링 목적상 MA implication이 낮더라도 사용자가 지정한 키워드/회사/브랜드와 관련 있으면 newsletter에 포함될 수 있어야 한다.

## 2. 핵심 원칙

1. Inclusion 기준의 중심은 `user-selected keyword/company/brand match`다.
2. MA relevance는 제외 gate가 아니라 분류/우선순위/해석 depth 조절 기준이다.
3. `Korea MA implication`은 방어 가능할 때만 작성한다.
4. MA implication이 없더라도 모니터링 가치가 있으면 `Monitoring News` 또는 `Watchlist`로 포함한다.
5. BioSpectator와 forwarding email은 calibration/reference input이며 live source 자체는 아니다.
6. 기본 운영은 Gmail draft-only이며 live-send는 승인 기반으로 둔다.
7. 뉴스레터는 “놓치지 않는 모니터링”과 “읽기 좋은 요약”이 우선이고, MA 심층 해석은 해당 기사에 의미가 있을 때만 추가한다.

## 3. Dashboard 설정 모델

Dashboard에서 사용자가 선택/관리하는 항목:

- 회사명: MSD, 한국MSD, Merck, 경쟁사 등
- 브랜드명/제품명: Keytruda, Gardasil, Lynparza, Welireg 등
- 성분명/한글 alias
- 질환/치료영역
- 정책/기관 키워드: HIRA, MOHW, MFDS, 약평위, 암질심 등
- source group: 공식기관, 전문지, 경제지, 종합지, Naver discovery
- inclusion mode:
  - strict keyword match
  - keyword + alias match
  - semantic expansion
- newsletter section preference:
  - Company / Brand Monitoring
  - Policy & Reimbursement
  - Clinical / Regulatory
  - Competitor Movement
  - General Watchlist
- recipients and delivery mode

## 4. 수집 및 포함 로직

### 4.1 Primary inclusion rule

최근 24시간 내 기사 중 아래 중 하나를 만족하면 후보로 포함한다.

- user-selected company keyword 포함
- user-selected brand/product keyword 포함
- alias/한글명/영문명 매칭
- dashboard-selected topic keyword 포함
- source registry에서 모니터링 대상으로 지정된 공식/전문 source의 관련 기사

### 4.2 MA relevance의 역할

MA relevance는 기사 제외 기준이 아니라 다음을 결정한다.

- 이메일 상단 배치 여부
- `Market Access Note` 작성 여부
- `Policy/Reimbursement Signal` 섹션 배치 여부
- follow-up action 생성 여부
- 단순 monitoring summary로 처리할지 여부

### 4.3 포함 섹션

#### A. Top Monitoring Highlights
- 키워드 매칭이 강하거나 중요 source에서 나온 기사
- MA signal이 높지 않아도 브랜드/회사 관점에서 중요하면 포함

#### B. Company / Brand Monitoring
- 사용자가 지정한 회사/브랜드 관련 기사
- 일반 홍보/임상/제휴/허가/시장 동향 포함
- 단, 기사 성격을 명확히 라벨링한다: PR / clinical / regulatory / commercial / policy / access

#### C. Policy & Reimbursement Signals
- HIRA/MOHW/MFDS/약평위/암질심/약가/급여 관련 기사
- 여기에는 MA implication을 적극 작성

#### D. Competitor / Market Watch
- 경쟁사 제품, 적응증, 임상, 허가, 출시, 시장전략 관련 기사

#### E. Low-signal Watchlist
- 키워드는 맞지만 implication이 낮은 항목
- 한 줄 요약 중심
- 필요한 경우 “MA implication not identified”라고 두지 말고 그냥 implication 섹션을 생략

## 5. Writer Agent 규칙

기사별 기본 포맷:

```text
[제목]
출처 / 발행시각 / 링크
매칭 키워드: 회사명, 브랜드명, 질환, 정책 키워드 등
기사 유형: company / brand / clinical / regulatory / policy / reimbursement / market / PR

주요 내용
- 최대 3개 factual bullet

Why it matters
- 모니터링 관점에서 왜 포함했는지
- 회사/브랜드/경쟁/정책/시장 관점 의미

Market Access Note
- 급여, 약가, HIRA, HTA, payer, 환자 접근성 관점의 방어 가능한 함의가 있을 때만 작성
- 없으면 생략

Next watch
- 후속 확인 포인트가 있을 때만 작성
```

변경된 핵심:
- 이전의 `Korea MA implication`은 필수 섹션이 아니다.
- 모든 기사에 MA 해석을 붙이지 않는다.
- 대신 `Why it matters`를 통해 모니터링 포함 이유를 설명한다.

## 6. Newsletter 구조

Subject 예시:
`[Daily Monitoring] YYYY-MM-DD | MSD/Product/Policy Watch`

본문 구조:

1. Header
   - 기준일
   - 수집 기간
   - 선택된 monitoring scope
   - draft/final 상태

2. 오늘의 주요 모니터링 요약
   - 기사 수
   - 회사/브랜드 매칭 수
   - 정책/급여 signal 수
   - high-priority item 수

3. Top Monitoring Highlights
   - 중요도 높은 기사 3–5개
   - MA signal뿐 아니라 회사/브랜드 중요도 반영

4. Company / Brand Monitoring
   - 사용자 지정 회사/브랜드별 묶음

5. Policy / Reimbursement Signals
   - MA relevance 높은 항목

6. Competitor / Market Watch
   - 경쟁사/시장 관련 항목

7. Low-signal Watchlist
   - 키워드는 맞지만 중요도 낮은 기사
   - 제목/출처/한 줄 요약 중심

8. Excluded / Duplicates Summary
   - 중복, 날짜 범위 밖, source 불명, 키워드 약함 등

9. Source & Caveat
   - source registry
   - Naver discovery caveat
   - 접근 제한/본문 확인 제한 표시

## 7. Scoring 모델 변경

기존 모델: MA relevance가 낮으면 제외

수정 모델: Monitoring inclusion score와 MA relevance score를 분리

### Monitoring Inclusion Score
- user keyword exact match
- alias match
- selected company/brand match
- source authority
- freshness
- duplicate novelty
- title/body match strength

### MA Relevance Score
- reimbursement/pricing/HIRA/HTA/policy relevance
- payer/access implication
- official committee/regulatory link

### Priority 결정

```text
priority = monitoring_importance + source_authority + freshness + optional MA relevance bonus
```

따라서 MA relevance가 낮아도 monitoring_importance가 높으면 포함된다.

## 8. Agent 구조 수정

1. Dashboard Scope Agent
   - 사용자가 선택한 keyword/company/brand/topic 설정을 읽는다.

2. News Discovery Agent
   - Naver/API/RSS/site search를 통해 최근 24시간 후보 수집.

3. Keyword & Entity Match Agent
   - 회사/브랜드/alias/질환/정책 키워드 매칭.
   - 이 단계가 primary inclusion gate다.

4. Deduplication & Provenance Agent
   - URL canonicalization, 중복 제거, source labeling.

5. Monitoring Prioritization Agent
   - monitoring importance 기준으로 Top/Section/Watchlist 분류.

6. Optional MA Insight Agent
   - MA relevance가 있는 기사만 Market Access Note 작성.

7. Newsletter Writer Agent
   - 읽기 좋은 daily newsletter 구성.

8. Review/Delivery Agent
   - draft 생성, QA, Gmail draft/send control.

## 9. QA Gate 수정

유지할 gate:
- 24시간 필터
- source/provenance 저장
- 중복 제거
- broken link 표시
- draft-only 기본값
- recipient allowlist
- Gmail draft id 저장

완화/수정할 gate:
- MA relevance 낮음은 제외 사유가 아니다.
- implication 없음은 실패가 아니다.
- low-value MSD mention도 사용자가 MSD/제품 모니터링을 원하면 포함 가능하다. 단, 섹션을 Low-signal Watchlist 또는 Brand Monitoring으로 분류한다.

## 10. 구현 우선순위

1. Dashboard scope schema를 keyword/company/brand 중심으로 재정의.
2. Article card에 `matched_keywords`, `matched_entities`, `monitoring_importance`, `ma_relevance`, `section` 필드 추가.
3. Ranking을 MA gate 방식에서 monitoring-first 방식으로 수정.
4. Writer template에서 `Market Access Note`를 optional로 변경.
5. 이메일 섹션을 Top MA Signal 중심에서 Monitoring Newsletter 중심으로 변경.
6. 테스트 추가:
   - MA relevance 낮아도 user brand keyword match면 포함
   - company keyword only article은 Brand Monitoring/Watchlist로 분류
   - MA implication 없는 기사에서도 newsletter card 생성
   - policy/reimbursement article은 Policy section과 Market Access Note 생성
   - BioSpectator/forwarded email은 calibration only 유지

## 11. 실수 방지 문장

앞으로 이 서비스를 설명할 때는 다음처럼 표현한다.

> MA Daily Mailing은 사용자가 지정한 회사·브랜드·키워드 기반 daily monitoring newsletter이며, Market Access insight는 해당 기사에서 방어 가능할 때 추가되는 optional layer다.

하지 말아야 할 표현:

> MA relevance가 높은 기사만 통과시키는 professional MA briefing service

이 표현은 모니터링 목적을 과도하게 좁힌다.
