"""Health Monitor — 디스크/메모리 자원 감시 + 자동 회복.

매 작업 전 자원 상태 체크. 임계치 미만이면:
1. incident 기록 (state/health_incidents.jsonl)
2. remediator 호출 (보통 janitor + archive 즉시 삭제)
3. pause_seconds 만큼 sleep 후 재측정
4. 회복되면 consecutive_pauses 리셋, 미회복이면 +1
5. max_consecutive_pauses 초과 시 HealthExhausted

원인 분석/재발 방지:
- incidents.jsonl 누적 → 운영자가 패턴 분석 가능
- 같은 사유 N회 연속이면 자동 종료(코드 7) — 무한 루프 방지

stdlib만 사용 (psutil 의존성 없음). macOS는 vm_stat, linux는 /proc/meminfo.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class HealthThresholds:
    min_free_disk_mb: int = 1024  # 1 GB 미만이면 트리거
    min_free_mem_mb: int = 512  # 512 MB 미만이면 트리거
    pause_seconds: float = 60.0  # 트리거 시 sleep
    max_consecutive_pauses: int = 5  # 이만큼 연속 미회복 → HealthExhausted


@dataclass
class HealthSnapshot:
    free_disk_mb: int
    free_mem_mb: int
    state_dir_mb: int
    workspace_dir_mb: int

    def reasons(self, t: HealthThresholds) -> list[str]:
        """임계치 위반 사유. 측정 실패(-1)는 무시 — 위양성 방지."""
        out = []
        if 0 <= self.free_disk_mb < t.min_free_disk_mb:
            out.append(f"low_disk:{self.free_disk_mb}MB")
        if 0 <= self.free_mem_mb < t.min_free_mem_mb:
            out.append(f"low_memory:{self.free_mem_mb}MB")
        return out

    def healthy(self, t: HealthThresholds) -> bool:
        return not self.reasons(t)


class HealthExhausted(Exception):
    """자원 부족 회복 실패 — 운영자 개입 필요."""


class HealthMonitor:
    def __init__(
        self,
        state_dir: Path,
        workspace: Path,
        thresholds: HealthThresholds | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_event: Callable[[str], None] | None = None,
    ):
        self.state_dir = state_dir
        self.workspace = workspace
        self.t = thresholds or HealthThresholds()
        self.sleep = sleep
        self.on_event = on_event or (lambda msg: None)
        self.incidents_path = state_dir / "health_incidents.jsonl"
        self.consecutive_pauses = 0
        state_dir.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> HealthSnapshot:
        return HealthSnapshot(
            free_disk_mb=_free_disk_mb(self.state_dir),
            free_mem_mb=_free_mem_mb(),
            state_dir_mb=_dir_size_mb(self.state_dir),
            workspace_dir_mb=_dir_size_mb(self.workspace),
        )

    def check_and_remediate(self, remediator: Callable[[], dict[str, Any]]) -> None:
        """건강 체크 + 비정상 시 회복 시도.

        정상이면 즉시 return. 비정상이면 remediator 호출 + sleep + 재측정.
        max_consecutive_pauses 초과 시 HealthExhausted raise.
        """
        snap = self.snapshot()
        reasons = snap.reasons(self.t)
        if not reasons:
            self.consecutive_pauses = 0
            return

        reason_str = ",".join(reasons)
        self.on_event(
            f"⚠️  자원 부족: {reason_str} "
            f"(state={snap.state_dir_mb}MB, workspace={snap.workspace_dir_mb}MB)"
        )
        self._record_incident("before", reason_str, snap, remediation=None)

        try:
            result = remediator()
        except Exception as e:
            result = {"error": repr(e)}
        self.on_event(f"  cleanup → {result}")
        self.sleep(self.t.pause_seconds)

        snap2 = self.snapshot()
        reasons2 = snap2.reasons(self.t)
        self._record_incident("after", ",".join(reasons2) or "ok", snap2, remediation=result)

        if not reasons2:
            self.on_event(
                f"  ✅ 회복 (free_disk={snap2.free_disk_mb}MB, free_mem={snap2.free_mem_mb}MB)"
            )
            self.consecutive_pauses = 0
            return

        self.consecutive_pauses += 1
        self.on_event(
            f"  ❌ 미회복 {self.consecutive_pauses}/{self.t.max_consecutive_pauses}: "
            f"{','.join(reasons2)}"
        )
        if self.consecutive_pauses >= self.t.max_consecutive_pauses:
            raise HealthExhausted(
                f"자원 부족 회복 실패 ({','.join(reasons2)}). 원인 로그: {self.incidents_path}"
            )

    def _record_incident(
        self,
        phase: str,
        reason: str,
        snap: HealthSnapshot,
        remediation: dict[str, Any] | None,
    ) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "phase": phase,
            "reason": reason,
            "snapshot": asdict(snap),
            "remediation": remediation,
        }
        try:
            with self.incidents_path.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # incident 기록 실패는 시스템을 멈추지 않음


# ---- 측정 함수 ----


def _free_disk_mb(path: Path) -> int:
    try:
        return shutil.disk_usage(str(path)).free // (1024 * 1024)
    except OSError:
        return -1


def _free_mem_mb() -> int:
    """OS별 free+buffer 메모리(MB). 알 수 없으면 -1."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024
        except (OSError, ValueError):
            return -1
        return -1

    if sys.platform == "darwin":
        try:
            proc = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return -1
        if proc.returncode != 0:
            return -1

        page_size = 4096
        free_pages = 0
        for raw in proc.stdout.splitlines():
            line = raw.strip()
            if "page size of" in line:
                parts = line.split()
                try:
                    page_size = int(parts[parts.index("of") + 1])
                except (ValueError, IndexError):
                    pass
                continue
            for prefix in (
                "Pages free:",
                "Pages inactive:",
                "Pages speculative:",
                "Pages purgeable:",
            ):
                if line.startswith(prefix):
                    num = line[len(prefix) :].strip().rstrip(".")
                    try:
                        free_pages += int(num)
                    except ValueError:
                        pass
                    break
        return free_pages * page_size // (1024 * 1024)

    return -1  # type: ignore[unreachable]


def _dir_size_mb(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        return -1
    return total // (1024 * 1024)
