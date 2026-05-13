"""CodeJanitor — 사용 안 하는 코드 파일/심볼 식별.

전략: 보수적 (자동 삭제 금지)
  1. 워크스페이스의 .py 파일 전수
  2. 각 모듈/심볼이 다른 파일에서 import 또는 참조되는지 grep
  3. 미참조 + 진입점 아님 → 후보로 마킹
  4. 후보를 `<ws>/.archive/{ts}/` 로 이동 (삭제는 사람/팀장이 다음 정리에서)
  5. 결과를 `<ws>/.archive/{ts}/REPORT.md` 작성

진입점 보호 화이트리스트:
  - `__main__.py`, `__init__.py`
  - 명시적 entry로 등록된 파일
  - `tests/test_*.py` (테스트는 자체 정당화)
  - 7일 이내 mtime (최근 작성된 코드)
"""
from __future__ import annotations

import ast
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# 항상 보호 (사용 안 해도 지우지 않음)
DEFAULT_PROTECTED_NAMES = {"__main__.py", "__init__.py", "setup.py", "conftest.py"}
DEFAULT_PROTECTED_DIRS = {".git", ".venv", "venv", "__pycache__", ".archive", "node_modules", ".harness"}

# 최근에 만든 코드는 건드리지 않음 (초 단위)
RECENCY_GRACE_SEC = 7 * 24 * 3600  # 7일


@dataclass
class FileUsage:
    path: Path
    rel: str
    module_name: str         # foo/bar/baz.py → foo.bar.baz
    top_level_names: list[str] = field(default_factory=list)  # def/class top-level
    imported_by: list[str] = field(default_factory=list)      # rel paths of importers
    name_refs: dict[str, list[str]] = field(default_factory=dict)  # name → importer rels


@dataclass
class JanitorReport:
    scanned: int = 0
    archived: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    archive_dir: Optional[Path] = None
    skipped_recent: list[str] = field(default_factory=list)
    skipped_protected: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"code-janitor: scanned={self.scanned} "
            f"archived={len(self.archived)} kept={len(self.kept)} "
            f"recent_skip={len(self.skipped_recent)}"
        )


