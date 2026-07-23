# Daily Mailing — 스콥 변경 push 트리거 (헤르메스 즉시 반영) 설계

작성일: 2026-07-24
상태: 승인됨 (구현 계획 대기)

## 1. 배경 & 목표

Daily Mailing 은 대쉬보드가 **모니터링 스콥**만 정하고, 외부 러너("헤르메스")가 그 스콥으로 매일
메일을 검토·작성·발송하는 위임 구조다. 대쉬보드 ↔ 헤르메스는 비공개 GitHub repo
`jeonpapa/AccessRoutineAnalystic` 를 양방향 버스로 사용한다:

- **OUTBOUND**: 대쉬보드가 `daily_mailing/scopes/<id>.json` 발행 (구독 변경 시 즉시 PUT + 15분 백스톱)
- **INBOUND**: 헤르메스가 `daily_mailing/runs/` 에 run 번들 커밋 → 대쉬보드가 sync → 관리자 칸반

**현재 공백**: 스콥이 repo 에 올라와도 헤르메스는 "매일 아침" pull 할 때만 인지 → 스콥 변경 반영에
최대 하루 지연. **목표**: 스콥 push 를 헤르메스에 **즉시 도달**시켜 near-real-time 반영.

**결정 (승인)**: 실시간 대상 = **OUTBOUND(스콥 변경 → 헤르메스)**. 헤르메스 런타임 = **GitHub Actions
CI 스크립트**(대화형 에이전트 아님).

**중요**: 발견·검토·작성 파이프라인(`agents.daily_mailing.*`)은 **순수 규칙기반 결정론**이다 — Naver
뉴스 API 발견 + 결정론적 review_board/quality/writer. **LLM(OpenAI 등) 호출 없음** → CI 에서 안정적으로
재현되고 LLM API 키가 필요 없다.

## 2. 아키텍처

pipeline 코드는 MA_AI_Dossier(비공개, 내부)에 두고, 브릿지 repo 에는 **트리거만** 둔다
(파이프라인 코드·비공개 로직을 브릿지 repo Actions 러너에 노출하지 않음).

```
[대쉬보드] 구독/스콥 저장
   │  (기존) _mail_scope_export → GitHub Contents API PUT (즉시)
   ▼
[AccessRoutineAnalystic]  daily_mailing/scopes/<id>.json  ← push
   │  ★ 신규: .github/workflows/notify-scope-change.yml
   │     on: push, paths: ["daily_mailing/scopes/**"]
   │     - git diff 로 변경된 scope 파일명 추출
   │     - repository_dispatch (event_type: scope-changed,
   │       client_payload: { scope_ids: [...] }) → MA_AI_Dossier
   ▼
[MA_AI_Dossier]  ★ 신규: .github/workflows/hermes-daily-mailing.yml
   │     on: repository_dispatch (types: [scope-changed]) + workflow_dispatch(수동 드라이런)
   │     concurrency: group = hermes-<scope_id>, cancel-in-progress: true
   │     job:
   │       1) actions/checkout (self)
   │       2) setup-python + pip install (daily_mailing 최소 의존)
   │       3) GOOGLE_TOKEN_JSON secret → 파일로 기록, GOOGLE_TOKEN_PATH 지정
   │       4) python scripts/hermes_runner.py --scope-ids <ids>
   ▼
[scripts/hermes_runner.py]  (신규 오케스트레이터 — 기존 조각 재사용)
   │     for scope_id in scope_ids:
   │       a) 브릿지 raw 에서 scopes/<id>.json fetch
   │       b) generate_daily_mailing_draft.py 파이프라인 실행 (초안·review_board·quality)
   │       c) delivery 분기:
   │            - test_request 有 → 즉시 [TEST] 1회 (제목 [TEST] 접두)
   │            - delivery_mode=gmail_draft → Gmail 초안 생성 (검토 게이트, 기본값)
   │            - delivery_mode=gmail_send  → 발송
   │            - 일반 스콥 편집(test_request 無) → 초안 프리뷰 생성만, 정규 발송은
   │              기존 일일 스케줄에 위임 (스팸 방지)
   │       d) run 번들을 브릿지 runs/ 에 커밋 (기존 GitHub Contents API 방식)
   ▼
[대쉬보드]  기존 daily_mailing_runs_sync 가 번들 import → 관리자 칸반/이력
```

## 3. 컴포넌트 (단위 · 책임 · 의존)

