"""Path guard — P5 토론(2026-05-13 p5-isolation-policy.md) 결정 적용.

위협 모델: LLM이 생성한 verification_checks/I/O가 ws/{agent_id}/ 밖을 가리킬 수 있음.
- 절대경로 (`/etc/passwd`)
- 상위 이동 (`../../`)
- 홈 확장 (`~/.ssh/id_rsa`)
- symlink 우회 (link → /etc/...)

전략: deny-list 열거가 아니라 **경계 정규화** (Agent-C의 R2 논점).
  resolve_safe(p, agent_id, ws_root)가 Path.resolve() 후 ws_root/{agent_id} prefix만 허용.
  symlink 발견 시 즉시 거부 (resolve 전·후 비교).

보너스 deny-list: 명시적으로 위험한 호스트 경로 (~/.ssh, ~/.aws, .env).

CLI entrypoint (`python -m agent_system.core.path_guard --cwd <abs>`):
  claude CLI 의 PreToolUse hook 으로 동작. stdin 에 `{"tool_name":..., "tool_input":{...}}`
  JSON 을 받아 Write/Edit/MultiEdit 의 file_path 가 cwd 하위인지 검증.
  - 허용 → exit 0
  - 거부 → exit 2 + stderr 사유 (claude CLI 가 이 사유를 LLM 에 다음 턴 컨텍스트로 주입)
  - 매칭 안 되는 tool / file_path 누락 → passthrough (exit 0)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Hook 이 검사할 tool 화이트리스트. 그 외는 passthrough.
GUARDED_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "MultiEdit"})

# tool_input 에서 파일 경로를 찾을 후보 키. 우선순위 순.
FILE_PATH_KEYS: tuple[str, ...] = ("file_path", "path", "filePath", "filepath")


HOST_DENYLIST_FRAGMENTS = (
    ".ssh",
    ".aws",
    ".kube",
    ".gnupg",
    ".npmrc",
    ".pypirc",
    ".docker",
    "credentials",
)
HOST_DENYLIST_SUFFIXES = (".env", ".pem", ".key", ".p12", ".pfx")


class PathEscape(Exception):
    """경로가 허용 경계(workspace prefix) 밖이거나 symlink로 우회 시도."""


def resolve_within(target: str | Path, *, root: Path) -> Path:
    """target을 root 안 경로로 resolve. root 밖이거나 symlink 발견 시 PathEscape.

    resolve_safe는 ws/{agent_id} 컨텍스트용, 이건 verifier 자체의 self.workspace처럼
    root가 이미 명시적으로 주어진 경우용. agent_id 분리 없음.
    """
    if not target:
        raise PathEscape("빈 target")

    p = Path(target)
    if p.is_absolute() or str(target).startswith("~"):
        raise PathEscape(f"절대/홈 경로 거부: {target!r}")

    expected_root = root.resolve()
    candidate = (root / p).resolve()

    cur = candidate
    while cur != cur.parent:
        if cur.is_symlink():
            raise PathEscape(f"symlink 거부: {cur}")
        if cur == expected_root:
            break
        cur = cur.parent
    else:
        raise PathEscape(f"root 밖 경로: {candidate} (expected under {expected_root})")

    return candidate


def resolve_safe(target: str | Path, *, agent_id: str, ws_root: Path) -> Path:
    """target을 resolve해서 ws_root/{agent_id}/ prefix만 허용. 위반 시 PathEscape.

    - 절대경로 / 상위 이동 / `~` 확장 / symlink 우회 모두 차단.
    - ws_root 자체도 resolve해서 정규화 비교.
    """
    if not target:
        raise PathEscape("빈 target")

    p = Path(target)
    if p.is_absolute() or str(target).startswith("~"):
        raise PathEscape(f"절대/홈 경로 거부: {target!r}")

    expected_root = (ws_root / agent_id).resolve()
    candidate = (ws_root / agent_id / p).resolve()

    # symlink 우회 검사 — 어떤 부모 경로든 symlink면 거부
    cur = candidate
    while cur != cur.parent:
        if cur.is_symlink():
            raise PathEscape(f"symlink 거부: {cur}")
        if cur == expected_root:
            break
        cur = cur.parent
    else:
        # expected_root 도달 못 함 = prefix 밖
        raise PathEscape(f"workspace 밖 경로: {candidate} (expected under {expected_root})")

    return candidate


def is_within_cwd(path: Path, cwd: Path) -> bool:
    """`path` 의 resolve 결과가 `cwd` subtree 안에 있는지 확인 (symlink 거부)."""
    try:
        cwd_resolved = cwd.resolve()
    except OSError:
        return False
    target = path if path.is_absolute() else (cwd / path)
    try:
        if target.is_symlink():
            return False
    except OSError:
        return False
    try:
        resolved = target.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(cwd_resolved)
    except ValueError:
        return False
    return True


def is_path_under(target: Path, root: Path) -> bool:
    """target.resolve() 가 root.resolve() 의 하위(또는 동일) 경로인지 검사.

    Python 3.9+ 의 `Path.is_relative_to` 를 사용하되, 어떤 Python 빌드에서도
    안전하도록 부모 체인 walk 폴백을 둔다. symlink 우회는 `resolve()` 의
    `strict=False` 시멘틱에 의존: 존재하지 않는 마지막 컴포넌트도 정규화되며,
    실재하는 중간 symlink 는 해석된다.
    """
    target_r = target.resolve()
    root_r = root.resolve()
    is_relative_to = getattr(Path, "is_relative_to", None)
    if callable(is_relative_to):
        try:
            return target_r.is_relative_to(root_r)
        except (TypeError, ValueError):
            pass
    cur = target_r
    while True:
        if cur == root_r:
            return True
        parent = cur.parent
        if parent == cur:
            return False
        cur = parent


def _extract_file_path(tool_input: Any) -> str | None:
    """tool_input dict 에서 file_path 후보 키를 우선순위대로 조회. 없으면 None."""
    if not isinstance(tool_input, dict):
        return None
    for key in FILE_PATH_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def check_tool_input(payload: dict[str, Any], cwd: Path) -> tuple[int, str]:
    """Hook payload 를 검사해 (exit_code, stderr_message) 반환.

    - tool_name 이 GUARDED_TOOLS 외 → (0, "")  (passthrough)
    - file_path 없음 → (0, "")  (passthrough)
    - file_path 가 cwd 하위 → (0, "")
    - 그 외 → (2, 사유)
    """
    tool_name = payload.get("tool_name")
    if tool_name not in GUARDED_TOOLS:
        return 0, ""

    tool_input = payload.get("tool_input")
    file_path = _extract_file_path(tool_input)
    if file_path is None:
        return 0, ""

    target = Path(file_path)
    if not target.is_absolute():
        target = cwd / target

    try:
        inside = is_path_under(target, cwd)
    except OSError as exc:
        return 2, f"path_guard: resolve 실패 ({file_path!r}): {exc}"

    if inside:
        return 0, ""
    return 2, (
        f"path_guard: cwd 밖 경로 거부 — tool={tool_name} file_path={file_path!r} cwd={str(cwd)!r}"
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent_system.core.path_guard",
        description="claude CLI PreToolUse hook: cwd 밖 Write/Edit 차단.",
    )
    parser.add_argument(
        "--cwd",
        required=True,
        help="허용 경계 (멤버 workspace 절대경로).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. stdin JSON → check_tool_input → exit code."""
    args = _parse_args(list(sys.argv[1:]) if argv is None else argv)
    cwd = Path(args.cwd)

    raw = sys.stdin.read()
    if not raw.strip():
        return 0

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"path_guard: stdin JSON 파싱 실패 ({exc.msg}) — passthrough\n")
        return 0

    if not isinstance(payload, dict):
        sys.stderr.write("path_guard: stdin payload 가 dict 아님 — passthrough\n")
        return 0

    code, msg = check_tool_input(payload, cwd)
    if code != 0 and msg:
        sys.stderr.write(msg + "\n")
    return code


def host_path_blocked(text: str) -> tuple[bool, str]:
    """문자열(보통 shell command)에서 호스트 민감 경로 fragment 감지.

    호출자: core/verifier.py가 shell command sanity check 후 추가로 검사.
    """
    low = text.lower()
    for frag in HOST_DENYLIST_FRAGMENTS:
        if frag in low:
            return True, f"호스트 민감 경로 fragment: {frag!r}"
    for suf in HOST_DENYLIST_SUFFIXES:
        if low.endswith(suf) or f"{suf} " in low or f"{suf}'" in low or f'{suf}"' in low:
            return True, f"호스트 민감 파일 suffix: {suf!r}"
    return False, ""


if __name__ == "__main__":
    sys.exit(main())
