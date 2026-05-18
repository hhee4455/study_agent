"""FastAPI APIRouter — /api/llm-calls endpoint (read-only)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from web.api.members import get_ws_root

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _scan_llm_logs(
    ws_root: Path,
    member: Optional[str],
    kind: Optional[str],
) -> list[dict[str, Any]]:
    logs_dir = ws_root / "state" / "llm_logs"
    if not logs_dir.is_dir():
        return []

    calls: list[dict[str, Any]] = []
    for path in sorted(logs_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("llm_logs/%s read error: %s", path.name, exc)
            continue

        if not isinstance(data, dict):
            logger.warning("llm_logs/%s: expected JSON object, skipping", path.name)
            continue

        if member is not None and data.get("member") != member:
            continue
        if kind is not None and data.get("kind") != kind:
            continue

        calls.append({"filename": path.name, **data})

    return calls


@router.get("/llm-calls")
def list_llm_calls(
    member: Optional[str] = None,
    kind: Optional[str] = None,
    ws_root: Path = Depends(get_ws_root),
) -> dict[str, Any]:
    return {"calls": _scan_llm_logs(ws_root, member=member, kind=kind)}


def _validate_filename(filename: str) -> None:
    """경로 이스케이프(.. / 슬래시) 거부. HTTP 정규화가 못 잡는 케이스 차단."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")


@router.get("/llm-calls/{filename}")
def get_llm_call(
    filename: str,
    ws_root: Path = Depends(get_ws_root),
) -> dict[str, Any]:
    """단건 조회 — 본문 + 메타. parse 실패 시 content 는 raw str, parse_error 동봉."""
    _validate_filename(filename)
    path = ws_root / "state" / "llm_logs" / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")

    raw = path.read_text(encoding="utf-8")
    size_bytes = path.stat().st_size
    result: dict[str, Any] = {"filename": filename, "size_bytes": size_bytes}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        result["content"] = raw
        result["parse_error"] = str(exc)
        return result

    if isinstance(data, dict):
        result["member"] = data.get("member")
        result["kind"] = data.get("kind")
        # 테스트가 기대하는 `ts` 필드 — 원본의 `timestamp` 값을 옮긴다.
        result["ts"] = data.get("timestamp") or data.get("ts")
        result["content"] = data
    else:
        result["content"] = data
    return result
