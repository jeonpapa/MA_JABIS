# VPS 디스패처 설치·운영 절차 (에이전트 팀 P1 — 운영 감시자)

설계: `docs/superpowers/specs/2026-09-30-agent-team-full-service-design.md` §3·§5·§8
계획: `docs/superpowers/plans/2026-09-30-agent-team-P1-ops-sentinel.md` Task 6·7·9

## 0. 구성 요약

| 위치 | 역할 | 코드 |
|---|---|---|
| 프로덕션(fly, `ma-ai-dossier`) | 작업 원장(`agent_task`, sqlite) · 신호 수집 · 런북 실행 · `/api/agent/*` · 운영 원장 화면 | `agents/agent_tasks/**`, `api/server.py`, `scheduler.py` |
| VPS | **얇은 디스패처만** 10분 주기로 실행 — 로그인 → 과제 조회 → 선점 → 런북 요청 → 회신 | `scripts/agent_dispatcher.py` |

- P1 에는 LLM 이 없다. 런북은 프로덕션 안의 결정적 파이썬 함수다(`agents/agent_tasks/runbooks.py`).
- 디스패처는 **API 로만** 원장을 만진다. 프로덕션 DB·볼륨·`flyctl` 접근이 필요 없다.
- 디스패처는 표준 라이브러리만 쓴다(`urllib`). VPS 에 pip 의존성이 없고 Python 3.9 이상이면 된다.
- 인증은 `users` 테이블의 `role='agent'` 서비스 계정 + 기존 JWT 로그인. 토큰은 매 실행마다 새로 받고 디스크에 남기지 않는다.
- `agent` 역할은 `require_auth(role="agent")` 를 명시한 라우트, 즉 `/api/agent/*` 만 통과한다. 일반 사용자 API(`/api/mail-subscriptions`, `/api/msd/*`, `/api/auth/me` 등)와 관리자 라우트(`/api/admin/*`)는 모두 403 이다(`api/auth.py:SERVICE_ROLES`).

## 1. 에이전트 계정 발급 (프로덕션, 1회)

`agents/db/users.py:UsersDB.create_user` 는 `role` 로 `admin`·`user`·`agent` 를 받는다.

```bash
export MA_API_BASE=https://ma-ai-dossier.fly.dev

# 1) 강한 난수 비밀번호 — 이 값은 VPS 의 .env 에만 둔다 (git·메모·채팅 금지)
export MA_AGENT_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

# 2) 관리자 토큰
ADMIN_TOKEN=$(curl -sS "$MA_API_BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d '{"email":"<관리자 이메일>","password":"<관리자 비밀번호>"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

# 3) 서비스 계정 생성 (role=agent)
curl -sS "$MA_API_BASE/api/admin/users" -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"agent@msd.com\",\"password\":\"$MA_AGENT_PASSWORD\",\"role\":\"agent\"}"
# 기대: HTTP 201 {"user": {"email": "agent@msd.com", "role": "agent", ...}}

# 4) 검증 — agent 토큰은 /api/agent/* 만 열리고 일반 사용자 API·admin 라우트는 403 이어야 한다(권한 최소화)
AGENT_TOKEN=$(curl -sS "$MA_API_BASE/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"agent@msd.com\",\"password\":\"$MA_AGENT_PASSWORD\"}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
curl -sS -o /dev/null -w 'agent tasks  → %{http_code}\n' "$MA_API_BASE/api/agent/tasks"        -H "Authorization: Bearer $AGENT_TOKEN"   # 200
curl -sS -o /dev/null -w 'user api     → %{http_code}\n' "$MA_API_BASE/api/mail-subscriptions" -H "Authorization: Bearer $AGENT_TOKEN"   # 403
curl -sS -o /dev/null -w 'admin ledger → %{http_code}\n' "$MA_API_BASE/api/admin/agent-tasks"  -H "Authorization: Bearer $AGENT_TOKEN"   # 403
```

기존 `admin` 토큰을 VPS 에 두지 않는다. 비밀번호 교체는 관리자 화면(사용자 관리)에서 계정을 지우고 다시 만든 뒤 VPS `.env` 를 갱신한다.

