# API Contract — v2 Dashboard

**프로토콜**: HTTP JSON, Bearer JWT (except `/api/auth/login`)
**CORS**: dev = `http://localhost:5173`, prod = Vercel domain
**에러 포맷**: `{ "error": "<msg>", "code": "<UPPER_SNAKE>" }`

---

## 범례
- 🟢 **기존 API 재사용** (shape 만 조정)
- 🟡 **기존 + reshape wrapper 추가**
- 🔴 **신규 구현**

---

## 1. Auth (🔴 전부 신규)

### `POST /api/auth/login`
```json
// req
{ "email": "user@msd.com", "password": "..." }
// res 200
{ "token": "<jwt>", "user": { "email": "...", "role": "admin|user" } }
// res 401
{ "error": "invalid credentials", "code": "AUTH_INVALID" }
```

### `GET /api/auth/me` — JWT 검증, 현재 유저 반환
### `POST /api/auth/logout` — JWT revoke (선택)

### `GET /api/admin/users` — admin 전용
### `POST /api/admin/users` — admin 전용, `{ email, password }` 생성
### `DELETE /api/admin/users/<email>` — admin 전용
### `PATCH /api/admin/users/me` — 본인 비밀번호 변경

---

## 2. Home (`/`)

### 🟡 `GET /api/home/msd-summary`
**Wrapper**: `approval/products` + `indications` join
```json
{
  "total_reimbursed": 42,
  "keytruda": {
    "reimbursed": 12,
    "nonReimbursedApproved": 8,
    "indications": [
      { "type": "급여", "name": "NSCLC 1L metastatic + chemo" },
      { "type": "비급여", "name": "..." }
    ]
  }
}
```

### 🔴 `GET /api/home/pipeline`
```json
{
  "pipeline": [
    { "name": "MK-1026", "phase": "Phase 3", "indication": "CLL",
      "status": "current|upcoming", "expectedYear": 2026 }
  ]
}
```

### 🔴 `GET /api/home/keyword-cloud?days=30`
```json
{ "keywords": [{ "text": "약가 재평가", "weight": 98 }, ...] }
```

### 🟡 `GET /api/home/brand-traffic?top=5`
**Wrapper**: `market_intelligence/media.py` 일별 count aggregate
```json
{
  "brands": [{
    "rank": 1, "brand": "Keytruda", "company": "한국MSD",
    "trafficIndex": 9840, "change": 12.4, "category": "면역항암제",
    "sparkline": [6200, 7100, 6800, 7900, 8400, 8750, 9840],
    "news": [{ "title": "...", "source": "...", "date": "...", "tag": "급여", "url": "..." }]
  }]
}
```

### 🟢 `GET /api/home/price-changes-top?n=10&direction=abs`
**재사용**: `/api/domestic/price-changes` (sort + limit)

---

## 3. Domestic Pricing (`/domestic-pricing`)

### 🟢 `GET /api/domestic/search?q=&limit=`
**shape 확장 필요**: `hasRSA`, `rsaType`, `category`, `change`(최근 변동률) 추가
```json
{
  "items": [{
    "id": "...", "insuranceCode": "643901350",
    "productName": "키트루다주", "ingredient": "Pembrolizumab",
    "category": "항암제", "currentPrice": 2734875,
    "change": null, "hasRSA": true, "rsaType": "Expenditure Cap",
    "lastUpdated": "2025-03-01"
  }],
  "total": 42
}
```

### 🔴 `GET /api/domestic/detail?code=<insurance_code>`
**신규**: 상세패널 + 이력 + 아날로그 한 번에
```json
{
  "basic": {
    "productName": "...", "firstRegistDate": "...",
    "currentPrice": ..., "priceChangeCount": 5,
    "changeRateFromFirst": -12.3
  },
  "detail": {
    "hasRSA": true, "rsaType": "...", "evalCommitteeDoc": "...",
    "firstApprovalDate": "...", "dosage": "...",
    "dailyCost": ..., "monthlyCost": ..., "yearlyCost": ...
  },
  "priceHistory": [
    { "date": "2021-03-01", "type": "최초등재", "price": ..., "changeRate": null, "reason": "..." }
  ],
  "analogues": [{ "name": "...", "price": ..., "dailyCost": ... }],
  "sameIngredientCount": 3
}
```

