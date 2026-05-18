"""Read-only state reader with 5-second TTL cache."""
from __future__ import annotations

import dataclasses
import time
from datetime import datetime
from pathlib import Path

from lead.dashboard import collect_state

_CACHE: dict = {"ts": 0.0, "key": None, "value": None}


def _to_jsonable(obj: object) -> object:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, set):
        return [_to_jsonable(v) for v in sorted(obj, key=str)]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def get_state(ws_root: Path, now: float | None = None, ttl_sec: float = 5.0) -> dict:
    t = now if now is not None else time.monotonic()
    key = str(ws_root)
    if _CACHE["key"] == key and (t - _CACHE["ts"]) < ttl_sec:
        return _CACHE["value"]
    result = _to_jsonable(collect_state(ws_root))
    _CACHE.update({"ts": t, "key": key, "value": result})
    return result


def invalidate_cache() -> None:
    _CACHE.update({"ts": 0.0, "key": None, "value": None})
