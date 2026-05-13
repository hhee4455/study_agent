"""공통 유틸리티 — LLM 호출, JSON 파싱, 텍스트 정리.

모든 에이전트가 budget을 알고 있고 매번 record를 호출하던 패턴을
LLMClient 하나로 통합. 호출자는 client.call(system, user)만 하면
budget 추적이 자동.

티어링: 일부 호출은 가벼운 모델로 보낼 수 있음 (정리, 분류 등).
client.call_with_tier("haiku", ...) 형태. tier 미지정 시 기본 모델.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Literal, Optional, Protocol


# Claude 모델 티어 (claude CLI의 --model 인자값)
ModelTier = Literal["opus", "sonnet", "haiku"]


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
        budget: Optional[BudgetRecorder] = None,
        codex_factory: Optional[Callable[[str], Callable[[str, str], tuple[str, int, int]]]] = None,
    ):
        self._factory = raw_caller_factory
        self._codex_factory = codex_factory
        self._default_model = default_model
        self._budget = budget
        self._cache: dict[str, Callable[[str, str], tuple[str, int, int]]] = {}

    def _is_codex_model(self, model: str) -> bool:
        return any(model.startswith(p) for p in self._CODEX_PREFIXES)

    def _get_caller(self, model: str):
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
        self, system: str, user: str,
        tier: Optional[ModelTier] = None,
        model: Optional[str] = None,
    ) -> str:
        """model이 명시되면 우선. 아니면 tier, 아니면 default_model."""
        chosen = model or tier or self._default_model
        text, tin, tout = self._get_caller(chosen)(system, user)
        if self._budget:
            self._budget(tin, tout)
        return text


def strip_fences(text: str) -> str:
    """코드 펜스 제거. 마크다운/JSON 응답 정리용."""
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = [ln for ln in s.split("\n") if not ln.strip().startswith("```")]
    return "\n".join(lines).strip()


def parse_json_loose(text: str) -> dict:
    """LLM 응답에서 JSON 추출. 코드펜스, 앞뒤 텍스트 허용.

    실패하면 빈 dict 반환 (예외 안 던짐 — 호출자가 키 존재 여부로 판단).
    """
    s = strip_fences(text)
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return {}


# 모델이 자기 이름표를 응답에 붙이는 경우 제거용 (debate_panel). D는 codex outsider.
AGENT_LABEL_RE = re.compile(r"^\[Agent-[A-D][^\]]*\]\s*", re.MULTILINE)


def strip_agent_label(text: str) -> str:
    return AGENT_LABEL_RE.sub("", text.strip())
