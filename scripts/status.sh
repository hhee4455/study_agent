#!/usr/bin/env bash
# 시스템 상태 확인

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT_DIR"

CHECKPOINT="${CHECKPOINT:-./state}"
LOG_DIR="${LOG_DIR:-./logs}"
PID_FILE="$LOG_DIR/orchestrator.pid"

echo "=== 프로세스 ==="
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    PID=$(cat "$PID_FILE")
    echo "✅ 실행 중: PID $PID"
    ps -p "$PID" -o pid,etime,pcpu,pmem,cmd 2>/dev/null || true
else
    echo "❌ 실행 중 아님"
fi

echo ""
echo "=== 진행 상황 ==="
if [ -f "$CHECKPOINT/tasks.json" ]; then
    python3 -c "
import json
tasks = json.load(open('$CHECKPOINT/tasks.json'))
counts = {}
for t in tasks:
    counts[t['status']] = counts.get(t['status'], 0) + 1
print(f'총 작업: {len(tasks)}')
for status, n in sorted(counts.items()):
    print(f'  {status}: {n}')
"
else
    echo "tasks.json 없음 (아직 분해 전)"
fi

echo ""
echo "=== Budget ==="
if [ -f "$CHECKPOINT/budget.json" ]; then
    cat "$CHECKPOINT/budget.json" | python3 -m json.tool
fi

echo ""
echo "=== 결정 대기 ==="
DECISIONS_DIR="$CHECKPOINT/decisions"
if [ -d "$DECISIONS_DIR" ]; then
    PENDING=$(grep -L "^## 최종 결정$" "$DECISIONS_DIR"/*.md 2>/dev/null | xargs -I{} grep -l "운영자가 채울" {} 2>/dev/null || true)
    if [ -n "$PENDING" ]; then
        echo "⚠️  사람 결정 대기 중인 파일:"
        echo "$PENDING" | sed 's/^/  /'
    else
        echo "없음"
    fi
fi

echo ""
echo "=== 최근 로그 ==="
LATEST_LOG=$(ls -t "$LOG_DIR"/run-*.log 2>/dev/null | head -1 || true)
if [ -n "$LATEST_LOG" ]; then
    echo "파일: $LATEST_LOG"
    tail -20 "$LATEST_LOG"
fi
