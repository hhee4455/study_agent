"""test_schemas.py — pydantic 검증, retry 루프, plan 백업 정리 단위 테스트.

verifier 가 `cd agent_system && python -m pytest tests/test_schemas.py -q` 로 실행하므로
sys.path 에 agent_system 을 넣어 `core.schemas` 를 import.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

# 패키지 경로 — agent_system 디렉토리를 sys.path 에.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# conftest 는 team_lead 테스트용으로 core.schemas 를 stub 하지만 — 본 파일은 실제
# schemas 모듈을 검증하므로 stub 을 걷어내고 진짜 모듈을 로드한다.
sys.modules.pop("core.schemas", None)

from core.schemas import (  # noqa: E402
    DECOMPOSER_VALIDATE_MAX_RETRIES,
    PLAN_BACKUP_KEEP,
    DeliverableSchema,
    HireBriefSchema,
    PlanSchema,
    SubGoalSchema,
    ValidationFailure,
    call_decomposer_with_validation,
    prune_plan_backups,
    validate_decomposer_output,
)

# ---------- PlanSchema 직접 검증 ----------


def test_plan_schema_accepts_minimal_valid():
    plan = PlanSchema.model_validate(
        {
            "sub_goals": [
                {"id": "G-001-foo", "title": "한 줄 설명"},
            ],
        }
    )
    assert plan.sub_goals[0].id == "G-001-foo"
    assert plan.notes == ""


def test_plan_schema_rejects_empty_sub_goals():
    with pytest.raises(Exception):
        PlanSchema.model_validate({"sub_goals": []})


def test_plan_schema_rejects_duplicate_ids():
    with pytest.raises(Exception):
        PlanSchema.model_validate(
            {
                "sub_goals": [
                    {"id": "G-001", "title": "a"},
                    {"id": "G-001", "title": "b"},
                ]
            }
        )


def test_plan_schema_rejects_extra_top_level_fields():
    """extra='forbid' — 예상 못한 키는 거부."""
    with pytest.raises(Exception):
        PlanSchema.model_validate(
            {
                "sub_goals": [{"id": "G-1", "title": "x"}],
                "unexpected": "boom",
            }
        )


def test_sub_goal_id_pattern_enforced():
    with pytest.raises(Exception):
        SubGoalSchema.model_validate({"id": "no-prefix", "title": "x"})
    with pytest.raises(Exception):
        SubGoalSchema.model_validate({"id": "G-bad space", "title": "x"})
    SubGoalSchema.model_validate({"id": "G-abc_123-x", "title": "x"})  # ok


def test_sub_goal_title_min_length():
    with pytest.raises(Exception):
        SubGoalSchema.model_validate({"id": "G-1", "title": ""})


# ---------- DeliverableSchema (kind enum + 경로 형식) ----------


def test_deliverable_kind_enum_enforced():
    DeliverableSchema.model_validate({"path": "src/x.py", "kind": "new"})
    DeliverableSchema.model_validate({"path": "src/x.py", "kind": "refine"})
    DeliverableSchema.model_validate({"path": "src/x.py", "kind": "extend"})
    DeliverableSchema.model_validate({"path": "src/x.py", "kind": "remove"})
    with pytest.raises(Exception):
        DeliverableSchema.model_validate({"path": "src/x.py", "kind": "create"})
    with pytest.raises(Exception):
        DeliverableSchema.model_validate({"path": "src/x.py", "kind": "NEW"})


def test_deliverable_path_rejects_absolute_and_traversal():
    for bad in ["/etc/passwd", "~/x", "../escape.py", "a/../b", "a/b/../c"]:
        with pytest.raises(Exception):
            DeliverableSchema.model_validate({"path": bad, "kind": "new"})


def test_deliverable_path_rejects_empty():
    with pytest.raises(Exception):
        DeliverableSchema.model_validate({"path": "", "kind": "new"})
    with pytest.raises(Exception):
        DeliverableSchema.model_validate({"path": "   ", "kind": "new"})


def test_deliverable_path_rejects_disallowed_chars():
    for bad in ["a b.py", "a;b.py", "a|b.py", "a\x00b.py"]:
        with pytest.raises(Exception):
            DeliverableSchema.model_validate({"path": bad, "kind": "new"})


def test_deliverable_path_accepts_typical_relpath():
    for ok in ["a.py", "src/a.py", "src/sub/a.py", "a-b_c.py", "tests/test_x.py"]:
        d = DeliverableSchema.model_validate({"path": ok, "kind": "refine"})
        assert d.path == ok


# ---------- HireBriefSchema (선택적 보조 모델) ----------


def test_hire_brief_schema_requires_mission_and_deliverables():
    with pytest.raises(Exception):
        HireBriefSchema.model_validate({"mission": "", "deliverables": []})
    hb = HireBriefSchema.model_validate(
        {
            "mission": "X 작성",
            "deliverables": [{"path": "x.py", "kind": "new"}],
        }
    )
    assert hb.verify is False
    assert hb.allowed_tools is None


def test_hire_brief_seed_files_path_validated():
    with pytest.raises(Exception):
        HireBriefSchema.model_validate(
            {
                "mission": "m",
                "deliverables": [{"path": "x.py", "kind": "new"}],
                "seed_files": ["../escape.py"],
            }
        )


# ---------- validate_decomposer_output (raw 문자열 & dict) ----------

VALID_PLAN_DICT: dict = {
    "sub_goals": [
        {
            "id": "G-001-foo",
            "title": "create foo",
            "deliverables": [{"path": "src/foo.py", "kind": "new"}],
        },
        {
            "id": "G-002-bar",
            "title": "refine bar",
            "deliverables": [{"path": "src/bar.py", "kind": "refine"}],
            "seed_files": ["src/bar.py"],
        },
    ],
    "notes": "ok",
}


def test_validate_decomposer_output_from_dict():
    plan = validate_decomposer_output(VALID_PLAN_DICT)
    assert isinstance(plan, PlanSchema)
    assert len(plan.sub_goals) == 2


def test_validate_decomposer_output_from_json_string():
    plan = validate_decomposer_output(json.dumps(VALID_PLAN_DICT))
    assert len(plan.sub_goals) == 2


def test_validate_decomposer_output_strips_code_fence():
    raw = "```json\n" + json.dumps(VALID_PLAN_DICT) + "\n```"
    plan = validate_decomposer_output(raw)
    assert len(plan.sub_goals) == 2


def test_validate_decomposer_output_with_preamble_text():
    raw = "여기 plan 입니다:\n" + json.dumps(VALID_PLAN_DICT) + "\n끝."
    plan = validate_decomposer_output(raw)
    assert len(plan.sub_goals) == 2


def test_validate_decomposer_output_failure_on_garbage():
    with pytest.raises(ValidationFailure) as ei:
        validate_decomposer_output("not json at all")
    assert "JSON" in ei.value.reason
    assert ei.value.raw == "not json at all"


def test_validate_decomposer_output_failure_on_schema_violation():
    with pytest.raises(ValidationFailure) as ei:
        validate_decomposer_output({"sub_goals": []})
    assert "PlanSchema" in ei.value.reason or "sub_goals" in ei.value.reason


def test_validate_decomposer_output_failure_on_top_level_list():
    with pytest.raises(ValidationFailure) as ei:
        validate_decomposer_output("[1,2,3]")
    # 객체 아님 또는 schema 검증 실패
    assert "객체" in ei.value.reason or "PlanSchema" in ei.value.reason


def test_validation_failure_carries_raw_for_feedback():
    raw_bad = json.dumps({"sub_goals": [{"id": "nope", "title": "x"}]})
    with pytest.raises(ValidationFailure) as ei:
        validate_decomposer_output(raw_bad)
    assert ei.value.raw == raw_bad
    assert ei.value.reason  # non-empty


# ---------- call_decomposer_with_validation (retry 루프) ----------


class _QueueLLM:
    """순차 응답 큐. call(system, user, tier=...) 마다 다음 응답 반환."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    def call(self, system: str, user: str, tier: str = "opus", **_kw) -> str:
        self.calls.append((system, user, tier))
        if not self._responses:
            raise RuntimeError("응답 큐 빔")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def test_retry_loop_succeeds_first_try():
    llm = _QueueLLM([json.dumps(VALID_PLAN_DICT)])
    plan = call_decomposer_with_validation(llm, "sys", "user")
    assert isinstance(plan, PlanSchema)
    assert len(llm.calls) == 1


