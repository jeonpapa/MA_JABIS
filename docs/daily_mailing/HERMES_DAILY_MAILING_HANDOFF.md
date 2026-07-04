# 헤르메스 Daily Mailing 작업 지시서 (핸드오프)

> 대상: 외부 GPT-5.5 에이전트 "헤르메스". 대쉬보드는 **모니터링 스콥만** 정하고, 헤르메스가
> 그 스콥으로 **매일 메일을 검토·작성·발송**한다. (정책 인텔리전스 헤르메스 모델과 동일 구조:
> 대쉬보드=입력 스냅샷 / 헤르메스=작업+전달 / 결과=대쉬보드로 동기화.)

## 0. 역할
1. 대쉬보드 사용자가 Daily Mailing UI 에서 모니터링 스콥(키워드·미디어·수신자 등)을 저장.
2. 대쉬보드가 그 스콥을 **`dashboard_scope` JSON 스냅샷**으로 내보낸다 (`data/daily_mailing/scopes/<subscription_id>.json`).
3. **헤르메스**가 그 스콥을 읽고 **검토** → daily-monitoring 파이프라인으로 초안 생성 → **최종 메일 작성 후 매일 발송**.
4. 헤르메스가 실행 산출물(run 번들)을 채널에 커밋 → 대쉬보드가 동기화해 **관리자 칸반/이력**으로 표시.

## 1. 입력 — dashboard_scope JSON
계약: `agents/daily_mailing/dashboard_scope.py` (`load_dashboard_scope`). 예시: `agents/daily_mailing/dashboard_scope.example.json`.
주요 필드: `subscription_id, owner_email, recipients[], keywords[], companies[], brands[], aliases{}, disease_areas[], policy_topics[], media[], custom_sources[], personas[], lookback_hours, delivery_mode`.
- 대쉬보드가 `GET /api/mail-subscriptions/<id>/scope` 로 생성·저장한다. 헤르메스는 이 파일을 소비만 한다.

### 특수 필드
- **`custom_sources: [{url, name?}]`** — 사용자가 직접 추가한 사이트. 헤르메스가 각 사이트를 keywords 로
  **웹 검색/브라우징**해 후보 기사를 수집한다. **미등록 소스**이므로 `source_status=media_report_only`
  로 취급하고, 원출처/공식출처 확인 후에만 채택한다. 기존 Naver 발견 결과와 병합해 랭킹.
- **`test_request: {requested_at, requested_by}`** — 존재하면 **1회 테스트 발송 요청**. 아래 §2-테스트 참조.

## 2. 절차 (매일 아침)
```
1) 스콥 스냅샷 읽기: data/daily_mailing/scopes/<subscription_id>.json
2) 스콥 검토 (사용자 의도·수신자·정책토픽 확인. 부적절하면 발송 보류하고 사유 기록).
3) 파이프라인 실행 (초안 생성, 발송 아님):
     python scripts/generate_daily_mailing_draft.py \
       --dashboard-scope data/daily_mailing/scopes/<subscription_id>.json \
       --env config/.env --out-dir runs/drafts
   → runs/drafts/ 에 JSON/Markdown/HTML draft + review_board JSON + DB run 적재.
   → 산출물: discovered/recent/selected 수, quality_report(status), review_board 기사 카드.
4) 리뷰: review_board 의 reviewer_findings·verification_caveat·quality_flags 를 확인.
   - source_status ∈ {media_report_only, calibration_only} 는 원출처/공식출처 확인 후에만 채택.
   - MA 관련 주장(약가·급여·RSA)은 공식 HIRA/MOHW/MFDS 또는 회사 원문 대조.
   - 근거 부족·과장은 제외. Top MA signal vs Watchlist 섹션 구분 유지.
5) 최종 메일 작성 (HTML) 후 **발송**:
     - delivery_mode='gmail_draft' → Gmail 초안만 생성(사용자 검토 후 수동 발송). `agents/notify/gmail_delivery.create_gmail_draft`.
     - delivery_mode='gmail_send' → 검토 통과분을 헤르메스가 직접 발송.
     - GOOGLE_TOKEN_PATH 토큰에 gmail.send scope 필요.
6) run 번들 커밋(동기화용): `{ "payload": {...run meta...}, "articles": [...카드...] }` 를
   비공개 채널에 커밋 → 대쉬보드가 `daily_mailing_run`/`daily_mailing_article` 로 import →
   관리자 칸반에 표시. (`agents/ingest/daily_mailing_sync.py` 소비.)
```

## 2-테스트. 테스트 메일 요청 (`test_request` 플래그)
사용자가 대쉬보드에서 "테스트 메일 요청"을 누르면 스콥에 `test_request` 가 실린다.
- 감지하면 **정규 발송과 별개로 1회** 파이프라인 실행 → 검토 → 제목에 **`[TEST]` 접두**를 붙여
  recipients(또는 owner)에게 발송한다.
- run 번들에 `"is_test": true` 를 기록해 대쉬보드 칸반에서 정규 발송과 구분되게 한다.
- **처리 후 `test_request` 를 소비**한다(중복 [TEST] 발송 방지). 대쉬보드는 최신 요청 시각만 기록하므로,
  가장 최근 요청 1건만 처리하면 된다.

## 3. 품질 기준 (반드시 준수)
- **근거·출처**: 기사 카드의 publisher_url/naver_url·source_status·verification_caveat 를 존중. 미검증 미디어 단독 주장 금지.
- **MA 시사점**: 적응증·약가·급여·RSA·사용량-약가 축으로 defensible 하게. 지어내기 금지.
- **모니터링 우선**: 사용자 선택 키워드/브랜드는 MA 신호가 약해도 Watchlist 로 포함.
- **발송 게이트**: 스콥·수신자·품질 확인 후에만 발송. 애매하면 gmail_draft 로 남기고 사용자 확인.
- 상세 규칙: `docs/daily_mailing/MA_DAILY_MAILING_PERMANENT_SERVICE_SPEC.md`,
  `docs/daily_mailing/MA_DAILY_MONITORING_NEWSLETTER_REVISED_PLAN.md`.

## 4. 채널 / 사생활
- 스콥·run 번들·초안은 메일 본문·수신자를 포함 → **비공개 채널만**. 메인 repo(MA_JABIS) 커밋 금지.
- `data/daily_mailing/` 는 gitignore. 헤르메스 채널(비공개 git / prod 볼륨)로만 동기화.

## 5. 설정
- `config/.env`: `NAVER_API_CLIENT_ID`/`SECRET`(발견), `GOOGLE_TOKEN_PATH`(gmail.send scope; 초안/발송).
- source registry: `config/source_registry.yaml`.