## 2. VPS 준비

```bash
sudo mkdir -p /srv/ma-dossier && sudo chown "$USER" /srv/ma-dossier
git clone <저장소 URL> /srv/ma-dossier
cd /srv/ma-dossier
python3 --version                    # 3.9 이상
python3 -m venv venv                 # 디스패처는 표준 라이브러리만 쓴다 — requirements.txt 설치 불필요
mkdir -p logs

cat > .env <<'ENV'
MA_API_BASE=https://ma-ai-dossier.fly.dev
MA_AGENT_EMAIL=agent@msd.com
MA_AGENT_PASSWORD=<1 단계의 난수 비밀번호>
ENV
chmod 600 .env
```

`.env` 는 저장소의 `config/.env` 와 다른 파일이다(VPS 전용, 커밋 금지). 저장소 `.gitignore` 는 루트 `.env` 를 무시하지 않으므로 VPS 에서만 로컬 제외 규칙을 둔다(추적 파일을 바꾸지 않아 `git pull --ff-only` 가 막히지 않는다):

```bash
echo '.env' >> .git/info/exclude && git status --short   # .env 가 보이지 않아야 한다
```
Python 이 3.9 미만이거나 venv 를 쓰지 않을 때는 아래 명령의 `./venv/bin/python` 을 `python3` 로 바꾼다.

## 3. 첫 실행은 반드시 `--dry-run`

```bash
cd /srv/ma-dossier && set -a && . ./.env && set +a && ./venv/bin/python scripts/agent_dispatcher.py --dry-run
```

기대 출력(마지막 줄): `dispatch: {"planned": [{"task_id": 12, "runbook": "domestic_price_catchup"}, ...], "handled": 0, "candidates": N, "errors": 0}`

- `planned` 는 이번 실행이 집을 과제와 매핑된 런북. `runbook: null` 은 런북 힌트가 없어 `blocked`(사람 확인) 로 넘길 과제다.
- dry-run 도 로그인은 한다. 여기서 `HTTP Error 401` 이면 계정·비밀번호, `403` 이면 계정 역할(`agent` 가 아님)을 확인한다.
- 계획이 맞으면 `--dry-run` 없이 한 번 수동 실행하고 운영 원장 화면(`/admin/agent-tasks`)에서 이벤트(`claimed → runbook_started → runbook_ok|runbook_failed → status:closed|blocked`)를 확인한다.

옵션: `--max-tasks N` (1회 실행에서 처리할 과제 상한, 기본 3).

## 4. 크론 등록 (10분 주기)

```bash
crontab -e
```

```cron
*/10 * * * * cd /srv/ma-dossier && set -a && . ./.env && set +a && ./venv/bin/python scripts/agent_dispatcher.py >> logs/agent.log 2>&1
```

- 상주 데몬이 아니라 짧은 실행의 반복이다. 실행 하나가 죽어도 다음 실행이 이어받는다(자동 분류·선점 모두 조건부 UPDATE 1회라 중복 실행에도 안전 — 같은 과제를 두 실행이 동시에 집어도 하나만 선점된다).
- 런북은 서버 안에서 **동기** 실행된다(`domestic_price_catchup` 은 Playwright 크롤로 수 분). 디스패처의 요청 타임아웃은 600초(`REQUEST_TIMEOUT_SECONDS`)라 한 실행이 10분을 넘길 수 있다 — 크론이 겹쳐도 위의 조건부 선점 때문에 안전하다.
- 로그 순환: `logrotate` 에 `/srv/ma-dossier/logs/agent.log { weekly rotate 8 compress missingok notifempty }` 를 등록한다.
- 코드 갱신: `cd /srv/ma-dossier && git pull --ff-only`. 디스패처는 서버 API 계약(`/api/agent/*`)만 쓰므로 프로덕션 배포와 순서를 맞출 필요는 없다.

## 5. 정지 방법

