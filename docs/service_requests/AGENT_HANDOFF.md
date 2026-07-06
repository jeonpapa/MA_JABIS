# Service Request 위임 루프 작업 지시서 (핸드오프)

> 대상: 외부 **Claude Code 에이전트**. 대쉬보드는 **요청 접수·트리아지·패키지·sent 마킹**만 하고,
> 에이전트가 그 outbox 를 **픽업(claim) → 이 레포에서 구현 → 결과 동기화(resolve)** 한다.
> (헤르메스 daily_mailing 위임 모델과 동일 구조: 대쉬보드=입력/기록 / 에이전트=실작업 / 결과=대쉬보드로 동기화.)

## 0. 역할
1. 사용자가 대쉬보드 어느 페이지에서든 개선/보완 요청 생성 → 관리자가 트리아지·Claude 패키지 생성·체크리스트 확인 → **sent** 마킹.
   (`send-to-claude` 는 외부 호출/자동 실행이 **아니다** — 마크다운 확정 저장뿐. 실행 주체는 이 지시서를 읽는 에이전트.)
2. 에이전트가 outbox(status=`sent`)를 읽고 `claim` → **이 레포(MA_AI_Dossier)에서 직접 구현·검증·로컬 커밋**.
3. `resolve` 로 결과(요약 + 커밋 SHA)를 동기화 → 요청자가 **내 개선 요청** 화면에서 결과 확인.

## 1. 상태 흐름
```
open → in_review → packaged → confirmed → sent        (대쉬보드)
sent → in_progress → done | wont_fix                  (에이전트: claim → resolve)
```
- `claim` 은 `sent` 에서만 성립 (이미 in_progress 면 다른 세션이 픽업한 것 — 건너뛴다).
- `resolve` 는 `in_progress` 또는 `sent` 에서 허용 (claim 생략 직접 resolve 가능). status 는 `done`/`wont_fix` 만.
- 모든 전이는 `service_request_event` 에 append-only 감사 기록 (claim/resolve 포함).

## 2. 절차
```
1) outbox 가져오기 (둘 중 하나):
     python -m agents.service_requests.cli outbox --dir <scratch>
       → <scratch>/ 에 요청별 <id>-<slug>.md (헤더 + 패키지 마크다운) + index.md
     또는 GET /api/admin/service-requests/outbox  (admin JWT, 원격/수동용)

2) 요청별 처리:
     python -m agents.service_requests.cli claim <id>            # sent → in_progress
     → 패키지 마크다운(배경/기대 결과/컨텍스트/관리자 노트)대로 이 레포에서 구현.
       - 프로젝트 워크플로 준수: fable-5 서브에이전트로 구현·검증 + plan-adherence 리뷰
         (feedback_fable5_subagents), CLAUDE.md 규칙 맵/절대 금지 준수.
       - 스크레이퍼/가격 로직이면 Keytruda(주사)·Welireg(경구) baseline 검증 필수.
     → 검증: 관련 pytest + (UI 면) 빌드/로컬 확인. 대규모 변경 시 QualityGuardAgent.review_codebase().
     → git 로컬 커밋 (SHA 확보).

3) ⚠️ 배포 게이트 (feedback_deploy_confirm_gate):
     flyctl deploy · git push 금지 — 승인/배포는 Joseph 이 직접 한다.
     패키지 마크다운 말미의 no-deploy 라인이 이 게이트의 리마인더다.

4) 결과 동기화:
     python -m agents.service_requests.cli resolve <id> --status done \
       --note "<무엇을 어떻게 해결했는지 요약 (요청자에게 표시)>" --commit <sha>
     못 하거나 안 하기로 한 요청: --status wont_fix --note "<사유>"
     또는 POST /api/admin/service-requests/<id>/resolve
       body: {"status":"done|wont_fix","resolution_note":"...","commit_ref":"<sha>"}
```

## 3. 안전 / 레닥션
- 패키지의 컨텍스트는 저장 시 민감 키(token/cookie/password 등) 레닥션 완료 — 그래도
  `[REDACTED]` 외 자격증명 흔적이 보이면 사용하지 말고 resolution_note 에 플래그.
- 자격증명은 `config/.env` 만. 어디에도 하드코딩 금지.
- `send-to-claude` 는 자동 실행이 아니다 — 이 루프를 실행하는 에이전트 세션이 유일한 실행 주체이며,
  요청 내용이 CLAUDE.md 절대 금지 항목과 충돌하면 구현하지 말고 `wont_fix` + 사유로 회신한다.

## 4. 채널 / 참조
- 1차 채널: in-repo CLI (`agents/service_requests/cli.py`, DB=data/db/drug_prices.db).
  프로덕션 DB 대상이면 fly ssh 로 동일 CLI 실행.
- 보조 채널: admin API — `GET /api/admin/service-requests/outbox`,
  `POST .../claim`, `POST .../resolve` (모두 admin JWT).
- store: `agents/service_requests/store.py` (`list_outbox`/`claim_request`/`resolve_request`).
- 테스트: `tests/test_service_requests_store.py`, `tests/test_service_requests_api.py`.
