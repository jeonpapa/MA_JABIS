# 약가·건보재정 Stakeholder & Verbatim Intelligence — 대쉬보드 이관 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 각 Task 머리의 **[Opus]** 는 설계 판단이 필요한 구현(Opus 5.5 빌더), **[Sonnet]** 은 기계적 작업(복사·픽스처·보일러플레이트·문서)이다. 오케스트레이터(Fable 5.1)가 Task 단위로 검증한다.

**Goal:** 독립 서비스 `jeonpapa/kr-drug-pricing-nhi-finance-intelligence`(FastAPI `stakeholder_service.py` + JSON 스토어 + 파일 아카이브 + 정적 스케치 4종)를 MA AI Dossier 대쉬보드 안으로 **손실 없이** 옮긴다 — 인물 레지스트리·출처 레지스트리·감사 제안의 **결정 권한은 대쉬보드(admin)** 가 갖고, 일일 모니터링·커버리지 감사·원문 아카이브·정본 MD 는 **외부 에이전트(Key/Hermes, VPS)** 가 계속 소스 repo 에 발행하며 대쉬보드가 **git-sync 로 읽기만** 한다. 운영자 승인 결정은 어떤 sync 에도 덮어써지지 않는다.

**Architecture:** 경계는 **"누가 쓰는가"** 로 나눈다. ① **외부 에이전트 소유(읽기 전용 sync)** — `sources/`(원문·추출텍스트·SHA-256), `source_manifest.json`, `monitoring-runs/`, `audit-runs/`, 정본 MD 4종·관계도·정책 2종. 매일 02:20·08:20 `stakeholder_data_sync` 잡이 GitHub Git Trees API 로 blob sha 를 대조해 **바뀐 파일만** 볼륨 `data/stakeholder/`(소스 repo 와 동일 상대경로)에 내려받고, `source_manifest.json` 의 SHA-256 으로 무결성을 판정해 `sv_*` 테이블에 멱등 적재한다(정본 MD → 결정론 파서 → `sv_event`/`sv_statement`/`sv_quote`, 바뀐 sha 일 때만 `origin='canonical_md'` 행을 한 트랜잭션으로 교체). ② **대쉬보드 소유(DB 가 기록 원본)** — `sv_person`·`sv_person_role_history`·`sv_source_domain`·`sv_audit_proposal`(+event). sync 는 이 네 테이블에 **insert-if-absent 만** 하고 기존 행은 절대 갱신하지 않는다(승인·반려·효력일 보존). ③ **에이전트 → 대쉬보드 API** — 감사 에이전트는 `role='agent'` 서비스 계정으로 `/api/agent/stakeholder/registry`(모니터링 스콥 pull) 와 `/api/agent/stakeholder/proposals`(제안 POST, dedupe 409) 만 쓴다(기존 `X-Operator-Key` 폐기, `agent` 역할의 `/api/agent/*` 한정 유지). 승인 → `scheduled`(익일 효력) → 매일 sync 잡의 `apply_scheduled` 가 `applied` 로 반영(직책 변경은 `role_history` 보존 + `verified` 전환). 파일 잠금·원자적 JSON 쓰기·감사 트랜잭션 저널은 sqlite WAL 의 `BEGIN IMMEDIATE` 트랜잭션 하나로 대체한다. 원문 전문·원본 HTML/HWP 는 볼륨에만 두고 admin 만 열람·다운로드, 사용자는 **인용문 + locator 창(≤600자) + URL + SHA-256 + 증거등급** 까지만 본다. 언론사 CDN 사진은 핫링크하지 않는다(내부 업로드 사진만, 인증 라우트로 서빙).

**Tech Stack:** Python 3.12, sqlite3(WAL, 공용 `data/db/drug_prices.db` — `sv_` 접두 테이블 14개), Flask Blueprint(`api/stakeholder_routes.py`), APScheduler `JobSpec`/`guarded_job`, `agents/agent_tasks`(freshness·runbooks·signals), React+TS(Tailwind 다크 팔레트, `pages/*/page.tsx` 패턴), Pillow(사진 정규화 — `requirements.txt` 에 추가 필요), GitHub REST(Git Trees + Contents raw, 토큰 fail-closed), pytest(`tests/conftest.py` 실제 DB 읽기전용 가드).

**Spec:** 소스 repo 의 `MONITORING_SOURCE_AND_QUOTE_POLICY.md`(mandatory-v1: 원문 그대로·verbatim/summary/analysis 분리·다매체 차집합·`search_log`·`coverage_matrix`·`source_saturation`), `MONITORING_AUDIT_AGENT_POLICY.md`(감사축 3·증거수준 E0~E4·제안 필드·상태 7·에이전트 금지행위), `dashboard-sketches/001~004`(의도된 UX), `stakeholder_service.py`(승인 불변식), `scripts/verify_repository.py`(무결성 검증 의미론). 대상 측 선례: `agents/ingest/REIMB_DATA_README.md`·`agents/ingest/policy_analysis_sync.py`(git-sync + sha 게이트 + prod fail-closed), `agents/service_requests/store.py`(독립 store + append-only 이벤트), `docs/superpowers/plans/2026-09-30-agent-team-P1.5-platform-coverage.md`(잡·신선도·스모크 규약).

---

## 작업 규칙

- Python: `/Users/kimjeong-ae/MA_AI_Dossier/.venv/bin/python`. 테스트는 저장소 루트에서 `.venv/bin/python -m pytest <경로> -q`. 프론트는 `cd frontend && npm run build`(타입 오류 = 실패).
- TDD: 실패 테스트 → 최소 구현 → 통과 → 커밋. 커밋 메시지는 한국어, 끝에 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **실제 DB 보호**: `tests/conftest.py` 가 `data/db/drug_prices.db`·`users.db` 를 `mode=ro` 로 돌리고 세션 종료 시 fingerprint 를 대조한다. 모든 store 함수는 `db_path=` 를 받고 테스트는 `tmp_path` DB 만 쓴다. **모듈 import 시 실제 DB 에 테이블을 만들지 않는다**(`ensure_tables` 는 첫 호출에서 지연 실행).
- 네트워크 금지(GitHub API·LLM·Playwright). sync 는 `source_dir=`(로컬 디렉터리를 원격으로 간주) 모드로 테스트한다. `flyctl`·`git push`·배포 금지 — 문서 말미 "배포(사용자 확인 후)" 절차대로 **사용자만** 한다.
- 자격증명은 `config/.env` / fly secrets 만(`STAKEHOLDER_SYNC_TOKEN`). 코드·테스트·문서·픽스처에 값 금지.
- **소스 디렉터리 불변**: `/private/tmp/claude-501/-Users-kimjeong-ae-MA-AI-Dossier/b3279d67-c95d-451b-98e4-5ceca336f208/scratchpad/nhi_src/`(이하 `<SRC>`) 는 읽기만 한다. 실데이터 검증(T10)은 `<SRC>` 를 `--source-dir` 로 주고 **임시 root·임시 DB** 로 돈다.
- **저작권 하이진**: 언론 원문(HTML·추출 텍스트)·사진은 메인 repo(`MA_JABIS`, 공개 취급)에 커밋하지 않는다. 테스트 픽스처는 **합성 데이터**(가짜 매체·가짜 인용)만. 실데이터는 볼륨(`data/stakeholder/`, `.gitignore`)에만 있다.
- 신호 경계 관례(P1): 원장 기록 실패는 경계 함수 안에서 삼키고 warning 만 남긴다. 잡·API 가 신호 때문에 죽으면 회귀다.
- 테스트 대상 Task 외 공유 파일(`scheduler.py`·`signals.py`·`freshness.py`·`smoke.py`·router·Sidebar)은 **지정된 Task 만** 만진다(아래 경계표).

## 조사 확인 결과 (코드로 대조)

계획 전 두 코드베이스를 직접 읽었다. 빌더가 다시 확인하지 않아도 되는 사실들.

| # | 항목 | 확인 결과 (`파일:위치`) |
|---|---|---|
| 1 | 소스 서비스 범위 | `stakeholder_service.py` 560줄: `/api/health`, `/api/people`(GET/POST/PUT/PATCH status/POST·DELETE photo), `/api/audit/proposals`(GET/POST/approve/reject), `/api/audit/sources`, 페이지 4개(`/`, `/people`, `/analyst`, `/audit`), `/media`·`/dashboard` 정적 마운트. 운영자 인증 `require_operator`(`X-Operator-Key` ↔ 키 파일 `hmac.compare_digest`). 제안 POST 는 **무인증**(127.0.0.1:8791 로컬 전제 — `audit-runs/2026-10-10/raw/proposal_post_responses.json` 의 `api` 필드). |
| 2 | 승인 불변식 | `has_evidence(levels, official)` — `publisher`·`locator` 둘 다 있어야 인정; `source_expansion`: official 은 {E3,E4}+`source_class=='official'`, 그 외 {E2,E3,E4}; `normalize_domain` 은 경로·쿼리·fragment 거부; 도메인이 근거 URL hostname 과 일치해야 함; 레지스트리 중복 도메인 409. `role_change`: 공식 {E3,E4} 필수, `pending_role_changes` 에 scheduled 있으면 409, `role` 비면 422. `new_person`: {E2,E3,E4}, name/org/dept/role 전부, 같은 기관 동명 409, 등록은 항상 `candidate`. 공통: `conflicts` 비어 있지 않으면 422, 상태 {needs_review, needs_evidence} 만 승인·반려 가능, `effective_from=tomorrow_kst()`. 검증 인물의 org/dept/role 직접 수정은 409(PUT). |
| 3 | 동시성 | `threading.RLock` + `fcntl.flock` 파일 잠금 + `.tmp` → `replace` 원자 쓰기 + 승인 시 `.audit_transaction.json` 저널 쓰고 스토어 2~3개 순차 교체 후 저널 삭제, 기동 시 `recover_audit_transaction()`. 대상은 sqlite WAL — 한 연결의 `BEGIN IMMEDIATE … COMMIT` 이 같은 보장을 준다(T1·T2). |
| 4 | 데이터 규모 | 파일 384개. `sources/` 20M(그중 `user_input/monitoring_list_upscaled.png` 14M, 원본 jpeg 1.9M, 원문 HTML 20개 ≤400K, HWP 444K), `monitoring-runs/` 1.4M(3일, raw xml 86개/일), `audit-runs/` 3.1M(2일), `data/` 44K. people 11, source domains 32, proposals 5(전부 `needs_review`: new_person 3·source_expansion 1·role_change 1), manifest sources 22(local 있음 20 + user image 2; `SRC-BOK-NHIS-20251211-YT` 는 video-only), artifacts 10, evidence grade 어휘 7. |
| 5 | 정본 MD 구조 | `2026-10-08_주제별_인물발언_타임라인_정본.md`: §1 마스터 표 **12 행사**, §2~6 주제 5개 아래 `## YYYY-MM-DD｜제목`(U+FF5C) 26개(행사 교차 표시), `### 이름 — 당시 직책` 35개(`### 이소영·홍승권` 복수 1개), `> ` 인용 블록 **52개**(주제 간 교차 표시로 중복 포함), `- **등급:**`·`- **근거:**` 33개씩, 근거 locator 형식 `` `sources/<file>.txt:<a>-<b>` ``(세미콜론으로 복수), §7 반복 메시지, §8 출처 검증대장 14행, §9 보류 6항. 부서-인물지도 §2.2 에 11명 중요도 점수표. |
| 6 | 런 JSON 구조 | `daily_monitoring.json`(schema 1.1): `run_id`, `run_date_kst`, `executed_at_kst`, `window_*`, `run_status`, `scope{scope_snapshot_id, people_registry_sha256, source_registry_sha256, active_people[], approved_sources[]}`, `search_summary`, `events[]`, `statements[]`, `search_log[43]`, `coverage_matrix[32]`, `source_saturation{status, groups}`, `limitations`, `quality_control`, `next_run`. `coverage_audit.json`(1.0): `audit_run_id`, `run_status`(`degraded`), `upstream_status`, `claim`, `why_this_matters`, `scope_counts`, `axes{source_expansion, role_change, new_person}`, `evidence_register`, `proposal_summary`, `adversarial_review`, `access_failures`, `quality_control`. 두 JSON 모두 절대경로(`/opt/data/obsidian-vault/...`)를 품고 있다 — 표시용 문자열일 뿐, 파일 접근 키로 쓰지 않는다. |
| 7 | 대상 인증 | `api/auth.py:75-103` `require_auth(role=None)` — 미지정은 user·admin, `agent` 는 403; `role="agent"` 라우트는 agent·admin. 역할은 토큰 claim 만 본다. `users.py:76` 허용 role `admin|user|agent`. 관리자 UI 는 각 페이지가 `fetchMe()` 로 셀프 가드(`pages/admin/service-requests/page.tsx`). `isAdmin()` 은 `ADMIN_EMAIL` 비교(`utils/authUsers.ts:89`). |
| 8 | 대상 저장 패턴 | 독립 store 선례 `agents/service_requests/store.py`(`DEFAULT_DB_PATH = data/db/drug_prices.db`, `SCHEMA_SQL` + 멱등 ALTER, append-only `service_request_event`). `agents/agent_tasks/store.py:88` `connect()` = `sqlite3.connect(timeout=BUSY_TIMEOUT_SECONDS)` + `row_factory`. 기존 73 테이블에 `sv_` 접두 없음(충돌 0). `DrugPriceDB` mixin 에 끼우지 않고 독립 store 로 둔다(`agents/db/base.py` 무접촉). |
| 9 | git-sync 선례 | `agents/ingest/policy_analysis_sync.py`: `GITHUB_TOKEN`/전용 토큰, GitHub Contents API `Accept: application/vnd.github.raw`, 프로덕션(`/app/data` 존재) 토큰 없으면 `error: auth required` fail-closed → `scheduler.py:924-937` 이 `error_class="config"` 로 `policy_intel_analysis_sync:config` 과제 1건 유지. `_reimb_committee_data_sync` 는 `data/reimb/.last_applied_hash` 해시 게이트. |
| 10 | 잡 규약 | `scheduler.py:969-1041` `JobSpec`/`build_job_specs`, `agents/agent_tasks/jobs.py:122` `guarded_job`, `signals.py:37` `JOB_RUNBOOK`(키 집합 == 등록 잡 id — `tests/test_scheduler_job_specs.py:21`), `runbooks.py:217` `RUNBOOKS.update`, `freshness.py:632` `DOMAIN_CHECKS`(20개; `_max_ts(ctx, domain, sql, max_days, runbook, what, priority)` 헬퍼, `kind="improvement"` 는 `{domain}:backlog` 1행), `scripts/smoke.py` `SCREENS`(22개 — `tests/test_smoke_screens.py:67` 이 `== 22` 를 핀). |
| 11 | 볼륨·부팅 | `fly.toml` 볼륨 `/app/data`; `scripts/start_production.sh` 가 필수 디렉터리 `mkdir -p` + 부팅 1회 `--amjilsim-daily-crawl-now`. `.gitignore` 는 `data/uploads/`·`data/policy_intelligence/` 등 볼륨 산출물 제외. Pillow 는 venv 에 12.2.0 이 있으나 **`requirements.txt` 에 없다**(T4 가 추가 — 없으면 프로덕션 이미지에서 사진 업로드 500). |
| 12 | 스케치 UX | 001 리더십 타임라인: KPI 4 + 이번 달 시그널 카드 + 주제 탭 + 증거등급 범례 + 행 테이블(행사일·주제·행사·인물(아바타)·발언·등급) + 우측 드로어(인용·메타·판단·한계·출처 감사 SHA-256). 002 워크벤치: 좌 필터(기관·주제·증거·기간) / 중앙 주제 레인 스트림 / 우측 상세(정책적 의미·해석 한계·증거 행·원문 열기). 003 인물 관리: 검색·상태 칩·행 테이블·드로어·등록 모달(사진 업로드 5MB 정사각 크롭·익일 반영 고지·verified 선택 비활성). 004 감사: 검토 필요·승인 출처 KPI, 원칙 3카드, 유형 칩, 제안 카드(유형·상태 배지, 근거 링크, 승인·반려 + 사유 prompt). |

---

## 설계 결정 (질문 1~8)

### Q1. 산출물의 행선지

| 소스 산출물 | 행선지 | 비고 |
|---|---|---|
| `data/people_registry.json` | **DB `sv_person`**(+`sv_person_role_history`) | 1회 import(`origin='import'`), 이후 insert-if-absent. 사진은 `photo_path`(볼륨) / `photo_url`(메타, 핫링크 금지) |
| `data/source_registry.json` | **DB `sv_source_domain`** | 동일 |
| `data/audit_proposals.json` | **DB `sv_audit_proposal`** + `sv_audit_proposal_event` | 동일. 부분 유니크 인덱스로 열린 dedupe_key 1건 강제 |
| `source_manifest.json` | **DB `sv_source`(22) · `sv_artifact`(10) · `sv_meta`**(project·scope·withheld_claims·grade 어휘·topic_timeline_update) + 볼륨 원본 복사 | SHA-256·bytes·locator·last_live_check 보존, `integrity_status` 추가 |
| `sources/*.txt`, `sources/raw/*`, `sources/user_input/*` | **볼륨 파일**(`data/stakeholder/sources/...`) | admin 만 전문·원본. 사용자는 locator 창 발췌 |
| 정본 MD `주제별_인물발언_타임라인_정본` | **DB `sv_event`(12)·`sv_statement`·`sv_quote`** + 볼륨 원본 | 결정론 파서(T3). `origin='canonical_md'` + `canonical_sha256` |
| 정본 MD `부서-인물지도`, `Stakeholder_Verbatim_1차`, `보강조사_통합기록`, `README.md`, 정책 2종 | **볼륨 파일** + `sv_artifact` 행 | 인물지도 §2.2 중요도표만 파싱해 `sv_person.importance_score/json` |
| `*.dot/.svg/.png` 관계도 | **볼륨 파일** + `sv_artifact` | SVG 는 인증 라우트로 서빙(타임라인 "관계도" 모달) |
| `monitoring-runs/<d>/daily_monitoring.json/.md` + `raw/` | **DB `sv_monitoring_run`**(payload_json 전체) + 볼륨 raw | 검색 로그·커버리지 매트릭스는 payload 안(별도 테이블 없음) |
| `audit-runs/<d>/coverage_audit.json/.md` + `raw/` | **DB `sv_audit_run`** + 볼륨 raw | `claim`·`why_this_matters`·`run_status` 컬럼 승격(홈 카드) |
| `scripts/verify_repository.py` | **테스트·스크립트로 이식**(T10) | 볼륨 root 에 대해 동일 의미론 |
| `render_relationship_map.py`, `dashboard-sketches/`, `03-Work-Logs/`, `stakeholder_service.py`, `.env.example`, `requirements.txt`, `.gitignore` | **이관 안 함** | 렌더러는 VPS 폰트 절대경로·Pillow 수동 좌표 — 결과물(svg/png)만 보존. 스케치는 React 로 대체. 업무일지는 Obsidian 외부 링크 |

