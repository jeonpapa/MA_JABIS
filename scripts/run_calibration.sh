#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# MA AI Dossier — MediaCalibrator 분기 실행 스크립트
#
# 설정 방법 (macOS crontab):
#   crontab -e
#   # 매년 1월·4월·7월·10월 1일 오전 9시에 실행
#   0 9 1 1,4,7,10 * /Users/kimjeong-ae/MA_AI_Dossier/scripts/run_calibration.sh
#
# 수동 실행:
#   bash scripts/run_calibration.sh
#   bash scripts/run_calibration.sh --dry-run   # 수집만 테스트
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/logs/calibration_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$PROJECT_DIR/logs"

echo "========================================"   | tee "$LOG_FILE"
echo "MediaCalibrator 시작: $(date)"              | tee -a "$LOG_FILE"
echo "프로젝트 경로: $PROJECT_DIR"                | tee -a "$LOG_FILE"
echo "========================================"   | tee -a "$LOG_FILE"

cd "$PROJECT_DIR" || exit 1

ARGS=""
if [[ "$1" == "--dry-run" ]]; then
    ARGS="--dry-run"
    echo "[dry-run 모드] 기사 수집만 진행합니다." | tee -a "$LOG_FILE"
fi

python3 agents/media_calibrator.py $ARGS 2>&1 | tee -a "$LOG_FILE"

EXIT_CODE=${PIPESTATUS[0]}
echo ""                                           | tee -a "$LOG_FILE"
echo "완료: $(date)  (exit: $EXIT_CODE)"          | tee -a "$LOG_FILE"
echo "로그: $LOG_FILE"

exit $EXIT_CODE
