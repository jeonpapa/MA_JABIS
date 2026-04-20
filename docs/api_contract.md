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
