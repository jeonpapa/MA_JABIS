# Stakeholder & Verbatim git-sync 채널 — 외부 에이전트(Key/Hermes) 작업 가이드

외부 에이전트는 **소스 repo(`jeonpapa/kr-drug-pricing-nhi-finance-intelligence`, 브랜치 `main`)에 커밋**하고,
**대쉬보드 API 로 스콥을 받고 제안을 올리기만** 하면 된다.
배포 앱이 **매일 02:20·08:20(KST)** 에 소스 repo 를 읽어 볼륨과 DB 에 멱등 반영한다.
**재배포·재시딩·admin 업로드·DB 직접수정은 필요 없다.**

결정 권한은 대쉬보드(admin)에 있다. 에이전트는 사람·출처·제안의 결정 테이블을 쓰지 못한다(§4, §5).
세부 규칙: `agents/rules/stakeholder_verbatim_rules.md`. 운영 절차: `docs/agent_team/STAKEHOLDER_RUNBOOK.md`.
API 계약: `docs/api_contract.md` §11.

---

## 1. 채널 그림

```
외부 에이전트 (VPS)
  ├─ 07:00 일일 모니터링 ──┐
  ├─ 07:40 커버리지 감사 ──┤  소스 repo 에 git commit/push
  │                        ▼
  │             sources/ · monitoring-runs/ · audit-runs/ · source_manifest.json · 정본 MD ...
  │                        │   (02:20 · 08:20 KST)
  │                        ▼
  │      대쉬보드 stakeholder_data_sync 잡
  │        GitHub Git Trees API → blob sha 대조 → 바뀐 파일만 받기 → 볼륨 data/stakeholder/
  │        → source_manifest.json SHA-256 무결성 판정 → sv_* 테이블 멱등 적재 → apply_scheduled
  │                        │
  │                        ▼
  │             화면: Stakeholder Signals(user) / 인물 관리·모니터링 감사(admin)
  │
  ├─ GET  /api/agent/stakeholder/registry          ← 07:00 실행 전에 pull (스콥)
  ├─ POST /api/agent/stakeholder/proposals         → 감사 제안 (사람이 승인·반려)
  └─ GET  /api/agent/stakeholder/proposals?audit_run_id=   ← QC 에서 방금 올린 제안 재조회
```

두 방향은 서로 다른 채널이다. **산출물(런·원문·정본) = git**, **스콥 pull·제안 = API**.
소스 repo 의 JSON 에 승인 결과를 되써 달라는 요구는 없다 — 승인은 대쉬보드 DB 에만 기록된다.

---

## 2. 에이전트가 커밋하는 것

볼륨 `data/stakeholder/` 는 소스 repo 와 **같은 상대경로**를 쓴다. 다음 경로만 받는다(허용목록: `agents/stakeholder/paths.py`).

| 경로 | 내용 | 대쉬보드 반영 |
|---|---|---|
| `source_manifest.json` | 출처 목록(`sources[]`)·산출물(`artifacts[]`)·`withheld_claims`·등급 어휘·`topic_timeline_update` | `sv_source`·`sv_artifact`·`sv_meta` + 파일 SHA-256 무결성 판정 |
| `sources/` (`*.txt`, `raw/*`, `user_input/*`) | 원문 추출 텍스트, 원본 HTML/HWP/이미지 | 볼륨 파일 그대로. admin 만 전문·원본 열람 |
| `monitoring-runs/<YYYY-MM-DD>/daily_monitoring.json` (+ `.md`, `raw/`) | 일일 모니터링 런 | `sv_monitoring_run` (payload 전체) |
| `audit-runs/<YYYY-MM-DD>/coverage_audit.json` (+ `.md`, `raw/`) | 커버리지 감사 런 | `sv_audit_run` |
| 루트 `*_주제별_인물발언_타임라인_정본.md` | 정본 타임라인 | 결정론 파서 → `sv_event`·`sv_statement`·`sv_quote` (바뀐 sha 일 때만 `origin='canonical_md'` 행 교체) |
| 루트 `*_부서-인물지도_정본.md` | 부서-인물지도 | §2.2 중요도표 → `sv_person.importance_score` |
| 루트 `*.md`·`*.json`·`*.dot`·`*.svg`·`*.png`, `render_relationship_map.py` | 보강조사 기록·README·정책 문서·관계도 | 볼륨 파일 (+ manifest `artifacts[]` 에 있으면 `sv_artifact`) |
| `data/policy_signals.json` | 정책 시그널(분석 가설) — 확인 근거·분석 추론·미확인 범위 분리 | 스키마 검증 → `sv_policy_signal`·`sv_signal_evidence` (내용이 바뀌면 전체 교체 — 아래 §정책 시그널 계약) |
| 루트 `*_정책시그널_*.md` | 정책 시그널 분석 MD | 볼륨 파일 + manifest `artifacts[]` → `sv_artifact`(무결성). 발언 정본으로 파싱하지 **않는다** |
| `data/`, `scripts/` | 레지스트리 JSON, `verify_repository.py` | 아래 참고 |

