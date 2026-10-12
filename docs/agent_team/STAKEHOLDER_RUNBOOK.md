# Stakeholder & Verbatim 운영 런북 (2026-10-12 기준)

> 대상: MA 리드 + 운영자. 무엇이 자동으로 돌고, 실패하면 어디에 드러나며, 무엇을 어떻게 고치는지.
> 외부 에이전트 계약: `agents/ingest/STAKEHOLDER_SYNC_README.md` · 규칙: `agents/rules/stakeholder_verbatim_rules.md` · API: `docs/api_contract.md` §11.
> 기준 코드: sync(`agents/stakeholder/sync.py`)·승인(`approval.py`)·API(`api/stakeholder_routes.py`)는 `main` 기준. §2 의 잡·신선도·스모크 행은 계획 Task 8 규격이다(머지 후 `scheduler.py`·`freshness.py` 와 대조).

---

## 1. 한 줄 요약

**외부 에이전트가 소스 repo 에 발행한 산출물(원문·런·정본)을 매일 02:20·08:20 에 읽어 오고, 사람·출처·제안의 결정은 대쉬보드 DB 가 기록 원본이다.**
sync 는 결정 테이블(`sv_person`·`sv_person_role_history`·`sv_source_domain`·`sv_audit_proposal`)에 **insert-if-absent 만** 하므로 어떤 sync·재sync·볼륨 재수신도 운영자 승인·반려·효력일을 덮어쓰지 못한다.

| 구분 | 자동? | 근거 |
|---|---|---|
| 소스 repo → 볼륨 미러 + DB 적재 | ✓ 매일 02:20·08:20 | `stakeholder_data_sync` → `agents.stakeholder.sync.sync` |
| 승인된 제안의 익일 반영 (`scheduled` → `applied`) | ✓ 같은 잡 안에서 (토큰이 없어도 DB 가 있으면 돈다) | `approval.apply_scheduled` |
| 제안 승인·반려, 충돌 확인, 인물 관리, 사진 | ✗ 사람(admin) | 화면 `/admin/stakeholder-audit`, `/admin/stakeholder-people` |
| 스콥 pull · 제안 POST | 외부 에이전트가 API 로 | `/api/agent/stakeholder/*` |

---

## 2. 잡 · 신선도 · 스모크

시간은 모두 Asia/Seoul.

| 항목 | 스케줄/기준 | 하는 일 | 실패 시 |
|---|---|---|---|
| 잡 `stakeholder_data_sync` | 매일 02:20 · 08:20 (02:00 reimb·02:10 policy 뒤, VPS 07:00 일일·07:40 감사 커밋 뒤) | GitHub Git Trees 로 바뀐 파일만 받기 → 무결성 판정 → `sv_*` 멱등 적재 → `apply_scheduled` | 원장 과제. 토큰 부재(프로덕션)·repo/권한 오류는 `stakeholder_data_sync:config` 1건이 닫힐 때까지 유지 |
| 신선도 `stakeholder_monitoring_run` | 최신 `sv_monitoring_run.run_date` ≥ 오늘−2일 | 에이전트가 일일 모니터링을 안 올렸거나 sync 가 못 받음 | ops_incident, 런북 `stakeholder_data_sync` |
| 신선도 `stakeholder_audit_run` | 최신 `sv_audit_run.run_date` ≥ 오늘−3일 | 감사 런 정체 | ops_incident, 런북 `stakeholder_data_sync` |
| 신선도 `stakeholder_proposal_backlog` | 7일 넘게 `needs_review`/`needs_evidence` 인 제안 0건 | **사람 몫** — 승인·반려 대기 | improvement 과제 `…:backlog` 1건 (런북 없음) |
| 신선도 `stakeholder_source_integrity` | `integrity_status IN ('mismatch','missing')` 인 출처 0건 | 원문 파일이 manifest 해시와 다르거나 없음 | ops_incident (priority 3), 런북 `stakeholder_data_sync` |
| 스모크 | 매일 07:50 (`platform_smoke`) | 화면 4개 `stakeholder-signals`, `stakeholder-signals/workbench`, `admin/stakeholder-people`, `admin/stakeholder-audit` + auth-gates 2(`/api/admin/stakeholder/proposals`·`/api/agent/stakeholder/registry` 가 user 토큰에 403) | 실패 체크 집합당 과제 1건 |

