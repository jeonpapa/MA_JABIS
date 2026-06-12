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

# ── Python 인터프리터: .venv 우선 (시스템 python3 는 flask_cors 등 의존성 없음) ──
PYTHON="$PROJECT_DIR/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

# ── 기존 서버 프로세스 정리 ─────────────────────────────────────────────────
EXISTING_PID=$(lsof -ti tcp:$PORT 2>/dev/null)
if [ -n "$EXISTING_PID" ]; then
  echo "기존 서버 종료 중 (PID: $EXISTING_PID)..."
  kill "$EXISTING_PID" 2>/dev/null
  sleep 1
fi

# ── Flask 서버 백그라운드 시작 ───────────────────────────────────────────────
echo "Flask 서버 시작 중... (포트 $PORT)"
"$PYTHON" api/server.py &
SERVER_PID=$!

# ── 서버 준비 대기 (최대 10초) ──────────────────────────────────────────────
for i in $(seq 1 10); do
  if curl -s "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
    echo "서버 준비 완료 (${i}초)"
    break
  fi
  sleep 1
done

# ── 스케줄러 상시 프로세스 시작 (월별 약가 catch-up·QG·compliance 등) ────────
# scheduler.py = APScheduler BlockingScheduler. Flask 와 별개 프로세스로 상시
# 구동돼야 매월 1일 09:00 국내 약가 자동 적재가 동작한다. (배포 시에도 동일 패턴)
SCHED_PID=""
if [[ "$1" != "--no-scheduler" && "$2" != "--no-scheduler" ]]; then
  echo "스케줄러 시작 중... (월별 약가 자동 적재 + QG/compliance)"
  "$PYTHON" scheduler.py >> "$PROJECT_DIR/logs/scheduler.out" 2>&1 &
  SCHED_PID=$!
fi

# ── Chrome으로 대쉬보드 오픈 ────────────────────────────────────────────────
if [[ "$1" != "--no-browser" ]]; then
  echo "Chrome 오픈: $DASHBOARD_URL"
  open -a "Google Chrome" "$DASHBOARD_URL"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  대쉬보드: $DASHBOARD_URL"
echo "  서버 PID: $SERVER_PID"
[ -n "$SCHED_PID" ] && echo "  스케줄러 PID: $SCHED_PID (매월 1일 09:00 약가 적재)"
echo "  종료: Ctrl+C"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Ctrl+C 시 서버 + 스케줄러 함께 종료 ─────────────────────────────────────
trap "echo '서버/스케줄러 종료 중...'; kill $SERVER_PID 2>/dev/null; [ -n \"$SCHED_PID\" ] && kill $SCHED_PID 2>/dev/null; exit 0" INT TERM

# 포그라운드에서 서버 로그 출력
wait $SERVER_PID
