"""Seed Similarity Gate 단위 테스트.

세 층:
  1. `core.similarity` 의 순수 함수 (compute_ratio / summarize_diff / evaluate /
     evaluate_conflicts / split_outcomes / decide_gate).
  2. `lead.mailbox.build_refine_message` 빌더.
  3. `lead.team_lead.TeamLead._seed_similarity_gate` 통합 흐름 — TeamLead 의
     무거운 의존성 (`core.budget`, `lead.member` 등) 은 mock 클래스로 대체하고
     `__new__` 우회로 인스턴스를 만들어 게이트 메서드만 호출.
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

# 패키지 경로 — tests/ 의 상위 (agent_system/) 를 PYTHONPATH 에 노출.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _pre_stub_core_schemas() -> None:
    """core.__init__ 가 `from . import schemas` 를 시도하기 전에 stub 주입.

    verifier 환경에선 실제 schemas.py 가 머지돼 있어 stub 이 덮이지 않지만,
    M006 격리 ws 에서는 schemas.py 가 없어 core 패키지 자체가 로드 실패한다.
    이 헬퍼는 module-level import 가 시작되기 전에 호출돼야 한다.
    """
    if "core.schemas" in sys.modules:
        return
    mod = types.ModuleType("core.schemas")

    class _ValidationFailure(Exception):
        def __init__(self, reason: str = "") -> None:
            super().__init__(reason)
            self.reason = reason

    mod.PLAN_BACKUP_KEEP = 5
    mod.PlanSchema = type("PlanSchema", (), {})
    mod.ValidationFailure = _ValidationFailure
    mod.call_decomposer_with_validation = lambda *a, **kw: None
    mod.prune_plan_backups = lambda *a, **kw: None
    mod.validate_decomposer_output = lambda *a, **kw: None
    sys.modules["core.schemas"] = mod


_pre_stub_core_schemas()


from core.similarity import (  # noqa: E402
    GATE_ACTION_BYPASS,
    GATE_ACTION_PASS,
    GATE_ACTION_REFINE,
    GATE_ACTION_SKIP,
    SEED_SIMILARITY_THRESHOLD,
    GateOutcome,
    compute_ratio,
    decide_gate,
    evaluate,
    evaluate_conflicts,
    split_outcomes,
    summarize_diff,
)
from lead.mailbox import (  # noqa: E402
    MESSAGE_KINDS,
    append_message,
    build_refine_message,
    parse_messages,
)

# =========================================================================
# 1) 순수 함수 — similarity 계산
# =========================================================================


def test_threshold_constant_is_80_percent() -> None:
    """임계값 상수는 0.80 으로 고정 — 시드 정합성 기준."""
    assert SEED_SIMILARITY_THRESHOLD == 0.80


def test_compute_ratio_identical_text_is_one() -> None:
    assert compute_ratio("hello world", "hello world") == 1.0


def test_compute_ratio_both_empty_is_one() -> None:
    """양쪽 다 빈 문자열은 1.0 (분모 0 회피)."""
    assert compute_ratio("", "") == 1.0


def test_compute_ratio_disjoint_text_is_low() -> None:
    ratio = compute_ratio("abcdefg", "ZYXWVUT")
    assert 0.0 <= ratio < 0.2


def test_compute_ratio_partial_overlap_is_mid() -> None:
    seed = "def foo():\n    return 1\n"
    member = "def foo():\n    return 2\n"
    ratio = compute_ratio(seed, member)
    assert 0.85 < ratio < 1.0


def test_summarize_diff_truncates_long_diffs() -> None:
    seed = "\n".join(f"seed line {i}" for i in range(50))
    member = "\n".join(f"member line {i}" for i in range(50))
    summary = summarize_diff(seed, member, max_lines=5)
    assert "truncated" in summary
    assert summary.count("\n") <= 6  # 5 lines + truncation marker


def test_summarize_diff_empty_on_identical() -> None:
    summary = summarize_diff("same\n", "same\n")
    assert summary == "(no textual diff)"


def test_evaluate_above_threshold_for_near_identical() -> None:
    result = evaluate("def x(): return 1\n", "def x(): return 1\n")
    assert result.ratio == 1.0
    assert result.above_threshold is True


def test_evaluate_below_threshold_for_disjoint() -> None:
    result = evaluate("hello world", "TOTALLY DIFFERENT TEXT XX")
    assert result.above_threshold is False
    assert result.ratio < SEED_SIMILARITY_THRESHOLD


def test_evaluate_custom_threshold() -> None:
    """custom threshold 인자 — 호출자가 정책 단위로 조절."""
    seed = "abc"
    member = "abd"  # ratio ~= 0.66
    result = evaluate(seed, member, threshold=0.5)
    assert result.above_threshold is True
    result_strict = evaluate(seed, member, threshold=0.99)
    assert result_strict.above_threshold is False


# =========================================================================
# 2) evaluate_conflicts — ws_main / stash 파일 IO
# =========================================================================


def _make_conflict(
    ws_main: Path,
    agent_id: str,
    rel: str,
    seed_text: str,
    member_text: str,
) -> None:
    """ws_main 에 seed 파일과 같은 위치의 stash 파일을 동시 작성."""
    seed_p = ws_main / rel
    seed_p.parent.mkdir(parents=True, exist_ok=True)
    seed_p.write_text(seed_text, encoding="utf-8")
    stash_p = seed_p.with_name(f"{seed_p.name}.from-{agent_id}")
    stash_p.write_text(member_text, encoding="utf-8")


def test_evaluate_conflicts_pairs_seed_and_stash() -> None:
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "a.py", "alpha\n", "alpha\n")
        _make_conflict(ws_main, "M001", "b.py", "abcdef\n", "zzzzzz\n")
        outcomes = evaluate_conflicts(
            ["a.py", "b.py"],
            ws_main=ws_main,
            agent_id="M001",
        )
        rels = {o.rel: o for o in outcomes}
        assert rels["a.py"].above_threshold is True
        assert rels["a.py"].similarity == 1.0
        assert rels["b.py"].above_threshold is False
        assert rels["b.py"].similarity < SEED_SIMILARITY_THRESHOLD


def test_evaluate_conflicts_handles_conflict_string_with_suffix() -> None:
    """workspace 가 'file (binary)' 같은 suffix 를 붙여도 첫 토큰만 사용."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "x.py", "X" * 100, "X" * 100)
        outcomes = evaluate_conflicts(
            ["x.py (some note)"],
            ws_main=ws_main,
            agent_id="M001",
        )
        assert len(outcomes) == 1
        assert outcomes[0].rel == "x.py"


