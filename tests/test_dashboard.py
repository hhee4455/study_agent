"""lead/dashboard.py — collect/render/write 라운드트립 + 빈 state 그레이스풀 렌더."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

# `agent_system/` 을 sys.path 에 추가 — 패키지 형태로 import.
_AGENT_SYSTEM = Path(__file__).resolve().parent.parent
if str(_AGENT_SYSTEM) not in sys.path:
    sys.path.insert(0, str(_AGENT_SYSTEM))

from lead.dashboard import (  # noqa: E402
    BudgetSummary,
    ConflictEntry,
    DashboardState,
    GoalRow,
    MemberRow,
    collect_state,
    render_dashboard,
    write_dashboard,
)


def _make_state_tree(ws_root: Path) -> None:
    """현실적인 state/ 트리: budget.json + agents.json + plan.md + mailbox + conflict."""
    state = ws_root / "state"
    lead = state / "lead"
    agents = state / "agents"
    conflicts = lead / "conflicts"
    for d in (state, lead, agents, conflicts, agents / "M001", agents / "M002"):
        d.mkdir(parents=True, exist_ok=True)

    (state / "budget.json").write_text(
        json.dumps(
            {
                "started_at": time.time() - 3600,  # 1h 전 시작
                "turns": 7,
                "cost_usd": 1.2345,
                "tokens_in": 12_000,
                "tokens_out": 8_500,
            }
        )
    )

    (lead / "agents.json").write_text(
        json.dumps(
            {
                "M001": {
                    "status": "RUNNING",
                    "goal_id": "G-foo",
                    "last_msg_id": 3,
                    "hired_at": "2026-05-15T00:00:00Z",
                    "completed_at": "",
                    "last_resume": 0,
                    "last_error": "",
                    "cost_usd": 0.5500,
                    "last_session_id": "abc",
                },
                "M002": {
                    "status": "DONE",
                    "goal_id": "G-bar",
                    "last_msg_id": 4,
                    "hired_at": "2026-05-15T00:00:10Z",
                    "completed_at": "2026-05-15T00:10:00Z",
                    "last_resume": 0,
                    "last_error": "",
                    "cost_usd": 0.7800,
                    "last_session_id": "def",
                },
            }
        )
    )

    (lead / "plan.md").write_text(
        "# Plan\n"
        "\n"
        "- [x] G-bar: write README (assigned: M002)\n"
        "- [ ] G-foo: bootstrap python project (assigned: M001)\n"
        "- [ ] G-baz: build CI pipeline\n"
    )

    (agents / "M001" / "mailbox.md").write_text(
        "<!-- MSG id=1 from=lead to=M001 kind=instruction ts=2026-05-15T00:00:00Z -->\n"
        "초기 지시\n"
        "<!-- /MSG -->\n\n"
        "<!-- MSG id=2 from=M001 to=lead kind=status ts=2026-05-15T00:05:00Z -->\n"
        "진행 중\n"
        "<!-- /MSG -->\n"
    )
    (agents / "M001" / "status").write_text("RUNNING")
    (agents / "M002" / "status").write_text("DONE")

    (conflicts / "M002-20260515T000900Z.md").write_text("# Merge conflicts — M002\n")

    (lead / "events.jsonl").write_text(
        '{"ts":"2026-05-15T00:01:00Z","actor":"lead","kind":"llm_call",'
        '"model":"opus","cost_usd":0.40,"tokens_in":1000,"tokens_out":500}\n'
        '{"ts":"2026-05-15T00:02:00Z","actor":"lead","kind":"llm_call",'
        '"model":"sonnet","cost_usd":0.10,"tokens_in":500,"tokens_out":200}\n'
        '{"ts":"2026-05-15T00:02:30Z","actor":"lead","kind":"hire","agent_id":"M001"}\n'
    )


def test_render_dashboard_includes_expected_tokens(tmp_path: Path) -> None:
    _make_state_tree(tmp_path)
    state = collect_state(tmp_path)

    # 멤버 표
    member_ids = {m.agent_id for m in state.members}
    assert {"M001", "M002"} <= member_ids
    m001 = next(m for m in state.members if m.agent_id == "M001")
    assert m001.status == "RUNNING"
    assert m001.goal_id == "G-foo"
    assert m001.last_msg_ts == "2026-05-15T00:05:00Z"

    # 남은 goal 2개, 완료 1개
    pending_ids = {g.id for g in state.goals_pending}
    assert pending_ids == {"G-foo", "G-baz"}
    assert state.goals_done_count == 1

    # 충돌 1건
    assert len(state.conflicts) == 1
    assert state.conflicts[0].name == "M002-20260515T000900Z.md"

    # 예산
    assert state.budget.cost_usd == pytest.approx(1.2345)
    assert state.budget.tokens_in == 12_000
    assert state.budget.tokens_out == 8_500
    assert state.budget.turns == 7
    assert state.budget.elapsed_h >= 0.5  # 약 1h 전 시작

    # by-model 집계
    assert "opus" in state.budget.by_model
    assert state.budget.by_model["opus"]["calls"] == 1.0
    assert state.budget.by_model["opus"]["cost_usd"] == pytest.approx(0.40)
    assert "sonnet" in state.budget.by_model

    md = render_dashboard(state)
    # 핵심 토큰들이 markdown 에 들어가야 함
    assert "# Dashboard" in md
    assert "## Members" in md
    assert "M001" in md and "M002" in md
    assert "G-foo" in md and "G-baz" in md
    assert "$1.2345" in md  # 예산 USD
    assert "M002-20260515T000900Z.md" in md
    assert "opus" in md and "sonnet" in md


def test_write_dashboard_creates_file(tmp_path: Path) -> None:
    _make_state_tree(tmp_path)
    out = write_dashboard(tmp_path)
    assert out == tmp_path / "state" / "dashboard.md"
    assert out.exists()
    md = out.read_text(encoding="utf-8")
    assert "# Dashboard" in md
    assert "## Budget" in md


def test_empty_state_dir_renders_gracefully(tmp_path: Path) -> None:
    # state/ 만 만들고 비워둠
    (tmp_path / "state").mkdir()
    state = collect_state(tmp_path)
    assert state.members == []
    assert state.goals_pending == []
    assert state.goals_done_count == 0
    assert state.conflicts == []
    assert state.budget.cost_usd == 0.0
    md = render_dashboard(state)
    # 섹션 헤더는 다 있고, 본문은 "(없음)" 같은 fallback
    assert "## Members" in md
    assert "## Goals" in md
    assert "## Conflicts" in md
    assert "## Budget" in md
    assert "(없음)" in md  # members + conflicts fallback


def test_corrupt_budget_json_falls_back_to_zero(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "budget.json").write_text("{ this is not json")
    state = collect_state(tmp_path)
    assert state.budget.cost_usd == 0.0
    md = render_dashboard(state)
    assert "$0.0000" in md  # 안전 fallback 으로 렌더


def test_missing_agents_json_uses_disk_scan(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    (state_dir / "agents" / "M042").mkdir(parents=True)
    (state_dir / "agents" / "M042" / "status").write_text("WAITING")
    # mailbox 없어도 OK
    state = collect_state(tmp_path)
    assert any(m.agent_id == "M042" and m.status == "WAITING" for m in state.members)


def test_render_dashboard_pure_function() -> None:
    # collect 없이 직접 DashboardState 를 만들어도 잘 렌더.
    state = DashboardState(
        generated_at="2026-05-15T01:00:00Z",
        members=[MemberRow("M999", "RUNNING", "G-test", "2026-05-15T00:30:00Z", 0.12)],
        goals_pending=[GoalRow("G-test", "do thing", "M999", False)],
        goals_done_count=2,
        conflicts=[ConflictEntry("c.md", "2026-05-15T00:45:00Z")],
        budget=BudgetSummary(cost_usd=0.99, tokens_in=10, tokens_out=20, turns=3, elapsed_h=0.5),
    )
    md = render_dashboard(state)
    assert "M999" in md
    assert "$0.9900" in md
    assert "남은 1" in md
    assert "완료 2" in md


# ---------------------------------------------------------------------------
# Extended cases — M033 신규 추가
# dashboard.md 자동 생성 검증, 멤버/충돌/예산 카운트 라인, 30초 주기 재호출
# 시뮬레이션, 누락 budget.json graceful 처리, conflicts 디렉토리 비어있을 때
# fallback 등을 보강.
# ---------------------------------------------------------------------------


def test_write_dashboard_idempotent_periodic_trigger(tmp_path: Path) -> None:
    """30초 주기 트리거 시뮬: write_dashboard 를 두 번 호출 — 같은 파일을 안전하게 덮어쓴다."""
    _make_state_tree(tmp_path)
    out1 = write_dashboard(tmp_path)
    md1 = out1.read_text(encoding="utf-8")

    # 짧은 시간 후 다시 호출 (sleep 없이 — generated_at 만 다르면 OK)
    out2 = write_dashboard(tmp_path)
    md2 = out2.read_text(encoding="utf-8")

    assert out1 == out2  # 동일 경로
    # 핵심 콘텐츠 (멤버 / 예산) 는 동일
    assert "M001" in md1 and "M001" in md2
    assert "$1.2345" in md1 and "$1.2345" in md2


def test_dashboard_includes_member_status_counts(tmp_path: Path) -> None:
    """active/queued/conflicts 라인의 의미적 카운트가 렌더에 반영된다."""
    _make_state_tree(tmp_path)
    state = collect_state(tmp_path)

    # state 단의 active(RUNNING) / done / conflict 카운트
    running = [m for m in state.members if m.status == "RUNNING"]
    done = [m for m in state.members if m.status == "DONE"]
    assert len(running) == 1  # M001
    assert len(done) == 1  # M002
    # conflicts 1건
    assert len(state.conflicts) == 1

    md = render_dashboard(state)
    # 충돌 N건이 헤더에 노출되어야 함 (verification: format 가시화)
    assert "1 파일 미해결" in md


def test_missing_budget_json_renders_zero_cost(tmp_path: Path) -> None:
    """budget.json 자체가 없을 때 — spent=$0 fallback 가 렌더에 반영."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # budget.json 미생성, agents/ 도 비어있음
    state = collect_state(tmp_path)
    assert state.budget.cost_usd == 0.0
    md = render_dashboard(state)
    assert "$0.0000" in md
    # 활동 0건일 때도 섹션 헤더는 모두 존재 (graceful)
    assert "## Members" in md
    assert "## Budget" in md


