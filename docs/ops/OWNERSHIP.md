# 운영 책임 매트릭스 (OWNERSHIP) — 서버 크론 · Aside · Hermes

> 기준일 2026-10-17. 운영 모델은 사용자 결정(2026-10-17)이다. 코드 근거: `scheduler.py:build_job_specs`,
> `agents/agent_tasks/{owner,signals,freshness,runbooks,store}.py`, `api/server.py`(`/api/agent/*`, `/api/admin/agent-tasks`),
> `scripts/agent_dispatcher.py`, `docs/price_request_worker.md`, `agents/ingest/*README*.md`.

## 0. 세 담당

| 담당 (`owner`) | 누구 | 하는 일 | 하지 않는 일 |
|---|---|---|---|
| **서버 크론** (`server`) | 프로덕션 Fly `ma-ai-dossier`, `scheduler.py` APScheduler | 서버 안에서 끝까지 도는 단순·신뢰 가능한 크론: 공개 소스 수집, git repo sync, 스모크, 신선도, QG/RC, 메일 발송 판정. 실패하면 결정적 런북(`runbooks.py`)으로 복구 | 판단·큐레이션, 로그인 소스 해외약가 수집 |
| **Aside** (`aside`) | 사용자 노트북의 Aside 브라우저 에이전트 | 해외약가 전부: A8(JP IT FR CH UK DE CA US)·OECD/공단 추가국가 가격 요청 처리, 증빙 캡처, 로그인 소스(Micromedex·Rote Liste/DocCheck·MIMS), JP/CH 접속 차단 우회(노트북 IP), 결과 메일 | 서버 런북 실행, 해외약가 외 원장 과제 |
| **Hermes** (`hermes`) | 외부 VPS Hermes 에이전트 팀 | 판단이 필요하거나 크론이 아닌 일: 위원회·HIRA 보고서 분석, KRPIA 정책 ingest+큐레이션, Stakeholder 모니터링·감사, Daily Mailing 큐레이션, 경쟁사 동향 큐레이션, 원장 사고 처리(디스패처·런북), 사람(admin) 몫 적체 정리·요청 | 해외약가(Aside 몫), 프로덕션 DB 직접 쓰기, 배포 |

Hermes 가 커밋하는 저장소: `jeonpapa/AccessRoutineAnalystic`(비공개 — 위원회 데이터·보고서·정책 사이드카·메일링 번들)과
`jeonpapa/kr-drug-pricing-nhi-finance-intelligence`(Stakeholder 소스). 프로덕션은 이 저장소를 **읽어 가기만** 한다(sync 잡).

## 1. 원장 과제의 담당 라우팅 (코드)

과제(`agent_task`)의 담당은 **저장 컬럼이 아니라** `agents/agent_tasks/owner.py:owner_for(source, source_ref, detail)` 가 결정적으로 계산한다
(마이그레이션 없음 — 기존 행도 즉시 분류, 규칙 변경이 원장 전체에 일관 적용). 판정 순서(첫 일치):

1. **aside** — source `foreign_search` · `price_request*` · `foreign_price*`, `scheduler_job` 의 `foreign_config_probe:*`(JP/CH/UK 프록시·US/DE 자격증명·scrapling) · `foreign_price_backfill:*`, `domain_freshness` 의 `foreign_price_age` · `foreign_country_coverage`
2. **hermes** — `source_ref` 에 `:config`(토큰·권한·secret — 재실행 무효, Hermes 가 원인 정리 후 사람에게 요청)
3. **server** — detail 의 `런북: <이름>` 이 등록 런북(`runbooks.list_runbooks()`)
4. **hermes** — 그 외 전부(QG 제안·회귀·drift, 런북 없는 RC FAIL, known_debt, 놓친 실행, 런북 없는 잡 실패·신선도·메일링 문제)

API·권한:

| 경로 | 역할 | 담당 범위 |
|---|---|---|
| `GET /api/agent/tasks?owner=hermes[,server]` | `agent` (Hermes 디스패처) | 읽기 전부, `?owner=` 로 필터. 선점·회신은 `server`·`hermes` 만 |
| `GET /api/agent/tasks?owner=aside` | `price_agent` (Aside) | **aside 만**. `owner` 생략 시 aside 로 고정, 다른 owner 요청은 403 |
| `POST /api/agent/tasks/<id>/claim`, `/resolve` | `agent`·`price_agent` | 역할 밖 담당이면 **409 `OWNER_MISMATCH`**(구버전 디스패처도 '선점 실패' 로 넘어감) |
| `POST /api/agent/tasks/<id>/runbook` | `agent` 만 | price_agent 는 서버 런북을 돌리지 않는다(403) |
| `GET /api/admin/agent-tasks[?owner=]` | admin | 모든 과제 + `owner`, `owners`, `activity.waiting_by_owner`. 화면 `/admin/agent-tasks` 에 **담당** 열과 담당 필터 칩 |

**Aside 최소 권한 결정**: 새 계정을 만들지 않고 기존 `price_agent` 서비스 계정(`PRICE_AGENT_EMAIL`·`PRICE_AGENT_PASSWORD`)을 쓴다.
`agent` 계정을 노트북에 두면 Hermes 몫·서버 런북까지 선점할 수 있으므로 쓰지 않는다. price_agent 는 원장에서 aside 과제의
조회·선점·회신만 가능하고, 일반 사용자·admin 라우트는 여전히 403(`api/auth.py:SERVICE_ROLES`).

디스패처: `scripts/agent_dispatcher.py --owner hermes`(기본, 환경변수 `MA_AGENT_OWNER`). 서버 런북 과제까지 트리거하려면
`--owner hermes,server` — **현재 프로덕션 안에 런북 자동 실행기는 없으므로 VPS 크론은 `--owner hermes,server` 로 돈다**
(`docs/agent_team/VPS_SETUP.md` §4). 디스패처는 `?owner=` 로 받고, 받은 행의 `owner` 를 다시 확인하고, 서버도 409 로 막는 3중 가드라
Aside 와 Hermes 가 같은 과제를 집지 않는다.

신선도 과제(`domain_freshness`)는 담당이 hermes/aside 면 **제목 꼬리 + detail 첫 줄에 구체 조치**를 적는다
(`freshness.py:HERMES_ACTIONS`·`ASIDE_ACTIONS`). 예: `데이터 신선도 — stakeholder_monitoring_run stale — Hermes: stakeholder monitoring run 커밋 필요 (마지막 2026-10-11)`.
런북으로 풀리는 server 몫은 기존처럼 `런북: <이름>` 줄이 조치다.

## 2. 책임 매트릭스

시간은 KST. "건강 신호" = 신선도 키(`domain_freshness`) · 잡 이름(`scheduler_job_run`/원장 `scheduler_job:{job}:{날짜}`) · 원장 source.
자격증명은 **이름만** 적는다(값은 `config/.env`·fly secret·VPS `.env` 에만).

### 2-1. 서버 크론 (`server`)