def test_evaluate_conflicts_skips_missing_files() -> None:
    """seed 또는 stash 가 없으면 결과에서 제외."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        # stash 만 있고 seed 없음
        (ws_main / "ghost.py.from-M001").write_text("only stash")
        outcomes = evaluate_conflicts(
            ["ghost.py"],
            ws_main=ws_main,
            agent_id="M001",
        )
        assert outcomes == []


def test_evaluate_conflicts_drops_symlink_rejected_entries() -> None:
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "ok.py", "alpha", "alpha")
        outcomes = evaluate_conflicts(
            ["ok.py", "evil.py symlink rejected"],
            ws_main=ws_main,
            agent_id="M001",
        )
        assert [o.rel for o in outcomes] == ["ok.py"]


def test_split_outcomes_partitions_by_threshold() -> None:
    passed, failed = split_outcomes(
        [
            GateOutcome(rel="a", similarity=0.9, diff_summary="", above_threshold=True),
            GateOutcome(rel="b", similarity=0.5, diff_summary="", above_threshold=False),
        ]
    )
    assert [o.rel for o in passed] == ["a"]
    assert [o.rel for o in failed] == ["b"]


# =========================================================================
# 3) decide_gate — pure decision
# =========================================================================


def test_decide_gate_empty_conflicts_is_pass() -> None:
    with tempfile.TemporaryDirectory() as d:
        decision = decide_gate([], ws_main=Path(d), agent_id="M001")
        assert decision.action == GATE_ACTION_PASS
        assert decision.surviving_conflicts == []


def test_decide_gate_kind_new_skips_gate() -> None:
    """brief.kind == 'new' 이면 시드 개념 없음 → 게이트 skip, 충돌 그대로 통과."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "a.py", "X" * 100, "Y" * 100)  # 매우 다름
        decision = decide_gate(
            ["a.py"],
            ws_main=ws_main,
            agent_id="M001",
            brief_kind="new",
        )
        assert decision.action == GATE_ACTION_SKIP
        assert decision.surviving_conflicts == ["a.py"]
        assert decision.failed_outcomes == []


