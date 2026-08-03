# KRPIA Policy Intelligence — 헤르메스/유지보수 후속 요청

**작성일**: 2026-08-04
**대상**: 헤르메스(GPT-5.5 큐레이터) · maintainer(Joseph) · 배포 운영
**배경**: `jeonpapa/MA_JABIS#1` 구현 핸드오프 검토 결과 — 시스템은 **이미 거의 완성**(백엔드 4모듈·overview 등 엔드포인트·프론트 topic-ledger UX·5개 테스트 전부 존재, 29 테스트 통과). 실질 코드 갭은 **B3 fail-closed 하드닝 1건뿐이며 반영 완료**(`9d6a23f9`). 이 문서는 코드가 아니라 **헤르메스 운영·데이터·확정이 필요한 항목**을 정리한다.

---

## A. 미결정 항목 확정 요청 (핸드오프 §11.3)

| # | 항목 | 요청 |
|---|------|------|
| A1 | 큐레이션 모델 | 사이드카 `model` 필드가 `gpt-5.5`로 명시됨 — 실제 사용 모델과 일치하는지 확정 |
| A2 | scheduler `misfire_grace_time` | 값 확정(다른 잡과 정합) |
| A3 | 리포트 아티팩트 템플릿 저장 위치 | 확정 |
| A4 | 라우트/private lane 접근 | 코드상 이미 확정(`/policy-intelligence`, GitHub API+token) — **문서만** 업데이트 |

## B. 헤르메스 큐레이션 운영 작업 (핸드오프 §7 — 실행 루틴)

- **B1. pending 사이드카 작성**: `python -m agents.policy_analysis list-pending --since <cutoff>` → 본문/첨부 근거로 sidecar JSON 작성 → `python -m agents.policy_analysis validate --file policy_intelligence/analysis/<event_id>.json` → **비공개 `AccessRoutineAnalystic`에만** commit.
- **B2. new_topic 후보**: taxonomy(controlled 8종)에 안 맞는 메일은 **topic 임의 생성 금지**, `data_gaps:["new_topic_candidate: ..."]`로 남김 → maintainer가 `TOPIC_RULES` 갱신 + 테스트 추가.
- **B3. grounding 원칙**: `evidence_quotes`는 본문/파일의 **정확한 substring**만. 금지 토큰(`brdBltNo`,`idxno`,`PR-`,`Precision`,`Recall`,`F1`,`조건부 통과`) 사용 금지 — validate가 자동 차단.

## C. 정량 영향분석 데이터 갭 (핸드오프 §6.3 — Joseph/내부데이터 필요)

1순위 lane `기등재 약제 재평가·약가조정` 정량화에 필요(확보 전까지 대시보드는 **정성·payer-policy 중심 유지**):
- MSD 대상제품 리스트
- 현 급여가 / 실계약가 가정(내부 확보 시)
- 물량/매출 노출
- RSA · 약가-물량 · 리베이트 제약
- 특허/LOE · 단일제품 예외 사실
- 시나리오 정의(Joseph/팀 승인)

## D. 배포 전 운영 시크릿 (핸드오프 §8 — fly secrets)

| 시크릿 | 용도 | 미설정 시 |
|--------|------|-----------|
| `JWT_SECRET` | 세션 토큰 서명 고정 | 재시작마다 ephemeral 생성 → 세션 무효화(로그인 풀림) |
| `POLICY_ANALYSIS_TOKEN` (또는 `GITHUB_TOKEN`) | 비공개 `AccessRoutineAnalystic` 사이드카 인증 fetch | **B3 하드닝으로 프로덕션 sync fail-closed** (사이드바 미동기화) |

설정 명령은 `scripts/set_policy_intel_secrets.sh` 참조(플레이스홀더 — 실제 값은 커밋 금지).

---

## 참고: 반영 완료(코드) 요약

- **B3 fail-closed**(`9d6a23f9`): 프로덕션에서 토큰 없이 공개 raw 폴백으로 사설 사이드카를 fetch하지 않고 구조화 에러 반환(부분복사 방지). `_require_auth()`=`/app/data` 또는 `POLICY_ANALYSIS_REQUIRE_AUTH`. 로컬/테스트 무영향.
- 검증: policy 관련 **29 테스트 통과**, 프론트 빌드 ✓.
- 검토 오탐 정정: "조건부 통과 banned token 누락"은 **오탐** — `validate_analysis`가 이미 명시 체크(별도 라인).
