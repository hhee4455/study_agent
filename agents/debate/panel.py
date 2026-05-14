"""Debate Panel — 3 에이전트 md 기반 토론 (요구사항 3).

설계:
- 10명 → 3명 (belief entrenchment 완화)
- 모든 대화는 md 파일에 누적, 파일 자체가 컨텍스트
- 길어지면 정리 에이전트가 압축
- 자동 채택 안 함 — 사람이 ## 최종 결정 채워야 진행
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.llm import LLMClient, strip_agent_label


# 각 페르소나: (handle, role, persona_description, model_or_tier)
# model_or_tier: claude tier ("sonnet"/"opus"/"haiku") 또는 codex 모델 슬러그 ("gpt-5.4-mini")
PERSONAS = [
    ("Agent-A", "Pragmatist", "단순한 해결책 선호. 출하 우선.", "sonnet"),
    ("Agent-B", "Skeptic", "가정에 의문 제기. 동조 압력 거부, 약점 찾기.", "sonnet"),
    ("Agent-C", "Architect", "장기 영향, 원칙 기반 설계.", "opus"),
    # 4번째는 다른 백엔드 (OpenAI) — belief entrenchment 완화. claude 3명만 있으면
    # 같은 베이스 모델 출신이라 같은 사각지대 가짐. 진짜 다른 출처가 한 명 필요.
    ("Agent-D", "Outsider",
     "다른 진영의 시각. claude 토론에 외부자로 합류. 합의 형성 압력 거부, "
     "셋이 놓친 측면 발굴.", "gpt-5.4-mini"),
]

# 충돌 머지 통합 결정 같은 "두 버전 중 선택/통합 방향"용 단축 패널.
# Skeptic(약점 찾기) + Outsider(외부 시각) 둘만, max_rounds=1 권장.
# belief entrenchment 완화의 핵심(다른 백엔드 1명)은 보존. high-stakes 답변에는 쓰지 말 것.
PERSONAS_FAST = [
    ("Agent-B", "Skeptic", "가정에 의문 제기. 동조 압력 거부, 약점 찾기.", "sonnet"),
    ("Agent-D", "Outsider",
     "다른 진영의 시각. 합의 형성 압력 거부, 상대편이 놓친 측면 발굴.", "gpt-5.4-mini"),
]
COMPACT_THRESHOLD_BYTES = 8000


@dataclass
class DebateOutcome:
    md_path: Path
    summary: str
    decision: str = ""           # 자동 결정 본문 (auto_decide=True인 경우)
    requires_human: bool = True  # False면 팀장이 이미 결정함


class DebatePanel:
    def __init__(
        self,
        debates_dir: Path,
        llm: LLMClient,
        max_rounds: int = 2,
        personas: Optional[list] = None,
    ):
        self.debates_dir = debates_dir
        self.llm = llm
        self.max_rounds = max_rounds
        self.personas = personas if personas is not None else PERSONAS
        debates_dir.mkdir(parents=True, exist_ok=True)

    def deliberate(
        self, question: str, context: str = "",
        debate_id: Optional[str] = None,
        auto_decide: bool = True,
    ) -> DebateOutcome:
        """4 페르소나가 토론 + 요약 후, auto_decide면 팀장이 결정도 직접 작성.

        auto_decide=False: '## 최종 결정' 섹션을 placeholder로 두고 사람이 채우길 대기.
        auto_decide=True (기본): 팀장(opus) LLM이 토론 전체를 보고 결정 + 근거 작성.
        """
        debate_id = debate_id or f"debate-{int(datetime.utcnow().timestamp())}"
        md_path = self.debates_dir / f"{debate_id}.md"
        self._init_md(md_path, question, context)

        for round_num in range(1, self.max_rounds + 1):
            self._append(md_path, f"## Round {round_num}\n")
            for handle, role, persona, model in self.personas:
                statement = self._speak(md_path, handle, role, persona, round_num, model)
                self._append(md_path, f"\n[{handle} / {role}]\n{statement}\n")

            if md_path.stat().st_size > COMPACT_THRESHOLD_BYTES:
                self._compact(md_path)
            self._append(md_path, "\n---\n\n")

        summary = self._summarize(md_path)
        self._append(md_path, f"## Summary\n{summary}\n\n")

        if auto_decide:
            decision = self._decide(md_path, question)
            self._append(md_path, f"## 최종 결정\n{decision}\n")
            return DebateOutcome(
                md_path=md_path, summary=summary,
                decision=decision, requires_human=False,
            )

        self._append(
            md_path,
            "## 최종 결정\n_운영자가 채울 섹션. 채워지면 작업 재개._\n",
        )
        return DebateOutcome(md_path=md_path, summary=summary)

    # ---- md 조작 ----

    @staticmethod
    def _init_md(path: Path, question: str, context: str) -> None:
        path.write_text(
            f"# Debate: {question[:80]}\n\n"
            f"**Question**: {question}\n\n"
            f"**Context**:\n{context}\n\n"
            f"**Started**: {datetime.utcnow().isoformat()}\n\n"
            f"---\n\n"
        )

    @staticmethod
    def _append(path: Path, content: str) -> None:
        with path.open("a") as f:
            f.write(content)

    # ---- 에이전트 발언 ----

    def _speak(
        self, md_path: Path, handle: str, role: str, persona: str,
        round_num: int, model: str,
    ) -> str:
        slim = self._slim_context(md_path, round_num)
        system = (
            f"너는 토론 참가자 '{handle} ({role})'다. {persona}\n\n"
            f"규칙:\n"
            f"- 3-5문장. 길게 쓰지 마라.\n"
            f"- 다른 에이전트가 한 말 반복 금지. 동의면 '동의' 한 줄.\n"
            f"- 동조 압력 거부. 옳다고 생각하면 소수의견 유지.\n"
            f"- 마크다운 헤더/코드펜스 사용 금지.\n"
            f"- 이름표 [{handle}]는 시스템이 붙임. 본문만 작성."
        )
        if round_num == 1:
            user = (
                f"첫 라운드. 다른 에이전트 의견 무시하고 독립적으로 답하라.\n\n"
                f"{slim}\n\n네 의견:"
            )
        else:
            user = (
                f"직전 라운드 발언에 응답. 반복 금지, 새 정보/반론만. "
                f"의견 바꾸면 *왜* 한 줄로.\n\n{slim}"
            )
        # 모델 prefix가 codex 백엔드면 LLMClient가 자동 라우팅 (gpt-*, o*-, codex-*).
        return strip_agent_label(self.llm.call(system, user, model=model))

    @staticmethod
    def _slim_context(md_path: Path, round_num: int) -> str:
        """전체 md 대신 헤더 + 직전 라운드만 LLM에 전송 (토큰 quadratic 완화).

        라운드 1: 헤더(질문/컨텍스트)만.
        라운드 N (N>=2): 헤더 + 직전 라운드 본문.
        """
        text = md_path.read_text()
        # `## Round` 마커로 분할. 첫 토막은 헤더.
        parts = text.split("\n## Round ")
        header = parts[0]
        if round_num == 1 or len(parts) < 2:
            return header
        # 직전 라운드 = 마지막 완성된 라운드
        prev_round = parts[-1]
        return f"{header}\n## Round {prev_round}"

    # ---- 정리 ----

    def _compact(self, md_path: Path) -> None:
        original = md_path.read_text()
        md_path.with_suffix(".archive.md").write_text(original)

        system = "너는 토론 정리 에이전트다. 핵심만 남기고 중복 제거."
        user = (
            f"이전 라운드들만 각 에이전트당 1-2문장으로 압축. "
            f"마지막 라운드는 그대로 유지. 헤더 보존.\n\n{original}"
        )
        # 단순 압축 작업 → haiku
        compacted = strip_agent_label(self.llm.call(system, user, tier="haiku"))
        compacted += f"\n\n_[Compacted at {datetime.utcnow().isoformat()}]_\n"
        md_path.write_text(compacted)

    def _decide(self, md_path: Path, question: str) -> str:
        """팀장 페르소나가 토론 + summary 보고 최종 결정 작성."""
        full_md = md_path.read_text(encoding="utf-8")
        system = (
            "너는 이 시스템의 팀장(lead)이다. 팀원 토론과 요약을 보고 최종 결정을 내린다. "
            "각 페르소나는 자기 시각의 일부만 본다. 너는 모든 상황을 알고 있고 결재 권한이 있다."
        )
        user = (
            f"# 결정할 질문\n{question}\n\n"
            f"# 토론 전문\n{full_md}\n\n"
            "# 출력 형식 (정확히 이 형식)\n"
            "**결정**: <1-2문장으로 명확히 어떤 옵션/조합을 택할지>\n\n"
            "**근거**: <3-5문장. 어느 페르소나의 어떤 논점을 채택했고 왜 / 다른 페르소나의 우려는 어떻게 처리할지>\n\n"
            "**즉시 실행**: <지금 바로 코드/시스템에 적용할 한 줄 또는 'TBD'>\n\n"
            "**후속 작업**: <- bullet 1\\n- bullet 2 (생략 가능)>\n\n"
            "마크다운 다른 헤더(##) 쓰지 마라. 위 형식 그대로."
        )
        # 결정은 가장 강한 모델로 (opus). belief entrenchment 완화 위해 토론 발언자와 별개 호출.
        return self.llm.call(system, user, tier="opus").strip()

    def _summarize(self, md_path: Path) -> str:
        full_md = md_path.read_text()
        system = "토론 결과 요약자. 결정 내리지 말고 옵션만 정리."
        user = (
            f"{full_md}\n\n형식:\n"
            f"- **옵션 1**: ... (지지: 누구, 근거: ...)\n"
            f"- **옵션 2**: ...\n"
            f"- **합의된 부분**: ...\n"
            f"- **미해결 쟁점**: ...\n"
            f"- **운영자 주의**: 모두 같은 답이면 belief entrenchment 가능성."
        )
        # 요약 작업 → haiku (구조화된 단순 출력)
        return strip_agent_label(self.llm.call(system, user, tier="haiku"))