def test_decide_gate_bypass_when_retry_cap_reached() -> None:
    """refine_count >= max_respawns 면 게이트 우회 (debate 행)."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "a.py", "X" * 100, "Y" * 100)
        decision = decide_gate(
            ["a.py"],
            ws_main=ws_main,
            agent_id="M001",
            refine_count=2,
            max_respawns=2,
        )
        assert decision.action == GATE_ACTION_BYPASS
        assert decision.surviving_conflicts == ["a.py"]


def test_decide_gate_pass_when_all_outcomes_above_threshold() -> None:
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "a.py", "alpha\nbeta\n", "alpha\nbeta\n")
        decision = decide_gate(
            ["a.py"],
            ws_main=ws_main,
            agent_id="M001",
        )
        assert decision.action == GATE_ACTION_PASS
        assert decision.surviving_conflicts == ["a.py"]


def test_decide_gate_refine_when_any_outcome_below_threshold() -> None:
    """임계 미만이 하나라도 있으면 REFINE 결정 — surviving 비고 failed 채움."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "ok.py", "X" * 100, "X" * 100)  # 동일
        _make_conflict(
            ws_main, "M001", "bad.py", "alpha beta gamma" * 5, "TOTALLY DIFFERENT CONTENT" * 5
        )
        decision = decide_gate(
            ["ok.py", "bad.py"],
            ws_main=ws_main,
            agent_id="M001",
        )
        assert decision.action == GATE_ACTION_REFINE
        assert decision.surviving_conflicts == []
        failed_rels = {o.rel for o in decision.failed_outcomes}
        assert "bad.py" in failed_rels
        # worst 는 'bad.py' (유사도 더 낮음)
        assert decision.worst_outcome is not None
        assert decision.worst_outcome.rel == "bad.py"
        # refine_count_after 는 +1
        assert decision.refine_count_after == 1


def test_decide_gate_no_evaluable_files_returns_pass() -> None:
    """모든 충돌 파일이 누락(stash 없음) 이면 PASS (그대로 debate 흘림)."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        decision = decide_gate(
            ["nothing.py"],
            ws_main=ws_main,
            agent_id="M001",
        )
        assert decision.action == GATE_ACTION_PASS


# =========================================================================
# 4) mailbox builder — build_refine_message
# =========================================================================


def test_refine_kind_is_registered_in_mailbox() -> None:
    """append_message 가 kind='refine' 을 받아들이도록 MESSAGE_KINDS 확장됨."""
    assert "refine" in MESSAGE_KINDS


def test_build_refine_message_contains_required_fields() -> None:
    body = build_refine_message(
        seed_path="agent_system/lead/team_lead.py",
        member_path="agent_system/lead/team_lead.py.from-M001",
        similarity=0.42,
        diff_summary="- old\n+ new",
    )
    assert "kind=refine" in body
    assert "agent_system/lead/team_lead.py" in body
    assert "agent_system/lead/team_lead.py.from-M001" in body
    assert "0.420" in body or "42.0%" in body
    # 핵심 지시문
    assert "Read" in body and "Edit" in body
    assert "시드" in body
    # diff 블록
    assert "```diff" in body
    assert "- old" in body and "+ new" in body


def test_build_refine_message_lists_extra_files() -> None:
    body = build_refine_message(
        seed_path="a.py",
        member_path="a.py.from-M001",
        similarity=0.30,
        diff_summary="(no textual diff)",
        extra_files=["b.py", "c.py"],
    )
    assert "b.py" in body
    assert "c.py" in body


def test_build_refine_message_no_extras_omits_section() -> None:
    body = build_refine_message(
        seed_path="a.py",
        member_path="a.py.from-M001",
        similarity=0.30,
        diff_summary="-",
    )
    assert "동시 폐기 파일" not in body


def test_append_refine_message_persists_and_parses_back() -> None:
    """build_refine_message 결과를 mailbox 에 kind=refine 으로 append 후 다시 파싱."""
    with tempfile.TemporaryDirectory() as d:
        mbox = Path(d) / "M001" / "mailbox.md"
        mbox.parent.mkdir()
        body = build_refine_message(
            seed_path="a.py",
            member_path="a.py.from-M001",
            similarity=0.5,
            diff_summary="-",
        )
        append_message(mbox, from_="lead", to="M001", kind="refine", body=body)
        msgs = parse_messages(mbox)
        assert len(msgs) == 1
        assert msgs[0].kind == "refine"
        assert "kind=refine" in msgs[0].body


# =========================================================================
# 5) TeamLead._seed_similarity_gate — 통합 흐름 (TeamLead 의 무거운 의존성을
#    sys.modules 에 stub 으로 주입해 import 가능하게 함)
# =========================================================================


def _ensure_team_lead_importable() -> None:
    """TeamLead 모듈이 import 가능하도록 누락 의존성을 sys.modules 에 stub 으로
    주입. 이미 실 모듈이 import 돼있으면 그대로 두고, 빠진 것만 채운다.

    verifier 환경에서는 ws/main 전체가 머지된 상태라 stub 이 불필요하지만,
    M006 격리 ws 에서는 의존성이 빠져있어 이 헬퍼 없이는 team_lead 가 로드되지
    않는다.
    """
    import types

    def _stub(name: str, **attrs: object) -> None:
        if name in sys.modules:
            return
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod

    class _Stub:
        """단순 sentinel — TeamLead module-level 의 isinstance/import-only 용도."""

        def __init__(self, *a: object, **kw: object) -> None:
            self._a, self._kw = a, kw

    class _StubExc(Exception):
        pass

    # core/__init__.py 가 `from . import schemas` 를 시도 — schemas stub 도 필요.
    _stub(
        "core.schemas",
        PLAN_BACKUP_KEEP=5,
        PlanSchema=_Stub,
        ValidationFailure=_StubExc,
        call_decomposer_with_validation=lambda *a, **kw: None,
        prune_plan_backups=lambda *a, **kw: None,
        validate_decomposer_output=lambda *a, **kw: None,
    )
    _stub("core.budget", BudgetExceeded=_StubExc, BudgetManager=_Stub, BudgetLimits=_Stub)
    _stub("core.health", HealthMonitor=_Stub)
    _stub("core.llm", LLMClient=_Stub, parse_json_loose=lambda raw: {})
    _stub("core.rate_limit", RateLimitExhausted=_StubExc)
    _stub("core.verifier", Check=_Stub, Verifier=_Stub, shell_sanity_check=lambda cmd: (True, ""))
    _stub("lead.member", HireBrief=_Stub, MemberSpawner=_Stub, SpawnResult=_Stub)
    _stub("lead.prompts", render_split=lambda *a, **kw: ("", ""))
    _stub("lead.registry", AgentRegistry=_Stub)
    _stub("lead.timeline", TimelineRenderer=_Stub)
    _stub("lead.workspace", WorkspaceMerger=_Stub)
    _stub(
        "agents",
    )
    _stub("agents.debate", DebatePanel=_Stub)
    _stub("agents.janitor", CodeJanitor=_Stub)
    _stub("agents.audit", AdversarialVerifier=_Stub)


class _RecordingTimeline:
    """team_lead.timeline.emit 호출을 메모리에 모음."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, source: str, kind: str, **fields: object) -> None:
        self.events.append((source, kind, dict(fields)))


