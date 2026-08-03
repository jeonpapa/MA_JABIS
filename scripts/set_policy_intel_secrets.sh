#!/usr/bin/env bash
# Policy Intelligence 배포 전 fly secrets 설정 (사용자가 직접 실행).
#
# ⚠️ 실제 시크릿 값은 이 파일에 절대 넣지 말 것 — openssl 생성 또는 환경변수로 전달.
# ⚠️ `flyctl secrets set` 은 매 호출마다 머신을 1회 재시작한다.
#
# 사용:
#   POLICY_ANALYSIS_PAT=ghp_xxxxx bash scripts/set_policy_intel_secrets.sh
#   (JWT_SECRET 은 미설정 시 자동으로 강한 랜덤값을 1회 고정)
set -euo pipefail

APP="${FLY_APP:-ma-ai-dossier}"
echo "대상 앱: $APP"

# ── 1) JWT_SECRET — 세션 토큰 서명 고정 ──────────────────────────────────────
# 미설정이면 재시작마다 ephemeral 생성 → 전체 로그인 세션이 풀린다.
# 이미 설정돼 있으면 건드리지 않는다 (값 변경 시 기존 세션 전부 무효화).
if flyctl secrets list -a "$APP" 2>/dev/null | awk '{print $1}' | grep -qx "JWT_SECRET"; then
  echo "[skip] JWT_SECRET 이미 설정됨 (변경은 전체 세션 무효화 — 의도적일 때만 수동 변경)"
else
  JWT_VALUE="$(openssl rand -base64 48)"
  flyctl secrets set JWT_SECRET="$JWT_VALUE" -a "$APP"
  unset JWT_VALUE
  echo "[set] JWT_SECRET (고정 랜덤 48B)"
fi

# ── 2) POLICY_ANALYSIS_TOKEN — 비공개 사이드카 인증 fetch ─────────────────────
# 미설정이면 프로덕션 sync 가 fail-closed (사이드카 미동기화, B3 하드닝).
# GitHub PAT (AccessRoutineAnalystic 에 대한 contents:read) 를 환경변수로 전달.
if [ -n "${POLICY_ANALYSIS_PAT:-}" ]; then
  flyctl secrets set POLICY_ANALYSIS_TOKEN="$POLICY_ANALYSIS_PAT" -a "$APP"
  echo "[set] POLICY_ANALYSIS_TOKEN"
else
  echo "[skip] POLICY_ANALYSIS_PAT 미제공 — 실행 예:"
  echo "        POLICY_ANALYSIS_PAT=ghp_xxx bash scripts/set_policy_intel_secrets.sh"
fi

echo "완료. (secrets set 은 머신을 재시작함 — 잠시 후 flyctl status 로 확인)"
