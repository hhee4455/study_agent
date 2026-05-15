"""DebatePanel 단위 테스트 — model 파라미터, consensus_reached, integrated_content.

`_resolve_conflicts_via_debate` 의 escalate 흐름이 의존하는 패널 계약을 고정한다.
LLM 호출은 모두 fake — 외부 호출 없이 라우팅/감지 로직만 검증.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# conftest.py 가 agents/agents.debate 를 stub 으로 채워두면 ('agents.debate' is
# not a package) 진짜 패키지 import 가 차단된다. 이 테스트는 정식 패키지 경로의
# 회귀를 보장해야 하므로 관련 stub 만 비우고 실모듈을 로드한다. core.llm 등의
# stub 은 그대로 유지 — panel.py 가 그것에 의존하기 때문.
for _stub in ("agents.audit", "agents.janitor", "agents.debate", "agents"):
    sys.modules.pop(_stub, None)

from agents.debate.panel import (  # noqa: E402
    PERSONAS_FAST,
    DebateOutcome,
    DebatePanel,
)


class _RecordingLLM:
    """LLM 호출을 (model, system_prefix) 튜플로 기록. 응답은 prefix 매칭으로 분기."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def call(self, system: str, user: str, tier=None, model=None, **_kw) -> str:
        chosen = model or tier or "opus"
        self.calls.append((chosen, system[:30]))
        if "토론 참가자" in system:
            return "동의"
        if "팀장(lead)" in system:
            return "**결정**: A 채택"
        if "통합 작성자" in system:
            return "```\nINTEGRATED\n```"
        if "요약자" in system:
            return "- opt 1"
        if "정리 에이전트" in system:
            return "compacted"
        return "(fallback)"


def test_panel_default_model_param_is_sonnet_when_overridden():
    """model='sonnet' 지정 시 speak/decide/summarize 가 모두 sonnet 로 라우팅."""
    with tempfile.TemporaryDirectory() as d:
        llm = _RecordingLLM()
        panel = DebatePanel(
            Path(d),
            llm,
            max_rounds=1,
            personas=PERSONAS_FAST,
            model="sonnet",
        )
        outcome = panel.deliberate(
            question="q?",
            context="ctx",
            debate_id="t1",
            auto_decide=True,
        )
        assert isinstance(outcome, DebateOutcome)
        # 모든 호출이 sonnet 으로 — 단, summarize 만 별도 정책(haiku)
        non_summary_models = {m for m, sys_ in llm.calls if "요약자" not in sys_}
        assert non_summary_models == {"sonnet"}, (
            f"summarize 외 호출은 sonnet 만이어야 함, 실제: {non_summary_models}"
        )


def test_panel_model_override_at_call_time():
    """__init__ 에 model 없어도 deliberate(model='opus') 로 라운드 단위 override 가능."""
    with tempfile.TemporaryDirectory() as d:
        llm = _RecordingLLM()
        panel = DebatePanel(Path(d), llm, max_rounds=1, personas=PERSONAS_FAST)
        panel.deliberate(
            question="q",
            context="c",
            debate_id="t2",
            auto_decide=True,
            model="opus",
        )
        speak_models = {m for m, sys_ in llm.calls if "토론 참가자" in sys_}
        assert speak_models == {"opus"}, speak_models


def test_panel_consensus_reached_true_when_all_agree():
    """모든 페르소나가 '동의' → consensus_reached=True."""
    with tempfile.TemporaryDirectory() as d:
        llm = _RecordingLLM()  # speak → '동의'
        panel = DebatePanel(Path(d), llm, max_rounds=2, personas=PERSONAS_FAST)
        outcome = panel.deliberate(
            question="q",
            context="c",
            debate_id="t3",
            auto_decide=True,
        )
        assert outcome.consensus_reached is True


