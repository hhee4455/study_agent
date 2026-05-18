"""Decomposer 출력 검증 schema (pydantic v2).

decomposer LLM 의 비결정성을 봉인하기 위한 strict 모델.
- `validate_decomposer_output(raw)` : raw JSON 문자열 또는 dict 를 받아 정규화된
  `PlanSchema` 반환.
- 실패 시 `ValidationFailure(reason, raw)` raise — team_lead 재시도 루프가 잡아
  다음 프롬프트에 reason 주입.

kind 라벨 의미:
  new    — 시드에 없는 신규 파일 작성
  refine — 시드 파일을 부분 수정
  extend — 시드 파일에 항목 추가
  remove — 시드 파일 삭제

schema 의 책임은 **enum 강제 + 빈 값/형식 거부** 까지. "시드 존재 시 kind 가 new 가
아니어야 한다" 같은 의미 검증은 decomposer 측 (team_lead 의 seed_files 자동 보완 로직).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

__all__ = [
    "DECOMPOSER_VALIDATE_MAX_RETRIES",
    "PLAN_BACKUP_KEEP",
    "DeliverableSchema",
    "HireBriefSchema",
    "Kind",
    "PlanSchema",
    "SubGoalSchema",
    "ValidationFailure",
    "call_decomposer_with_validation",
    "prune_plan_backups",
    "validate_decomposer_output",
]


# decomposer 출력 pydantic 검증 재시도: 최초 호출 + 최대 N 회 재시도 (총 N+1 회).
DECOMPOSER_VALIDATE_MAX_RETRIES: int = 3

# plan.replaced-*.md 백업 보존 개수.
PLAN_BACKUP_KEEP: int = 5


Kind = Literal["new", "refine", "extend", "remove"]


# 안전한 상대 경로 형식: 절대 경로 / 부모 디렉토리 탈출 / null byte 거부.
_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


class ValidationFailure(Exception):
    """decomposer 출력 검증 실패. team_lead 재시도 루프에서 잡아 다음 프롬프트에 reason 주입."""

    def __init__(self, reason: str, raw: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw = raw

    def __str__(self) -> str:
        return self.reason


def _validate_relpath(value: str, field_name: str) -> str:
    """파일 경로 형식 검증. 절대 경로, 상위 탈출, null byte, 빈 값 거부."""
    v = value.strip()
    if not v:
        raise ValueError(f"{field_name} 가 빈 문자열")
    if v.startswith("/") or v.startswith("~"):
        raise ValueError(f"{field_name} 절대 경로 금지: {v!r}")
    if "\x00" in v:
        raise ValueError(f"{field_name} null byte 포함")
    parts = v.split("/")
    if ".." in parts:
        raise ValueError(f"{field_name} 상위 디렉토리 참조 금지: {v!r}")
    if not _PATH_RE.match(v):
        raise ValueError(f"{field_name} 허용되지 않은 문자 포함: {v!r}")
    return v


class DeliverableSchema(BaseModel):
    """단일 산출물. path + kind + 한 줄 설명."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str = Field(..., min_length=1, description="cwd 상대 경로")
    kind: Kind = Field(..., description="new | refine | extend | remove")
    note: str = Field(default="", description="짧은 설명 (선택)")

    @field_validator("path")
    @classmethod
    def _check_path(cls, v: str) -> str:
        return _validate_relpath(v, "deliverable.path")


class HireBriefSchema(BaseModel):
    """채용 brief 의 strict 형태. (선택적 — decomposer 가 직접 brief 까지 출력할 때 사용)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mission: str = Field(..., min_length=1)
    deliverables: list[DeliverableSchema] = Field(..., min_length=1)
    seed_files: list[str] = Field(default_factory=list)
    verification_checks: list[dict[str, Any]] = Field(default_factory=list)
    system_prompt: str = Field(default="")
    allowed_tools: list[str] | None = Field(default=None)
    verify: bool = Field(default=False)
    model: Literal["sonnet", "opus"] | None = Field(default=None)

    @field_validator("seed_files")
    @classmethod
    def _check_seed_files(cls, v: list[str]) -> list[str]:
        return [_validate_relpath(p, "seed_files[*]") for p in v]


class SubGoalSchema(BaseModel):
    """단일 sub-goal. plan.md 의 한 라인에 대응."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(..., pattern=r"^G-[A-Za-z0-9_-]+$")
    title: str = Field(..., min_length=1, max_length=300)
    deliverables: list[DeliverableSchema] = Field(default_factory=list)
    seed_files: list[str] = Field(default_factory=list)

    @field_validator("seed_files")
    @classmethod
    def _check_seed_files(cls, v: list[str]) -> list[str]:
        return [_validate_relpath(p, "seed_files[*]") for p in v]