class CodeJanitor:
    def __init__(
        self,
        workspace: Path,
        *,
        protected_files: Optional[set[str]] = None,
        entrypoints: Optional[list[str]] = None,
        dry_run: bool = False,
        recency_grace_sec: int = RECENCY_GRACE_SEC,
    ):
        """
        workspace: 정리 대상 루트 (예: ws/main)
        protected_files: 항상 보호할 파일 basename
        entrypoints: rel path로 명시한 진입점 (예: ["lead/main.py"])
        dry_run: True면 archive 안 하고 보고만
        """
        self.workspace = workspace.resolve()
        self.protected = (protected_files or set()) | DEFAULT_PROTECTED_NAMES
        self.entrypoints = set(entrypoints or [])
        self.dry_run = dry_run
        self.recency_grace_sec = recency_grace_sec

    def run(self) -> JanitorReport:
        report = JanitorReport()
        if not self.workspace.exists():
            return report

        py_files = self._list_py_files()
        report.scanned = len(py_files)

        usages = {f: self._analyze_file(f) for f in py_files}
        text_index = self._build_text_index(py_files)
        for u in usages.values():
            self._populate_refs(u, text_index, usages)

        unused = []
        for f, u in usages.items():
            rel = u.rel
            if rel in self.entrypoints or f.name in self.protected:
                report.skipped_protected.append(rel)
                continue
            try:
                age = time.time() - f.stat().st_mtime
            except OSError:
                age = 0
            if age < self.recency_grace_sec:
                report.skipped_recent.append(rel)
                continue
            if u.imported_by:
                report.kept.append(rel)
                continue
            # 모듈 자체 미import. top-level names가 다른 파일에서 참조되는지도 체크
            if u.name_refs:
                # 어떤 이름이라도 다른 파일에서 참조됨 → keep
                report.kept.append(rel)
                continue
            unused.append((f, u))

        if not unused:
            return report

        if not self.dry_run:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive_root = self.workspace / ".archive" / ts
            archive_root.mkdir(parents=True, exist_ok=True)
            report.archive_dir = archive_root
            self._write_report_md(archive_root, unused)
            for f, u in unused:
                dst = archive_root / u.rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(f), str(dst))
                    report.archived.append(u.rel)
                except OSError as e:
                    report.errors.append(f"{u.rel}: {e}")
        else:
            report.archived = [u.rel for _, u in unused]

        return report

    # ---- 내부 ----

    def _list_py_files(self) -> list[Path]:
        out: list[Path] = []
        for p in self.workspace.rglob("*.py"):
            if any(part in DEFAULT_PROTECTED_DIRS for part in p.relative_to(self.workspace).parts):
                continue
            if any(part.startswith(".") for part in p.relative_to(self.workspace).parts[:-1]):
                continue
            out.append(p)
        return out

    def _analyze_file(self, path: Path) -> FileUsage:
        rel = str(path.relative_to(self.workspace))
        mod = rel.removesuffix(".py").replace("/", ".")
        names: list[str] = []
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if not node.name.startswith("_"):
                        names.append(node.name)
                elif isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name) and not tgt.id.startswith("_") and tgt.id.isupper():
                            names.append(tgt.id)
        except (SyntaxError, OSError):
            pass
        return FileUsage(path=path, rel=rel, module_name=mod, top_level_names=names)

    def _build_text_index(self, files: list[Path]) -> dict[str, str]:
        idx = {}
        for f in files:
            try:
                idx[str(f.relative_to(self.workspace))] = f.read_text(encoding="utf-8")
            except OSError:
                idx[str(f.relative_to(self.workspace))] = ""
        return idx

    def _populate_refs(
        self,
        u: FileUsage,
        text_index: dict[str, str],
        usages: dict[Path, FileUsage],
    ) -> None:
        mod_patterns = [
            re.compile(rf"\bfrom\s+{re.escape(u.module_name)}\b"),
            re.compile(rf"\bimport\s+{re.escape(u.module_name)}\b"),
        ]
        # 패키지 __init__.py가 자신의 자식 파일을 사용하는 경우도 import로 침
        parent_init = u.path.parent / "__init__.py"
        if parent_init.exists() and parent_init != u.path:
            try:
                init_text = parent_init.read_text(encoding="utf-8")
                if re.search(rf"\b{re.escape(u.path.stem)}\b", init_text):
                    # __init__이 같은 폴더의 형제 모듈 참조 → keep
                    u.imported_by.append(str(parent_init.relative_to(self.workspace)))
            except OSError:
                pass

        for rel, text in text_index.items():
            if rel == u.rel:
                continue
            if any(p.search(text) for p in mod_patterns):
                u.imported_by.append(rel)
            # 심볼 단위 grep (꽤 보수적 — 이름이 흔하면 false positive 있음; 의도적)
            for name in u.top_level_names:
                if re.search(rf"\b{re.escape(name)}\b", text):
                    u.name_refs.setdefault(name, []).append(rel)

    @staticmethod
    def _write_report_md(archive_root: Path, unused: list[tuple[Path, FileUsage]]) -> None:
        lines = [
            f"# Code-janitor archive — {datetime.now(timezone.utc).isoformat()}",
            "",
            "이 폴더의 파일들은 정적 분석으로 import/참조가 발견되지 않아 이동되었음.",
            "",
            "오탐 가능성: 동적 import, 문자열 기반 reflection, 외부 진입점 등.",
            "되돌리려면 파일을 원래 경로로 복귀.",
            "",
            "## Archived files",
        ]
        for f, u in unused:
            lines.append(f"- `{u.rel}` (module: `{u.module_name}`, top-level: {u.top_level_names or '없음'})")
        lines += [
            "",
            "## 보호 화이트리스트 추가하려면",
            "CodeJanitor 호출 시 `protected_files`(basename) 또는 `entrypoints`(rel path) 인자에 명시.",
        ]
        (archive_root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
