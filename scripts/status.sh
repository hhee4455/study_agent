#!/usr/bin/env bash
# 시스템 상태 확인

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT_DIR"

# start.sh / stop.sh 와 동일 default.
CHECKPOINT="${CHECKPOINT:-../workspace/state}"
LOG_DIR="${LOG_DIR:-$CHECKPOINT/logs}"
PID_FILE="$LOG_DIR/lead.pid"

echo "=== 프로세스 ==="
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    PID=$(cat "$PID_FILE")
    echo "✅ 실행 중: PID $PID"
    ps -p "$PID" -o pid,etime,pcpu,pmem,cmd 2>/dev/null || true
else
    echo "❌ 실행 중 아님 ($PID_FILE)"
fi

echo ""
echo "=== 진행 상황 (plan.md) ==="
PLAN_MD="$CHECKPOINT/lead/plan.md"
if [ -f "$PLAN_MD" ]; then
    DONE=$(grep -c '^- \[x\]' "$PLAN_MD" || true)
    TODO=$(grep -c '^- \[ \]' "$PLAN_MD" || true)
    TOTAL=$((DONE + TODO))
    echo "  완료: $DONE / 총 $TOTAL"
    if [ "$TODO" -gt 0 ]; then
        echo "  미완:"
        grep '^- \[ \]' "$PLAN_MD" | sed 's/^/    /'
    fi
else
    echo "  plan.md 없음 (아직 분해 전)"
fi

echo ""
echo "=== Budget ==="
if [ -f "$CHECKPOINT/budget.json" ]; then
    python3 -m json.tool < "$CHECKPOINT/budget.json"
else
    echo "  budget.json 없음"
fi

echo ""
echo "=== Registry 상태 ==="
AGENTS_JSON="$CHECKPOINT/lead/agents.json"
if [ -f "$AGENTS_JSON" ]; then
    python3 -c "
import json
data = json.load(open('$AGENTS_JSON'))
counts = {}
for rec in data.values():
    s = rec.get('status', '?')
    counts[s] = counts.get(s, 0) + 1
print(f'  총 에이전트: {len(data)}')
for s, n in sorted(counts.items()):
    print(f'    {s}: {n}')
"
else
    echo "  agents.json 없음"
fi

echo ""
echo "=== 최근 timeline ==="
TIMELINE="$CHECKPOINT/lead/timeline.md"
if [ -f "$TIMELINE" ]; then
    tail -15 "$TIMELINE"
else
    echo "  timeline.md 없음"
fi

echo ""
echo "=== 최근 실행 로그 ==="
LATEST_LOG=$(ls -t "$LOG_DIR"/run-*.log 2>/dev/null | head -1 || true)
if [ -n "$LATEST_LOG" ]; then
    echo "파일: $LATEST_LOG"
    tail -20 "$LATEST_LOG"
fi