class PlanSchema(BaseModel):
    """decomposer 가 출력하는 plan 전체. sub_goals 1개 이상 필수."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sub_goals: list[SubGoalSchema] = Field(..., min_length=1)
    notes: str = Field(default="")

    @field_validator("sub_goals")
    @classmethod
    def _unique_ids(cls, v: list[SubGoalSchema]) -> list[SubGoalSchema]:
        seen: set[str] = set()
        for g in v:
            if g.id in seen:
                raise ValueError(f"중복 sub_goal id: {g.id!r}")
            seen.add(g.id)
        return v


def _extract_json_object(text: str) -> dict[str, Any]:
    """LLM 텍스트 응답에서 첫 JSON 객체 추출. 코드 펜스/앞뒤 설명 허용."""
    s = text.strip()
    if s.startswith("```"):
        s = "\n".join(line for line in s.splitlines() if not line.strip().startswith("```"))
        s = s.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise json.JSONDecodeError("JSON 객체 경계 못 찾음", s, 0)
    result: dict[str, Any] = json.loads(s[start : end + 1])
    return result


def validate_decomposer_output(raw: str | dict[str, Any]) -> PlanSchema:
    """raw 문자열(JSON) 또는 dict 를 받아 PlanSchema 로 정규화. 실패 시 ValidationFailure."""
    raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)

    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = _extract_json_object(raw)
        except json.JSONDecodeError as e:
            raise ValidationFailure(
                reason=f"JSON 파싱 실패: {e.msg} (pos={e.pos})",
                raw=raw_text,
            ) from e

    if not isinstance(data, dict):
        raise ValidationFailure(
            reason=f"최상위 JSON 이 객체 아님: {type(data).__name__}",
            raw=raw_text,
        )

    try:
        return PlanSchema.model_validate(data)
    except ValidationError as e:
        errors = e.errors(include_url=False)
        raise ValidationFailure(
            reason=f"PlanSchema 검증 실패: {e.error_count()}개 오류 — {errors}",
            raw=raw_text,
        ) from e


class _LLMCallable(Protocol):
    """`LLMClient.call(system, user, tier=...)` 와 호환되는 최소 인터페이스.

    Liskov: 구현체(LLMClient)의 tier 가 Literal 로 좁혀져 있어도 받아들이도록
    *args/**kwargs 로 정의 — Protocol 매개변수를 contravariant 하게 유지.
    """

    def call(self, system: str, user: str, *args: Any, **kwargs: Any) -> str: ...


def call_decomposer_with_validation(
    llm: _LLMCallable,
    system: str,
    user: str,
    *,
    tier: str = "opus",
    max_retries: int = DECOMPOSER_VALIDATE_MAX_RETRIES,
    log: Callable[[str], None] | None = None,
) -> PlanSchema:
    """decomposer LLM 호출 + pydantic 검증 + 실패 시 reason 주입한 재시도 루프.

    총 (max_retries + 1) 회 시도. 마지막 실패는 ValidationFailure 그대로 raise.
    """
    if max_retries < 0:
        raise ValueError(f"max_retries 음수: {max_retries}")
    last_failure: ValidationFailure | None = None
    last_raw: str = ""
    attempts = max_retries + 1
    for attempt in range(1, attempts + 1):
        prompt_user = user
        if last_failure is not None:
            prompt_user = (
                f"{user}\n\n"
                f"# 이전 시도 실패 ({attempt - 1}/{attempts})\n"
                f"네 직전 응답이 schema 검증을 통과하지 못했다.\n"
                f"검증 에러:\n```\n{last_failure.reason}\n```\n"
                f"직전 응답 (앞 800자):\n```\n{last_raw[:800]}\n```\n"
                f"이번에는 **JSON 객체 하나** 만 출력하라. 마크다운/설명/펜스 일체 금지."
            )
        try:
            raw = llm.call(system, prompt_user, tier=tier)
        except (RuntimeError, OSError, ValueError) as e:
            last_failure = ValidationFailure(
                reason=f"LLM 호출 예외: {type(e).__name__}: {e}",
                raw="",
            )
            last_raw = ""
            if log:
                log(f"  ⚠ decomposer 시도 {attempt}/{attempts} LLM 예외: {e}")
            continue

        last_raw = raw
        try:
            plan = validate_decomposer_output(raw)
        except ValidationFailure as vf:
            last_failure = vf
            if log:
                log(f"  ⚠ decomposer 시도 {attempt}/{attempts} 검증 실패: {vf.reason[:200]}")
            continue
        if log and attempt > 1:
            log(f"  ✓ decomposer 시도 {attempt}/{attempts} 검증 통과")
        return plan

    assert last_failure is not None
    raise last_failure


def prune_plan_backups(root: Path, keep: int = PLAN_BACKUP_KEEP) -> None:
    """root 내 plan.replaced-*.md 백업을 mtime 내림차순 정렬해 keep 개 초과분 삭제."""
    if keep < 0:
        raise ValueError(f"keep 음수: {keep}")
    if not root.exists() or not root.is_dir():
        return
    backups = sorted(
        root.glob("plan.replaced-*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[keep:]:
        try:
            stale.unlink()
        except OSError:
            # 권한/경합으로 못 지워도 무시 — 다음 호출에 재시도됨
            continue