정상 상태에서는 과제가 0건/일이다. 예외: 제안이 7일 넘게 쌓이면 backlog 1건이 정상적으로 생긴다(사람이 처리하면 닫힌다).

---

## 3. 승인 운영 (admin)

화면: **모니터링 감사** (`/admin/stakeholder-audit`). 사이드바 배지가 미검토 제안 수다.

1. 제안 카드의 **근거 링크**와 **증거수준**(E0~E4)을 읽는다.
2. 제안에 `conflicts`(충돌·검토 메모)가 있으면 **충돌마다** 사유(2~1000자)를 적고 *확인* 한다.
   *모든 충돌이 현재 문구 그대로 확인돼야 승인 게이트가 열린다*(사용자 결정 2026-10-12). 확인은 충돌 게이트만 연다 — 근거 불변식은 확인과 무관하게 그대로 검사된다.
   문구가 바뀌면 이전 확인은 `stale`(무효)이 되어 다시 확인해야 하고, 유효한 확인은 덮어쓸 수 없다.
3. **승인** 또는 **반려**(둘 다 사유 2~1000자 필수). 검토자(reviewer)는 로그인한 admin 의 이메일이 자동 기록된다.
4. 승인하면 상태가 `scheduled`, 효력은 **익일(KST)** 이다. 다음 `stakeholder_data_sync` 실행(02:20/08:20)이 `effective_from <= 오늘` 인 제안을 `applied` 로 반영한다.
   - `role_change`: 현재 직책을 `sv_person_role_history(applied, effective_to=오늘)` 로 내리고 새 직책 적용 + `verification_status='verified'`.
   - `new_person`: 승인 시 `candidate` 로 등록돼 있고, 반영은 제안 상태만 `applied` (자동 `verified` 승격 없음).
   - `source_expansion`: 승인 시 도메인이 레지스트리에 추가돼 있고, 반영은 제안 상태만 `applied`.
   - 새 인물·출처는 익일부터 에이전트의 스콥(`GET /api/agent/stakeholder/registry`)에 들어간다.

승인·반려·확인 API 오류 코드 (`{error, code[, details]}`):

| HTTP | code | 의미 / 조치 |
|---|---|---|
| 404 | `NOT_FOUND` | 제안·인물 없음 |
| 409 | `STATE` / `RACE` | 이미 결정됐거나 동시에 다른 admin 이 처리 — 새로고침 |
| 409 | `ALREADY_ACKNOWLEDGED` | 유효한 충돌 확인이 이미 있음 (원 확인 보존) |
| 409 | `DUPLICATE_DOMAIN` / `DUPLICATE_PERSON` | 이미 레지스트리에 있는 도메인 / 같은 기관의 동명 인물 |
| 409 | `PENDING_ROLE_CHANGE` | 같은 인물에 적용 대기 중인 직책 변경이 있음 — 적용 후 다시 |
| 422 | `BAD_REASON` | 사유가 2~1000자가 아님 (`reviewer` 는 토큰에서 자동) |
| 422 | `CONFLICTS_UNACKNOWLEDGED` | 미확인 충돌 있음 — `details.unacknowledged` 가 index 목록. 화면에서 충돌별로 확인 |
| 422 | `BAD_CONFLICT_INDEX` | 충돌 index 가 범위 밖(0부터) |
| 422 | `EVIDENCE` | 증거수준·공식 근거 요건 미충족 (`source_expansion` 공식 E3+/그 외 E2+, `role_change` 공식 E3+, `new_person` E2+) — 반려 후 에이전트가 근거를 보강해 재제안 |
| 422 | `EVIDENCE_DOMAIN` | 제안 도메인과 일치하는 근거 URL 이 없음 |
| 422 | `BAD_SOURCE` / `BAD_ROLE` / `BAD_PERSON` | 제안 필드 누락 (출처명·도메인·출처군 / 변경 직책 / 이름·기관·부서·직책) |

