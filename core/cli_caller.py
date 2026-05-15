"""claude / codex CLI 호출 래퍼. lead/main.py 의 LLMClient 가 사용.

build_cli_command / stream_call / make_raw_llm_factory 와 codex 변형을 한 모듈에 모음.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json as _json
import logging
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from core import budget as _budget
from core import llm as _llm
from core.rate_limit import (
    CallOutcome,
    RateLimitedCaller,
    classify_response,
    parse_retry_after,
)

_log = logging.getLogger(__name__)
_call_counter = itertools.count(1)


def _record_call_usage(
    model: str,
    tokens_in: int,
    tokens_out: int,
    usd: float | None,
    source: str,
) -> None:
    """cli stream 결과를 모듈 누적기에 기록 (state/budget.json + events.jsonl).

    ``usd`` 가 None 이면 budget 모듈이 가격표로 추정. 컨텍스트 메타 (agent/goal)
    가 있으면 함께 첨부. 기록 성공 시 LLMClient 에게 알려서 중복 기록을 막는다.
    """
    meta = {**_llm.current_call_meta(), "source": source}
    call_id = f"cli-{next(_call_counter):06d}"
    try:
        _budget.record_usage(
            call_id=call_id,
            model=model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            usd=usd,
            meta=meta,
        )
        _llm.mark_cli_recorded()
    except (OSError, ValueError) as e:
        _log.warning("budget.record_usage 실패 (무시): %s", e)


def build_cli_command(model: str, system: str) -> list[str]:
    """claude CLI 명령어 조립. 내부 LLM 호출 전용 (text-only).

    `--disallowedTools` 로 모든 파일·셸 도구 차단 — lead 내부 호출(plan 분해,
    hire_brief, reply 등)이 cwd 의 파일을 직접 건드리면 안 된다 (예: LLM 이
    plan.md 를 Write 툴로 직접 작성해 cwd=agent_system/ 에 떨어뜨린 사건).
    멤버 spawn 은 별도 경로(`lead/member.py`)에서 자체 allowedTools 를 지정.
    """
    return [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--append-system-prompt",
        system,
        "--disallowedTools",
        "Read,Write,Edit,Bash,Grep,Glob,WebFetch,WebSearch,Task,NotebookEdit,TodoWrite",
    ]


def stream_call(
    cmd: list[str],
    user_input: str,
    log_path: Path | None,
    timeout: int,
    model: str | None = None,
) -> CallOutcome:
    """claude CLI를 stream-json 모드로 돌리고, 한 줄씩 log_path에 적어가며 결과 추출.

    log_path가 None이면 파일 기록 생략 (테스트용).
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if proc.stdin:
        try:
            proc.stdin.write(user_input)
        finally:
            proc.stdin.close()

    timed_out = {"v": False}

    def _kill() -> None:
        timed_out["v"] = True
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()

    final_text = ""
    tokens_in = 0
    tokens_out = 0
    total_cost_usd: float | None = None
    is_error = False
    error_text = ""

    log_f = log_path.open("w", encoding="utf-8") if log_path else None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if log_f:
                log_f.write(line)
                log_f.flush()
            s = line.strip()
            if not s:
                continue
            try:
                evt = _json.loads(s)
            except _json.JSONDecodeError:
                continue
            if evt.get("type") == "result":
                if evt.get("is_error"):
                    is_error = True
                    error_text = evt.get("result") or evt.get("error") or "session error"
                else:
                    final_text = evt.get("result", "") or ""
                usage = evt.get("usage", {}) or {}
                tokens_in = int(usage.get("input_tokens", 0) or 0)
                tokens_out = int(usage.get("output_tokens", 0) or 0)
                cost_raw = evt.get("total_cost_usd")
                if cost_raw is not None:
                    try:
                        total_cost_usd = float(cost_raw)
                    except (TypeError, ValueError):
                        total_cost_usd = None
                if tokens_in == 0 and tokens_out == 0 and total_cost_usd:
                    tokens_out = int(total_cost_usd / 3 * 1_000_000 / 75)
                    tokens_in = int(total_cost_usd * 2 / 3 * 1_000_000 / 15)
        proc.wait()
    finally:
        timer.cancel()
        if log_f:
            log_f.close()

    if timed_out["v"]:
        return CallOutcome(
            kind="other_error",
            text=f"[ERROR: claude CLI 타임아웃 ({timeout}s)]",
        )

    stderr_text = proc.stderr.read() if proc.stderr else ""
    combined = (final_text or "") + "\n" + stderr_text

    kind = classify_response(combined, proc.returncode)
    retry_after = parse_retry_after(combined)
    if kind != "ok":
        return CallOutcome(kind=kind, text=combined[:500], retry_after_sec=retry_after)

    if is_error:
        return CallOutcome(kind="other_error", text=error_text)

    if model:
        _record_call_usage(
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usd=total_cost_usd,
            source="claude_cli",
        )

    return CallOutcome(kind="ok", text=final_text, tokens_in=tokens_in, tokens_out=tokens_out)