| 도메인 / 잡 | 트리거 | 입력 채널 | 대시보드 출력 | 건강 신호 | 실패 시 | 자격증명(이름) |
|---|---|---|---|---|---|---|
| 국내 약가 `domestic_price_pipeline` | 매월 1~7일 09:00 | HIRA 공개 고시 | 국내 약가 | `domestic_price` · 잡 | 런북 `domestic_price_catchup` | (공개) `HIRA_WAP_*` 선택 |
| KEB 환율 `exchange_rate_refresh` | 매월 2일 04:00 | 하나은행 공개 | 해외약가 원화 환산 | `exchange_rate_cache`, `foreign_price_fx_alignment` | 런북 `exchange_rate_refresh`/`exchange_rate_recompute` | — |
| 해외 허가 `foreign_approval_sync` | 매일 04:30 | FDA/EMA/PMDA/MFDS 공개 | 허가 타임라인 | `foreign_approval_coverage` | 런북 `foreign_approval_sync`(분리) | — |
| 해외 급여 `reimbursement_xnational_sync` | 1·4·7·10월 1일 02:00 | NICE/PBAC/CMS/中医協 | 국가 카드 급여 축 | `reimbursement_xnational` | 런북 `reimbursement_xnational_sync` | — |
| 뉴스 `competitor_news_weekly`/`_daily` | 월 03:30 / 매일 06:30 | 공개 뉴스 | 뉴스 아카이브 | `news_archive` | 런북 `news_weekly_crawl`/`news_daily_fetch` | `NAVER_API_CLIENT_ID/SECRET` |
| 동향 승격 `competitor_trend_promote` | 매일 07:00 | 뉴스 아카이브 | 경쟁사 동향 카드 | `competitor_trends` | 런북 `competitor_trend_promote` | — |
| Access Insight `access_insight_fresh_signals` | 월 04:00 | 공개 매체 | 신호 | `access_insight_signals` | 런북(분리) | — |
| NHIS 협상 `nhis_negotiation_sync` | 월 02:30 | NHIS 공개자료 | 파이프라인 보드 | `nhis_negotiations` | 런북 `nhis_negotiation_sync` | — |
| 위원회 sync `reimb_data_sync` | 매일 02:00 + 부팅 | git raw: AccessRoutineAnalystic `agents/ingest/committee_results.json`·`reports_manifest.json` | 위원회 보드·보고서 | 잡 + `committee_*` | 잡 실패면 런북 `reimb_data_sync`, 소스 미갱신이면 **Hermes** | `REIMB_DATA_URL`, `REIMB_DATA_TOKEN`, `REPORTS_DATA_URL` |
| 정책 사이드카 `policy_intel_analysis_sync` | 매일 02:10 + 부팅 | AccessRoutineAnalystic `policy_intelligence/analysis/` | Policy Intelligence | `policy_intelligence`(analysis) | 런북 `policy_intel_analysis_sync` | `POLICY_ANALYSIS_URL`, `POLICY_ANALYSIS_TOKEN` |
| Stakeholder sync `stakeholder_data_sync` | 02:20·08:20 | kr-drug-pricing-nhi-finance-intelligence `main` | Stakeholder Signals·감사 | 잡 + `stakeholder_*` | transient 런북 / `:config` 는 Hermes→사람 | `STAKEHOLDER_SYNC_REPO`, `STAKEHOLDER_SYNC_TOKEN` |
| 메일 `daily_mailing_dispatch`·`_top_news`·`_health` | 10분 / 06:40 / 09:30 | 구독 + Hermes 번들(`DAILY_MAILING_RUNS_URL`) | 메일링 칸반 | `daily_mailing_health` 원장 | `not_generated`·`send_failed`·`ready_to_send` 런북, `held`·`error`·`expired`·`gmail_token_invalid` 은 Hermes | Gmail OAuth 토큰, `OPENAI_API_KEY`, `DAILY_MAILING_RUNS_TOKEN`, `DAILY_MAILING_SCOPES_TOKEN` |
| Home 브랜드 `home_brand_related_refresh` | 월 05:00 | Naver 연관검색어 | 후보 대기 | `human_backlog` | 승인은 admin(Hermes 가 요청) | — |
| 관측 `rule_compliance_audit`·`quality_guard_review` | 05:30 / 06:00 | 코드·메모리 | `quality_guard/*.md` | `rule_compliance`·`quality_guard` 원장 | 런북 있는 RC 는 server, 나머지 Hermes | — |
| 관측 `foreign_config_probe` | 07:40 | 환경 키 이름·scrapling | — | `scheduler_job:foreign_config_probe:config:*` | **Aside** 몫(아래) | 키 이름만 확인 |
| 관측 `domain_freshness`·`platform_smoke`·`scheduler_heartbeat` | 07:45 / 07:50 / 60초 | DB·화면 API | `/admin/agent-tasks` | 각 원장 source | 런북 없음 → Hermes | `MA_SMOKE_BASE` |
| `git_auto_backup` | 00:00 | — | — | 잡 | Hermes(원인 정리) | — |

### 2-2. Aside (`aside`) — 해외약가

