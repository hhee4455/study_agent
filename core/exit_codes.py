"""Exit codes — 종류별 종료 코드 + 사용자 권장조치 힌트."""

from __future__ import annotations

import sys
from enum import IntEnum
from typing import IO


class ExitCode(IntEnum):
    OK = 0
    GENERIC_ERROR = 1        # 분류되지 않은 일반 오류
    VALIDATION_FAILURE = 2   # plan/schema 검증 실패
    BUDGET_EXCEEDED = 3      # 시간/비용/턴 초과
    # 4 는 의도적으로 비워둠 — 과거 EXIT_BUDGET=4 와 충돌 방지 (test_lead_exit_codes
    # 의 `rc != 4` 가드 참고). RATE_LIMIT 는 12 로 재배치.
    MEMBER_TIMEOUT = 5       # 멤버 세션 timeout
    CONFLICT_UNRESOLVED = 6  # 머지 충돌 해결 실패
    AUTH_FAILURE = 7         # claude/codex 인증 실패
    GENERAL_FAILURE = 8      # 범용 실패 (힌트 fallback)
    NO_PROGRESS = 9          # 진행 없음 감지
    SERVER_ERROR = 10        # 서버/API 오류
    HEALTH_EXHAUSTED = 11    # 시스템 헬스 소진
    RATE_LIMIT_EXHAUSTED = 12  # API rate limit 영구 소진 (과거 4 와 분리)
    SPEC_NOT_FOUND = 17      # spec 파일 없음
    INTERRUPTED = 130        # Ctrl-C (SIGINT 표준)
    INTERRUPT = 130          # alias for INTERRUPTED


EXIT_HINTS: dict[ExitCode, str] = {
    ExitCode.OK: "정상 종료.",
    ExitCode.GENERIC_ERROR: "실패. timeline.md 와 stderr 로그를 확인하세요.",
    ExitCode.VALIDATION_FAILURE: "plan/brief schema 검증 실패. state/plan/ 또는 brief 로그를 확인하세요.",
    ExitCode.BUDGET_EXCEEDED: "예산(시간/비용/턴) 초과. budget.json 또는 --max-* 플래그를 확인하세요.",
    ExitCode.RATE_LIMIT_EXHAUSTED: "API rate limit 소진. claude login 후 재시도하거나 --max-parallel 을 줄이세요.",
    ExitCode.MEMBER_TIMEOUT: "멤버 세션이 timeout. 작업 단위를 더 작게 쪼개거나 --max-turns 를 늘려보세요.",
    ExitCode.CONFLICT_UNRESOLVED: "머지 충돌 해결 실패. state/conflicts/ 파일을 직접 확인하세요.",
    ExitCode.AUTH_FAILURE: "claude login 후 재시도하세요. (codex 사용 시 codex login)",
    ExitCode.GENERAL_FAILURE: "실패. stderr 로그와 timeline.md 를 확인하세요.",
    ExitCode.NO_PROGRESS: "진행 없음. plan.md 와 멤버 status 를 확인하세요.",
    ExitCode.SERVER_ERROR: "서버 오류. 잠시 후 재시도하거나 API 상태를 확인하세요.",
    ExitCode.HEALTH_EXHAUSTED: "시스템 헬스(디스크/메모리) 소진. 리소스를 확인하세요.",
    ExitCode.SPEC_NOT_FOUND: "spec 파일을 찾을 수 없습니다. --spec 경로를 확인하세요.",
    ExitCode.INTERRUPTED: "사용자 중단(Ctrl-C). restore_state 후 필요 시 재실행하세요.",
}


def hint_for(code: ExitCode | int) -> str:
    """code에 대응하는 권장조치 힌트를 반환한다. OK는 빈 문자열, 미지 코드는 GENERAL_FAILURE 힌트."""
    try:
        ec = ExitCode(int(code))
    except ValueError:
        ec = ExitCode.GENERAL_FAILURE
    if ec == ExitCode.OK:
        return ""
    return EXIT_HINTS.get(ec, EXIT_HINTS[ExitCode.GENERAL_FAILURE])


def print_hint(
    code: ExitCode | int,
    *,
    stream: IO[str] | None = None,
    extra: str | None = None,
) -> None:
    """code 힌트를 stream(기본 stderr)에 한 줄 출력한다. OK는 no-op."""
    hint = hint_for(code)
    if not hint:
        return
    if stream is None:
        stream = sys.stderr
    if extra:
        stream.write(f"[hint] {extra} — {hint}\n")
    else:
        stream.write(f"[hint] {hint}\n")


def format_failure_note(code: ExitCode | int, detail: str | None = None) -> str:
    """ExitCode + 선택적 detail을 사람이 읽을 수 있는 노트 문자열로 합성한다."""
    hint = hint_for(code)
    if detail and hint:
        return f"{detail} | hint: {hint}"
    if detail:
        return detail
    return hint


def format_exit_message(code: "ExitCode | int", detail: str | None = None) -> str:
    """코드 이름 + 힌트 + detail 을 합쳐 stderr 출력용 문자열을 반환한다."""
    try:
        ec = ExitCode(code)
        name = ec.name
        hint = EXIT_HINTS.get(ec, "")
    except ValueError:
        name = f"UNKNOWN({code})"
        hint = "알 수 없는 종료 코드. stderr 로그를 확인하세요."

    header = f"[exit={int(code)} {name}] {hint}"
    if detail is not None:
        return f"{header}\n  detail: {detail}"
    return header
