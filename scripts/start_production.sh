#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# 프로덕션 엔트리포인트 — Flask(web) + APScheduler(scheduler) 동시 구동.
#
# 왜 한 컨테이너에 두 프로세스인가:
#   DB 가 sqlite 파일(data/db/drug_prices.db)이라 web·scheduler 가 같은 볼륨을
#   읽고 써야 한다. Fly.io 의 [processes] 분리는 머신이 갈라져 volume 공유 불가
#   → 단일 머신에서 둘을 함께 띄우고, 어느 한쪽이 죽으면 컨테이너 재시작.
# ─────────────────────────────────────────────────────────────────────────────
set -m

mkdir -p logs

python scheduler.py >> logs/scheduler.out 2>&1 &
SCHED_PID=$!
echo "[start] scheduler PID=$SCHED_PID"

python api/server.py &
WEB_PID=$!
echo "[start] web PID=$WEB_PID"

# 한쪽이라도 종료되면 전체 종료 → 플랫폼이 재시작 (둘 다 살아있어야 정상)
wait -n $SCHED_PID $WEB_PID
EXIT_CODE=$?
echo "[start] 프로세스 종료 감지 (exit=$EXIT_CODE) — 컨테이너 재시작 유도"
kill $SCHED_PID $WEB_PID 2>/dev/null
exit $EXIT_CODE
