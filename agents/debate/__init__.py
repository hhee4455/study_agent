"""Debate — 토론 패널. 결정 게이트 md 생성.

high-stakes 는 PERSONAS(4명/R2), 충돌 머지는 PERSONAS_FAST(2명/R1) 권장.
"""

from .panel import PERSONAS, PERSONAS_FAST, DebateOutcome, DebatePanel

__all__ = ["PERSONAS", "PERSONAS_FAST", "DebateOutcome", "DebatePanel"]