def make_raw_llm_factory(
    short_call_timeout: int = 300,
    llm_log_dir: Path | str | None = None,
) -> Callable[[str], Callable[[str, str], tuple[str, int, int]]]:
    """모델 받아 raw_caller 반환하는 factory.

    LLMClient에 주입. 호출 시점에 모델 결정 가능 (티어링).
    `llm_log_dir`이 주어지면 호출마다 NDJSON 스트림을 별도 파일에 기록.
    """
    counter = itertools.count(1)
    log_dir: Path | None = None
    if llm_log_dir is not None:
        log_dir = Path(llm_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

    def make_for_model(model: str) -> Callable[[str, str], tuple[str, int, int]]:
        if not shutil.which("claude"):

            def stub(system: str, user: str) -> tuple[str, int, int]:
                return (f"(claude CLI 미설치 — stub for {model})", 100, 50)

            return stub

        def raw_call(system: str, user: str) -> CallOutcome:
            cmd = build_cli_command(model, system)
            log_path: Path | None = None
            if log_dir is not None:
                ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                idx = next(counter)
                log_path = log_dir / f"{ts}-{idx:05d}-{model}.jsonl"
            return stream_call(cmd, user, log_path, short_call_timeout, model=model)

        return RateLimitedCaller(
            raw_call,
            on_wait=lambda kind, secs: print(
                f"⏸️  rate limit ({kind}, {model}): {secs:.1f}s 대기", flush=True
            ),
        )

    return make_for_model


# ---------- Codex CLI (OpenAI) ----------


def build_codex_command(model: str) -> list[str]:
    """codex exec 명령. system prompt는 stdin 앞부분에 prepend (codex는 system/user 분리 없음).

    --json: JSONL 이벤트 스트림
    --skip-git-repo-check: git repo 밖에서도 동작
    -s read-only: 샌드박스 (파일 수정 금지, LLM 발언만 받음 — debate panel용)
    """
    cmd = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-s",
        "read-only",
        "-",  # stdin에서 프롬프트 읽음
    ]
    if model:
        cmd[2:2] = ["-m", model]  # `exec` 뒤에 -m 삽입
    return cmd


def codex_stream_call(
    cmd: list[str],
    system: str,
    user: str,
    log_path: Path | None,
    timeout: int,
    model: str | None = None,
) -> CallOutcome:
    """codex CLI를 JSONL 모드로 돌리고 결과 추출.

    codex는 system/user 구분 없으므로 system을 user 앞에 prepend.
    출력 이벤트:
      - thread.started / turn.started (무시)
      - item.completed { item: { type: 'agent_message', text: ... } } → 본문 모음
      - turn.completed { usage: { input_tokens, output_tokens, cached_input_tokens } }
    """
    full_input = f"{system}\n\n---\n\n{user}" if system else user

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if proc.stdin:
        try:
            proc.stdin.write(full_input)
        finally:
            proc.stdin.close()

    timed_out = {"v": False}

    def _kill() -> None:
        timed_out["v"] = True
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()

    parts: list[str] = []
    tokens_in = 0
    tokens_out = 0
    is_error = False
    error_text = ""

    log_f = log_path.open("w", encoding="utf-8") if log_path else None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if log_f:
                log_f.write(line)
                log_f.flush()
            s = line.strip()
            if not s:
                continue
            try:
                evt = _json.loads(s)
            except _json.JSONDecodeError:
                continue
            etype = evt.get("type", "")
            if etype == "item.completed":
                item = evt.get("item") or {}
                if item.get("type") == "agent_message":
                    txt = item.get("text", "") or ""
                    if txt:
                        parts.append(txt)
            elif etype == "turn.completed":
                usage = evt.get("usage") or {}
                tokens_in = int(usage.get("input_tokens", 0) or 0)
                tokens_out = int(usage.get("output_tokens", 0) or 0)
            elif etype == "error":
                is_error = True
                error_text = evt.get("message") or "codex error"
        proc.wait()
    finally:
        timer.cancel()
        if log_f:
            log_f.close()

    if timed_out["v"]:
        return CallOutcome(
            kind="other_error",
            text=f"[ERROR: codex CLI 타임아웃 ({timeout}s)]",
        )

    stderr_text = proc.stderr.read() if proc.stderr else ""
    final_text = "\n\n".join(parts).strip()
    combined = final_text + "\n" + stderr_text

    kind = classify_response(combined, proc.returncode)
    retry_after = parse_retry_after(combined)
    if kind != "ok":
        return CallOutcome(kind=kind, text=combined[:500], retry_after_sec=retry_after)
    if is_error:
        return CallOutcome(kind="other_error", text=error_text)
    if not final_text:
        return CallOutcome(kind="other_error", text="codex: empty response")

    if model:
        # codex CLI 는 USD 를 직접 알려주지 않음 → 가격표 추정에 맡김 (usd=None).
        _record_call_usage(
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usd=None,
            source="codex_cli",
        )

    return CallOutcome(
        kind="ok",
        text=final_text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def make_codex_raw_factory(
    short_call_timeout: int = 600,
    llm_log_dir: Path | str | None = None,
) -> Callable[[str], Callable[[str, str], tuple[str, int, int]]]:
    """codex 백엔드 raw_caller factory. claude factory와 같은 시그니처.

    timeout은 600s 기본 (codex는 reasoning step 때문에 더 오래 걸릴 수 있음).
    """
    counter = itertools.count(1)
    log_dir: Path | None = None
    if llm_log_dir is not None:
        log_dir = Path(llm_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

    def make_for_model(model: str) -> Callable[[str, str], tuple[str, int, int]]:
        if not shutil.which("codex"):

            def stub(system: str, user: str) -> tuple[str, int, int]:
                return (f"(codex CLI 미설치 — stub for {model})", 100, 50)

            return stub

        def raw_call(system: str, user: str) -> CallOutcome:
            cmd = build_codex_command(model)
            log_path: Path | None = None
            if log_dir is not None:
                ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                idx = next(counter)
                log_path = log_dir / f"{ts}-{idx:05d}-codex-{model or 'default'}.jsonl"
            return codex_stream_call(
                cmd,
                system,
                user,
                log_path,
                short_call_timeout,
                model=model,
            )

        return RateLimitedCaller(
            raw_call,
            on_wait=lambda kind, secs: print(
                f"⏸️  rate limit (codex {kind}, {model}): {secs:.1f}s 대기", flush=True
            ),
        )

    return make_for_model