테이블은 `sv_` 접두(`stakeholder verbatim`)·독립 store(`agents/stakeholder/store.py`) — `agents/db/base.py` 의 마이그레이션 사슬에 끼우지 않는다(접촉 0, 병렬 안전). 기존 73 테이블과 이름 충돌 없음.

### Q2. 적재 모델 — (c) 하이브리드, 이유

- 소스 정책은 "발견은 자동화, **검증·승격·정본 변경은 승인 게이트**"(`MONITORING_AUDIT_AGENT_POLICY.md` 안전원칙) 이고 에이전트 금지행위에 "자신의 제안 승인·기록 삭제·공식 직책 덮어쓰기" 가 있다. 따라서 **결정 테이블(사람·출처·제안)의 기록 원본은 대쉬보드 DB** 여야 하고, 에이전트는 제안만 넣는다. (a) 1회 import 만 하면 매일 나오는 런·원문·정본 갱신(2026-10-12 보강조사처럼 MD 와 manifest 가 함께 바뀜)이 끊긴다. (b) 순수 git-sync 는 승인 결정을 JSON 에 되돌려 써야 해 "대쉬보드는 DB 만 읽는다·단방향" 원칙과 충돌하고, 15분 스콥 export 가 프로덕션 스콥을 prune 하던 충돌(메모리 `project_local_launchd_scheduler`)의 재연이다.
- 그래서: **에이전트 산출물(런·원문·정본) = git-sync 읽기 전용**, **결정 = 대쉬보드 DB**, **에이전트가 필요로 하는 스콥(활성 인물·승인 도메인) = API pull**(`GET /api/agent/stakeholder/registry`). 런 JSON 의 `scope.people_registry_sha256` 는 이 응답의 `people_sha256` 를 넣으면 된다(에이전트 측 1줄 변경 — 배포 절의 cutover).
- **덮어쓰기 금지 보장**: sync 는 `sv_person`·`sv_source_domain`·`sv_audit_proposal` 에 `INSERT OR IGNORE` 만 한다(T5 테스트 `test_resync_never_overwrites_operator_decision`). 정본 MD 파싱 결과도 `origin='canonical_md'` 행만 교체하고 그 외 origin 은 건드리지 않는다.
- 단방향: 스크레이퍼(= sync 잡) → DB → 대쉬보드. 대쉬보드 API 는 DB 만 읽고, admin 쓰기도 DB 로만 간다(파일 쓰기는 사진 업로드 하나 — 볼륨 `photos/`).

### Q3. 인증 매핑

| 소스 | 대상 |
|---|---|
| 페이지·목록 GET(무인증) | `require_auth()` — 로그인 user·admin |
| `X-Operator-Key` 운영자 행위(인물 CRUD·상태·사진, 승인·반려) | `require_auth(role="admin")`; `reviewer = request.user["sub"]`(이메일) — 하드코딩 "조셉 (운영자 인증)" 폐기 |
| 제안 POST(무인증, 로컬 루프백) | `require_auth(role="agent")` **`/api/agent/stakeholder/proposals`** — 기존 `agent` 역할의 `/api/agent/*` 한정 그대로. 동일 토큰으로 `GET /api/agent/stakeholder/registry`·`GET /api/agent/stakeholder/proposals?audit_run_id=` 허용(에이전트 QC 의 `proposal_ids_retrieved_from_api`) |
| 사진·관계도 정적 마운트 | 인증 라우트(`/api/stakeholder/people/<id>/photo`, `/api/stakeholder/artifacts/relationship-map.svg`) — 정적 마운트 없음 |

서비스 계정은 기존 VPS 디스패처 계정을 재사용해도 되지만 **감사 에이전트 전용 `agent` 계정을 따로 만들기를 권장**(사용자 결정 §말미).

### Q4. 동시성·무결성

- 쓰기는 모두 `store.transaction(db_path)` 컨텍스트(`BEGIN IMMEDIATE` → yield conn → `COMMIT`/`ROLLBACK`, `busy_timeout` 5s)로. 승인은 제안 상태 전이 + 부수 행(출처 추가 / `role_history` scheduled / 신규 인물) 을 **한 트랜잭션**으로 — 파일 저널·복구 함수가 하는 일이 사라진다.
- 경합: 승인·반려는 `UPDATE sv_audit_proposal SET … WHERE proposal_id=? AND status IN ('needs_review','needs_evidence')` 의 rowcount 가 0 이면 409(동시 승인 두 번째 요청은 실패).
- 불변식은 **순수 함수**로 이식(`agents/stakeholder/approval.py`: `has_evidence`, `normalize_domain`, `validate_proposal_input`, `plan_approval`) + 표 기반 테스트. DB 제약으로 2중화: 열린 dedupe_key 부분 유니크 인덱스, `sv_person(name, organization)` UNIQUE, `sv_source_domain.domain` UNIQUE, 상태 CHECK.
- 스케줄 반영(`scheduled` → `applied`)은 소스에 없던 단계다(소스는 `pending_role_changes` 에 쌓기만 했다). `apply_scheduled(today)` 가 sync 잡 안에서 매일 돈다: `effective_from <= today` 인 role_change 는 현재 직책을 `role_history(applied, effective_to=오늘)` 로 내리고 새 직책 적용 + **`verification_status='verified'`**(승인 자체가 공식 E3+ 근거를 요구하므로 — 정책 감사축2 "공식 인사자료가 직책 승인 최소기준"); new_person/source_expansion 은 행이 이미 있으므로 제안 상태만 `applied`. 실패는 `application_failed` + 이벤트.

### Q5. UI 매핑

| 스케치 | 라우트 | 권한 | 사이드바 |
|---|---|---|---|
| 001 Leadership Timeline | `/stakeholder-signals` | user | Analytics: **Stakeholder Signals**(`ri-user-voice-line`) |
| 002 Analyst Workbench | `/stakeholder-signals/workbench` | user | (같은 항목 — 페이지 상단 뷰 탭 "타임라인 / 워크벤치") |
| 003 People Management | `/admin/stakeholder-people` | admin | Management: **모니터링 인물 관리** |
| 004 Monitoring Audit | `/admin/stakeholder-audit` | admin | Management: **모니터링 감사**(미검토 제안 배지) |
| 관계도(스케치 nav) | 타임라인 페이지의 "관계도" 버튼 → SVG 모달 | user | — |

스타일은 기존 다크 팔레트(`#0A0E1A`/`#161B27`/`#1E2530`/`#00E5CC`/`#8B9BB4`)와 `pages/admin/service-requests/page.tsx` 의 배지·입력 클래스를 재사용한다. 스케치의 CSS·JS 는 **가져오지 않는다**(구조·필드·상호작용만 이식). "+ 행사 업데이트"(001)·"검증 큐"(002) 버튼은 이 단계에서 **구현하지 않는다**(§보류).

### Q6. 에이전트 팀

- 잡: `stakeholder_data_sync`(`_cron(hour="2,8", minute=20)` — 02:20 은 reimb 02:00·policy 02:10 뒤, 08:20 은 VPS 일일 07:00·감사 07:40 커밋 뒤) + CLI `--stakeholder-sync-now`. `JOB_RUNBOOK["stakeholder_data_sync"]="stakeholder_data_sync"`, 런북은 `_from_job` 동기. 토큰 부재(prod) 는 `error_class="config"` → `stakeholder_data_sync:config` 과제 1건.
- 신선도(`DOMAIN_CHECKS` +4): `stakeholder_monitoring_run`(MAX(run_date) ≥ 오늘-2, ops_incident, 런북 sync), `stakeholder_audit_run`(≥ 오늘-3), `stakeholder_proposal_backlog`(7일 넘은 needs_review/needs_evidence > 0, **improvement** `…:backlog` 1행 — 사람 몫), `stakeholder_source_integrity`(`integrity_status IN ('mismatch','missing')` > 0, ops_incident, priority 3). 정상 상태 과제 0건/일 — 일일 예산(≤3) 안.
- 스모크: Screen 4개(`stakeholder-signals`, `stakeholder-signals/workbench`, `admin/stakeholder-people`, `admin/stakeholder-audit`) + `auth-gates` 에 `/api/admin/stakeholder/proposals`→403, `/api/agent/stakeholder/registry`→403(user) 추가. `tests/test_smoke_screens.py:67` 의 22 → 26.
- 규칙 문서 `agents/rules/stakeholder_verbatim_rules.md`(정책 2종 이식 + 소유권·인증·저작권 규칙) + `CLAUDE.md` 규칙 맵 1행. 에이전트 측 계약 `agents/ingest/STAKEHOLDER_SYNC_README.md`.

### Q7. 증거·법적 하이진

- **접근 등급**: user = 인용문(verbatim)·귀속요약·해석·등급·locator·URL·SHA-256·`integrity_status` + **locator 창 발췌 ≤600자**(`GET /sources/<id>/excerpt?locator=`). admin = 추출 전문(`/text`)·원본 다운로드(`/raw`). 원문은 볼륨에만, git 금지, 외부 공개 라우트 없음(모두 `require_auth`). 소스 README 의 "원문 저작권은 각 발행기관·언론사, 내부 검증·provenance 보존 목적" 을 `sv_meta.copyright_notice` 와 UI 각주에 그대로 둔다.
- **provenance 사슬 보존**: `sv_quote.locator` → `sv_statement.source_ids_json` → `sv_source.sha256/text_sha256/url/last_live_check_json` → 볼륨 파일. sync 가 파일 SHA-256 을 재계산해 `integrity_status`(`verified|mismatch|missing|no_local`) 를 쓰고, UI 의 "원문 SHA-256 일치" 배지는 `verified` 일 때만 켠다. 인용문은 파서가 추출 텍스트에 대한 정확 부분문자열 검사(`locator_match`: `range|file|none`) 를 남긴다.
- **개인정보**: 공직자의 공개 직책·공개 발언만. `sv_person` 은 이름·소속·부서·직책·별칭·주제·사진 경로뿐. 삭제 API 없음(소스와 동일 — `paused` 로만). 사진: admin 업로드만, JPG/PNG/WEBP ≤5MB, Pillow 로 EXIF 회전·640×640 crop·WEBP 재인코딩(메타데이터 제거), `data/stakeholder/photos/<person_id>-<hex>.webp`, 인증 라우트 서빙, 교체 시 구 파일 삭제. **언론사 CDN `photo_url` 은 렌더링하지 않는다**(admin 상세에 "출처 사진 링크" 텍스트만).

### Q8. 손실 없음 검증

- `scripts/stakeholder_verify_import.py --root <볼륨 root> --db <db> [--source-dir <SRC>]`: ① `verify_repository.py` 이식(모든 `*.json` 파싱, manifest `local_path/text_path` 존재 + `sha256/text_sha256` 일치, artifacts `sha256/bytes` 일치) ② `--source-dir` 가 있으면 허용목록 파일 전부에 대해 **소스 ↔ 볼륨 SHA-256 동일**(바이트 단위 미러) ③ DB 카운트: `sv_person`=people 수, `sv_source_domain`=sources 수, `sv_audit_proposal` ⊇ proposals id 집합(상태·내용 동일), `sv_source`=manifest sources 수, `sv_artifact`=artifacts 수, `sv_monitoring_run`=런 디렉터리 수, `sv_audit_run`=감사 디렉터리 수, `sv_event`=마스터 표 행 수, **정본 MD 의 모든 `> ` 블록이 `sv_quote` 에 존재**(정규화 비교) ④ 모든 quote 의 `locator_match != 'none'`. 실데이터 기대값(2026-10-12 스냅샷): 11 / 32 / 5 / 22 / 10 / 3 / 2 / 12 / 52블록(유니크 ≤52). T10 이 수행하고 결과표를 커밋 메시지에 남긴다.
- 픽스처(합성)로는 `tests/test_stakeholder_lossless.py` 가 같은 스크립트 함수를 호출해 CI 에서 돈다.

---

## 파일 구조

| 파일 | 책임 | Task |
|---|---|---|
| `agents/stakeholder/__init__.py` (신규) | 패키지 | T1 |
| `agents/stakeholder/paths.py` (신규) | `stakeholder_root()`(`STAKEHOLDER_ROOT` → `BASE_DIR/data/stakeholder`), 하위 디렉터리 상수, 경로 안전 검사 | T1 |
| `agents/stakeholder/store.py` (신규) | `SCHEMA_SQL`(sv_ 14 테이블), `connect`/`transaction`/`ensure_tables`, 인물·출처·제안·런·정본 CRUD, 스콥 스냅샷 sha | T1 |
| `agents/stakeholder/approval.py` (신규) | 불변식 순수 함수 + `approve`/`reject`/`apply_scheduled`/`create_proposal` | T2 |
| `agents/stakeholder/canonical_parse.py` (신규) | 정본 타임라인 MD → events/statements/quotes, 부분문자열 검증, 인물지도 중요도표 | T3 |
| `agents/stakeholder/sync.py` (신규) | GitHub tree 대조 다운로드(허용목록·크기 상한), `source_dir` 모드, `import_snapshot`, 무결성, CLI | T5 |
| `agents/stakeholder/photos.py` (신규) | Pillow 정규화·저장·삭제 | T6 |
| `api/stakeholder_routes.py` (신규) | Blueprint: user GET / admin 쓰기 / agent 2종 / 사진·발췌·원본 / 관계도 | T6 |
| `api/server.py` (수정, 2줄) | `register_blueprint(build_stakeholder_blueprint())` | T6 |
| `scheduler.py` (수정) | `stakeholder_data_sync_job` + `JobSpec` 1행 + CLI 1개 | T8 |
| `agents/agent_tasks/signals.py` (수정, 1행) | `JOB_RUNBOOK["stakeholder_data_sync"]` | T8 |
| `agents/agent_tasks/runbooks.py` (수정) | `_runbook_stakeholder_data_sync` + `RUNBOOKS.update` 1행 | T8 |
| `agents/agent_tasks/freshness.py` (수정) | 체크 4개 + `DOMAIN_CHECKS` 4행 | T8 |
| `scripts/smoke.py` (수정) | Screen 4 + auth-gates 2 | T8 |
| `scripts/stakeholder_verify_import.py` (신규) | 손실 없음 검증 CLI | T10 |
| `frontend/src/api/stakeholder.ts` (신규) | API 모듈·타입 | T7a |
| `frontend/src/pages/stakeholder-signals/page.tsx`, `workbench.tsx`, `components/` (신규) | 001·002 | T7a |
| `frontend/src/pages/admin/stakeholder-people/page.tsx`, `admin/stakeholder-audit/page.tsx` (신규) | 003·004 | T7b |
| `frontend/src/router/config.tsx`, `components/feature/Sidebar.tsx` (수정) | 라우트 4 + nav 3 | T7a(user 2) · T7b(admin 2) |
| `agents/rules/stakeholder_verbatim_rules.md` (신규), `CLAUDE.md` (1행) | 규칙 | T4 |
| `agents/ingest/STAKEHOLDER_SYNC_README.md` (신규) | 외부 에이전트 계약 | T9 |
| `docs/agent_team/STAKEHOLDER_RUNBOOK.md` (신규), `docs/api_contract.md` (추가) | 운영 런북·API 계약 | T9 |
| `requirements.txt` (+`Pillow>=10,<13`), `.gitignore` (+`data/stakeholder/`), `scripts/start_production.sh` (mkdir) | 환경 | T4 |
| `tests/fixtures/stakeholder_src_mini/` (신규, 합성) | 소스 repo 축소 픽스처 | T4 |
| `tests/test_stakeholder_store.py`, `_approval.py`, `_canonical_parse.py`, `_sync.py`, `_api.py`, `_jobs.py`, `_lossless.py` (신규) | 테스트 | T1·T2·T3·T5·T6·T8·T10 |
| `tests/test_smoke_screens.py:67` (수정) | 22 → 26 | T8 |

### Task 경계 (병렬 worktree 용)

| Task | 모델 | 소유 파일 | 공유 파일 접촉 | 선행 |
|---|---|---|---|---|
| T1 store | Opus | `agents/stakeholder/{__init__,paths,store}.py`, `tests/test_stakeholder_store.py` | 없음 | 없음 |
| T2 approval | Opus | `agents/stakeholder/approval.py`, `tests/test_stakeholder_approval.py` | 없음(T1 의 `store.transaction`/`connect` 만 import) | T1 |
| T3 parser | Opus | `agents/stakeholder/canonical_parse.py`, `tests/test_stakeholder_canonical_parse.py` | 없음 | 없음 (T4 픽스처 사용 — T4 와 동시 진행 시 자체 임시 MD 로 테스트) |
| T4 환경·규칙·픽스처 | Sonnet | `tests/fixtures/stakeholder_src_mini/**`, `agents/rules/stakeholder_verbatim_rules.md` | `CLAUDE.md`(1행), `requirements.txt`(1행), `.gitignore`(1행), `scripts/start_production.sh`(mkdir 1곳) | 없음 |
| T5 sync·import | Opus | `agents/stakeholder/sync.py`, `tests/test_stakeholder_sync.py` | 없음 | T1·T2·T3·T4 |
| T6 API | Opus | `api/stakeholder_routes.py`, `agents/stakeholder/photos.py`, `tests/test_stakeholder_api.py` | `api/server.py`(blueprint 등록 2줄) | T1·T2 |
| T7a 프론트 user | Opus | `frontend/src/api/stakeholder.ts`, `pages/stakeholder-signals/**` | `router/config.tsx`(2행), `Sidebar.tsx`(allNavItems 1행) | 없음(§API 계약으로 구현, T6 와 병렬) |
| T7b 프론트 admin | Opus | `pages/admin/stakeholder-people/**`, `pages/admin/stakeholder-audit/**` | `router/config.tsx`(2행), `Sidebar.tsx`(adminNavItems 2행 + 배지) | T7a 의 `api/stakeholder.ts`(타입 공유 — T7a 가 먼저 API 모듈을 커밋하거나 T7b 가 같은 파일에 추가 후 머지) |
| T8 잡·신선도·스모크 | Opus | `tests/test_stakeholder_jobs.py` | `scheduler.py`, `signals.py`, `runbooks.py`, `freshness.py`, `scripts/smoke.py`, `tests/test_smoke_screens.py` | T5·T6 |
| T9 문서 | Sonnet | `agents/ingest/STAKEHOLDER_SYNC_README.md`, `docs/agent_team/STAKEHOLDER_RUNBOOK.md` | `docs/api_contract.md`(섹션 추가) | T6·T8 |
| T10 검증 | Opus | `scripts/stakeholder_verify_import.py`, `tests/test_stakeholder_lossless.py` | 없음 | T5 |