| 컴포넌트 | 위치 | 책임 | 의존 |
|---------|------|------|------|
| notify-scope-change.yml | AccessRoutineAnalystic repo | scopes/ push 감지 → dispatch 발사 | `DISPATCH_TOKEN` |
| hermes-daily-mailing.yml | MA_AI_Dossier `.github/workflows/` | dispatch 수신 → 환경 구성 → runner 실행 | Actions Secrets |
| **scripts/hermes_runner.py** (신규) | MA_AI_Dossier | scope fetch → 파이프라인 → delivery 분기 → run 번들 커밋 오케스트레이션 | 아래 기존 조각 |
| generate_daily_mailing_draft.py | 기존 | 발견·review_board·quality·초안(HTML/MD) | agents.daily_mailing.* |
| agents/notify/gmail_delivery.py | 기존 | Gmail 초안/발송 | GOOGLE_TOKEN_PATH |
| agents/ingest/daily_mailing_sync.py | 기존 | INBOUND runs 번들 import (변경 없음) | RUNS_TOKEN |

**신규 코드는 `hermes_runner.py` 와 2개 워크플로 YAML 뿐.** 나머지는 기존 조각 조합.

## 4. 시크릿 & 설정

- 브릿지 repo Secrets: `DISPATCH_TOKEN` (MA_AI_Dossier repo scope PAT).
- MA_AI_Dossier repo Secrets:
  - `NAVER_API_CLIENT_ID`, `NAVER_API_CLIENT_SECRET` — 뉴스 발견
  - `GOOGLE_TOKEN_JSON` — gmail.send scope 토큰 내용 (워크플로가 파일로 기록)
  - `DAILY_MAILING_RUNS_TOKEN` — 브릿지 scopes/ 읽기 + runs/ 커밋
  - (LLM 키 불필요 — 파이프라인은 규칙기반 결정론, §1 참조)
- **기본 delivery_mode = `gmail_draft`** (검토 게이트). `gmail_send` 는 검증 후 명시 전환.

## 5. 에러 처리 / 안전장치

- **품질 게이트**: `min-total-articles`/`min-top-signals` 미달 → 초안만, 발송 보류 + 사유 기록 (기존 quality 로직).
- **중복 방지**: ① 워크플로 `concurrency: group=hermes-<scope_id>, cancel-in-progress` 로 연속 저장 디바운스, ② run 번들 run_id 키로 멱등, ③ `test_request` 소비 후 플래그 해제 (기존 `_clear_consumed_test_requests` 재사용).
- **실패 격리**: runner 예외 → 발송 안 됨, Actions job 실패로 가시화. 스콥 여러 개 중 일부 실패해도 나머지 계속.
- **발송 안전**: 기본 draft 모드라 오발송 위험 최소. test_request 만 즉시 발송 경로.

## 6. 테스트 전략

- **로컬**: `generate_daily_mailing_draft.py --dashboard-scope agents/daily_mailing/dashboard_scope.example.json` → 초안 산출물 검증 (기존 동작). `hermes_runner.py` 는 `--scope-file <로컬 json>` 옵션으로 브릿지 없이 로컬 드라이런.
- **워크플로**: `workflow_dispatch` 수동 트리거로 드라이런. test_request 스콥으로 [TEST] 초안 확인.
- **단계적 활성화**: gmail_draft(안전) → 검증 후 gmail_send.
- **트리거 검증**: 브릿지 scopes/ 에 더미 push → dispatch → MA_AI_Dossier 워크플로 발화 확인.

## 7. 범위 밖 (YAGNI)

- INBOUND(runs → 칸반) 실시간화: 이번 범위 아님. 기존 07:30 sync 유지 (추후 동일 패턴으로 push 트리거 가능).
- 대쉬보드 직접 발송: 위임 구조 유지, 구현 안 함.
- 정규(비-test) 메일의 push 즉시 발송: 스팸 방지 위해 일일 스케줄 유지.

## 8. 배포 / 운영 노트

- 두 워크플로 YAML + 시크릿 설정은 사용자가 각 repo 에 배치 (배포 게이트 · flyctl/Actions 권한 경계).
- MA_AI_Dossier 워크플로는 프로덕션 대쉬보드(fly)와 독립 — Actions 러너에서 실행되므로 fly 볼륨 DB
  접근 없음. run 번들은 브릿지 repo 를 통해서만 대쉬보드로 흐름 (기존 sync 재사용).
