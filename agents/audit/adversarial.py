"""Adversarial Verifier — 객관적 검증 통과 후 회의주의 패널이 한 번 더 점검.

reward hacking을 막기 위해 reverse 방향의 검토:
- Adversary: "이 산출물의 약점은?"
- Requirements-Lawyer: "spec의 어느 줄이 미충족?"
- Edge-Case Hunter: "어떤 입력에서 실패할까?"

각 페르소나는 PASS|FAIL|UNCERTAIN + **구체적 근거** 필수.
근거가 검증 가능한 형태(파일:라인, shell 명령, 입력 예제)가 아니면 자동 PASS
— false positive로 시스템을 멈추지 않음.

판정:
- 1명이라도 FAIL → DebatePanel.deliberate 트리거 + 작업 BLOCKED
- UNCERTAIN 2+ → 경고 누적 (state/adversarial_warnings.json), 5회 시 강제 중단
- 그 외 → 통과
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.llm import LLMClient, parse_json_loose

Verdict = Literal["PASS", "FAIL", "UNCERTAIN"]


PERSONAS = [
    (
        "Adversary",
        "산출물의 약점/취약점/누락을 찾는다. 호의적 해석 금지. "
        "구체적인 실패 경로(파일:라인, 명령, 입력)를 제시할 수 없으면 PASS.",
    ),
    (
        "Requirements-Lawyer",
        "원본 요구서의 각 항목이 산출물에서 충족됐는지 항목별로 점검. "
        "미충족 시 spec의 어느 표현이 미충족인지 인용 + 산출물의 어느 부분이 누락인지 명시.",
    ),
    (
        "Edge-Case Hunter",
        "정상 흐름이 아닌 실패 입력을 찾는다. 입력 예제 + 예상 실패를 명시할 수 없으면 PASS.",
    ),
]

WARNINGS_FILENAME = "adversarial_warnings.json"
DEFAULT_MAX_WARNINGS = 5


@dataclass
class PersonaJudgement:
    persona: str
    verdict: Verdict
    evidence: str  # 구체적 근거. 비어있거나 모호하면 PASS로 자동 강등.


@dataclass
class AdversarialReport:
    judgements: list[PersonaJudgement]
    triggered_debate: bool
    warnings_total: int

    def has_fail(self) -> bool:
        return any(j.verdict == "FAIL" for j in self.judgements)

    def uncertain_count(self) -> int:
        return sum(1 for j in self.judgements if j.verdict == "UNCERTAIN")


VERDICT_RE = re.compile(r"\b(PASS|FAIL|UNCERTAIN)\b")
EVIDENCE_HINT_RE = re.compile(r"(\.\w+:\d+|\$\s*\S|`[^`]+`|^- \S|입력[:：])", re.MULTILINE)


class AdversarialVerifier:
    def __init__(
        self,
        state_dir: Path,
        llm: LLMClient,
        max_warnings: int = DEFAULT_MAX_WARNINGS,
    ):
        self.state_dir = state_dir
        self.warnings_path = state_dir / WARNINGS_FILENAME
        self.llm = llm
        self.max_warnings = max_warnings

    def review(
        self,
        task_id: str,
        task_title: str,
        spec_excerpt: str,
        artifacts_summary: str,
        verifier_log: str = "",
    ) -> AdversarialReport:
        judgements = [
            self._ask_persona(
                name, persona_brief, task_title, spec_excerpt, artifacts_summary, verifier_log
            )
            for name, persona_brief in PERSONAS
        ]

        report = AdversarialReport(
            judgements=judgements,
            triggered_debate=any(j.verdict == "FAIL" for j in judgements),
            warnings_total=self._load_warning_count(),
        )

        if not report.has_fail() and report.uncertain_count() >= 2:
            report.warnings_total = self._bump_warnings(task_id, judgements)

        return report

    def threshold_exceeded(self) -> bool:
        return self._load_warning_count() >= self.max_warnings

    # ---- 페르소나 호출 ----

    def _ask_persona(
        self,
        name: str,
        persona_brief: str,
        task_title: str,
        spec_excerpt: str,
        artifacts_summary: str,
        verifier_log: str,
    ) -> PersonaJudgement:
        system = (
            f"너는 회의주의 검증 패널의 '{name}'다. {persona_brief}\n\n"
            "출력 형식 (JSON 한 줄):\n"
            '{"verdict": "PASS|FAIL|UNCERTAIN", "evidence": "<구체적 근거>"}\n\n'
            "evidence가 검증 가능한 형태(파일:라인, shell 명령, 입력 예제)가 아니면 PASS."
        )
        user = (
            f"# 작업\n{task_title}\n\n"
            f"# Spec 발췌\n{spec_excerpt}\n\n"
            f"# 산출물 요약\n{artifacts_summary}\n\n"
            f"# 객관 검증 로그\n{verifier_log[:1500]}\n\n"
            "위 작업이 spec을 충족하나? JSON 한 줄로 답하라."
        )
        text = self.llm.call(system, user, tier="sonnet")
        return self._parse_judgement(name, text)

    @staticmethod
    def _parse_judgement(persona: str, text: str) -> PersonaJudgement:
        data = parse_json_loose(text)
        verdict_raw = (data.get("verdict") or "").upper()
        evidence = str(data.get("evidence") or "").strip()

        if verdict_raw not in ("PASS", "FAIL", "UNCERTAIN"):
            m = VERDICT_RE.search(text)
            verdict_raw = m.group(1) if m else "UNCERTAIN"

        # 검증 가능한 근거가 없으면 FAIL을 PASS로 강등 (false positive 방지)
        if verdict_raw == "FAIL" and not _is_concrete_evidence(evidence):
            verdict_raw = "PASS"
            evidence = f"[downgraded: 근거 모호] {evidence}"

        return PersonaJudgement(
            persona=persona,
            verdict=verdict_raw,  # type: ignore[arg-type]
            evidence=evidence,
        )

    # ---- warnings 영속화 ----

    def _load_warnings(self) -> list[dict[str, Any]]:
        if not self.warnings_path.exists():
            return []
        try:
            result: list[dict[str, Any]] = json.loads(self.warnings_path.read_text())
            return result
        except (json.JSONDecodeError, OSError):
            return []

    def _load_warning_count(self) -> int:
        return len(self._load_warnings())

    def _bump_warnings(self, task_id: str, judgements: list[PersonaJudgement]) -> int:
        warnings = self._load_warnings()
        warnings.append(
            {
                "task_id": task_id,
                "uncertain": [
                    {"persona": j.persona, "evidence": j.evidence}
                    for j in judgements
                    if j.verdict == "UNCERTAIN"
                ],
            }
        )
        self.warnings_path.write_text(json.dumps(warnings, indent=2, ensure_ascii=False))
        return len(warnings)


def _is_concrete_evidence(evidence: str) -> bool:
    """근거가 검증 가능한 형태인지 휴리스틱.

    파일:라인 / 명령 / 코드 블록 / 입력 예제 등이 있어야 통과.
    """
    if len(evidence) < 20:
        return False
    return bool(EVIDENCE_HINT_RE.search(evidence))
