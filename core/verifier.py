"""Verifier — 종료 조건 판정.

"완벽"은 모델 자기평가에 맡기면 reward hacking. 대신 객관적 검증만 사용.
- shell: 명령 종료 코드 0
- file_exists: 파일/디렉토리 존재
- file_contains: 파일 내용 정규식 매칭

llm_judge는 의도적으로 빠짐 — self-evaluation은 신뢰 불가.
정말 필요하면 호출자가 별도로 처리.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CheckKind = Literal["shell", "file_exists", "file_contains"]


# LLM이 생성한 shell command에 대한 sanity check.
# 2026-05-13 토론 (4-way-security.md) 결정으로 도입:
#   - LLM의 verification_checks가 자유 shell이라 격리 ws 안이어도 손상 가능
#   - 1차 방어선: deny-list (명백히 위험한 토큰) + whitelist (안전 명령어만)
#   - 장기적으론 AST-typed checks로 전환 예정 (현 코드는 임시 방패)
#
# 차단 토큰: 파괴/권한상승/외부통신/리다이렉트 인젝션.
SHELL_DENY_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bsudo\b",
    r"\bsu\s+-?\b",
    r"\bdoas\b",
    r"\bcurl\b",
    r"\bwget\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bdd\s",
    r"\bmkfs\b",
    r"\$\(",  # command substitution
    r"`[^`]*`",  # backtick substitution
    r"\|\s*(sh|bash|zsh|python|ruby|perl)\b",  # pipe to interpreter
    r"\bnc\b\s",  # netcat
    r"\bssh\b\s",
    r"\.\./\.\./",  # 2단 이상 상위 디렉토리 (../까지는 file_exists 등에서 자주 쓰이므로 허용)
    r">\s*/dev/(sd|nvme|disk)",  # 디스크 raw write
    r"\beval\b",
    r":\s*\(\s*\)\s*\{",  # fork bomb 패턴
]

# 허용 명령 prefix (whitelist). shell command가 이 중 하나로 시작해야 통과.
# pipe / && / ;로 연결된 경우 각 segment 첫 토큰 모두 검사.
SHELL_ALLOW_PREFIXES = {
    "test",
    "[",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "find",
    "echo",
    "true",
    "false",
    "python",
    "python3",
    "pytest",
    "node",
    "npm",
    "npx",
    "ruff",
    "mypy",
    "black",
    "git",
    "bash",
    "sh",  # 단, deny 패턴이 우선 적용됨
    "diff",
    "cmp",
    "stat",
    "file",
    "awk",
    "sed",  # 읽기 전용 사용이 일반적
    "sort",
    "uniq",
    "tr",
    "make",
}


def shell_sanity_check(command: str) -> tuple[bool, str]:
    """LLM이 생성한 shell command에 대한 sanity check.

    Returns (ok, reason). ok=False면 reason이 거부 사유.
    """
    if not command or not command.strip():
        return False, "빈 command"
    cmd = command.strip()

    # 0) 호스트 민감 경로(`.ssh`, `.env` 등) — P5 토론 결정 (2026-05-13)
    from core.path_guard import host_path_blocked

    bad, reason = host_path_blocked(cmd)
    if bad:
        return False, reason

    # 1) deny-list 토큰 검사
    for pat in SHELL_DENY_PATTERNS:
        if re.search(pat, cmd):
            return False, f"deny pattern: {pat!r}"

    # 2) whitelist prefix 검사 (segment 단위로 |, &&, ;, ||)
    segments = re.split(r"\|\||&&|;|\|", cmd)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # 첫 토큰 추출 (env VAR=val cmd 같은 케이스는 단순화로 거부)
        first = seg.split(None, 1)[0] if seg else ""
        # path-prefixed (./foo, /abs/foo) 명령은 거부 — LLM이 만든 임의 바이너리일 수 있음
        if first.startswith("./") or first.startswith("/") or first.startswith("~"):
            return False, f"path-prefixed command 거부: {first!r}"
        base = first.split("=")[-1] if "=" in first else first  # FOO=bar cmd 분리
        if base not in SHELL_ALLOW_PREFIXES:
            return False, f"whitelist에 없는 명령: {base!r}"

    return True, "ok"


@dataclass
class Check:
    name: str
    kind: CheckKind
    command: str = ""
    path: str = ""
    pattern: str = ""
    cwd: str = ""
    timeout_sec: int = 300
    min_bytes: int = 0  # file_exists에서 빈 파일 거부용

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Check:
        """brief 의 verification_checks dict 표현에서 생성. 알 수 없는 키는 무시."""
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    output: str = ""


@dataclass
class VerifyReport:
    passed: bool
    results: list[CheckResult] = field(default_factory=list)

    def failure_summary(self) -> str:
        return "\n".join(f"- {r.name}: {r.detail}" for r in self.results if not r.passed)


class Verifier:
    def __init__(self, workspace: Path):
        self.workspace = workspace

    def run(self, checks: list[Check]) -> VerifyReport:
        results = [self._run_one(c) for c in checks]
        return VerifyReport(passed=all(r.passed for r in results), results=results)

    def _run_one(self, c: Check) -> CheckResult:
        try:
            if c.kind == "shell":
                return self._shell(c)
            if c.kind == "file_exists":
                return self._exists(c)
            if c.kind == "file_contains":
                return self._contains(c)
            return CheckResult(c.name, False, f"알 수 없는 kind: {c.kind}")  # type: ignore[unreachable]
        except Exception as e:  # 어떤 검증 실패도 시스템을 멈추지 않음
            return CheckResult(c.name, False, f"검증 중 예외: {e!r}")

    def _shell(self, c: Check) -> CheckResult:
        if not c.command:
            return CheckResult(c.name, False, "command 누락")
        ok, reason = shell_sanity_check(c.command)
        if not ok:
            return CheckResult(
                c.name, False, f"sanity check 거부: {reason} (command={c.command[:80]!r})"
            )
        ws = self.workspace.resolve()
        if c.cwd:
            cwd = Path(c.cwd)
            if not cwd.is_absolute():
                cwd = ws / cwd
            cwd = cwd.resolve()
            try:
                cwd.relative_to(ws)
            except ValueError:
                return CheckResult(c.name, False, f"cwd workspace 외부: {cwd}")
        else:
            cwd = ws
        try:
            proc = subprocess.run(
                c.command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=c.timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(c.name, False, f"{c.timeout_sec}s 타임아웃")

        output = proc.stdout
        if proc.stderr:
            output += f"\n[stderr]\n{proc.stderr}"
        if proc.returncode == 0:
            return CheckResult(c.name, True, "통과", output)
        return CheckResult(c.name, False, f"exit {proc.returncode}", output)

    def _exists(self, c: Check) -> CheckResult:
        if not c.path:
            return CheckResult(c.name, False, "path 누락")
        from core.path_guard import PathEscape, resolve_within

        try:
            target = resolve_within(c.path, root=self.workspace)
        except PathEscape as e:
            return CheckResult(c.name, False, f"path guard: {e}")
        if not target.exists():
            return CheckResult(c.name, False, f"{c.path} 없음")
        if c.min_bytes > 0 and target.is_file():
            size = target.stat().st_size
            if size < c.min_bytes:
                return CheckResult(c.name, False, f"{c.path} 너무 작음 ({size}B < {c.min_bytes}B)")
        return CheckResult(c.name, True, f"{c.path} 존재")

    def _contains(self, c: Check) -> CheckResult:
        if not (c.path and c.pattern):
            return CheckResult(c.name, False, "path/pattern 누락")
        from core.path_guard import PathEscape, resolve_within

        try:
            target = resolve_within(c.path, root=self.workspace)
        except PathEscape as e:
            return CheckResult(c.name, False, f"path guard: {e}")
        if not target.exists():
            return CheckResult(c.name, False, f"{c.path} 없음")
        if re.search(c.pattern, target.read_text()):
            return CheckResult(c.name, True, "패턴 일치")
        return CheckResult(c.name, False, "패턴 불일치")
