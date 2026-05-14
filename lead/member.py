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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.session_manager import SessionConfig, SessionManager, SessionResult

from lead.mailbox import (
    Message, append_message, detect_terminal_status, parse_messages,
)
from lead.prompts import render


@dataclass
class SpawnResult:
    agent_id: str
    status: str               # DONE | WAITING | FAILED | UNKNOWN
    raw_output: str
    last_question: Optional[Message] = None
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
    verification_checks: list[dict]  # Verifier 형식
    system_prompt: str               # 팀장이 LLM으로 작성한 멤버 persona
    seed_files: list[str] = None     # 옵션: main에서 복사해 줄 파일들
    allowed_tools: list[str] = None
    # P4 결정(2026-05-13): per-hire Evaluator 토글.
    # 팀장이 채용 LLM 응답 JSON에서 verify=true를 받으면 그 멤버 산출물은
    # AdversarialVerifier critique-refine 1 cycle 거침. 기본 false.
    # 켜야 할 조건은 hire_brief.md 프롬프트에서 lead에게 지시.
    verify: bool = False


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
- mailbox: {agent_dir / 'mailbox.md'}
- delivery: {agent_dir / 'delivery.md'}

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
        """SessionManager로 멤버 세션 1회 실행. resume_count>0이면 task_id에 -rN 접미."""
        agent_id = brief.agent_id
        agent_dir = self.agents_root / agent_id
        ws = self.ws_root / agent_id
        brief_path = agent_dir / "brief.md"
        mailbox_path = agent_dir / "mailbox.md"
        delivery_path = agent_dir / "delivery.md"

        if not brief_path.exists():
            self.write_brief(brief)

        # 각 spawn마다 cwd=ws/{agent_id}로 격리된 새 SessionManager 인스턴스
        sm = SessionManager(self.state_dir, ws)

        task_id = agent_id if resume_count == 0 else f"{agent_id}-r{resume_count}"

        # 멤버용 system prompt를 별도 파일로 저장 (SessionConfig.system_prompt_path는 텍스트 파일을 받음)
        sp_path = agent_dir / "system_prompt.md"
        sp_path.write_text(brief.system_prompt, encoding="utf-8")

        # allowed_tools 명시 안 됐으면 SessionConfig 기본값 사용 (web+grep+glob 포함).
        config_kwargs = dict(
            model=self.default_model,
            max_turns=max_turns,
            timeout_sec=timeout_sec,
            system_prompt_path=sp_path,
        )
        if brief.allowed_tools:
            config_kwargs["allowed_tools"] = brief.allowed_tools
        config = SessionConfig(**config_kwargs)

        driver = render(
            "driver",
            agent_id=agent_id,
            ws=str(ws),
            brief=str(brief_path),
            mailbox=str(mailbox_path),
            delivery=str(delivery_path),
        )

        # 멤버에게 brief + mailbox를 context_files로도 주입 (claude -p 프롬프트 헤더에 포함)
        result: SessionResult = sm.run(
            task_id=task_id,
            prompt=driver,
            config=config,
            context_files=[brief_path, mailbox_path],
        )

        # 상태 토큰 감지. 세션 자체가 error (max_turns / timeout / claude error) 면
        # 토큰 유무와 무관하게 FAILED — UNKNOWN→WAITING fallback 으로 좀비 만들지 않기.
        terminal = detect_terminal_status(result.output)
        if not result.success and not terminal:
            status = "FAILED"
        else:
            status = terminal or "UNKNOWN"

        # 마지막 question 감지 (멤버가 mailbox에 막 append 했을 수도)
        last_q = self._latest_question(agent_id)

        # delivery 텍스트
        delivery_text = delivery_path.read_text(encoding="utf-8") if delivery_path.exists() else ""

        # error 가 비어있어도 session 자체가 실패면 명시적 사유 채워넣음
        error_text = result.error
        if status == "FAILED" and not error_text:
            error_text = f"session error (success=False, no [STATUS:*] token, raw_output={result.output[:120]!r})"

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

    def _latest_question(self, agent_id: str) -> Optional[Message]:
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
            from_="lead", to=agent_dir.name, kind="instruction", body=body,
        )

    @staticmethod
    def post_reply(agent_dir: Path, body: str, ref: int) -> Message:
        return append_message(
            agent_dir / "mailbox.md",
            from_="lead", to=agent_dir.name, kind="reply", body=body, ref=ref,
        )