검증(`verified`) 인물의 소속·부서·직책은 인물 관리 화면(PUT)에서 직접 수정할 수 없다(409) — 직책 변경은 `role_change` 제안 경로로만.

---

## 4. 흔한 과제별 조치

시작점은 항상 같다: `GET /api/admin/stakeholder/integrity`(admin) 또는 `python -m agents.stakeholder.sync --now -v` 의 출력, 그리고 운영 원장(`/admin/agent-tasks`).

### 4-1. `stakeholder_data_sync:config` — 토큰·repo 설정

원인: 프로덕션(`/app/data` 존재)에서 `STAKEHOLDER_SYNC_TOKEN`(→ `GITHUB_TOKEN` 폴백)이 없음(`auth required: STAKEHOLDER_SYNC_TOKEN missing (fail-closed in production)`), 또는 GitHub 응답이 401/404/403(레이트리밋 아님) — 토큰 만료·권한 부족·repo 이름 오류.
조치:
1. 토큰이 없으면 fine-grained PAT(대상 repo 한정, Contents: Read-only)를 발급해 `fly secrets set STAKEHOLDER_SYNC_TOKEN=<값> -a ma-ai-dossier` (**값을 채팅·문서·로그에 남기지 않는다**).
2. repo 가 다르면 `fly secrets set STAKEHOLDER_SYNC_REPO=<owner/repo>` (기본 `jeonpapa/kr-drug-pricing-nhi-finance-intelligence`).
3. `fly ssh console -a ma-ai-dossier -C "python -m agents.stakeholder.sync --now -v"` → `ok=True` 확인 후 원장 과제를 닫는다.

이 상태에서도 화면은 이미 적재된 데이터를 보여 주고, `apply_scheduled` 는 계속 돈다(운영자 결정의 익일 반영은 GitHub 접근과 무관).

### 4-2. 일시적 실패 (`transient`)

GitHub 5xx·네트워크·레이트리밋·`stakeholder sync already running (lock held)`. 다음 스케줄(02:20 → 08:20 → 익일 02:20)이 다시 시도한다. 수동 재실행 중 잠금에 걸리면 수동 sync API 가 409 `SYNC_RUNNING` 을 돌려준다 — 잠시 후 재시도.
`fetch_failed` 가 일부 파일만 있으면 그 파일만 다음 실행에서 다시 받는다(blob sha 불일치 파일은 쓰지 않음).

### 4-3. 원문 무결성 `mismatch` / `missing` (`stakeholder_source_integrity`)

1. `GET /api/admin/stakeholder/integrity` — `problems`(출처별 사유), `artifact_problems`, `summary` 확인.
2. **`mismatch`**: 볼륨 파일이 manifest `sha256`/`text_sha256`/`bytes` 와 다름.
   - 볼륨 쪽 변조·손상이면 다음 sync 가 **자가 치유**한다(받을 당시 SHA-256 과 디스크가 다르면 원격 sha 가 같아도 다시 받는다). `sync --now` 한 번으로 확인.
   - 재수신 후에도 남으면 소스 repo 의 파일과 manifest 해시가 어긋난 것이다 → 외부 에이전트에게 소스 repo 에서 `python -I scripts/verify_repository.py` 를 돌려 고쳐 커밋하게 한다. 대쉬보드에서 해시를 맞추지 않는다.
3. **`missing`**: 파일이 볼륨에 없음. 원인 후보 — 소스 repo 에 파일이 없음(manifest 만 갱신됨), 경로가 허용목록 밖/안전하지 않음(`rejected`), 20MB 초과, 총량 200MB 초과로 이월(`deferred`). `sync --dry-run` 의 `rejected`·`deferred`·`would_download` 로 구분.
4. `no_local`(받아 둔 파일이 없는 출처, 예: video-only `SRC-BOK-NHIS-20251211-YT`)은 손실이 아니라 소스 상태다 — 과제 대상이 아니다.

