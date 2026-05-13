#!/usr/bin/env bash
# 백그라운드 실행 중인 시스템 정지

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT_DIR"

# start.sh 와 동일 default — CHECKPOINT 아래 logs/.
CHECKPOINT="${CHECKPOINT:-../workspace/state}"
LOG_DIR="${LOG_DIR:-$CHECKPOINT/logs}"
PID_FILE="$LOG_DIR/lead.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "PID 파일 없음 ($PID_FILE) — 실행 중이 아닐 수 있음"
    exit 0
fi

PID=$(cat "$PID_FILE")
if ! kill -0 "$PID" 2>/dev/null; then
    echo "PID $PID 프로세스 없음 — 정리"
    rm -f "$PID_FILE"
    exit 0
fi

echo "PID $PID에 SIGTERM 전송..."
kill "$PID"

# 최대 30초 대기
for i in {1..30}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "✅ 정지됨"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

echo "⚠️  정지 안 됨 — SIGKILL"
kill -9 "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