| 방법 | 효과 | 되돌리기 |
|---|---|---|
| 운영 원장 화면(`/admin/agent-tasks`) 정책 토글 `enabled=false` | 서버가 모든 선점을 `423 POLICY_BLOCKED` 로 거부. 디스패처는 계속 돌지만 아무것도 집지 않는다 | 토글 `enabled=true` |
| API: `curl -X POST "$MA_API_BASE/api/admin/agent-policy" -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -d '{"enabled": false}'` | 위와 동일 | `{"enabled": true}` |
| VPS `crontab -e` 에서 해당 줄 주석 처리 | 디스패처 자체 정지 | 주석 해제 |

**정지해도 프로덕션 스케줄러(수집·발송·신호 기록)는 계속 돈다.** 원장에는 과제가 계속 쌓이고, 재개하면 우선순위순으로 처리된다.
일일 예산(`max_tasks_per_day`·`max_cost_usd_per_day`·`max_deploys_per_day`)을 넘겨도 같은 방식으로 선점이 멈춘다(사유 `task_budget` 등).

## 6. 문제 시 확인 순서

1. **운영 원장 화면** `/admin/agent-tasks` — `blocked` 과제(사람 확인 필요)와 이벤트 로그부터 본다.
2. **VPS** `tail -n 200 /srv/ma-dossier/logs/agent.log`
3. **프로덕션** `flyctl logs -a ma-ai-dossier` (읽기 전용) — 런북 자체의 실패 원인(스케줄러·Gmail·HIRA)은 여기에 있다.

| 증상 | 원인 | 조치 |
|---|---|---|
| `HTTP Error 401` (로그인) | 비밀번호 불일치·계정 없음 | 1 단계 재발급 후 `.env` 갱신 |
| `HTTP Error 403` | 계정 역할이 `agent`/`admin` 이 아님 | 계정 삭제 후 `role=agent` 로 재생성 |
| 매 실행 `선점 거부 ... HTTP 423` | 정책 정지 또는 일일 예산 초과 | 화면에서 정책·오늘 처리 수 확인 |
| `blocked` + "자동 복구 런북 없음" | 신호에 런북 매핑이 없음 | `agents/agent_tasks/signals.py` 의 `JOB_RUNBOOK`/`HEALTH_RUNBOOK` 에 매핑 추가(런북은 `runbooks.py` 에 등록) 또는 사람이 처리한 뒤 아래 **운영 개입 절차** 로 종료 |
| `blocked` + "디스패처 오류: ..." | 런북 요청 중 네트워크/서버 오류 | `flyctl logs` 로 서버측 원인 확인. 재시도는 `triaged` 로 되돌리면 된다(`POST /api/agent/tasks/<id>/resolve {"status":"triaged"}`) — 선점 표식(`claimed_by`)이 풀리고 다음 실행이 집는다. `attempts` 는 보존된다 |
| `blocked` + "재시도 한도(3회) 초과 — 사람 확인 필요" | 같은 과제가 3번 선점됐는데도 닫히지 않음(설계 §5: 재시도 2회까지) | 자동 복구로는 안 되는 문제다. 원인을 사람이 처리한 뒤 아래 **운영 개입 절차** 로 종료한다. 다시 돌리고 싶으면 새 과제가 생기게 두거나(신호는 매일 새 `source_ref`), `triaged` 로 되돌려도 서버가 다시 `blocked` 로 막는다 |
| 과제가 `in_progress` 에 오래 머묾 | 디스패처가 회신 전에 죽음 | **`claimed_at` 에서 런북 타임아웃(디스패처 `REQUEST_TIMEOUT_SECONDS`, 600초)이 지나기 전에는 되돌리지 말 것** — 런북이 아직 도는 중일 수 있다. 되돌리면 `claimed_by` 가 비워져 다음 실행이 재선점하고, 서버가 같은 런북을 **두 번** 돌린다(1GB 머신에서 Playwright 크롤 2개). 타임아웃이 지난 뒤 admin 토큰으로 `POST /api/agent/tasks/<id>/resolve {"status":"blocked","resolution":"..."}` 또는 `{"status":"triaged"}`. 되돌린 뒤 원래 실행이 뒤늦게 회신하면 서버가 `409 STALE_CLAIM` 으로 거부하고 디스패처는 그 과제를 건너뛴다(로그 `선점 무효`) |
| VPS 로그 `선점 무효 task=N (STALE_CLAIM)` | 위 되돌리기 뒤 다른 실행이 재선점한 과제에 원래 실행이 회신함 | 정상 동작(결과는 재선점한 실행의 것만 남는다). 되돌리기가 너무 일렀다는 신호 — 위 경고 참고 |

