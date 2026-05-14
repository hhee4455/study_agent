#!/usr/bin/env bash
# 다음 강화 사이클을 위한 워크스페이스 리셋.
#
# 보존: ws/main/ (완성된 코드), workspace 의 spec 파일들,
#       state/lead/plan.md (+ plan.replaced-*.md) — 다음 --replan 사이클의 백업 입력,
#       agent_system 자체.
# 삭제: ws/members/* (이미 머지 끝난 멤버 ws),
#       state/agents/, state/session_logs/, state/llm_logs/, state/logs/, budget.json,
#       state/lead/ 안의 events.jsonl / timeline.md / conflicts/.
#
# 사용법:
#   ./scripts/reset.sh                # dry-run (실제로는 안 지움, 무엇이 지워질지 표시만)
#   ./scripts/reset.sh --apply        # 실제 삭제 실행
#   ./scripts/reset.sh --archive      # 삭제 대신 .archive/{ts}/ 로 이동 (복구 가능)
#   ./scripts/reset.sh --apply --keep-budget   # budget.json 만 누적 유지

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT_DIR"

# start.sh / stop.sh 와 동일 default 경로.
CHECKPOINT="${CHECKPOINT:-../workspace/state}"
WS_ROOT="${WS_ROOT:-../workspace/ws}"
LOG_DIR="${LOG_DIR:-$CHECKPOINT/logs}"

MODE="dry-run"
KEEP_BUDGET=0

for arg in "$@"; do
    case "$arg" in
        --apply)   MODE="apply" ;;
        --archive) MODE="archive" ;;
        --keep-budget) KEEP_BUDGET=1 ;;
        -h|--help)
            cat <<'EOF'
다음 강화 사이클을 위한 워크스페이스 리셋.

보존: ws/main/ (완성된 코드), workspace/project.md (spec),
      state/lead/plan.md (+ plan.replaced-*.md) — 다음 --replan 사이클의 백업 입력,
      agent_system 자체.
