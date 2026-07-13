# 대쉬보드 개선 검증 리포트 (fable 5, 2026-07-03)

4개 영역 병렬 검증(프론트 UX · 백엔드 API · 정책인텔리전스 E2E · 데이터/규칙 정합성) + prod 라이브 확인. 모든 항목 실제 코드/런타임 대조로 검증.

**총평**: 코드베이스는 대체로 견고(과거실수 6건 수정 전부 현존·테스트 통과, SQL injection 없음, 시크릿 위생 양호, 프론트 빌드 clean). 그러나 **방금 배포한 정책인텔리전스 큐레이션이 prod에서 실제로는 표시되지 않고(라이브 확인)**, **공개 repo 유출**과 **무인증 엔드포인트 클러스터**가 최우선.

---

## 🔴 즉시 — 프로덕션 라이브 이슈 (런타임 확인됨)

### P1. 헤르메스 큐레이션이 prod 대쉬보드에 표시 안 됨 (E2E 단절)
헤르메스가 첫 실제 큐레이션(event `19f257b0419c6a57`, 3.3KB)을 커밋 → sync가 prod로 가져옴(`copied=2`) → 그러나 **`curated=0`**. 원인:
- prod에 **정기 Gmail ingest 잡이 없음** — manifest는 파일럿 10건 스냅샷뿐. 큐레이션한 새 이벤트가 prod manifest에 없어 사이드카가 고아가 됨.
- fingerprint base = 원문 `message_sha256`인데 prod에 원문/raw_folder가 없으면 base 붕괴 → 설령 이벤트가 있어도 조용히 규칙 폴백. `resolve_curation`은 폴백 사유를 로깅하지 않아 `pending`과 구분 불가.
- **조치**: prod에 정기 ingest 잡 추가(02:00, 사이드카 02:10 sync 직전; 토큰 `/opt/data/google_token.json` 지원됨) **또는** 헤르메스가 manifest+원문을 prod 볼륨에 함께 적재. + `resolve_curation`에 사유(missing/stale/raw_unavailable) 반환·로깅, overview에 mismatch 카운트.
- 근거: `scheduler.py:698,873-885`(sync만 있음), `api/server.py:5686`(ingest는 수동 admin API), `agents/policy_analysis.py:48-62,84,155`.

### P2. 비공개여야 할 KRPIA 메일 발췌가 공개 GitHub에 노출
prod sync가 **토큰 없이** `raw.githubusercontent.com/jeonpapa/AccessRoutineAnalystic`에서 성공 fetch(`copied=2`) → repo가 **public**. 사이드카 `evidence_quotes` = KRPIA 메일 본문 발췌 → 세계 공개. (reimb 채널도 동일 public 의존: `REIMB_DATA_URL` no_token.)
- **조치**: repo를 **private 전환** + sync를 GitHub API + `Authorization: Bearer $GITHUB_TOKEN`(fly secret)로. 이미 push된 발췌 이력 정리. sync 실패는 `logger.warning`이 아니라 QualityGuard/deviation_log로 에스컬레이션.
- 근거: `agents/ingest/policy_analysis_sync.py:18,38-44,57-60`, `POLICY_INTEL_CURATION_README.md:24-25`.

### P3. manifest가 30일 스냅샷 — priority-1 이벤트 이미 유실
리더는 단일 manifest 소비(`policy_intelligence.py:53-73`), `run_ingest`는 `newer_than:30d` + `max_results=20`로 매번 새로 빌드(누적 병합 없음). 현재 active manifest가 **"[KRPIA] MA Briefing Session: 기등재 약제 재평가 추진계획"(핵심 priority-1) 이미 누락**(6/30 실행이 20건 cap). 8월이면 6월 이력 전부 소멸.
- **조치**: build 후 `event_id` union으로 누적 manifest 유지(사이드카는 event_id 키라 유효 유지). 근거: `policy_intelligence_ingest.py:43-46,368-397`.

---

## 🟠 보안 (공개 배포 위험, 백엔드)

| # | 항목 | 근거 | 조치 |
|---|---|---|---|
| P4 | **무인증 mutation/고비용 엔드포인트 7개** — calibrate-media(5~10분 GPT 잡), rsa-registry 쓰기/삭제, enrichment-bulk, foreign/drugs 삭제, workbench assumptions, foreign/search(라이브 8국 스크레이프) | `server.py:1433,1486,1507,1204,1763,2460,1585` | `/api/admin/*`·mutation에 `@require_auth(role="admin")`, 고비용 POST에 `@require_auth()` |
| P5 | **기본 admin 계정 자동 시드**(`admin@marketintel.kr`/`admin1234`) `ADMIN_PASSWORD` 미설정 시 매 부팅 생성 | `agents/db/users.py:39-41,57-66` | 미설정 시 시드 거부 또는 랜덤 1회 로깅. fly secret 설정 확인 |
| P6 | **JWT_SECRET 임시 fallback** — 미설정 시 프로세스마다 랜덤 → 배포·재시작마다 전 세션 무효화 | `api/auth.py:37-39` | 프로덕션 미설정 시 fail-fast, health에 노출 |
| P7 | **Werkzeug 개발서버가 프로덕션 서버**(gunicorn 없음) | `scripts/start_production.sh:36`, `server.py:5948` | `gunicorn -w2 -k gthread --threads8 api.server:app` |

---

## 🟠 데이터/규칙 정합성