def test_format_status_line_can_be_embedded_in_md(tmp_path: Path) -> None:
    """format_status_line 출력 — active/queued/conflicts/spent/eta 5종 키워드 보장.

    이 형식은 verification_check.status_line_format 가 대시보드/팀장 라인에서
    찾는 패턴. dashboard.md 가 라인을 embed 하는지 여부와 별개로, 포맷 자체가
    검증 키워드를 모두 노출해야 한다.
    """
    from core.budget import format_status_line

    line = format_status_line(
        active=2,
        queued=3,
        conflicts=1,
        spent_usd=4.56,
        eta_minutes=7,
    )
    for kw in ("active=", "queued=", "conflicts=", "spent=$", "eta="):
        assert kw in line, f"missing keyword {kw!r} in {line!r}"


def test_dashboard_handles_no_conflicts_gracefully(tmp_path: Path) -> None:
    """conflicts 디렉토리는 있지만 .md 파일 없음 → '(없음)' fallback."""
    state_dir = tmp_path / "state"
    (state_dir / "lead" / "conflicts").mkdir(parents=True)
    state = collect_state(tmp_path)
    assert state.conflicts == []
    md = render_dashboard(state)
    assert "(없음)" in md
    # 충돌 헤더는 0건임을 노출
    assert "0 파일" in md


