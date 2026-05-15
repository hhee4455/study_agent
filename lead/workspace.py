"""WorkspaceMerger — 팀원의 ws/{agent_id}/ → ws/main/ 머지.

머지 전략:
  1. 멤버에만 있는 파일/디렉토리: 그대로 복사
  2. main에만 있는 파일: 손대지 않음
  3. 양쪽 동일: skip
  4. 양쪽 수정 (conflict):
     - main 파일 유지
     - 멤버 파일은 main/{path}.from-{agent_id}로 보존
     - <state_dir>/lead/conflicts/{agent_id}-{ts}.md에 충돌 기록

자동 3-way 머지 안 함. 충돌 발생 시 팀장이 다음 tick에서 처리.
"""

from __future__ import annotations

import filecmp
import fnmatch
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# 머지에서 제외 — 의존성/캐시/VCS 디렉토리는 ws/main에 안 옮김.
# 멤버가 만들었어도 그 멤버 ws/{id}/ 안에는 남고 main만 깨끗.
SKIP_DIRS = {
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    ".cache",
    ".gradle",
    ".idea",
    ".vscode",
    ".seed",  # 멤버 ws 의 시드 원본 사본 — main 에 옮기지 않음
}
SKIP_FILE_GLOBS = (
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".DS_Store",
    "*.egg-info",  # 디렉토리 + 파일 둘 다
    ".coverage",
    ".coverage.*",  # pytest-cov 바이너리 — main 에 머지하면 안 됨
)


def _should_skip(entry: Path) -> bool:
    name = entry.name
    if entry.is_dir():
        return name in SKIP_DIRS or any(fnmatch.fnmatch(name, g) for g in SKIP_FILE_GLOBS)
    return any(fnmatch.fnmatch(name, g) for g in SKIP_FILE_GLOBS)


def _is_binary(path: Path, sample: int = 8192) -> bool:
    """파일 앞부분에 null byte 있으면 바이너리 — 토론/통합 불가능."""
    try:
        return b"\0" in path.read_bytes()[:sample]
    except OSError:
        return True


@dataclass
class MergeReport:
    agent_id: str
    copied: list[str] = field(default_factory=list)  # 새로 복사된 상대 경로
    conflicts: list[str] = field(default_factory=list)  # 충돌 상대 경로
    skipped_same: list[str] = field(default_factory=list)  # 동일해서 skip
    skipped_pattern: list[str] = field(default_factory=list)  # SKIP_DIRS/GLOBS로 제외
    conflict_report_path: str = ""  # 충돌 시 .md 경로

    def ok(self) -> bool:
        return not self.conflicts

    def summary(self) -> str:
        return (
            f"merge {self.agent_id}: copied={len(self.copied)} "
            f"conflicts={len(self.conflicts)} same={len(self.skipped_same)} "
            f"skip_pattern={len(self.skipped_pattern)}"
        )


