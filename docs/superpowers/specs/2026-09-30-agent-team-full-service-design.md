# MA AI Dossier 에이전트 팀 — 풀 서비스 운영 설계 (2026-09-30)

> 상태: 설계 승인(2026-09-30). 구현은 단계별 계획(`docs/superpowers/plans/2026-09-30-agent-team-*.md`)으로.
> 대상 독자: 사용자(MA 리드) + 구현·검증 서브에이전트.

## 1. 목표

대쉬보드를 **사람이 지켜보는 도구**에서 **에이전트 팀이 운영하는 서비스**로 바꾼다. 세 가지를 자동화한다.

1. **운영**: 잡 실패·데이터 공백을 스스로 감지해 복구하고, 못 고치면 사람을 부른다.
2. **개선**: 품질 리포트·발송 통계·사용 로그에서 개선 과제를 만들어 구현·검증까지 끝낸다.
3. **요청 대응**: MA 팀 사용자가 올린 개선 요청을 접수·구현·회신한다.

**비목표(YAGNI)**: 사외 사용자 대상 서비스, 질의응답 챗봇, 다중 승인 워크플로, 별도 스테이징 환경.

## 2. 확정된 결정 (2026-09-30 사용자)

| 항목 | 결정 |
|---|---|
| 배포 권한 | **저위험 변경은 자동 배포**, 그 외는 사람 승인 대기열 |
| 실행 위치 | **VPS 상주 팀**(기존 헤르메스 운영 서버). 저장소 커밋·외부 API·flyctl·Claude Code 실행이 모두 가능 |
| 클라우드 루틴 | 예비 수단으로만. 크론 최소 1시간, `flyctl` 미제공, 네트워크 허용목록 제약 |
| 고객 범위 | **MA 팀 내부 사용자**(대쉬보드 로그인 사용자) |
| 작성·검증 분리 | 구현 에이전트와 검증 에이전트는 항상 다른 인스턴스 |

## 3. 아키텍처

```
[프로덕션 fly: web + scheduler]         [VPS: 에이전트 팀]            [GitHub]
  신호 발생                               10분 주기 디스패처            main 브랜치
  - 잡 실패/헬스 문제      ──POST──▶      ├ 운영 감시자                 PR·커밋
  - QualityGuard/RC 리포트                ├ 요청 처리자   ──claude -p──▶ 작업트리
  - 발송 품질 통계                        ├ 개선자                      │
  agent_task 원장(sqlite)  ◀──API──       ├ 검증자(별도 인스턴스)       │
  운영 원장 UI(admin)                     └ 릴리스 관리자 ──flyctl──▶ 배포
```

- **작업 원장은 프로덕션 DB**(`agent_task`)에 둔다. 대쉬보드가 그대로 읽어 화면에 보여주고, 사용자 요청(`service_request`)과 링크된다.
- **에이전트는 API로만 원장을 만진다.** DB 파일을 직접 쓰지 않는다(볼륨 접근 불필요, 감사 로그 일원화).
- **에이전트 인증**: `users` 테이블에 `role='agent'` 서비스 계정을 만들고, `/api/agent/*` 는 `require_auth(role='agent')` 로 보호한다. 토큰은 VPS 환경변수로만 보관한다. 기존 `admin` 토큰은 재사용하지 않는다(권한 최소화).
- **실행 형태**: VPS 크론(10분) → 디스패처 스크립트 → 역할별 헤드리스 `claude -p`(도구 허용목록 + 무인 권한 모드) 실행 → 결과를 API 로 회신. 상주 데몬이 아니라 **짧은 실행의 반복**이라 재시작·중단이 안전하다.

## 4. 팀 구성