---

## 4. International Pricing (`/international-pricing`)

### 🟢 `GET /api/international/search?q=` — 자동완성
### 🟢 `GET /api/international/history?limit=20` — 사용자별 검색 이력

### 🔴 `GET /api/international/detail?drug=keytruda`
**Wrapper**: `foreign/cached` + `hta/approvals` + `approval/matrix` 통합
```json
{
  "productName": "Keytruda", "ingredient": "Pembrolizumab",
  "searchedAt": "...", "searchedBy": "...",
  "a8Pricing": {
    "usa": { "price": 10800, "currency": "USD", "reimbursed": true, "reimbursedDate": "2014-09-04", "note": "..." },
    "uk": { ... }, "germany": { ... }, "france": { ... },
    "canada": { ... }, "japan": { ... }, "italy": { ... }, "switzerland": { ... }
  },
  "htaStatus": {
    "uk": { "status": "권고", "htaBody": "NICE", "date": "2022-06",
            "recommendation": "권고", "note": "TA724", "fullText": "..." },
    "canada": { ... }, "australia": { ... }, "scotland": { ... }
  },
  "approvalStatus": {
    "usa": { "approved": true, "date": "2014-09-04",
             "indication": "metastatic melanoma", "fullIndication": "..." },
    "uk": {...}, ..., "australia": {...}, "scotland": {...}
  }
}
```

---

## 5. Market Share (`/market-share`) — 🔴 전부 신규

### `GET /api/market-share/overview?quarter=2025Q1`
```json
{
  "donut": [{ "name": "Keytruda", "value": 37.8, "color": "#00E5CC" }],
  "unitTrend": [{ "quarter": "Q1 24", "Keytruda": 34.2, ... }],
  "revenueTrend": [{ "quarter": "Q1 24", "Keytruda": 4820, ... }],
  "uploadedAt": "2025-04-10", "sourceFile": "Q1_2025.xlsx"
}
```

### `POST /api/admin/market-share/upload` — admin 전용
### `GET /api/admin/market-share/uploads` — 업로드 이력

---

## 6. Daily Mailing (`/daily-mailing`) — 🔴 전부 신규

### `GET /api/mailing/configs` — 현재 유저의 구독 설정 목록
### `POST /api/mailing/configs` — 신규 등록
```json
{
  "name": "약가 정책 모니터링",
  "keywords": ["약가 인하", "급여 등재"],
  "media": ["medi", "yakup"],
  "schedule": "Daily", "time": "08:00", "weekDay": null,
  "emailList": ["a@msd.com"]
}
```
### `PATCH /api/mailing/configs/<id>` — active 토글 / 수정
### `DELETE /api/mailing/configs/<id>`
### `POST /api/mailing/test/<id>` — 즉시 테스트 발송

### `GET /api/mailing/media-catalog` — 매체 목록 (전문지/일간지/경제/방송)

---

## 7. Admin — Pipeline (`/admin/pipeline`) — 🔴 전부 신규

### `GET /api/admin/pipeline`
### `POST /api/admin/pipeline` — `{ name, phase, indication, status, expectedYear }`
### `PATCH /api/admin/pipeline/<id>`
### `DELETE /api/admin/pipeline/<id>`

---

## 8. Workbench (`/workbench`) — 🟢 모두 재사용 (P6 에서)

- `GET /api/workbench/assumptions`
- `PUT /api/workbench/assumptions`
- `GET /api/workbench/defaults`
- `GET /api/workbench/hta`
- `POST /api/workbench/compute`
- `POST /api/workbench/export`

---

## 9. 신규 DB 테이블

