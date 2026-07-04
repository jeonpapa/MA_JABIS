# Daily Mailing 강화 — 테스트 메일(헤르메스) + 사이트 URL 매체 — 설계

- **작성일**: 2026-07-04
- **상태**: 설계 확정 → 구현 계획(writing-plans)
- **관련**: [[project_daily_mailing_service]], [[project_hermes_krpia_curation]]

## 배경 / 목표
Daily Mailing 서비스에 두 가지 강화. 둘 다 **헤르메스 위임** — 대쉬보드는 설정 캡처 + 요청 전달만 하고, 실제 검색·발송은 헤르메스(GPT-5.5)가 한다. 대쉬보드 파이프라인(`discover_naver_news`)은 변경하지 않는다.

1. **테스트 메일**: subscription "테스트 메일 요청" → 스콥(양식)이 헤르메스 채널로 전달되며, 헤르메스가 검토 후 실제 테스트 메일을 발송(비동기, 진짜 프로덕션 경로 검증).
2. **사이트 URL 매체**: 매체 추가 시 등록 매체 ID 외에 **임의 사이트 URL**을 직접 추가 → 헤르메스가 그 사이트를 키워드로 검색해 모니터링.

## Non-goals
- 대쉬보드가 직접 메일 발송/사이트 크롤 안 함(헤르메스 몫).
- `discover_naver_news`(결정론 Naver 채널) 변경 없음.
- 새 검색 크레덴셜 도입 없음(헤르메스 웹 검색/브라우징 사용).

---

## Feature ① — 테스트 메일 (헤르메스 비동기, 스콥 플래그)

### 데이터
스콥 JSON(`dashboard_scope`)에 필드 추가:
```jsonc
"test_request": { "requested_at": "<UTC ISO8601>", "requested_by": "<owner_email>" }
// 대기 중인 테스트 없으면 null 또는 미존재
```

### 엔드포인트
`POST /api/mail-subscriptions/<id>/test-request` (`@require_auth()`, owner-scoped)
- 동작: 해당 subscription → `subscription_to_scope` → scope 에 `test_request={requested_at:now, requested_by:owner}` 세팅 → `write_scope_snapshot`(`data/daily_mailing/scopes/<id>.json`) 저장.
- 반환: `{ok:true, snapshot_path, requested_at}`.
- 사생활: 스콥은 비공개 채널로만 동기화(기존 `data/daily_mailing/` gitignore).

### UI (`daily-mailing/page.tsx`)
- subscription 카드에 **"테스트 메일 요청"** 버튼 추가(기존 "미리보기"=로컬 HTML 프리뷰 유지, 별개).
- 클릭 → confirm → `POST .../test-request` → 성공 토스트: "헤르메스에 테스트 요청을 전달했습니다 — 검토 후 [TEST] 메일이 발송됩니다."
- API: `requestTestMail(id)` in `mailSubscriptions.ts`.

### 헤르메스 (핸드오프 문서 갱신)
- 스콥에 `test_request` 가 있으면: 해당 스콥으로 파이프라인 1회 실행 → 검토 → 제목 `[TEST]` 접두로 recipients(또는 owner)에게 발송 → run 번들에 `is_test:true` 기록(칸반 구분) → **처리 후 스콥의 `test_request` 를 소비**(다음 스콥 write 시 비워짐).

---

## Feature ② — 사이트 URL 매체 (헤르메스 검색)

### 데이터
- **DB**: `mail_subscription.custom_sources_json TEXT NOT NULL DEFAULT '[]'` 컬럼 추가(`_migrate_mail_subscription_scope` 확장, ALTER-if-missing).
- 형식: `[{ "url": "https://...", "name": "표시명(선택)" }]`.
- 스콥 JSON 에 `custom_sources` 필드로 포함.

### 백엔드
- `_mail_sub_row_to_dict`: `custom_sources`(리스트) 파싱.
- `_coerce_mail_sub_input`: body `customSources`(배열) → `custom_sources_json`. 각 항목 `url` 필수·http(s) 형식 검증, `name` 선택.
- `subscription_bridge.subscription_to_scope`: `sub.get("custom_sources")` → scope `custom_sources`.
- `_MAIL_SUB_COLS` 에 `custom_sources_json` append(인덱스 16).

### UI (`daily-mailing/page.tsx`)
- 매체 카드 하단에 **"사이트 URL 직접 추가"** 입력행: URL 인풋 + 이름(선택) + 추가 버튼 → 칩(도메인 + 이름). URL 형식(http/https) 검증, 중복 제거.
- create/update payload 에 `customSources: [{url, name}]` 포함. 카드에 custom 소스 칩 표시.

### 헤르메스 (핸드오프 문서 갱신)
- 스콥의 `custom_sources` 각 사이트를 키워드로 검색(웹 검색/브라우징)해 후보 기사 수집.
- 미등록 소스이므로 `source_status = media_report_only` 로 취급 → 원출처/공식출처 확인 후에만 채택(review board 규율).
- 기존 Naver 발견 결과와 병합해 랭킹.

---

## 구현 순서
1. 백엔드 ②: `custom_sources_json` 마이그레이션 + coerce/row/bridge. 유닛 테스트(브릿지에 custom_sources 흐름).
2. 백엔드 ①: `test-request` 엔드포인트 + 스콥 `test_request` 플래그. 유닛 테스트(스냅샷에 test_request 기록).
3. 프론트: "테스트 메일 요청" 버튼 + API, "사이트 URL 직접 추가" 입력 + payload. build.
4. 헤르메스 핸드오프/런북 갱신(test_request 처리 + custom_sources 검색).
5. 메모리 갱신.

## 검증
- 브릿지: subscription(custom_sources 포함) → scope.custom_sources 반영.
- test-request: 엔드포인트 호출 → `scopes/<id>.json` 에 `test_request.requested_at/by` 기록(마이그레이션·owner 스코프).
- 마이그레이션: 기존 DB 에 `custom_sources_json` 추가(idempotent).
- 프론트 build clean, 기존 스콥필드/미리보기/칸반 무회귀.

## 위험
- (저) 스콥 `test_request` 소비 책임이 헤르메스에 있음 → 미소비 시 중복 [TEST] 발송 가능. 핸드오프에 "처리 후 소비" 명시. 대쉬보드는 요청 시각만 기록(최신 요청이 이김).
- (저) custom_sources 미검증 소스 → review board 가 media_report_only 로 이미 게이트. 핸드오프에 원출처 확인 명시.
