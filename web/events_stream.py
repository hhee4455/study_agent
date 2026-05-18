"""Async tail generator for events.jsonl → SSE formatted strings."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator


async def tail_events(path: Path) -> AsyncIterator[str]:
    """Yield SSE data lines for each new line appended to *path*.

    Skips content already present when called (seek-to-end on start).
    Polls every 0.5 s. Terminates cleanly on CancelledError or GeneratorExit.
    """
    offset = path.stat().st_size if path.exists() else 0
    try:
        while True:
            await asyncio.sleep(0.5)
            if not path.exists():
                continue
            size = path.stat().st_size
            if size <= offset:
                continue
            with path.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read(size - offset)
            offset = size
            for raw in chunk.decode("utf-8").splitlines():
                raw = raw.strip()
                if raw:
                    yield f"data: {raw}\n\n"
    except asyncio.CancelledError:
        pass
