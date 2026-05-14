#!/usr/bin/env bash
# 백그라운드 실행 중인 시스템 정지.
#
# lead 프로세스만 죽이면 자식 claude CLI 들이 orphan 으로 살아남아 새 run 의
# state/ 에 메시지를 흘리는 race condition 이 발생한다. 그래서 PID 의 자식
# 프로세스 트리를 *재귀적* 으로 모두 SIGTERM, grace 후 잔존은 SIGKILL.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT_DIR"

# start.sh 와 동일 default — CHECKPOINT 아래 logs/.
CHECKPOINT="${CHECKPOINT:-../workspace/state}"
LOG_DIR="${LOG_DIR:-$CHECKPOINT/logs}"
PID_FILE="$LOG_DIR/lead.pid"

# PID 의 자식 트리(손자 포함) 를 leaf 부터 시그널 — depth-first post-order.
# 인자: PID + signal.
signal_tree() {
    local pid=$1
    local sig=$2
    local child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        signal_tree "$child" "$sig"
    done
    kill -"$sig" "$pid" 2>/dev/null || true
}

# 트리에 살아있는 프로세스가 있나?
tree_alive() {
    local pid=$1
    if kill -0 "$pid" 2>/dev/null; then return 0; fi
    local child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        if tree_alive "$child"; then return 0; fi
    done
    return 1
}

# orphan claude 만 정리하는 헬퍼 (PID 파일이 없거나 lead 가 이미 죽은 케이스).
# SIGTERM 보내고 5s grace, 잔존 시 SIGKILL — stream-json claude 는 SIGTERM 무시 자주.
cleanup_orphan_claude() {
    local n
    n=$(pgrep -f "claude.*--append-system-prompt" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$n" = "0" ]; then return; fi
    echo "  orphan claude ${n}개 발견 — SIGTERM → 5s grace → SIGKILL"
    pkill -TERM -f "claude.*--append-system-prompt" 2>/dev/null || true
    local i
    for i in 1 2 3 4 5; do
        sleep 1
        n=$(pgrep -f "claude.*--append-system-prompt" 2>/dev/null | wc -l | tr -d ' ')
        if [ "$n" = "0" ]; then echo "  ✅ orphan 정리됨 (${i}s)"; return; fi
    done
    pkill -KILL -f "claude.*--append-system-prompt" 2>/dev/null || true
    sleep 1
    echo "  ✅ orphan SIGKILL 정리"
}

if [ ! -f "$PID_FILE" ]; then
    echo "PID 파일 없음 ($PID_FILE) — 실행 중이 아닐 수 있음"
    cleanup_orphan_claude
    exit 0
fi

PID=$(cat "$PID_FILE")
if ! kill -0 "$PID" 2>/dev/null; then
    echo "PID $PID 프로세스 없음 — orphan 확인 후 정리"
    cleanup_orphan_claude
    rm -f "$PID_FILE"
    exit 0
fi

# 자식 수 카운트 (보고용)
CHILD_COUNT=$(pgrep -P "$PID" 2>/dev/null | wc -l | tr -d ' ')
echo "PID ${PID} (+ 자식 ${CHILD_COUNT}개) 에 SIGTERM 전송..."
signal_tree "$PID" "TERM"

# 최대 30초 대기 — 트리 전체가 죽을 때까지
for i in {1..30}; do
    if ! tree_alive "$PID"; then
        echo "✅ 정지됨 (${i}s)"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

echo "⚠️  30s 내 정지 안 됨 — SIGKILL 트리"
signal_tree "$PID" "KILL"
sleep 1
# 그래도 남은 게 있으면 마지막 safety net
pkill -KILL -f "claude.*--append-system-prompt" 2>/dev/null || true
rm -f "$PID_FILE"
echo "✅ 강제 종료 완료"
