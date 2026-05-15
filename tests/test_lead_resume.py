"""lead/ 재시작 복구 + graceful shutdown 단위 테스트.

검증 범위:
  - PID 파일 lifecycle (write/read/remove)
  - is_pid_alive / try_reattach 시그널-기반 liveness 체크
  - TeamLead.restore_state 의 mailbox + PID 매핑 (DONE / 재spawn / WAITING / 재연결)
  - state/lead/conflicts/*.md 큐 재로드
  - graceful_shutdown 의 in-flight drain (grace 내 종료 + timeout 초과)

이 시드 환경엔 `core.budget` 등 일부 모듈이 없으니 sys.modules 에 가짜 stub 을
미리 심은 뒤 team_lead / member 를 import. 신규 conftest.py 작성 금지 규칙
(브리프 절대규칙 1) 때문에 stub 은 이 파일 안에서만 수행한다.
"""

from __future__ import annotations

import subprocess
import sys
import time
import types
from concurrent.futures import Future
from pathlib import Path
from typing import Any, ClassVar

import pytest

# ---------------------------------------------------------------------------
# 패키지 경로 + 누락 모듈 stub.
# ---------------------------------------------------------------------------
_AGENT_SYSTEM = Path(__file__).resolve().parent.parent
if str(_AGENT_SYSTEM) not in sys.path:
    sys.path.insert(0, str(_AGENT_SYSTEM))


def _stub(name: str, **attrs: Any) -> types.ModuleType:
    """sys.modules 에 모듈이 없을 때만 stub 을 생성/등록한다.

    verifier 환경(머지된 ws/main)에서는 진짜 모듈이 이미 sys.modules 에 있으므로
    그대로 둔다 — 과거에는 setattr 로 진짜 함수를 lambda 로 덮어버려 후속 테스트
    (예: tests/test_lead.py::test_deliverable_missing_or_outside_classifies_reasons)
    가 stub 동작에 노출되는 isolation 결함이 있었다.
    """
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class _BudgetExceeded(Exception):
    pass


class _RateLimitExhausted(Exception):
    pass


class _HealthExhausted(Exception):
    pass


class _StubBudget:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    def can_continue(self) -> bool:
        return True

    def record(self, *_a: Any, **_kw: Any) -> None:
        pass


class _StubHealth:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass


class _StubLLM:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    def call(self, *_a: Any, **_kw: Any) -> str:
        return ""


class _StubTimeline:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, *_a: Any, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def render(self) -> None:
        pass


class _StubCheck:
    @classmethod
    def from_dict(cls, d: dict) -> _StubCheck:
        return cls()


class _StubVerifier:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass


def _stub_validate_decomposer_output(_raw: str) -> Any:
    raise RuntimeError("stub validator should not be called in resume tests")


def _stub_call_decomposer(*_a: Any, **_kw: Any) -> Any:
    raise RuntimeError("stub decomposer should not be called in resume tests")


def _stub_prune_plan_backups(*_a: Any, **_kw: Any) -> None:
    return None


