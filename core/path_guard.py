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
"""
from __future__ import annotations

from pathlib import Path


HOST_DENYLIST_FRAGMENTS = (
    ".ssh", ".aws", ".kube", ".gnupg", ".npmrc",
    ".pypirc", ".docker", "credentials",
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
        raise PathEscape(
            f"root 밖 경로: {candidate} (expected under {expected_root})"
        )

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