### `users`
```sql
email TEXT PRIMARY KEY,
password_hash TEXT NOT NULL,
role TEXT NOT NULL DEFAULT 'user',  -- 'admin' | 'user'
created_at TEXT NOT NULL,
last_login_at TEXT
```

### `msd_pipeline`
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL,
phase TEXT,                    -- 'Phase 1/2/3', 'BLA', 'Approved'
indication TEXT,
status TEXT,                   -- 'current' | 'upcoming'
expected_year INTEGER,
notes TEXT,
created_at TEXT, updated_at TEXT
```

### `market_share_quarterly`
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
quarter TEXT NOT NULL,         -- '2025Q1'
product_name TEXT NOT NULL,
ingredient TEXT,
company TEXT,
category TEXT,
unit BIGINT,                   -- 수량
revenue BIGINT,                -- 매출액 (백만원)
source_file TEXT,
uploaded_at TEXT,
UNIQUE(quarter, product_name)
```

### `market_share_uploads`
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
filename TEXT, quarter TEXT, row_count INTEGER,
uploaded_by TEXT, uploaded_at TEXT,
status TEXT  -- 'success' | 'failed'
```

### `mailing_configs`
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
owner_email TEXT,
name TEXT,
keywords_json TEXT,  -- JSON array
media_json TEXT,
schedule TEXT,       -- 'Daily' | 'Weekly'
time TEXT,           -- 'HH:MM'
week_day TEXT,
email_list_json TEXT,
active INTEGER DEFAULT 1,
created_at TEXT, updated_at TEXT
```

### `mailing_logs`
```sql
id INTEGER PRIMARY KEY AUTOINCREMENT,
config_id INTEGER,
sent_at TEXT,
recipient_count INTEGER,
article_count INTEGER,
status TEXT, error_msg TEXT
```

### `brand_traffic_daily`
```sql
date TEXT,
brand TEXT,
news_count INTEGER,
PRIMARY KEY(date, brand)
```

---

## 10. 구현 우선순위

| Phase | 엔드포인트 수 (신규+reshape) |
|---|---|
| P1 인증 | 7 |
| P2 기존 연결 | 5 (detail wrapper 3개 + shape 확장 2개) |
| P3 신규 파이프라인 | 17 |

**총 신규/변경: ~29개**. 대부분은 기존 `agents/db` 위 얇은 wrapper.

---

## 11. Stakeholder Signals (`/stakeholder-signals`, `/admin/stakeholder-*`) — 🔴 전부 신규

구현: `api/stakeholder_routes.py` (Blueprint, 독립 `sv_*` 테이블). 규칙: `agents/rules/stakeholder_verbatim_rules.md`.
외부 에이전트 계약: `agents/ingest/STAKEHOLDER_SYNC_README.md`. 운영: `docs/agent_team/STAKEHOLDER_RUNBOOK.md`.

**권한 3계층**
- **user** — `require_auth()` (user·admin). 인용문·귀속요약·해석·증거등급·locator·URL·SHA-256·무결성 + **locator 창 발췌 ≤600자**. 응답 어디에도 `photo_url`·`photo_path`·`local_path`·`text_path` 가 없다(admin 제외).
- **admin** — `require_auth(role="admin")`. 추출 전문·원본 다운로드·인물 CRUD·사진·제안 승인/반려/충돌 확인·sync. `reviewer`/`actor` 는 토큰 `sub`(이메일) 이며 요청 본문으로 받지 않는다. 인물 삭제 API 는 없다(`paused` 로만).
- **agent** — `require_auth(role="agent")` (agent·admin). `/api/agent/stakeholder/*` 3개만. agent 토큰은 그 밖의 모든 라우트에서 403 (`AUTH_FORBIDDEN`).

**오류 포맷**: `{ "error": "<msg>", "code": "<UPPER_SNAKE>"[, "details": {...}] }`. 인증 실패 401, 권한 부족 403.
**공통 파라미터**: `org` ∈ `보건복지부|심평원|공단` (아니면 400 `BAD_ORG`), `grade` = 증거등급 토큰(`B-summary`) 또는 등급군(`A|B|C|role`), `from`/`to` = `YYYY-MM-DD`.

