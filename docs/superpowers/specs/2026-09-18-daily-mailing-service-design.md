# Daily Mailing 서비스 완성 — 설계 (2026-09-18)

> 상태: 설계 승인(2026-09-18). **서비스 확정은 파일럿 검수 통과 후** — §9 게이트.
> 대상 독자: 사용자(MA 리드) + 구현·검증 서브에이전트.

## 1. 배경 — 왜 다시 만드나

2026-09-18 프로덕션 점검 결과:

- 정규 생성·발송은 **다른 PC의 헤르메스(GPT-5.5)** 가 매일 06:00 에 담당했으나 **2026-08-03 이후 run 이 없다**. 이 저장소·프로덕션 어디에서도 원인을 관찰할 수 없다.
- 헤르메스 시절에도 **실제 발송은 없었다**. 8/3 run(구독 19 '파드셉')은 Gmail 초안만 생성했고, 자체 최종승인이 `no_send_quality_hold` 로 막았다. 선정 5건 중 스콥 주제 기사는 0건이었고, 요약은 검색 스니펫 기반이었다.
- 품질 저하의 원인(현 파이프라인):
  1. `expand_keywords_for_personas` 가 사용자 키워드에 페르소나 3종의 기본 키워드를 합산한다 (파드셉: 2개 → 33개).
  2. `select_daily_items` 가 무관한 **MSD 대표 기사**를 강제로 삽입한다.
  3. **본문 추출과 LLM 이 없다.** 요약은 스니펫, MA 시사점은 키워드 템플릿(`infer_ma_implication`)에서 나온다.
  4. Naver API 429 처리가 없어 실행이 중단된다(2026-09-18 로컬 재현).
- 프로덕션 연동 결함: `daily_mailing_runs_sync` 는 8/4 이후 매일 404 (토큰 secret 부재 + `runs/<날짜>/` 구조 불일치). 프로덕션 DB `daily_mailing_run` 은 0행이다. `google-api-python-client` 가 requirements 에 없다.

## 2. 목표 / 비목표

**목표**
- 프로덕션 서버(fly 스케줄러)가 구독별로 매일 **브리핑형** 메일을 생성하고 Gmail 로 발송한다.
- 스콥에 충실한 기사 선정 + 본문 근거 요약 + 인용으로 검증된 MA 시사점.
- 초기에는 승인 후 발송, 품질이 확인되면 구독별로 자동 발송으로 전환한다.
- 실패가 조용히 묻히지 않는다(칸반 상태 + 헬스체크).

**비목표 (YAGNI)**
- 발송 도메인/전용 발신 주소(추후 채널 교체 가능 구조만 유지).
- 애널리스트형 심층 분석, 주간 요약, 수신자별 개인화.
- 헤르메스 연동 유지(버스는 비활성화, 코드는 롤백용으로 보존).

## 3. 확정된 결정

| 항목 | 결정 |
|---|---|
| 생성·발송 주체 | 프로덕션 서버 |
| 메일 형태 | 브리핑형: 스콥 기사 3~5건, 기사당 2~3줄 요약 + MA 시사점 1줄 + 원문 링크. 해당 기사 0건이면 '특이사항 없음' |
| 범위 | 스콥 기사 본문 + 하단 '오늘의 주요 MA 뉴스' 1~2건 (구독별 끄기 가능) |
| 발송 게이트 | 신규 구독은 승인 모드(칸반 승인 후 발송) → 품질 확인 후 구독별 자동 모드로 전환 |
| 발신 계정 | 개인 Gmail (Gmail API, OAuth 토큰은 fly secret) |
| 엔진 접근 | 기존 파이프라인 업그레이드 (discovery/writer/quality/review_board 구조 유지) |
| LLM | 기존 `OPENAI_API_KEY`. 관련성 판정 `gpt-4o-mini`, 작성 `gpt-4o` — `config/settings.json` 에서 변경 가능 |

## 4. 아키텍처

```
[프로덕션 스케줄러 · fly 단일 머신]
 06:40      top_ma_news_job: '오늘의 주요 MA 뉴스' 1~2건 산출 → DB 캐시(당일 1회, 구독 공통)
 */10분     daily_mailing_dispatch_job: 발송 시각이 된 구독 판정
              └ 구독별 run 생성 (콘텐츠 엔진 §5) → persist_daily_mailing_run
                   ├ 승인 모드 → approval_status='pending' → 관리자 칸반 [승인] → Gmail 발송
                   └ 자동 모드 → 게이트 통과면 즉시 발송 / 미달이면 delivery_status='held' + 사유
 09:30      daily_mailing_health_job: 오늘 예정인데 미생성·미발송·보류된 구독 → ERROR 로그 + 칸반 배너
 [테스트 메일 버튼] → 서버가 즉시 1회 생성 → 요청자에게 '[TEST]' 발송 (게이트·승인 모드와 무관)
```

비활성화하는 잡과 워크플로(코드는 보존): `daily_mailing_scope_export`(15분), `daily_mailing_runs_sync`(07:30), `.github/workflows/hermes-daily-mailing.yml`.