| 역할 | 하는 일 | 트리거 | 권한 |
|---|---|---|---|
| **운영 감시자** | 헬스·잡 결과·데이터 신선도 점검, 정해진 복구 절차 실행(캐치업 재실행 등), 실패 시 과제 생성 | 10분 주기 + 프로덕션 신호 | 읽기 + 정해진 런북 실행 |
| **요청 처리자** | `service_request` outbox 픽업 → 분류 → 구현 → 테스트 → 커밋 → 요청자 회신 | 새 요청 웹훅 + 매시 | 저장소 쓰기(브랜치) |
| **개선자** | QualityGuard·RuleCompliance 리포트, 발송 통계(시사점 제외율·본문 확보율·클러스터 수), 사용 로그에서 과제 생성·구현 | 매일 05:00 | 저장소 쓰기(브랜치) |
| **검증자** | 설계 준수 → 코드 품질 2단계 검토. 작성자와 다른 인스턴스 | 변경 제출 시 | 읽기 전용 |
| **릴리스 관리자** | 위험도 분류 → 저위험 자동 배포 / 그 외 승인 대기, 배포 후 검증·롤백 | 검증 통과 시 + 배포 후 5분 | flyctl 배포·롤백 |

## 5. 작업 버스 (`agent_task` 원장)

```sql
CREATE TABLE agent_task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,             -- ops_incident | user_request | improvement | release
  source TEXT NOT NULL,           -- health_check | quality_guard | rule_compliance | service_request | mail_stats | manual
  source_ref TEXT,                -- service_request.id, run_id, 리포트 경로 등
  title TEXT NOT NULL,
  detail TEXT NOT NULL,           -- 근거·재현 방법
  priority INTEGER NOT NULL DEFAULT 3,   -- 1 긴급 ~ 5 낮음
  risk TEXT,                      -- low | high (릴리스 단계에서 판정)
  status TEXT NOT NULL,           -- new|triaged|in_progress|verified|awaiting_approval|deploying|deployed|closed|blocked|rejected
  claimed_by TEXT, claimed_at TEXT,
  branch TEXT, commit_ref TEXT, deploy_version TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  resolution TEXT,                -- 처리 결과/기각 사유
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE agent_task_event (   -- append-only 감사 로그
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL,
  detail TEXT, created_at TEXT NOT NULL
);
```

**상태 전이**

```
new → triaged → in_progress → verified → (low: deploying → deployed) → closed
                                      ↘ (high: awaiting_approval → deploying → deployed) → closed
        ↘ blocked(사람 필요)                     ↘ rejected(사유 기록)
```

규칙:
- **선점은 조건부 UPDATE 1회**(`WHERE status='triaged' AND claimed_by IS NULL`). Daily Mailing 발송 선점과 같은 방식.
- **재시도 2회까지.** 3번째 실패는 `blocked` + 사람 호출.
- **모든 전이는 `agent_task_event`** 에 기록한다. `user_request` 는 상태가 바뀔 때마다 `service_request` 에 역동기화되어 요청자 화면(`/my-requests`)에 보인다.
- **하루 예산**(작업 수·LLM 비용·자동 배포 횟수)을 넘으면 새 선점을 멈추고 대기열에 쌓는다.

## 6. 저위험 자동 배포 게이트

`agents/release/risk.py` 의 순수 함수가 git diff 로 판정한다(LLM 아님).

**자동 배포 (모두 충족)**
- 변경 경로가 허용 목록: `agents/scrapers/**`, `agents/daily_mailing/**`(프롬프트 파일 제외), `frontend/src/**`, `tests/**`, `docs/**`, `config/*.yaml`(자격증명 아님)
- 스키마 변경 없음: diff 에 `CREATE TABLE`/`ALTER TABLE`/마이그레이션 파일 없음
- 금지 경로 미포함: `api/auth.py`, `agents/notify/**`, `scheduler.py` 잡 등록부, `config/.env*`, `Dockerfile`, `fly.toml`
- **프롬프트·판정 기준 파일 미포함**(`llm.py`, `WRITER_SYSTEM`/`SUPPORT_SYSTEM` 이 있는 파일) — 메일 품질을 통째로 바꾸므로 사람 승인
- 전체 테스트 통과 + 변경 모듈에 신규/수정 테스트 존재
- diff ≤ 300줄, 변경 파일 ≤ 10개
- 검증자 승인

**사람 승인**: 위 조건 중 하나라도 불충족, 또는 검증자 이견.