### 4-4. 런 정체 (`stakeholder_monitoring_run` / `stakeholder_audit_run`)

1. `sync --dry-run` 으로 소스 repo 에 새 `monitoring-runs/<날짜>/`·`audit-runs/<날짜>/` 가 있는지 본다 (`would_download`).
2. 소스에 있는데 `sv_*_run` 에 없으면 `sync --now -v` 의 `ERROR: …/daily_monitoring.json 적재 실패` — `run_id`·`run_date_kst`(감사는 `audit_run_id`) 누락이 가장 흔하다. 외부 에이전트에게 전달.
3. 소스에도 없으면 외부 에이전트(VPS) 쪽 문제다 — 에이전트 실행 로그 확인.

### 4-5. 정본 MD 문제

- `정본 파싱 실패` / `정본 적재 실패`: 기존 정본 행·sha 는 유지되고 다음 실행이 다시 시도한다. 소스 MD 구조를 에이전트와 확인.
- 화면의 **"원문 불일치"** 인용(`locator_match=none`): 인용문이 `locator` 줄 범위 추출 텍스트에서 글자 그대로 찾아지지 않음. 건수는 `sv_meta.canonical_stats_json.locator_match`.
  **현재 알려진 1건**: 정본 296행(홍승권) 마침표가 닫는 따옴표 안에 있으나 원문(`sources/hira_20260619_rwe_event.txt:444-446`)에서는 밖이다. 상류(소스 repo) 수정 대기 — 대쉬보드에서 우회하지 않는다. 상세는 `STAKEHOLDER_SYNC_README.md` §7.

### 4-6. 제안 backlog (improvement)

`needs_review`/`needs_evidence` 가 7일 넘으면 생긴다. 에러가 아니다. **모니터링 감사** 화면에서 승인·반려하면 닫힌다. 근거가 약해 승인할 수 없는 제안(E1 만 등)은 사유를 적고 **반려**한다 — 방치하면 backlog 가 계속 남는다.

### 4-7. 반영 실패 `application_failed`

`apply_scheduled` 가 제안별로 실패를 기록한다(잡은 죽지 않음). 모니터링 감사 화면의 제안 이벤트에 `application_failed` 와 사유가 남고, `role_change` 의 `scheduled` 직책 이력 행은 `failed` 로 바뀌어 그 인물의 이후 직책 변경이 막히지 않는다.
복구: 원인(예: 승인 후 인물 행이 삭제/변경됨)을 확인하고 에이전트가 새 `role_change` 를 제안하게 한다. DB 를 직접 고치지 않는다.

### 4-8. 에이전트 API 403/401

에이전트 계정이 `role='agent'` 인지, 토큰이 7일 이내인지(매 실행 재로그인) 확인. 계정 발급·검증 절차는 `STAKEHOLDER_SYNC_README.md` §5 (`docs/agent_team/VPS_SETUP.md` §1 과 동일 패턴). agent 토큰이 `/api/stakeholder/*`·`/api/admin/*` 에 403 인 것은 정상이다.

---

## 5. 수동 명령

프로덕션은 `fly ssh console -a ma-ai-dossier -C "<명령>"` (로컬은 저장소 루트에서 `.venv/bin/python`). 모든 명령은 DB/볼륨 경로를 `--db`·`--root` 로 바꿀 수 있다.