def test_retry_loop_recovers_after_one_failure():
    llm = _QueueLLM(
        [
            "not json",
            json.dumps(VALID_PLAN_DICT),
        ]
    )
    plan = call_decomposer_with_validation(llm, "sys", "user")
    assert isinstance(plan, PlanSchema)
    assert len(llm.calls) == 2
    # 두 번째 호출의 user prompt 에 직전 에러 reason 이 포함돼야 함
    second_user = llm.calls[1][1]
    assert "이전 시도 실패" in second_user
    assert "JSON" in second_user or "schema" in second_user


def test_retry_loop_raises_after_max_retries():
    """모두 invalid — 총 (max_retries+1) 회 시도 후 ValidationFailure raise."""
    bad_responses = ["bad"] * 10
    llm = _QueueLLM(bad_responses)
    with pytest.raises(ValidationFailure):
        call_decomposer_with_validation(llm, "sys", "user", max_retries=3)
    assert len(llm.calls) == 4  # 1 초기 + 3 재시도


def test_retry_default_max_retries_is_3():
    bad_responses = ["bad"] * 10
    llm = _QueueLLM(bad_responses)
    with pytest.raises(ValidationFailure):
        call_decomposer_with_validation(llm, "sys", "user")
    assert len(llm.calls) == DECOMPOSER_VALIDATE_MAX_RETRIES + 1 == 4