**`data/*.json` 은 더 이상 기록 원본이 아니다.** `data/people_registry.json`·`data/source_registry.json`·`data/audit_proposals.json` 은
**최초 1회 시드**로만 읽히고, 이후에는 같은 키(`person_id`·`domain`·`proposal_id`)가 DB 에 있으면 소스 쪽 값을 **무시**한다(insert-if-absent).
JSON 을 계속 갱신해도 해롭지 않지만 대쉬보드의 승인·반려·상태·효력일을 바꾸지 못한다. 정본은 대쉬보드 DB 이다.

### 정책 시그널 계약 (`data/policy_signals.json`)

외부 에이전트 소유. 대쉬보드는 **읽기 전용 미러**다(insert-if-absent 가 아니다): 파일이 검증을 통과하고 내용이 바뀌면 시그널 전체를 교체하고, 파일에서 뺀 시그널은 대쉬보드에서도 사라진다.

- **스키마 = 소스 `stakeholder_service.py` 의 `PolicySignalStore` 그대로**(pydantic `extra="forbid"`): 최상위 `schema_version`·`updated_at`·`signals[]` 만. 시그널마다 `signal_id`·`title`·`institution`·`signal_type`·`status`·`as_of`·`direction_confidence`·`implementation_model_confidence`(둘 다 **0 이상 1 이하**)·`confidence_methodology{scale,method,direction_basis,implementation_basis,limitation,reviewed_by,next_review_trigger}`·`thesis`·`confirmed_evidence[]`·`analyst_inference{likely_contract_architecture,payer_benefits[{benefit,modality,rationale,basis_source_ids[]}],most_likely_first_use,selective_use,composite_scope{str:str}}`·`not_confirmed`·`watch_triggers[]` 가 **필수**. 근거(`confirmed_evidence[]`)는 `date`·`person`·`role_at_event`·`evidence_grade`·`evidence_role`·`claim`·`source_id`·`locator` 필수, `policy_status`·`scope_note` 선택. **선언되지 않은 키는 어느 깊이에서든 거부**된다.
- 대쉬보드 추가 제약: `signal_id` 는 파일 안에서 유일해야 한다.
- 예측은 `status: "analyst_hypothesis"` 로, 공식 정책·직접 발언과 분리한다(소스 README 규칙 6). 근거·추론·미확인을 한 필드에 섞지 않는다. confidence 는 분석자 판단(보정 확률 아님)이며 `confidence_methodology` 에 근거를 적는다.
- `confirmed_evidence[].source_id` 는 manifest `sources[]` 에 있어야 하고, `locator` 는 **그 출처의 `text_path`** 의 줄 범위(`sources/<file>.txt:<a>-<b>`)여야 한다. 대쉬보드는 근거마다 `locator_match`(`range|file|none|unchecked`)를 기록해 화면에 보이며 claim·locator 를 고치지 않는다 — `range` 가 아니면 상류에서 고친다.
- **파일이 무효면**(JSON 깨짐·스키마 위반·중복 id) 그 sync 는 오류로 보고되고(잡 원장) 대쉬보드는 **마지막 정상 시그널을 계속 보여 준다**(`import_status.state='invalid'`). 고쳐서 커밋하면 다음 sync 가 교체한다.
- 파일과 manifest `artifacts[]` 의 해시를 같은 커밋에서 갱신한다.

