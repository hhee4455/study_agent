"""SessionManager — fresh session 실행기.

세션 간 공유는 파일 시스템 (state, decisions, prompts).
각 세션은 시작 시 context_files를 읽고 시작 → 끝나면 컨텍스트 폐기.

실제 호출은 `claude -p` CLI 가정. anthropic SDK나 Managed Agents로 교체 가능.
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionConfig:
    model: str = "opus"  # claude CLI alias (LLMClient와 통일)
    max_turns: int = 30
    timeout_sec: int = 1800
    # 기본 toolset:
    # - Read/Write/Edit/Bash: 파일/명령 작업
    # - WebSearch/WebFetch: 인터넷 자료 수집 (라이브러리 문서, API 스펙, 시세 등)
    # - Grep/Glob: ws 내부 탐색 (Bash로 가능하지만 명시적으로 빠름)
    allowed_tools: list[str] = field(
        default_factory=lambda: [
            "Read",
            "Write",
            "Edit",
            "Bash",
            "WebSearch",
            "WebFetch",
            "Grep",
            "Glob",
        ]
    )
    system_prompt_path: Path | None = None


@dataclass
class SessionResult:
    success: bool
    output: str = ""
    error: str = ""
    session_id: str = ""  # claude CLI 의 result 이벤트 session_id (재spawn --resume 후속용)
    cost_usd: float = 0.0  # 이 spawn 의 total_cost_usd (멤버별 누적용)


class SessionManager:
    def __init__(self, state_dir: Path, workspace: Path):
        self.state_dir = state_dir
        self.workspace = workspace
        state_dir.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        task_id: str,
        prompt: str,
        config: SessionConfig,
        context_files: list[Path] | None = None,
    ) -> SessionResult:
        full_prompt = self._assemble_prompt(prompt, context_files or [])

        log_dir = self.state_dir / "session_logs" / task_id
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "prompt.txt").write_text(full_prompt)

        return self._execute(full_prompt, config, log_dir)

    @staticmethod
    def _assemble_prompt(prompt: str, context_files: list[Path]) -> str:
        parts = []
        for f in context_files:
            if f.exists():
                parts.append(f'<context path="{f}">\n{f.read_text()}\n</context>')
        parts.append(f"<task>\n{prompt}\n</task>")
        return "\n\n".join(parts)

    def _execute(self, full_prompt: str, config: SessionConfig, log_dir: Path) -> SessionResult:
        cmd = [
            "claude",
            "-p",
            full_prompt,
            "--model",
            config.model,
            "--max-turns",
            str(config.max_turns),
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if config.system_prompt_path and config.system_prompt_path.exists():
            cmd += ["--append-system-prompt", config.system_prompt_path.read_text()]
        if config.allowed_tools:
            cmd += ["--allowedTools", ",".join(config.allowed_tools)]

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            return SessionResult(success=False, error="claude CLI 미설치")

        # Watchdog: kill the process if timeout elapses while we're streaming.
        timed_out = {"v": False}

        def _kill_on_timeout() -> None:
            timed_out["v"] = True
            try:
                proc.kill()
            except OSError:
                pass

        timer = threading.Timer(config.timeout_sec, _kill_on_timeout)
        timer.daemon = True
        timer.start()

        final_text = ""
        result_error = ""
        session_id = ""
        cost_usd = 0.0
        try:
            with (log_dir / "stream.jsonl").open("w", encoding="utf-8") as stream_f:
                assert proc.stdout is not None
                for line in proc.stdout:
                    stream_f.write(line)
                    stream_f.flush()
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        evt = json.loads(s)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("type") == "result":
                        if evt.get("is_error"):
                            result_error = evt.get("result") or evt.get("error") or "session error"
                        else:
                            final_text = evt.get("result", "") or ""
                        session_id = evt.get("session_id", "") or session_id
                        cost_usd = float(evt.get("total_cost_usd", 0.0) or 0.0)
            proc.wait()
        finally:
            timer.cancel()

        if timed_out["v"]:
            return SessionResult(
                success=False,
                error=f"세션 타임아웃 ({config.timeout_sec}s)",
                session_id=session_id,
                cost_usd=cost_usd,
            )

        stderr_content = proc.stderr.read() if proc.stderr else ""
        if stderr_content:
            (log_dir / "stderr.txt").write_text(stderr_content)

        success = proc.returncode == 0 and not result_error
        return SessionResult(
            success=success,
            output=final_text,
            error=result_error or (stderr_content if not success else ""),
            session_id=session_id,
            cost_usd=cost_usd,
        )