def test_panel_consensus_reached_false_when_disagreement():
    """페르소나가 길게 다른 의견 → consensus_reached=False (escalate 트리거)."""

    class _DissentLLM:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def call(self, system, user, tier=None, model=None, **_kw):
            chosen = model or tier or "opus"
            self.calls.append((chosen, system[:30]))
            if "토론 참가자" in system:
                return (
                    "이 결정에는 동의할 수 없다. 회귀 위험이 높고, 테스트 커버가 "
                    "부족하다. 다른 접근을 제안한다 — 점진 적용."
                )
            if "팀장(lead)" in system:
                return "**결정**: TBD"
            if "통합 작성자" in system:
                return "```\nx\n```"
            return "- opt"

    with tempfile.TemporaryDirectory() as d:
        panel = DebatePanel(
            Path(d),
            _DissentLLM(),
            max_rounds=2,
            personas=PERSONAS_FAST,
        )
        outcome = panel.deliberate(
            question="q",
            context="c",
            debate_id="t4",
            auto_decide=True,
        )
        assert outcome.consensus_reached is False, (
            "긴 반대 의견인데 consensus_reached=True 면 escalate 가 안 일어남"
        )


def test_panel_integrated_content_extracted_when_flag_set():
    """integrate_content=True 면 outcome.integrated_content 가 코드 펜스 내부 텍스트."""
    with tempfile.TemporaryDirectory() as d:
        llm = _RecordingLLM()
        panel = DebatePanel(Path(d), llm, max_rounds=1, personas=PERSONAS_FAST)
        outcome = panel.deliberate(
            question="q",
            context="c",
            debate_id="t5",
            auto_decide=True,
            integrate_content=True,
            model="sonnet",
        )
        assert outcome.integrated_content == "INTEGRATED"


def test_panel_integrated_content_none_when_flag_unset():
    """integrate_content 기본값(False) 이면 integrated_content=None — 기존 호출자 영향 없음."""
    with tempfile.TemporaryDirectory() as d:
        llm = _RecordingLLM()
        panel = DebatePanel(Path(d), llm, max_rounds=1, personas=PERSONAS_FAST)
        outcome = panel.deliberate(
            question="q",
            context="c",
            debate_id="t6",
            auto_decide=True,
        )
        assert outcome.integrated_content is None


def test_import_path_panel_module():
    """`from agents.debate.panel import DebatePanel` 가 안정적으로 동작한다."""
    from agents.debate.panel import DebatePanel as DP

    assert DP is DebatePanel
    assert DP.__name__ == "DebatePanel"


def test_import_path_package():
    """`from agents.debate import DebatePanel` (재노출) 도 동작하고 동일 객체다."""
    from agents.debate import DebatePanel as DP

    assert DP is DebatePanel
    assert DP.__name__ == "DebatePanel"


def test_package_all_exposes_core_symbols():
    """agents.debate.__all__ 에 핵심 공개 심볼이 모두 등재되어 있다."""
    import agents.debate as pkg

    for name in ("DebatePanel", "DebateOutcome", "PERSONAS", "PERSONAS_FAST"):
        assert name in pkg.__all__, f"{name} 가 __all__ 에 누락"
        assert hasattr(pkg, name), f"{name} 가 패키지 네임스페이스에 없음"


def test_panel_backward_compat_no_model_uses_persona_defaults():
    """model 안 주면 PERSONAS_FAST 의 기본 모델(sonnet, gpt-5.4-mini)이 그대로 쓰임."""
    with tempfile.TemporaryDirectory() as d:
        llm = _RecordingLLM()
        panel = DebatePanel(Path(d), llm, max_rounds=1, personas=PERSONAS_FAST)
        panel.deliberate(
            question="q",
            context="c",
            debate_id="t7",
            auto_decide=True,
        )
        speak_models = {m for m, sys_ in llm.calls if "토론 참가자" in sys_}
        # PERSONAS_FAST = [(B, ..., 'sonnet'), (D, ..., 'gpt-5.4-mini')]
        assert "sonnet" in speak_models
        assert "gpt-5.4-mini" in speak_models


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
            print(f"OK {name}")
            passed += 1
        except Exception as e:
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            failed.append(name)
    print(f"{passed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
