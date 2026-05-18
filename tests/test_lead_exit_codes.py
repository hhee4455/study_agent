"""G-002 — ExitCode 매핑 + stderr 힌트 단위 테스트.

검증 범위:
  1. ``core.exit_codes.hint_for`` 가 모든 ExitCode 에 대해 1~2줄 힌트를 돌려준다.
  2. ``print_hint`` 가 ``[hint] …`` 한 줄을 stream 에 쓰며 ``OK`` 는 no-op.
  3. ``format_failure_note`` 가 detail + hint 를 한 줄로 합성.
  4. ``lead.main.main()`` 이 TeamLead.run 에서 던지는 예외별로 ExitCode 를 반환하고
     stderr 에 hint 한 줄을 동봉.
  5. ``TeamLead.run()`` 이 RateLimitExhausted 발생 시 ``ExitCode.RATE_LIMIT_EXHAUSTED``
     (=10) 을 반환 — 과거 단순 ``4`` 반환에서 분리됐는지.

테스트는 모두 in-process. 실제 subprocess / claude CLI / 멤버 spawn 없음.
"""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# tests/ 의 부모(agent_system/)를 PYTHONPATH 에 — conftest 가 먼저 stub 을 심음.
_AGENT_SYSTEM = Path(__file__).resolve().parent.parent
if str(_AGENT_SYSTEM) not in sys.path:
    sys.path.insert(0, str(_AGENT_SYSTEM))


# ---------------------------------------------------------------------------
# 시드 ws 누락 모듈 stub (test_lead_resume.py 와 동일 패턴).
#
# conftest.py 의 _install_stubs 가 다수의 의존성을 채우지만 core.auto_merge,
# core.similarity, core.session_manager, core.deliverable, core.cli_caller 는
# 빠져있다 — 머지된 verifier 환경에서는 실 모듈이 있으므로 ``_stub_if_missing``
# 은 no-op (verifier env 무해). 격리 시드 ws 에서만 stub 으로 채운다.
# ---------------------------------------------------------------------------


def _stub_if_missing(name: str, **attrs: Any) -> types.ModuleType:
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    try:
        import importlib

        return importlib.import_module(name)
    except ImportError:
        pass
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class _Stub:
    def __init__(self, *_a: Any, **_kw: Any) -> None:  # noqa: D401
        pass


_stub_if_missing(
    "core.auto_merge",
    try_auto_merge=lambda *_a, **_kw: types.SimpleNamespace(merged=None, strategy=""),
    MergeResult=_Stub,
)
_stub_if_missing(
    "core.similarity",
    GATE_ACTION_BYPASS="bypass",
    GATE_ACTION_PASS="pass",
    GATE_ACTION_REFINE="refine",
    GATE_ACTION_SKIP="skip",
    decide_gate=lambda *_a, **_kw: ("pass", 0.0),
)


