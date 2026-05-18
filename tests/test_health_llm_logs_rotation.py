"""llm_logs rotation/cap guard 단위 테스트.

in-process 전용 — 실제 lead/member 프로세스 불기동.
tmp_path fixture 로 가짜 llm_logs 디렉토리를 만들어 검증한다.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.health import HealthMonitor, HealthThresholds


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _make_monitor(tmp_path: Path) -> HealthMonitor:
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    return HealthMonitor(state_dir=state, workspace=tmp_path)


def _create_files(directory: Path, count: int, base_mtime: float | None = None) -> list[Path]:
    """디렉토리에 count개 파일을 생성하고 mtime을 순서대로 설정한다."""
    directory.mkdir(parents=True, exist_ok=True)
    t0 = base_mtime if base_mtime is not None else time.time() - count * 2
    files = []
    for i in range(count):
        f = directory / f"log_{i:04d}.jsonl"
        f.write_text(f"log entry {i}")
        mtime = t0 + i
        os.utime(f, (mtime, mtime))
        files.append(f)
    return files


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------


def test_no_rotation_under_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """파일 수가 cap 이하면 아무것도 삭제하지 않는다."""
    monkeypatch.setenv("AGENT_LLM_LOGS_CAP", "200")
    monitor = _make_monitor(tmp_path)
    llm_logs = tmp_path / "state" / "llm_logs"
    _create_files(llm_logs, 100)

    monitor._rotate_llm_logs()

    remaining = [p for p in llm_logs.iterdir() if p.is_file()]
    assert len(remaining) == 100


def test_no_rotation_at_exact_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """파일 수가 cap과 같을 때도 삭제하지 않는다."""
    monkeypatch.setenv("AGENT_LLM_LOGS_CAP", "10")
    monitor = _make_monitor(tmp_path)
    llm_logs = tmp_path / "state" / "llm_logs"
    _create_files(llm_logs, 10)

    monitor._rotate_llm_logs()

    remaining = [p for p in llm_logs.iterdir() if p.is_file()]
    assert len(remaining) == 10


def test_rotation_removes_oldest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cap 초과 시 mtime 기준 가장 오래된 파일부터 삭제한다."""
    monkeypatch.setenv("AGENT_LLM_LOGS_CAP", "10")
    monitor = _make_monitor(tmp_path)
    llm_logs = tmp_path / "state" / "llm_logs"
    files = _create_files(llm_logs, 15)  # 15 > cap=10 → target=8, delete 7

    oldest_names = {f.name for f in files[:7]}   # 삭제돼야 할 파일
    newest_names = {f.name for f in files[-8:]}  # 남아야 할 파일

    monitor._rotate_llm_logs()

    remaining = {f.name for f in llm_logs.iterdir() if f.is_file()}
    assert len(remaining) == 8
    assert newest_names <= remaining
    assert not (oldest_names & remaining)


def test_graceful_when_dir_missing(tmp_path: Path) -> None:
    """llm_logs 디렉토리가 없을 때 예외 없이 종료한다."""
    monitor = _make_monitor(tmp_path)
    # llm_logs 디렉토리를 생성하지 않음
    monitor._rotate_llm_logs()  # should not raise


def test_env_var_override_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENT_LLM_LOGS_CAP 환경변수로 cap을 override할 수 있다."""
    monkeypatch.setenv("AGENT_LLM_LOGS_CAP", "50")
    monitor = _make_monitor(tmp_path)
    llm_logs = tmp_path / "state" / "llm_logs"
    _create_files(llm_logs, 60)  # 60 > cap=50 → target=40, delete 20

    monitor._rotate_llm_logs()

    remaining = [p for p in llm_logs.iterdir() if p.is_file()]
    assert len(remaining) == 40  # int(50 * 0.8) = 40


def test_disabled_when_cap_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cap=0이면 가드가 비활성화되어 파일을 삭제하지 않는다."""
    monkeypatch.setenv("AGENT_LLM_LOGS_CAP", "0")
    monitor = _make_monitor(tmp_path)
    llm_logs = tmp_path / "state" / "llm_logs"
    _create_files(llm_logs, 500)

    monitor._rotate_llm_logs()

    remaining = [p for p in llm_logs.iterdir() if p.is_file()]
    assert len(remaining) == 500


def test_disabled_when_cap_negative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cap < 0이면 가드가 비활성화된다."""
    monkeypatch.setenv("AGENT_LLM_LOGS_CAP", "-1")
    monitor = _make_monitor(tmp_path)
    llm_logs = tmp_path / "state" / "llm_logs"
    _create_files(llm_logs, 300)

    monitor._rotate_llm_logs()

    remaining = [p for p in llm_logs.iterdir() if p.is_file()]
    assert len(remaining) == 300


def test_default_cap_is_200(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """환경변수 미설정 시 기본 cap=200이 적용된다."""
    monkeypatch.delenv("AGENT_LLM_LOGS_CAP", raising=False)
    monitor = _make_monitor(tmp_path)
    llm_logs = tmp_path / "state" / "llm_logs"
    _create_files(llm_logs, 220)  # 220 > 200 → target=160, delete 60

    monitor._rotate_llm_logs()

    remaining = [p for p in llm_logs.iterdir() if p.is_file()]
    assert len(remaining) == 160  # int(200 * 0.8) = 160


def test_rotation_triggered_via_check_and_remediate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_and_remediate 호출 시 rotation이 자동으로 트리거된다."""
    monkeypatch.setenv("AGENT_LLM_LOGS_CAP", "10")
    thresholds = HealthThresholds(
        min_free_disk_mb=0,   # 임계치 0 → 항상 healthy → 즉시 return
        min_free_mem_mb=0,
        pause_seconds=0.0,
        max_consecutive_pauses=5,
    )
    monitor = HealthMonitor(
        state_dir=tmp_path / "state",
        workspace=tmp_path,
        thresholds=thresholds,
        sleep=lambda _: None,
    )
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)

    llm_logs = tmp_path / "state" / "llm_logs"
    _create_files(llm_logs, 15)

    monitor.check_and_remediate(remediator=lambda: {})

    remaining = [p for p in llm_logs.iterdir() if p.is_file()]
    assert len(remaining) == 8  # int(10 * 0.8) = 8


def test_subdirs_in_llm_logs_not_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """llm_logs 내 서브디렉토리는 삭제하지 않는다."""
    monkeypatch.setenv("AGENT_LLM_LOGS_CAP", "5")
    monitor = _make_monitor(tmp_path)
    llm_logs = tmp_path / "state" / "llm_logs"
    _create_files(llm_logs, 10)

    subdir = llm_logs / "archive"
    subdir.mkdir()

    monitor._rotate_llm_logs()

    assert subdir.exists()
    remaining_files = [p for p in llm_logs.iterdir() if p.is_file()]
    assert len(remaining_files) == 4  # int(5 * 0.8) = 4
