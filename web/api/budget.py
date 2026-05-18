"""FastAPI APIRouter — /api/budget endpoint (read-only)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from web.api.members import get_ws_root

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


class ModelStats(BaseModel):
    usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0


class BudgetResponse(BaseModel):
    total_usd: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    by_model: dict[str, ModelStats] = {}
    limit_usd: Optional[float] = None
    limit_progress: Optional[float] = None
    hourly_usd: Optional[float] = None
    eta_seconds_to_limit: Optional[float] = None
    started_at: Optional[str] = None
    elapsed_sec: Optional[float] = None


def _load_raw(ws_root: Path) -> dict:
    path = ws_root / "state" / "budget.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("budget.json parse error: %s", exc)
        return {}


def _safe_float(v: object, default: float = 0.0) -> float:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return default


def _safe_int(v: object, default: int = 0) -> int:
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return default


def _started_at_to_unix(value: object) -> float:
    """ISO-8601 string or unix epoch float → unix epoch. Returns 0.0 on failure."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        s = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return 0.0
    return 0.0


@router.get("/budget", response_model=BudgetResponse)
def get_budget(ws_root: Path = Depends(get_ws_root)) -> BudgetResponse:
    raw = _load_raw(ws_root)

    # Detect schema: new G-009 (has "totals") vs legacy BudgetManager checkpoint
    totals = raw.get("totals")
    if isinstance(totals, dict):
        total_usd = _safe_float(totals.get("usd"))
        total_tokens_in = _safe_int(totals.get("input_tokens"))
        total_tokens_out = _safe_int(totals.get("output_tokens"))
        by_model: dict[str, ModelStats] = {}
        by_model_raw = raw.get("by_model")
        if isinstance(by_model_raw, dict):
            for model, bucket in by_model_raw.items():
                if not isinstance(bucket, dict):
                    continue
                by_model[model] = ModelStats(
                    usd=_safe_float(bucket.get("usd")),
                    tokens_in=_safe_int(bucket.get("input_tokens")),
                    tokens_out=_safe_int(bucket.get("output_tokens")),
                    calls=_safe_int(bucket.get("calls")),
                )
    else:
        # Legacy schema: cost_usd / tokens_in / tokens_out / turns
        total_usd = _safe_float(raw.get("cost_usd"))
        total_tokens_in = _safe_int(raw.get("tokens_in"))
        total_tokens_out = _safe_int(raw.get("tokens_out"))
        by_model = {}

    # limit_usd (new schema only)
    limit_usd: Optional[float] = None
    limit_raw = raw.get("limit_usd")
    if limit_raw is not None:
        v = _safe_float(limit_raw)
        if v > 0:
            limit_usd = v

    # started_at / elapsed_sec
    started_at_raw = raw.get("started_at")
    started_at: Optional[str] = str(started_at_raw) if started_at_raw is not None else None
    elapsed_sec: Optional[float] = None
    if started_at_raw is not None:
        unix_ts = _started_at_to_unix(started_at_raw)
        if unix_ts > 0:
            elapsed_sec = max(0.0, time.time() - unix_ts)

    # Derived: average hourly cost since started_at
    hourly_usd: Optional[float] = None
    if elapsed_sec is not None and elapsed_sec > 0:
        hourly_usd = total_usd / (elapsed_sec / 3600.0)

    # Derived: limit progress (can exceed 1.0 when over budget)
    limit_progress: Optional[float] = None
    if limit_usd is not None:
        limit_progress = total_usd / limit_usd

    # Derived: ETA seconds until limit is hit (0.0 when already exceeded)
    eta_seconds_to_limit: Optional[float] = None
    if limit_usd is not None and hourly_usd is not None and hourly_usd > 0:
        remaining = limit_usd - total_usd
        eta_seconds_to_limit = max(0.0, remaining / hourly_usd * 3600.0)

    return BudgetResponse(
        total_usd=total_usd,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        by_model=by_model,
        limit_usd=limit_usd,
        limit_progress=limit_progress,
        hourly_usd=hourly_usd,
        eta_seconds_to_limit=eta_seconds_to_limit,
        started_at=started_at,
        elapsed_sec=elapsed_sec,
    )