| 명령 | 하는 일 |
|---|---|
| `python -m agents.stakeholder.sync --now [-v]` | 원격(GitHub) sync 1회 — 스케줄 잡과 같은 경로. 토큰 필요 |
| `python scheduler.py --stakeholder-sync-now` | 잡 래퍼(`guarded_job`) 경유 — 원장에 결과가 남는다 (T8) |
| `python -m agents.stakeholder.sync --now --dry-run` | 목록·변경 수만 출력, 파일·DB 쓰기 없음 (`listed`·`would_download`·`rejected`·`orphaned`). 프로덕션 토큰 부재 시에는 dry-run 도 fail-closed |
| `python -m agents.stakeholder.sync --source-dir <디렉터리> [--root R] [--db D]` | 로컬 디렉터리를 원격으로 간주해 1회 import — 토큰 없이 최초 시딩/검증용 (임시 root·DB 를 주면 운영 데이터를 건드리지 않는다) |
| `... --today YYYY-MM-DD` | `apply_scheduled` 기준일 지정 (테스트/재현용) |
| `python scripts/stakeholder_verify_import.py --root /app/data/stakeholder --db /app/data/db/drug_prices.db [--source-dir S] [--json]` | 손실 없음 검증 (T10) — `failures: []` 가 정상 |
| `python scheduler.py --freshness-now` | 신선도 즉시 판정 — `stakeholder_*` 4행 확인 |
| `python scripts/smoke.py --base <URL> --login-email <admin>` | 화면 스모크 |
| `curl -H "Authorization: Bearer <admin 토큰>" -X POST .../api/admin/stakeholder/sync` | 화면 없이 수동 sync (admin). 실패 시 409 `SYNC_RUNNING` / 503 `SYNC_CONFIG` / 502 `SYNC_FAILED` |

CLI 출력: `stakeholder sync: ok=… listed=… downloaded=… unchanged=… rejected=… orphaned=… canonical_reparsed=… registry_new=… scheduled_applied=… changes=…` 와 `integrity:`·`canonical:`·`db:` 요약. 같은 스냅샷의 두 번째 실행은 `changes=0` 이어야 한다(멱등). 종료 코드 0 = 성공.

---

## 6. 볼륨 · DB 위치

| 위치 | 내용 |
|---|---|
| `/app/data/stakeholder/` (`STAKEHOLDER_ROOT`) | 소스 repo 와 같은 상대경로: `sources/`(+`raw/`·`user_input/`), `monitoring-runs/`, `audit-runs/`, `data/`, `scripts/`, 루트 정본 MD·manifest·관계도 |
| `/app/data/stakeholder/photos/` | 인물 사진(`<person_id>-<hex>.webp`) — **대쉬보드가 쓰는 유일한 파일**. 소스 repo 에 없다 |
| `/app/data/stakeholder/.sync/lock` | sync 동시 실행 잠금 (`flock`). 삭제 불필요 |
| `/app/data/db/drug_prices.db` | `sv_*` 15개 테이블: `sv_meta`·`sv_person`·`sv_person_role_history`·`sv_source_domain`·`sv_source`·`sv_artifact`·`sv_event`·`sv_statement`·`sv_quote`·`sv_monitoring_run`·`sv_audit_run`·`sv_audit_proposal`·`sv_audit_proposal_event`·`sv_audit_conflict_ack`·`sv_sync_file` |

- **결정 테이블(DB 가 기록 원본)**: `sv_person`·`sv_person_role_history`·`sv_source_domain`·`sv_audit_proposal`(+`_event`, `sv_audit_conflict_ack`). 이 값은 소스 repo 에서 복구되지 않는다 — DB 백업 대상으로 취급한다.
- **미러(소스에서 재구성 가능)**: 볼륨 파일, `sv_source`·`sv_artifact`·`sv_event`·`sv_statement`·`sv_quote`·`sv_monitoring_run`·`sv_audit_run`, `sv_sync_file`(받은 파일 색인).
- 언론 원문·사진은 git(메인 repo)에 커밋하지 않는다(`.gitignore` 의 `data/stakeholder/`).
- `sv_*` 테이블은 첫 호출(첫 sync 또는 첫 API 요청)에서 지연 생성된다.

---

## 7. 복구

