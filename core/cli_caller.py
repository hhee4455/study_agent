"""claude / codex CLI 호출 래퍼. lead/main.py 의 LLMClient 가 사용.

build_cli_command / stream_call / make_raw_llm_factory 와 codex 변형을 한 모듈에 모음.
"""
from __future__ import annotations

import datetime as _dt
import itertools
import json as _json
import shutil
import subprocess
import threading
from pathlib import Path

from core.rate_limit import (
    CallOutcome, RateLimitedCaller, classify_response, parse_retry_after,
)


def build_cli_command(model: str, system: str) -> list[str]:
    """claude CLI 명령어 조립. 테스트 가능하게 분리."""
    return [
        "claude", "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--model", model,
        "--append-system-prompt", system,
    ]


def stream_call(
    cmd: list[str],
    user_input: str,
    log_path,
    timeout: int,
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

    def _kill():
        timed_out["v"] = True
        try:
            proc.kill()
        except Exception:
            pass

    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()

    final_text = ""
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
            if evt.get("type") == "result":
                if evt.get("is_error"):
                    is_error = True
                    error_text = evt.get("result") or evt.get("error") or "session error"
                else:
                    final_text = evt.get("result", "") or ""
                usage = evt.get("usage", {}) or {}
                tokens_in = int(usage.get("input_tokens", 0) or 0)
                tokens_out = int(usage.get("output_tokens", 0) or 0)
                if tokens_in == 0 and tokens_out == 0:
                    cost = float(evt.get("total_cost_usd", 0.0) or 0.0)
                    if cost > 0:
                        tokens_out = int(cost / 3 * 1_000_000 / 75)
                        tokens_in = int(cost * 2 / 3 * 1_000_000 / 15)
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

    return CallOutcome(kind="ok", text=final_text, tokens_in=tokens_in, tokens_out=tokens_out)


def make_raw_llm_factory(short_call_timeout: int = 300, llm_log_dir=None):
    """모델 받아 raw_caller 반환하는 factory.

    LLMClient에 주입. 호출 시점에 모델 결정 가능 (티어링).
    `llm_log_dir`이 주어지면 호출마다 NDJSON 스트림을 별도 파일에 기록.
    """
    counter = itertools.count(1)
    if llm_log_dir is not None:
        llm_log_dir = Path(llm_log_dir)
        llm_log_dir.mkdir(parents=True, exist_ok=True)

    def make_for_model(model: str):
        if not shutil.which("claude"):
            def stub(system: str, user: str) -> tuple[str, int, int]:
                return (f"(claude CLI 미설치 — stub for {model})", 100, 50)
            return stub

        def raw_call(system: str, user: str) -> CallOutcome:
            cmd = build_cli_command(model, system)
            log_path = None
            if llm_log_dir is not None:
                ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                idx = next(counter)
                log_path = llm_log_dir / f"{ts}-{idx:05d}-{model}.jsonl"
            return stream_call(cmd, user, log_path, short_call_timeout)

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
        "codex", "exec",
        "--json",
        "--skip-git-repo-check",
        "-s", "read-only",
        "-",  # stdin에서 프롬프트 읽음
    ]
    if model:
        cmd[2:2] = ["-m", model]  # `exec` 뒤에 -m 삽입
    return cmd


def codex_stream_call(
    cmd: list[str],
    system: str,
    user: str,
    log_path,
    timeout: int,
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

    def _kill():
        timed_out["v"] = True
        try:
            proc.kill()
        except Exception:
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

    return CallOutcome(
        kind="ok", text=final_text, tokens_in=tokens_in, tokens_out=tokens_out,
    )


def make_codex_raw_factory(short_call_timeout: int = 600, llm_log_dir=None):
    """codex 백엔드 raw_caller factory. claude factory와 같은 시그니처.

    timeout은 600s 기본 (codex는 reasoning step 때문에 더 오래 걸릴 수 있음).
    """
    counter = itertools.count(1)
    if llm_log_dir is not None:
        llm_log_dir = Path(llm_log_dir)
        llm_log_dir.mkdir(parents=True, exist_ok=True)

    def make_for_model(model: str):
        if not shutil.which("codex"):
            def stub(system: str, user: str) -> tuple[str, int, int]:
                return (f"(codex CLI 미설치 — stub for {model})", 100, 50)
            return stub

        def raw_call(system: str, user: str) -> CallOutcome:
            cmd = build_codex_command(model)
            log_path = None
            if llm_log_dir is not None:
                ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                idx = next(counter)
                log_path = llm_log_dir / f"{ts}-{idx:05d}-codex-{model or 'default'}.jsonl"
            return codex_stream_call(cmd, system, user, log_path, short_call_timeout)

        return RateLimitedCaller(
            raw_call,
            on_wait=lambda kind, secs: print(
                f"⏸️  rate limit (codex {kind}, {model}): {secs:.1f}s 대기", flush=True
            ),
        )

    return make_for_model