### 11-1. User 읽기

| 메서드·경로 | 응답 |
|---|---|
| `GET /api/stakeholder/overview` | `{as_of, scope:{start,end}, kpi:{events, statements, quotes, speakers_observed, people_active, people_priority, people_candidates, pending_proposals}, latest_monitoring_run:{run_id, run_date, run_status, saturation_status, active_people, approved_domains, new_quotes}\|null, latest_audit_run:{audit_run_id, run_date, run_status, claim, why_this_matters, proposals_created}\|null, recent_events:[{event_id, event_date, title, topics, speakers}](3), integrity:{sources_verified, sources_total, mismatch, missing, last_sync_at}, copyright_notice, topics:[{slug,title}]}` |
| `GET /api/stakeholder/events?topic=&org=&grade=&from=&to=` | `{items:[event + statements_count + headline:{speaker_name, person, quote_text, evidence_grade}\|null]}` 최신순. 필터를 주면 해당 발언이 없는 행사는 제외 |
| `GET /api/stakeholder/statements?topic=&org=&grade=&person_id=&q=&from=&to=&limit=` | `{items:[{statement_id, event:{event_id,event_date,title}, topic, speaker_name, speaker_role_at_event, person:{person_id,name,photo}\|null, evidence_grade, grade_class:'a'\|'b'\|'c'\|'role', headline_quote:{quote_id,quote_text,locator_match}\|null, summary_text, quotes_count, source:{source_id,publisher_title,url,publication_date,integrity_status}\|null}]}` |
| `GET /api/stakeholder/statements/<id>` | 발언 블록 전체 + `quotes:[{quote_id, quote_text, segments, locator, locator_match('range'\|'file'\|'none'\|'unchecked'), topics, source_id, evidence_grade}]` + `provenance:[{source_id, title, url, source_type, evidence_grade, publication_date, sha256, text_sha256, integrity_status, last_live_check}]` + `excerpt:{source_id, locator, text}\|null`(첫 locator 창). 404 `NOT_FOUND` |
| `GET /api/stakeholder/people?status=&q=` | `{active_effective_count, updated_at, items:[인물 공개 필드 + has_photo + statements_count + importance_score]}` (`photo_url` 제외) |
| `GET /api/stakeholder/people/<id>` | 인물 + `role_history` + `recent_statements`(5). admin 이면 `photo_url`(메타 텍스트) 포함 |
| `GET /api/stakeholder/people/<id>/photo` | `image/webp` (볼륨) / 404 `NO_PHOTO`. `Cache-Control: private` |
| `GET /api/stakeholder/sources` | manifest 출처 목록(+`integrity_status`). `local_path`·`text_path` 는 admin 에게만 |
| `GET /api/stakeholder/sources/<id>/excerpt?locator=sources/<file>.txt:<a>-<b>` | `{source_id, locator, text, start, end, chars, truncated}` (≤600자). 400 `BAD_LOCATOR`/`LOCATOR_MISMATCH`(locator 경로가 그 출처의 `text_path` 와 다름)/`LOCATOR_RANGE`, 404 `TEXT_MISSING` |
| `GET /api/stakeholder/artifacts/relationship-map.svg` | 관계도 SVG (`Content-Security-Policy` 샌드박스) / 404 |
| `GET /api/stakeholder/runs?kind=monitoring\|audit&limit=` | 런 요약 목록(payload 제외), `limit` 1~200 (기본 30). `kind` 오류 400 |
| `GET /api/stakeholder/runs/<kind>/<run_id>` | `{...요약, payload(전체 JSON), md(런 .md 본문\|null)}` |

### 11-2. Admin

