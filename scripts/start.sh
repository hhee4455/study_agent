#!/usr/bin/env bash
# 자율 에이전트 시스템 시작 스크립트
#
# 사용법:
#   ./scripts/start.sh <spec.md> [추가 옵션]
#
# 예:
#   ./scripts/start.sh project.md --max-hours 24
#
# 스크립트는 백그라운드 실행 후 PID 파일에 저장하고 로그를 tail.
# Ctrl-C로 tail 종료 → 시스템은 계속 실행 (nohup 효과).
# 정지: ./scripts/stop.sh

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$ROOT_DIR"

# ── REQUIRE_WEB 가드 ────────────────────────────────────────────────────────
_rw_lower="$(echo "${REQUIRE_WEB:-}" | tr '[:upper:]' '[:lower:]')"
if [ "$_rw_lower" = "1" ] || [ "$_rw_lower" = "true" ] || [ "$_rw_lower" = "yes" ]; then
    if [ ! -d "$ROOT_DIR/web/static" ]; then
        echo "❌ [REQUIRE_WEB] web/static 디렉터리를 찾을 수 없습니다." >&2
        echo "   경로: $ROOT_DIR/web/static" >&2
        echo "" >&2
        echo "   웹 에셋을 먼저 빌드해 주세요:" >&2
        echo "     scripts/web-venv.sh build" >&2
        echo "" >&2
        echo "   웹 에셋 없이 시작하려면 REQUIRE_WEB를 비활성화하세요:" >&2
        echo "     unset REQUIRE_WEB  (또는 REQUIRE_WEB=0 으로 실행)" >&2
        exit 1
    fi
fi
unset _rw_lower
# ── /REQUIRE_WEB 가드 ───────────────────────────────────────────────────────

if [ $# -lt 1 ]; then
    echo "사용법: $0 <spec.md> [추가 옵션]"
    echo ""
    echo "예: $0 project.md --max-hours 24 --max-turns 2000"
    exit 1
fi

SPEC="$1"
shift

if [ ! -f "$SPEC" ]; then
    echo "❌ spec 파일 없음: $SPEC"
    exit 1
fi

# 환경 검증
echo "환경 검증..."
if ! command -v claude >/dev/null 2>&1; then
    echo "❌ claude CLI 미설치"
    echo "   설치: npm install -g @anthropic-ai/claude-code"
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ python3 미설치"
    exit 1
fi

# 디렉토리 기본값 — agent_system/ 옆 workspace/ 가정 (옵션 A 구조)
# LOG_DIR 은 CHECKPOINT 아래에 두어 stop.sh / status.sh 가 별도 인자 없이 같은 위치를 본다.
WORKSPACE="${WORKSPACE:-../workspace/ws/main}"
CHECKPOINT="${CHECKPOINT:-../workspace/state}"
LOG_DIR="${LOG_DIR:-$CHECKPOINT/logs}"
mkdir -p "$WORKSPACE" "$CHECKPOINT" "$LOG_DIR"

LOG_FILE="$LOG_DIR/run-$(date +%Y%m%d-%H%M%S).log"
PID_FILE="$LOG_DIR/lead.pid"

# 이미 실행 중인지
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "❌ 이미 실행 중 (PID $(cat "$PID_FILE")). 정지하려면 ./scripts/stop.sh"
    exit 1
fi

echo "🚀 시작"
echo "  spec:       $SPEC"
echo "  workspace:  $WORKSPACE"
echo "  checkpoint: $CHECKPOINT"
echo "  log:        $LOG_FILE"
echo ""

# nohup으로 백그라운드 실행
nohup python3 -u -m lead.main \
    --spec "$SPEC" \
    --workspace "$WORKSPACE" \
    --checkpoint "$CHECKPOINT" \
    "$@" \
    > "$LOG_FILE" 2>&1 &

PID=$!
echo "$PID" > "$PID_FILE"
echo "✅ PID $PID 백그라운드 실행"
echo ""
echo "Ctrl-C 하면 tail만 종료, 시스템은 계속 실행됨"
echo "정지하려면: ./scripts/stop.sh"
echo "================================================================"

# 로그 tail — TTY 일 때만 (Claude Code / nohup 같은 비-TTY 환경에서는 좀비 tail 안 만듦)
if [ -t 1 ]; then
    tail -f "$LOG_FILE"
else
    echo "비-TTY 환경 — tail 생략. 직접 따라잡으려면: tail -f $LOG_FILE"
fi