| # | 항목 | 근거 | 조치 |
|---|---|---|---|
| P8 | **MFDS DISEASE_KR 키워드 누락(CMV/CMV_P/KIDTX)** — 문서화된 과거실수 재발, Prevymis CMV 공식일 unverified 방치. 감지기는 작동하나 dict 미충전 | `quality_guard/review_2026-07-02.md`, `kr_mfds_indication_mapper.py:38,322-335` | DISEASE_KR에 한국어 라벨 추가 후 `apply_mfds_official_dates` 재실행 |
| P9 | **RuleCompliance 12건 묵시적 skip + orphan check 1건** — 커버리지 규율 이완("묵시적 통과 금지" 원칙 자기위반) | `rule_compliance/agent.py:107`, `checks.py:587` | 12건 등록 또는 agent.py:107을 fail 격상; orphan check 인덱스 추가 |
| P10 | **스케줄러 misfire_grace_time 1s** → 다운타임 후 월간/분기 잡 스킵(FX stale 계열 postmortem 재발 위험) | `scheduler.py:730` | `job_defaults={"misfire_grace_time":3600,"coalesce":True}` |
| P11 | **no-op 플레이스홀더 잡 4개**가 성공처럼 로깅(D-2/D+1/월간트렌드/hira_schedule) | `scheduler.py:460,487,510,526` | 구현 또는 WARNING "NOT IMPLEMENTED" |

---

## 🟠 프론트 (사용자 체감)

| # | 항목 | 근거 | 조치 |
|---|---|---|---|
| P12 | **daily-mailing "테스트 발송" 성공 시 "발송 실패" 오표시** — 백엔드 `mode:"preview"`를 프론트가 미인식 + 존재하지 않는 스케줄 발송 문구 | `page.tsx:137-142,168,384`, `mailSubscriptions.ts:36` | `'preview'` 분기 + 미리보기 모달, 문구를 "발송 파이프라인 준비 중"으로 |
| P13 | **401 세션만료 → dead page**(토큰만 clear, 리다이렉트 없음) | `api/client.ts:55-57`, `App.tsx:14` | `clearAuth()`가 이벤트 dispatch → AuthGuard가 `/login` 리다이렉트 |
| P14 | **analog-search 에러 무피드백**(console만, "결과없음"으로 표시) | `pages/analog-search/page.tsx:576,587,617` | error 상태 + 배너 |
| P15 | **dead code 번들 포함**(product-sales 페이지+mocks 1.7K줄+미사용 i18n) + 코드분할 없음(1MB 단일 청크) | `router/config.tsx`, `mocks/*`, `App.tsx` | 삭제 + route별 `React.lazy` |

---

## 🟡 MEDIUM (묶음)

- **정책인텔**: `validate_analysis`(grounding·용어가드)가 **prod에서 미실행** — serve 게이트는 필드+fingerprint뿐(`analysis_valid` `policy_analysis.py:79-84`); `msd_implication` 하위키·VALID_SEVERITY 미검증. new_topic_candidate write-only(`resolve_curation`이 data_gaps 드롭). 위원회 큐레이션 계산되나 미렌더(`CommitteeWorkspace.tsx`). 커버리지 칩/"규칙 기본값" 배지가 "파일럿 백필"과 "AI 대기"를 혼동시킴. → **sync 단계에서 validate_analysis 실행·격리**, cutoff를 machine-readable로.
- **백엔드**: nightly `git add .`가 데이터/WIP 전량 main 커밋(`scheduler.py:538`); `agents.daily_mailing` 모듈 dangling(503 endpoint); UsersDB 단일 커넥션 스레드 공유(`users.py:51`); `int(request.args)` 미가드 500(`server.py:92,2001,…`); hta_pdf `startswith` 경로체크(`:2207`).
- **프론트**: admin 게이팅 email(sidebar) vs role(page) 불일치; tab visibility "전체 적용" 오문구(실제 localStorage); 테마 7페이지 파편화(3페이지는 다크모드 없음); regimen-cost 삭제 confirm/에러핸들 없음; alert 19곳 vs 배너 혼재; 모달 접근성(Escape/focus/aria 부재).
- **데이터**: `check_foreign_price_coverage` orphan(호출 안 됨); QG `check_mfds_baseline` DB부재 시 silent PASS; hermes 체크가 전량 pending이어도 PASS; DocCheck 자격증명 실패(DE 가격 소스 열화) 미조치.

---

## 🟢 검증된 견고 항목 (조치 불필요)

- **과거실수 6건 코드 수정 전부 현존·테스트 통과**: injection form_type 가드, US WAC-only(AWP fallback), KEB '최종'회차, A8 공식(+`test_welireg_excel_baseline.py` PASS), 암질심→약평위 date-guard, MFDS peri/adj/neo LayerSpec(8 baseline 회귀 0).
- **보안 기본기**: SQL injection 없음(전부 화이트리스트+`?`), `config/.env` 미커밋·gitignore 유효, drug_prices 인덱스 쿼리(풀스캔 없음), 경로 traversal 3중 가드, `import api.server`/`import scheduler` clean.
- **프론트**: `tsc`/`build` clean, 다수 페이지 loading/error/empty 견고, `/krpia-committee` dead route 없음, useApi 취소가드 정상.
- **정책인텔**: 19 테스트 통과, raw 경로 API 미유출, 사이드카 main repo 미커밋(gitignore 확인).

---

## 권장 착수 순서
1. **P1 + P2** — 배포한 기능이 안 돌고(P1) + 메일 발췌 공개 유출(P2). 둘 다 라이브.
2. **P4 + P5** — 무인증 엔드포인트 + 기본 admin 계정(공개 배포 백도어).
3. **P3** — 30일 윈도우 데이터 유실 방지(누적 manifest).
4. **P6/P7** — JWT/gunicorn(다음 배포에 묶기).
5. **P8/P9/P10** — 데이터/규칙 정합성.
6. **P12~P15** — 프론트 체감 개선.