class _StubSession:
    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    def run(self, *_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("stub SessionManager should not be invoked")


_stub_if_missing(
    "core.session_manager",
    SessionConfig=_Stub,
    SessionManager=_StubSession,
    SessionResult=_Stub,
)
_stub_if_missing("core.deliverable", missing_or_outside=lambda paths, cwd: [])
_stub_if_missing(
    "core.cli_caller",
    make_codex_raw_factory=lambda **_kw: None,
    make_raw_llm_factory=lambda **_kw: None,
)

# lead.member 의 PID 유틸. conftest 의 기본 stub 은 HireBrief/MemberSpawner/SpawnResult
# 만 갖고 있어 team_lead.py 의 ``from lead.member import is_pid_alive, read_pid_file``
# 가 깨진다. 누락된 attribute 만 보강 (실 모듈이 있을 땐 setattr 가 덮어쓰지 않도록 hasattr 가드).
_member_mod = _stub_if_missing(
    "lead.member",
    HireBrief=_Stub,
    MemberSpawner=_Stub,
    SpawnResult=_Stub,
)
for _name, _val in (
    ("is_pid_alive", lambda _pid: False),
    ("read_pid_file", lambda _aid, _root: None),
    ("write_pid_file", lambda _aid, _root, pid=0: None),
    ("remove_pid_file", lambda _aid, _root: None),
    ("pid_file_path", lambda _aid, _root: Path()),
    ("try_reattach", lambda _aid, _root: False),
):
    if not hasattr(_member_mod, _name):
        setattr(_member_mod, _name, _val)

# build_refine_message 가 lead.mailbox 에 없는 경우 fallback. mailbox 자체는 실 모듈
# (seed 에 포함) 이라 stub 으로 덮지 않는다.
import lead.mailbox as _mbox  # noqa: E402

if not hasattr(_mbox, "build_refine_message"):
    setattr(_mbox, "build_refine_message", lambda *_a, **_kw: "")


from core.exit_codes import (  # noqa: E402
    ExitCode,
    format_failure_note,
    hint_for,
    print_hint,
)

# ---------------------------------------------------------------------------
# 1. hint_for — 모든 ExitCode 가 비어있지 않은 힌트를 갖는다 (OK 제외).
# ---------------------------------------------------------------------------


class TestHintFor:
    def test_ok_returns_empty_string(self) -> None:
        assert hint_for(ExitCode.OK) == ""

    @pytest.mark.parametrize(
        "code",
        [c for c in ExitCode if c is not ExitCode.OK],
    )
    def test_every_nonzero_code_has_hint(self, code: ExitCode) -> None:
        h = hint_for(code)
        assert isinstance(h, str)
        assert h.strip(), f"ExitCode.{code.name} 힌트가 비어있음"
        # 1~2 줄 권장 — 너무 길지 않도록 sanity 가드.
        assert h.count("\n") <= 1, f"ExitCode.{code.name} 힌트가 2줄 초과"

    def test_unknown_code_falls_back_to_general(self) -> None:
        # ExitCode 에 없는 코드도 graceful fallback (GENERAL_FAILURE 힌트).
        general = hint_for(ExitCode.GENERAL_FAILURE)
        assert hint_for(999) == general

    def test_rate_limit_hint_mentions_claude_login(self) -> None:
        """브리프 명시 권장 조치: rate limit → `claude login` 권유."""
        assert "claude login" in hint_for(ExitCode.RATE_LIMIT_EXHAUSTED)

    def test_budget_hint_mentions_budget_json(self) -> None:
        assert "budget.json" in hint_for(ExitCode.BUDGET_EXCEEDED)

    def test_auth_hint_mentions_claude_login(self) -> None:
        assert "claude login" in hint_for(ExitCode.AUTH_FAILURE)

    def test_conflict_hint_mentions_conflicts_dir(self) -> None:
        assert "state/conflicts" in hint_for(ExitCode.CONFLICT_UNRESOLVED)


# ---------------------------------------------------------------------------
# 2. print_hint — stream 에 한 줄 write, OK 는 no-op.
# ---------------------------------------------------------------------------


class TestPrintHint:
    def test_ok_writes_nothing(self) -> None:
        buf = io.StringIO()
        print_hint(ExitCode.OK, stream=buf)
        assert buf.getvalue() == ""

    def test_nonzero_writes_one_hint_line(self) -> None:
        buf = io.StringIO()
        print_hint(ExitCode.RATE_LIMIT_EXHAUSTED, stream=buf)
        out = buf.getvalue()
        assert out.startswith("[hint] ")
        assert out.count("\n") == 1
        assert "claude login" in out

    def test_extra_prefix_included(self) -> None:
        buf = io.StringIO()
        print_hint(ExitCode.BUDGET_EXCEEDED, stream=buf, extra="$50 초과")
        out = buf.getvalue()
        assert "[hint] $50 초과 —" in out
        assert "budget.json" in out

    def test_integer_code_accepted(self) -> None:
        """주의: int 만 받아도 동일 동작 — sys.exit(rc) 호환."""
        buf = io.StringIO()
        print_hint(int(ExitCode.NO_PROGRESS), stream=buf)
        assert "[hint]" in buf.getvalue()

    def test_default_stream_is_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_hint(ExitCode.SPEC_NOT_FOUND)
        captured = capsys.readouterr()
        assert "[hint]" in captured.err
        assert captured.out == ""


# ---------------------------------------------------------------------------
# 3. format_failure_note — timeline 이벤트용 합성.
# ---------------------------------------------------------------------------


class TestFormatFailureNote:
    def test_detail_and_hint_joined(self) -> None:
        note = format_failure_note(
            ExitCode.RATE_LIMIT_EXHAUSTED, "burst limit retries 초과"
        )
        assert "burst limit retries 초과" in note
        assert "hint:" in note
        assert "claude login" in note

    def test_detail_only_when_hint_missing(self) -> None:
        # OK 는 힌트가 빈 문자열 → detail 만 반환.
        assert format_failure_note(ExitCode.OK, "everything fine") == "everything fine"

    def test_hint_only_when_detail_empty(self) -> None:
        note = format_failure_note(ExitCode.BUDGET_EXCEEDED)
        assert "budget.json" in note
        assert "hint:" not in note  # detail 없으면 plain hint


# ---------------------------------------------------------------------------
# 4. lead.main.main() — 예외별 ExitCode + stderr 힌트 매핑.
# ---------------------------------------------------------------------------


# core.budget / core.rate_limit 의 예외 클래스 — conftest stub 또는 실 모듈.
# lead.main 도 동일 모듈을 import 하므로 isinstance 매칭 보장.
from core.budget import BudgetExceeded  # noqa: E402
from core.rate_limit import RateLimitExhausted  # noqa: E402

# core.health.HealthExhausted 와 core.rate_limit.ServerError 는 conftest 의 기본
# stub 에 없을 수 있다. 시드 ws 에서 raise 가능하도록 동적으로 보강.
_health_mod = sys.modules.setdefault("core.health", types.ModuleType("core.health"))
if not hasattr(_health_mod, "HealthExhausted"):

    class _HealthExhausted(Exception):
        pass

    setattr(_health_mod, "HealthExhausted", _HealthExhausted)
if not hasattr(_health_mod, "HealthMonitor"):
    setattr(_health_mod, "HealthMonitor", _Stub)

_rl_mod = sys.modules["core.rate_limit"]
if not hasattr(_rl_mod, "ServerError"):

    class _ServerError(Exception):
        pass

    setattr(_rl_mod, "ServerError", _ServerError)

from core.health import HealthExhausted  # noqa: E402
from core.rate_limit import ServerError  # noqa: E402
from core.exit_codes import ExitCode as _EC  # noqa: E402

# lead.main / lead.team_lead 는 추가 의존성을 끌어들인다 (lead.member 등). 시드
# ws 에는 일부 모듈이 빠져있어 import 자체가 실패할 수 있으므로 graceful skip.
# 머지된 verifier 환경에서는 실 모듈이 있어 모든 테스트가 실행된다.
try:
    import lead.main as lead_main  # noqa: E402

    _HAS_LEAD_MAIN = True
except Exception as _e:  # noqa: BLE001
    lead_main = None  # type: ignore[assignment]
    _LEAD_MAIN_IMPORT_ERROR = repr(_e)
    _HAS_LEAD_MAIN = False
else:
    _LEAD_MAIN_IMPORT_ERROR = ""

try:
    from lead.team_lead import TeamLead  # noqa: E402

    _HAS_TEAM_LEAD = True
except Exception as _e:  # noqa: BLE001
    TeamLead = None  # type: ignore[assignment, misc]
    _TEAM_LEAD_IMPORT_ERROR = repr(_e)
    _HAS_TEAM_LEAD = False
else:
    _TEAM_LEAD_IMPORT_ERROR = ""


class _NoOpLead:
    """TeamLead 대체 — main() 진입 후 run() 가 던지는 예외만 시뮬레이션."""

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        self._shutdown_requested = False
        self._run_impl: Any = None  # 테스트가 set

    def restore_state(self) -> None:  # noqa: D401
        return None

    def run(self) -> int:
        if self._run_impl is None:
            return int(_EC.OK)
        result = self._run_impl()
        # callable 가 raise 하지 않고 정수 반환 → 그대로 전달.
        return int(result)

    def graceful_shutdown(self, *, timeout: float) -> None:  # noqa: D401
        return None


def _build_argv(tmp_path: Path) -> list[str]:
    spec = tmp_path / "spec.md"
    spec.write_text("dummy spec")
    state = tmp_path / "state"
    ws = tmp_path / "ws" / "main"
    ws.mkdir(parents=True)
    return [
        "lead.main",
        "--spec",
        str(spec),
        "--workspace",
        str(ws),
        "--checkpoint",
        str(state),
        "--skip-preflight",
    ]


@pytest.fixture
def main_with_stub_lead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """lead.main.main() 호출 가능한 환경 셋업.

    - signal handler 등록 / preflight / 무거운 의존성 stub.
    - TeamLead 를 _NoOpLead 로 교체. 테스트가 lead._run_impl 에 callable 을 주입.
    - argv 패치.

    yield: (call_main, lead_ref) — call_main() 은 rc 반환, lead_ref[0] 은 마지막 인스턴스.
    """
    captured_lead: list[_NoOpLead] = []

    class _LeadFactory(_NoOpLead):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            captured_lead.append(self)

    class _StubBudgetMgr:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def record(self, *_a: Any, **_kw: Any) -> None:  # main 이 budget.record 를 LLMClient 에 넘김
            return None

        def can_continue(self) -> bool:
            return True

    monkeypatch.setattr(lead_main, "_install_signal_handlers", lambda lead: None)
    monkeypatch.setattr(lead_main, "_preflight", lambda skip: None)
    monkeypatch.setattr(lead_main, "TeamLead", _LeadFactory)
    monkeypatch.setattr(lead_main, "BudgetManager", _StubBudgetMgr)
    monkeypatch.setattr(lead_main, "LLMClient", lambda *a, **kw: object())
    monkeypatch.setattr(lead_main, "HealthMonitor", lambda *a, **kw: object())
    monkeypatch.setattr(lead_main, "make_codex_raw_factory", lambda **kw: None)
    monkeypatch.setattr(lead_main, "make_raw_llm_factory", lambda **kw: None)
    monkeypatch.setattr(sys, "argv", _build_argv(tmp_path))

    def _call(run_impl: Any = None) -> int:
        # _LeadFactory 가 main() 안에서 인스턴스화될 때 captured_lead 에 append.
        # 그 인스턴스의 _run_impl 을 미리 셋업할 수 없으니 monkey-patch on class.
        _NoOpLead._test_run_impl = staticmethod(run_impl) if run_impl else None  # type: ignore[attr-defined]
        original = _NoOpLead.run

        def _run(self: _NoOpLead) -> int:
            impl = getattr(_NoOpLead, "_test_run_impl", None)
            if impl is None:
                return int(_EC.OK)
            return int(impl())

        monkeypatch.setattr(_NoOpLead, "run", _run)
        try:
            return lead_main.main()
        finally:
            monkeypatch.setattr(_NoOpLead, "run", original)

    return _call, captured_lead


@pytest.mark.skipif(
    not _HAS_LEAD_MAIN,
    reason=f"lead.main import 실패 (시드 ws 의존성 누락): {_LEAD_MAIN_IMPORT_ERROR}",
)
class TestMainExitMapping:
    def test_clean_success_returns_ok(
        self, main_with_stub_lead: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        call, _ = main_with_stub_lead
        rc = call(lambda: _EC.OK)
        assert rc == int(_EC.OK)
        # 성공 시 hint 출력 없음.
        assert "[hint]" not in capsys.readouterr().err

    def test_budget_exceeded_maps_to_budget_exit(
        self, main_with_stub_lead: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _raise() -> int:
            raise BudgetExceeded("비용 한도 초과: $50.00")

        call, _ = main_with_stub_lead
        rc = call(_raise)
        assert rc == int(_EC.BUDGET_EXCEEDED)
        err = capsys.readouterr().err
        assert "[hint]" in err
        assert "budget.json" in err

    def test_rate_limit_maps_to_dedicated_code(
        self, main_with_stub_lead: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """rate limit 은 더 이상 BUDGET(4) 와 같은 코드를 쓰지 않는다."""

        def _raise() -> int:
            raise RateLimitExhausted("usage limit retries 초과")

        call, _ = main_with_stub_lead
        rc = call(_raise)
        assert rc == int(_EC.RATE_LIMIT_EXHAUSTED)
        assert rc != int(_EC.BUDGET_EXCEEDED), (
            "RATE_LIMIT_EXHAUSTED 는 BUDGET_EXCEEDED 와 다른 값이어야 한다"
        )
        err = capsys.readouterr().err
        assert "[hint]" in err
        assert "claude login" in err

    def test_server_error_maps_to_server_exit(
        self, main_with_stub_lead: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _raise() -> int:
            raise ServerError("서버 과부하 retries 초과")

        call, _ = main_with_stub_lead
        rc = call(_raise)
        assert rc == int(_EC.SERVER_ERROR)
        assert "[hint]" in capsys.readouterr().err

    def test_health_exhausted_maps_to_health_exit(
        self, main_with_stub_lead: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _raise() -> int:
            raise HealthExhausted("디스크 95%")

        call, _ = main_with_stub_lead
        rc = call(_raise)
        assert rc == int(_EC.HEALTH_EXHAUSTED)
        assert "[hint]" in capsys.readouterr().err

    def test_keyboard_interrupt_maps_to_interrupt(
        self, main_with_stub_lead: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def _raise() -> int:
            raise KeyboardInterrupt()

        call, _ = main_with_stub_lead
        rc = call(_raise)
        assert rc == int(_EC.INTERRUPT)
        err = capsys.readouterr().err
        assert "[hint]" in err
        assert "restore_state" in err  # interrupt hint 가 재시작 안내 포함

    def test_returned_nonzero_emits_hint(
        self, main_with_stub_lead: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """run() 이 예외 대신 정수를 반환해도 main 이 hint 한 줄 첨부."""
        call, _ = main_with_stub_lead
        rc = call(lambda: int(_EC.NO_PROGRESS))
        assert rc == int(_EC.NO_PROGRESS)
        err = capsys.readouterr().err
        assert "[hint]" in err
        assert "plan.md" in err  # NO_PROGRESS hint 가 plan/status 안내 포함

    def test_spec_missing_returns_spec_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--spec 경로가 없으면 ExitCode.SPEC_NOT_FOUND(17) + hint."""
        monkeypatch.setattr(lead_main, "_preflight", lambda skip: None)
        ws = tmp_path / "ws" / "main"
        ws.mkdir(parents=True)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "lead.main",
                "--spec",
                str(tmp_path / "does-not-exist.md"),
                "--workspace",
                str(ws),
                "--checkpoint",
                str(tmp_path / "state"),
                "--skip-preflight",
            ],
        )
        rc = lead_main.main()
        assert rc == int(_EC.SPEC_NOT_FOUND)
        err = capsys.readouterr().err
        assert "[hint]" in err
        assert "--spec" in err


# ---------------------------------------------------------------------------
# 5. team_lead.run() — RateLimitExhausted 발생 시 신규 코드(10) 반환.
# ---------------------------------------------------------------------------


def _make_minimal_team_lead(tmp_path: Path):
    """test_lead_resume.py 의 _make_lead 와 동일한 의존성 stub 으로 TeamLead 생성.

    이 파일에서 다시 build — conftest 가 이미 core.* / lead.* stub 을 등록했지만
    TeamLead 인스턴스 생성에 추가로 필요한 _StubBudget / _StubLLM 은 here-local.
    """

    class _StubBudget:
        def can_continue(self) -> bool:
            return True

        def record(self, *_a: Any, **_kw: Any) -> None:
            pass

    class _StubLLM:
        def call(self, *_a: Any, **_kw: Any) -> str:
            return ""

    state_dir = tmp_path / "state"
    lead_state_dir = state_dir / "lead"
    agents_root = state_dir / "agents"
    session_logs_root = state_dir / "session_logs"
    ws_main = tmp_path / "ws" / "main"
    ws_root = tmp_path / "ws" / "members"
    for d in (state_dir, lead_state_dir, agents_root, session_logs_root, ws_main, ws_root):
        d.mkdir(parents=True, exist_ok=True)

    return TeamLead(
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
        max_parallel=1,
        replan=False,
    )


@pytest.mark.skipif(
    not _HAS_TEAM_LEAD,
    reason=f"lead.team_lead import 실패 (시드 ws 의존성 누락): {_TEAM_LEAD_IMPORT_ERROR}",
)
class TestTeamLeadRunExitCodes:
    def test_rate_limit_returns_dedicated_code(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """run() 내부 RateLimitExhausted → ExitCode.RATE_LIMIT_EXHAUSTED (10)."""
        lead = _make_minimal_team_lead(tmp_path)

        # plan.md 가 없으면 _initial_plan 이 LLM 을 호출하므로 사전 셋업.
        lead.plan_md.parent.mkdir(parents=True, exist_ok=True)
        lead.plan_md.write_text("# Plan\n- [ ] G-001-x: do x\n")

        # _tick 이 곧장 RateLimitExhausted 를 던지도록 패치.
        def _raise(*_a: Any, **_kw: Any) -> bool:
            raise RateLimitExhausted("burst limit retries 초과")

        monkeypatch.setattr(lead, "_tick", _raise)
        # _recover_zombies / _drain_pending 는 부수효과 없도록 no-op.
        monkeypatch.setattr(lead, "_recover_zombies", lambda: None)
        monkeypatch.setattr(lead, "_drain_pending", lambda: None)

        rc = lead.run()
        assert rc == int(_EC.RATE_LIMIT_EXHAUSTED), (
            f"기대 {int(_EC.RATE_LIMIT_EXHAUSTED)}, 실제 {rc}"
        )
        # 호환 가드: 더 이상 단순 4 가 아니어야 한다.
        assert rc != 4
        # team_lead 가 직접 stderr 에 hint 한 줄을 흘려야 한다.
        err = capsys.readouterr().err
        assert "[hint]" in err
        assert "claude login" in err