### 운영 개입 절차 (종료·되돌리기·blocked)

관리자 화면(`/admin/agent-tasks`)에는 **정책 토글만** 있고 과제 상태를 바꾸는 버튼은 없다(상태 변경 UI 는 P2 —
`docs/superpowers/plans/2026-09-30-agent-team-P1-ops-sentinel.md` "P2 로 넘김"). 상태 변경은 **admin 토큰** 으로 `resolve` API 를 직접 보낸다
(`agent` 토큰은 자기 선점 과제만 만질 수 있다 — §7 소유권 규칙):

```bash
ADMIN_TOKEN=...   # 1 단계와 같은 방법으로 발급
# 사람이 처리한 과제 종료 (in_progress·blocked → closed)
curl -sS -X POST "$MA_API_BASE/api/agent/tasks/<id>/resolve" -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"status":"closed","resolution":"<처리 내용>"}'
# 재시도: triaged 로 되돌리기 (claimed_by 가 비워지고 다음 실행이 다시 집는다. attempts 는 보존)
curl -sS -X POST "$MA_API_BASE/api/agent/tasks/<id>/resolve" -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"status":"triaged"}'
```

허용 전이는 `agents/agent_tasks/store.py:ALLOWED_TRANSITIONS` (예: `blocked → triaged|rejected|closed`). 위반이면 `409 INVALID_STATE`.

## 7. 동작 원리 (한 사이클)

```
프로덕션 신호(scheduler.py → signals.py)  →  agent_task(status=new, detail 에 "런북: <이름>")
VPS 디스패처  GET  /api/agent/tasks?status=new,triaged&kind=ops_incident   (우선순위 → id 순, 최대 --max-tasks; P1 은 ops_incident 만)
              POST /api/agent/tasks/<id>/claim            new ops_incident 는 서버가 자동 분류(auto_triaged → status:triaged) 후 선점(claimed, attempts+1)
                                                          attempts 가 3 이면 선점 거부 + 서버가 blocked("재시도 한도(3회) 초과") 로 넘긴다
              POST /api/agent/tasks/<id>/runbook {name}   프로덕션 안에서 런북 실행 (runbook_started → runbook_ok|runbook_failed)
              POST /api/agent/tasks/<id>/resolve          ok → closed, 실패·런북 없음·요청 오류 → blocked(사람 확인)
```

`/runbook`·`/resolve` 는 **선점자(`claimed_by`)와 호출자가 같을 때만** 통과한다. 다르면 `409 STALE_CLAIM`(되돌리기 뒤 다른 실행이
재선점한 과제) — 디스패처는 그 과제를 건너뛴다. admin 토큰만 예외(운영 개입: 되돌리기·강제 blocked·종료), `api/server.py:_agent_stale_claim`.

모든 전이는 `agent_task_event` 에 append-only 로 남고 운영 원장 화면의 상세에서 그대로 보인다.
런북은 선점된(`in_progress`) 과제에서만 실행된다 — 예산 판정(`spend_check`)을 우회할 수 없다.

## 8. 보안 원칙

- 서비스 계정 비밀번호는 VPS `.env`(600) 에만 둔다. 토큰은 디스크에 저장하지 않는다.
- `agent` 역할은 `require_auth(role="agent")` 를 명시한 `/api/agent/*` 만 열린다. 일반 사용자 API(`require_auth()`)와 관리자 라우트는 403 이다(`api/auth.py:SERVICE_ROLES`). 관리자 토큰을 VPS 에 두지 않는다.
- 디스패처는 프로덕션 DB·`flyctl`·git push 를 하지 않는다. 배포는 사용자가 직접 한다.