### excluded — manifest 에 있지만 받지 않는 산출물

`source_manifest.json` `artifacts[]`(또는 source 의 `local_path`/`text_path`)가 허용목록 밖 경로(`stakeholder_service.py`, `dashboard-sketches/…` 등)를 가리키면 대쉬보드는 그 파일을 **설계상 받지 않는다**. 무결성 판정은 `missing` 이 아니라 **`excluded`** — 실패가 아니고 별도로 집계된다(sync CLI `integrity: … excluded=`, 검증기 `exclusions.excluded_artifacts`, 신선도 `stakeholder_source_integrity` 근거 문구). 에이전트는 이 항목들을 manifest 에 계속 등재해도 된다. 허용목록은 서비스 코드·스케치로 넓히지 않는다.

### manifest 가 지켜야 하는 것 (무결성 판정 입력)

- `sources[]` 각 항목: `source_id`(필수), `url`, `evidence_grade`, `local_path` + `sha256`(원본), `text_path` + `text_sha256`(추출 텍스트), `last_live_check`.
  `bytes` 가 있으면 `local_path` 크기도 대조한다.
- `artifacts[]` 각 항목: `path`, `sha256`, `bytes`.
- 판정(`integrity_status`): `verified`(모두 일치) · `mismatch`(해시/크기 불일치) · `missing`(파일 없음 — mismatch 보다 우선) · `no_local`(받아 둔 파일 없음, 예: video-only) · `unchecked`(대조할 해시 없음).
  **화면의 "원문 SHA-256 일치" 배지는 `verified` 일 때만 켜진다.**
- 경로는 상대경로·안전 문자만(`A-Za-z0-9가-힣._ /()+-`). `..`·절대경로·`.` 로 시작하는 세그먼트는 거부된다.
- 파일을 바꾸면 **manifest 의 해시도 같은 커밋에서** 갱신한다. 어긋난 채로 두면 해당 출처가 `mismatch` 가 되어 운영 과제가 생긴다.

### 런 JSON 요건

- `daily_monitoring.json`: `run_id`, `run_date_kst` 필수. `scope.people_registry_sha256` 에는 §4 의 `people_sha256` 를 넣는다. `scope.source_registry_sha256` 에는 `sources_sha256`.
- `coverage_audit.json`: `audit_run_id`, `run_date_kst` 필수. `claim`·`why_this_matters`·`run_status` 는 홈 카드로 승격된다.
- 안에 든 절대경로(`/opt/data/...`)는 표시용 문자열일 뿐 파일 접근 키로 쓰지 않는다.

### 정본 MD 요건

- 인용 블록(`> `)은 `locator`(`` `sources/<file>.txt:<a>-<b>` ``)의 줄 범위 **추출 텍스트에서 그대로 찾을 수 있어야** 한다. 파서가 정확 부분문자열 검사(`locator_match`: `range|file|none`)를 남기고,
  `none` 이면 화면에 **"원문 불일치"** 경고가 뜬다(숨기지 않는다). 따옴표 안의 마침표처럼 원문과 문장부호가 다르면 불일치로 판정된다.
- 정본 MD 는 manifest `artifacts[]` 에서 `name: topic_timeline_canonical` 인 항목, 없으면 루트의 파일명 패턴 `*_주제별_인물발언_타임라인_정본.md`(가장 최근 날짜)로 찾는다. 고치면 `artifacts[]` 의 해시도 같이 갱신한다.

---

## 3. 커밋하지 말아야 할 것