**병렬 묶음:** G1 {T1, T3, T4} → G2 {T2, T7a} → G3 {T5, T6, T7b} → G4 {T8, T10} → G5 {T9} → 오케스트레이터 최종 검증 → 배포(사용자). T7a/T7b 는 API 계약(§T6 표)만 보고 만들고, T6 머지 뒤 `npm run build` + 로컬 서버로 맞춘다.

---

## 공통 상수·계약 (모든 Task 가 참조)

```python
# agents/stakeholder/paths.py
BASE_DIR = Path(__file__).resolve().parents[2]
def stakeholder_root() -> Path:
    return Path(os.environ.get("STAKEHOLDER_ROOT") or BASE_DIR / "data" / "stakeholder")
PHOTOS_SUBDIR = "photos"; SYNC_SUBDIR = ".sync"
# 소스 repo 상대경로를 그대로 쓴다: sources/, monitoring-runs/, audit-runs/, data/, scripts/, 루트 md/json/dot/svg/png
SYNC_ALLOW_PREFIXES = ("sources/", "monitoring-runs/", "audit-runs/", "data/", "scripts/")
SYNC_ALLOW_ROOT_SUFFIXES = (".md", ".json", ".dot", ".svg", ".png", "render_relationship_map.py")
SYNC_DENY_PREFIXES = ("dashboard-sketches/", "03-Work-Logs/", ".")   # 숨김·환경 파일 제외
MAX_BLOB_BYTES = 20 * 1024 * 1024
SAFE_PATH = re.compile(r"^[A-Za-z0-9가-힣._ /()+-]+$")   # 한글 파일명(person_00_강준혁_bing.xml) 허용, '..'·절대경로 거부
```

- 시간: `KST = timezone(timedelta(hours=9))`, `today_kst()`, `tomorrow_kst()` 는 `store.py` 에 두고 모두 거기서 import.
- 증거등급 어휘: `("A","B-quote","B-summary","C-quote","C-summary","Role-only","Candidate-only")` — `sv_statement.evidence_grade` 는 슬래시 복합(`C-quote/C-summary`)을 허용하므로 CHECK 는 걸지 않고 파서가 어휘 밖 토큰을 경고한다.
- 주제 slug: `price_reform`(①), `access`(②), `rwe`(③), `finance`(④), `negotiation`(⑤) — 파서가 `# N. 주제 ①…` 순서로 매핑, UI 탭 라벨은 MD 제목.

---

### Task 1: Store — `sv_*` 스키마 + 트랜잭션 + CRUD **[Opus]**

**Files:**
- Create: `agents/stakeholder/__init__.py`, `agents/stakeholder/paths.py`, `agents/stakeholder/store.py`, `tests/test_stakeholder_store.py`

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_stakeholder_store.py
from __future__ import annotations
import json, sqlite3
from datetime import date
import pytest
from agents.stakeholder import store

def _db(tmp_path):
    return tmp_path / "s.db"

def test_ensure_tables_is_idempotent_and_lazy(tmp_path):
    db = _db(tmp_path)
    store.ensure_tables(db); store.ensure_tables(db)
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'sv_%'")}
    assert {"sv_person", "sv_person_role_history", "sv_source_domain", "sv_source", "sv_artifact", "sv_event",
            "sv_statement", "sv_quote", "sv_monitoring_run", "sv_audit_run", "sv_audit_proposal",
            "sv_audit_proposal_event", "sv_meta", "sv_sync_file"} <= names

def test_person_upsert_insert_only_and_unique_name_org(tmp_path):
    db = _db(tmp_path)
    p = {"person_id": "P-1", "name": "강준혁", "organization": "보건복지부", "organization_code": "MOHW",
         "department": "보험약제과", "role": "과장", "status": "active", "verification_status": "verified",
         "effective_from": "2026-10-09", "aliases": [], "topic_tags": ["약가"], "photo_url": None, "photo_path": None,
         "created_at": "2026-10-08T00:00:00+09:00", "updated_at": "2026-10-08T00:00:00+09:00"}
    assert store.insert_person_if_absent(p, origin="import", db_path=db) is True
    p2 = dict(p, role="국장")
    assert store.insert_person_if_absent(p2, origin="import", db_path=db) is False      # 기존 행 보존
    assert store.get_person("P-1", db_path=db)["role"] == "과장"
    with pytest.raises(store.ConflictError):
        store.create_person({**p, "person_id": "P-2"}, actor="a@x", db_path=db)        # 같은 기관 동명