**배포 후 검증(5분)**: `/api/health` 200 · 스케줄러 프로세스 생존 · 직전 10분 ERROR 로그 증가 없음 · 오늘 발송 예정 구독의 run 상태 정상. 실패 시 **직전 릴리스로 롤백**하고 과제를 `blocked` 로 돌린다.

## 7. 관측과 보고

- **운영 원장 화면**(admin): 오늘 처리한 작업·상태·근거 링크·배포 버전·비용. Daily Mailing 칸반과 같은 구조.
- **매일 아침 요약 메일 1통**: 어제 자동 처리분 / 승인 대기 / 사람이 봐야 할 문제. Daily Mailing 발송 경로를 재사용한다.
- **요청자 회신**: `user_request` 상태 변화가 `/my-requests` 에 자동 반영.
- **감사 로그**: 누가(어느 역할), 무엇을, 왜, 어떤 근거로 했는지 append-only.

## 8. 안전장치

- **정지 스위치**: `agent_policy.enabled=0` 한 줄로 팀 전체 정지. 정지해도 기존 스케줄러(수집·발송)는 계속 돈다.
- **하루 예산 상한**: 작업 건수·LLM 비용·자동 배포 횟수. 초과 시 대기열.
- **금지 행위**: 시크릿 파일 읽기/커밋, 프로덕션 DB 직접 수정(API 경유만), 사용자 데이터 삭제, 외부 발송(요약 메일 제외), `git push --force`, 스케줄 변경.
- **막히면 멈춘다**: 2회 실패·검증자 이견·금지 행위 필요 시 `blocked` + 사람 호출.
- **주간 회고**(월요일 06:00): 지난주 자동 처리 결과를 점검해 오탐·재발 항목을 개선 과제로 만든다.

## 9. 단계적 도입

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **P1 운영 감시자** | `agent_task` 원장 + `/api/agent/*` + 프로덕션 신호 수집 + VPS 디스패처 + 런북 기반 자가 복구 + 운영 원장 화면 | 잡 실패를 사람이 아니라 원장에서 먼저 본다. 캐치업 재실행 같은 런북이 자동 수행된다 |
| **P2 요청 처리자** | `service_request` ↔ `agent_task` 연결, 요청 구현 루프(작성→검증), 요청자 자동 회신 | 사용자 요청이 사람 개입 없이 브랜치·커밋까지 간다 |
| **P3 개선자** | 품질 리포트·발송 통계 수집 → 과제화 → 구현, 주간 회고 | 매주 개선 과제가 자동 생성·처리된다 |
| **P4 릴리스 관리자** | 위험 분류기 + 자동 배포 + 배포 후 검증·롤백 + 승인 대기 UI | 저위험 변경이 사람 손 없이 프로덕션에 반영된다 |

P1 이 끝나야 P2 가 의미 있다(원장·인증·디스패처가 공통 기반). P4 는 P2·P3 의 산출물이 쌓인 뒤에 켠다.

## 10. 테스트 전략

- **순수 함수 우선**: 위험 분류기, 상태 기계, 예산 계산, 신호→과제 변환은 LLM 없이 단위 테스트.
- **디스패처는 드라이런 모드**: 실제 `claude -p` 실행 없이 선택·선점·회신만 검증.
- **통합 테스트**: 프로덕션 API + 원장 + 디스패처를 임시 DB 로 연결(외부 호출은 가짜).
- **스테이징 없음을 보완**: 배포 후 검증 + 즉시 롤백이 안전망이다.

## 11. 리스크

- **VPS 단일 장애점** — 죽으면 자동화가 멈춘다(서비스 자체는 계속 동작). 헬스 신호가 24시간 이상 소비되지 않으면 프로덕션이 사람에게 알린다.
- **에이전트 폭주** — 예산 상한·재시도 2회·정지 스위치로 제한.
- **자동 배포 사고** — 허용 목록을 좁게 시작하고(문구·테스트·문서부터), 배포 후 검증과 롤백을 먼저 만든 뒤 켠다.
- **LLM 비용** — 과제당 비용을 원장에 기록하고 일 상한을 둔다.