class WorkspaceMerger:
    def __init__(self, main_ws: Path, conflicts_dir: Path):
        """
        main_ws: ws/main/ (또는 args.workspace)
        conflicts_dir: <state_dir>/lead/conflicts/
        """
        self.main_ws = main_ws
        self.conflicts_dir = conflicts_dir
        main_ws.mkdir(parents=True, exist_ok=True)
        conflicts_dir.mkdir(parents=True, exist_ok=True)

    def merge(self, agent_ws: Path, agent_id: str) -> MergeReport:
        report = MergeReport(agent_id=agent_id)
        if not agent_ws.exists():
            return report

        # 시드 사본 root — member.write_brief 가 만들어 둠. 없으면 멤버가 시드 안 받음
        # (이전 사이클 멤버, 또는 seed_files 비어있던 hire). 그 경우 seed 비교는 skip 됨.
        seed_root = agent_ws / ".seed"
        self._seed_root = seed_root if seed_root.is_dir() else None
        self._walk(agent_ws, self.main_ws, agent_ws, report, agent_id)

        if report.conflicts:
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            conflict_path = self.conflicts_dir / f"{agent_id}-{ts}.md"
            conflict_path.write_text(self._render_conflict_md(report))
            report.conflict_report_path = str(conflict_path)

        return report

    def _walk(
        self,
        member_dir: Path,
        main_dir: Path,
        member_root: Path,
        report: MergeReport,
        agent_id: str,
    ) -> None:
        for entry in sorted(member_dir.iterdir()):
            rel = entry.relative_to(member_root)
            target = main_dir / entry.name

            # P5 결정: symlink는 우회 벡터. 머지에서 거부 (conflict로 기록).
            if entry.is_symlink():
                report.conflicts.append(str(rel) + " (symlink rejected)")
                continue

            # venv/cache/node_modules/.git 등은 main에 안 옮김. 멤버 ws에 남아있음.
            if _should_skip(entry):
                report.skipped_pattern.append(str(rel))
                continue

            if entry.is_dir():
                if target.exists() and target.is_file():
                    # 멤버는 디렉토리, main은 파일 — 충돌
                    report.conflicts.append(str(rel))
                    self._stash(entry, target, agent_id, is_dir=True)
                    continue
                target.mkdir(parents=True, exist_ok=True)
                self._walk(entry, target, member_root, report, agent_id)
                continue

            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, target)
                report.copied.append(str(rel))
                continue

            # 파일 양쪽 존재 — 비교
            if filecmp.cmp(entry, target, shallow=False):
                report.skipped_same.append(str(rel))
                continue

            # 멤버가 실제로 *이 파일을 변경했는지* 시드 사본과 대조.
            # 시드 사본과 동일하면 = 멤버가 안 건드림. 그러면 main 의 현재 버전(다른 멤버가
            # 머지로 갱신했을 수 있음)을 그대로 유지하고, 멤버 파일은 폐기 (clean fast-forward).
            # 이게 없으면 동시 작업하던 다른 멤버가 main 을 변경한 직후 이 멤버가 머지할 때
            # *멤버는 안 건드린* 파일까지 모조리 충돌로 잡혀 토론 비용이 폭증한다.
            if self._seed_root is not None:
                seed_copy = self._seed_root / rel
                if seed_copy.is_file() and filecmp.cmp(entry, seed_copy, shallow=False):
                    report.skipped_same.append(str(rel) + " (unchanged-from-seed)")
                    continue

            # 바이너리는 토론 불가 — main 유지, 멤버 변경 폐기, conflict 가 아닌 skip 으로 기록
            if _is_binary(entry) or _is_binary(target):
                report.skipped_pattern.append(str(rel) + " (binary)")
                continue

            # 다름 → 충돌
            report.conflicts.append(str(rel))
            self._stash(entry, target, agent_id, is_dir=False)

    def _stash(self, member_entry: Path, main_target: Path, agent_id: str, *, is_dir: bool) -> None:
        """충돌난 멤버 측 파일을 main_target과 같은 부모에 .from-{agent_id} 접미로 보존."""
        stash_path = main_target.with_name(f"{main_target.name}.from-{agent_id}")
        if is_dir:
            stash_path = stash_path.with_name(f"{main_target.name}.from-{agent_id}.dir")
            if stash_path.exists():
                stash_path = stash_path.with_name(f"{stash_path.name}-{int(time.time())}")
            shutil.copytree(member_entry, stash_path)
        else:
            shutil.copy2(member_entry, stash_path)

    def _render_conflict_md(self, report: MergeReport) -> str:
        lines = [
            f"# Merge conflicts — {report.agent_id}",
            f"_생성: {datetime.now(UTC).isoformat()}_",
            "",
            "팀원이 만든 파일이 main_workspace의 파일과 충돌. main 파일은 유지되었고,",
            "멤버 파일은 `<path>.from-<agent_id>`로 옆에 보존됨.",
            "",
            "## 충돌 파일",
        ]
        for c in report.conflicts:
            lines.append(f"- `{c}` — 보존: `{c}.from-{report.agent_id}`")
        lines += [
            "",
            "## 깨끗하게 복사된 파일",
        ]
        if report.copied:
            for c in report.copied:
                lines.append(f"- `{c}`")
        else:
            lines.append("- (없음)")
        lines += [
            "",
            "## 다음 행동",
            "팀장이 (1) 멤버 변경 채택해 main 덮어쓰기, (2) 멤버 변경 폐기, ",
            "또는 (3) 새 멤버 채용해 두 버전 머지 중 결정.",
        ]
        return "\n".join(lines) + "\n"
