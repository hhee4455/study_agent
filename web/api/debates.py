"""FastAPI APIRouter — /api/debates endpoints (read-only)."""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api")


def get_ws_root() -> Path:
    """Return workspace root. Override via AGENT_WS_ROOT env var or dependency_overrides."""
    return Path(os.environ.get("AGENT_WS_ROOT", str(Path.cwd())))


# ── Pydantic schemas ──────────────────────────────────────────────────────────


class PersonaOut(BaseModel):
    name: str
    stance: str
    content: str


class EscalationOut(BaseModel):
    detected: bool
    snippet: Optional[str] = None


class ConsensusOut(BaseModel):
    reached: bool
    summary: Optional[str] = None


class DebateListItem(BaseModel):
    conflict_id: str
    topic: str
    file: str
    created_at: Optional[str] = None
    consensus_reached: bool
    escalated: bool


class DebateListResponse(BaseModel):
    debates: list[DebateListItem]


class DebateDetail(BaseModel):
    conflict_id: str
    topic: str
    file: str
    raw_markdown: str
    personas: list[PersonaOut]
    escalation: Optional[EscalationOut] = None
    consensus: ConsensusOut


# ── Helpers ───────────────────────────────────────────────────────────────────

# Matches [Agent-A / Pragmatist] markers written by DebatePanel
_BRACKET_PERSONA_RE = re.compile(r"\[([^\]/\n]+?)\s*/\s*([^\]\n]+)\]")


def _validate_conflict_id(conflict_id: str) -> None:
    """Reject path traversal attempts."""
    if ".." in conflict_id or "/" in conflict_id or "\\" in conflict_id:
        raise HTTPException(status_code=400, detail="invalid conflict_id")


def _conflicts_dir(ws_root: Path) -> Path:
    return ws_root / "state" / "lead" / "conflicts"


def _parse_topic(text: str, stem: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return stem


def _parse_personas(text: str) -> list[PersonaOut]:
    """Parse [Name / Role] bracket markers from DebatePanel-format files.

    Aggregates multiple rounds: same-name personas have their content combined.
    Falls back gracefully to empty list when no markers found.
    """
    matches = list(_BRACKET_PERSONA_RE.finditer(text))
    if not matches:
        return []

    order: list[str] = []
    personas: dict[str, PersonaOut] = {}

    for i, m in enumerate(matches):
        name = m.group(1).strip()
        stance = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = text[start:end]
        # Stop content at section boundary (round separator or new ## header)
        for sep in ("\n---", "\n## "):
            pos = raw.find(sep)
            if pos != -1:
                raw = raw[:pos]
        content = raw.strip()

        if name in personas:
            existing = personas[name]
            combined = (existing.content + "\n\n" + content).strip() if content else existing.content
            personas[name] = PersonaOut(name=name, stance=stance, content=combined)
        else:
            order.append(name)
            personas[name] = PersonaOut(name=name, stance=stance, content=content)

    return [personas[n] for n in order]


def _detect_escalation(text: str) -> Optional[EscalationOut]:
    lower = text.lower()
    if "escalat" not in lower:
        return None
    idx = lower.find("escalat")
    snippet = text[max(0, idx - 20) : idx + 80].strip()
    return EscalationOut(detected=True, snippet=snippet)


def _detect_consensus(text: str) -> ConsensusOut:
    lower = text.lower()
    reached = any(marker in lower for marker in ("consensus", "합의", "reached"))
    summary: Optional[str] = None
    if reached:
        m = re.search(r"##\s+Summary\s*\n(.*?)(?:\n##|\Z)", text, re.DOTALL | re.IGNORECASE)
        if m:
            summary = m.group(1).strip() or None
    return ConsensusOut(reached=reached, summary=summary)


def _file_created_at(path: Path) -> Optional[str]:
    try:
        return datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z"
    except OSError:
        return None


def _build_list_item(path: Path) -> DebateListItem:
    stem = path.stem
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    return DebateListItem(
        conflict_id=stem,
        topic=_parse_topic(text, stem),
        file=path.name,
        created_at=_file_created_at(path),
        consensus_reached=_detect_consensus(text).reached,
        escalated=_detect_escalation(text) is not None,
    )


def _build_detail(path: Path) -> DebateDetail:
    stem = path.stem
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    return DebateDetail(
        conflict_id=stem,
        topic=_parse_topic(text, stem),
        file=path.name,
        raw_markdown=text,
        personas=_parse_personas(text),
        escalation=_detect_escalation(text),
        consensus=_detect_consensus(text),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/debates", response_model=DebateListResponse)
def list_debates(ws_root: Path = Depends(get_ws_root)) -> DebateListResponse:
    """List all conflict debate files from state/lead/conflicts/*.md."""
    d = _conflicts_dir(ws_root)
    if not d.exists():
        return DebateListResponse(debates=[])
    items = [
        _build_list_item(p)
        for p in sorted(d.glob("*.md"))
        if not p.name.endswith(".archive.md")
    ]
    return DebateListResponse(debates=items)


@router.get("/debates/{conflict_id}", response_model=DebateDetail)
def get_debate(
    conflict_id: str,
    ws_root: Path = Depends(get_ws_root),
) -> DebateDetail:
    """Get detail for a single conflict debate by conflict_id (file stem)."""
    _validate_conflict_id(conflict_id)
    path = _conflicts_dir(ws_root) / f"{conflict_id}.md"
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"debate not found: {conflict_id}")
    return _build_detail(path)
