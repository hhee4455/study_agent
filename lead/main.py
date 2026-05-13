"""lead/main.py — 팀장 시스템 진입점.

  python -m lead.main --spec requirements.md --workspace ws/main --checkpoint <state_dir>

종료 코드:
  0   완료
  3   진행 가능 작업 없음 (정체)
  4   budget 또는 rate limit 한도
  5   사람 결정 필요 (decisions/*.md)
  6   claude CLI 미설치/로그인 안 됨
  130 사용자 중단
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 패키지 경로 (직접 실행 시)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.budget import BudgetExceeded, BudgetLimits, BudgetManager
from core.cli_caller import make_codex_raw_factory, make_raw_llm_factory
from core.health import HealthExhausted, HealthMonitor
from core.llm import LLMClient

from lead.team_lead import TeamLead


EXIT_OK = 0
EXIT_NO_PROGRESS = 3
EXIT_BUDGET = 4
EXIT_HUMAN_NEEDED = 5
EXIT_CLAUDE_MISSING = 6
EXIT_HEALTH = 7
EXIT_INTERRUPT = 130


def _preflight(skip: bool) -> None:
    """claude CLI 설치 + 로그인 확인."""
    if skip:
        return
    import shutil, subprocess
    if not shutil.which("claude"):
        print("❌ claude CLI 미설치 (npm i -g @anthropic-ai/claude-code)", file=sys.stderr)
        sys.exit(EXIT_CLAUDE_MISSING)
    try:
        proc = subprocess.run(
            ["claude", "-p", "--output-format", "json", "ping"],
            input="", capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("⚠️  preflight 30s 타임아웃 — 계속", file=sys.stderr)
        return
    if proc.returncode != 0:
        err = (proc.stderr or "").lower()
        if any(k in err for k in ("login", "auth", "sign in", "not authenticated")):
            print("❌ claude CLI 로그인 필요 — `claude login`", file=sys.stderr)
            sys.exit(EXIT_CLAUDE_MISSING)
        print(f"⚠️  preflight 실패 (계속): {proc.stderr[:200]}", file=sys.stderr)


def parse_args():
    p = argparse.ArgumentParser(
        description="팀장-팀원 에이전트 시스템 (lead entry)"
    )
    p.add_argument("--spec", required=True, help="요구서 .md")
    p.add_argument("--workspace", required=True, help="메인 워크스페이스 (ws/main)")
    p.add_argument("--checkpoint", required=True, help="상태 디렉토리 (<state_dir>)")
    p.add_argument("--max-hours", type=float, default=12.0)
    p.add_argument("--max-cost-usd", type=float, default=float("inf"))
    p.add_argument("--max-turns", type=int, default=2000)
    p.add_argument("--model", default="opus",
                   help="기본 모델 (가벼운 작업은 sonnet/haiku로 자동 라우팅)")
    p.add_argument("--skip-preflight", action="store_true")
    p.add_argument(
        "--enable-evaluator", action="store_true",
        help="Anthropic critique-refine 패턴: 각 멤버 산출물에 AdversarialVerifier 1회 통과. 비용 증가, 품질 ↑.",
    )
    p.add_argument(
        "--max-parallel", type=int, default=3,
        help="동시 실행 가능한 팀원 수 (default 3). 너무 크면 burst rate limit + 충돌 ↑. 1=직렬.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    _preflight(args.skip_preflight)

    spec_path = Path(args.spec).resolve()
    if not spec_path.exists():
        print(f"❌ spec 파일 없음: {spec_path}", file=sys.stderr)
        return 1
    spec = spec_path.read_text(encoding="utf-8")
    spec_name = spec_path.name

    state_dir = Path(args.checkpoint).resolve()
    ws_main = Path(args.workspace).resolve()
    ws_root = ws_main.parent
    if ws_main.name != "main":
        print(
            f"⚠️  --workspace 의 마지막 컴포넌트는 'main' 권장 (실제: {ws_main.name!r}). "
            f"머지 자체는 동작하지만 seed_files 복사 (ws_root/main 경로 가정)가 깨질 수 있음.",
            file=sys.stderr,
        )

    lead_state_dir = state_dir / "lead"
    agents_root = state_dir / "agents"
    session_logs_root = state_dir / "session_logs"

    for d in (state_dir, lead_state_dir, agents_root, session_logs_root, ws_root, ws_main):
        d.mkdir(parents=True, exist_ok=True)

    # Budget + LLM
    limits = BudgetLimits(
        max_hours=args.max_hours,
        max_cost_usd=args.max_cost_usd,
        max_turns=args.max_turns,
    )
    budget = BudgetManager(limits, state_dir / "budget.json")
    llm = LLMClient(
        raw_caller_factory=make_raw_llm_factory(
            llm_log_dir=state_dir / "llm_logs"
        ),
        codex_factory=make_codex_raw_factory(
            llm_log_dir=state_dir / "llm_logs"
        ),
        default_model=args.model,
        budget=budget.record,
    )

    # Health (optional)
    health = HealthMonitor(state_dir, ws_main)

    lead = TeamLead(
        spec=spec,
        spec_name=spec_name,
        state_dir=state_dir,
        lead_state_dir=lead_state_dir,
        agents_root=agents_root,
        session_logs_root=session_logs_root,
        ws_root=ws_root,
        ws_main=ws_main,
        llm=llm,
        budget=budget,
        health=health,
        default_model=args.model,
        enable_evaluator=args.enable_evaluator,
        max_parallel=args.max_parallel,
    )

    try:
        return lead.run()
    except BudgetExceeded as e:
        print(f"\n💰 {e}", flush=True)
        return EXIT_BUDGET
    except HealthExhausted as e:
        print(f"\n🏥 {e}", flush=True)
        return EXIT_HEALTH
    except KeyboardInterrupt:
        print("\n⏹️  사용자 중단", flush=True)
        return EXIT_INTERRUPT


if __name__ == "__main__":
    sys.exit(main())
