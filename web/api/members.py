"""FastAPI APIRouter — /api/members stub (exposes get_ws_root for shared use)."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api")


def get_ws_root() -> Path:
    return Path(os.environ.get("AGENT_WS_ROOT", str(Path.cwd())))
