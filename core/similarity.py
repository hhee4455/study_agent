"""시드 vs 멤버 산출물 유사도 게이트.

`team_lead._seed_similarity_gate` 가 충돌 등록 직전 호출. 멤버의 산출물이 시드
의도에서 너무 멀어졌으면(`< SEED_SIMILARITY_THRESHOLD`) 토론 비용 없이 즉시
폐기 + 재지시 흐름을 트리거한다.

핵심:
  - ratio 1차 지표는 `difflib.SequenceMatcher().ratio()`.
  - 결정 로직은 순수 함수 (`evaluate_conflicts`) — TeamLead 의 mutable 상태와
    분리해 단위 테스트가 손쉽다.
  - 시드 파일이 없는(new) 산출물은 호출 측에서 게이트 호출 자체를 skip.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SEED_SIMILARITY_THRESHOLD: float = 0.80
"""기본 임계값. 80% 미만 유사도면 토론 보내지 말고 폐기 + 재지시."""

_DIFF_SUMMARY_MAX_LINES: int = 20
"""diff 요약 최대 라인 수 — refine 메시지가 너무 길어지지 않게."""


@dataclass(frozen=True)
class SimilarityResult:
    """단일 파일 유사도 평가 결과."""

    ratio: float
    diff_summary: str
    above_threshold: bool


@dataclass(frozen=True)
class GateOutcome:
    """게이트가 한 충돌 파일에 대해 내린 판정.

    `rel` 은 ws_main 기준 상대 경로 (e.g. "agent_system/lead/team_lead.py").
    `stash_rel` 은 멤버 stash 파일의 동일 기준 상대 경로 (없으면 None).
    """

    rel: str
    similarity: float
    diff_summary: str
    above_threshold: bool
    stash_rel: str | None = None


def compute_ratio(seed_text: str, member_text: str) -> float:
    """두 텍스트의 SequenceMatcher.ratio() 반환. 둘 다 빈 문자열이면 1.0 취급."""
    if not seed_text and not member_text:
        return 1.0
    return difflib.SequenceMatcher(a=seed_text, b=member_text).ratio()


def summarize_diff(
    seed_text: str,
    member_text: str,
    *,
    max_lines: int = _DIFF_SUMMARY_MAX_LINES,
) -> str:
    """unified diff 의 앞부분만 잘라 짧게. refine 메시지 본문에 임베드용."""
    seed_lines = seed_text.splitlines(keepends=False)
    member_lines = member_text.splitlines(keepends=False)
    diff_iter = difflib.unified_diff(
        seed_lines,
        member_lines,
        fromfile="seed",
        tofile="member",
        lineterm="",
        n=2,
    )
    collected: list[str] = []
    for line in diff_iter:
        collected.append(line)
        if len(collected) >= max_lines:
            collected.append(f"... (diff truncated at {max_lines} lines)")
            break
    return "\n".join(collected) if collected else "(no textual diff)"


def evaluate(
    seed_text: str,
    member_text: str,
    *,
    threshold: float = SEED_SIMILARITY_THRESHOLD,
) -> SimilarityResult:
    """텍스트 두 개로부터 SimilarityResult 한 개 빌드."""
    ratio = compute_ratio(seed_text, member_text)
    summary = summarize_diff(seed_text, member_text)
    return SimilarityResult(
        ratio=ratio,
        diff_summary=summary,
        above_threshold=ratio >= threshold,
    )


def _read_text_safe(path: Path) -> str | None:
    """텍스트 파일 읽기. 실패 (없음/바이너리/권한) 시 None."""
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def evaluate_conflicts(
    conflicts: Iterable[str],
    *,
    ws_main: Path,
    agent_id: str,
    threshold: float = SEED_SIMILARITY_THRESHOLD,
) -> list[GateOutcome]:
    """각 충돌 항목에 대해 시드(ws_main) vs 멤버 stash 유사도 평가.

    stash 경로 규칙은 `WorkspaceMerger` 와 일치:
        ws_main/<rel>           ← 시드 (먼저 머지됨)
        ws_main/<rel>.from-<id> ← 멤버 stash

    파일이 없거나 읽을 수 없으면 해당 항목은 결과에서 제외 (호출자가 그대로
    debate 로 흘려보낼 수 있게 — gate 가 임의로 통과/실패를 단정하지 않음).
    """
    outcomes: list[GateOutcome] = []
    for raw in conflicts:
        if "symlink rejected" in raw:
            continue
        rel = raw.split(" ", 1)[0].strip()
        if not rel:
            continue
        seed_path = ws_main / rel
        stash_path = seed_path.with_name(f"{seed_path.name}.from-{agent_id}")
        seed_text = _read_text_safe(seed_path)
        member_text = _read_text_safe(stash_path)
        if seed_text is None or member_text is None:
            continue
        result = evaluate(seed_text, member_text, threshold=threshold)
        outcomes.append(
            GateOutcome(
                rel=rel,
                similarity=result.ratio,
                diff_summary=result.diff_summary,
                above_threshold=result.above_threshold,
                stash_rel=f"{rel}.from-{agent_id}",
            )
        )
    return outcomes


def split_outcomes(
    outcomes: Iterable[GateOutcome],
) -> tuple[list[GateOutcome], list[GateOutcome]]:
    """(통과, 폐기) 분할 — 호출자가 debate/refine 분기에 사용."""
    passed: list[GateOutcome] = []
    failed: list[GateOutcome] = []
    for o in outcomes:
        (passed if o.above_threshold else failed).append(o)
    return passed, failed


# 게이트 액션 4종 — TeamLead._seed_similarity_gate 가 분기 처리.
GATE_ACTION_PASS = "pass"  # 모든 충돌 임계값 통과 → 그대로 debate
GATE_ACTION_REFINE = "refine"  # 임계 미만 발견 → 멤버 폐기 + refine 재spawn
GATE_ACTION_BYPASS = "bypass"  # 게이트 재시도 한도 초과 → 우회 (debate 행)
GATE_ACTION_SKIP = "skip"  # kind=new 등 게이트 대상 아님


@dataclass(frozen=True)
class GateDecision:
    """게이트 판정 결과 — TeamLead 가 side effect 분기에 사용.

    REFINE 액션일 때:
      - `surviving_conflicts` 는 비어있고 (= debate 안 거침)
      - `failed_outcomes` 는 임계 미달 파일들
      - `all_outcomes` 는 평가된 모든 파일 (passed + failed) — 멤버 전체 폐기
        의미상 *모든* stash 를 정리하므로 호출자가 이 리스트로 unlink 한다.
    """

    action: str
    surviving_conflicts: list[str]
    failed_outcomes: list[GateOutcome]
    all_outcomes: list[GateOutcome]
    worst_outcome: GateOutcome | None
    refine_count_after: int


def decide_gate(
    conflicts: list[str],
    *,
    ws_main: Path,
    agent_id: str,
    brief_kind: str = "",
    refine_count: int = 0,
    threshold: float = SEED_SIMILARITY_THRESHOLD,
    max_respawns: int = 2,
) -> GateDecision:
    """게이트 결정 순수 함수.

    `brief_kind == "new"` 또는 `refine_count >= max_respawns` 면 게이트 우회/skip.
    그 외에는 충돌 텍스트 비교 후 임계값 미달이 하나라도 있으면 `refine` 결정.

    호출자는 결정 액션에 따라 mailbox append / stash 삭제 / 재spawn 트리거를
    수행한다 (결정 자체는 side effect 없음).
    """
    base_conflicts = list(conflicts)
    if not base_conflicts:
        return GateDecision(
            action=GATE_ACTION_PASS,
            surviving_conflicts=base_conflicts,
            failed_outcomes=[],
            all_outcomes=[],
            worst_outcome=None,
            refine_count_after=refine_count,
        )

    if brief_kind == "new":
        return GateDecision(
            action=GATE_ACTION_SKIP,
            surviving_conflicts=base_conflicts,
            failed_outcomes=[],
            all_outcomes=[],
            worst_outcome=None,
            refine_count_after=refine_count,
        )

    if refine_count >= max_respawns:
        return GateDecision(
            action=GATE_ACTION_BYPASS,
            surviving_conflicts=base_conflicts,
            failed_outcomes=[],
            all_outcomes=[],
            worst_outcome=None,
            refine_count_after=refine_count,
        )

    outcomes = evaluate_conflicts(
        base_conflicts,
        ws_main=ws_main,
        agent_id=agent_id,
        threshold=threshold,
    )
    if not outcomes:
        return GateDecision(
            action=GATE_ACTION_PASS,
            surviving_conflicts=base_conflicts,
            failed_outcomes=[],
            all_outcomes=[],
            worst_outcome=None,
            refine_count_after=refine_count,
        )

    _, failed = split_outcomes(outcomes)
    if not failed:
        return GateDecision(
            action=GATE_ACTION_PASS,
            surviving_conflicts=base_conflicts,
            failed_outcomes=[],
            all_outcomes=outcomes,
            worst_outcome=None,
            refine_count_after=refine_count,
        )

    worst = min(failed, key=lambda o: o.similarity)
    return GateDecision(
        action=GATE_ACTION_REFINE,
        surviving_conflicts=[],
        failed_outcomes=failed,
        all_outcomes=outcomes,
        worst_outcome=worst,
        refine_count_after=refine_count + 1,
    )
