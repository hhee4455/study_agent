"""path_guard CLI hook 단위 테스트.

라이브러리 함수 (check_tool_input, is_path_under) 와 모듈 CLI entrypoint
양쪽을 커버. 모듈 import 는 importlib 로 직접 로드해 패키지 __init__.py 의
부작용을 피한다 (다른 멤버 시드와 동거하는 sandbox 환경 대응).
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "core" / "path_guard.py"


def _load_module():
    """path_guard 모듈을 spec_from_file_location 으로 직접 로드."""
    spec = importlib.util.spec_from_file_location("path_guard_under_test", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cli(payload: dict | str, cwd_arg: Path, *, run_cwd: Path | None = None):
    """path_guard.py 를 스크립트로 직접 invoke. (subprocess + stdin)"""
    stdin_text = json.dumps(payload) if isinstance(payload, dict) else payload
    proc = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--cwd", str(cwd_arg)],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(run_cwd or Path(tempfile.gettempdir())),
        timeout=30,
    )
    return proc


# ---------- 라이브러리 함수 직접 호출 ----------


def test_is_path_under_basic():
    mod = _load_module()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "sub").mkdir()
        assert mod.is_path_under(root / "sub" / "x.txt", root) is True
        assert mod.is_path_under(root, root) is True
        assert mod.is_path_under(Path("/etc/passwd"), root) is False


def test_check_tool_input_inside_absolute_allowed():
    """(a) cwd 안 절대경로 → exit 0, 메시지 없음."""
    mod = _load_module()
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d).resolve()
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(cwd / "out.txt")}}
        code, msg = mod.check_tool_input(payload, cwd)
        assert code == 0 and msg == ""


def test_check_tool_input_outside_absolute_rejected():
    """(b) cwd 밖 절대경로 → exit 2."""
    mod = _load_module()
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d).resolve()
        payload = {"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}}
        code, msg = mod.check_tool_input(payload, cwd)
        assert code == 2
        assert "cwd 밖" in msg and "/etc/passwd" in msg


def test_check_tool_input_relative_resolved_against_cwd():
    """(c) 상대경로 → cwd 기준 resolve 후 검사."""
    mod = _load_module()
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d).resolve()
        payload = {"tool_name": "Edit", "tool_input": {"file_path": "sub/file.txt"}}
        code, _ = mod.check_tool_input(payload, cwd)
        assert code == 0


def test_check_tool_input_dotdot_traversal_rejected():
    """(d) `../../etc/passwd` 같은 상위 이동 거부."""
    mod = _load_module()
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d).resolve() / "nested" / "deeper"
        cwd.mkdir(parents=True)
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "../../../../../../etc/passwd"},
        }
        code, msg = mod.check_tool_input(payload, cwd)
        assert code == 2
        assert "cwd 밖" in msg


def test_check_tool_input_symlink_bypass_rejected():
    """(e) symlink → /etc/... 도 resolve 후 검사로 차단."""
    mod = _load_module()
    if os.name == "nt":  # pragma: no cover — symlink permission diff on Windows
        return
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d).resolve()
        link = cwd / "escape_link"
        try:
            link.symlink_to("/etc")
        except (OSError, NotImplementedError):
            return  # symlink 미지원 환경은 skip
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(link / "passwd")},
        }
        code, msg = mod.check_tool_input(payload, cwd)
        assert code == 2, msg


def test_check_tool_input_missing_file_path_passthrough():
    """(f) file_path 없는 tool_input 은 passthrough."""
    mod = _load_module()
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d).resolve()
        # tool_input 자체가 빈 dict
        code, msg = mod.check_tool_input({"tool_name": "Write", "tool_input": {}}, cwd)
        assert code == 0 and msg == ""
        # tool_input 누락
        code2, _ = mod.check_tool_input({"tool_name": "Write"}, cwd)
        assert code2 == 0


def test_check_tool_input_non_fileop_tool_passthrough():
    """(g) Write/Edit/MultiEdit 외 tool 은 항상 passthrough — 위험 경로여도."""
    mod = _load_module()
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d).resolve()
        for tool in ("Read", "Bash", "Grep", "WebFetch", "Glob"):
            payload = {"tool_name": tool, "tool_input": {"file_path": "/etc/passwd"}}
            code, msg = mod.check_tool_input(payload, cwd)
            assert code == 0 and msg == "", f"{tool} 가 차단됨 (예상: passthrough)"


def test_check_tool_input_multi_edit_guarded():
    """MultiEdit 도 동일하게 guarded — 화이트리스트 회귀 방어."""
    mod = _load_module()
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d).resolve()
        payload = {
            "tool_name": "MultiEdit",
            "tool_input": {"file_path": "/etc/hosts"},
        }
        code, _ = mod.check_tool_input(payload, cwd)
        assert code == 2


def test_check_tool_input_alternate_path_keys():
    """`path` 같은 변종 키도 인식."""
    mod = _load_module()
    with tempfile.TemporaryDirectory() as d:
        cwd = Path(d).resolve()
        payload = {"tool_name": "Edit", "tool_input": {"path": "/etc/passwd"}}
        code, _ = mod.check_tool_input(payload, cwd)
        assert code == 2


# ---------- subprocess (모듈 CLI) 호출 ----------


def test_cli_outside_path_rejected():
    """subprocess: cwd 밖 → exit 2 + stderr 사유."""
    with tempfile.TemporaryDirectory() as d:
        proc = _run_cli(
            {"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd"}},
            Path(d),
        )
        assert proc.returncode == 2, f"stderr={proc.stderr!r}"
        assert "path_guard" in proc.stderr


def test_cli_inside_path_allowed():
    """subprocess: cwd 안 → exit 0."""
    with tempfile.TemporaryDirectory() as d:
        proc = _run_cli(
            {"tool_name": "Write", "tool_input": {"file_path": str(Path(d) / "foo.txt")}},
            Path(d),
        )
        assert proc.returncode == 0, f"stderr={proc.stderr!r}"


def test_cli_non_fileop_passthrough():
    """subprocess: 매칭 안 되는 tool 은 exit 0."""
    with tempfile.TemporaryDirectory() as d:
        proc = _run_cli(
            {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}},
            Path(d),
        )
        assert proc.returncode == 0


def test_cli_invalid_json_graceful_passthrough():
    """(h) 잘못된 JSON 입력 → 크래시 없이 passthrough (exit 0) + stderr 안내."""
    with tempfile.TemporaryDirectory() as d:
        proc = _run_cli("this is not json {{{", Path(d))
        assert proc.returncode == 0, f"stderr={proc.stderr!r}"
        assert "JSON" in proc.stderr or "json" in proc.stderr


def test_cli_empty_stdin_passthrough():
    """빈 stdin 도 크래시 없이 passthrough."""
    with tempfile.TemporaryDirectory() as d:
        proc = _run_cli("", Path(d))
        assert proc.returncode == 0


def test_cli_non_dict_payload_passthrough():
    """JSON 배열/원시값 같은 dict 아닌 payload 도 passthrough."""
    with tempfile.TemporaryDirectory() as d:
        proc = _run_cli("[1,2,3]", Path(d))
        assert proc.returncode == 0
