"""AgentRegistry — agents.json 인덱스.

<state_dir>/lead/agents.json이 source of truth는 아님 — <state_dir>/agents/{agent_id}/
디렉토리가 진짜 상태고, agents.json은 빠른 조회용 인덱스. 시작 시 disk scan으로
rehydrate 가능.

상태 값: HIRED | RUNNING | WAITING | DONE | FAILED
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


STATUS_VALUES = {"HIRED", "RUNNING", "WAITING", "DONE", "FAILED"}


@dataclass
class AgentRecord:
    agent_id: str
    status: str = "HIRED"
    goal_id: str = ""
    last_msg_id: int = 0
    hired_at: str = ""
    completed_at: str = ""
    last_resume: int = 0  # 재spawn 횟수 (task_id 충돌 방지용)
    last_error: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AgentRegistry:
    def __init__(self, lead_state_dir: Path, agents_root: Path):
        """
        lead_state_dir: <state_dir>/lead/ — agents.json 저장 위치
        agents_root: <state_dir>/agents/ — 에이전트별 디렉토리 root
        """
        self.lead_state_dir = lead_state_dir
        self.agents_root = agents_root
        self.index_path = lead_state_dir / "agents.json"
        lead_state_dir.mkdir(parents=True, exist_ok=True)
        agents_root.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, AgentRecord] = {}
        self._load_or_rehydrate()

    def _load_or_rehydrate(self) -> None:
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text())
                self._records = {
                    aid: AgentRecord(agent_id=aid, **fields)
                    for aid, fields in data.items()
                }
                return
            except (json.JSONDecodeError, TypeError):
                pass
        self.rehydrate()

    def rehydrate(self) -> int:
        """디스크에서 agents/{id}/status, mailbox.md last id를 읽어 인덱스 재구축."""
        from lead.mailbox import parse_messages

        self._records = {}
        for agent_dir in sorted(self.agents_root.iterdir() if self.agents_root.exists() else []):
            if not agent_dir.is_dir():
                continue
            aid = agent_dir.name
            status_file = agent_dir / "status"
            status = status_file.read_text().strip() if status_file.exists() else "HIRED"
            if status not in STATUS_VALUES:
                status = "HIRED"
            msgs = parse_messages(agent_dir / "mailbox.md")
            last_id = max((m.id for m in msgs), default=0)
            self._records[aid] = AgentRecord(
                agent_id=aid,
                status=status,
                last_msg_id=last_id,
                hired_at=msgs[0].ts if msgs else "",
            )
        self.save()
        return len(self._records)

    def save(self) -> None:
        data = {aid: {k: v for k, v in asdict(r).items() if k != "agent_id"}
                for aid, r in self._records.items()}
        # 손상 시 rehydrate()가 디스크(agents/*/status, mailbox.md)에서 복구 가능.
        self.index_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # ---- 조회 ----

    def get(self, agent_id: str) -> Optional[AgentRecord]:
        return self._records.get(agent_id)

    def all(self) -> list[AgentRecord]:
        return list(self._records.values())

    def last_seen_map(self) -> dict[str, int]:
        return {aid: r.last_msg_id for aid, r in self._records.items()}

    def by_status(self, *statuses: str) -> list[AgentRecord]:
        return [r for r in self._records.values() if r.status in statuses]

    def next_agent_id(self, prefix: str = "M") -> str:
        """다음 사용 가능한 ID. M001, M002, ..."""
        existing_nums = []
        for aid in self._records.keys():
            if aid.startswith(prefix):
                try:
                    existing_nums.append(int(aid[len(prefix):]))
                except ValueError:
                    pass
        # 디스크에도 확인 (등록 안 된 디렉토리 있을 수도)
        if self.agents_root.exists():
            for d in self.agents_root.iterdir():
                if d.is_dir() and d.name.startswith(prefix):
                    try:
                        existing_nums.append(int(d.name[len(prefix):]))
                    except ValueError:
                        pass
        n = max(existing_nums, default=0) + 1
        return f"{prefix}{n:03d}"

    # ---- 변경 ----

    def register(self, agent_id: str, goal_id: str = "") -> AgentRecord:
        if agent_id in self._records:
            raise ValueError(f"agent_id 이미 등록됨: {agent_id}")
        rec = AgentRecord(
            agent_id=agent_id,
            status="HIRED",
            goal_id=goal_id,
            hired_at=_now_iso(),
        )
        self._records[agent_id] = rec
        self.save()
        return rec

    def update(self, agent_id: str, **changes) -> AgentRecord:
        if agent_id not in self._records:
            raise KeyError(f"agent not registered: {agent_id}")
        rec = self._records[agent_id]
        for k, v in changes.items():
            if k == "status":
                if v not in STATUS_VALUES:
                    raise ValueError(f"invalid status: {v}")
                if v == "DONE" and not rec.completed_at:
                    rec.completed_at = _now_iso()
            setattr(rec, k, v)
        self.save()
        return rec

    def set_status(self, agent_id: str, status: str) -> None:
        self.update(agent_id, status=status)
        # 디스크 status 파일도 동기화
        sf = self.agents_root / agent_id / "status"
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(status)
