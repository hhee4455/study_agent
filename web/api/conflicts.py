"""FastAPI APIRouter — /api/conflicts endpoints (read-only)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..state_reader import iter_conflict_files, read_conflict_content

router = APIRouter(prefix="/api")


def get_ws_root() -> Path:
    return Path(os.environ.get("AGENT_WS_ROOT", str(Path.cwd())))


def _file_meta(ws_root: Path, filename: str) -> dict:
    path = ws_root / "state" / "lead" / "conflicts" / filename
    try:
        stat = path.stat()
        return {
            "filename": filename,
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
        }
    except OSError:
        return {"filename": filename, "size_bytes": 0, "modified_at": None}


@router.get("/conflicts")
def list_conflicts(ws_root: Path = Depends(get_ws_root)) -> dict:
    return {"files": [_file_meta(ws_root, name) for name in iter_conflict_files(ws_root)]}


@router.get("/conflicts/{filename}")
def get_conflict(filename: str, ws_root: Path = Depends(get_ws_root)) -> dict:
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    try:
        content = read_conflict_content(ws_root, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"not found: {filename}")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid filename")
    return {"filename": filename, "content": content}
