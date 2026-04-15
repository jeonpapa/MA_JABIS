#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# MA AI Dossier — 대쉬보드 실행 스크립트
#
# 사용법:
#   bash scripts/run_dashboard.sh          # 서버 시작 + Chrome 오픈
#   bash scripts/run_dashboard.sh --no-browser  # 서버만 시작
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DASHBOARD_URL="http://127.0.0.1:5001/dashboard/"
PORT=5001

cd "$PROJECT_DIR" || exit 1

# ── 기존 서버 프로세스 정리 ─────────────────────────────────────────────────
EXISTING_PID=$(lsof -ti tcp:$PORT 2>/dev/null)
if [ -n "$EXISTING_PID" ]; then
  echo "기존 서버 종료 중 (PID: $EXISTING_PID)..."
  kill "$EXISTING_PID" 2>/dev/null
  sleep 1
fi

# ── Flask 서버 백그라운드 시작 ───────────────────────────────────────────────
echo "Flask 서버 시작 중... (포트 $PORT)"
python3 api/server.py &
SERVER_PID=$!

# ── 서버 준비 대기 (최대 10초) ──────────────────────────────────────────────
for i in $(seq 1 10); do
  if curl -s "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
    echo "서버 준비 완료 (${i}초)"
    break
  fi
  sleep 1
done

# ── Chrome으로 대쉬보드 오픈 ────────────────────────────────────────────────
if [[ "$1" != "--no-browser" ]]; then
  echo "Chrome 오픈: $DASHBOARD_URL"
  open -a "Google Chrome" "$DASHBOARD_URL"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  대쉬보드: $DASHBOARD_URL"
echo "  서버 PID: $SERVER_PID"
echo "  종료: Ctrl+C"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Ctrl+C 시 서버 종료 ─────────────────────────────────────────────────────
trap "echo '서버 종료 중...'; kill $SERVER_PID 2>/dev/null; exit 0" INT TERM

# 포그라운드에서 서버 로그 출력
wait $SERVER_PID
