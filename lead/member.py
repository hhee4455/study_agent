"""MemberSpawner — 팀원 채용 + 세션 spawn + 결과 파싱.

핵심 책임:
  - brief.md, mailbox.md, delivery.md, status 파일 생성/관리
  - ws/{agent_id}/ 디렉토리 보장
  - SessionManager(state_dir, ws/{agent_id})로 격리된 서브프로세스 spawn
  - 멤버용 driver prompt 작성 (brief.md 읽고 미션 수행 + 메일박스 규칙)
  - 세션 종료 후 [STATUS:DONE|WAITING|FAILED] 토큰 감지 + delivery.md 갱신

재spawn(resume): 같은 ws/{agent_id}/ 유지, 새 task_id ({agent_id}-r{n})로 호출.
mailbox.md가 멤버의 메모리 역할 — claude -c 같은 세션 재개 불필요.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.deliverable import missing_or_outside
from core.session_manager import SessionConfig, SessionManager, SessionResult
from lead.mailbox import (
    Message,
    append_message,
    detect_terminal_status,
    parse_messages,
)
from lead.prompts import build_refine_write_guard, render

logger = logging.getLogger("lead")


# ---------------------------------------------------------------------------
# PID file lifecycle (resilience: lead 재시작 시 live child 식별용)
#
# spawn 시 `state/agents/<id>/pid` 에 자식 process PID 기록 → exit 시 제거.
# `try_reattach` 는 PID 파일이 살아있는지 확인하여 lead 재시작 시 in-flight 자식에
# 재연결할지 / 재spawn 할지 결정한다. liveness 는 `os.kill(pid, 0)` 신호 0 기반.
#
# 주의: write_pid_file() 호출 시 반드시 자식 PID 를 명시 전달해야 한다 — pid 인자를
# 생략하면 호출자(lead) 의 PID 가 기록되어 try_reattach 가 항상 True 를 반환하는
# 오염이 발생한다.
# ---------------------------------------------------------------------------


def pid_file_path(agent_id: str, agents_root: Path) -> Path:
    """PID 파일 절대 경로 반환. (디렉토리 존재 보장은 호출자 책임)."""
    return agents_root / agent_id / "pid"


def write_pid_file(agent_id: str, agents_root: Path, pid: int | None = None) -> Path:
    """`state/agents/<agent_id>/pid` 에 PID(기본=현재 프로세스) 기록 후 경로 반환."""
    path = pid_file_path(agent_id, agents_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    real_pid = os.getpid() if pid is None else int(pid)
    path.write_text(str(real_pid), encoding="utf-8")
    return path


def remove_pid_file(agent_id: str, agents_root: Path) -> None:
    """PID 파일 삭제. 없어도 무시 — 멤버 정상 종료 후 정리용."""
    path = pid_file_path(agent_id, agents_root)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def read_pid_file(agent_id: str, agents_root: Path) -> int | None:
    """PID 파일에서 정수 PID 읽기. 없거나 손상되면 None."""
    path = pid_file_path(agent_id, agents_root)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def is_pid_alive(pid: int) -> bool:
    """`os.kill(pid, 0)` 로 liveness 확인.

    - 성공 → 살아있음 (현재 사용자 소유)
    - ProcessLookupError → 죽음 (PID 미존재)
    - PermissionError → 살아있지만 다른 사용자 소유 (재spawn 안전을 위해 살아있음으로 취급)
    - 그 외 OSError → 알 수 없음 → 죽음으로 간주 (재spawn 쪽이 안전)
    """
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def try_reattach(member_id: str, agents_root: Path) -> bool:
    """PID 파일에 기록된 process 가 살아있으면 True (in-flight 재연결 가능).

    파일 없음 / PID 죽음 / 손상 → False. 호출자는 False 일 때 재spawn 정책 적용.
    """
    pid = read_pid_file(member_id, agents_root)
    if pid is None:
        return False
    return is_pid_alive(pid)


# evaluator FAIL 사이클 절약을 위해 spawn 안에서 한 번 더 자가-재시도. 그 이후의
# 재시도는 team_lead 의 일반 resume 메커니즘이 흡수 (무한 루프 방지).
PREDELIVERY_SANITY_RETRIES = 1


# PreToolUse hook: cwd 밖 Write/Edit/MultiEdit 를 OS 레벨에서 차단.
# matcher 는 claude CLI 의 hook spec 그대로. command 는 멤버 ws 절대경로를 박아
# 각 멤버가 자기 ws 만 보이도록 한다 (다른 멤버 ws/state 디렉토리 침범 방지).
_HOOK_MATCHER = "Write|Edit|MultiEdit"
_HOOK_COMMAND_TEMPLATE = "python -m agent_system.core.path_guard --cwd {ws_abs}"


def build_member_settings(ws_abs: Path) -> dict[str, Any]:
    """멤버 workspace 절대경로를 받아 .claude/settings.json 내용(dict)을 반환.

    claude CLI hook spec: settings.json 의 `hooks.PreToolUse[*].hooks[*].command`
    가 매칭 시 실행되며, exit code 2 + stderr 사유 → 그 turn 의 tool call 거부.
    """
    return {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": _HOOK_MATCHER,
                    "hooks": [
                        {
                            "type": "command",
                            "command": _HOOK_COMMAND_TEMPLATE.format(ws_abs=str(ws_abs)),
                        }
                    ],
                }
            ]
        }
    }


def write_member_settings(ws: Path) -> Path:
    """`<ws>/.claude/settings.json` 을 생성/갱신. 반환: 작성된 파일 경로.

    이미 존재하면 hooks.PreToolUse 슬롯만 덮어쓰고 나머지 키는 보존
    (다른 멤버/사용자 설정과 공존). 멤버 spawn 직전에 호출된다.
    """
    settings_dir = ws / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.json"

    payload = build_member_settings(ws.resolve())

    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                hooks = existing.setdefault("hooks", {})
                if isinstance(hooks, dict):
                    hooks["PreToolUse"] = payload["hooks"]["PreToolUse"]
                    payload = existing
        except (json.JSONDecodeError, OSError):
            # 손상된 settings.json 은 새 payload 로 덮어쓴다 — hook 보장이 우선.
            pass

    settings_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return settings_path


@dataclass
class SpawnResult:
    agent_id: str
    status: str  # DONE | WAITING | FAILED | UNKNOWN
    raw_output: str
    last_question: Message | None = None
    delivery_text: str = ""
    error: str = ""
    session_id: str = ""
    cost_usd: float = 0.0


@dataclass
class HireBrief:
    """팀장이 채용할 때 작성하는 정보. brief.md로 직렬화됨."""

    agent_id: str
    goal_id: str
    mission: str
    deliverables: list[str]
    verification_checks: list[dict[str, Any]]  # Verifier 형식
    system_prompt: str  # 팀장이 LLM으로 작성한 멤버 persona
    seed_files: list[str] | None = None  # 옵션: main에서 복사해 줄 파일들
    allowed_tools: list[str] | None = None
    # P4 결정(2026-05-13): per-hire Evaluator 토글.
    # 팀장이 채용 LLM 응답 JSON에서 verify=true를 받으면 그 멤버 산출물은
    # AdversarialVerifier critique-refine 1 cycle 거침. 기본 false.
    # 켜야 할 조건은 hire_brief.md 프롬프트에서 lead에게 지시.
    verify: bool = False
    # 채용 라벨: 'new' | 'extend' | 'refine' | 'remove' | None.
    # 'refine' 이면 system_prompt 말미에 write-guard closure 가 합성된다.
    kind: str | None = None


# driver prompt는 `lead/prompts/driver.md`에서 로드됨.


class MemberSpawner:
    def __init__(
        self,
        agents_root: Path,
        ws_root: Path,
        state_dir: Path,
        default_model: str = "opus",
    ):
        """
        agents_root: <state_dir>/agents/
        ws_root: ws/ (각 멤버는 ws/{agent_id}/)
        state_dir: <state_dir>/ (session_logs 저장용)
        """
        self.agents_root = agents_root
        self.ws_root = ws_root
        self.state_dir = state_dir
        self.default_model = default_model
        agents_root.mkdir(parents=True, exist_ok=True)
        ws_root.mkdir(parents=True, exist_ok=True)

    def write_brief(self, brief: HireBrief) -> Path:
        """채용 시 brief.md 작성 + 빈 mailbox/delivery/status 파일 보장."""
        agent_dir = self.agents_root / brief.agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        ws = self.ws_root / brief.agent_id
        ws.mkdir(parents=True, exist_ok=True)

        # seed files 복사 (있다면). ws_root = workspace/ws/members 이므로 main 은 부모의 형제.
        # 원본 사본을 ws/{id}/.seed/ 에도 저장 — 머지 시 WorkspaceMerger 가 "멤버가 이 파일을
        # 실제로 변경했는지" 판단하는 reference. 멤버가 안 건드린 파일은 머지 시 무시되어
        # 다른 멤버의 동시 변경으로 인한 부수적 충돌이 자동으로 해소된다.
        if brief.seed_files:
            main_root = self.ws_root.parent / "main"
            seed_root = ws / ".seed"
            for rel in brief.seed_files:
                src = main_root / rel
                if src.exists() and src.is_file():
                    dst = ws / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_bytes(src.read_bytes())
                    seed_dst = seed_root / rel
                    seed_dst.parent.mkdir(parents=True, exist_ok=True)
                    seed_dst.write_bytes(src.read_bytes())

        brief_path = agent_dir / "brief.md"
        checks_md = "\n".join(f"  - {c}" for c in brief.verification_checks) or "  - (없음)"
        deliverables_md = "\n".join(f"  - {d}" for d in brief.deliverables) or "  - (없음)"
        body = f"""# 채용 브리프 — {brief.agent_id}