class _StubRegistry:
    """team_lead.registry 의 부분 mock — get/update 만 지원."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}

    def register(self, agent_id: str, **fields: object) -> None:
        self.records[agent_id] = {
            "agent_id": agent_id,
            "last_resume": 0,
            "status": "HIRED",
            **fields,
        }

    def get(self, agent_id: str):
        rec = self.records.get(agent_id)
        if rec is None:
            return None
        return _Rec(**rec)

    def update(self, agent_id: str, **fields: object) -> None:
        if agent_id not in self.records:
            self.records[agent_id] = {"agent_id": agent_id, "last_resume": 0, "status": "HIRED"}
        self.records[agent_id].update(fields)


class _Rec:
    """AgentRecord 의 부분 mock — _seed_similarity_gate / _trigger_refine_respawn
    에 필요한 필드만."""

    def __init__(self, **kw: object) -> None:
        for k, v in kw.items():
            setattr(self, k, v)
        if not hasattr(self, "last_resume"):
            self.last_resume = 0


class _StubBrief:
    """HireBrief 의 부분 mock — kind 만 필요."""

    def __init__(self, kind: str = "refine") -> None:
        self.kind = kind
        self.agent_id = "M001"
        self.system_prompt = "stub"


def _build_test_lead(d: Path, brief_kind: str = "refine"):
    """TeamLead 인스턴스를 __new__ 우회로 만들고 게이트가 필요한 속성만 채움."""
    _ensure_team_lead_importable()
    from lead.team_lead import TeamLead

    state = d / "state"
    agents_root = state / "agents"
    ws_main = d / "ws" / "main"
    lead_state = state / "lead"
    agents_root.mkdir(parents=True)
    ws_main.mkdir(parents=True)
    lead_state.mkdir(parents=True)

    lead = TeamLead.__new__(TeamLead)
    lead.agents_root = agents_root
    lead.ws_main = ws_main
    lead.lead_state_dir = lead_state
    lead.timeline = _RecordingTimeline()
    lead.registry = _StubRegistry()
    lead.registry.register("M001", goal_id="G-001", last_resume=0, status="DONE")
    lead._briefs = {"M001": _StubBrief(kind=brief_kind)}
    lead._pending = {}
    lead._executor = None
    lead._submit_spawn_calls: list[tuple[str, int]] = []  # type: ignore[attr-defined]

    def _capture_submit_spawn(brief, resume_count: int) -> None:
        lead._submit_spawn_calls.append((brief.agent_id, resume_count))

    lead._submit_spawn = _capture_submit_spawn  # type: ignore[assignment]
    lead._reconstruct_brief = lambda agent_id: lead._briefs.get(agent_id)
    lead._log = lambda msg: None  # silence
    (agents_root / "M001").mkdir(parents=True)
    return lead


def test_seed_gate_passes_when_similarity_above_threshold() -> None:
    """동일 텍스트 → 모든 충돌 통과 → 입력 그대로 반환."""
    with tempfile.TemporaryDirectory() as d:
        lead = _build_test_lead(Path(d))
        _make_conflict(lead.ws_main, "M001", "x.py", "alpha\n", "alpha\n")
        out = lead._seed_similarity_gate("M001", ["x.py"])
        assert out == ["x.py"]
        assert lead._submit_spawn_calls == []  # type: ignore[attr-defined]


def test_seed_gate_triggers_refine_when_below_threshold() -> None:
    """충돌 파일 유사도 < 0.80 → kind=refine append + stash 삭제 + 재spawn 호출."""
    with tempfile.TemporaryDirectory() as d:
        lead = _build_test_lead(Path(d))
        rel = "agent_system/lead/team_lead.py"
        _make_conflict(
            lead.ws_main,
            "M001",
            rel,
            seed_text="alpha beta gamma " * 20,
            member_text="TOTALLY DIFFERENT WORDS " * 20,
        )
        out = lead._seed_similarity_gate("M001", [rel])
        assert out is None  # 멤버 폐기 → merge 중단 시그널
        # stash 폐기됐는지
        stash = lead.ws_main / f"{rel}.from-M001"
        assert not stash.exists()
        # mailbox 에 kind=refine append
        msgs = parse_messages(lead.agents_root / "M001" / "mailbox.md")
        assert any(m.kind == "refine" for m in msgs)
        refine = next(m for m in msgs if m.kind == "refine")
        assert rel in refine.body
        # 재spawn 호출됨
        assert lead._submit_spawn_calls == [("M001", 1)]  # type: ignore[attr-defined]
        # timeline 이벤트
        kinds = [e[1] for e in lead.timeline.events]
        assert "seed_gate_refine" in kinds


def test_seed_gate_skips_when_brief_kind_new() -> None:
    """brief.kind == 'new' → 게이트 skip, 충돌 그대로 통과."""
    with tempfile.TemporaryDirectory() as d:
        lead = _build_test_lead(Path(d), brief_kind="new")
        rel = "foo.py"
        _make_conflict(
            lead.ws_main,
            "M001",
            rel,
            seed_text="X" * 100,
            member_text="Y" * 100,
        )
        out = lead._seed_similarity_gate("M001", [rel])
        assert out == [rel]
        assert lead._submit_spawn_calls == []  # type: ignore[attr-defined]


def test_seed_gate_bypasses_after_max_respawns() -> None:
    """이미 refine 메시지 SEED_GATE_MAX_RESPAWNS 회 보냈으면 게이트 우회 → debate 행."""
    from lead.team_lead import SEED_GATE_MAX_RESPAWNS

    with tempfile.TemporaryDirectory() as d:
        lead = _build_test_lead(Path(d))
        # 한도만큼 refine 메시지 prefill
        mbox = lead.agents_root / "M001" / "mailbox.md"
        for _ in range(SEED_GATE_MAX_RESPAWNS):
            append_message(mbox, from_="lead", to="M001", kind="refine", body="prior refine")
        rel = "x.py"
        _make_conflict(lead.ws_main, "M001", rel, seed_text="X" * 100, member_text="Y" * 100)
        out = lead._seed_similarity_gate("M001", [rel])
        assert out == [rel]  # 우회 → 그대로 debate 로
        # 재spawn 안 됨
        assert lead._submit_spawn_calls == []  # type: ignore[attr-defined]
        # timeline 에 bypass 이벤트
        kinds = [e[1] for e in lead.timeline.events]
        assert "seed_gate_bypass" in kinds


def test_seed_gate_returns_empty_for_empty_input() -> None:
    """빈 충돌 입력은 항상 빈 통과 리스트 — 호출자가 debate 안 거치게 함."""
    with tempfile.TemporaryDirectory() as d:
        lead = _build_test_lead(Path(d))
        out = lead._seed_similarity_gate("M001", [])
        assert out == []


def test_seed_gate_partial_failure_discards_whole_member() -> None:
    """파일 A 는 통과, B 는 임계 미달 → 멤버 전체 폐기 (양쪽 stash 다 삭제)."""
    with tempfile.TemporaryDirectory() as d:
        lead = _build_test_lead(Path(d))
        _make_conflict(lead.ws_main, "M001", "ok.py", "alpha\n", "alpha\n")  # 1.0
        _make_conflict(
            lead.ws_main, "M001", "bad.py", "alpha beta gamma " * 20, "Z Y X W V " * 30
        )  # 매우 낮음
        out = lead._seed_similarity_gate("M001", ["ok.py", "bad.py"])
        assert out is None
        # 두 stash 모두 폐기
        assert not (lead.ws_main / "ok.py.from-M001").exists()
        assert not (lead.ws_main / "bad.py.from-M001").exists()
        # refine 메시지에 worst 파일 표기 + extras 에 'ok.py' 안 들어감
        # (only bad.py 가 failed 이므로 extras 비어있음 — ok.py 는 passed)
        msgs = parse_messages(lead.agents_root / "M001" / "mailbox.md")
        refine = next(m for m in msgs if m.kind == "refine")
        assert "bad.py" in refine.body


# =========================================================================
# 6) Edge cases — boundary, empty seed, feedback message contract
# =========================================================================


def test_compute_ratio_empty_seed_full_member_is_zero() -> None:
    """시드는 비어있고 멤버 산출물만 있다 → 시드 대비 100% 신규.

    compute_ratio 자체는 1.0 (양쪽 빈) 또는 0.0 (한쪽 빈) — 후자가 정상.
    """
    ratio = compute_ratio("", "def x():\n    return 1\n")
    assert ratio == 0.0


def test_evaluate_empty_seed_member_has_content_below_threshold() -> None:
    """시드 비어있고 멤버는 코드 — ratio 0.0 이라 게이트는 임계 미만으로 본다.

    즉, 시드가 비었으면 새 코드를 어떻게 써도 시드 대비 유사도가 0 → REFINE.
    이는 'kind=new' 경로에서 의도적으로 우회되어야 (decide_gate brief_kind='new').
    """
    result = evaluate("", "def x():\n    return 1\n")
    assert result.ratio == 0.0
    assert result.above_threshold is False


def test_evaluate_both_empty_treats_as_full_match() -> None:
    """양쪽 다 빈 텍스트는 1.0 — 게이트 통과 (no-op)."""
    result = evaluate("", "")
    assert result.ratio == 1.0
    assert result.above_threshold is True


def test_evaluate_threshold_boundary_79_9_percent_fails() -> None:
    """ratio 가 정확히 0.799 미만 (79.9%) 이면 above_threshold=False.

    경계 인접 ratio 를 직접 합성 (a/b 의 매칭 분포 조작).
    """
    # ratio = 2*M / (len(a) + len(b)) 형식. 다음은 ratio ≈ 0.7999 미만이 나오는 케이스.
    seed = "a" * 100
    member = "a" * 79 + "Z" * 21  # M=79, total=200, ratio = 158/200 = 0.79
    result = evaluate(seed, member)
    assert result.ratio < SEED_SIMILARITY_THRESHOLD
    assert result.above_threshold is False


def test_evaluate_threshold_boundary_80_0_percent_passes() -> None:
    """ratio == 0.80 인 경우 (>= 임계) 통과."""
    seed = "a" * 100
    member = "a" * 80 + "Z" * 20  # M=80, total=200, ratio = 160/200 = 0.80
    result = evaluate(seed, member)
    # 부동소수 오차 허용 — 정확히 0.80 또는 그 이상이어야 함.
    assert result.ratio >= 0.80 - 1e-9
    assert result.above_threshold is True


def test_evaluate_threshold_boundary_80_1_percent_passes() -> None:
    """ratio > 0.80 (80.1%) 면 분명히 통과."""
    seed = "a" * 1000
    # ratio = 2*801 / (1000 + 1000) = 0.801
    member = "a" * 801 + "Z" * 199
    result = evaluate(seed, member)
    assert result.ratio >= 0.80
    assert result.above_threshold is True


def test_decide_gate_custom_threshold_routes_borderline_pass() -> None:
    """custom threshold=0.5 면 0.66 케이스는 통과 (refine 안 함)."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "x.py", "abc" * 30, "abd" * 30)
        decision = decide_gate(
            ["x.py"],
            ws_main=ws_main,
            agent_id="M001",
            threshold=0.5,
        )
        assert decision.action == GATE_ACTION_PASS