| 메서드·경로 | 응답/동작 |
|---|---|
| `GET /api/stakeholder/sources/<id>/text` | 추출 전문 `text/plain`. 404 `TEXT_MISSING` |
| `GET /api/stakeholder/sources/<id>/raw` | 원본 다운로드 (`Content-Disposition: attachment`, `no-store`). 404 `RAW_MISSING` |
| `POST /api/admin/stakeholder/people` | 201 인물. 항상 `verification_status='candidate'`, `effective_from`=익일. 409 같은 기관 동명 |
| `PUT /api/admin/stakeholder/people/<id>` | 수정. **검증(`verified`) 인물의 소속·부서·직책 변경은 409** (직책은 `role_change` 제안 경로로만) |
| `PATCH /api/admin/stakeholder/people/<id>/status` | `{status:'active'\|'paused'}` |
| `POST /api/admin/stakeholder/people/<id>/photo` (multipart `photo`) | JPG/PNG/WEBP ≤5MB → 640×640 WEBP 재인코딩(EXIF 제거). 413 `PHOTO_TOO_LARGE` · 415 `PHOTO_TYPE` · 422 `PHOTO_EMPTY`/`PHOTO_MISSING`. 교체 시 구 파일 삭제 |
| `DELETE /api/admin/stakeholder/people/<id>/photo` | 사진 제거 |
| `GET /api/admin/stakeholder/proposals?status=&type=` | `{pending_count, updated_at, proposals:[제안 + events + conflicts_status + conflicts_unacknowledged]}`. `status` 는 쉼표 목록, 잘못된 값 400 `BAD_STATUS`/`BAD_TYPE` |
| `GET /api/admin/stakeholder/proposals/<id>` | 제안 + `events` + `conflicts_status:[{index,text,acknowledged,stale,by,at,reason}]` + `conflicts_unacknowledged:[int]` |
| `POST /api/admin/stakeholder/proposals/<id>/conflicts/<index>/ack` `{reason}` | 충돌 하나를 사유(2~1000자)와 함께 확인. `<index>` 0부터. 200 제안(상태 포함). 404 `NOT_FOUND` · 409 `STATE`/`ALREADY_ACKNOWLEDGED` · 422 `BAD_CONFLICT_INDEX`/`BAD_REASON` |
| `GET /api/admin/stakeholder/proposals/<id>/conflict-acks` | `{items:[{conflict_index, conflict_text, reason, acknowledged_by, acknowledged_at}]}` |
| `POST /api/admin/stakeholder/proposals/<id>/approve` `{reason}` | **모든 충돌이 현재 문구로 확인돼야 한다.** → `scheduled`(효력 익일 KST). 부수 행(출처 도메인/직책 이력 scheduled/신규 인물 candidate)과 상태 전이가 한 트랜잭션. 오류는 아래 표 |
| `POST /api/admin/stakeholder/proposals/<id>/reject` `{reason}` | → `rejected`. 충돌·확인은 보지 않는다. 404 · 409 `STATE`/`RACE` · 422 `BAD_REASON` |
| `GET /api/admin/stakeholder/source-domains` | `{effective_count, items}` |
| `GET /api/admin/stakeholder/integrity` | `{summary, last_sync_at, counts, artifact_counts, problems:[{source_id, title, integrity_status, ...}], artifact_problems}` |
| `POST /api/admin/stakeholder/sync` | 잡과 같은 경로로 sync 1회. 성공 200. 실패: 409 `SYNC_RUNNING` (잠금) · 503 `SYNC_CONFIG` (토큰·repo 설정) · 502 `SYNC_FAILED` · 503 `RUNBOOK_MISSING` |

**승인 오류 코드** (`approve`; 검사 순서 = 404 → 상태 409 → 충돌 확인 422 → 유형별 근거·필드)

