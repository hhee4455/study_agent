"""공용 pytest fixture + 의존성 stub.

여러 테스트 파일이 동일하게 필요로 하는 셋업을 한 곳에 모아둔다:
  - sys.path 에 agent_system 등록 (tests/ 부모)
  - team_lead 가 import 하는 무거운 의존성 모듈 stub (격리 ws 에서는 모듈 자체가
    빠져있어 stub 없이는 team_lead 자체가 로드 안 됨)
  - 공용 fixture: tmp_ws, fake_llm, seed_writer, make_conflict, build_test_lead

verifier 환경(머지된 ws/main)에서는 실 모듈이 이미 sys.modules 에 있어 stub 이
덮이지 않으므로 동작에 영향 없음.
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

import pytest

# tests/의 부모(agent_system/)를 PYTHONPATH 에 노출 — 모든 import 보다 먼저.
_THIS_DIR = Path(__file__).resolve().parent
_PARENT = _THIS_DIR.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))


# =========================================================================
# 의존성 stub — module-level 에서 자동 적용
# =========================================================================


def _stub_module(name: str, **attrs: object) -> None:
    """실 모듈이 import 가능하면 그것을 sys.modules에 등록, 아니면 stub 으로 채움.

    이전 구현은 단순히 `name in sys.modules` 만 검사해서, conftest 가 가장 먼저
    로드되는 verifier 환경에서도 stub 이 먼저 자리잡고 진짜 모듈을 가렸다 (예:
    `lead.member` stub 에 없는 심볼을 후속 테스트가 import 하면 ImportError).
    실 import 를 우선 시도하면 stub 은 진짜 의존성이 비어있는 시드 환경에서만
    fallback 으로 동작한다.
    """
    if name in sys.modules:
        return
    try:
        import importlib

        importlib.import_module(name)
        return
    except Exception:
        pass
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


class _Stub:
    """단순 sentinel — isinstance/import-only 용도."""

    def __init__(self, *a: object, **kw: object) -> None:
        self._a, self._kw = a, kw


class _StubExc(Exception):
    pass


def _install_stubs() -> None:
    """team_lead 가 import 하는 무거운 의존성을 stub 으로 채움.

    이미 실 모듈이 sys.modules 에 있으면 그대로 둠 (verifier env).
    """
    _stub_module(
        "core.schemas",
        PLAN_BACKUP_KEEP=5,
        PlanSchema=_Stub,
        ValidationFailure=_StubExc,
        call_decomposer_with_validation=lambda *a, **kw: None,
        prune_plan_backups=lambda *a, **kw: None,
        validate_decomposer_output=lambda *a, **kw: None,
    )
    _stub_module(
        "core.budget",
        BudgetExceeded=_StubExc,
        BudgetManager=_Stub,
        BudgetLimits=_Stub,
    )
    _stub_module("core.health", HealthMonitor=_Stub)
    _stub_module(
        "core.llm",
        LLMClient=_Stub,
        parse_json_loose=lambda raw: {},
        MODEL_OPUS="opus",
        MODEL_SONNET="sonnet",
        MODEL_HAIKU="haiku",
        strip_agent_label=lambda s: s,
    )
    _stub_module("core.rate_limit", RateLimitExhausted=_StubExc)
    _stub_module(
        "core.verifier",
        Check=_Stub,
        Verifier=_Stub,
        shell_sanity_check=lambda cmd: (True, ""),
    )
    _stub_module("lead.member", HireBrief=_Stub, MemberSpawner=_Stub, SpawnResult=_Stub)
    _stub_module("lead.prompts", render_split=lambda *a, **kw: ("", ""))
    _stub_module("lead.registry", AgentRegistry=_Stub)
    _stub_module("lead.timeline", TimelineRenderer=_Stub)
    _stub_module("lead.workspace", WorkspaceMerger=_Stub)
    _stub_module("agents")
    _stub_module("agents.debate", DebatePanel=_Stub)
    _stub_module("agents.janitor", CodeJanitor=_Stub)
    _stub_module("agents.audit", AdversarialVerifier=_Stub)


_install_stubs()


# =========================================================================
# 공용 fixture
# =========================================================================


@pytest.fixture
def tmp_ws():
    """임시 디렉토리 트리 — ws/main + state/agents + state/lead 구조 미리 생성."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        ws_main = root / "ws" / "main"
        agents_root = root / "state" / "agents"
        lead_state = root / "state" / "lead"
        ws_main.mkdir(parents=True)
        agents_root.mkdir(parents=True)
        lead_state.mkdir(parents=True)
        yield types.SimpleNamespace(
            root=root,
            ws_main=ws_main,
            agents_root=agents_root,
            lead_state=lead_state,
        )


@pytest.fixture
def fake_llm():
    """순차 응답 큐 + call 횟수/인자 기록. tier/model 인자 모두 받음."""

    class _FakeLLM:
        def __init__(self) -> None:
            self.responses: list[str] = []
            self.calls: list[dict] = []

        def queue(self, *responses: str) -> _FakeLLM:
            self.responses.extend(responses)
            return self

        def call(self, system: str, user: str, **kw: object) -> str:
            self.calls.append(
                {
                    "system": system[:80],
                    "user": user[:80],
                    "tier": kw.get("tier"),
                    "model": kw.get("model"),
                    "call_kind": kw.get("call_kind"),
                }
            )
            if not self.responses:
                raise RuntimeError("FakeLLM 응답 큐 빔")
            return self.responses.pop(0)

        @property
        def call_count(self) -> int:
            return len(self.calls)

    return _FakeLLM()


@pytest.fixture
def seed_writer(tmp_ws):
    """ws_main 에 seed/stash 페어 작성 헬퍼.

    사용: ``seed_writer("M001", "lead/foo.py", seed="alpha", member="beta")``.
    """

    def _write(agent_id: str, rel: str, *, seed: str, member: str) -> tuple[Path, Path]:
        seed_p = tmp_ws.ws_main / rel
        seed_p.parent.mkdir(parents=True, exist_ok=True)
        seed_p.write_text(seed, encoding="utf-8")
        stash_p = seed_p.with_name(f"{seed_p.name}.from-{agent_id}")
        stash_p.write_text(member, encoding="utf-8")
        return seed_p, stash_p

    return _write