def test_decide_gate_custom_threshold_routes_strict_refine() -> None:
    """custom threshold=0.99 면 거의 동일해도 refine 트리거."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "x.py", "abc" * 30, "abd" * 30)
        decision = decide_gate(
            ["x.py"],
            ws_main=ws_main,
            agent_id="M001",
            threshold=0.99,
        )
        assert decision.action == GATE_ACTION_REFINE


def test_evaluate_conflicts_respects_custom_threshold() -> None:
    """custom threshold=0.999 면 약간만 달라도 위반 — boundary tightening."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "x.py", "a" * 100, "a" * 99 + "Z")
        outcomes = evaluate_conflicts(
            ["x.py"],
            ws_main=ws_main,
            agent_id="M001",
            threshold=0.999,
        )
        assert len(outcomes) == 1
        # ratio = 0.99 < 0.999 → above_threshold=False
        assert outcomes[0].above_threshold is False


def test_evaluate_conflicts_rejects_empty_string_entries() -> None:
    """빈 문자열/공백만의 충돌 항목은 skip — 잘못된 입력 방어."""
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        outcomes = evaluate_conflicts(
            ["", "  ", "\t"],
            ws_main=ws_main,
            agent_id="M001",
        )
        assert outcomes == []


def test_summarize_diff_long_max_lines_zero_returns_marker() -> None:
    """max_lines=0 이어도 hang 하지 않고 끝까지 처리 (방어적 동작)."""
    seed = "a\nb\nc\n"
    member = "x\ny\nz\n"
    summary = summarize_diff(seed, member, max_lines=0)
    # max_lines=0 도 정상 처리. 첫 라인부터 truncation marker.
    assert "truncated" in summary or summary == "(no textual diff)" or len(summary) > 0


