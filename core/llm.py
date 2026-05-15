"""공통 유틸리티 — LLM 호출, JSON 파싱, 텍스트 정리.

모든 에이전트가 budget을 알고 있고 매번 record를 호출하던 패턴을
LLMClient 하나로 통합. 호출자는 client.call(system, user)만 하면
budget 추적이 자동.

티어링: 일부 호출은 가벼운 모델로 보낼 수 있음 (정리, 분류 등).
client.call_with_tier("haiku", ...) 형태. tier 미지정 시 기본 모델.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Literal, Protocol

from core import budget as _budget

_log = logging.getLogger(__name__)
_call_counter = itertools.count(1)


# Thread-local: 진행 중인 call 의 metadata 와 "이미 record 했는지" 플래그.
# cli_caller 가 정확한 USD 와 함께 record 하면 _recorded 를 True 로 마킹 →
# LLMClient.call 은 추가 record 생략. CLI 미설치 stub 경로는 cli_caller 를
# 통하지 않으므로 LLMClient.call 이 fallback 으로 record.
class _CallContext(threading.local):
    model: str | None = None
    agent: str | None = None
    goal_id: str | None = None
    call_kind: str | None = None
    recorded: bool = False


_CALL_CTX = _CallContext()


@contextmanager
def _call_context(
    model: str,
    agent: str | None,
    goal_id: str | None,
    call_kind: str,
) -> Iterator[None]:
    prev = (
        _CALL_CTX.model,
        _CALL_CTX.agent,
        _CALL_CTX.goal_id,
        _CALL_CTX.call_kind,
        _CALL_CTX.recorded,
    )
    _CALL_CTX.model = model
    _CALL_CTX.agent = agent
    _CALL_CTX.goal_id = goal_id
    _CALL_CTX.call_kind = call_kind
    _CALL_CTX.recorded = False
    try:
        yield
    finally:
        (
            _CALL_CTX.model,
            _CALL_CTX.agent,
            _CALL_CTX.goal_id,
            _CALL_CTX.call_kind,
            _CALL_CTX.recorded,
        ) = prev


def current_call_meta() -> dict[str, str | None]:
    """cli_caller 가 record_usage 시 첨부할 메타데이터. 컨텍스트 밖이면 모두 None."""
    return {
        "agent": _CALL_CTX.agent,
        "goal_id": _CALL_CTX.goal_id,
        "call_kind": _CALL_CTX.call_kind,
    }


def mark_cli_recorded() -> None:
    """cli_caller 가 record_usage 호출했음을 표시 → LLMClient 가 중복 기록 안 함."""
    _CALL_CTX.recorded = True


def _cli_recorded_last_call() -> bool:
    return _CALL_CTX.recorded


# Claude 모델 티어 (claude CLI의 --model 인자값)
ModelTier = Literal["opus", "sonnet", "haiku"]

# 명시적 모델 식별자 상수 — 2단계 escalate (sonnet → opus) 호출자가 매직 스트링 대신 사용.
# 값은 LLMClient.call(model=...) / tier=... 에 그대로 전달 가능.
MODEL_SONNET: ModelTier = "sonnet"
MODEL_OPUS: ModelTier = "opus"
MODEL_HAIKU: ModelTier = "haiku"


class BudgetRecorder(Protocol):
    """BudgetManager.record와 호환되는 콜러블."""

    def __call__(self, tokens_in: int, tokens_out: int) -> None: ...


class LLMClient:
    """LLM 호출 + budget 자동 기록 + 선택적 모델 티어링 + multi-backend 라우팅.

    `raw_caller_factory`는 model 받아서 callable 반환 — claude 백엔드.
    추가로 `codex_factory`를 주면 모델 prefix(`gpt-`, `o1`, `o3`, `codex-`)에 따라
    codex 백엔드로 라우팅.

    예:
        client.call("sys", "user")                      # 기본 (claude opus)
        client.call("sys", "user", tier="haiku")        # claude haiku
        client.call("sys", "user", model="gpt-5")       # codex (gpt-5)
        client.call("sys", "user", model="o3")          # codex (o3 reasoning)
    """

    _CODEX_PREFIXES = ("gpt-", "o1", "o3", "o4", "codex-")

    def __init__(
        self,
        raw_caller_factory: Callable[[str], Callable[[str, str], tuple[str, int, int]]],
        default_model: str = "opus",
        budget: BudgetRecorder | None = None,
        codex_factory: Callable[[str], Callable[[str, str], tuple[str, int, int]]] | None = None,
        agent_id: str | None = None,
        goal_id: str | None = None,
    ):
        self._factory = raw_caller_factory
        self._codex_factory = codex_factory
        self._default_model = default_model
        self._budget = budget
        self._agent_id = agent_id
        self._goal_id = goal_id
        self._cache: dict[str, Callable[[str, str], tuple[str, int, int]]] = {}

    def _is_codex_model(self, model: str) -> bool:
        return any(model.startswith(p) for p in self._CODEX_PREFIXES)

    def _get_caller(self, model: str) -> Callable[[str, str], tuple[str, int, int]]:
        if model not in self._cache:
            if self._is_codex_model(model):
                if not self._codex_factory:
                    raise RuntimeError(
                        f"모델 '{model}'은 codex 백엔드를 요구하는데 codex_factory가 주입 안 됨"
                    )
                self._cache[model] = self._codex_factory(model)
            else:
                self._cache[model] = self._factory(model)
        return self._cache[model]

    def call(
        self,
        system: str,
        user: str,
        tier: ModelTier | None = None,
        model: str | None = None,
        call_kind: str = "llm_call",
    ) -> str:
        """model이 명시되면 우선. 아니면 tier, 아니면 default_model.

        하드 리밋 추적: legacy ``self._budget(tin, tout)`` 콜백 호출.

        세부 토큰/USD 누적 (state/budget.json, events.jsonl) 은 ``cli_caller``
        쪽 stream_call 이 CLI 의 정확한 ``total_cost_usd`` 와 함께 직접 기록.
        여기서는 모델 ID 와 agent/goal 메타데이터를 thread-local 로 노출해
        cli_caller 가 함께 기록하도록 한다 — stub 백엔드는 LLMClient 가 대신
        record.
        """
        chosen = model or tier or self._default_model
        with _call_context(
            model=chosen,
            agent=self._agent_id,
            goal_id=self._goal_id,
            call_kind=call_kind,
        ):
            text, tin, tout = self._get_caller(chosen)(system, user)
        if self._budget:
            self._budget(tin, tout)
        if not _cli_recorded_last_call():
            call_id = f"llm-{next(_call_counter):06d}"
            try:
                _budget.record_usage(
                    call_id=call_id,
                    model=chosen,
                    input_tokens=tin,
                    output_tokens=tout,
                    meta={
                        "agent": self._agent_id,
                        "goal_id": self._goal_id,
                        "call_kind": call_kind,
                        "source": "llm_client",
                    },
                )
            except (OSError, ValueError) as e:
                _log.warning("budget.record_usage 실패 (무시): %s", e)
        return text


def strip_fences(text: str) -> str:
    """코드 펜스 제거. 마크다운/JSON 응답 정리용."""
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = [ln for ln in s.split("\n") if not ln.strip().startswith("```")]
    return "\n".join(lines).strip()


def parse_json_loose(text: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 추출. 코드펜스, 앞뒤 텍스트 허용.

    실패하면 빈 dict 반환 (예외 안 던짐 — 호출자가 키 존재 여부로 판단).
    """
    s = strip_fences(text)
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        result: dict[str, Any] = json.loads(s[start : end + 1])
        return result
    except json.JSONDecodeError:
        return {}


# 모델이 자기 이름표를 응답에 붙이는 경우 제거용 (debate_panel). D는 codex outsider.
AGENT_LABEL_RE = re.compile(r"^\[Agent-[A-D][^\]]*\]\s*", re.MULTILINE)


def strip_agent_label(text: str) -> str:
    return AGENT_LABEL_RE.sub("", text.strip())