def test_dashboard_collects_multiple_conflicts_sorted(tmp_path: Path) -> None:
    """conflicts 디렉토리에 2개 .md 가 있으면 모두 수집되고 sorted 순서."""
    conflicts = tmp_path / "state" / "lead" / "conflicts"
    conflicts.mkdir(parents=True)
    (conflicts / "M001-aaa.md").write_text("# c1", encoding="utf-8")
    (conflicts / "M002-bbb.md").write_text("# c2", encoding="utf-8")
    state = collect_state(tmp_path)
    assert len(state.conflicts) == 2
    names = [c.name for c in state.conflicts]
    assert names == sorted(names)


def test_write_dashboard_creates_parent_dir(tmp_path: Path) -> None:
    """ws_root 만 있고 state/ 가 아직 없어도 write_dashboard 가 디렉토리 보장."""
    # state/ 미존재
    out = write_dashboard(tmp_path)
    assert out.exists()
    assert out.parent == tmp_path / "state"
    md = out.read_text(encoding="utf-8")
    # 빈 상태에서도 핵심 섹션 모두 존재
    for header in ("# Dashboard", "## Members", "## Goals", "## Conflicts", "## Budget"):
        assert header in md


def test_dashboard_includes_member_cost_column(tmp_path: Path) -> None:
    """멤버 표에 cost_usd 컬럼이 렌더되어 멤버별 비용 가시화."""
    _make_state_tree(tmp_path)
    md = render_dashboard(collect_state(tmp_path))
    # M001.cost_usd = 0.5500, M002.cost_usd = 0.7800 → $0.5500 / $0.7800 셀
    assert "$0.5500" in md
    assert "$0.7800" in md