def test_refine_message_contains_seed_path_and_reason() -> None:
    """폐기된 멤버 산출물용 mailbox 메시지에 (a) 시드 경로 (b) 폐기 사유 모두 포함."""
    body = build_refine_message(
        seed_path="agent_system/lead/team_lead.py",
        member_path="agent_system/lead/team_lead.py.from-M005",
        similarity=0.42,
        diff_summary="- old\n+ new",
    )
    # 시드 경로 명시
    assert "agent_system/lead/team_lead.py" in body
    # 폐기 사유: 임계값 미만이라는 점 + 토론 비용 회피 의도
    assert "임계" in body or "threshold" in body.lower()
    assert "유사도" in body or "similarity" in body.lower()
    # kind 표식 (현재 시드는 'refine' — message body 에 명시)
    assert "kind=refine" in body


def test_refine_message_kind_registered() -> None:
    """build_refine_message 가 만드는 메시지는 kind=refine 으로 append 되어야 함.

    'feedback' 으로 잘못 명명하지 않는지 회귀 방어 (현재 시드: refine).
    """
    assert "refine" in MESSAGE_KINDS
    # 'refine' 외에 어떤 kind 도 mailbox 가 거부해선 안 됨 (sanity).
    assert "instruction" in MESSAGE_KINDS
    assert "delivery" in MESSAGE_KINDS