| 상황 | 조치 |
|---|---|
| **볼륨 파일 유실/손상** (DB 는 정상) | `sync --now` 1회. `sv_sync_file` 색인이 가리키는 파일이 디스크에 없거나 크기/SHA-256 이 다르면 원격 sha 가 같아도 **전량 다시 받는다**(≈25MB). 결정 테이블(`sv_person` 등)은 영향 없음. 이후 `stakeholder_verify_import.py` 로 `failures: []` 확인 |
| **볼륨·DB 모두 유실** | DB 백업 복원이 우선(결정 테이블은 소스에서 복구되지 않음). 백업이 없으면 `sync --now` 로 미러는 복구되지만 승인·반려·효력일·사진·충돌 확인은 소스 시드(`data/*.json`)의 값으로만 돌아온다 — 최후 수단 |
| **토큰 유출 의심** | GitHub 에서 PAT 즉시 폐기 → 새 PAT 발급 → `fly secrets set STAKEHOLDER_SYNC_TOKEN=...`. Contents: Read-only 라 소스 repo 변조 위험은 없다 |
| **정본이 잘못 파싱됨** | 소스 정본 MD 를 고쳐 커밋하면 sha 변경으로 재파싱된다. 대쉬보드 DB 의 `origin='canonical_md'` 행만 교체되고 그 외 origin 은 건드리지 않는다 |
| **sync 중 프로세스 종료** | 파일은 `.tmp` 에 쓴 뒤 교체(원자적)하고 DB 는 단계별 트랜잭션이라 중간 상태가 남지 않는다. 다음 실행이 이어서 처리 |
| **원격에서 파일이 삭제됨** | 볼륨에서 지우지 않는다(`orphaned` 로만 집계). 의도적 정리는 수동으로 하되 manifest·DB 와의 일관성을 먼저 확인 |

---

## 8. 배포 순서 (사용자만 실행 — 에이전트는 실행하지 않는다)

배포는 기능 완성 → 로컬 검증 이후, 사용자 최종 확인 뒤에만 한다.

1. **비밀**: `fly secrets set STAKEHOLDER_SYNC_TOKEN=<fine-grained PAT, 소스 repo Contents:read> -a ma-ai-dossier` (+ 선택 `STAKEHOLDER_SYNC_REPO=<owner/repo>`). 토큰 없이 배포해도 안전하다 — 잡이 `stakeholder_data_sync:config` 1건을 만들고 화면은 빈 상태.
2. **코드**: 푸시 후 `fly deploy` (Pillow 는 `requirements.txt`, 볼륨 디렉터리는 `scripts/start_production.sh` 가 만든다).
3. **첫 sync (1회)**: `fly ssh console -a ma-ai-dossier -C "python -m agents.stakeholder.sync --now"` → 전량(≈25MB) 수신 + import. 토큰이 아직 없으면 `fly ssh sftp shell` 로 소스를 `/app/data/stakeholder_import/` 에 올리고 `... sync --source-dir /app/data/stakeholder_import` 후 업로드 디렉터리를 삭제.
4. **검증**: `fly ssh console -a ma-ai-dossier -C "python scripts/stakeholder_verify_import.py --root /app/data/stakeholder --db /app/data/db/drug_prices.db --json"` → `failures: []`.
5. **에이전트 계정 발급**: `STAKEHOLDER_SYNC_README.md` §5 절차(`role=agent` 전용 계정) → 외부 에이전트 VPS `.env` 에 설정 → 에이전트가 07:00 실행 전에 `GET /api/agent/stakeholder/registry` 를 pull 하도록 전환(cutover).
6. **확인**: 화면 4개 열람, `python scheduler.py --freshness-now` 의 `stakeholder_*` 행, 스모크 통과. 정본 296행의 "원문 불일치" 1건은 상류 수정 전까지 정상.

---

## 9. 관련 문서

- 외부 에이전트 계약·API·계정 발급: `agents/ingest/STAKEHOLDER_SYNC_README.md`
- 규칙(소유권·승인 불변식·접근 등급·저작권·금지): `agents/rules/stakeholder_verbatim_rules.md`
- 서비스 계정 패턴: `docs/agent_team/VPS_SETUP.md` §1
- 구현 계획: `docs/superpowers/plans/2026-10-12-stakeholder-verbatim-migration.md`
