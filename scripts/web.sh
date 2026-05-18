#!/usr/bin/env bash
# Web 대시보드 서버 시작 스크립트 (uvicorn / 127.0.0.1:8765)
#
# 사용법:
#   ./scripts/web.sh [옵션]
#
# 옵션:
#   -h, --help          이 메시지 출력
#   --workers N         uvicorn worker 수 (기본: 1)
#   --log-level LEVEL   uvicorn 로그 레벨 (기본: info)
#
# 기본 백그라운드 실행 후 PID 파일에 저장하고 로그를 tail.
# Ctrl-C 로 tail 종료 → 서버는 계속 실행 (nohup 효과).
# 정지: ./scripts/web-stop.sh

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT_DIR"

# start.sh 와 동일 default — CHECKPOINT 아래 logs/
CHECKPOINT="${CHECKPOINT:-../workspace/state}"
LOG_DIR="${LOG_DIR:-$CHECKPOINT/logs}"
PID_FILE="$LOG_DIR/web.pid"

# 서버가 state/ws/agents 등을 찾을 root. start.sh 의 --workspace 부모 디렉토리와 일치.
# get_ws_root() (web/server.py 등) 가 이 환경변수를 읽음.
export AGENT_WS_ROOT="${AGENT_WS_ROOT:-$(cd "$CHECKPOINT/.." && pwd)}"

WORKERS=1
LOG_LEVEL="info"

usage() {
    grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'
    exit 0
}

# 옵션 파싱
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            ;;
        --workers)
            WORKERS="${2:?--workers 에 숫자 필요}"
            shift 2
            ;;
        --log-level)
            LOG_LEVEL="${2:?--log-level 에 레벨 필요}"
            shift 2
            ;;
        *)
            echo "❌ 알 수 없는 옵션: $1"
            echo "   사용법: $0 --help"
            exit 1
            ;;
    esac
done

# 환경 검증
if ! command -v uvicorn >/dev/null 2>&1; then
    echo "❌ uvicorn 미설치 — pip install uvicorn 후 재시도"
    exit 1
fi

mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/web-$(date +%Y%m%d-%H%M%S).log"

# 이미 실행 중인지
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "❌ 이미 실행 중 (PID $(cat "$PID_FILE")). 정지하려면 ./scripts/web-stop.sh"
    exit 1
fi

echo "🚀 Web 서버 시작"
echo "  bind:     127.0.0.1:8765"
echo "  workers:  $WORKERS"
echo "  log-level: $LOG_LEVEL"
echo "  log:      $LOG_FILE"
echo ""

# nohup 으로 백그라운드 실행 — 비-TTY 환경에서도 정상 동작
nohup uvicorn web.server:app \
    --host 127.0.0.1 \
    --port 8765 \
    --workers "$WORKERS" \
    --log-level "$LOG_LEVEL" \
    > "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"
echo "✅ PID $PID 백그라운드 실행"
echo ""
echo "Ctrl-C 하면 tail 만 종료, 서버는 계속 실행됨"
echo "정지하려면: ./scripts/web-stop.sh"
echo "================================================================"

# 로그 tail — TTY 일 때만 (비-TTY 환경에서는 좀비 tail 안 만듦)
if [ -t 1 ]; then
    tail -f "$LOG_FILE"
else
    echo "비-TTY 환경 — tail 생략. 직접 따라잡으려면: tail -f $LOG_FILE"
fi