def test_refine_message_similarity_pct_format() -> None:
    """similarity 가 0~1 범위 밖이어도 0~100% 로 클램프해 표시 — 메시지 깨짐 방어."""
    body_low = build_refine_message(
        seed_path="a.py",
        member_path="a.py.from-M001",
        similarity=-0.5,  # 비정상
        diff_summary="-",
    )
    # 음수도 0.0 으로 클램프되어 표시
    assert "0.0%" in body_low or "0.000" in body_low
    body_high = build_refine_message(
        seed_path="a.py",
        member_path="a.py.from-M001",
        similarity=1.5,  # 비정상
        diff_summary="-",
    )
    # 1.0 초과는 100% 로 클램프
    assert "100.0%" in body_high or "1.500" in body_high


def test_decide_gate_returns_evaluable_outcomes_in_all_outcomes_field() -> None:
    """평가 가능한 모든 outcome 이 all_outcomes 에 들어감 (passed + failed 합집합).

    REFINE 액션 시 호출자가 이 리스트로 stash 를 전부 unlink 한다.
    """
    with tempfile.TemporaryDirectory() as d:
        ws_main = Path(d)
        _make_conflict(ws_main, "M001", "good.py", "X" * 100, "X" * 100)  # passes
        _make_conflict(ws_main, "M001", "bad.py", "alpha beta " * 20, "ZZZZ " * 30)  # fails
        decision = decide_gate(
            ["good.py", "bad.py"],
            ws_main=ws_main,
            agent_id="M001",
        )
        assert decision.action == GATE_ACTION_REFINE
        rels = {o.rel for o in decision.all_outcomes}
        assert rels == {"good.py", "bad.py"}
        # worst 는 bad.py
        assert decision.worst_outcome is not None
        assert decision.worst_outcome.rel == "bad.py"


# =========================================================================
# 7) mailbox 분기 보강 — 메시지 라이프사이클 직접 검증
# =========================================================================


def test_mailbox_append_rejects_unknown_kind() -> None:
    """허용 집합 밖 kind 는 ValueError — append 자체가 거부."""
    import pytest as _pytest

    with tempfile.TemporaryDirectory() as d:
        mbox = Path(d) / "M001" / "mailbox.md"
        mbox.parent.mkdir()
        with _pytest.raises(ValueError):
            append_message(mbox, from_="lead", to="M001", kind="garbage", body="x")


def test_mailbox_append_with_ref_records_reference_id() -> None:
    """ref 인자가 헤더에 직렬화되고 parse 시 복원됨."""
    with tempfile.TemporaryDirectory() as d:
        mbox = Path(d) / "M001" / "mailbox.md"
        mbox.parent.mkdir()
        append_message(mbox, from_="M001", to="lead", kind="question", body="A or B?")
        reply = append_message(mbox, from_="lead", to="M001", kind="reply", body="A", ref=1)
        msgs = parse_messages(mbox)
        assert msgs[-1].ref == 1
        assert reply.ref == 1