- 허용목록 밖: `dashboard-sketches/`, `03-Work-Logs/`, `.` 으로 시작하는 파일·폴더(`.env` 등), 루트의 `*.txt`·`*.py`(`render_relationship_map.py` 제외) — 받지 않고 건너뛴다(`rejected`).
- 단일 파일 20MB 초과(`MAX_BLOB_BYTES`) — 거부. 한 번의 sync 가 받는 총량은 200MB 이며 넘치면 다음 실행으로 이월된다(`deferred`).
- 심볼릭 링크 — 거부.
- **자격증명·토큰·`.env`** — 어디에도 커밋하지 않는다.
- 소스 repo 에서 지운 파일은 볼륨에서 **지워지지 않는다**(기록 삭제 금지, `orphaned` 로 집계만). 파일을 정정하려면 삭제가 아니라 같은 경로를 새 내용으로 덮어쓴다.
- 대쉬보드가 쓰는 `photos/` 는 소스 repo 에 두지 않는다(사진은 admin 이 대쉬보드에서 업로드).

---

## 4. 에이전트가 API 로 하는 것

전용 `agent` 서비스 계정 토큰(§5)으로 다음 3개 라우트만 쓸 수 있다. 그 밖의 모든 라우트는 403 이다.
쓰기 라우트의 오류 응답은 `{ "error": "<메시지>", "code": "<CODE>" }` 이다(소스 서비스의 pydantic `{"detail": [...]}` 가 아니다).

### 4-1. 스콥 pull — 07:00 실행 **전에**

```
GET /api/agent/stakeholder/registry
→ 200
{
  "as_of": "2026-10-13",
  "people":  [{"person_id","name","organization","organization_code","department","role",
               "status","verification_status","effective_from","aliases","topic_tags"}, ...],
  "sources": [{"source_id","name","domain","source_group","status","effective_from"}, ...],
  "people_sha256":  "<hex>",
  "sources_sha256": "<hex>"
}
```

- `people` 은 `status='active'` 이고 `effective_from <= 오늘(KST)` 인 인물, `sources` 는 `status='approved'` 이고 `effective_from <= 오늘` 인 출처 도메인이다. **이 목록이 그날의 모니터링 대상이다.**
  익일 효력 규칙 때문에 어제 승인된 인물·출처가 오늘 목록에 처음 들어온다.
- 런 JSON 의 `scope.people_registry_sha256` = `people_sha256`, `scope.source_registry_sha256` = `sources_sha256` 로 기록한다(같은 스콥이면 같은 해시).
  소스 repo 의 `data/people_registry.json` 을 스콥 입력으로 읽지 않는다 — 이 API 가 정본이다.

### 4-2. 감사 제안 POST

```
POST /api/agent/stakeholder/proposals
Content-Type: application/json
```

```jsonc
{
  "proposal_type": "new_person",              // source_expansion | role_change | new_person
  "dedupe_key": "new_person:이소희:대한민국국회:국회의원",   // 3~240자. 열린 제안 중 유일해야 함
  "priority": "high",                         // urgent | high | normal | low  (기본 normal)
  "summary": "...",                           // 3~1000자
  "rationale": "...",                         // 3~3000자
  "proposed_change": { "name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회",
                       "role": "제22대 국회의원(위원)", "aliases": [], "topic_tags": ["약가·경제성평가"] },
  "evidence": [{
    "url": "https://open.assembly.go.kr/...",   // 필수, http/https
    "publisher": "국회 열린국회정보", "source_class": "official", "evidence_level": "E3",
    "quoted_text": "...", "locator": "API row: HG_NM, POLY_NM",
    "publication_date": null, "effective_date": null,
    "retrieved_at": "2026-10-10T07:48:00+09:00", "content_hash": "<sha256>", "independence_group": "ASSEMBLY-openapi-role"
  }],
  "conflicts": ["검토자가 알아야 할 우려·한계 (문자열 목록)"],
  "audit_run_id": "coverage-audit-2026-10-10",
  "created_by": "Monitoring Coverage Auditor"
}
```

응답:

| 상태 | 의미 | 본문 |
|---|---|---|
| **201** | 접수. `status='needs_review'` | 저장된 제안 전체 (`proposal_id` = `AUD-<12hex>`, `created_at`, `status`, `reviewer: null` 등) — `audit-runs/2026-10-10/raw/proposal_post_responses.json` 의 201 `result` 와 같은 모양 |
| **409** | 같은 `dedupe_key` 의 열린 제안이 이미 있음 (`code: DUPLICATE`) | `{"error": "같은 감사 제안이 이미 열려 있습니다: AUD-...", "code": "DUPLICATE"}` — 재전송하지 말고 기존 `proposal_id` 를 연결 |
| **422** | 입력 검증 실패 | `{"error": "priority 값이 올바르지 않습니다.", "code": "BAD_PRIORITY"}` |

- 422 의 흔한 원인은 `priority` 어휘다. 2026-10-10 감사에서 `"medium"` 을 보내 422 가 났다. **허용값은 `urgent|high|normal|low` 이고, 애매하면 `normal` 로 보낸다.**
  그 밖의 코드: `BAD_TYPE`, `BAD_DEDUPE_KEY`, `BAD_SUMMARY`, `BAD_RATIONALE`, `BAD_CHANGE`(객체 아님), `BAD_EVIDENCE`/`BAD_URL`(근거 URL 이 http/https 가 아님), `BAD_CONFLICTS`(문자열 목록 아님).
- `status`·`proposal_id`·`reviewer` 같은 선언되지 않은 키는 버려진다. 에이전트는 상태를 정하지 못한다.
- 증거수준·공식 근거 요건(`source_expansion` 공식 E3+/그 외 E2+, `role_change` 공식 E3+, `new_person` E2+)은 **승인 시점**에 admin 승인 불변식으로 검사된다. POST 는 형식만 본다.
  E1 근거만 있는 제안도 접수되지만 승인할 수 없다(`422 EVIDENCE`) — 정직한 등급을 그대로 보낸다.
- `conflicts` 는 숨기지 않는다. admin 은 **충돌마다 사유를 남기고 확인해야** 승인할 수 있다. 검토 메모도 충돌에 적는다(감사 degraded, 매체 전사 수준 등).

### 4-3. 제안 재조회 (QC)

```
GET /api/agent/stakeholder/proposals?audit_run_id=coverage-audit-2026-10-10
→ 200 { "audit_run_id": "...", "items": [ {제안...}, ... ] }
```

감사 QC 의 `proposal_ids_retrieved_from_api` 는 이 응답으로 채운다. `audit_run_id` 를 생략하면 전체 제안이 온다.

### 에이전트가 못 하는 것 (대쉬보드 API 가 막는다)

검증 인물 승격 · 공식 직책 덮어쓰기 · 권위 출처 승격 · 인물 병합 · 과거 발언 직책 수정 · 기록 삭제 · **자신의 제안 승인/반려**. 승인·반려는 `role='admin'` 만 가능하다.

---

## 5. 토큰 · 계정

두 개의 서로 다른 자격증명이 있다. 서로 바꿔 쓰지 않는다.

| 이름 | 누가 쓰는가 | 용도 | 보관 |
|---|---|---|---|
| `STAKEHOLDER_SYNC_TOKEN` | **대쉬보드(프로덕션 서버)** | 소스 repo 읽기 — GitHub fine-grained PAT, 대상 repo 한정, **Contents: Read-only** | fly secret (`fly secrets set STAKEHOLDER_SYNC_TOKEN=...`, 사용자가 발급·설정). 에이전트는 필요 없음 |
| `agent` 서비스 계정 | **외부 에이전트(VPS)** | §4 의 API 3개 | VPS `.env` (`chmod 600`, git 금지) |

관련 환경변수(대쉬보드 측): `STAKEHOLDER_SYNC_TOKEN`(없으면 `GITHUB_TOKEN` 폴백), `STAKEHOLDER_SYNC_REPO`(기본 `jeonpapa/kr-drug-pricing-nhi-finance-intelligence`), `STAKEHOLDER_ROOT`(기본 `data/stakeholder`, 프로덕션 `/app/data/stakeholder`).
**프로덕션에서 토큰이 없으면 fail-closed**: GitHub 호출 없이 `error_class=config` 로 실패하고, 운영 원장에 과제 `stakeholder_data_sync:config` 가 닫힐 때까지 1건 유지된다.
화면은 빈 상태일 뿐 안전하다(이미 적재된 데이터와 운영자 결정은 그대로, `apply_scheduled` 도 계속 돈다).

