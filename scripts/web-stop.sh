#!/usr/bin/env bash
# Web 대시보드 서버 정지.
#
# web.sh 와 동일 CHECKPOINT / LOG_DIR 컨벤션을 사용한다.
# SIGTERM → 최대 30초 대기 → SIGKILL fallback, PID 파일 정리.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT_DIR"

# web.sh 와 동일 default
CHECKPOINT="${CHECKPOINT:-../workspace/state}"
LOG_DIR="${LOG_DIR:-$CHECKPOINT/logs}"
PID_FILE="$LOG_DIR/web.pid"

usage() {
    echo "사용법: $0 [-h|--help]"
    echo ""
    echo "web.sh 로 기동한 uvicorn 서버를 PID 파일 기반으로 종료한다."
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage ;;
        *)
            echo "❌ 알 수 없는 옵션: $1"
            exit 1
            ;;
    esac
done

if [ ! -f "$PID_FILE" ]; then
    echo "PID 파일 없음 ($PID_FILE) — 실행 중이 아닐 수 있음"
    exit 0
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "PID $PID 프로세스 없음 — PID 파일만 정리"
    rm -f "$PID_FILE"
    exit 0
fi

echo "PID $PID 에 SIGTERM 전송..."
kill -TERM "$PID" 2>/dev/null || true

# 최대 30초 대기
for i in {1..30}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "✅ 정지됨 (${i}s)"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

echo "⚠️  30s 내 정지 안 됨 — SIGKILL"
kill -KILL "$PID" 2>/dev/null || true
sleep 1
rm -f "$PID_FILE"
echo "✅ 강제 종료 완료"