삭제: ws/members/* (이미 머지 끝난 멤버 ws),
      state/agents/, state/session_logs/, state/llm_logs/, state/logs/, budget.json,
      state/lead/ 안의 events.jsonl / timeline.md / conflicts/.

사용법:
  ./scripts/reset.sh                       # dry-run (안 지움, 무엇이 지워질지 표시만)
  ./scripts/reset.sh --apply               # 실제 삭제 실행
  ./scripts/reset.sh --archive             # 삭제 대신 .archive/{ts}/ 로 이동 (복구 가능)
  ./scripts/reset.sh --apply --keep-budget # budget.json 만 누적 유지
EOF
            exit 0
            ;;
        *)
            echo "❌ 알 수 없는 옵션: $arg" >&2
            exit 2
            ;;
    esac
done

# lead 실행 중이면 거부 — 실행 중 상태 지우면 데이터 손상.
PID_FILE="$LOG_DIR/lead.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "❌ lead 실행 중 (PID $(cat "$PID_FILE")). 먼저 ./scripts/stop.sh 로 중지." >&2
    exit 1
fi

echo "=================================================================="
echo "Reset for next enhancement cycle"
echo "  mode:        $MODE"
echo "  checkpoint:  $CHECKPOINT"
echo "  ws_root:     $WS_ROOT"
echo "  keep_budget: $([ "$KEEP_BUDGET" -eq 1 ] && echo yes || echo no)"
echo "=================================================================="

# 대상 수집.
TARGETS=()

# 1) 멤버 격리 ws. 머지는 이미 ws/main 으로 끝났으므로 정리.
#    새 컨벤션: $WS_ROOT/members/M0XX
#    옛 컨벤션: $WS_ROOT/M0XX (이전 버전에서 사용된 형태 — 호환 처리)
if [ -d "$WS_ROOT/members" ]; then
    for d in "$WS_ROOT/members"/*; do
        [ -e "$d" ] && TARGETS+=("$d")
    done
fi
for d in "$WS_ROOT"/M[0-9][0-9][0-9]; do
    [ -e "$d" ] && TARGETS+=("$d")
done
# ws/ 의 잡 디렉토리 (.pytest_cache 등)
for stale_ws in "$WS_ROOT/.pytest_cache" "$WS_ROOT/__pycache__"; do
    [ -e "$stale_ws" ] && TARGETS+=("$stale_ws")
done

# 2) state/ 의 하위 항목. ws/main 과 spec 은 안 건드림.
if [ -d "$CHECKPOINT" ]; then
    for sub in agents session_logs llm_logs logs; do
        p="$CHECKPOINT/$sub"
        [ -e "$p" ] && TARGETS+=("$p")
    done
    # state/lead 는 통째 삭제 X. plan.md / plan.replaced-*.md 는 다음 --replan
    # 사이클의 백업 입력으로 보존하고, 나머지(events.jsonl, timeline.md, conflicts/) 만 정리.
    if [ -d "$CHECKPOINT/lead" ]; then
        for item in "$CHECKPOINT/lead"/*; do
            [ -e "$item" ] || continue
            base=$(basename "$item")
            case "$base" in
                plan.md|plan.replaced-*.md) ;;   # 보존
                *) TARGETS+=("$item") ;;
            esac
        done
    fi
    # budget.json — 옵션으로 보존 가능 (누적 비용 추적 유지 원할 때)
    if [ -f "$CHECKPOINT/budget.json" ] && [ "$KEEP_BUDGET" -eq 0 ]; then
        TARGETS+=("$CHECKPOINT/budget.json")
    fi
    # 옛 잔재 (이전 버전 start.sh 가 남긴 것)
    for stale in lead.pid lead.out; do
        p="$CHECKPOINT/$stale"
        [ -e "$p" ] && TARGETS+=("$p")
    done
fi

if [ ${#TARGETS[@]} -eq 0 ]; then
    echo "✅ 지울 것 없음 — 이미 깨끗"
    exit 0
fi

echo ""
echo "삭제 대상 (${#TARGETS[@]} 개):"
for t in "${TARGETS[@]}"; do
    size=$(du -sh "$t" 2>/dev/null | awk '{print $1}')
    echo "  - $t  (${size})"
done

echo ""
echo "보존 (검증용):"
echo "  - $WS_ROOT/main/                ws/main 완성 코드"
echo "  - ../workspace/project.md       spec (단일 source of truth)"
echo "  - $CHECKPOINT/lead/plan.md      다음 --replan 사이클의 백업 입력"
[ "$KEEP_BUDGET" -eq 1 ] && echo "  - $CHECKPOINT/budget.json       누적 비용 유지"

case "$MODE" in
    dry-run)
        echo ""
        echo "🔍 dry-run — 실제로 지우려면: $0 --apply  또는  --archive"
        ;;
    archive)
        TS=$(date +"%Y%m%dT%H%M%SZ")
        ARCHIVE_DIR="$CHECKPOINT/.archive-$TS"
        mkdir -p "$ARCHIVE_DIR"
        echo ""
        echo "📦 archive → $ARCHIVE_DIR"
        for t in "${TARGETS[@]}"; do
            # 상대 경로 유지하며 archive 안으로 이동
            base=$(basename "$t")
            mv "$t" "$ARCHIVE_DIR/$base"
            echo "  ↪ $base"
        done
        echo "✅ archive 완료. 복구하려면 $ARCHIVE_DIR 의 내용을 원위치로 이동."
        ;;
    apply)
        echo ""
        echo "🗑️  실제 삭제 진행 (Ctrl-C 로 5초 안에 취소 가능)..."
        sleep 5
        for t in "${TARGETS[@]}"; do
            rm -rf "$t"
            echo "  ✓ $t"
        done
        echo "✅ 삭제 완료. 다음 강화 사이클은 새 state 로 시작됨."
        ;;
esac