| 도메인 | 트리거 | 입력 채널 | 대시보드 출력 | 건강 신호 | 에스컬레이션 | 자격증명(이름) |
|---|---|---|---|---|---|---|
| 가격 요청 워커(A8·OECD/공단 추가국가) | `/international-pricing` 요청 → 큐, Aside 10분 루틴(**미활성** — 배포·실계정 e2e 후) | `/api/price-agent/requests/*` (claim·heartbeat·upload·readback·verify·notification), `scripts/price_request_client.mjs` | 해외약가 국가 카드, 요청별 `export.xlsx` | 요청 상태·lease, `foreign_price_age`, `foreign_country_coverage` | 로그인·메일 실패 → 사용자에게 알림, 완료 주장 금지 | `PRICE_API_BASE_URL`, `PRICE_AGENT_EMAIL`, `PRICE_AGENT_PASSWORD`, 결과 통지 Gmail 계정 |
| 로그인 소스 | 요청 국가별 | 브라우저 수동 로그인 | 같은 카드 | `foreign_search:US:*`·`DE:*`·`UK:*`, `foreign_config_probe:config:{US,DE}:credentials` | 좌석 제한 → 조회 후 로그아웃. MIMS 권한 제한은 계정 등급 문제 → 사용자 | `MICROMEDEX_US_USERNAME/PASSWORD`, `ROTE_LISTE_DE_USERNAME/PASSWORD`(DocCheck), `MIMS_UK_USERNAME/PASSWORD`, `VIDAL_FR_*`(선택) |
| JP/CH(UK) 접속 차단 | 서버 라이브 검색이 데이터센터 IP 로 막힘 | 노트북 IP 로 Aside 직접 조사 | 같은 카드 | `foreign_config_probe:config:{JP,CH,UK}:proxy`, `foreign_search:{JP,CH}:*` | 노트북에서도 막히면 사용자(프록시 계약 여부 결정) | `JP_MHLW_PROXY`, `CH_COMPENDIUM_PROXY`, `MIMS_UK_PROXY`, `FOREIGN_PROXY`(서버용, 선택) |
| 일괄 백필 `foreign_price_backfill` | 수동 전용(`--price-backfill-now`) | — | — | `scheduler_job:foreign_price_backfill:*` | 스케줄 금지 — 사람 확인 | — |
| 원장 aside 과제 | (향후) Aside 루틴 | `GET /api/agent/tasks?owner=aside` → claim → 조사 → `resolve` | `/admin/agent-tasks` 담당=Aside | `activity.waiting_by_owner.aside` | 해결 못 하면 `blocked` + 사유 | `price_agent` 계정(위와 동일) |

### 2-3. Hermes (`hermes`)

| 도메인 | 트리거 | 입력 채널(Hermes → 서버) | 대시보드 출력 | 건강 신호 | 에스컬레이션 | 자격증명(이름) |
|---|---|---|---|---|---|---|
| 위원회 결과(트랙 A) | 회차 D+1 | AccessRoutineAnalystic `agents/ingest/committee_results.json` 커밋 → 02:00 sync | 위원회 보드·파이프라인 | `committee_results`, `pipeline_expected_sessions`, `committee_schedule` | 일정 미확보 → admin 입력 요청 | GitHub push(AccessRoutineAnalystic) |
| 위원회 보고서(트랙 B, HIRA 분석) | D-2 / D+1 / 월간 | PDF + `agents/ingest/reports_manifest.json` 커밋 | 보고서 모달 | `committee_reports` | — | 같음 |
| Policy Intelligence(KRPIA) | 메일 수신 시 | `python -m agents.policy_intelligence_ingest` → prod 볼륨 `/app/data/policy_intelligence/` 업로드 + `policy_intelligence/analysis/*.json` 커밋 | Policy Intelligence | `policy_intelligence`(ingest `ran_at`) | 새 topic 은 `data_gaps` 만 | GitHub push, prod 볼륨 업로드 권한(파일럿 방식), KRPIA 메일 수신함 |
| Stakeholder 모니터링·감사 | 07:00 모니터링 / 07:40 감사 | kr-drug-pricing-nhi-finance-intelligence `monitoring-runs/`·`audit-runs/`·`sources/`·manifest·정본 MD 커밋; `GET /api/agent/stakeholder/registry`, `POST /api/agent/stakeholder/proposals` | Stakeholder Signals, `/admin/stakeholder-audit` | `stakeholder_monitoring_run`, `stakeholder_audit_run`, `stakeholder_source_integrity`, `stakeholder_quote_provenance`, `stakeholder_proposal_backlog` | 승인·반려는 admin | GitHub push(kr-drug-…), `MA_AGENT_EMAIL/PASSWORD`(agent) |
| Daily Mailing 큐레이션 | 구독 일정 | run 번들 → `DAILY_MAILING_RUNS_URL` 저장소, scope export 수신 | 메일링 칸반·발송 | `daily_mailing_health`(held·error·expired), 메일 잡 | Gmail 토큰 재발급은 사람 | GitHub push(번들 저장소) |
| 경쟁사 동향 큐레이션 | 주간 | (채널 미정) 현재는 서버 `competitor_trend_promote` + admin 편집 `/admin/competitor-trends` | 동향 카드 | `competitor_trends` | 채널 확정 필요(아래 G5) | — |
| 원장 사고 처리 | 10분(디스패처) | `scripts/agent_dispatcher.py --owner hermes,server` → `/api/agent/*` | `/admin/agent-tasks` | `activity.last_claimed_at`, `waiting_by_owner` | 런북 없음·한도 초과 → `blocked` → admin | `MA_API_BASE`, `MA_AGENT_EMAIL`, `MA_AGENT_PASSWORD` |
| QG/RC 개선·known_debt | 매일 05:30/06:00 산출 | 원장 improvement(디스패처는 집지 않음) | `/admin/agent-tasks` | `quality_guard`·`rule_compliance`·`known_debt` | 코드 변경은 사용자 승인 PR(메인 repo) | — |
| 사람 몫 적체 정리 | 신선도 07:45 | 원장 improvement(`*:backlog`) | — | `market_share`, `analog_reports`, `reimbursement_checklist_coverage`, `human_backlog`, `stakeholder_proposal_backlog` | admin 에게 구체 요청(과제 첫 줄의 조치) | — |