### 전용 `agent` 계정 발급 (프로덕션, 1회 — 관리자)

기존 VPS 디스패처 계정(`docs/agent_team/VPS_SETUP.md` §1)과 **분리된 전용 계정**을 만든다(권한은 같은 `agent` 역할이지만 로그·폐기 단위를 분리한다).
절차는 `VPS_SETUP.md` §1 과 같다. 계정 이메일은 예시이며 운영 도메인에 맞게 정한다.

```bash
export MA_API_BASE=https://ma-ai-dossier.fly.dev

# 1) 강한 난수 비밀번호 — 이 값은 VPS 의 .env 에만 둔다 (git·메모·채팅 금지)
export MA_STAKEHOLDER_AGENT_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

# 2) 관리자 토큰
ADMIN_TOKEN=$(curl -sS "$MA_API_BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"<관리자 이메일>","password":"<관리자 비밀번호>"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

# 3) 서비스 계정 생성 (role=agent)
curl -sS "$MA_API_BASE/api/admin/users" -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"stakeholder-agent@msd.com\",\"password\":\"$MA_STAKEHOLDER_AGENT_PASSWORD\",\"role\":\"agent\"}"
# 기대: HTTP 201 {"user": {"email": "stakeholder-agent@msd.com", "role": "agent", ...}}

# 4) 검증 — registry 는 200, 일반 사용자 API·admin 라우트는 403
AGENT_TOKEN=$(curl -sS "$MA_API_BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"stakeholder-agent@msd.com\",\"password\":\"$MA_STAKEHOLDER_AGENT_PASSWORD\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -sS -o /dev/null -w 'registry     → %{http_code}\n' "$MA_API_BASE/api/agent/stakeholder/registry"  -H "Authorization: Bearer $AGENT_TOKEN"   # 200
curl -sS -o /dev/null -w 'user api     → %{http_code}\n' "$MA_API_BASE/api/stakeholder/overview"        -H "Authorization: Bearer $AGENT_TOKEN"   # 403
curl -sS -o /dev/null -w 'admin api    → %{http_code}\n' "$MA_API_BASE/api/admin/stakeholder/proposals" -H "Authorization: Bearer $AGENT_TOKEN"   # 403
```

- 로그인 토큰은 7일 유효하다. **매 실행마다 새로 로그인**하고 디스크에 남기지 않는다.
- VPS `.env` 예: `MA_API_BASE`, `MA_STAKEHOLDER_AGENT_EMAIL`, `MA_STAKEHOLDER_AGENT_PASSWORD`. 값은 이 문서·repo·채팅에 쓰지 않는다.
- 비밀번호 교체: 관리자 화면(사용자 관리)에서 계정을 지우고 다시 만든 뒤 VPS `.env` 를 갱신한다.
- 기존 `X-Operator-Key`(소스 서비스의 운영자 키)와 무인증 로컬 POST(`127.0.0.1:8791`)는 폐기됐다. 에이전트는 더 이상 로컬 서비스를 호출하지 않는다.

---

## 6. 손실 없음 검증

```bash
# 볼륨 root + DB 가 소스와 같은지 (읽기 전용 검사; 소스 체크아웃이 있으면 --source-dir 로 바이트 미러까지 대조)
python scripts/stakeholder_verify_import.py --root data/stakeholder --db data/db/drug_prices.db \
       [--source-dir <소스 repo 체크아웃>] [--json]
# 프로덕션
fly ssh console -a ma-ai-dossier -C "python scripts/stakeholder_verify_import.py --root /app/data/stakeholder --db /app/data/db/drug_prices.db --json"
```