## 5. 콘텐츠 엔진 — 단계별 변경

| 단계 | 변경 |
|---|---|
| ① 검색어 | **사용자 스콥만** 사용(keywords·brands·companies·aliases·policy_topics). 페르소나는 작성 관점에만 쓴다. 페르소나 기본 키워드는 §5.1 주요 MA 뉴스 전용 |
| ② 수집 | `NaverNewsClient` 에 호출 간격(≥0.15s), 429 지수 백오프(최대 3회), 실행당 호출 상한 추가. 06:30 뉴스 아카이브(`competitor_news`)에서 스콥 매칭 기사를 후보로 병합(URL 정규화로 중복 제거) |
| ③ 24시간 창 | 유지 (`lookback_hours`) |
| ④ 본문 추출 (신규) | 상위 후보 ≤15건. `requests` + BeautifulSoup. 매체별 본문 선택자(`source_registry.yaml` 에 `body_selector` 추가 가능) → 일반 규칙 폴백. 결과: `article_body`, `article_body_status ∈ {ok, failed, too_short}` |
| ⑤ 관련성 판정 (신규, LLM) | 입력: 스콥 항목 + 제목 + 본문 앞부분(≤2,000자). 출력(JSON): `relevant`, `matched_scope_terms[]`, `reason`. 스콥 항목을 **주제로 다루는** 기사만 통과(단순 언급 제외). **MSD 대표 기사 강제 삽입 제거** |
| ⑥ 선별 | 기존 랭킹 + `_editorial_theme_key` 중복 제거, 최대 5건 |
| ⑦ 작성 (신규, LLM) | 기사별 `summary_bullets`(2~3), `ma_implication`(1줄), `evidence_quote`(본문 원문 그대로). **인용 검증**: 공백을 정규화한 뒤 `evidence_quote` 가 `article_body` 의 부분 문자열이 아니면 `ma_implication` 을 삭제하고 플래그를 남긴다. 본문을 확보하지 못한 기사는 스니펫 요약만 쓰고 시사점은 생략 |
| ⑧ 품질 게이트 | 차단 조건: 관련성 탈락 기사 포함, 인용 검증 실패 시사점 노출, 시사점 중복. 기사 0건은 차단하지 않고 `no_news` 상태로 '특이사항 없음' 메일을 보낸다. 기존 `min_total_articles`/`min_top_signals` 차단은 제거 |
| ⑨ 리뷰보드·칸반 | 구조 유지. 카드에 관련성 사유·매칭 스콥·인용문·본문 확보 여부 추가 |
| ⑩ 렌더 | 브리핑형 HTML/Markdown (`writer.render_daily_draft_html` 교체). 하단에 §5.1 섹션 |

### 5.1 오늘의 주요 MA 뉴스
- 06:40 에 1회 산출: 페르소나 기본 키워드 + 당일 `gov_policy` 아카이브에서 후보를 모으고, 동일한 ④⑤⑦ 로 1~2건을 뽑아 DB 에 캐시한다.
- 구독 설정 `include_top_ma_news`(기본 on)가 켜진 경우에만 붙인다. 스콥 기사와 중복이면 제외한다.

## 6. 발송·스케줄·승인

- **판정(10분 주기)**: `active=1` 이고 `schedule` 조건(Daily / Weekly+week_day)을 만족하며, 현재 KST ≥ `time` 이고, 오늘(KST) 정규 run 이 없는 구독. 재시작으로 시각을 놓쳐도 당일 안에 따라잡는다.
- **멱등**: 정규 run 은 `(subscription_id, run_date, is_test=0)` 기준으로 하루 1개. 발송 성공 시 `sent_at`, `gmail_message_id`, `mail_subscription.last_sent_at` 을 갱신한다.
- **상태**: `delivery_status ∈ {pending_approval, ready_to_send, sending, sent, held, send_failed, no_news_sent, error, expired}` (`ready_to_send`→`sending` 은 조건부 UPDATE 로 발송 선점 — web 승인과 scheduler 재시도의 이중 발송 방지), `approval_status ∈ {not_required, pending, approved, rejected}`. `send_failed` 는 다음 주기에서 최대 3회 재시도한 뒤 `held` 로 바꾼다.
- **승인 모드 시점**: run 은 구독 `time` 에 생성되고, 발송은 관리자가 승인한 시점에 한다. 당일(KST 23:59)까지 승인되지 않은 run 은 `expired` 로 닫는다(다음 날 run 과 섞이지 않게).
- **API**: `POST /api/admin/daily-mailing/runs/<id>/approve` (발송 트리거), `.../reject`, `GET .../runs/<id>/preview`, `GET /api/admin/daily-mailing/health` — 관리자 인증. 테스트 메일은 기존 경로 `POST /api/mail-subscriptions/<id>/test-request` 의 동작을 바꾼다(UI 버튼이 이미 연결됨): 서버가 백그라운드로 즉시 생성 → **요청자(로그인 이메일)에게만** `[TEST]` 발송, 202 응답. `test-send`('최근 발송 보기')는 DB 의 `email_html` 을 반환하도록 유지·수정.
- **생성 오류**: 구독 생성 중 예외는 그날의 정규 run 자리를 `error` run 으로 채운다(같은 날 10분마다 재생성·LLM 재호출 폭주 방지). 헬스체크가 표시하고, 관리자는 테스트 메일로 재확인한다.
- **UI**: 칸반 카드에 [승인]/[반려] 버튼과 메일 미리보기. 구독 폼에 `발송 모드(승인/자동)`, `주요 MA 뉴스 포함` 토글.
- **Gmail**: `gmail_delivery.send_gmail_message` 재사용. 토큰은 `GOOGLE_TOKEN_JSON`(base64) secret 에서 기동 시 파일로 복원한다. `google-api-python-client`, `google-auth` 를 requirements 에 추가한다. OAuth 앱은 **'프로덕션' 게시 상태**여야 한다('테스트' 상태이면 토큰이 7일 만에 만료).