def test_dashboard_handles_missing_mailbox(tmp_path: Path) -> None:
    """agents.json 에 있지만 mailbox.md 가 없는 멤버 → last_msg_ts="" graceful."""
    state_dir = tmp_path / "state"
    (state_dir / "lead").mkdir(parents=True)
    (state_dir / "agents" / "M100").mkdir(parents=True)
    (state_dir / "lead" / "agents.json").write_text(
        json.dumps(
            {
                "M100": {
                    "status": "RUNNING",
                    "goal_id": "G-test",
                    "last_msg_id": 0,
                    "hired_at": "",
                    "completed_at": "",
                    "last_resume": 0,
                    "last_error": "",
                    "cost_usd": 0.0,
                    "last_session_id": "",
                }
            }
        )
    )
    state = collect_state(tmp_path)
    m100 = next(m for m in state.members if m.agent_id == "M100")
    assert m100.last_msg_ts == ""
    md = render_dashboard(state)
    # 빈 ts 는 '-' 로 렌더되어야 (셀 깨짐 방지)
    assert "M100" in md


def test_render_dashboard_keeps_section_order(tmp_path: Path) -> None:
    """섹션 순서: Members → Goals → Conflicts → Budget — 외부 grep 안정성."""
    _make_state_tree(tmp_path)
    md = render_dashboard(collect_state(tmp_path))
    idx_members = md.index("## Members")
    idx_goals = md.index("## Goals")
    idx_conflicts = md.index("## Conflicts")
    idx_budget = md.index("## Budget")
    assert idx_members < idx_goals < idx_conflicts < idx_budget


def test_dashboard_corrupt_agents_json_falls_back_to_disk_scan(tmp_path: Path) -> None:
    """agents.json 손상 → 디스크 agents/* 디렉토리 스캔으로 자동 fallback."""
    state_dir = tmp_path / "state"
    (state_dir / "lead").mkdir(parents=True)
    (state_dir / "lead" / "agents.json").write_text("{ not valid json", encoding="utf-8")
    (state_dir / "agents" / "M050").mkdir(parents=True)
    (state_dir / "agents" / "M050" / "status").write_text("DONE", encoding="utf-8")

    state = collect_state(tmp_path)
    found = [m for m in state.members if m.agent_id == "M050"]
    assert len(found) == 1
    assert found[0].status == "DONE"