기대: `failures: []`. 검사 항목은 JSON 파싱, manifest 경로·SHA-256, artifact 바이트, (선택) 소스↔볼륨 SHA-256 미러, DB 카운트(인물·출처 도메인·제안·출처·산출물·런·행사·정본 인용 블록), 정책 시그널(스키마·원문 객체·근거 claim 바이트 동일·locator 판정 재측정·파일 전체 재구성)이다. 허용목록 밖 manifest 항목은 실패가 아니라 `exclusions.excluded_artifacts`/`excluded_source_paths` 로 열거된다.
실패 문구 `missing manifest file` / `SHA-256 mismatch` / `artifact byte-size mismatch` 는 소스 repo 의 `scripts/verify_repository.py` 와 같다 —
**에이전트는 커밋 전에 소스 repo 안에서 `python -I scripts/verify_repository.py` 가 `VALIDATION OK` 인지 확인**하면 대쉬보드에서 mismatch 가 날 일이 거의 없다.

sync 결과를 미리 보려면(쓰기 없음): `python -m agents.stakeholder.sync --source-dir <체크아웃> --dry-run` — `listed`·`would_download`·`rejected`·`orphaned` 만 출력한다.

---

## 7. 체크리스트

**일일 실행 (에이전트)**
1. 07:00 전에 `GET /api/agent/stakeholder/registry` → 스콥으로 사용, 런 JSON 의 `scope.people_registry_sha256`/`source_registry_sha256` 에 `people_sha256`/`sources_sha256` 기록.
2. 원문을 `sources/` 에 저장하고 manifest 에 `source_id`·`url`·`sha256`·`text_path`·`text_sha256` 등록(파일과 해시를 **같은 커밋**에).
3. 런 JSON(`monitoring-runs/<날짜>/`, `audit-runs/<날짜>/`)과 `.md` 를 커밋. `run_id`/`audit_run_id`·`run_date_kst` 필수.
4. 정본 MD 를 고쳤으면 인용이 `locator` 줄 범위 추출 텍스트에서 **글자 그대로** 찾아지는지 확인(문장부호 포함) + manifest `artifacts[]` 해시 갱신.
5. 소스 repo 에서 `python -I scripts/verify_repository.py` → `VALIDATION OK`.
5-1. 정책 시그널을 고쳤으면 `data/policy_signals.json` 이 스키마(위 계약)를 지키는지, 근거 `locator` 가 그 출처 `text_path` 의 줄 범위인지 확인.
6. 제안은 `POST /api/agent/stakeholder/proposals` 로만. `priority` 는 `urgent|high|normal|low`. 409 는 기존 제안 연결, 422 는 `code` 를 보고 수정.
7. 감사 QC 에서 `GET /api/agent/stakeholder/proposals?audit_run_id=<id>` 로 접수된 제안을 재조회해 기록.

**하지 않는 것**
- `data/*.json` 의 승인 상태를 고쳐 승인 결과를 흉내내기(무시된다), 자신의 제안 승인, 인물·직책 직접 수정.
- 원문·사진을 메인 repo(`MA_JABIS`)에 복사하기 — 원문은 소스 repo 와 대쉬보드 볼륨에만 있다.
- 토큰·비밀번호를 repo·로그·채팅에 남기기.

**알려진 소스 측 이슈 (에이전트가 상류에서 고쳐야 함)**

- 정본 MD `2026-10-08_주제별_인물발언_타임라인_정본.md` **296행**(홍승권, 건강보험심사평가원장)의 인용 블록은 닫는 따옴표 **안**에 마침표가 있다
  (`…기대한다.”`). 원문 `sources/hira_20260619_rwe_event.txt:444-446` 에서는 마침표가 닫는 따옴표 **밖**이다(`…기대한다` / `”` / `고 밝혔다`).
  이 때문에 이 인용은 대쉬보드에서 `locator_match=none` → **"원문 불일치"** 로 표시된다.
  조치: 해당 인용의 마침표를 원문대로 따옴표 밖으로 옮기거나 따옴표 안 마침표를 제거하고, manifest `artifacts[]` 의 정본 MD 해시를 갱신해 커밋한다. 다음 sync 에서 정본 sha 가 바뀌어 재파싱되며 자동 해소된다. 대쉬보드 쪽에서 파서를 느슨하게 만들지 않는다(원문 그대로 원칙).
