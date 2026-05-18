"""FastAPI APIRouter — GET /api/events/stream SSE endpoint."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..events_stream import tail_events

# /api 프리픽스로 다른 라우터(plan/members/debates/budget) 와 일관성 맞춤.
# 기존 server.py 가 prefix 없이 include 하던 회귀를 막아 404 방지.
router = APIRouter(prefix="/api")


def get_events_path() -> Path:
    """Return path to events.jsonl. Override via AGENT_WS_ROOT or dependency_overrides."""
    ws_root = Path(os.environ.get("AGENT_WS_ROOT", str(Path.cwd())))
    return ws_root / "state" / "lead" / "events.jsonl"


@router.get("/events/stream")
async def stream_events(events_path: Path = Depends(get_events_path)):
    """Stream new lines from events.jsonl as Server-Sent Events."""
    return StreamingResponse(
        tail_events(events_path),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
