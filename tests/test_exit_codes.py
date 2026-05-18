"""Unit tests for agent_system/core/exit_codes.py."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.exit_codes import ExitCode, EXIT_HINTS, format_exit_message


def test_legacy_code_values():
    """ExitCode 의 핵심 값이 변경되지 않았는지 회귀 방지.

    설계 규칙(test_lead_exit_codes 와 일관):
      - BUDGET_EXCEEDED(3) 와 RATE_LIMIT_EXHAUSTED 는 분리된 코드여야 한다
        (과거 EXIT_BUDGET=4 와 동일 코드로 묶이지 않게).
      - INTERRUPTED 는 POSIX 관례에 따라 130.
    """
    assert ExitCode.GENERIC_ERROR == 1
    assert ExitCode.BUDGET_EXCEEDED == 3
    assert ExitCode.RATE_LIMIT_EXHAUSTED != ExitCode.BUDGET_EXCEEDED
    assert int(ExitCode.RATE_LIMIT_EXHAUSTED) != 4, (
        "RATE_LIMIT_EXHAUSTED 는 과거 EXIT_BUDGET=4 와 분리돼야 함"
    )
    assert ExitCode.CONFLICT_UNRESOLVED == 6
    assert ExitCode.AUTH_FAILURE == 7
    assert ExitCode.INTERRUPTED == 130


def test_exit_hints_covers_all_members():
    """EXIT_HINTS 가 ExitCode 의 모든 멤버를 키로 포함해야 한다."""
    missing = [m for m in ExitCode if m not in EXIT_HINTS]
    assert missing == [], f"EXIT_HINTS 에 누락된 ExitCode 멤버: {missing}"


def test_format_auth_failure_contains_name_and_login():
    msg = format_exit_message(ExitCode.AUTH_FAILURE)
    assert "AUTH_FAILURE" in msg
    assert "login" in msg


def test_format_rate_limit_with_detail():
    msg = format_exit_message(ExitCode.RATE_LIMIT_EXHAUSTED, detail="429")
    assert "429" in msg
    assert "RATE_LIMIT_EXHAUSTED" in msg


def test_format_unknown_int_no_exception():
    msg = format_exit_message(999)
    assert isinstance(msg, str)
    assert len(msg) > 0


def test_format_unknown_int_contains_fallback():
    msg = format_exit_message(999)
    assert "UNKNOWN" in msg or "999" in msg


def test_format_ok_no_detail():
    msg = format_exit_message(ExitCode.OK)
    assert "OK" in msg
    assert "detail:" not in msg


def test_format_with_none_detail_omits_detail_line():
    msg = format_exit_message(ExitCode.GENERIC_ERROR, detail=None)
    assert "detail:" not in msg


def test_format_with_detail_included():
    msg = format_exit_message(ExitCode.BUDGET_EXCEEDED, detail="turn limit 50 reached")
    assert "turn limit 50 reached" in msg
    assert "detail:" in msg