## 7. 스콥 입력 검증

- 스콥 항목(keywords·brands·companies·policy_topics·disease_areas)이 모두 비면 저장을 거부한다(400). 기존 UI 저장 조건(`scopeTermCount === 0` 차단)과 같은 기준.
- 구독 이름에 등록 약제명(브랜드 사전)이 있는데 keywords·brands 에 없으면 경고를 반환하고, UI 에 표시한다. 저장은 허용한다.

## 8. 오류 처리·감시

| 실패 | 동작 |
|---|---|
| Naver 429 / 상한 | 백오프 재시도, 상한 도달 시 수집된 후보로 진행 |
| 본문 추출 실패 | 스니펫 요약 + '본문 미확보', 시사점 생략 |
| LLM 오류·타임아웃 | 1회 재시도 후 해당 기사 제외. 전부 실패하면 run `held` (자동 모드에서도 미발송) |
| Gmail 실패 | `send_failed` → 3회 재시도 → `held` |
| 구독 단위 예외 | 해당 run `error` + 사유 기록. 다른 구독은 계속 진행 |

헬스체크(09:30): 오늘 예정 구독별 상태를 집계한다. 미생성·`held`·`send_failed`·Gmail 토큰 무효가 있으면 ERROR 로그와 칸반 배너로 표시한다.

## 9. 테스트 · 파일럿 검수 게이트 · 출시

**단위 테스트** (LLM·Naver·Gmail 은 목): 검색어 구성(페르소나 미합산), 429 백오프, 본문 추출(저장 HTML 픽스처), 관련성 필터, 인용 검증, 0건 → no_news, 게이트, 발송 판정(시각·Weekly·따라잡기), 멱등, 상태 전이, 승인 API, 스콥 검증.

**파일럿 검수 게이트 (서비스 확정 조건)**
1. 구현이 끝나면 **실제 프로덕션 구독 6개의 스콥**으로 로컬에서 실제 Naver·LLM 을 호출해 파일럿 메일을 생성한다(발송 없음).
2. 사용자에게 HTML 미리보기 + 기사별 판정 근거(관련성 사유·인용)를 제출하고, 8/3 헤르메스 결과와 비교한다.
3. **사용자 검수 통과 → 서비스 확정 → 배포.** 미통과 시 지적 사항을 반영해 1로 돌아간다.

**출시**: 배포(사용자) → `GOOGLE_TOKEN_JSON` secret 설정(사용자, 안내 제공) → 전 구독 승인 모드로 시작 → 1~2주 칸반 승인 운영 → 구독별 자동 전환.

## 10. 구현 분할 (병렬 에이전트 작성 + 별도 에이전트 검증)

| 슬라이스 | 범위 | 경계(계약) |
|---|---|---|
| A. 콘텐츠 엔진 | §5 전부 + 파일럿 생성 CLI | 입력 `dashboard_scope` → 출력 run payload + articles (`persist_daily_mailing_run` 인자 형식 유지, 신규 필드는 추가만) |
| B. 발송·스케줄·승인 | §6 + §8 헬스체크 + 칸반·구독 UI | A 의 run 생성 함수를 주입받아 호출(테스트에서는 목). DB 상태 컬럼·API |
| C. 입력 검증·버스 정리 | §7 + §4 비활성화 목록 | 구독 저장 API, 스케줄러 잡 등록부 |

A 와 B 는 동시에 진행한다. C 는 작아서 B 와 함께 진행해도 된다. 각 슬라이스는 작성 에이전트와 다른 에이전트가 검증한다. 커밋은 메인 세션이 리뷰한 뒤에 한다. 배포는 사용자가 직접 한다.

## 11. 리스크

- 언론사별 본문 구조 차이로 추출에 실패할 수 있다 → 폴백 + '본문 미확보' 표시로 거짓 시사점을 차단한다. 파일럿에서 실패율을 측정한다.
- 개인 Gmail → @msd.com 수신 시 스팸으로 분류될 수 있다 → 승인 모드 기간에 수신함 도착 여부를 확인한다.
- OAuth 토큰 만료 → 앱 프로덕션 게시 + 헬스체크로 조기 탐지한다.