def test_retry_loop_injects_previous_error_into_next_prompt():
    """직전 에러 메시지가 다음 프롬프트의 user 본문에 명시적으로 들어가야 한다."""
    llm = _QueueLLM(
        [
            "{ broken json",
            '{"sub_goals": []}',  # schema 위반
            json.dumps(VALID_PLAN_DICT),
        ]
    )
    plan = call_decomposer_with_validation(llm, "sys", "user")
    assert isinstance(plan, PlanSchema)
    # 2번째 호출 prompt 에 1번째 raw 응답 일부가 들어감
    second_user = llm.calls[1][1]
    assert "broken" in second_user or "JSON" in second_user
    # 3번째 호출 prompt 에 2번째 에러(PlanSchema 검증 실패) 가 들어감
    third_user = llm.calls[2][1]
    assert "PlanSchema" in third_user or "sub_goals" in third_user


def test_retry_loop_handles_llm_exception():
    """LLM 호출 자체가 예외 던지면 ValidationFailure 로 감싸서 retry."""
    llm = _QueueLLM(
        [
            RuntimeError("transient backend error"),
            json.dumps(VALID_PLAN_DICT),
        ]
    )
    plan = call_decomposer_with_validation(llm, "sys", "user")
    assert isinstance(plan, PlanSchema)


def test_retry_loop_invalid_max_retries():
    llm = _QueueLLM([])
    with pytest.raises(ValueError):
        call_decomposer_with_validation(llm, "sys", "user", max_retries=-1)


def test_retry_loop_log_callback_invoked():
    log_lines: list[str] = []
    llm = _QueueLLM(["bad", json.dumps(VALID_PLAN_DICT)])
    call_decomposer_with_validation(
        llm,
        "sys",
        "user",
        log=log_lines.append,
    )
    assert any("검증 실패" in line for line in log_lines)
    assert any("검증 통과" in line for line in log_lines)


# ---------- prune_plan_backups (백업 정리) ----------


def _touch_backup(dir_: Path, suffix: str, mtime: float) -> Path:
    p = dir_ / f"plan.replaced-{suffix}.md"
    p.write_text(f"backup {suffix}\n")
    os.utime(p, (mtime, mtime))
    return p


def test_prune_keeps_default_5_when_7_exist():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        now = time.time()
        for i in range(7):
            _touch_backup(root, str(i), now - (7 - i) * 10)
        # 다른 파일은 건드리지 않아야 함
        (root / "plan.md").write_text("active")
        (root / "unrelated.md").write_text("x")

        prune_plan_backups(root)

        remaining = sorted(p.name for p in root.glob("plan.replaced-*.md"))
        assert len(remaining) == 5
        # 가장 오래된 0, 1 이 삭제됨 (가장 작은 mtime)
        assert "plan.replaced-0.md" not in remaining
        assert "plan.replaced-1.md" not in remaining
        # 가장 최근 6 이 남음
        assert "plan.replaced-6.md" in remaining
        # 무관 파일 보존
        assert (root / "plan.md").exists()
        assert (root / "unrelated.md").exists()


def test_prune_no_op_when_under_keep():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for i in range(3):
            _touch_backup(root, str(i), time.time() - i)
        prune_plan_backups(root, keep=5)
        assert len(list(root.glob("plan.replaced-*.md"))) == 3


def test_prune_keep_zero_deletes_all():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for i in range(3):
            _touch_backup(root, str(i), time.time() - i)
        prune_plan_backups(root, keep=0)
        assert list(root.glob("plan.replaced-*.md")) == []


def test_prune_negative_keep_raises():
    with tempfile.TemporaryDirectory() as d, pytest.raises(ValueError):
        prune_plan_backups(Path(d), keep=-1)


def test_prune_handles_missing_root():
    prune_plan_backups(Path("/nonexistent/never/exists"))  # 에러 없이 no-op


def test_prune_handles_file_as_root():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "file"
        f.write_text("x")
        prune_plan_backups(f)  # 디렉토리 아님 → no-op


def test_prune_mtime_ordering_keeps_newest():
    """mtime 내림차순으로 keep 개 보존. 가장 새 N 개가 살아남아야 함."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        now = time.time()
        # 의도적으로 이름 순서와 mtime 순서를 어긋나게
        _touch_backup(root, "old-name-but-new-mtime", now - 1)
        _touch_backup(root, "new-name-but-old-mtime", now - 1000)
        _touch_backup(root, "middle", now - 500)
        prune_plan_backups(root, keep=2)
        remaining = {p.name for p in root.glob("plan.replaced-*.md")}
        assert "plan.replaced-old-name-but-new-mtime.md" in remaining
        assert "plan.replaced-middle.md" in remaining
        assert "plan.replaced-new-name-but-old-mtime.md" not in remaining


# ---------- 모듈 상수 / 공개 API 노출 ----------


def test_module_constants():
    assert DECOMPOSER_VALIDATE_MAX_RETRIES == 3
    assert PLAN_BACKUP_KEEP == 5


def test_decomposer_validate_max_retries_exposed():
    from core.schemas import DECOMPOSER_VALIDATE_MAX_RETRIES as const

    assert isinstance(const, int)
    assert const >= 1


def test_validation_failure_is_exception_subclass():
    assert issubclass(ValidationFailure, Exception)
    e = ValidationFailure(reason="r", raw="raw-text")
    assert e.reason == "r"
    assert e.raw == "raw-text"
    assert str(e) == "r"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