## 3. 알려진 공백 — Hermes 액션 아이템

| # | 공백 (2026-10-17 기준) | 확인 방법 | Hermes 조치 | 완료 기준 |
|---|---|---|---|---|
| G1 | Stakeholder 런 정지 — **2026-10-11 이후 모니터링·감사 런 커밋 없음** | 신선도 `stakeholder_monitoring_run`·`stakeholder_audit_run`(원인 ② = sync 정상, 미커밋) | VPS 07:00/07:40 크론·에이전트 로그 확인 → 누락일 런 재생성·커밋 | `sv_monitoring_run MAX(run_date)` ≥ 어제, 원장 과제 자동 재발 없음 |
| G2 | **KRPIA ingest 2026-06-30 이후 정지** (`latest_ingest_status.json ran_at`) | 신선도 `policy_intelligence` → `Hermes: KRPIA ingest 재개 필요` | 누적 ingest 실행 → prod 볼륨 업로드(manifest + raw) → `list-pending --since` 대상 사이드카 작성·validate·커밋 | `ran_at` ≤ 21일, 신규 이벤트가 화면에 뜨고 fingerprint 일치 |
| G3 | **감사 pre-run 스크립트 부재** — 07:40 커버리지 감사 사전 단계가 소스 repo 에 없음 | `stakeholder_audit_run` stale, 소스 repo `scripts/` | 스크립트 복구·커밋 + VPS 크론 연결, `verify_repository.py` 통과 | 감사 런이 매일 커밋, `audit_run_id`·`run_date_kst` 포함 |
| G4 | **원장 28건 미선점** — 디스패처 미가동(`last_claimed_at` 없음) | `/admin/agent-tasks` "선점 허용 · 처리 멈춤", 담당 칩별 대기 수 | VPS_SETUP §1~4 로 agent 계정·`.env`·크론 설치, 첫 실행 `--dry-run --owner hermes,server` → 계획 검토 → 실행. aside 과제는 건드리지 않는다 | `last_claimed_at` 24시간 이내, server 과제 closed/blocked 로 정리, hermes 과제는 조치 후 resolve |
| G5 | 경쟁사 동향 큐레이션 입력 채널 미정 | — | 커밋 채널(파일·스키마) 제안 → 사용자 합의 | README 1건 + sync 잡/화면 반영 |

## 4. Hermes 온보딩 체크리스트