| HTTP | code | 의미 |
|---|---|---|
| 404 | `NOT_FOUND` | 제안(또는 `role_change` 대상 인물) 없음 |
| 409 | `STATE` | `needs_review`/`needs_evidence` 가 아님 |
| 409 | `RACE` | 동시 결정 — 다른 요청이 먼저 처리 |
| 409 | `DUPLICATE_DOMAIN` · `DUPLICATE_PERSON` · `PENDING_ROLE_CHANGE` | 이미 있는 도메인 · 같은 기관 동명 · 적용 대기 직책 변경 있음 |
| 422 | `BAD_REASON` · `BAD_REVIEWER` | 사유 2~1000자 아님 · 검토자 정보 오류 |
| 422 | `CONFLICTS_UNACKNOWLEDGED` | 미확인 충돌 존재 — `details: {"unacknowledged": [index, ...]}` |
| 422 | `EVIDENCE` · `EVIDENCE_DOMAIN` | 증거수준·공식 근거 미충족 (`source_expansion` 공식 E3+/그 외 E2+, `role_change` 공식 E3+, `new_person` E2+) · 제안 도메인과 근거 URL hostname 불일치 |
| 422 | `BAD_SOURCE` · `BAD_ROLE` · `BAD_PERSON` · `BAD_DOMAIN` | 필수 필드 누락 · 도메인에 경로/쿼리 포함 |

승인 → `scheduled` → 매일 sync 잡의 `apply_scheduled` 가 `applied` 로 반영(실패는 `application_failed` + 이벤트).

### 11-3. Agent (`role='agent'` 서비스 계정)

| 메서드·경로 | 응답/동작 |
|---|---|
| `GET /api/agent/stakeholder/registry` | `{as_of, people:[{person_id,name,organization,organization_code,department,role,status,verification_status,effective_from,aliases,topic_tags}], sources:[{source_id,name,domain,source_group,status,effective_from}], people_sha256, sources_sha256}` — `active`/`approved` 이고 `effective_from <= 오늘(KST)` 인 행만. 에이전트는 런 JSON `scope.people_registry_sha256`/`source_registry_sha256` 에 두 해시를 기록 |
| `POST /api/agent/stakeholder/proposals` | 감사 제안 접수 → **201** 저장된 제안(`proposal_id=AUD-<12hex>`, `status='needs_review'`). **409** `DUPLICATE`(같은 `dedupe_key` 의 열린 제안). **422** 검증 실패(`BAD_TYPE`·`BAD_PRIORITY`·`BAD_DEDUPE_KEY`·`BAD_SUMMARY`·`BAD_RATIONALE`·`BAD_CHANGE`·`BAD_EVIDENCE`·`BAD_URL`·`BAD_CONFLICTS`·...). 본문: `proposal_type`(`source_expansion\|role_change\|new_person`), `dedupe_key`(3~240), `priority`(`urgent\|high\|normal\|low`, 기본 `normal`), `summary`(3~1000), `rationale`(3~3000), `proposed_change`(객체), `evidence[]`(`url` 필수 http/https), `conflicts[]`(문자열), `audit_run_id`, `created_by`. 선언되지 않은 키(`status` 등)는 버린다 |
| `GET /api/agent/stakeholder/proposals?audit_run_id=` | `{audit_run_id, items:[제안...]}` — 그 감사 런의 제안 (`audit_run_id` 생략 시 전체) |

### 11-4. 신규 DB 테이블 (`sv_` 접두, 15개)

`sv_meta` · `sv_person` · `sv_person_role_history` · `sv_source_domain` · `sv_source` · `sv_artifact` · `sv_event` · `sv_statement` · `sv_quote` · `sv_monitoring_run` · `sv_audit_run` · `sv_audit_proposal` · `sv_audit_proposal_event` · `sv_audit_conflict_ack` · `sv_sync_file`.
결정 테이블(`sv_person`·`sv_person_role_history`·`sv_source_domain`·`sv_audit_proposal`+event·`sv_audit_conflict_ack`)은 대쉬보드가 기록 원본이며 sync 는 insert-if-absent 만 한다. 나머지는 소스 repo 산출물의 미러다. 스키마 정의: `agents/stakeholder/store.py` `SCHEMA_SQL`.