- agent_id: {brief.agent_id}
- goal_id: {brief.goal_id}
- workspace: {ws}
- mailbox: {agent_dir / "mailbox.md"}
- delivery: {agent_dir / "delivery.md"}

## 미션
{brief.mission}

## Deliverables
{deliverables_md}

## 검증 기준 (verifier가 후속 실행)
{checks_md}

## 너의 페르소나 / 행동 방침 (system prompt)
{brief.system_prompt}
"""
        brief_path.write_text(body, encoding="utf-8")

        mailbox = agent_dir / "mailbox.md"
        if not mailbox.exists():
            mailbox.write_text("", encoding="utf-8")
        delivery = agent_dir / "delivery.md"
        if not delivery.exists():
            delivery.write_text("", encoding="utf-8")
        status = agent_dir / "status"
        status.write_text("HIRED", encoding="utf-8")

        return brief_path

    def spawn(
        self,
        brief: HireBrief,
        *,
        resume_count: int = 0,
        timeout_sec: int = 1800,
        max_turns: int = 120,
    ) -> SpawnResult:
        """SessionManager로 멤버 세션 1회 실행.

        DONE 시 predelivery sanity check 후 필요하면 1회 자가-재spawn.
        """
        agent_id = brief.agent_id
        ws = self.ws_root / agent_id
        agent_dir = self.agents_root / agent_id
        brief_path = agent_dir / "brief.md"

        if not brief_path.exists():
            self.write_brief(brief)

        # PreToolUse hook 주입: cwd 밖 Write/Edit/MultiEdit 를 OS 레벨에서 차단.
        # LLM 호출 전 단계에서 reject 되므로 evaluator FAIL 사이클 사전 방지.
        write_member_settings(ws)

        attempt = 0
        current_resume = resume_count
        while True:
            result = self._run_session(
                brief,
                resume_count=current_resume,
                timeout_sec=timeout_sec,
                max_turns=max_turns,
            )

            if result.status != "DONE":
                return result

            issues = _predelivery_sanity_check(brief.deliverables or [], ws)
            if not issues:
                return result

            logger.info(
                "predelivery_sanity status=fail reasons=%s",
                [reason for _, reason in issues],
            )

            if attempt >= PREDELIVERY_SANITY_RETRIES:
                # 한도 초과 — evaluator 호출 막기 위해 FAILED 로 격하.
                # 분류 사유만 노출 (절대경로 마스킹).
                summary = ", ".join(reason for _, reason in issues)
                return SpawnResult(
                    agent_id=result.agent_id,
                    status="FAILED",
                    raw_output=result.raw_output,
                    last_question=result.last_question,
                    delivery_text=result.delivery_text,
                    error=f"predelivery_sanity_persistent_failure: {summary}",
                    session_id=result.session_id,
                    cost_usd=result.cost_usd,
                )

            append_message(
                agent_dir / "mailbox.md",
                from_="lead",
                to=agent_id,
                kind="instruction",
                body=_format_sanity_feedback(issues),
            )
            attempt += 1
            current_resume += 1

    def _run_session(
        self,
        brief: HireBrief,
        *,
        resume_count: int,
        timeout_sec: int,
        max_turns: int,
    ) -> SpawnResult:
        """SessionManager 1회 호출 + status 파싱. 부수효과: system_prompt.md 작성."""
        agent_id = brief.agent_id
        agent_dir = self.agents_root / agent_id
        ws = self.ws_root / agent_id
        brief_path = agent_dir / "brief.md"
        mailbox_path = agent_dir / "mailbox.md"
        delivery_path = agent_dir / "delivery.md"

        # 각 spawn마다 cwd=ws/{agent_id}로 격리된 새 SessionManager 인스턴스
        sm = SessionManager(self.state_dir, ws)

        task_id = agent_id if resume_count == 0 else f"{agent_id}-r{resume_count}"

        # 멤버용 system prompt를 별도 파일로 저장
        # (SessionConfig.system_prompt_path는 텍스트 파일을 받음).
        # brief.kind == 'refine' 이면 write-guard closure 가 자동 append.
        sp_path = agent_dir / "system_prompt.md"
        sp_path.write_text(self._compose_system_prompt(brief), encoding="utf-8")

        # allowed_tools 명시 안 됐으면 SessionConfig 기본값 사용 (web+grep+glob 포함).
        if brief.allowed_tools:
            config = SessionConfig(
                model=self.default_model,
                max_turns=max_turns,
                timeout_sec=timeout_sec,
                system_prompt_path=sp_path,
                allowed_tools=brief.allowed_tools,
            )
        else:
            config = SessionConfig(
                model=self.default_model,
                max_turns=max_turns,
                timeout_sec=timeout_sec,
                system_prompt_path=sp_path,
            )

        driver = render(
            "driver",
            agent_id=agent_id,
            ws=str(ws),
            brief=str(brief_path),
            mailbox=str(mailbox_path),
            delivery=str(delivery_path),
        )

        # PID 파일 기록: lead 재시작 시 try_reattach 가 보고 in-flight 식별.
        # 자식 PID 는 sm.run() 내부에서 spawn 후 SessionResult.pid 로 노출되므로
        # 여기서는 placeholder 로 lead PID 를 기록한 뒤, sm.run() 이 자식 PID 를
        # 알게 되면 즉시 덮어쓰는 책임이 SessionManager 에 위임된다.
        # session 종료 (정상/예외 모두) 시 finally 에서 정리.
        child_pid: int | None = None
        write_pid_file(agent_id, self.agents_root, pid=child_pid)
        try:
            # 멤버에게 brief + mailbox를 context_files로도 주입 (claude -p 프롬프트 헤더에 포함)
            result: SessionResult = sm.run(
                task_id=task_id,
                prompt=driver,
                config=config,
                context_files=[brief_path, mailbox_path],
            )
            # SessionManager 가 자식 PID 를 result.pid 로 돌려준다면, finally 직전에
            # 명시적으로 다시 기록해 둔다 (이후 try_reattach 가 정확한 자식을 본다).
            child_pid = getattr(result, "pid", None)
            if child_pid:
                write_pid_file(agent_id, self.agents_root, pid=child_pid)
        finally:
            remove_pid_file(agent_id, self.agents_root)

        # 상태 토큰 감지. 세션 자체가 error (max_turns / timeout / claude error) 면
        # 토큰 유무와 무관하게 FAILED — UNKNOWN→WAITING fallback 으로 좀비 만들지 않기.
        terminal = detect_terminal_status(result.output)
        status = "FAILED" if not result.success and not terminal else (terminal or "UNKNOWN")

        # 마지막 question 감지 (멤버가 mailbox에 막 append 했을 수도)
        last_q = self._latest_question(agent_id)

        # delivery 텍스트
        delivery_text = delivery_path.read_text(encoding="utf-8") if delivery_path.exists() else ""

        # error 가 비어있어도 session 자체가 실패면 명시적 사유 채워넣음
        error_text = result.error
        if status == "FAILED" and not error_text:
            error_text = (
                f"session error (success=False, no [STATUS:*] token, "
                f"raw_output={result.output[:120]!r})"
            )

        return SpawnResult(
            agent_id=agent_id,
            status=status,
            raw_output=result.output,
            last_question=last_q,
            delivery_text=delivery_text,
            error=error_text,
            session_id=result.session_id,
            cost_usd=result.cost_usd,
        )

    def _compose_system_prompt(self, brief: HireBrief) -> str:
        """brief.system_prompt 에 kind 별 closure 를 합성해 반환.

        현재는 'refine' 라벨에만 write-guard closure 를 추가한다.
        다른 라벨('new'/'extend'/'remove') 및 None 은 원본을 그대로 반환 — 기존 동작 보존.
        kind 필드가 누락된 외부 brief 도 getattr 로 안전하게 처리.
        """
        sp = brief.system_prompt
        kind = getattr(brief, "kind", None)
        if kind == "refine":
            guard = build_refine_write_guard(brief.seed_files or [])
            sp = f"{sp}\n\n{guard}"
        return sp

    def _latest_question(self, agent_id: str) -> Message | None:
        mbox = self.agents_root / agent_id / "mailbox.md"
        msgs = parse_messages(mbox)
        for m in reversed(msgs):
            if m.kind == "question" and m.from_ == agent_id:
                return m
        return None

    @staticmethod
    def post_instruction(agent_dir: Path, body: str) -> Message:
        return append_message(
            agent_dir / "mailbox.md",
            from_="lead",
            to=agent_dir.name,
            kind="instruction",
            body=body,
        )

    @staticmethod
    def post_reply(agent_dir: Path, body: str, ref: int) -> Message:
        return append_message(
            agent_dir / "mailbox.md",
            from_="lead",
            to=agent_dir.name,
            kind="reply",
            body=body,
            ref=ref,
        )


def _predelivery_sanity_check(deliverables: list[str], cwd: Path) -> list[tuple[Path, str]]:
    """deliverables 가 cwd 내 실재 + size>0 인지 검사 → (path, reason) 위반 목록."""
    paths = [Path(d) for d in deliverables if d]
    if not paths:
        return []
    return missing_or_outside(paths, cwd)


def _format_sanity_feedback(issues: list[tuple[Path, str]]) -> str:
    """sanity 실패 사유를 mailbox 본문으로 직렬화. 경로는 마스킹하고 분류 사유만 노출."""
    lines = [
        "# Predelivery Sanity Feedback",
        "",
        "[STATUS:DONE] 보고 직후 검증에서 산출물 파일 일부에 문제가 발견되어",
        "evaluator 호출을 건너뜁니다. brief 의 Deliverables 목록을 다시 확인하여",
        "각 파일을 멤버 cwd 안에 작성(크기 > 0)한 뒤 다시 `[STATUS:DONE]` 으로 보고하세요.",
        "",
        "## 위반 사유 (경로는 보안상 마스킹됨)",
    ]
    for _, reason in issues:
        lines.append(f"- {reason}")
    return "\n".join(lines)