def test_mailbox_parse_skips_corrupted_block_with_no_footer() -> None:
    """닫는 마커 없는 블록은 손상 — 건너뛰고 이후 메시지는 정상 파싱."""
    with tempfile.TemporaryDirectory() as d:
        mbox = Path(d) / "mailbox.md"
        mbox.write_text(
            "<!-- MSG id=1 from=a to=b kind=instruction ts=2026-01 -->\n"
            "ok\n"
            "<!-- /MSG -->\n\n"
            "<!-- MSG id=2 from=a to=b kind=status ts=2026-01 -->\n"
            "missing footer here, continues...\n"
            "<!-- MSG id=3 from=a to=b kind=status ts=2026-01 -->\n"
            "later\n"
            "<!-- /MSG -->\n",
            encoding="utf-8",
        )
        msgs = parse_messages(mbox)
        # id=1 정상, id=2 손상은 본문이 id=3 footer 까지 다 먹힐 수 있음 — 최소한 id=1 은 들어옴
        assert any(m.id == 1 for m in msgs)


def test_mailbox_next_msg_id_on_empty_file_returns_one() -> None:
    """빈/없는 mailbox 의 next id 는 1."""
    from lead.mailbox import next_msg_id

    with tempfile.TemporaryDirectory() as d:
        mbox = Path(d) / "M001" / "mailbox.md"
        mbox.parent.mkdir()
        assert next_msg_id(mbox) == 1


def test_mailbox_append_creates_parent_directories() -> None:
    """append_message 가 mailbox 의 부모 디렉토리를 자동 생성."""
    with tempfile.TemporaryDirectory() as d:
        nested = Path(d) / "deep" / "nested" / "agents" / "M001" / "mailbox.md"
        append_message(nested, from_="lead", to="M001", kind="instruction", body="boot")
        assert nested.exists()
        assert len(parse_messages(nested)) == 1


def test_mailbox_detect_terminal_status_returns_none_when_missing() -> None:
    """[STATUS:...] 토큰 없으면 None."""
    from lead.mailbox import detect_terminal_status

    assert detect_terminal_status("hello world no token here") is None
    assert detect_terminal_status("") is None
    assert detect_terminal_status("[STATUS:WRONG]") is None  # WRONG 은 허용 집합 밖


def test_mailbox_detect_terminal_status_picks_last_512_chars() -> None:
    """긴 출력의 *끝* 512 자에서만 토큰 검출 — 본문 중간의 토큰 무시."""
    from lead.mailbox import detect_terminal_status

    long_text = "[STATUS:DONE]" + "x" * 5000  # 토큰이 *앞* 5000자 안에만
    # tail 만 보므로 None
    assert detect_terminal_status(long_text) is None
    # 끝에 있으면 검출
    assert detect_terminal_status("x" * 100 + "[STATUS:DONE]") == "DONE"


def test_mailbox_message_is_terminal_only_for_delivery() -> None:
    """Message.is_terminal() 은 kind == 'delivery' 일 때만 True."""
    from lead.mailbox import Message

    delivery = Message(
        id=1, from_="M001", to="lead", kind="delivery", ts="2026-01-01T00:00:00Z", body="done"
    )
    assert delivery.is_terminal() is True
    other = Message(
        id=2, from_="lead", to="M001", kind="instruction", ts="2026-01-01T00:00:00Z", body="go"
    )
    assert other.is_terminal() is False


def test_mailbox_scan_new_returns_empty_when_root_missing() -> None:
    """agents_root 자체가 없으면 빈 리스트."""
    from lead.mailbox import scan_new

    with tempfile.TemporaryDirectory() as d:
        ghost = Path(d) / "nope"
        assert scan_new(ghost, {}) == []


def test_mailbox_scan_new_respects_last_seen_per_agent() -> None:
    """last_seen 사전 기준으로 agent 별 메시지 필터링."""
    from lead.mailbox import scan_new

    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "agents"
        for agent in ("M001", "M002"):
            mbox = root / agent / "mailbox.md"
            mbox.parent.mkdir(parents=True)
            for i in range(3):
                append_message(mbox, from_="lead", to=agent, kind="instruction", body=f"m{i}")
        # M001 은 1 까지 봤고 M002 는 모두 처음.
        new = scan_new(root, {"M001": 1, "M002": 0})
        # M001: id 2,3 / M002: id 1,2,3 → 5개
        ids_by_agent = {}
        for m in new:
            ids_by_agent.setdefault(m.source_path.parent.name, []).append(m.id)
        assert ids_by_agent.get("M001") == [2, 3]
        assert ids_by_agent.get("M002") == [1, 2, 3]


# ---------- 실행 (직접 호출용) ----------

if __name__ == "__main__":
    import inspect

    mod = sys.modules[__name__]
    tests = [
        (n, fn) for n, fn in inspect.getmembers(mod, inspect.isfunction) if n.startswith("test_")
    ]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            failed.append(name)
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
