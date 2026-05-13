"""Incidents — 운영 이벤트 JSONL 어펜더.

무인 운영 후 운영자 디버깅용. 모든 P0/P1 경로(rate limit, 작업 예외,
토론 트리거, harness regression 등)가 공통으로 사용.

health.py:_record_incident의 패턴을 일반화. health도 이걸 통해 기록.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class Incidents:
    """JSONL 어펜더. 한 줄 = 한 이벤트.

    실패가 시스템을 멈추지 않도록 설계 (OSError는 삼킴).
    """

    def __init__(self, state_dir: Path, filename: str = "incidents.jsonl"):
        self.path = state_dir / filename
        state_dir.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, **fields: Any) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": kind,
            **fields,
        }
        try:
            with self.path.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass
