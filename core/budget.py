"""Budget — 시간/비용/턴 하드 리밋.

요구사항 5 (12-24h 자율) + max_turns 미설정 시 무한 루프 위험 방지.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


# Claude Opus 4.7 (2026.05 추정 — 실제 가격은 호출 시점에 확인)
PRICE_INPUT_PER_MTOK = 15.0
PRICE_OUTPUT_PER_MTOK = 75.0


@dataclass
class BudgetLimits:
    max_hours: float = 12.0
    max_cost_usd: float = 50.0
    max_turns: int = 1000


@dataclass
class BudgetState:
    started_at: float
    turns: int = 0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class BudgetExceeded(Exception):
    """하드 리밋 초과 — 시스템 즉시 정지."""


class BudgetManager:
    """LLM 호출마다 record 호출. 한도 초과 시 즉시 BudgetExceeded raise."""

    def __init__(self, limits: BudgetLimits, checkpoint: Optional[Path] = None):
        self.limits = limits
        self.checkpoint = checkpoint
        self.state = self._load()
        # 병렬 spawn (lead/team_lead.py ThreadPoolExecutor)에서 동시 record 호출 보호.
        self._lock = threading.Lock()

    def _load(self) -> BudgetState:
        if self.checkpoint and self.checkpoint.exists():
            return BudgetState(**json.loads(self.checkpoint.read_text()))
        return BudgetState(started_at=time.time())

    def _save(self) -> None:
        if self.checkpoint:
            self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
            self.checkpoint.write_text(json.dumps(asdict(self.state), indent=2))

    def record(self, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self.state.turns += 1
            self.state.tokens_in += tokens_in
            self.state.tokens_out += tokens_out
            self.state.cost_usd += (
                tokens_in * PRICE_INPUT_PER_MTOK / 1_000_000
                + tokens_out * PRICE_OUTPUT_PER_MTOK / 1_000_000
            )
            self._save()
            self._check()

    def _check(self) -> None:
        elapsed_h = (time.time() - self.state.started_at) / 3600
        if elapsed_h > self.limits.max_hours:
            raise BudgetExceeded(f"시간 한도 초과: {elapsed_h:.2f}h")
        if self.limits.max_cost_usd != float("inf") and self.state.cost_usd > self.limits.max_cost_usd:
            raise BudgetExceeded(f"비용 한도 초과: ${self.state.cost_usd:.2f}")
        if self.state.turns > self.limits.max_turns:
            raise BudgetExceeded(f"턴 한도 초과: {self.state.turns}")

    def can_continue(self) -> bool:
        try:
            self._check()
            return True
        except BudgetExceeded:
            return False

    def status(self) -> dict:
        elapsed_h = (time.time() - self.state.started_at) / 3600
        return {
            "elapsed_h": round(elapsed_h, 2),
            "cost_usd": round(self.state.cost_usd, 2),
            "turns": self.state.turns,
            "tokens_in": self.state.tokens_in,
            "tokens_out": self.state.tokens_out,
        }