def test_create_person_sets_candidate_and_tomorrow(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    row = store.create_person({"name": "홍길동", "organization": "보건복지부", "department": "보험약제과", "role": "사무관",
                               "status": "active", "verification_status": "verified", "aliases": [], "topic_tags": []},
                              actor="admin@x", db_path=db)
    assert row["person_id"].startswith("P-USER-") and row["verification_status"] == "candidate"
    assert row["effective_from"] == "2026-10-13" and row["origin"] == "dashboard"

def test_update_verified_person_org_role_is_409(tmp_path):
    db = _db(tmp_path)
    store.insert_person_if_absent({"person_id": "P-1", "name": "a", "organization": "o", "department": "d", "role": "r",
                                   "status": "active", "verification_status": "verified", "effective_from": "2026-01-01",
                                   "aliases": [], "topic_tags": [], "photo_url": None, "photo_path": None,
                                   "created_at": "t", "updated_at": "t"}, origin="import", db_path=db)
    with pytest.raises(store.ConflictError):
        store.update_person("P-1", {"name": "a", "organization": "o", "department": "d", "role": "r2", "status": "active",
                                    "aliases": [], "topic_tags": []}, actor="admin@x", db_path=db)
    row = store.update_person("P-1", {"name": "a", "organization": "o", "department": "d", "role": "r", "status": "paused",
                                      "aliases": ["A"], "topic_tags": []}, actor="admin@x", db_path=db)
    assert row["status"] == "paused" and row["verification_status"] == "verified"

def test_registry_snapshot_sha_is_stable(tmp_path):
    db = _db(tmp_path)
    snap1 = store.registry_snapshot(today=date(2026, 10, 12), db_path=db)
    snap2 = store.registry_snapshot(today=date(2026, 10, 12), db_path=db)
    assert snap1["people_sha256"] == snap2["people_sha256"] and snap1["people"] == []

def test_transaction_rolls_back_on_error(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(RuntimeError):
        with store.transaction(db) as conn:
            conn.execute("INSERT INTO sv_meta (key, value, updated_at) VALUES ('k','v','t')")
            raise RuntimeError("boom")
    assert store.get_meta("k", db_path=db) is None

def test_proposal_open_dedupe_enforced_by_index(tmp_path):
    db = _db(tmp_path)
    base = {"proposal_id": "AUD-1", "proposal_type": "new_person", "dedupe_key": "new_person:x:y:z", "priority": "high",
            "summary": "s", "rationale": "r", "proposed_change": {}, "evidence": [], "conflicts": [], "audit_run_id": None,
            "created_by": "auditor", "status": "needs_review", "effective_from": None, "reviewer": None, "reviewed_at": None,
            "decision_reason": None, "created_at": "t", "updated_at": "t"}
    assert store.insert_proposal_if_absent(base, origin="import", db_path=db)
    with pytest.raises(store.ConflictError):
        store.insert_proposal_if_absent({**base, "proposal_id": "AUD-2"}, origin="agent_api", db_path=db)
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python -m pytest tests/test_stakeholder_store.py -q` → `ModuleNotFoundError`

- [ ] **Step 3: 구현 — `store.py`** (핵심 부분; 나머지 CRUD 는 같은 패턴)

```python
# agents/stakeholder/store.py
"""Stakeholder & Verbatim Intelligence 저장소 — sv_* 테이블 (독립 store, agents/db/base.py 무접촉).

소유권: sv_person / sv_person_role_history / sv_source_domain / sv_audit_proposal(+event) 는 **대쉬보드가 기록 원본**.
sync(agents/stakeholder/sync.py) 는 이 네 테이블에 insert-if-absent 만 한다. 나머지(sv_source·sv_artifact·sv_event·
sv_statement·sv_quote·sv_monitoring_run·sv_audit_run)는 외부 에이전트 산출물의 미러(origin 으로 구분).
쓰기는 transaction() 안에서만 — BEGIN IMMEDIATE 가 소스의 파일 잠금+저널을 대체한다.
"""
from __future__ import annotations
import hashlib, json, re, sqlite3, uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "db" / "drug_prices.db"
BUSY_TIMEOUT_SECONDS = 5
KST = timezone(timedelta(hours=9))

PERSON_STATUSES = ("active", "paused")
VERIFICATION_STATUSES = ("verified", "candidate")
SOURCE_GROUPS = ("official", "top_tier_general", "healthcare_trade", "discovered_additional")
PROPOSAL_TYPES = ("source_expansion", "role_change", "new_person")
PRIORITIES = ("urgent", "high", "normal", "low")
PROPOSAL_STATUSES = ("needs_review", "needs_evidence", "approved", "scheduled", "applied", "rejected", "superseded",
                     "application_failed")
OPEN_PROPOSAL_STATUSES = ("needs_review", "needs_evidence")
ORIGINS = ("import", "dashboard", "audit", "agent_api", "canonical_md")
EVIDENCE_GRADES = ("A", "B-quote", "B-summary", "C-quote", "C-summary", "Role-only", "Candidate-only")

class ConflictError(Exception):
    """409 — 중복·상태 불일치."""
class NotFoundError(Exception):
    """404."""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sv_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sv_person (
    person_id            TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    organization         TEXT NOT NULL,
    organization_code    TEXT,
    department           TEXT NOT NULL,
    role                 TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('active','paused')),
    verification_status  TEXT NOT NULL CHECK (verification_status IN ('verified','candidate')),
    effective_from       TEXT NOT NULL,
    aliases_json         TEXT NOT NULL DEFAULT '[]',
    topic_tags_json      TEXT NOT NULL DEFAULT '[]',
    photo_url            TEXT,            -- 소스 메타(언론 CDN). UI 는 렌더링하지 않는다
    photo_path           TEXT,            -- 볼륨 photos/<file>.webp (admin 업로드)
    importance_score     INTEGER,         -- 부서-인물지도 §2.2 중요도(1~5)
    importance_json      TEXT,            -- 구성점수 원본
    discovered_from_proposal TEXT,
    discovery_evidence_json  TEXT,
    origin               TEXT NOT NULL CHECK (origin IN ('import','dashboard','audit')),
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    UNIQUE (name, organization)
);
CREATE TABLE IF NOT EXISTS sv_person_role_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id      TEXT NOT NULL,
    organization   TEXT, department TEXT, role TEXT,
    effective_from TEXT, effective_to TEXT,
    status         TEXT NOT NULL CHECK (status IN ('scheduled','applied','superseded')),
    proposal_id    TEXT, evidence_json TEXT, approved_at TEXT, applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sv_role_hist_person ON sv_person_role_history(person_id, status);

CREATE TABLE IF NOT EXISTS sv_source_domain (
    source_id      TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    domain         TEXT NOT NULL UNIQUE,
    source_group   TEXT NOT NULL CHECK (source_group IN ('official','top_tier_general','healthcare_trade','discovered_additional')),
    status         TEXT NOT NULL CHECK (status IN ('approved','retired')),
    effective_from TEXT NOT NULL,
    approved_from_proposal TEXT, evidence_json TEXT,
    origin         TEXT NOT NULL CHECK (origin IN ('import','dashboard','audit')),
    created_at     TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sv_source (                 -- source_manifest.json .sources[] 미러
    source_id        TEXT PRIMARY KEY,
    title TEXT, url TEXT, source_type TEXT, evidence_grade TEXT, access_status TEXT,
    local_path TEXT, sha256 TEXT, text_path TEXT, text_sha256 TEXT, bytes INTEGER,
    event_date TEXT, publication_date TEXT, publication_date_basis TEXT,
    locator TEXT, limitation TEXT, scope_status TEXT, scope_note TEXT,
    evidence_facets_json TEXT, official_video_json TEXT, last_live_check_json TEXT, known_timestamp TEXT,
    integrity_status TEXT NOT NULL DEFAULT 'unchecked'
        CHECK (integrity_status IN ('verified','mismatch','missing','no_local','unchecked')),
    integrity_checked_at TEXT, manifest_sha256 TEXT, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sv_artifact (               -- source_manifest.json .artifacts[] 미러
    path TEXT PRIMARY KEY, name TEXT, description TEXT, sha256 TEXT, bytes INTEGER,
    integrity_status TEXT NOT NULL DEFAULT 'unchecked', integrity_checked_at TEXT, imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sv_event (
    event_id TEXT PRIMARY KEY, event_date TEXT NOT NULL, title TEXT NOT NULL,
    context TEXT, publication_dates_json TEXT, topics_json TEXT, speakers_json TEXT, source_ids_json TEXT,
    grades_json TEXT, origin TEXT NOT NULL DEFAULT 'canonical_md', canonical_sha256 TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sv_statement (              -- (행사 × 발언자 × 주제) 블록: 요약·해석·한계·등급·근거
    statement_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, topic TEXT NOT NULL,
    speaker_name TEXT NOT NULL, speaker_role_at_event TEXT, person_id TEXT, multi_speaker INTEGER NOT NULL DEFAULT 0,
    summary_text TEXT, analysis_text TEXT, limitation_text TEXT, policy_status TEXT,
    evidence_grade TEXT NOT NULL, locators_json TEXT, source_ids_json TEXT, source_url TEXT, role_check_url TEXT,
    publication_date TEXT, headline_quote_id TEXT,
    origin TEXT NOT NULL DEFAULT 'canonical_md', canonical_sha256 TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sv_statement_event ON sv_statement(event_id, topic);
CREATE INDEX IF NOT EXISTS idx_sv_statement_person ON sv_statement(person_id);
CREATE TABLE IF NOT EXISTS sv_quote (                  -- 직접인용 1건 = 1행. 원문 그대로. (행사, 발언자, 정규화 문구) 유일
    quote_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, speaker_name TEXT NOT NULL, person_id TEXT,
    quote_text TEXT NOT NULL, quote_norm TEXT NOT NULL, statement_ids_json TEXT NOT NULL, topics_json TEXT NOT NULL,
    source_id TEXT, locator TEXT, locator_match TEXT NOT NULL DEFAULT 'unchecked'
        CHECK (locator_match IN ('range','file','none','unchecked')),
    evidence_grade TEXT, origin TEXT NOT NULL DEFAULT 'canonical_md', canonical_sha256 TEXT, created_at TEXT NOT NULL,
    UNIQUE (event_id, speaker_name, quote_norm)
);

CREATE TABLE IF NOT EXISTS sv_monitoring_run (
    run_id TEXT PRIMARY KEY, run_date TEXT NOT NULL, executed_at TEXT, window_start TEXT, window_end TEXT,
    run_status TEXT, scope_snapshot_id TEXT, people_registry_sha256 TEXT, source_registry_sha256 TEXT,
    active_people INTEGER, approved_domains INTEGER, new_quotes INTEGER, saturation_status TEXT,
    payload_json TEXT NOT NULL, md_path TEXT, raw_dir TEXT, payload_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sv_audit_run (
    audit_run_id TEXT PRIMARY KEY, run_date TEXT NOT NULL, executed_at TEXT, completed_at TEXT, run_status TEXT,
    claim TEXT, why_this_matters TEXT, proposals_created INTEGER,
    payload_json TEXT NOT NULL, md_path TEXT, raw_dir TEXT, payload_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sv_audit_proposal (
    proposal_id TEXT PRIMARY KEY,
    proposal_type TEXT NOT NULL CHECK (proposal_type IN ('source_expansion','role_change','new_person')),
    dedupe_key TEXT NOT NULL,
    priority TEXT NOT NULL CHECK (priority IN ('urgent','high','normal','low')),
    summary TEXT NOT NULL, rationale TEXT NOT NULL,
    proposed_change_json TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '[]', conflicts_json TEXT NOT NULL DEFAULT '[]',
    audit_run_id TEXT, created_by TEXT,
    status TEXT NOT NULL CHECK (status IN ('needs_review','needs_evidence','approved','scheduled','applied','rejected',
                                           'superseded','application_failed')),
    effective_from TEXT, reviewer TEXT, reviewed_at TEXT, decision_reason TEXT, applied_at TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('import','agent_api','dashboard')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- 열린 제안은 dedupe_key 당 1건 (소스 create_audit_proposal 의 409 규칙을 DB 가 강제)
CREATE UNIQUE INDEX IF NOT EXISTS idx_sv_proposal_open_dedupe ON sv_audit_proposal(dedupe_key)
    WHERE status IN ('needs_review','needs_evidence','scheduled');
CREATE TABLE IF NOT EXISTS sv_audit_proposal_event (   -- append-only 감사 기록 (service_request_event 선례)
    id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL,
    detail TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sv_sync_file (              -- 볼륨 미러 색인: 경로 → git blob sha / sha256
    path TEXT PRIMARY KEY, blob_sha TEXT NOT NULL, sha256 TEXT, bytes INTEGER, synced_at TEXT NOT NULL
);
"""

def now_kst() -> datetime: return datetime.now(KST)
def today_kst() -> date: return now_kst().date()
def tomorrow_kst() -> str: return (today_kst() + timedelta(days=1)).isoformat()
def _ts() -> str: return now_kst().isoformat(timespec="seconds")

def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)   # autocommit; 트랜잭션은 명시
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SECONDS * 1000}")
    return conn

def ensure_tables(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    conn = connect(db_path)
    try: conn.executescript(SCHEMA_SQL)
    finally: conn.close()

@contextmanager
def transaction(db_path: str | Path = DEFAULT_DB_PATH):
    """BEGIN IMMEDIATE … COMMIT. 예외면 ROLLBACK. 소스의 flock+저널+복구를 대체한다."""
    ensure_tables(db_path)
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

def _j(v) -> str: return json.dumps(v, ensure_ascii=False)
def _jl(s) -> list:
    try: return json.loads(s or "[]")
    except Exception: return []

def person_row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["aliases"] = _jl(d.pop("aliases_json")); d["topic_tags"] = _jl(d.pop("topic_tags_json"))
    d["discovery_evidence"] = _jl(d.pop("discovery_evidence_json")) if d.get("discovery_evidence_json") else None
    d["importance"] = json.loads(d["importance_json"]) if d.get("importance_json") else None; d.pop("importance_json", None)
    return d

def insert_person_if_absent(p: dict, *, origin: str, db_path=DEFAULT_DB_PATH) -> bool:
    """sync/import 전용 — 기존 person_id 는 절대 갱신하지 않는다. 반환: 삽입 여부."""
    with transaction(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO sv_person (person_id,name,organization,organization_code,department,role,status,"
            "verification_status,effective_from,aliases_json,topic_tags_json,photo_url,photo_path,discovered_from_proposal,"
            "discovery_evidence_json,origin,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (p["person_id"], p["name"], p["organization"], p.get("organization_code"), p["department"], p["role"],
             p["status"], p["verification_status"], p["effective_from"], _j(p.get("aliases") or []),
             _j(p.get("topic_tags") or []), p.get("photo_url"), p.get("photo_path"), p.get("discovered_from_proposal"),
             _j(p["discovery_evidence"]) if p.get("discovery_evidence") else None, origin,
             p.get("created_at") or _ts(), p.get("updated_at") or _ts()))
        return cur.rowcount == 1
```

`create_person`(dashboard: `P-USER-<12hex>`, `verification_status` 강제 `candidate`, `effective_from=tomorrow_kst()`, 같은 기관 동명 → `ConflictError`), `update_person`(검증 인물의 org/dept/role 변경 → `ConflictError`; `verification_status` 는 입력 무시·보존; `effective_from=tomorrow`), `set_person_status`, `set_person_photo`, `get_person`, `list_people(status=None, q=None)`(정렬: active 우선 → organization → name), `insert_source_domain_if_absent`, `list_source_domains`, `insert_proposal_if_absent`(부분 유니크 위반 `sqlite3.IntegrityError` → `ConflictError`), `get_proposal`, `list_proposals(status=None, type=None)`(정렬: 열린 것 우선 → priority 순 → created_at), `add_proposal_event`, `upsert_monitoring_run`/`upsert_audit_run`(payload sha 같으면 skip, 반환 `inserted|updated|unchanged`), `list_runs(kind, limit)`, `upsert_source`/`upsert_artifact`, `set_meta`/`get_meta`, `replace_canonical(events, statements, quotes, sha, conn)`(origin='canonical_md' 삭제 후 삽입 — **호출자 트랜잭션 안에서**), `list_statements(filters)`, `get_statement`, `registry_snapshot(today)`:

```python
def registry_snapshot(*, today: date, db_path=DEFAULT_DB_PATH) -> dict:
    """에이전트가 pull 하는 모니터링 스콥 — 소스 run JSON 의 scope.people_registry_sha256 / source_registry_sha256 와 같은 자리.
    sha 는 정렬된 공개 필드만의 JSON 으로 계산한다(사진 경로·사용자 메타 제외) — 같은 스콥이면 같은 해시."""
    ensure_tables(db_path)
    conn = connect(db_path)
    try:
        t = today.isoformat()
        people = [person_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM sv_person WHERE status='active' AND effective_from<=? ORDER BY organization, name", (t,))]
        sources = [dict(r) for r in conn.execute(
            "SELECT source_id,name,domain,source_group,status,effective_from FROM sv_source_domain "
            "WHERE status='approved' AND effective_from<=? ORDER BY source_group, name", (t,))]
    finally:
        conn.close()
    pub = [{k: p[k] for k in ("person_id","name","organization","organization_code","department","role","status",
                              "verification_status","effective_from","aliases","topic_tags")} for p in people]
    sha = lambda o: hashlib.sha256(json.dumps(o, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"as_of": t, "people": pub, "sources": sources, "people_sha256": sha(pub), "sources_sha256": sha(sources)}
```

- [ ] **Step 4: 통과 확인** — `.venv/bin/python -m pytest tests/test_stakeholder_store.py -q` → 7 passed
- [ ] **Step 5: 커밋** — `feat(stakeholder): sv_* 스키마·트랜잭션·레지스트리 store (대쉬보드 소유 테이블 insert-if-absent 규약)`

**완료 기준:** 7 테스트 통과; 모듈 import 가 실제 DB 를 열지 않는다(`python -c "import agents.stakeholder.store"` 가 `data/db/drug_prices.db-wal` 을 만들지 않음 — `ls data/db` 전후 비교); `store.py` 의 공개 함수에 모두 `db_path=` 키워드가 있다.

---

### Task 2: 승인 불변식 + 승인·반려·스케줄 반영 **[Opus]**

**Files:**
- Create: `agents/stakeholder/approval.py`, `tests/test_stakeholder_approval.py`

규약: 불변식은 **순수 함수**(DB 없이 표 기반 테스트), 실제 쓰기는 `store.transaction` 안에서 한 번에. 오류는 `ApprovalError(status, code, message)` — 라우트가 그대로 HTTP 로 옮긴다. 메시지는 소스 한국어 문구를 유지한다(에이전트의 `proposal_post_responses.json` 가 422 문구를 기록한다).

- [ ] **Step 1: 실패 테스트** (표 기반 — 소스 `stakeholder_service.py` 의 모든 분기)

```python
# tests/test_stakeholder_approval.py
from __future__ import annotations
from datetime import date
import pytest
from agents.stakeholder import approval as ap, store

E = lambda **kw: {"url": "https://www.hira.or.kr/x", "publisher": "심평원", "source_class": "official", "evidence_level": "E3",
                  "locator": "본문 1문단", **kw}

@pytest.mark.parametrize("evidence,levels,official,expected", [
    ([E()], {"E3", "E4"}, True, True),
    ([E(evidence_level="E2")], {"E3", "E4"}, True, False),
    ([E(source_class="healthcare_trade")], {"E3", "E4"}, True, False),      # official 요구
    ([E(locator="")], {"E3", "E4"}, False, False),                          # locator 필수
    ([E(publisher="")], {"E3", "E4"}, False, False),                        # publisher 필수
    ([E(evidence_level="E2", source_class="healthcare_trade")], {"E2", "E3", "E4"}, False, True),
])
def test_has_evidence(evidence, levels, official, expected):
    assert ap.has_evidence({"evidence": evidence}, levels, official=official) is expected

@pytest.mark.parametrize("raw,expected", [("NewsPrime.co.kr.", "newsprime.co.kr"), ("https://www.hira.or.kr/", "www.hira.or.kr")])
def test_normalize_domain_ok(raw, expected):
    assert ap.normalize_domain(raw) == expected

@pytest.mark.parametrize("raw", ["hira.or.kr/path", "hira.or.kr?x=1", "", "https://a.b/#f"])
def test_normalize_domain_rejects(raw):
    with pytest.raises(ap.ApprovalError) as ei: ap.normalize_domain(raw)
    assert ei.value.status == 422

def test_validate_proposal_input_mirrors_pydantic():
    ok = ap.validate_proposal_input({"proposal_type": "new_person", "dedupe_key": "new_person:a:b:c", "summary": "요약",
                                     "rationale": "근거", "proposed_change": {"name": "a"}, "evidence": [E()]})
    assert ok["priority"] == "normal" and ok["created_by"] == "Monitoring Coverage Auditor" and ok["conflicts"] == []
    for bad in [{"proposal_type": "merge"}, {"priority": "medium"}, {"dedupe_key": "ab"}, {"summary": "x"},
                {"evidence": [{"url": "ftp://x"}]}]:
        with pytest.raises(ap.ApprovalError) as ei:
            ap.validate_proposal_input({"proposal_type": "new_person", "dedupe_key": "new_person:a:b:c", "summary": "요약",
                                        "rationale": "근거", "proposed_change": {}, **bad})
        assert ei.value.status == 422

def _seed(db):
    store.insert_person_if_absent({"person_id": "P-HIRA-KIM", "name": "김국희", "organization": "건강보험심사평가원",
        "organization_code": "HIRA", "department": "희귀·중증질환성과평가실", "role": "실장 후보", "status": "active",
        "verification_status": "candidate", "effective_from": "2026-10-09", "aliases": [], "topic_tags": [],
        "photo_url": None, "photo_path": None, "created_at": "t", "updated_at": "t"}, origin="import", db_path=db)
    store.insert_source_domain_if_absent({"source_id": "SRC-HIRA", "name": "심평원", "domain": "hira.or.kr",
        "source_group": "official", "status": "approved", "effective_from": "2026-10-09"}, origin="import", db_path=db)

def _proposal(db, ptype, change, evidence, conflicts=(), pid="AUD-1"):
    store.insert_proposal_if_absent({"proposal_id": pid, "proposal_type": ptype, "dedupe_key": f"{ptype}:{pid}", "priority": "high",
        "summary": "s", "rationale": "r", "proposed_change": change, "evidence": evidence, "conflicts": list(conflicts),
        "audit_run_id": "coverage-audit-2026-10-10", "created_by": "auditor", "status": "needs_review", "effective_from": None,
        "reviewer": None, "reviewed_at": None, "decision_reason": None, "created_at": "t", "updated_at": "t"},
        origin="import", db_path=db)

def test_role_change_requires_official_e3_and_schedules(tmp_path, monkeypatch):
    db = tmp_path / "a.db"; _seed(db)
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E(evidence_level="E2")])
    with pytest.raises(ap.ApprovalError) as ei: ap.approve("AUD-1", reviewer="admin@x", reason="ok", db_path=db)
    assert ei.value.status == 422 and "공식 E3" in ei.value.message
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E()], pid="AUD-2")
    out = ap.approve("AUD-2", reviewer="admin@x", reason="공식 인사 확인", db_path=db)
    assert out["status"] == "scheduled" and out["effective_from"] == "2026-10-13" and out["reviewer"] == "admin@x"
    hist = store.list_role_history("P-HIRA-KIM", db_path=db)
    assert len(hist) == 1 and hist[0]["status"] == "scheduled" and hist[0]["role"] == "실장"
    assert store.get_person("P-HIRA-KIM", db_path=db)["role"] == "실장 후보"          # 익일 전 미반영
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장2"}, [E()], pid="AUD-3")
    with pytest.raises(ap.ApprovalError) as ei: ap.approve("AUD-3", reviewer="admin@x", reason="x", db_path=db)
    assert ei.value.status == 409                                                     # 적용 대기 중 변경 존재

def test_apply_scheduled_moves_role_to_history_and_verifies(tmp_path, monkeypatch):
    db = tmp_path / "a.db"; _seed(db)
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    _proposal(db, "role_change", {"person_id": "P-HIRA-KIM", "role": "실장"}, [E()])
    ap.approve("AUD-1", reviewer="admin@x", reason="ok", db_path=db)
    assert ap.apply_scheduled(today=date(2026, 10, 12), db_path=db)["applied"] == 0
    res = ap.apply_scheduled(today=date(2026, 10, 13), db_path=db)
    assert res["applied"] == 1
    p = store.get_person("P-HIRA-KIM", db_path=db)
    assert p["role"] == "실장" and p["verification_status"] == "verified" and p["effective_from"] == "2026-10-13"
    hist = store.list_role_history("P-HIRA-KIM", db_path=db)
    assert {h["status"] for h in hist} == {"applied", "superseded"}                # 새 직책 applied + 옛 직책 superseded(effective_to)
    assert store.get_proposal("AUD-1", db_path=db)["status"] == "applied"

def test_source_expansion_rules(tmp_path, monkeypatch):
    db = tmp_path / "a.db"; _seed(db)
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    ev = [E(url="https://www.newsprime.co.kr/news/1", publisher="프라임경제", source_class="off_list_media", evidence_level="E2")]
    _proposal(db, "source_expansion", {"name": "프라임경제", "domain": "newsprime.co.kr", "source_group": "official"}, ev)
    with pytest.raises(ap.ApprovalError) as ei: ap.approve("AUD-1", reviewer="a", reason="r", db_path=db)
    assert ei.value.status == 422                                                      # official 은 E3+official 필요
    _proposal(db, "source_expansion", {"name": "심평원", "domain": "hira.or.kr", "source_group": "healthcare_trade"}, [E()], pid="AUD-2")
    with pytest.raises(ap.ApprovalError) as ei: ap.approve("AUD-2", reviewer="a", reason="r", db_path=db)
    assert ei.value.status == 409                                                      # 이미 있는 도메인
    _proposal(db, "source_expansion", {"name": "프라임경제", "domain": "newsprime.co.kr", "source_group": "top_tier_general"},
              [E(url="https://www.other.com/1", evidence_level="E2", source_class="x")], pid="AUD-3")
    with pytest.raises(ap.ApprovalError) as ei: ap.approve("AUD-3", reviewer="a", reason="r", db_path=db)
    assert ei.value.status == 422 and "일치하는 근거 URL" in ei.value.message
    _proposal(db, "source_expansion", {"name": "프라임경제", "domain": "newsprime.co.kr", "source_group": "top_tier_general"}, ev, pid="AUD-4")
    out = ap.approve("AUD-4", reviewer="a", reason="r", db_path=db)
    dom = [s for s in store.list_source_domains(db_path=db) if s["domain"] == "newsprime.co.kr"][0]
    assert dom["status"] == "approved" and dom["effective_from"] == "2026-10-13" and dom["approved_from_proposal"] == "AUD-4"
    assert dom["source_id"].startswith("SRC-AUD-") and out["status"] == "scheduled"

def test_new_person_rules_and_conflicts_block(tmp_path, monkeypatch):
    db = tmp_path / "a.db"; _seed(db)
    monkeypatch.setattr(store, "today_kst", lambda: date(2026, 10, 12))
    change = {"name": "이소희", "organization": "대한민국 국회", "department": "보건복지위원회", "role": "제22대 국회의원(위원)"}
    _proposal(db, "new_person", change, [E(evidence_level="E2")], conflicts=["미해결"])
    with pytest.raises(ap.ApprovalError) as ei: ap.approve("AUD-1", reviewer="a", reason="r", db_path=db)
    assert ei.value.status == 422 and "충돌" in ei.value.message
    _proposal(db, "new_person", change, [E(evidence_level="E2")], pid="AUD-2")
    ap.approve("AUD-2", reviewer="a", reason="r", db_path=db)
    p = [x for x in store.list_people(db_path=db) if x["name"] == "이소희"][0]
    assert p["verification_status"] == "candidate" and p["origin"] == "audit" and p["person_id"].startswith("P-AUD-")
    assert p["discovered_from_proposal"] == "AUD-2" and p["effective_from"] == "2026-10-13"
    _proposal(db, "new_person", change, [E(evidence_level="E2")], pid="AUD-3")
    with pytest.raises(ap.ApprovalError) as ei: ap.approve("AUD-3", reviewer="a", reason="r", db_path=db)
    assert ei.value.status == 409                                                       # 같은 기관 동명

def test_reject_and_state_guard(tmp_path):
    db = tmp_path / "a.db"; _seed(db)
    _proposal(db, "new_person", {}, [])
    out = ap.reject("AUD-1", reviewer="a", reason="근거 부족", db_path=db)
    assert out["status"] == "rejected" and out["decision_reason"] == "근거 부족"
    with pytest.raises(ap.ApprovalError) as ei: ap.reject("AUD-1", reviewer="a", reason="again", db_path=db)
    assert ei.value.status == 409
    events = store.list_proposal_events("AUD-1", db_path=db)
    assert [e["event"] for e in events] == ["rejected"]

def test_create_proposal_dedupes_open_keys(tmp_path):
    db = tmp_path / "a.db"
    body = {"proposal_type": "new_person", "dedupe_key": "new_person:a:b:c", "summary": "요약", "rationale": "근거",
            "proposed_change": {"name": "a"}, "evidence": [E()]}
    first = ap.create_proposal(body, origin="agent_api", db_path=db)
    assert first["proposal_id"].startswith("AUD-") and first["status"] == "needs_review"
    with pytest.raises(ap.ApprovalError) as ei: ap.create_proposal(body, origin="agent_api", db_path=db)
    assert ei.value.status == 409 and first["proposal_id"] in ei.value.message
```

- [ ] **Step 2: 실패 확인** → `ModuleNotFoundError: agents.stakeholder.approval`

- [ ] **Step 3: 구현 — `approval.py`** (불변식은 소스 `stakeholder_service.py` 를 1:1 로 옮긴다)

```python
# agents/stakeholder/approval.py
"""감사 제안 승인 불변식 + 승인/반려/스케줄 반영. 소스 stakeholder_service.py 의 approve/reject/create 를 sqlite 트랜잭션으로.

정책(MONITORING_AUDIT_AGENT_POLICY.md): 에이전트는 제안만, 승인은 사람(admin). source_expansion official 은 E3+공식,
그 외 E2+; role_change 는 공식 E3+; new_person 은 E2+ 이고 등록은 항상 candidate. 충돌(conflicts) 있으면 승인 불가.
효력은 익일(tomorrow_kst). scheduled → applied 는 apply_scheduled(today) 가 매일 sync 잡 안에서 돌린다.
"""
from __future__ import annotations
import json, uuid
from datetime import date
from urllib.parse import urlparse
from . import store
from .store import (OPEN_PROPOSAL_STATUSES, PRIORITIES, PROPOSAL_TYPES, SOURCE_GROUPS, _j, _ts, tomorrow_kst)

class ApprovalError(Exception):
    def __init__(self, status: int, message: str, code: str = "APPROVAL"):
        super().__init__(message); self.status, self.message, self.code = status, message, code

def has_evidence(proposal: dict, levels: set[str], official: bool = False) -> bool:
    for item in proposal.get("evidence", []) or []:
        if item.get("evidence_level") not in levels: continue
        if official and item.get("source_class") != "official": continue
        if not item.get("publisher") or not item.get("locator"): continue
        return True
    return False

def normalize_domain(value: str) -> str:
    raw = (value or "").strip().lower().rstrip(".")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").rstrip(".")
    if not host or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ApprovalError(422, "출처 도메인은 경로 없는 호스트명이어야 합니다.", "BAD_DOMAIN")
    return host

def validate_evidence(item: dict) -> dict:
    url = str(item.get("url") or "")
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.netloc:
        raise ApprovalError(422, "근거 URL은 http 또는 https 주소여야 합니다.", "BAD_URL")
    out = {"url": url, "publisher": str(item.get("publisher") or ""), "source_class": str(item.get("source_class") or "secondary"),
           "evidence_level": str(item.get("evidence_level") or "E0"), "quoted_text": str(item.get("quoted_text") or ""),
           "locator": str(item.get("locator") or "")}
    for k in ("publication_date", "effective_date", "retrieved_at", "content_hash", "independence_group"):
        out[k] = item.get(k)
    return out

def validate_proposal_input(body: dict) -> dict:
    """소스 AuditProposalInput(pydantic) 과 같은 규칙. 422 메시지도 같다."""
    ptype = body.get("proposal_type")
    if ptype not in PROPOSAL_TYPES: raise ApprovalError(422, "지원하지 않는 감사 제안 유형입니다.", "BAD_TYPE")
    priority = body.get("priority", "normal")
    if priority not in PRIORITIES: raise ApprovalError(422, "priority 값이 올바르지 않습니다.", "BAD_PRIORITY")
    dedupe = str(body.get("dedupe_key") or "")
    if not 3 <= len(dedupe) <= 240: raise ApprovalError(422, "dedupe_key 길이는 3~240 이어야 합니다.", "BAD_DEDUPE")
    summary, rationale = str(body.get("summary") or ""), str(body.get("rationale") or "")
    if not 3 <= len(summary) <= 1000: raise ApprovalError(422, "summary 길이는 3~1000 이어야 합니다.", "BAD_SUMMARY")
    if not 3 <= len(rationale) <= 3000: raise ApprovalError(422, "rationale 길이는 3~3000 이어야 합니다.", "BAD_RATIONALE")
    change = body.get("proposed_change")
    if not isinstance(change, dict): raise ApprovalError(422, "proposed_change 는 객체여야 합니다.", "BAD_CHANGE")
    return {"proposal_type": ptype, "dedupe_key": dedupe, "priority": priority, "summary": summary, "rationale": rationale,
            "proposed_change": change, "evidence": [validate_evidence(e) for e in (body.get("evidence") or [])],
            "conflicts": [str(c) for c in (body.get("conflicts") or [])], "audit_run_id": body.get("audit_run_id"),
            "created_by": str(body.get("created_by") or "Monitoring Coverage Auditor")}

def create_proposal(body: dict, *, origin: str, db_path=store.DEFAULT_DB_PATH) -> dict:
    data = validate_proposal_input(body)
    ts = _ts()
    row = {**data, "proposal_id": f"AUD-{uuid.uuid4().hex[:12].upper()}", "status": "needs_review", "effective_from": None,
           "reviewer": None, "reviewed_at": None, "decision_reason": None, "created_at": ts, "updated_at": ts}
    try:
        store.insert_proposal_if_absent(row, origin=origin, db_path=db_path)
    except store.ConflictError:
        dup = store.find_open_proposal(data["dedupe_key"], db_path=db_path)
        raise ApprovalError(409, f"같은 감사 제안이 이미 열려 있습니다: {dup['proposal_id']}", "DUPLICATE")
    store.add_proposal_event(row["proposal_id"], actor=data["created_by"], event="created", detail=origin, db_path=db_path)
    return store.get_proposal(row["proposal_id"], db_path=db_path)

def _plan_source_expansion(conn, proposal: dict, change: dict, effective_from: str) -> dict:
    group = str(change.get("source_group", "discovered_additional"))
    levels = {"E3", "E4"} if group == "official" else {"E2", "E3", "E4"}
    if not has_evidence(proposal, levels, official=group == "official"):
        raise ApprovalError(422, "출처 승인에는 E2 이상의 근거가 필요합니다.", "EVIDENCE")
    domain = normalize_domain(str(change.get("domain", "")))
    name = str(change.get("name", "")).strip()
    if not domain or not name or group not in SOURCE_GROUPS:
        raise ApprovalError(422, "출처명, 도메인, 출처군을 확인해 주세요.", "BAD_SOURCE")
    if conn.execute("SELECT 1 FROM sv_source_domain WHERE domain=?", (domain,)).fetchone():
        raise ApprovalError(409, "이미 출처 레지스트리에 있는 도메인입니다.", "DUPLICATE_DOMAIN")
    if not any((urlparse(e.get("url", "")).hostname or "").lower().rstrip(".") == domain for e in proposal.get("evidence", [])):
        raise ApprovalError(422, "제안 도메인과 일치하는 근거 URL이 필요합니다.", "EVIDENCE_DOMAIN")
    return {"source_id": f"SRC-AUD-{uuid.uuid4().hex[:10].upper()}", "name": name, "domain": domain, "source_group": group,
            "status": "approved", "effective_from": effective_from, "approved_from_proposal": proposal["proposal_id"],
            "evidence": proposal.get("evidence", [])}
# _plan_role_change / _plan_new_person 도 같은 식 — 소스 approve_audit_proposal 의 분기 그대로 (409/422 문구 보존).

def approve(proposal_id: str, *, reviewer: str, reason: str, db_path=store.DEFAULT_DB_PATH) -> dict:
    if not reason or len(reason.strip()) < 2: raise ApprovalError(422, "검토 사유를 입력해 주세요.", "BAD_REASON")
    with store.transaction(db_path) as conn:
        proposal = store.get_proposal(proposal_id, conn=conn)
        if proposal is None: raise ApprovalError(404, "감사 제안을 찾을 수 없습니다.", "NOT_FOUND")
        if proposal["status"] not in OPEN_PROPOSAL_STATUSES: raise ApprovalError(409, "현재 상태에서는 승인할 수 없습니다.", "STATE")
        if proposal.get("conflicts"): raise ApprovalError(422, "미해결 충돌이 있는 제안은 승인할 수 없습니다.", "CONFLICTS")
        change, effective_from, ts = proposal["proposed_change"], tomorrow_kst(), _ts()
        if proposal["proposal_type"] == "source_expansion":
            store.insert_source_domain_row(conn, _plan_source_expansion(conn, proposal, change, effective_from), origin="audit")
        elif proposal["proposal_type"] == "role_change":
            store.insert_role_history_row(conn, _plan_role_change(conn, proposal, change, effective_from, ts))
        else:
            store.insert_person_row(conn, _plan_new_person(conn, proposal, change, effective_from, ts), origin="audit")
        cur = conn.execute("UPDATE sv_audit_proposal SET status='scheduled', effective_from=?, reviewer=?, reviewed_at=?, "
                           "decision_reason=?, updated_at=? WHERE proposal_id=? AND status IN ('needs_review','needs_evidence')",
                           (effective_from, reviewer, ts, reason.strip(), ts, proposal_id))
        if cur.rowcount != 1: raise ApprovalError(409, "현재 상태에서는 승인할 수 없습니다.", "RACE")
        store.add_proposal_event_row(conn, proposal_id, actor=reviewer, event="approved", detail=reason.strip())
    return store.get_proposal(proposal_id, db_path=db_path)

def apply_scheduled(*, today: date, db_path=store.DEFAULT_DB_PATH) -> dict:
    """effective_from <= today 인 scheduled 제안을 applied 로. role_change 는 현재 직책을 role_history(superseded, effective_to=today)
    로 내리고 새 직책 적용 + verification_status='verified'(승인이 공식 E3+ 를 요구했으므로). 실패는 application_failed + 이벤트."""
    ...
    return {"applied": n_applied, "failed": n_failed}
```

- [ ] **Step 4: 통과 확인** → 테스트 전부 통과
- [ ] **Step 5: 커밋** — `feat(stakeholder): 감사 제안 승인 불변식·승인/반려·익일 스케줄 반영을 sqlite 트랜잭션으로 이식`

**완료 기준:** 소스 `approve_audit_proposal`/`reject_audit_proposal`/`create_audit_proposal` 의 모든 `HTTPException` 분기에 대응하는 테스트가 있고 상태코드·핵심 문구가 같다; `apply_scheduled` 가 멱등(같은 날 두 번 → 두 번째 `applied=0`); 승인 중 예외 시 부수 행이 남지 않는다(테스트: `_plan_new_person` 를 monkeypatch 로 터뜨린 뒤 `sv_person` 변화 없음).

---

### Task 3: 정본 타임라인 MD 파서 + 부분문자열 검증 + 중요도표 **[Opus]**

**Files:**
- Create: `agents/stakeholder/canonical_parse.py`, `tests/test_stakeholder_canonical_parse.py`
- 픽스처: T4 의 `tests/fixtures/stakeholder_src_mini/2026-10-08_주제별_인물발언_타임라인_정본.md` (T4 미완이면 테스트가 `tmp_path` 에 같은 형식의 MD 를 쓴다 — 아래 `_MD` 문자열)

**문법(소스 MD 실측, `조사 확인 결과 #5`)**

| 요소 | 정규식 / 규칙 |
|---|---|
| 주제 섹션 | `^# (\d+)\. 주제 ([①②③④⑤]) (.+)$` → topic slug 순서 `price_reform, access, rwe, finance, negotiation` |
| 행사 | `^## (\d{4}-\d{2}-\d{2})｜(.+)$` (`｜` U+FF5C; ASCII `|` 도 허용) → `event_id = f"{date}-{slug(title)}"[:90]`, slug 는 `[^0-9A-Za-z가-힣]+` → `-` |
| 발언자 | `^### (.+?)(?: — (.+))?$` → `speaker_name`, `speaker_role_at_event`. 이름에 `·` 가 있고 role 이 없으면 `multi_speaker=1`, 이름 그대로 |
| 인용 블록 | 연속한 `^> ` 줄 = 블록 1개. 블록 안 `“([^”]+)”` 세그먼트 전부 추출 — 세그먼트마다 `sv_quote` 1행(**블록 전체 문자열은 `quote_text`, 세그먼트 목록은 `segments_json` 에** — 손실 없음). 곡괄호 인용부호가 없으면 블록 전체가 세그먼트 1개 |
| 필드 | `^- \*\*(내용|해석|한계|제도상태|시점 한계|등급|근거|원문|행사일/게시일|행사·직책 확인|해석 신뢰도|대안해석)[:：]\*\*\s*(.*)$` → 매핑: 내용→`summary_text`, 해석→`analysis_text`, 한계/제도상태/시점 한계→`limitation_text`(복수면 `\n` 으로 이어붙이고 `policy_status` 에 제도상태 원문), 등급→`evidence_grade`, 근거→locators, 원문→`source_url`, 행사일/게시일→`publication_date`(뒤쪽), 행사·직책 확인→`role_check_url` |
| locator | `` `sources/([^`:]+?)(?::(\d+)(?:-(\d+))?)?` `` 를 전부 → `[{path, start, end}]`; `source_id` 는 manifest 에서 `text_path == f"sources/{path}"` 인 행 (import 단계에서 결합; 파서는 path 만) |
| 마스터 표 | §1 안 `^\| \*\*(\d{4}-\d{2}-\d{2})\*\* \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$` → `sv_event.context/speakers_json/topics_json/grades_json` 보강. 섹션 행사의 (date,title) 는 마스터 표와 일치해야 하며 불일치는 `warnings` |
| §7·§9 | 헤딩 `^# 7\.`·`^# 9\.` 아래 본문을 `meta["repeat_messages_md"]`·`meta["withheld_md"]` 로 |
| 부분문자열 검증 | `norm(s) = s 에서 공백 전부 제거 + “”‘’"' 제거`; 세그먼트를 `[…]` 로 나눈 조각마다 locator 범위 줄(`lines[start-1:end]`)의 norm 에 포함되면 `range`, 아니면 파일 전체 norm 에 포함되면 `file`, 아니면 `none`. 인용 1행이 여러 블록(주제 교차)에 속하면 `locator` 는 첫 블록 것, `locator_match` 는 모든 블록 locator 중 **최선**(`range` > `file` > `none`). locator 가 없으면 `unchecked` |
| 중요도표 | `부서-인물지도` §2.2 표 행 `^\| \*\*(.+?)\*\* \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| \*\*(\d+)\*\* \| \*\*(\d)\*\* \| (\d)(\(잠정\))? \| (\d) \|$` → `{name: {score_total, importance, components, work_importance, work_provisional, public_confidence}}`. 실패는 warning, 결과 없음 |

- [ ] **Step 1: 실패 테스트** (합성 MD — 실제 문장 금지)

```python
# tests/test_stakeholder_canonical_parse.py
from __future__ import annotations
from pathlib import Path
from agents.stakeholder import canonical_parse as cp

_MD = """# 최근 1년 테스트 타임라인

# 1. 전체 행사 마스터 타임라인

| 행사일 | 행사·맥락 | 핵심 인물과 당시 직책 | 핵심 주제 | 발언근거 |
|---|---|---|---|---|
| **2026-09-03** | 가상 협회 약가 토론 | 홍길동 가상부 가상과장 | 약가개편 | C-quote |
| **2026-06-19** | 가상 RWE 심포지엄 | 김가상 가상원 실장 | RWE | B-summary |

# 2. 주제 ① 약가제도 개편

## 2026-09-03｜가상 협회 약가 토론

### 홍길동 — 가상부 가상과장

> “신약 보상과 재정은 상충하지 않는다고 본다”며 “하반기에 신약을 어떻게 가져갈지가 핵심”

- **내용:** 개편 의제를 설명했다고 보도됐다.
- **해석:** 양자택일이 아닌 동시 설계 과제로 제시했다.
- **등급:** C-quote/C-summary
- **근거:** `sources/fake_20260903_hong.txt:3-5`
- **원문:** https://example.test/news/1

# 3. 주제 ② 신약 접근성

## 2026-09-03｜가상 협회 약가 토론

### 홍길동 — 가상부 가상과장

> “신약 보상과 재정은 상충하지 않는다고 본다”며 “하반기에 신약을 어떻게 가져갈지가 핵심”

- **한계:** 본사업 전환이 확정됐다는 발언은 아니다.
- **등급:** C-quote
- **근거:** `sources/fake_20260903_hong.txt:3-5`

# 4. 주제 ③ RWE

## 2026-06-19｜가상 RWE 심포지엄

### 김가상·박가상

- **내용:** 국가 레지스트리 필요성을 제시했다.
- **등급:** B-summary
- **근거:** `sources/fake_20260619_rwe.txt`

# 7. 인물별 반복 메시지 변화

홍길동은 반복해서 균형을 말했다.

# 9. 보류·한계

1. 가상 보류 항목.
"""

_TXT = "제목\n\n홍길동 과장은 “신약 보상과 재정은 상충하지 않는다고 본다”며\n“하반기에 신약을 어떻게 가져갈지가 핵심”이라고 말했다.\n끝\n"

def _root(tmp_path) -> Path:
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "fake_20260903_hong.txt").write_text(_TXT, encoding="utf-8")
    (tmp_path / "sources" / "fake_20260619_rwe.txt").write_text("요약만 있는 보도자료\n", encoding="utf-8")
    (tmp_path / "t.md").write_text(_MD, encoding="utf-8")
    return tmp_path

def test_parse_events_statements_quotes(tmp_path):
    root = _root(tmp_path)
    res = cp.parse_timeline(root / "t.md", root=root)
    assert [e.event_id for e in res.events] == ["2026-09-03-가상-협회-약가-토론", "2026-06-19-가상-RWE-심포지엄"]
    assert res.events[0].context == "가상 협회 약가 토론" and res.events[0].grades == ["C-quote"]
    assert len(res.statements) == 3                       # (행사×발언자×주제) 블록 3
    s0 = res.statements[0]
    assert s0.topic == "price_reform" and s0.speaker_name == "홍길동" and s0.speaker_role_at_event == "가상부 가상과장"
    assert s0.evidence_grade == "C-quote/C-summary" and s0.locators == [{"path": "sources/fake_20260903_hong.txt", "start": 3, "end": 5}]
    assert s0.source_url == "https://example.test/news/1" and s0.summary_text.startswith("개편 의제")
    assert res.statements[1].limitation_text.startswith("본사업") and res.statements[2].multi_speaker is True
    assert len(res.quotes) == 1                            # 같은 인용이 두 주제에 교차 표시 → 1행, topics 2
    q = res.quotes[0]
    assert q.topics == ["price_reform", "access"] and len(q.statement_ids) == 2
    assert q.segments == ["신약 보상과 재정은 상충하지 않는다고 본다", "하반기에 신약을 어떻게 가져갈지가 핵심"]
    assert q.locator_match == "range"
    assert res.meta["repeat_messages_md"].startswith("홍길동은") and "가상 보류" in res.meta["withheld_md"]
    assert res.raw_quote_blocks == 2                       # 손실 없음 검증용: 원본 `> ` 블록 수

def test_locator_match_falls_back_to_file_then_none(tmp_path):
    root = _root(tmp_path)
    md = _MD.replace("hong.txt:3-5`", "hong.txt:1-1`")          # 두 블록(두 주제) 모두 — 인용 1행이 두 블록을 공유한다
    (root / "t2.md").write_text(md, encoding="utf-8")
    assert cp.parse_timeline(root / "t2.md", root=root).quotes[0].locator_match == "file"
    md2 = _MD.replace("상충하지 않는다고 본다", "완전히 다른 문장")
    (root / "t3.md").write_text(md2, encoding="utf-8")
    assert cp.parse_timeline(root / "t3.md", root=root).quotes[0].locator_match == "none"

def test_norm_ignores_whitespace_and_quote_marks():
    assert cp.norm(" “a b”  c ") == "abc"

def test_master_table_mismatch_is_warning_not_error(tmp_path):
    root = _root(tmp_path)
    (root / "t4.md").write_text(_MD.replace("| **2026-06-19** | 가상 RWE 심포지엄", "| **2026-06-19** | 다른 제목"), encoding="utf-8")
    res = cp.parse_timeline(root / "t4.md", root=root)
    assert any("마스터 표" in w for w in res.warnings) and len(res.events) == 2

def test_importance_table(tmp_path):
    md = """## 2.2 모니터링 리스트 평가
| 인물 | 직접성/30 | 조직/25 | 발언/20 | 근거/15 | 반복/10 | 총점 | 중요도 | 업무중요도 | 공개확신도 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **홍길동** | 30 | 25 | 20 | 10 | 10 | **95** | **5** | 5 | 5 |
| **박가상** | 18 | 18 | 0 | 3 | 0 | **39** | **1** | 3(잠정) | 1 |
"""
    (tmp_path / "m.md").write_text(md, encoding="utf-8")
    t = cp.parse_importance_table(tmp_path / "m.md")
    assert t["홍길동"]["importance"] == 5 and t["홍길동"]["score_total"] == 95
    assert t["박가상"]["work_provisional"] is True and t["박가상"]["components"] == [18, 18, 0, 3, 0]
```

- [ ] **Step 2: 실패 확인**, **Step 3: 구현** — 데이터클래스 `ParsedEvent/ParsedStatement/ParsedQuote/ParseResult(events, statements, quotes, meta, warnings, raw_quote_blocks)`; `statement_id = sha1(f"{event_id}|{speaker}|{topic}")[:16]`, `quote_id = sha1(f"{event_id}|{speaker}|{norm}")[:16]`; `headline_quote_id` 는 블록의 첫 인용. 파서는 **파일을 읽기만** 하고 DB 를 모른다.
- [ ] **Step 4: 실데이터 스모크(커밋 대상 아님)** — `.venv/bin/python -c "from agents.stakeholder import canonical_parse as cp; r=cp.parse_timeline('<SRC>/2026-10-08_주제별_인물발언_타임라인_정본.md', root='<SRC>'); print(len(r.events), len(r.statements), len(r.quotes), r.raw_quote_blocks, sum(q.locator_match=='none' for q in r.quotes), r.warnings)"` → 기대 `12 35 ≤52 52 0 []`(유니크 인용 수는 교차 표시 때문에 52 미만; `none` 이 0 이 아니면 파서 결함 — 소스가 자체 검증을 통과한 문서다).
- [ ] **Step 5: 커밋** — `feat(stakeholder): 정본 발언 타임라인 MD 결정론 파서 — 행사·발언블록·인용(교차주제 병합)·locator 부분문자열 검증`

**완료 기준:** 5 테스트 통과; Step 4 실측에서 `none`=0, `warnings`=[]; 파서가 어휘 밖 등급·필드를 만나면 **경고**(예외 아님).

---

### Task 4: 환경·규칙·합성 픽스처 **[Sonnet]**

**Files:**
- Create: `tests/fixtures/stakeholder_src_mini/**`, `agents/rules/stakeholder_verbatim_rules.md`
- Modify: `requirements.txt`(+`Pillow>=10,<13` — 소스 `requirements.txt` 범위와 동일), `.gitignore`(+`data/stakeholder/`), `scripts/start_production.sh`(mkdir 목록에 `data/stakeholder/photos data/stakeholder/.sync` 추가), `CLAUDE.md` 규칙 맵에 행 `| Stakeholder Verbatim (인물·발언·감사) | agents/rules/stakeholder_verbatim_rules.md |`

- [ ] **Step 1: 픽스처** — 소스 repo 와 **같은 디렉터리 구조**, 전부 합성(매체명 `가상뉴스`, 도메인 `example-news.test`, 인물 `홍길동`·`김가상`·`박가상`). 파일:
  - `data/people_registry.json`(schema_version 1.0, people 3: verified 2 + candidate 1, 한 명은 `photo_url` 가상 CDN), `data/source_registry.json`(sources 4: official 1·top_tier_general 1·healthcare_trade 2), `data/audit_proposals.json`(proposals 3: new_person(E3 official + E1 ×2, conflicts 1), role_change(E3 official, candidate 인물 대상), source_expansion(E1 ×2 off_list_media))
  - `source_manifest.json`: sources 3(텍스트+raw html 2, user image 1 — 1×1 png), artifacts 2(정본 MD, `scripts/verify_repository.py` 사본), `withheld_claims` 1, `evidence_grade_vocabulary` 7, `project`·`scope_*`·`updated_at_kst`. **sha256/bytes 는 생성 스크립트가 실제 파일에서 계산**(`tests/fixtures/stakeholder_src_mini/_build.py` — 픽스처를 고칠 때 재실행; 테스트는 이 스크립트를 부르지 않는다)
  - `sources/fake_20260903_hong.txt`, `sources/fake_20260619_rwe.txt`, `sources/raw/fake_20260903_hong.html`, `sources/raw/fake_20260619_rwe.html`, `sources/user_input/list.png`
  - `2026-10-08_주제별_인물발언_타임라인_정본.md`(T3 의 `_MD` 와 같은 내용에 §8 출처 검증대장 표 추가), `2026-10-08_최근1년_약가-건보재정_부서-인물지도_정본.md`(§2.2 표만), `README.md`, `MONITORING_SOURCE_AND_QUOTE_POLICY.md`(제목 1줄), `relationship.svg`(최소 SVG)
  - `monitoring-runs/2026-10-10/daily_monitoring.json`(소스 schema 1.1 의 **모든 최상위 키**, `search_log` 2건, `coverage_matrix` 4건, `events`/`statements` 빈 배열) + `.md` + `raw/search_results.json` + `raw/00_example_bing.xml`; `monitoring-runs/2026-10-11/...`(run_status `no_change`)
  - `audit-runs/2026-10-10/coverage_audit.json`(소스 1.0 의 모든 최상위 키, `run_status: "degraded"`, `claim`, `proposal_summary.created: 3`) + `.md` + `raw/proposal_post_responses.json`
  - `dashboard-sketches/README.md`, `03-Work-Logs/x.md`, `.env.example` — **허용목록 제외 검증용**(sync 가 복사하지 않아야 함)
  - `scripts/verify_repository.py` — 소스 원본 그대로(저작권 무관한 코드)
- [ ] **Step 2: 픽스처 자가검증** — `.venv/bin/python -I tests/fixtures/stakeholder_src_mini/scripts/verify_repository.py` 를 픽스처 루트에서 실행(스크립트의 `ROOT = parents[1]`) → `VALIDATION OK: N JSON files parsed; M manifest hashes matched`
- [ ] **Step 3: 규칙 문서** `agents/rules/stakeholder_verbatim_rules.md` — 구성: §1 범위·소유권(외부 에이전트 소유 vs 대쉬보드 소유 표, insert-if-absent 규약), §2 원문·인용 규칙(소스 정책 §1~§7 을 **문구 그대로** 옮기고 출처 명시), §3 감사·승인 규칙(감사축 3·증거수준 E0~E4·제안 필드·상태·금지행위·승인 효력 익일·role_change 적용 시 verified 전환 근거), §4 인증·접근 등급(Q3·Q7 표), §5 저작권·개인정보, §6 절대 금지(원문·사진 git 커밋, 언론 CDN 핫링크, sync 의 결정 테이블 UPDATE, 제안 자동 승인, 기록 삭제), §7 회귀 체크 포인터(T10 스크립트).
- [ ] **Step 4: 커밋** — `chore(stakeholder): 합성 픽스처·규칙 문서·Pillow 의존성·볼륨 디렉터리`

**완료 기준:** 픽스처에 실제 매체명·실제 인물·실제 기사 문장이 없다(`grep -rE "히트뉴스|hitnews|데일리팜|강준혁|김국희" tests/fixtures/stakeholder_src_mini` → 0건); `verify_repository.py` OK; `pip-compile` 불필요(요구사항 1줄).

---

### Task 5: git-sync + 스냅샷 import + 무결성 + CLI **[Opus]**

**Files:**
- Create: `agents/stakeholder/sync.py`, `tests/test_stakeholder_sync.py`

**설계**

```
sync(root, db_path, *, source_dir=None, repo=None, branch="main", token=None, fetch_tree=None, fetch_blob=None, today=None)
  1) 원격 목록: source_dir 모드 → os.walk + git blob sha(= sha1("blob <len>\0"+bytes)) / 원격 → GET /repos/{repo}/git/trees/{branch}?recursive=1
  2) 허용목록(paths.SYNC_*) + SAFE_PATH + MAX_BLOB_BYTES 필터 → rejected 집계
  3) sv_sync_file 의 blob_sha 와 대조 → 바뀐/없는 파일만 fetch(Contents API raw) → root/<path> 에 .tmp 쓰고 os.replace → sv_sync_file 갱신
     원격에서 사라진 경로는 삭제하지 않는다(기록 삭제 금지) → counts.orphaned
  4) import_snapshot(root, db_path, today) :
     a. source_manifest.json → sv_meta(project, scope_*, withheld_claims_json, evidence_grade_vocabulary_json, topic_timeline_update_json,
        manifest_sha256, copyright_notice) + sv_source upsert(22) + sv_artifact upsert(10); 파일별 SHA-256 재계산 → integrity_status
     b. monitoring-runs/*/daily_monitoring.json → sv_monitoring_run upsert(payload sha 게이트); audit-runs/*/coverage_audit.json → sv_audit_run
     c. 정본 타임라인 MD: sha != sv_meta['canonical_timeline_sha256'] 일 때만 canonical_parse → 한 트랜잭션에서 origin='canonical_md' 행 교체,
        person_id 결합(sv_person.name 일치 시), locator → source_id 결합(sv_source.text_path). 파서 예외 → errors, 기존 행 유지
        부서-인물지도 중요도표 → sv_person.importance_* (이름 일치 행만 UPDATE — 이 두 컬럼은 sync 소유)
     d. data/people_registry.json / source_registry.json / audit_proposals.json → insert-if-absent (origin='import'); counts.registry_new/_existing
     e. approval.apply_scheduled(today)
     f. sv_meta: last_sync_at, last_tree_sha, integrity_summary_json
  반환: {"ok": not errors, "counts": {...}, "errors": [...], "error_class": "config"|"transient"}
```

- 프로덕션 fail-closed: `source_dir is None and token is None and Path("/app/data").exists()` → `{"ok": False, "error": "auth required: STAKEHOLDER_SYNC_TOKEN missing (fail-closed in production)", "error_class": "config"}` 즉시 반환(policy_analysis_sync 와 같은 문구 패턴).
- 토큰 우선순위: `STAKEHOLDER_SYNC_TOKEN` → `GITHUB_TOKEN`. repo 기본 `STAKEHOLDER_SYNC_REPO` 또는 `jeonpapa/kr-drug-pricing-nhi-finance-intelligence`.
- HTTP: `urllib.request` 30초, 헤더 `Authorization: Bearer`, `Accept: application/vnd.github+json`(tree) / `application/vnd.github.raw`(blob), `X-GitHub-Api-Version: 2022-11-28`. 한 번의 sync 가 받는 총량 상한 200MB(초과 시 나머지는 다음 실행 — `counts.deferred`).
- CLI: `python -m agents.stakeholder.sync --source-dir <dir> [--root <root>] [--db <db>]`(1회 import/로컬) · `--now`(원격; 잡과 같은 경로) · `--dry-run`(목록·변경 수만).

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_stakeholder_sync.py
from __future__ import annotations
import hashlib, json, shutil
from datetime import date
from pathlib import Path
import pytest
from agents.stakeholder import approval, store, sync

FIX = Path(__file__).parent / "fixtures" / "stakeholder_src_mini"

def _run(tmp_path, src=FIX, today=date(2026, 10, 12)):
    root, db = tmp_path / "root", tmp_path / "s.db"
    return sync.sync(root, db, source_dir=src, today=today), root, db

def test_first_sync_mirrors_allowed_files_only(tmp_path):
    res, root, db = _run(tmp_path)
    assert res["ok"] is True and res["errors"] == []
    assert (root / "sources" / "fake_20260903_hong.txt").exists() and (root / "source_manifest.json").exists()
    assert not (root / "dashboard-sketches").exists() and not (root / "03-Work-Logs").exists() and not (root / ".env.example").exists()
    assert res["counts"]["downloaded"] == res["counts"]["listed"] and res["counts"]["rejected"] >= 3

def test_second_sync_is_noop_by_blob_sha(tmp_path):
    _, root, db = _run(tmp_path)
    res2 = sync.sync(root, db, source_dir=FIX, today=date(2026, 10, 12))
    assert res2["counts"]["downloaded"] == 0 and res2["counts"]["canonical_reparsed"] == 0 and res2["ok"]

def test_import_counts_match_fixture(tmp_path):
    _, root, db = _run(tmp_path)
    c = store.counts(db_path=db)
    m = json.loads((FIX / "source_manifest.json").read_text(encoding="utf-8"))
    assert c["sv_source"] == len(m["sources"]) and c["sv_artifact"] == len(m["artifacts"])
    assert c["sv_person"] == 3 and c["sv_source_domain"] == 4 and c["sv_audit_proposal"] == 3
    assert c["sv_monitoring_run"] == 2 and c["sv_audit_run"] == 1 and c["sv_event"] == 2
    assert all(s["integrity_status"] in ("verified", "no_local") for s in store.list_sources(db_path=db))
    run = store.list_runs("audit", db_path=db)[0]
    assert run["claim"] and run["run_status"] == "degraded"

def test_integrity_mismatch_is_flagged_not_fatal(tmp_path):
    src = tmp_path / "src"; shutil.copytree(FIX, src)
    (src / "sources" / "fake_20260903_hong.txt").write_text("변조", encoding="utf-8")
    res, root, db = _run(tmp_path, src=src)
    st = {s["text_path"]: s["integrity_status"] for s in store.list_sources(db_path=db)}
    assert st["sources/fake_20260903_hong.txt"] == "mismatch" and res["ok"] is True
    assert store.get_meta("integrity_summary_json", db_path=db) and "mismatch" in store.get_meta("integrity_summary_json", db_path=db)

def test_resync_never_overwrites_operator_decision(tmp_path):
    _, root, db = _run(tmp_path)
    pid = [p for p in store.list_proposals(db_path=db) if p["proposal_type"] == "role_change"][0]["proposal_id"]
    approval.approve(pid, reviewer="admin@x", reason="확인", db_path=db)
    store.set_person_status([p for p in store.list_people(db_path=db)][0]["person_id"], "paused", actor="admin@x", db_path=db)
    before = {p["person_id"]: p for p in store.list_people(db_path=db)}
    res = sync.sync(root, db, source_dir=FIX, today=date(2026, 10, 12))
    assert store.get_proposal(pid, db_path=db)["status"] == "scheduled" and store.get_proposal(pid, db_path=db)["reviewer"] == "admin@x"
    assert {p["person_id"]: p for p in store.list_people(db_path=db)} == before
    assert res["counts"]["registry_new"] == 0 and res["counts"]["registry_existing"] == 10

def test_apply_scheduled_runs_inside_sync(tmp_path):
    _, root, db = _run(tmp_path)
    pid = [p for p in store.list_proposals(db_path=db) if p["proposal_type"] == "role_change"][0]["proposal_id"]
    approval.approve(pid, reviewer="admin@x", reason="확인", db_path=db)          # effective 2026-10-13
    res = sync.sync(root, db, source_dir=FIX, today=date(2026, 10, 13))
    assert res["counts"]["scheduled_applied"] == 1 and store.get_proposal(pid, db_path=db)["status"] == "applied"

def test_canonical_reparse_only_when_sha_changes_and_keeps_dashboard_rows(tmp_path):
    src = tmp_path / "src"; shutil.copytree(FIX, src)
    _, root, db = _run(tmp_path, src=src)
    md = src / "2026-10-08_주제별_인물발언_타임라인_정본.md"
    md.write_text(md.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    res = sync.sync(root, db, source_dir=src, today=date(2026, 10, 12))
    assert res["counts"]["canonical_reparsed"] == 1 and store.counts(db_path=db)["sv_event"] == 2

def test_parser_failure_keeps_previous_rows_and_reports_error(tmp_path, monkeypatch):
    src = tmp_path / "src"; shutil.copytree(FIX, src)
    _, root, db = _run(tmp_path, src=src)
    md = src / "2026-10-08_주제별_인물발언_타임라인_정본.md"
    md.write_text(md.read_text(encoding="utf-8") + "\n#", encoding="utf-8")
    monkeypatch.setattr(sync.canonical_parse, "parse_timeline", lambda *a, **k: (_ for _ in ()).throw(ValueError("깨짐")))
    res = sync.sync(root, db, source_dir=src, today=date(2026, 10, 12))
    assert res["ok"] is False and any("깨짐" in e for e in res["errors"]) and store.counts(db_path=db)["sv_quote"] >= 1

def test_production_without_token_is_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "_require_auth", lambda: True)
    monkeypatch.delenv("STAKEHOLDER_SYNC_TOKEN", raising=False); monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    res = sync.sync(tmp_path / "root", tmp_path / "s.db", today=date(2026, 10, 12))
    assert res["ok"] is False and res["error_class"] == "config" and res["error"].startswith("auth required")

def test_remote_mode_uses_injected_fetchers(tmp_path):
    tree = {"sha": "T", "tree": [{"path": "source_manifest.json", "type": "blob", "sha": "a", "size": 10},
                                  {"path": "dashboard-sketches/x.html", "type": "blob", "sha": "b", "size": 10},
                                  {"path": "sources/../x", "type": "blob", "sha": "c", "size": 1}]}
    manifest = (FIX / "source_manifest.json").read_bytes()
    res = sync.sync(tmp_path / "root", tmp_path / "s.db", repo="o/r", token="t", today=date(2026, 10, 12),
                    fetch_tree=lambda repo, branch, token: tree, fetch_blob=lambda repo, path, branch, token: manifest)
    assert res["counts"]["listed"] == 1 and res["counts"]["rejected"] == 2 and (tmp_path / "root" / "source_manifest.json").exists()
```

- [ ] **Step 2: 실패 확인** · **Step 3: 구현**(위 설계대로; `store.counts()`·`list_sources()`·`list_role_history()`·`find_open_proposal()`·`list_proposal_events()` 등 T1 에 없는 조회는 **T5 가 `store.py` 에 추가**해도 된다 — 단 T1 테스트를 깨지 않는다)
- [ ] **Step 4: 실데이터 1회 import 리허설(커밋 대상 아님, 임시 경로)** — `.venv/bin/python -m agents.stakeholder.sync --source-dir <SRC> --root /tmp/sv_root --db /tmp/sv.db` → 출력에 `listed=~370 downloaded=~370 rejected≥8(스케치 4·업무일지 1·.env.example·.gitignore·requirements.txt·stakeholder_service.py) canonical_reparsed=1 registry_new=48(11+32+5) integrity: verified=20 no_local=1 mismatch=0 missing=0 scheduled_applied=0`. `/tmp/sv_root` 용량 ≈ 25MB.
- [ ] **Step 5: 커밋** — `feat(stakeholder): 소스 repo git-sync(blob sha 대조·허용목록·fail-closed) + 스냅샷 멱등 import + 무결성 판정 + CLI`

**완료 기준:** 10 테스트 통과; Step 4 리허설 수치가 기대와 일치(불일치면 사유를 커밋 메시지에); 두 번째 실행 `downloaded=0`; `import_snapshot` 안의 모든 DB 쓰기가 `store.transaction` 안에 있다(grep `conn.execute("INSERT` 가 `with store.transaction` 블록 밖에 없음).

---

### Task 6: API Blueprint + 사진 + 접근 등급 **[Opus]**

**Files:**
- Create: `api/stakeholder_routes.py`, `agents/stakeholder/photos.py`, `tests/test_stakeholder_api.py`
- Modify: `api/server.py` — `app.register_blueprint(build_auth_blueprint(users_db))` 다음 줄에 `from api.stakeholder_routes import build_stakeholder_blueprint` + `app.register_blueprint(build_stakeholder_blueprint())` (2줄; 모듈 import 실패 시 `logger.warning` 후 비활성 — `service_requests` 선례 `server.py:6521-6524`)

**API 계약** (T7a/T7b 는 이 표로 구현한다)

| 메서드·경로 | 권한 | 응답/동작 |
|---|---|---|
| `GET /api/stakeholder/overview` | user | `{scope:{start,end}, kpi:{events, statements, quotes, speakers_observed, people_active, people_priority(importance≥5), people_candidates, pending_proposals}, latest_monitoring_run:{run_id, run_date, run_status, saturation_status, active_people, approved_domains, new_quotes}, latest_audit_run:{audit_run_id, run_date, run_status, claim, why_this_matters, proposals_created}, recent_events:[{event_id, event_date, title, topics, speakers}] (3), integrity:{sources_verified, sources_total, mismatch, missing, last_sync_at}, copyright_notice}` |
| `GET /api/stakeholder/events?topic=&org=&grade=&from=&to=` | user | `{items:[event + statements_count + headline:{speaker_name, person, quote_text, evidence_grade}]}` 최신순 |
| `GET /api/stakeholder/statements?topic=&org=&grade=&person_id=&q=&limit=` | user | `{items:[{statement_id, event:{event_id,event_date,title}, topic, speaker_name, speaker_role_at_event, person:{person_id,name,photo:bool}\|null, evidence_grade, grade_class:'a'\|'b'\|'c'\|'role', headline_quote, summary_text, quotes_count, source:{source_id, publisher_title, url, publication_date, integrity_status}}]}` — `org` 는 speaker_role_at_event/person.organization 에 `보건복지부|심평원|공단` 매칭(서버 측 `ORG_PATTERNS`) |
| `GET /api/stakeholder/statements/<id>` | user | 블록 전체 + `quotes:[{quote_id, quote_text, segments, locator, locator_match, topics}]` + `provenance:[{source_id, title, url, source_type, evidence_grade, publication_date, sha256, text_sha256, integrity_status, last_live_check}]` + `excerpt:{source_id, locator, text}`(첫 locator 창, ≤600자) |
| `GET /api/stakeholder/people?status=&q=` | user | `{active_effective_count, updated_at, items:[public 필드 + has_photo + statements_count + importance_score]}` (`photo_url` **제외**) |
| `GET /api/stakeholder/people/<id>` | user | + `role_history`, `recent_statements`(5). admin 이면 `photo_url`(메타) 포함 |
| `GET /api/stakeholder/people/<id>/photo` | user | `image/webp`(볼륨) / 404 |
| `GET /api/stakeholder/sources` | user | manifest 목록(`local_path`·`text_path` 는 **admin 에게만**) |
| `GET /api/stakeholder/sources/<id>/excerpt?locator=path:a-b` | user | `{text, start, end, chars}` ≤600자; locator 의 path 가 그 source 의 `text_path` 와 다르면 400 |
| `GET /api/stakeholder/sources/<id>/text` | admin | 추출 전문 `text/plain` |
| `GET /api/stakeholder/sources/<id>/raw` | admin | 원본 다운로드(`local_path`), `Content-Disposition: attachment` |
| `GET /api/stakeholder/artifacts/relationship-map.svg` | user | 볼륨 SVG(`sv_artifact` 중 `.svg`) / 404 |
| `GET /api/stakeholder/runs?kind=monitoring\|audit&limit=` | user | 요약 목록(payload 제외) |
| `GET /api/stakeholder/runs/<kind>/<run_id>` | user | `payload`(전체 JSON) + `md`(볼륨 .md 본문) |
| `POST /api/admin/stakeholder/people` / `PUT …/<id>` / `PATCH …/<id>/status` | admin | store.create/update/set_status; 409/422 는 `{error, code}` |
| `POST /api/admin/stakeholder/people/<id>/photo` (multipart `photo`) / `DELETE` | admin | photos.normalize → 저장; 415/413 |
| `GET /api/admin/stakeholder/proposals?status=&type=` | admin | `{pending_count, updated_at, proposals:[... + events]}` |
| `POST /api/admin/stakeholder/proposals/<id>/approve\|reject` `{reason}` | admin | approval.approve/reject; `ApprovalError` → 그 status |
| `GET /api/admin/stakeholder/source-domains` | admin | `{effective_count, items}` |
| `GET /api/admin/stakeholder/integrity` | admin | `sv_meta.integrity_summary_json` + mismatch 목록 |
| `POST /api/admin/stakeholder/sync` | admin | `runbooks.run("stakeholder_data_sync")` 결과(T8 뒤 활성; 그 전엔 503 `{code:'RUNBOOK_MISSING'}`) |
| `GET /api/agent/stakeholder/registry` | agent | `store.registry_snapshot(today_kst())` |
| `POST /api/agent/stakeholder/proposals` | agent | `approval.create_proposal(body, origin='agent_api')` → 201 / 409 / 422 |
| `GET /api/agent/stakeholder/proposals?audit_run_id=` | agent | 그 run 의 제안 목록 |

공통: 쓰기 라우트는 `request.user["sub"]` 를 actor/reviewer 로; 모든 쓰기에 `X-MA-Smoke` 가드 불필요(GET 만 스모크). JSON 오류 형식 `{error, code}`(기존 관례).

- [ ] **Step 1: 실패 테스트** — Flask test client(`api.server.app` import 는 무겁다 → **Blueprint 를 독립 `Flask()` 에 등록**해 테스트; `require_auth` 는 실제 JWT 발급 `api.auth._issue_token(email, role)` 사용), 임시 DB·임시 root 는 `monkeypatch.setenv("STAKEHOLDER_ROOT")` + `build_stakeholder_blueprint(db_path=tmp)`.

```python
# tests/test_stakeholder_api.py (발췌 — 전체는 아래 목록대로)
import io
from flask import Flask
from api.auth import _issue_token
from api.stakeholder_routes import build_stakeholder_blueprint
from agents.stakeholder import sync

def _app(tmp_path, monkeypatch):
    root, db = tmp_path / "root", tmp_path / "s.db"
    monkeypatch.setenv("STAKEHOLDER_ROOT", str(root))
    sync.sync(root, db, source_dir=FIX, today=date(2026, 10, 12))
    app = Flask(__name__); app.register_blueprint(build_stakeholder_blueprint(db_path=db))
    return app.test_client(), db

H = lambda role: {"Authorization": f"Bearer {_issue_token(f'{role}@x', role)}"}

def test_auth_gates(tmp_path, monkeypatch):
    c, _ = _app(tmp_path, monkeypatch)
    assert c.get("/api/stakeholder/overview").status_code == 401
    assert c.get("/api/stakeholder/overview", headers=H("agent")).status_code == 403          # agent 는 user 라우트 금지
    assert c.get("/api/admin/stakeholder/proposals", headers=H("user")).status_code == 403
    assert c.get("/api/agent/stakeholder/registry", headers=H("user")).status_code == 403
    assert c.get("/api/agent/stakeholder/registry", headers=H("agent")).status_code == 200
    assert c.get("/api/stakeholder/sources/SRC-FAKE-1/text", headers=H("user")).status_code == 403
    assert c.get("/api/stakeholder/sources/SRC-FAKE-1/text", headers=H("admin")).status_code == 200

def test_user_sees_excerpt_not_full_text_and_no_photo_url(tmp_path, monkeypatch): ...
def test_overview_kpis_and_latest_runs(tmp_path, monkeypatch): ...
def test_statement_detail_has_provenance_and_integrity(tmp_path, monkeypatch): ...
def test_agent_registry_matches_snapshot_and_proposal_post_201_409_422(tmp_path, monkeypatch): ...
def test_admin_people_crud_and_photo_roundtrip(tmp_path, monkeypatch):
    # 1×1 PNG 업로드 → 200 → GET photo 200 image/webp → DELETE → 404; 6MB 더미 → 413; text/plain → 415
def test_admin_approve_reject_with_reviewer_email(tmp_path, monkeypatch): ...
def test_excerpt_locator_must_match_source(tmp_path, monkeypatch): ...
```

- [ ] **Step 2: 실패 확인** · **Step 3: 구현** — `photos.py`: `normalize(raw: bytes) -> bytes`(Pillow: `Image.open`→`verify`→재open→`convert("RGB")`→`ImageOps.exif_transpose`→`ImageOps.fit((640,640), LANCZOS, centering=(0.5,0.42))`→WEBP q88 method6, 메타 없음), `save_person_photo(person_id, name, raw, root) -> rel_path`, `delete_photo(rel_path, root)`. 라우트: `send_file` 로 서빙, 경로는 `root/photos/` 아래로 `resolve()` 검증. 발췌: `locator` 파싱은 `canonical_parse.parse_locator` 재사용.
- [ ] **Step 4: 서버 통합 확인** — `.venv/bin/python -c "import api.server"` 가 경고 없이 import; `.venv/bin/python -m pytest tests/test_health_endpoint.py tests/test_agent_api.py -q` 기존 통과 유지.
- [ ] **Step 5: 커밋** — `feat(stakeholder): API Blueprint — user 읽기/admin 운영/agent 제안 채널, 사진 업로드(WEBP 정규화), 원문 접근 등급`

**완료 기준:** 8 테스트 통과; 사용자 응답 어디에도 `photo_url`·`local_path`·`text_path` 가 없다(테스트가 JSON 전체를 재귀 검사); agent 토큰은 `/api/agent/stakeholder/*` 외 전부 403.

---

### Task 7a: 프론트 — Stakeholder Signals(타임라인·워크벤치) **[Opus]**

**Files:**
- Create: `frontend/src/api/stakeholder.ts`, `frontend/src/pages/stakeholder-signals/page.tsx`, `frontend/src/pages/stakeholder-signals/workbench.tsx`, `frontend/src/pages/stakeholder-signals/components/{StatementDrawer,GradeBadge,PersonAvatar,TopicTabs,RelationshipMapModal}.tsx`
- Modify: `frontend/src/router/config.tsx`(+2 라우트), `frontend/src/components/feature/Sidebar.tsx`(`allNavItems` 에 `{ path: '/stakeholder-signals', label: 'Stakeholder Signals', icon: 'ri-user-voice-line' }` — `/analog-search` 다음)

- [ ] **Step 1: API 모듈** — §T6 계약의 타입 전부(`SVOverview, SVEvent, SVStatementRow, SVStatementDetail, SVPerson, SVRun, SVProposal, SVSourceDomain`)와 함수(`fetchOverview, fetchStatements(params), fetchStatement(id), fetchEvents, fetchPeople, fetchPerson, personPhotoUrl(id), fetchRuns, fetchRun, fetchSources, fetchExcerpt, relationshipMapUrl`, admin: `adminListProposals, adminApprove, adminReject, adminListPeople, adminCreatePerson, adminUpdatePerson, adminSetStatus, adminUploadPhoto(FormData — `fetch` 직접, Authorization 헤더), adminDeletePhoto, adminSourceDomains, adminIntegrity, adminSync`). 방어적 optional 타이핑(serviceRequests.ts 선례).
- [ ] **Step 2: 타임라인 페이지(001)** — 레이아웃: 상단 헤더(제목 "정책 발언 인텔리전스" / 부제 / 우측 스탬프 "마지막 sync · 출처 무결성 n/m") + 뷰 탭(타임라인 | 워크벤치 = `<Link>`) + "관계도" 버튼(모달에 `<img src=relationshipMapUrl()>` — Authorization 필요하므로 `fetch` → `blob` → objectURL) · KPI 4 카드(포착 행사 / 확인 발언(quotes, 원문 대조 verified/total) / 핵심 인물(active, 우선 n명) / 후속 확인 필요(candidates + pending_proposals)) · 시그널 카드(최근 감사 `claim` + `why_this_matters` + run_status 배지 / 우측 최근 행사 3) · 주제 탭(전체·5주제) + 증거등급 범례 · 행 테이블(행사일·주제·행사·인물(아바타=내부 사진 or 이니셜)·확인된 발언(headline_quote 또는 summary 이탤릭)·등급 배지, 행 클릭/Enter → 드로어) · `StatementDrawer`(인용 블록들, 메타: 행사·행사일·당시 직책·판단(analysis)·한계(limitation)·제도상태, 출처 감사: publisher/title·게시일·URL·locator·SHA-256 일치 배지(`integrity_status==='verified'`)·"원문 발췌 보기"(excerpt 600자) ; 각주 copyright_notice).
- [ ] **Step 3: 워크벤치(002)** — 좌 필터(기관 체크 3 + 주제 5 + 증거 A/B/C/Role-only + 기간 표시) / 중앙 주제 레인(topic 별 `section`, 행 카드: 날짜·인물·statement headline(summary 첫 문장)·등급 pill) / 우측 상세(정책적 의미=analysis, 해석 한계=limitation, 증거 행: 행사일·게시일·출처 유형·무결성, 버튼 "원문 및 인용 위치 열기" → 발췌 패널). 모바일: 상세는 모달.
- [ ] **Step 4: 빌드** — `cd frontend && npm run build` 통과; `npm run lint` 있으면 통과.
- [ ] **Step 5: 커밋** — `feat(stakeholder-ui): Stakeholder Signals 타임라인·워크벤치 페이지 + API 모듈`

**완료 기준:** 빌드 통과; 페이지가 API 오류 시 빈 상태 메시지(스켈레톤→"불러오지 못했습니다")를 그린다; `photo_url`·언론 CDN 을 참조하는 코드가 없다(`grep -rn "cdn\.\|photo_url" frontend/src/pages/stakeholder-signals` → 0).

---

### Task 7b: 프론트 — 인물 관리·모니터링 감사(admin) **[Opus]**

**Files:**
- Create: `frontend/src/pages/admin/stakeholder-people/page.tsx`, `frontend/src/pages/admin/stakeholder-audit/page.tsx`
- Modify: `router/config.tsx`(+2), `Sidebar.tsx`(`adminNavItems` 에 `모니터링 인물 관리`(`ri-contacts-line`) · `모니터링 감사`(`ri-shield-check-line`) + 미검토 제안 배지: `fetchPendingProposalCount()` 를 서비스요청 배지와 같은 `useEffect` 패턴, admin 만)

- [ ] **Step 1: 인물 관리(003)** — `fetchMe()` 셀프 가드(`pages/admin/service-requests/page.tsx` 와 동일) · 헤더 + "+ 인물 등록" · 상태 카드(현재 유효 활성 인물 n / 레지스트리 갱신시각) · 검색 + 상태 칩(전체/활성/일시중지) · 행 테이블(인물(아바타·이름·verified/candidate 표시)·기관·부서·직책·상태 배지·최근 변경·더보기) · 드로어(프로필·상세 그리드·role_history·"익일 07:00 반영" 고지·버튼: 편집/일시중지·재개/사진 업로드·삭제) · 등록·편집 모달(사진 업로드 프리뷰 — JPG/PNG/WEBP ≤5MB 안내, 이름·기관(datalist 3기관)·부서·직책·상태·확인상태(`verified` 옵션 disabled + 설명 "감사 근거 승인으로만")·별칭) · 409/422 메시지 그대로 표시 · 토스트.
- [ ] **Step 2: 모니터링 감사(004)** — 헤더 + 고지 "승인 변경은 익일부터 적용" · KPI(검토 필요 n / 승인 출처 n / 최근 sync) · 원칙 3카드(정책 문구) · 유형 칩(검토 필요/전체/매체 확장/직책 변경/신규 인물) · 제안 카드(유형·상태 배지, summary, 메타(priority·created_by·created_at·effective_from), rationale(pre-wrap), proposed_change 키-값, evidence 링크 목록(publisher·level·source_class·quoted_text), conflicts 경고 박스, decision_reason, 승인/반려 버튼 → 사유 입력 모달(prompt 금지 — 기존 모달 패턴) ) · **런 탭**(모니터링/감사 최근 10건: 날짜·상태·saturation·건수, 클릭 시 MD 본문 모달 — `react-markdown` 이 프로젝트에 있으면 사용, 없으면 `<pre>`) · 무결성 패널(`adminIntegrity`) · "지금 sync" 버튼(`adminSync`, 결과 토스트).
- [ ] **Step 3: 빌드·커밋** — `feat(stakeholder-ui): 모니터링 인물 관리·모니터링 감사 admin 페이지 + 사이드바 배지`

**완료 기준:** 빌드 통과; 비관리자로 `/admin/stakeholder-*` 진입 시 홈으로 리다이렉트(fetchMe 가드); 승인·반려가 사유 2자 미만이면 클라이언트에서 막는다.

---

### Task 8: 스케줄러 잡·런북·신선도·스모크 **[Opus]**

**Files:**
- Create: `tests/test_stakeholder_jobs.py`
- Modify: `scheduler.py`, `agents/agent_tasks/signals.py`(JOB_RUNBOOK 1행), `agents/agent_tasks/runbooks.py`, `agents/agent_tasks/freshness.py`, `scripts/smoke.py`, `tests/test_smoke_screens.py:67`

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_stakeholder_jobs.py
from datetime import date
import sqlite3
import scheduler as sch
from agents.agent_tasks import freshness as fr, runbooks
from agents.agent_tasks.signals import JOB_RUNBOOK
import scripts.smoke as smoke

def test_job_registered_guarded_and_runbook_mapped():
    specs = {s.id: s for s in sch.build_job_specs({"day": "1-7", "hour": 9, "minute": 0})}
    s = specs["stakeholder_data_sync"]
    assert getattr(s.fn, "__job_id__") == "stakeholder_data_sync" and JOB_RUNBOOK["stakeholder_data_sync"] == "stakeholder_data_sync"
    assert "stakeholder_data_sync" in runbooks.RUNBOOKS
    assert str(s.trigger.fields[5]) == "2,8" and str(s.trigger.fields[6]) == "20"      # hour, minute

def test_job_maps_sync_result_to_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TASK_DB", str(tmp_path / "a.db"))
    monkeypatch.setattr("agents.stakeholder.sync.sync", lambda *a, **k: {"ok": False, "error": "auth required: x", "error_class": "config", "counts": {}})
    res = sch.stakeholder_data_sync_job()
    assert res["ok"] is False and res["error_class"] == "config"
    from agents.agent_tasks.store import list_tasks
    assert list_tasks(db_path=tmp_path / "a.db")[0]["source_ref"] == "stakeholder_data_sync:config"

def _seed(db, *sql):
    c = sqlite3.connect(db); [c.executescript(s) for s in sql]; c.commit(); c.close()

def _ctx(tmp_path, today=date(2026, 10, 12)):
    db = tmp_path / "d.db"; sqlite3.connect(db).close()
    return fr.Ctx(db_path=db, base_dir=tmp_path, today=today)

def test_freshness_monitoring_and_audit_runs(tmp_path):
    _seed(tmp_path / "d.db", "CREATE TABLE sv_monitoring_run (run_date TEXT); INSERT INTO sv_monitoring_run VALUES ('2026-10-09');",
          "CREATE TABLE sv_audit_run (run_date TEXT); INSERT INTO sv_audit_run VALUES ('2026-10-10');")
    r = fr.check_stakeholder_monitoring_run(_ctx(tmp_path))
    assert r.stale is True and r.deadline == "2026-10-10" and r.runbook == "stakeholder_data_sync" and r.kind == "ops_incident"
    assert fr.check_stakeholder_audit_run(_ctx(tmp_path)).stale is False               # 3일 허용

def test_freshness_backlog_is_improvement_and_integrity_is_incident(tmp_path):
    _seed(tmp_path / "d.db", "CREATE TABLE sv_audit_proposal (status TEXT, created_at TEXT);"
          "INSERT INTO sv_audit_proposal VALUES ('needs_review','2026-10-01T07:00:00+09:00');"
          "INSERT INTO sv_audit_proposal VALUES ('needs_review','2026-10-11T07:00:00+09:00');",
          "CREATE TABLE sv_source (integrity_status TEXT); INSERT INTO sv_source VALUES ('mismatch');")
    b = fr.check_stakeholder_proposal_backlog(_ctx(tmp_path))
    assert b.stale and b.kind == "improvement" and b.runbook is None and "1건" in b.evidence
    i = fr.check_stakeholder_source_integrity(_ctx(tmp_path))
    assert i.stale and i.kind == "ops_incident" and i.runbook == "stakeholder_data_sync" and i.priority == 3

def test_domain_checks_registered():
    for d in ("stakeholder_monitoring_run", "stakeholder_audit_run", "stakeholder_proposal_backlog", "stakeholder_source_integrity"):
        assert d in fr.DOMAIN_CHECKS

def test_smoke_screens_added():
    names = {s.name for s in smoke.SCREENS}
    assert {"stakeholder-signals", "stakeholder-signals/workbench", "admin/stakeholder-people", "admin/stakeholder-audit"} <= names
    gates = {c.path for s in smoke.SCREENS if s.name == "auth-gates" for c in s.checks}
    assert "/api/admin/stakeholder/proposals" in gates and "/api/agent/stakeholder/registry" in gates
```

- [ ] **Step 2: 구현**
  - `scheduler.py`: 다른 잡 함수 옆에
    ```python
    @guarded_job("stakeholder_data_sync")
    def stakeholder_data_sync_job():
        """매일 02:20·08:20 — Stakeholder 소스 repo(kr-drug-pricing-nhi-finance-intelligence) git-sync + 스냅샷 적재 + 스케줄 반영.
        토큰 부재(prod fail-closed)는 config 과제 1건(stakeholder_data_sync:config)."""
        from agents.stakeholder import sync as _sv_sync
        from agents.stakeholder.paths import stakeholder_root
        res = _sv_sync.sync(stakeholder_root(), _sv_sync.DEFAULT_DB_PATH)
        if res.get("error"):
            return {"ok": False, "counts": res.get("counts") or {}, "errors": [str(res["error"])],
                    "error_class": res.get("error_class") or "transient"}
        return {"ok": bool(res.get("ok")), "counts": res.get("counts") or {}, "errors": res.get("errors") or []}
    ```
    `build_job_specs` 에 `JobSpec("stakeholder_data_sync", "Stakeholder 소스 repo git-sync (02:20·08:20, 런·원문·정본 + 스케줄 반영)", stakeholder_data_sync_job, _cron(hour="2,8", minute=20))`; CLI `--stakeholder-sync-now`.
  - `signals.py`: `"stakeholder_data_sync": "stakeholder_data_sync",`
  - `runbooks.py`: `def _runbook_stakeholder_data_sync(): return _from_job("stakeholder_data_sync_job", "Stakeholder sync")` + `RUNBOOKS.update({...})` 1행.
  - `freshness.py`: 상수 `STAKEHOLDER_MONITORING_MAX_DAYS=2, STAKEHOLDER_AUDIT_MAX_DAYS=3, STAKEHOLDER_PROPOSAL_REVIEW_DAYS=7`; 체크 4개(§Q6; `_max_ts` 헬퍼 재사용, backlog 는 `kind="improvement"`, evidence 에 "담당: admin — 모니터링 감사 화면에서 승인·반려" 명시); `DOMAIN_CHECKS` 4행.
  - `scripts/smoke.py`: Screen 4개 + auth-gates 2 Check; `tests/test_smoke_screens.py:67` `== 22` → `== 26`, 그리고 `:92` 의 가짜 응답 맵(`routes = {c.path: (200, …)}`)에 새 `_has_key` 체크 경로의 본문(`{"kpi": {}}`, `{"items": []}`, `{"proposals": []}`)을 추가해 `:126` 의 `passed == 전체 체크 수` 가 유지되게 한다.
- [ ] **Step 3: 회귀** — `.venv/bin/python -m pytest tests/test_stakeholder_jobs.py tests/test_scheduler_job_specs.py tests/test_domain_freshness.py tests/test_freshness_platform_checks.py tests/test_smoke_screens.py tests/test_agent_runbooks.py -q` 전부 통과.
- [ ] **Step 4: 커밋** — `feat(agent-team): stakeholder_data_sync 잡·런북·신선도 4종·스모크 4화면`

**완료 기준:** 위 회귀 묶음 통과; `JOB_RUNBOOK` 키 집합 == 등록 잡 id(기존 테스트); 새 신선도 체크가 테이블 부재 시 "판정 불가" 로 강등(기존 `run_all` 규약 — 테스트 1개 추가).

---

### Task 9: 문서 — 외부 에이전트 계약·운영 런북·API 계약 **[Sonnet]**

**Files:**
- Create: `agents/ingest/STAKEHOLDER_SYNC_README.md`, `docs/agent_team/STAKEHOLDER_RUNBOOK.md`
- Modify: `docs/api_contract.md`(섹션 "Stakeholder Signals" — §T6 표 그대로)

- [ ] `STAKEHOLDER_SYNC_README.md`(REIMB_DATA_README 형식): ① 채널 그림(에이전트 → 소스 repo 커밋 → 02:20/08:20 sync → 볼륨+DB → 화면), ② 에이전트가 커밋하는 것(런·원문·manifest·정본 MD — **`data/*.json` 은 더 이상 기록 원본이 아님**, 유지해도 insert-if-absent 로만 읽힘), ③ 커밋하지 말아야 할 것, ④ 에이전트가 API 로 하는 것(`GET /api/agent/stakeholder/registry` 를 07:00 실행 전에 pull 해 `scope.people_registry_sha256`=`people_sha256`; `POST /api/agent/stakeholder/proposals` — 요청·응답 예시는 `audit-runs/2026-10-10/raw/proposal_post_responses.json` 과 같은 모양, 422 시 priority 는 `normal`), ⑤ 토큰·계정(서비스 계정 발급은 `docs/agent_team/VPS_SETUP.md` §1 과 같은 절차, fly secret `STAKEHOLDER_SYNC_TOKEN`), ⑥ 손실 없음 검증 명령, ⑦ 체크리스트.
- [ ] `STAKEHOLDER_RUNBOOK.md`: 잡·신선도·스모크 표, 흔한 과제별 조치(config 토큰, mismatch, backlog), 수동 명령(`--stakeholder-sync-now`, `python -m agents.stakeholder.sync --dry-run`, `scripts/stakeholder_verify_import.py`), 볼륨 경로, 복구(볼륨 유실 시 `--now` 1회로 전량 재수신 — DB 결정 테이블은 영향 없음).
- [ ] 커밋 — `docs(stakeholder): 외부 에이전트 sync 계약·운영 런북·API 계약`

**완료 기준:** 문서의 모든 경로·명령이 실제 파일·플래그와 일치(`grep` 로 대조).

---

### Task 10: 손실 없음 검증 스크립트 + 테스트 + 실데이터 실측 **[Opus]**

**Files:**
- Create: `scripts/stakeholder_verify_import.py`, `tests/test_stakeholder_lossless.py`

- [ ] **Step 1: 스크립트** — `verify(root, db_path, source_dir=None, canonical_md=None) -> Report(failures: list[str], counts: dict)`; CLI `--root --db [--source-dir] [--json]`. 검사(§Q8 ①~④) + 추가: `sv_audit_proposal` 의 import 행은 JSON 과 **필드 단위 동일**(status·evidence·conflicts·created_at 포함), `sv_person` import 행도 동일(photo_url 포함), 런 `payload_json` == 파일 JSON(정렬 비교), `sv_meta.withheld_claims_json` == manifest. `verify_repository.py` 의 메시지 문구(`missing manifest file`, `SHA-256 mismatch`, `artifact byte-size mismatch`)를 유지.
- [ ] **Step 2: 테스트** — 픽스처로 `sync` 후 `verify` → `failures == []`; 볼륨 파일 1개 변조 → `SHA-256 mismatch` 1건; DB 에서 quote 1행 삭제 → `정본 인용 블록 미반영` 1건; `--source-dir` 미러 검사에서 허용목록 밖 파일은 비교 대상이 아님.
- [ ] **Step 3: 실데이터 실측(필수, 결과를 커밋 메시지 본문에)** —
  ```
  .venv/bin/python -m agents.stakeholder.sync --source-dir <SRC> --root /tmp/sv_root --db /tmp/sv.db
  .venv/bin/python scripts/stakeholder_verify_import.py --root /tmp/sv_root --db /tmp/sv.db --source-dir <SRC> --json
  ```
  기대: `failures: []`, counts `{people: 11, source_domains: 32, proposals: 5, sources: 22, artifacts: 10, monitoring_runs: 3, audit_runs: 2, events: 12, statements: 35, quote_blocks_md: 52, quotes_db: <=52, quotes_unmatched: 0, mirrored_files: ~370, mirror_mismatch: 0, integrity: {verified: 20, no_local: 1, mismatch: 0, missing: 0}}`. `sources: 22` 중 `no_local` 은 `SRC-BOK-NHIS-20251211-YT` 1건(video-only, 소스 README "YouTube bot verification" 한계) — 이것은 손실이 아니라 소스 상태다. `user_input` 2건은 `local_path` 가 있으므로 `verified`.
- [ ] **Step 4: 커밋** — `test(stakeholder): 손실 없음 검증 스크립트·테스트 + 실데이터 실측 결과`

**완료 기준:** 테스트 통과; 실측 `failures == []`; 실측 수치가 조사 확인 결과 #4·#5 와 일치(불일치 시 원인 규명 전 완료 금지).

---

## 병렬 묶음 요약

```
G1  T1(store) ‖ T3(parser) ‖ T4(픽스처·규칙)                 — worktree 3개
G2  T2(approval) ‖ T7a(user UI, API 계약 기반)                — T1 머지 후
G3  T5(sync) ‖ T6(API) ‖ T7b(admin UI)                        — T2·T3·T4 머지 후 (T7b 는 T7a 의 api 모듈 머지 후)
G4  T8(잡·신선도·스모크) ‖ T10(검증)                            — T5·T6 머지 후
G5  T9(문서)                                                   — T6·T8 머지 후
```
머지 순서에서 줄 충돌이 나는 파일은 `router/config.tsx`·`Sidebar.tsx`(T7a/T7b) 뿐 — 서로 다른 배열에 행 추가라 의미 충돌 없음.

## 오케스트레이터 최종 검증 (배포 전)

- [ ] 전체 테스트: `.venv/bin/python -m pytest tests -q -x` — 실패 0, `tests/conftest.py` 의 세션 fingerprint 통과(실제 DB 무변경).
- [ ] 프론트 빌드: `cd frontend && npm run build`.
- [ ] 로컬 E2E(네트워크 없음): `python -m agents.stakeholder.sync --source-dir <SRC> --root data/stakeholder --db data/db/drug_prices.db` 로 **로컬 개발 DB** 에 1회 import(로컬 DB 는 개발용 — 실제 운영 DB 는 Fly 볼륨) → `python api/server.py` → 브라우저로 4 화면 확인: KPI 12/≤52/11/… , 드로어 SHA-256 배지, 워크벤치 발췌, 인물 등록(사진), 제안 1건 반려(사유) → `scripts/smoke.py --base http://127.0.0.1:5001 --login-email … ` 로 새 Screen 4개 passed.
- [ ] `scripts/stakeholder_verify_import.py --root data/stakeholder --db data/db/drug_prices.db --source-dir <SRC>` → failures 0.
- [ ] `python scheduler.py --freshness-now` 출력에 `stakeholder_*` 4행이 `ok` 또는 의도된 `STALE`(backlog: 5건 needs_review 가 7일 넘으면 improvement 1건 — 이것은 사실).
- [ ] `QualityGuardAgent.review_codebase()` 수동 실행(CLAUDE.md 규칙 — 신규 규칙 파일 추가 시).
- [ ] `agents/rule_compliance/checks.py`: 새 메모리 항목을 추가했다면 체크 함수 또는 SKIP 등록(메모리 `project_rule_compliance_agent`).

## 배포 (사용자 확인 후 — 사용자만 실행)

1. **비밀**: `fly secrets set STAKEHOLDER_SYNC_TOKEN=<GitHub fine-grained PAT, 소스 repo Contents:read>` (+ 선택 `STAKEHOLDER_SYNC_REPO=jeonpapa/kr-drug-pricing-nhi-finance-intelligence`). 토큰 없이 배포하면 잡이 `stakeholder_data_sync:config` 과제 1건을 만들고 화면은 빈 상태 — 그래도 안전.
2. **코드**: `git push origin main` → `fly deploy` (Dockerfile 이 `requirements.txt` 의 Pillow 를 설치).
3. **볼륨 데이터(1회)** — 둘 중 하나:
   - (권장) 토큰이 있으면 `fly ssh console -C "python scheduler.py --stakeholder-sync-now"` → 첫 실행이 전량(≈25MB) 수신 + import. 이어서 `fly ssh console -C "python scripts/stakeholder_verify_import.py --root /app/data/stakeholder --db /app/data/db/drug_prices.db --json"` → `failures: []`.
   - 토큰 전이라면 `fly ssh sftp shell` 로 `<SRC>` 를 `/app/data/stakeholder_import/` 에 올린 뒤 `fly ssh console -C "python -m agents.stakeholder.sync --source-dir /app/data/stakeholder_import"` → 검증 동일 → 업로드 디렉터리 삭제.
   - 결정 테이블은 DB 에 생기므로 `apply_analog_sync` 식 테이블 교체 불필요. 재시딩 금지(메모리 `project_prod_data_deploy`).
4. **에이전트 cutover(VPS, Key/Hermes)**: ① `agent` 역할 계정 발급(`docs/agent_team/VPS_SETUP.md` §1 절차; 전용 계정 권장) ② 감사 에이전트의 제안 POST 대상을 `http://127.0.0.1:8791/api/audit/proposals` → `https://ma-ai-dossier.fly.dev/api/agent/stakeholder/proposals`(Bearer) ③ 일일 모니터링의 스콥을 `data/people_registry.json` 대신 `GET /api/agent/stakeholder/registry` 로 ④ 기존 FastAPI 서비스는 읽기 전용으로 두거나 종료. 소스 repo 커밋 흐름(런·원문·정본)은 **그대로**.
5. **확인**: 다음날 09:00 `fly ssh console -C "python scheduler.py --freshness-now"` 에 `stakeholder_*` 4행; 운영 원장(`/admin/agent-tasks`)에 `stakeholder_data_sync` 실패 과제 없음; `/stakeholder-signals` KPI 가 실측과 일치.

## 보류·미이관 (이번 범위 밖, 이유)

- `render_relationship_map.py`(Pillow 좌표 하드코딩 + VPS 폰트 절대경로) — 재렌더 불필요. SVG/PNG 산출물만 보존·표시. 데이터 기반 관계도(React)는 후속.
- 스케치 001 "+ 행사 업데이트"·002 "검증 큐"·"내보내기" — 발언 정본은 정책상 외부 에이전트(원문 대조·QC 게이트)가 쓰는 문서다. 대쉬보드 편집을 열면 verbatim 원칙의 책임 주체가 둘이 된다. 후속 단계에서 "검토 요청(proposal_type=statement_correction)" 형태로 같은 승인 게이트에 얹는 것을 제안.
- YouTube 자막 공백(`SRC-BOK-NHIS-20251211-YT`, 보강조사 §4) — 소스 한계 그대로 `no_local` 표시. 수집은 소스 에이전트 몫.
- 런 raw(`*.xml`, 일 ≈400KB) 보존 기간 — 전량 보존으로 시작(연 ≈150MB, 볼륨 3GB). 180일 정리 잡은 사용자 결정 후.
- `monitoring-runs` 의 `search_log`·`coverage_matrix` 전용 테이블·화면 — payload_json 안에 보존, 런 상세 모달에서 열람 가능. 집계 UI 는 후속.

## 사용자 결정 필요

1. **소스 repo 토큰**: `jeonpapa/kr-drug-pricing-nhi-finance-intelligence` 가 비공개라면 Contents:read fine-grained PAT 을 `STAKEHOLDER_SYNC_TOKEN` 으로 발급해 주셔야 프로덕션 sync 가 돈다(fail-closed). 공개 repo 라면 토큰 없이도 동작하게 할지(현 설계는 prod 에서 토큰 필수 — policy sync 와 동일) 결정.
2. **감사 에이전트 서비스 계정**: 기존 VPS 디스패처 `agent` 계정 공유 vs 전용 계정(예: `stakeholder-auditor@internal`). 전용을 권장(권한 추적·회수 단위).
3. **사진**: 현재 4명(김민정·이숙현·이윤학·오세림 — `photo_url` 에 언론 CDN 링크 보유)의 언론 CDN 사진은 표시하지 않는다. 내부 사진을 admin 이 업로드할 것인지, 이니셜 아바타로 둘지.
4. **대쉬보드에서의 발언 편집**: 보류 항목대로 후속 단계로 미룰지, 이번에 "정정 제안" 유형만이라도 넣을지.
5. **raw XML 보존 기간**: 전량 보존(기본) vs 180일.
