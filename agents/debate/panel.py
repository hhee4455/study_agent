"""Debate Panel — 3 에이전트 md 기반 토론 (요구사항 3).

설계:
- 10명 → 3명 (belief entrenchment 완화)
- 모든 대화는 md 파일에 누적, 파일 자체가 컨텍스트
- 길어지면 정리 에이전트가 압축
- 자동 채택 안 함 — 사람이 ## 최종 결정 채워야 진행
- 2단계 escalate 지원: model 파라미터로 라운드별 강한 모델 전환 (sonnet → opus)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.llm import MODEL_OPUS, MODEL_SONNET, LLMClient, strip_agent_label

# 각 페르소나: (handle, role, persona_description, model_or_tier)
# model_or_tier: claude tier ("sonnet"/"opus"/"haiku") 또는 codex 모델 슬러그 ("gpt-5.4-mini")
PERSONAS = [
    ("Agent-A", "Pragmatist", "단순한 해결책 선호. 출하 우선.", "sonnet"),
    ("Agent-B", "Skeptic", "가정에 의문 제기. 동조 압력 거부, 약점 찾기.", "sonnet"),
    ("Agent-C", "Architect", "장기 영향, 원칙 기반 설계.", "opus"),
    # 4번째는 다른 백엔드 (OpenAI) — belief entrenchment 완화. claude 3명만 있으면
    # 같은 베이스 모델 출신이라 같은 사각지대 가짐. 진짜 다른 출처가 한 명 필요.
    (
        "Agent-D",
        "Outsider",
        "다른 진영의 시각. claude 토론에 외부자로 합류. 합의 형성 압력 거부, 셋이 놓친 측면 발굴.",
        "gpt-5.4-mini",
    ),
]

# 충돌 머지 통합 결정 같은 "두 버전 중 선택/통합 방향"용 단축 패널.
# Skeptic(약점 찾기) + Outsider(외부 시각) 둘만, max_rounds=1 권장.
# belief entrenchment 완화의 핵심(다른 백엔드 1명)은 보존. high-stakes 답변에는 쓰지 말 것.
PERSONAS_FAST = [
    ("Agent-B", "Skeptic", "가정에 의문 제기. 동조 압력 거부, 약점 찾기.", "sonnet"),
    (
        "Agent-D",
        "Outsider",
        "다른 진영의 시각. 합의 형성 압력 거부, 상대편이 놓친 측면 발굴.",
        "gpt-5.4-mini",
    ),
]
COMPACT_THRESHOLD_BYTES = 8000


@dataclass
class DebateOutcome:
    """토론 결과 — 합의 여부 + 결정문 + (선택) 통합 산출물."""

    md_path: Path
    summary: str
    decision: str = ""  # 자동 결정 본문 (auto_decide=True인 경우)
    requires_human: bool = True  # False면 팀장이 이미 결정함
    consensus_reached: bool = False  # 토론 중 합의 감지 여부 (escalate 트리거)
    integrated_content: str | None = None  # integrate_content=True 일 때 추출한 통합본


class DebatePanel:
    """토론 패널 — 페르소나가 라운드를 돌며 결정/합의를 합성한다.

    `model` 을 주면 모든 페르소나 발언과 decide/summarize 가 그 식별자로 라우팅된다.
    None 이면 PERSONAS 각각의 기본 모델을 유지 (backward compat).
    """

    def __init__(
        self,
        debates_dir: Path,
        llm: LLMClient,
        max_rounds: int = 2,
        personas: list[Any] | None = None,
        model: str | None = None,
    ):
        """`model=None` 이면 PERSONAS 각자의 기본 모델 사용, 아니면 전원 override."""
        self.debates_dir = debates_dir
        self.llm = llm
        self.max_rounds = max_rounds
        self.personas = personas if personas is not None else PERSONAS
        self.model = model
        debates_dir.mkdir(parents=True, exist_ok=True)

    def deliberate(
        self,
        question: str,
        context: str = "",
        debate_id: str | None = None,
        auto_decide: bool = True,
        model: str | None = None,
        integrate_content: bool = False,
    ) -> DebateOutcome:
        """페르소나 토론 + 요약 + (옵션) 통합본 추출.

        `model` (round 단위 override) 우선순위: 호출 인자 > __init__ > 페르소나 기본.
        `integrate_content=True` 면 결정 후 통합 코드/텍스트 한 번 더 추출해 outcome 에 부착.
        """
        effective_model = model or self.model
        debate_id = debate_id or f"debate-{int(datetime.utcnow().timestamp())}"
        md_path = self.debates_dir / f"{debate_id}.md"
        self._init_md(md_path, question, context, effective_model)

        consensus = False
        for round_num in range(1, self.max_rounds + 1):
            self._append(md_path, f"## Round {round_num}\n")
            round_statements: list[str] = []
            for handle, role, persona, default_model in self.personas:
                model_for_call = effective_model or default_model
                statement = self._speak(
                    md_path,
                    handle,
                    role,
                    persona,
                    round_num,
                    model_for_call,
                )
                round_statements.append(statement)
                self._append(md_path, f"\n[{handle} / {role}]\n{statement}\n")

            if md_path.stat().st_size > COMPACT_THRESHOLD_BYTES:
                self._compact(md_path)
            self._append(md_path, "\n---\n\n")

            # 조기 종료: 합의 감지되면 다음 라운드 skip (iMAD/SID 패턴, ~30% 토큰 절감).
            if self._round_is_consensus(round_statements):
                consensus = True
                if round_num < self.max_rounds:
                    self._append(
                        md_path,
                        f"_Round {round_num + 1}+ skipped: consensus detected_\n\n",
                    )
                    break

        summary = self._summarize(md_path, effective_model)
        self._append(md_path, f"## Summary\n{summary}\n\n")

        if not auto_decide:
            self._append(
                md_path,
                "## 최종 결정\n_운영자가 채울 섹션. 채워지면 작업 재개._\n",
            )
            return DebateOutcome(
                md_path=md_path,
                summary=summary,
                consensus_reached=consensus,
            )

        decision = self._decide(md_path, question, effective_model)
        self._append(md_path, f"## 최종 결정\n{decision}\n")

        integrated: str | None = None
        if integrate_content:
            integrated = self._integrate(question, context, decision, effective_model)
            if integrated is not None:
                self._append(md_path, "## 통합본\n```\n" + integrated + "\n```\n")

        return DebateOutcome(
            md_path=md_path,
            summary=summary,
            decision=decision,
            requires_human=False,
            consensus_reached=consensus,
            integrated_content=integrated,
        )

    # ---- md 조작 ----

    @staticmethod
    def _init_md(
        path: Path,
        question: str,
        context: str,
        model: str | None = None,
    ) -> None:
        model_line = f"**Model**: {model}\n\n" if model else ""
        path.write_text(
            f"# Debate: {question[:80]}\n\n"
            f"**Question**: {question}\n\n"
            f"{model_line}"
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
        self,
        md_path: Path,
        handle: str,
        role: str,
        persona: str,
        round_num: int,
        model: str,
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
                f"첫 라운드. 다른 에이전트 의견 무시하고 독립적으로 답하라.\n\n{slim}\n\n네 의견:"
            )
        else:
            user = (
                f"직전 라운드 발언에 응답. 반복 금지, 새 정보/반론만. "
                f"의견 바꾸면 *왜* 한 줄로.\n\n{slim}"
            )
        # 모델 prefix가 codex 백엔드면 LLMClient가 자동 라우팅 (gpt-*, o*-, codex-*).
        return strip_agent_label(self.llm.call(system, user, model=model))

    @staticmethod
    def _round_is_consensus(statements: list[str]) -> bool:
        """라운드 발언 묶음이 합의 분위기인가. True 면 추가 라운드 가치 없음.

        조건 (하나라도 만족): (a) 대부분 페르소나가 '동의' prefix, (b) 모든 발언이
        매우 짧음(<60자) — 새 논점 없음을 의미.
        """
        if not statements:
            return False
        agree_prefixes = ("동의", "agree", "찬성", "yes", "맞")
        n = len(statements)
        agree_n = sum(1 for s in statements if s.strip().lower().startswith(agree_prefixes))
        short_n = sum(1 for s in statements if len(s.strip()) < 60)
        # 페르소나 4명 중 3+ 동의, 또는 전원 짧음.
        return agree_n >= max(1, n - 1) or short_n == n

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

    def _decide(self, md_path: Path, question: str, model: str | None = None) -> str:
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
            "**근거**: <3-5문장. 어느 페르소나의 어떤 논점을 채택했고 왜 / "
            "다른 페르소나의 우려는 어떻게 처리할지>\n\n"
            "**즉시 실행**: <지금 바로 코드/시스템에 적용할 한 줄 또는 'TBD'>\n\n"
            "**후속 작업**: <- bullet 1\\n- bullet 2 (생략 가능)>\n\n"
            "마크다운 다른 헤더(##) 쓰지 마라. 위 형식 그대로."
        )
        # 결정은 model 지정되면 그 모델, 아니면 opus (최강 모델로 합의 추출).
        chosen_model = model or MODEL_OPUS
        return self.llm.call(system, user, model=chosen_model).strip()

    def _summarize(self, md_path: Path, model: str | None = None) -> str:
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
        # 요약은 구조화된 단순 출력 — model override 가 있어도 haiku 로 유지(비용).
        chosen_model = model or "haiku"
        return strip_agent_label(self.llm.call(system, user, model=chosen_model))

    def _integrate(
        self,
        question: str,
        context: str,
        decision: str,
        model: str | None = None,
    ) -> str | None:
        """결정문 + 컨텍스트 → 통합본(파일 내용) 한 번 더 추출. 코드 펜스로 감싸야 함."""
        system = (
            "너는 통합 작성자다. 토론 결정문을 적용해 최종 산출물(파일 내용)을 만든다. "
            "출력은 정확히 ```...``` 코드 펜스 하나만 — 펜스 밖 텍스트 금지."
        )
        user = (
            f"# 결정\n{decision}\n\n"
            f"# 원래 질문/컨텍스트\n{question}\n\n{context}\n\n"
            "# 출력: 통합본을 ```...``` 한 블록으로만 출력."
        )
        chosen_model = model or MODEL_SONNET
        raw = self.llm.call(system, user, model=chosen_model)
        m = re.search(r"```(?:[a-zA-Z0-9_+\-.]*)\s*\n(.*?)\n```", raw, re.DOTALL)
        return m.group(1) if m else None
