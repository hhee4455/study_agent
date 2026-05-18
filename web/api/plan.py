"""FastAPI APIRouter — /api/plan endpoint (read-only)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lead.team_lead import Goal, parse_plan

router = APIRouter(prefix="/api")


def get_ws_root() -> Path:
    """Return workspace root. Override via AGENT_WS_ROOT env var or dependency_overrides."""
    return Path(os.environ.get("AGENT_WS_ROOT", str(Path.cwd())))


class GoalOut(BaseModel):
    """Single goal serialization schema."""

    id: str
    title: str
    assigned: Optional[str]
    done: bool
    model: Optional[str]


class PlanResponse(BaseModel):
    """Response schema for GET /api/plan."""

    goals: list[GoalOut]


def _goal_to_out(g: Goal) -> GoalOut:
    return GoalOut(
        id=g.id,
        title=g.title,
        assigned=g.assigned or None,
        done=g.done,
        model=None,
    )


@router.get("/plan", response_model=PlanResponse)
def get_plan(ws_root: Path = Depends(get_ws_root)) -> PlanResponse:
    """Return parsed plan goals from state/lead/plan.md."""
    plan_md = ws_root / "state" / "lead" / "plan.md"
    return PlanResponse(goals=[_goal_to_out(g) for g in parse_plan(plan_md)])
