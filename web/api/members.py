"""FastAPI APIRouter — /api/members endpoints (read-only)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from lead.mailbox import Message, parse_messages
from lead.registry import AgentRecord, AgentRegistry

router = APIRouter(prefix="/api")


def get_ws_root() -> Path:
    """Return workspace root. Override via AGENT_WS_ROOT env var or dependency_overrides."""
    return Path(os.environ.get("AGENT_WS_ROOT", str(Path.cwd())))


def _get_registry(ws_root: Path = Depends(get_ws_root)) -> AgentRegistry:
    return AgentRegistry(
        lead_state_dir=ws_root / "state" / "lead",
        agents_root=ws_root / "state" / "agents",
    )


def _record_to_dict(rec: AgentRecord) -> dict[str, Any]:
    return dataclasses.asdict(rec)


def _message_to_dict(msg: Message) -> dict[str, Any]:
    return {
        "id": msg.id,
        "from": msg.from_,
        "to": msg.to,
        "kind": msg.kind,
        "ts": msg.ts,
        "body": msg.body,
        "ref": msg.ref,
    }


@router.get("/members")
def list_members(
    registry: AgentRegistry = Depends(_get_registry),
) -> list[dict[str, Any]]:
    return [_record_to_dict(r) for r in registry.all()]


@router.get("/members/{agent_id}")
def get_member(
    agent_id: str,
    registry: AgentRegistry = Depends(_get_registry),
) -> dict[str, Any]:
    rec = registry.get(agent_id)
    if rec is None:
        raise HTTPException(404, detail=f"agent not found: {agent_id}")
    return _record_to_dict(rec)


@router.get("/members/{agent_id}/brief")
def get_brief(
    agent_id: str,
    ws_root: Path = Depends(get_ws_root),
    registry: AgentRegistry = Depends(_get_registry),
) -> dict[str, Any]:
    if registry.get(agent_id) is None:
        raise HTTPException(404, detail=f"agent not found: {agent_id}")
    path = ws_root / "state" / "agents" / agent_id / "brief.md"
    if not path.exists():
        raise HTTPException(404, detail=f"brief.md not found for {agent_id}")
    return {"path": str(path), "content": path.read_text(encoding="utf-8")}


@router.get("/members/{agent_id}/mailbox")
def get_mailbox(
    agent_id: str,
    ws_root: Path = Depends(get_ws_root),
    registry: AgentRegistry = Depends(_get_registry),
) -> list[dict[str, Any]]:
    if registry.get(agent_id) is None:
        raise HTTPException(404, detail=f"agent not found: {agent_id}")
    path = ws_root / "state" / "agents" / agent_id / "mailbox.md"
    return [_message_to_dict(m) for m in parse_messages(path)]


@router.get("/members/{agent_id}/delivery")
def get_delivery(
    agent_id: str,
    ws_root: Path = Depends(get_ws_root),
    registry: AgentRegistry = Depends(_get_registry),
) -> dict[str, Any]:
    if registry.get(agent_id) is None:
        raise HTTPException(404, detail=f"agent not found: {agent_id}")
    path = ws_root / "state" / "agents" / agent_id / "delivery.md"
    if not path.exists():
        raise HTTPException(404, detail=f"delivery.md not found for {agent_id}")
    return {"path": str(path), "content": path.read_text(encoding="utf-8")}