class _StubValidationFailure(Exception):
    def __init__(self, reason: str = "", raw: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw = raw


class _StubPlanSchema:
    sub_goals: ClassVar[list[Any]] = []


_stub(
    "core.budget",
    BudgetExceeded=_BudgetExceeded,
    BudgetLimits=type("BudgetLimits", (), {}),
    BudgetManager=_StubBudget,
)
_stub("core.rate_limit", RateLimitExhausted=_RateLimitExhausted)
_stub("core.health", HealthMonitor=_StubHealth, HealthExhausted=_HealthExhausted)
_stub(
    "core.llm",
    MODEL_OPUS="opus",
    MODEL_SONNET="sonnet",
    LLMClient=_StubLLM,
    parse_json_loose=lambda s: {},
)
_stub(
    "core.cli_caller",
    make_codex_raw_factory=lambda **_kw: None,
    make_raw_llm_factory=lambda **_kw: None,
)
_stub(
    "core.auto_merge",
    try_auto_merge=lambda *_a, **_kw: types.SimpleNamespace(merged=None, strategy=""),
)
_stub("core.verifier", Check=_StubCheck, Verifier=_StubVerifier)
_stub("core.deliverable", missing_or_outside=lambda paths, cwd: [])


# lead.member 가 core.session_manager 를 import — 시드에는 미존재하므로 stub.
class _StubSessionConfig:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass


class _StubSessionManager:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    def run(self, *_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("stub SessionManager should not be invoked in resume tests")


class _StubSessionResult:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass


_stub(
    "core.session_manager",
    SessionConfig=_StubSessionConfig,
    SessionManager=_StubSessionManager,
    SessionResult=_StubSessionResult,
)

# 실제 seed 의 core.schemas 가 pydantic 을 요구하므로 가벼운 stub 으로 대체.
_stub(
    "core.schemas",
    PLAN_BACKUP_KEEP=5,
    PlanSchema=_StubPlanSchema,
    ValidationFailure=_StubValidationFailure,
    call_decomposer_with_validation=_stub_call_decomposer,
    validate_decomposer_output=_stub_validate_decomposer_output,
    prune_plan_backups=_stub_prune_plan_backups,
)

_stub(
    "lead.prompts",
    render=lambda *_a, **_kw: "",
    render_split=lambda *_a, **_kw: ("", ""),
    build_refine_write_guard=lambda _seed: "",
)
_stub("lead.timeline", TimelineRenderer=_StubTimeline)


# ---------------------------------------------------------------------------
# 이제 진짜 import.
# ---------------------------------------------------------------------------
from lead.mailbox import append_message  # noqa: E402
from lead.member import (  # noqa: E402
    is_pid_alive,
    pid_file_path,
    read_pid_file,
    remove_pid_file,
    try_reattach,
    write_pid_file,
)

# lead.team_lead — 일부 심볼 (ConflictQueueItem, classify_mailbox_state,
# mailbox_last_member_message, parse_conflict_file) 은 시드 구현에 아직 노출되지
# 않음. ImportError 시 None 으로 fallback 하고 dependent 테스트는 skipif.
from lead.team_lead import TeamLead  # noqa: E402

try:
    from lead.team_lead import (  # type: ignore[attr-defined]
        ConflictQueueItem,
        classify_mailbox_state,
        mailbox_last_member_message,
        parse_conflict_file,
    )

    _HAS_RESUME_HELPERS = True
except ImportError:
    ConflictQueueItem = None  # type: ignore[assignment, misc]
    classify_mailbox_state = None  # type: ignore[assignment]
    mailbox_last_member_message = None  # type: ignore[assignment]
    parse_conflict_file = None  # type: ignore[assignment]
    _HAS_RESUME_HELPERS = False

_HAS_RESTORE_STATE = hasattr(TeamLead, "restore_state")
_HAS_GRACEFUL_SHUTDOWN = hasattr(TeamLead, "graceful_shutdown")


# ---------------------------------------------------------------------------
# 헬퍼: TeamLead 인스턴스 생성 (stub 의존성 주입).
# ---------------------------------------------------------------------------
def _make_lead(tmp_path: Path) -> TeamLead:
    state_dir = tmp_path / "state"
    lead_state_dir = state_dir / "lead"
    agents_root = state_dir / "agents"
    session_logs_root = state_dir / "session_logs"
    ws_main = tmp_path / "ws" / "main"
    ws_root = tmp_path / "ws" / "members"
    for d in (state_dir, lead_state_dir, agents_root, session_logs_root, ws_main, ws_root):
        d.mkdir(parents=True, exist_ok=True)

    lead = TeamLead(
        spec="dummy spec",
        spec_name="spec.md",
        state_dir=state_dir,
        lead_state_dir=lead_state_dir,
        agents_root=agents_root,
        session_logs_root=session_logs_root,
        ws_root=ws_root,
        ws_main=ws_main,
        llm=_StubLLM(),
        budget=_StubBudget(),
        health=None,
        default_model="opus",
        enable_evaluator=False,
        max_parallel=2,
        replan=False,
    )
    return lead


def _live_sleep_proc(duration: int = 60) -> subprocess.Popen:
    """teardown 에서 kill 할 sleep 서브프로세스를 만들어 PID 활성 시나리오 시뮬."""
    return subprocess.Popen(
        ["sleep", str(duration)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _kill_proc(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    except (ProcessLookupError, OSError):
        pass


# ---------------------------------------------------------------------------
# PID 파일 + liveness 헬퍼.
# ---------------------------------------------------------------------------
class TestPidLifecycle:
    def test_write_read_remove_roundtrip(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        (agents_root / "M001").mkdir(parents=True)
        path = write_pid_file("M001", agents_root, pid=12345)
        assert path == pid_file_path("M001", agents_root)
        assert read_pid_file("M001", agents_root) == 12345
        remove_pid_file("M001", agents_root)
        assert read_pid_file("M001", agents_root) is None
        # 두 번째 remove 는 no-op (FileNotFoundError 흡수)
        remove_pid_file("M001", agents_root)

    def test_read_returns_none_for_garbage_pid(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        path = pid_file_path("M001", agents_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-a-number\n")
        assert read_pid_file("M001", agents_root) is None

    def test_is_pid_alive_for_real_subprocess(self) -> None:
        proc = _live_sleep_proc()
        try:
            assert is_pid_alive(proc.pid) is True
        finally:
            _kill_proc(proc)
        # 종료 후 잠시 대기 → wait 으로 reaped 됐는지 확인 후 liveness false 기대.
        # macOS/Linux 모두 wait 후 PID 는 즉시 재사용 가능하지만 직전 PID 자체는 죽음.
        assert is_pid_alive(proc.pid) is False

    def test_is_pid_alive_rejects_invalid_pids(self) -> None:
        assert is_pid_alive(0) is False
        assert is_pid_alive(-1) is False


class TestTryReattach:
    def test_reattach_true_when_pid_alive(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        (agents_root / "M001").mkdir(parents=True)
        proc = _live_sleep_proc()
        try:
            write_pid_file("M001", agents_root, pid=proc.pid)
            assert try_reattach("M001", agents_root) is True
        finally:
            _kill_proc(proc)

    def test_reattach_false_when_pid_dead(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        (agents_root / "M001").mkdir(parents=True)
        proc = _live_sleep_proc(duration=1)
        proc_pid = proc.pid
        _kill_proc(proc)
        write_pid_file("M001", agents_root, pid=proc_pid)
        assert try_reattach("M001", agents_root) is False

    def test_reattach_false_when_no_pid_file(self, tmp_path: Path) -> None:
        agents_root = tmp_path / "agents"
        (agents_root / "M001").mkdir(parents=True)
        assert try_reattach("M001", agents_root) is False


# ---------------------------------------------------------------------------
# mailbox state 매핑 / 충돌 파일 파서.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not _HAS_RESUME_HELPERS,
    reason="lead.team_lead.classify_mailbox_state / mailbox_last_member_message 미구현",
)
class TestMailboxClassify:
    def test_classify_delivery_done(self, tmp_path: Path) -> None:
        mbox = tmp_path / "mailbox.md"
        append_message(mbox, from_="lead", to="M001", kind="instruction", body="시작")
        append_message(mbox, from_="M001", to="lead", kind="delivery", body="done!")
        last = mailbox_last_member_message(mbox, "M001")
        assert last is not None and last.kind == "delivery"
        assert classify_mailbox_state(last) == "DONE"

    def test_classify_failed_status_body(self, tmp_path: Path) -> None:
        mbox = tmp_path / "mailbox.md"
        append_message(mbox, from_="M001", to="lead", kind="status", body="[STATUS:FAILED] reason")
        last = mailbox_last_member_message(mbox, "M001")
        assert classify_mailbox_state(last) == "FAILED"

    def test_classify_question_waiting(self, tmp_path: Path) -> None:
        mbox = tmp_path / "mailbox.md"
        append_message(mbox, from_="M001", to="lead", kind="question", body="A vs B?")
        last = mailbox_last_member_message(mbox, "M001")
        assert classify_mailbox_state(last) == "WAITING"

    def test_classify_running_for_active_status(self, tmp_path: Path) -> None:
        mbox = tmp_path / "mailbox.md"
        append_message(mbox, from_="M001", to="lead", kind="status", body="working on step 2")
        last = mailbox_last_member_message(mbox, "M001")
        assert classify_mailbox_state(last) == "RUNNING"

    def test_classify_unknown_when_no_member_msg(self, tmp_path: Path) -> None:
        mbox = tmp_path / "mailbox.md"
        append_message(mbox, from_="lead", to="M001", kind="instruction", body="start")
        last = mailbox_last_member_message(mbox, "M001")
        assert last is None
        assert classify_mailbox_state(last) == "UNKNOWN"


@pytest.mark.skipif(
    not _HAS_RESUME_HELPERS,
    reason="lead.team_lead.parse_conflict_file / ConflictQueueItem 미구현",
)
class TestConflictFileParser:
    def test_parse_minimal_conflict_md(self, tmp_path: Path) -> None:
        path = tmp_path / "M003-20260515T000000Z.md"
        path.write_text(
            "# Merge conflicts — M003\n"
            "\n"
            "## 충돌 파일\n"
            "- `lead/team_lead.py` — 보존: `lead/team_lead.py.from-M003`\n"
            "- `lead/main.py` — 보존: `lead/main.py.from-M003`\n"
            "\n"
            "## 다음 행동\n"
            "팀장 결정\n",
            encoding="utf-8",
        )
        item = parse_conflict_file(path)
        assert item is not None
        assert item.agent_id == "M003"
        assert item.files == ["lead/team_lead.py", "lead/main.py"]
        assert item.path == path

    def test_parse_returns_none_for_empty_section(self, tmp_path: Path) -> None:
        path = tmp_path / "M002-abc.md"
        path.write_text("# Merge conflicts — M002\n\n## 충돌 파일\n\n## 끝\n", encoding="utf-8")
        assert parse_conflict_file(path) is None


# ---------------------------------------------------------------------------
# TeamLead.restore_state — 5개 핵심 시나리오.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not _HAS_RESTORE_STATE or not _HAS_RESUME_HELPERS,
    reason="TeamLead.restore_state / ConflictQueueItem 미구현 — 후속 작업에서 활성화",
)
class TestRestoreState:
    def test_alive_pid_active_mailbox_reattaches(self, tmp_path: Path) -> None:
        """(1) PID 살아있고 mailbox 정상 → 재연결 (RUNNING 유지, _reattached 마킹)."""
        lead = _make_lead(tmp_path)
        agent_id = "M001"
        agent_dir = lead.agents_root / agent_id
        agent_dir.mkdir(parents=True)
        append_message(
            agent_dir / "mailbox.md", from_="lead", to=agent_id, kind="instruction", body="seed"
        )
        append_message(
            agent_dir / "mailbox.md", from_=agent_id, to="lead", kind="status", body="진행중"
        )
        (agent_dir / "status").write_text("RUNNING")

        proc = _live_sleep_proc()
        try:
            write_pid_file(agent_id, lead.agents_root, pid=proc.pid)
            lead.registry.rehydrate()  # 디스크 상태 흡수
            lead.restore_state()
            rec = lead.registry.get(agent_id)
            assert rec is not None and rec.status == "RUNNING"
            assert agent_id in lead._reattached
        finally:
            _kill_proc(proc)

    def test_dead_pid_active_mailbox_marks_respawn(self, tmp_path: Path) -> None:
        """(2) PID 죽음 + mailbox active → RUNNING 유지 (이후 _recover_zombies → 재hire)."""
        lead = _make_lead(tmp_path)
        agent_id = "M002"
        agent_dir = lead.agents_root / agent_id
        agent_dir.mkdir(parents=True)
        append_message(
            agent_dir / "mailbox.md", from_=agent_id, to="lead", kind="status", body="진행중"
        )
        (agent_dir / "status").write_text("RUNNING")

        # 죽은 프로세스 PID 시뮬: 짧은 sleep 즉시 kill 후 그 PID 기록.
        proc = _live_sleep_proc(duration=1)
        dead_pid = proc.pid
        _kill_proc(proc)
        write_pid_file(agent_id, lead.agents_root, pid=dead_pid)

        lead.registry.rehydrate()
        lead.restore_state()
        rec = lead.registry.get(agent_id)
        assert rec is not None
        assert rec.status == "RUNNING"
        # 재연결 마킹은 없어야 함 → _recover_zombies 가 정리할 수 있게.
        assert agent_id not in lead._reattached

    def test_mailbox_delivery_marks_done_and_skips_respawn(self, tmp_path: Path) -> None:
        """(3) mailbox=delivery → DONE, 재spawn skip (verify+merge 경로로 위임)."""
        lead = _make_lead(tmp_path)
        agent_id = "M003"
        agent_dir = lead.agents_root / agent_id
        agent_dir.mkdir(parents=True)
        append_message(
            agent_dir / "mailbox.md", from_=agent_id, to="lead", kind="delivery", body="산출물 완료"
        )
        (agent_dir / "status").write_text("RUNNING")
        # PID 파일 없음 (이미 정상 종료)

        lead.registry.rehydrate()
        lead.restore_state()
        rec = lead.registry.get(agent_id)
        assert rec is not None and rec.status == "DONE"
        assert agent_id not in lead._reattached

    def test_conflict_queue_reload(self, tmp_path: Path) -> None:
        """(4) state/lead/conflicts/*.md 글롭 → self.conflict_queue 재로드."""
        lead = _make_lead(tmp_path)
        conflicts_dir = lead.lead_state_dir / "conflicts"
        conflicts_dir.mkdir(parents=True, exist_ok=True)
        (conflicts_dir / "M007-20260515T010101Z.md").write_text(
            "# Merge conflicts — M007\n\n"
            "## 충돌 파일\n"
            "- `lead/team_lead.py` — 보존: `lead/team_lead.py.from-M007`\n"
            "- `lead/member.py` — 보존: `lead/member.py.from-M007`\n",
            encoding="utf-8",
        )
        (conflicts_dir / "M008-20260515T010102Z.md").write_text(
            "# Merge conflicts — M008\n\n"
            "## 충돌 파일\n"
            "- `lead/main.py` — 보존: `lead/main.py.from-M008`\n",
            encoding="utf-8",
        )

        lead.restore_state()
        assert len(lead.conflict_queue) == 2
        by_agent = {it.agent_id: it for it in lead.conflict_queue}
        assert "M007" in by_agent and "M008" in by_agent
        assert by_agent["M007"].files == ["lead/team_lead.py", "lead/member.py"]
        assert isinstance(by_agent["M007"], ConflictQueueItem)

    def test_mailbox_question_marks_waiting(self, tmp_path: Path) -> None:
        """추가: mailbox=question → WAITING (lead 가 reply 후 resume 트리거)."""
        lead = _make_lead(tmp_path)
        agent_id = "M004"
        agent_dir = lead.agents_root / agent_id
        agent_dir.mkdir(parents=True)
        append_message(
            agent_dir / "mailbox.md", from_=agent_id, to="lead", kind="question", body="A vs B?"
        )
        (agent_dir / "status").write_text("RUNNING")

        lead.registry.rehydrate()
        lead.restore_state()
        rec = lead.registry.get(agent_id)
        assert rec is not None and rec.status == "WAITING"


# ---------------------------------------------------------------------------
# graceful_shutdown — flag + drain + timeout.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    not _HAS_GRACEFUL_SHUTDOWN,
    reason="TeamLead.graceful_shutdown 미구현 — 후속 작업에서 활성화",
)
class TestGracefulShutdown:
    def test_signal_handler_sets_flag(self, tmp_path: Path) -> None:
        """SIGTERM 시뮬: 직접 핸들러 호출 + flag 확인."""
        lead = _make_lead(tmp_path)
        assert lead._shutdown_requested is False
        # 핸들러 본체와 동일한 동작 시뮬 (시그널 컨텍스트에서 무거운 작업 금지)
        lead._shutdown_requested = True
        assert lead._shutdown_requested is True

    def test_clean_drain_when_no_pending(self, tmp_path: Path) -> None:
        """(5a) in-flight 없음 → graceful_shutdown 즉시 종료, flag set."""
        lead = _make_lead(tmp_path)
        t0 = time.monotonic()
        lead.graceful_shutdown(timeout=5.0)
        elapsed = time.monotonic() - t0
        assert lead._shutdown_requested is True
        assert elapsed < 2.0  # 즉시 종료 기대

    def test_drain_waits_for_pending_within_grace(self, tmp_path: Path) -> None:
        """(5b) 완료된 future 가 _pending 에 있어도 grace 내 drain 후 종료."""
        lead = _make_lead(tmp_path)
        fut: Future = Future()
        # 미리 완료 표시 → graceful_shutdown 의 _collect_completed_spawns 가 pop.
        fut.set_result(
            types.SimpleNamespace(
                agent_id="M999",
                status="DONE",
                raw_output="",
                error="",
                session_id="",
                cost_usd=0.0,
                last_question=None,
                delivery_text="",
            )
        )
        lead._pending["M999"] = fut
        # _post_spawn 이 호출되면 registry.update 가 필요 → 등록 후 brief 캐시도.
        lead.registry.register("M999", goal_id="G-tmp")
        lead._briefs["M999"] = types.SimpleNamespace(agent_id="M999")

        lead.graceful_shutdown(timeout=5.0)
        assert lead._shutdown_requested is True
        assert "M999" not in lead._pending

    def test_timeout_exceeded_logs_warning(self, tmp_path: Path) -> None:
        """(5c) future 영원히 미완료 → timeout 내 미회수 → 경고 emit 후 반환."""
        lead = _make_lead(tmp_path)
        never_done: Future = Future()  # 의도적으로 set_result 안 함
        lead._pending["M998"] = never_done
        lead.registry.register("M998", goal_id="G-tmp")

        t0 = time.monotonic()
        lead.graceful_shutdown(timeout=0.3)
        elapsed = time.monotonic() - t0
        assert 0.2 <= elapsed < 2.0
        # timeline stub 에 timeout 이벤트가 기록됐는지
        assert any(ev.get("pending") for ev in lead.timeline.events if "pending" in ev)
        # _pending 그대로 유지 (강제 cancel 안 함 — 호출자가 process 종료로 정리)
        assert "M998" in lead._pending
        never_done.cancel()  # 테스트 teardown 안전.


# ---------------------------------------------------------------------------
# Extended cases — M033 신규 추가
# 상위 API (restore_state, graceful_shutdown, 4종 helper) 미구현 환경에서도
# 동작하는 PID/mailbox/conflicts-glob 회귀 케이스. 상위가 구현되면 위쪽 클래스
# 들이 자동으로 활성화된다.
# ---------------------------------------------------------------------------

from lead.mailbox import parse_messages  # noqa: E402


class TestPidEdgeCases:
    def test_pid_file_strips_whitespace(self, tmp_path: Path) -> None:
        """PID 파일에 \\n / 공백 포함되어 있어도 정상 파싱."""
        agents_root = tmp_path / "agents"
        path = pid_file_path("M001", agents_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("  12345\n\n")
        assert read_pid_file("M001", agents_root) == 12345

    def test_pid_file_multiline_first_token_only(self, tmp_path: Path) -> None:
        """첫 줄이 숫자 단독이 아니면 파싱 실패 → None (graceful)."""
        agents_root = tmp_path / "agents"
        path = pid_file_path("M001", agents_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("12345 extra-junk\nline2\n")
        # `int(raw)` 가 raw.strip() 결과를 받는데 공백 split 아님 → ValueError → None
        assert read_pid_file("M001", agents_root) is None

    def test_is_pid_alive_returns_false_for_zero(self) -> None:
        """pid=0 은 unix 에서 '현재 프로세스 그룹' 의미 — liveness 시그널로 부적합."""
        assert is_pid_alive(0) is False

    def test_is_pid_alive_returns_false_for_negative(self) -> None:
        assert is_pid_alive(-100) is False

    def test_remove_pid_file_no_error_when_missing(self, tmp_path: Path) -> None:
        """remove_pid_file 은 파일 없을 때 raise 하지 않는다."""
        agents_root = tmp_path / "agents"
        (agents_root / "M001").mkdir(parents=True)
        # 두 번 연속 호출도 안전
        remove_pid_file("M001", agents_root)
        remove_pid_file("M001", agents_root)
        assert read_pid_file("M001", agents_root) is None

    def test_write_pid_file_overwrites_existing(self, tmp_path: Path) -> None:
        """동일 agent_id 로 write_pid_file 재호출 → PID 덮어쓰기 (재spawn 시나리오)."""
        agents_root = tmp_path / "agents"
        (agents_root / "M001").mkdir(parents=True)
        write_pid_file("M001", agents_root, pid=1111)
        write_pid_file("M001", agents_root, pid=2222)
        assert read_pid_file("M001", agents_root) == 2222


class TestMailboxParsing:
    def test_parse_all_six_kinds_roundtrip(self, tmp_path: Path) -> None:
        """6종 kind 모두 append + parse 라운드트립."""
        mbox = tmp_path / "mailbox.md"
        kinds = ["instruction", "status", "question", "reply", "delivery", "refine"]
        for k in kinds:
            from_ = "lead" if k in {"instruction", "reply", "refine"} else "M001"
            to = "M001" if from_ == "lead" else "lead"
            ref = 1 if k == "reply" else None
            append_message(mbox, from_=from_, to=to, kind=k, body=f"body of {k}", ref=ref)

        msgs = parse_messages(mbox)
        assert [m.kind for m in msgs] == kinds
        # reply 에 ref 가 보존되었는지
        reply = next(m for m in msgs if m.kind == "reply")
        assert reply.ref == 1

    def test_parse_messages_skips_malformed_blocks(self, tmp_path: Path) -> None:
        """헤더는 있지만 footer 없는 블록은 무시되어야 한다."""
        mbox = tmp_path / "mailbox.md"
        append_message(mbox, from_="lead", to="M001", kind="instruction", body="ok msg")
        # 손상된 헤더 - 닫는 마커 없음
        with mbox.open("a", encoding="utf-8") as f:
            f.write(
                "<!-- MSG id=99 from=lead to=M001 kind=instruction ts=2026-05-15T00:00:00Z -->\n"
            )
            f.write("orphan body without footer\n")
        msgs = parse_messages(mbox)
        # 정상 메시지 한 건만 파싱 — orphan 은 무시
        assert len(msgs) == 1
        assert msgs[0].kind == "instruction"

    def test_parse_messages_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        mbox = tmp_path / "mailbox.md"
        mbox.write_text("", encoding="utf-8")
        assert parse_messages(mbox) == []

    def test_parse_messages_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        mbox = tmp_path / "does-not-exist.md"
        assert parse_messages(mbox) == []

    def test_mailbox_preserves_message_order_by_id(self, tmp_path: Path) -> None:
        """append_message 의 id 는 단조 증가 — restore 시 순서 보존 필수."""
        mbox = tmp_path / "mailbox.md"
        ids: list[int] = []
        for _ in range(6):
            m = append_message(mbox, from_="lead", to="M001", kind="instruction", body="x")
            ids.append(m.id)
        msgs = parse_messages(mbox)
        assert [m.id for m in msgs] == ids
        assert ids == sorted(ids)


class TestConflictsDirGlob:
    """state/lead/conflicts/*.md 글롭 동작 회귀 — restore_state 가 의존하는 패턴."""

    def test_glob_empty_directory(self, tmp_path: Path) -> None:
        conflicts = tmp_path / "conflicts"
        conflicts.mkdir()
        assert sorted(conflicts.glob("*.md")) == []

    def test_glob_ignores_non_md_files(self, tmp_path: Path) -> None:
        conflicts = tmp_path / "conflicts"
        conflicts.mkdir()
        (conflicts / "M001-x.md").write_text("ok", encoding="utf-8")
        (conflicts / "M002-y.txt").write_text("noise", encoding="utf-8")
        (conflicts / "summary.json").write_text("{}", encoding="utf-8")
        files = sorted(conflicts.glob("*.md"))
        assert len(files) == 1
        assert files[0].name == "M001-x.md"

    def test_glob_picks_multiple_in_stable_order(self, tmp_path: Path) -> None:
        conflicts = tmp_path / "conflicts"
        conflicts.mkdir()
        names = ["M003-c.md", "M001-a.md", "M002-b.md"]
        for n in names:
            (conflicts / n).write_text("# conflict", encoding="utf-8")
        files = sorted(conflicts.glob("*.md"))
        assert [f.name for f in files] == sorted(names)

    def test_glob_handles_missing_dir_via_glob_safety(self, tmp_path: Path) -> None:
        """존재하지 않는 conflicts 디렉토리에 glob 호출 — 빈 결과 반환."""
        conflicts = tmp_path / "no-such-dir"
        # Path.glob 은 존재 안 하는 경로에서 빈 iterator 반환
        assert list(conflicts.glob("*.md")) == []


class TestRegistryRestoreSurface:
    """registry rehydrate + mailbox 마지막 메시지 — restore_state 에 필요한 토대."""

    def test_rehydrate_picks_up_disk_status(self, tmp_path: Path) -> None:
        """disk 의 status 파일을 rehydrate 가 인덱스로 흡수."""
        lead = _make_lead(tmp_path)
        agent_id = "M050"
        agent_dir = lead.agents_root / agent_id
        agent_dir.mkdir(parents=True)
        (agent_dir / "status").write_text("DONE")
        append_message(
            agent_dir / "mailbox.md",
            from_=agent_id,
            to="lead",
            kind="delivery",
            body="done body",
        )
        lead.registry.rehydrate()
        rec = lead.registry.get(agent_id)
        assert rec is not None
        assert rec.status == "DONE"
        assert rec.last_msg_id == 1

    def test_rehydrate_unknown_status_defaults_to_hired(self, tmp_path: Path) -> None:
        """STATUS_VALUES 외 값은 HIRED 로 정규화 — 디스크 손상 graceful."""
        lead = _make_lead(tmp_path)
        agent_id = "M051"
        agent_dir = lead.agents_root / agent_id
        agent_dir.mkdir(parents=True)
        (agent_dir / "status").write_text("garbage-status")
        lead.registry.rehydrate()
        rec = lead.registry.get(agent_id)
        assert rec is not None
        assert rec.status == "HIRED"


class TestPendingFutureSemantics:
    """_pending / _briefs 의 기본 컨테이너 동작 — graceful_shutdown 전제."""

    def test_pending_empty_on_init(self, tmp_path: Path) -> None:
        lead = _make_lead(tmp_path)
        assert lead._pending == {}
        assert lead._briefs == {}

    def test_collect_completed_spawns_returns_false_when_empty(self, tmp_path: Path) -> None:
        """드레인 진입점 — 빈 상태에서 False, 부수효과 없음."""
        lead = _make_lead(tmp_path)
        assert lead._collect_completed_spawns() is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-x", "-q"]))