1. `AGENTS.md`, 본 문서, `docs/agent_team/VPS_SETUP.md`, `agents/ingest/{REIMB_DATA,POLICY_INTEL_CURATION,STAKEHOLDER_SYNC}_README.md`, `docs/agent_team/STAKEHOLDER_RUNBOOK.md` 읽기.
2. 두 저장소 push 권한 확인: `jeonpapa/AccessRoutineAnalystic`(main), `jeonpapa/kr-drug-pricing-nhi-finance-intelligence`(main). 메인 repo(MA_AI_Dossier)에는 사이드카·메일 본문 발췌를 커밋하지 않는다.
3. `role=agent` 서비스 계정 발급(admin) → VPS `.env`(600)에 `MA_API_BASE`·`MA_AGENT_EMAIL`·`MA_AGENT_PASSWORD`. admin 토큰은 VPS 에 두지 않는다.
   - **2026-10-17 발급 완료**: `hermes-agent@marketintel.kr`(role=agent). 비밀번호는 운영자 노트북 `config/.env` 의 `HERMES_AGENT_PASSWORD` 에만 있다 — Slack·이메일·커밋으로 옮기지 말고 운영자가 VPS `.env` 로 직접 옮긴 뒤 노트북 항목을 지운다. 권한 검증: hermes/server 과제 조회 200, 가격요청 큐·admin 403.
4. 디스패처 `--dry-run --owner hermes,server` → `planned` 검토 → 크론 등록(flock, 10분). `--owner aside` 는 쓰지 않는다.
5. `/admin/agent-tasks` 에서 담당=Hermes 칩으로 대기 과제 확인 — 신선도 과제는 첫 줄의 `Hermes: ...` 조치를 그대로 수행하고, 끝나면 `POST /api/agent/tasks/<id>/claim` → `/resolve {"status":"closed","resolution":"<커밋 SHA·근거>"}`.
6. 일일 루틴: 07:00 모니터링 커밋 → 07:40 감사 커밋 → 07:45 신선도 결과 확인 → 위원회 D+1 결과·보고서 커밋 → KRPIA 신규 메일 ingest·큐레이션.
7. G1~G4 처리 결과를 원장 resolution 에 남긴다.

## 5. Aside 체크리스트

> **2026-10-17 사용자 결정 — 해외약가는 요청 기반 수집만 한다.** 실제 조사 요청이 들어온 제품·국가만 조사해 DB 에 누적한다.
> 자동 백필 없음, 미요청 제품의 미수집·오래된 가격·국가 공백은 결함이 아니다(`foreign_price_age`·`foreign_country_coverage` 는 관측만, 과제 없음).
> JP·CH 는 서버 프록시 대신 Aside 요청 큐 전담 — `config_probe.REQUEST_ONLY_PROXY_COUNTRIES` 로 프록시 경고 제외.

1. `docs/price_request_worker.md` + 계정 스킬 `nhis-foreign-drug-price` 읽기. 워커 루틴은 **배포 + Keytruda IT 1국가 실계정 e2e 검증 전까지 활성화하지 않는다**.
   - **2026-10-17 e2e 완료**: 요청 `d28a997a…`(KEYTRUDA·IT) claim → AIFA DET 1185/2026 4행 업로드 → readback → 화면 확인 → verify → Gmail 발송(영수증 실제 message id) → `completed`. 10분 루틴(요청 1건 → 없으면 aside 원장 1건) 활성화.
2. 자격증명은 `config/.env` 의 `PRICE_API_BASE_URL`·`PRICE_AGENT_EMAIL`·`PRICE_AGENT_PASSWORD` 만 사용(`role=price_agent`). `agent`·admin 자격증명을 노트북 루틴에 쓰지 않는다. 채팅에 노출된 사이트 자격증명(MIMS·Micromedex·Rote Liste·Vidal)은 교체 대상.
3. 원장 루틴: `node scripts/price_request_client.mjs tasks | task-claim N | task-resolve N --file` (price_agent, owner=aside 만) → 해당 국가 재조사·증빙 → `resolve`(closed | blocked + 사유). 런북 API 는 쓰지 않는다(403). price_agent 는 가격 요청을 만들 수 없으므로 재수집이 필요하면 blocked + "사용자 요청 필요: 제품·국가" 로 남긴다.
4. 로그인 소스는 조회 후 로그아웃(좌석 제한). MIMS 권한 제한·JP/CH 차단은 실제 접속으로 확인하고, 안 되면 실패 사유로 보고(임의값·추정 금지).
5. 노트북이 깨어 있어야 루틴이 돈다 — 놓친 실행은 자동 복구되지 않는다. 결과 메일은 요청 `notify_email` 로, 영수증은 실제 Gmail message id.
