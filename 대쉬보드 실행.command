#!/bin/bash
# Finder에서 더블클릭으로 실행됩니다
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$SCRIPT_DIR/scripts/run_dashboard.sh"
