"""Rate limit 대응 — exponential backoff + jitter.

Claude Code CLI에서 만나는 두 종류의 rate limit:

1. **Usage limit** (5-hour / weekly window)
   - "API Error: Rate limit reached" / "usage limit"
   - 회복: 분~시간 단위. 짧게 기다려서 풀릴 게 아님
   - 대응: 길게 sleep (5분) 후 재시도, N번 실패하면 포기

2. **Burst/concurrency limit** (서버 단기)
   - "Server is temporarily limiting requests (not your usage limit)"
   - 회복: 초~분 단위
   - 대응: exponential backoff (1s → 2s → 4s → 8s) + jitter

retry_after 헤더가 응답에 있으면 우선. 없으면 패턴에 따라 결정.

레퍼런스:
- https://github.com/anthropics/claude-code/issues/53922 (burst limit)
- https://platform.claude.com/docs/en/api/rate-limits (retry-after)
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

# 패턴 — Claude Code의 stderr/stdout에서 잡아냄
USAGE_LIMIT_PATTERNS = [
    r"usage limit",
    r"5[- ]hour",
    r"weekly limit",
    r"rate limit reached",
]
BURST_LIMIT_PATTERNS = [
    r"server is temporarily limiting",
    r"not your usage limit",
    r"too many requests",
]
# 서버측 일시 과부하 (529 Overloaded 등) — 사용자 한도가 아니라 Anthropic 서버 문제.
# burst 와 같은 backoff 재시도로 처리하지 않으면 사일런트 실패 → 무한 재spawn 루프.
SERVER_ERROR_PATTERNS = [
    r"API Error:\s*5\d\d",
    r"\boverloaded\b",
    r"server[- ]side issue",
    r"status\.claude\.com",
]
RETRY_AFTER_RE = re.compile(r"retry[- ]after[:\s]+(\d+)", re.IGNORECASE)


@dataclass
class RateLimitConfig:
    # Burst limit 처리
    burst_initial_delay: float = 1.0  # 첫 backoff
    burst_max_delay: float = 60.0  # 한 번에 안 넘어가는 상한
    burst_max_retries: int = 5

    # Usage limit 처리
    usage_delay_sec: float = 300.0  # 5분 sleep
    usage_max_retries: int = 3  # 총 15분 시도 후 포기

    # 서버 과부하 (5xx) — burst 보다 짧게 시작, 더 길게 기다림. claude CLI 가 이미
    # 내부에서 10회 재시도하고 던진 거라 너무 자주 두드리지 말 것.
    server_error_initial_delay: float = 30.0
    server_error_max_delay: float = 300.0
    server_error_max_retries: int = 3

    # 모든 호출에 추가하는 jitter (burst 여러 호출이 동시에 풀려서 다시 burst 막기)
    jitter_pct: float = 0.2


@dataclass
class CallOutcome:
    """LLM 호출 결과 분류."""

    kind: str  # "ok" | "burst_limit" | "usage_limit" | "other_error"
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    retry_after_sec: float | None = None


def classify_response(text: str, returncode: int = 0) -> str:
    """응답 텍스트 + returncode로 종류 판정.

    Claude CLI는 rate limit 에러 시 비-0 exit code 또는 에러 메시지를 stdout에 담음.
    text는 stdout + stderr 합쳐서 넘기면 됨.
    """
    if returncode == 0 and not _matches_any(text, USAGE_LIMIT_PATTERNS + BURST_LIMIT_PATTERNS):
        return "ok"

    text_lower = text.lower()
    if _matches_any(text_lower, BURST_LIMIT_PATTERNS):
        return "burst_limit"
    if _matches_any(text_lower, USAGE_LIMIT_PATTERNS):
        return "usage_limit"
    return "other_error"


def parse_retry_after(text: str) -> float | None:
    m = RETRY_AFTER_RE.search(text)
    return float(m.group(1)) if m else None


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


class RateLimitedCaller:
    """원시 LLM 호출자를 감싸 rate limit 대응을 추가.

    원시 호출자 시그니처: (system, user) -> CallOutcome
    이 클래스는 외부에 (system, user) -> (text, tokens_in, tokens_out)로 노출.

    일반 호출 → 그대로 반환
    burst → exponential backoff
    usage → 길게 sleep 후 재시도, 한도 초과 시 RateLimitExhausted
    """

    def __init__(
        self,
        raw_caller: Callable[[str, str], CallOutcome],
        config: RateLimitConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_wait: Callable[[str, float], None] | None = None,
    ):
        self.raw = raw_caller
        self.cfg = config or RateLimitConfig()
        self.sleep = sleep
        self.on_wait = on_wait or (lambda kind, secs: None)

    def __call__(self, system: str, user: str) -> tuple[str, int, int]:
        """RateLimitExhausted/ServerError 를 raise 하거나 정상 응답 반환."""
        burst_attempts = 0
        usage_attempts = 0
        server_attempts = 0

        while True:
            outcome = self.raw(system, user)

            if outcome.kind == "ok":
                return outcome.text, outcome.tokens_in, outcome.tokens_out

            if outcome.kind == "burst_limit":
                if burst_attempts >= self.cfg.burst_max_retries:
                    raise RateLimitExhausted(f"burst limit retries 초과: {outcome.text[:200]}")
                delay = self._burst_delay(burst_attempts, outcome.retry_after_sec)
                self.on_wait("burst", delay)
                self.sleep(delay)
                burst_attempts += 1
                continue

            if outcome.kind == "usage_limit":
                if usage_attempts >= self.cfg.usage_max_retries:
                    raise RateLimitExhausted(f"usage limit retries 초과: {outcome.text[:200]}")
                delay = outcome.retry_after_sec or self.cfg.usage_delay_sec
                self.on_wait("usage", delay)
                self.sleep(delay)
                usage_attempts += 1
                continue

            # other_error 중 일부는 서버측 일시 과부하 (529 등). 텍스트 패턴으로 식별해
            # backoff 재시도. cli_caller 가 is_error=True 경로로 only kind=other_error 를
            # 만들고 분류 정보를 잃으니 여기서 한 번 더 본다.
            if outcome.kind == "other_error" and _matches_any(outcome.text, SERVER_ERROR_PATTERNS):
                if server_attempts >= self.cfg.server_error_max_retries:
                    raise ServerError(f"서버 과부하 retries 초과: {outcome.text[:200]}")
                delay = self._server_delay(server_attempts, outcome.retry_after_sec)
                self.on_wait("server", delay)
                self.sleep(delay)
                server_attempts += 1
                continue

            # 진짜 일반 에러 — 호출자가 try/except 로 처리하도록 예외로 던진다.
            # 이전엔 outcome.text 를 정상 응답인 척 return 해서 silent corruption 발생
            # (예: "API Error: ..." 가 코드 펜스 추출/JSON 파싱에 그대로 흘러감).
            raise OtherCallError(outcome.text[:300] or "raw call failed (kind=other_error)")

    def _burst_delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after:
            base = retry_after
        else:
            base = min(
                self.cfg.burst_initial_delay * (2**attempt),
                self.cfg.burst_max_delay,
            )
        # jitter
        jitter = base * self.cfg.jitter_pct
        return base + random.uniform(-jitter, jitter)

    def _server_delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after:
            base = retry_after
        else:
            base = min(
                self.cfg.server_error_initial_delay * (2**attempt),
                self.cfg.server_error_max_delay,
            )
        jitter = base * self.cfg.jitter_pct
        return base + random.uniform(-jitter, jitter)


class RateLimitExhausted(Exception):
    """Rate limit 재시도 횟수 초과 — 작업 실패로 처리하고 다음 작업으로."""


class ServerError(Exception):
    """서버측 일시 과부하 (5xx) 재시도 횟수 초과 — 작업 실패로 처리하고 다음 작업으로."""


class OtherCallError(Exception):
    """rate limit / 서버 5xx 외 일반 raw 호출 실패. 호출자가 graceful degrade 결정."""
