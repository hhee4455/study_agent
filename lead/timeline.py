"""TimelineRenderer — 사람 친화 timeline.md 생성.

raw 데이터 (보존):
  - <state_dir>/lead/events.jsonl   ← 팀장이 emit() 호출로 직접 기록 (hire, reply, verify, merge)
  - <state_dir>/agents/{id}/mailbox.md ← 메일박스 메시지
  - <state_dir>/session_logs/{task_id}/stream.jsonl ← claude -p NDJSON stream

렌더링 (사람용): <state_dir>/lead/timeline.md
  - 모든 이벤트를 timestamp 순 정렬
  - 한 줄당 한 이벤트 (grep/scroll 친화)

전략: 매 tick마다 full rebuild. 멤버 수가 적고 파일 작아서 충분.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class TimelineEntry:
    ts: str
    actor: str  # "lead" | agent_id | "system"
    icon: str  # ▶ ■ ★ ? ✓ ✗ ⚒ etc
    text: str


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate(text: str, n: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


class TimelineRenderer:
    def __init__(self, lead_state_dir: Path, agents_root: Path, session_logs_root: Path):
        """
        lead_state_dir: <state_dir>/lead/
        agents_root: <state_dir>/agents/
        session_logs_root: <state_dir>/session_logs/
        """
        self.lead_state_dir = lead_state_dir
        self.agents_root = agents_root
        self.session_logs_root = session_logs_root
        self.events_path = lead_state_dir / "events.jsonl"
        self.timeline_path = lead_state_dir / "timeline.md"
        lead_state_dir.mkdir(parents=True, exist_ok=True)
        # 인메모리 미러 — 단위 테스트가 디스크 IO 없이 검증할 수 있게.
        # run 1회 분량(수백~수천 이벤트)이라 메모리 부담 없음.
        self.events: list[dict[str, Any]] = []

    # ---- 팀장이 호출하는 emitter ----

    def emit(self, actor: str, kind: str, **fields: object) -> None:
        """팀장이 직접 기록할 이벤트 (hire, reply, verify, merge, error 등)."""
        rec: dict[str, Any] = {"ts": _now_iso(), "actor": actor, "kind": kind, **fields}
        self.events.append(rec)
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- 렌더링 ----

    def _needs_rerender(self) -> bool:
        """timeline.md 보다 새로운 입력이 있을 때만 full rebuild. idle tick 시 I/O 절약."""
        if not self.timeline_path.exists():
            return True
        threshold = self.timeline_path.stat().st_mtime
        if self.events_path.exists() and self.events_path.stat().st_mtime > threshold:
            return True
        for root in (self.agents_root, self.session_logs_root):
            if not root.exists():
                continue
            for d in root.iterdir():
                if not d.is_dir():
                    continue
                # mailbox.md 또는 stream.jsonl 둘 중 어느 것이라도
                for name in ("mailbox.md", "stream.jsonl"):
                    f = d / name
                    if f.exists() and f.stat().st_mtime > threshold:
                        return True
        return False

    def render(self) -> Path:
        if not self._needs_rerender():
            return self.timeline_path
        entries: list[TimelineEntry] = []
        entries.extend(self._from_events())
        entries.extend(self._from_mailboxes())
        entries.extend(self._from_session_streams())

        entries.sort(key=lambda e: (e.ts, e.actor))

        lines = ["# Timeline", "", f"_렌더: {_now_iso()}_", ""]
        last_actor = None
        for e in entries:
            if e.actor != last_actor:
                lines.append("")
                lines.append(f"## {e.actor}")
                last_actor = e.actor
            lines.append(f"- `{e.ts}` {e.icon} {e.text}")
        self.timeline_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.timeline_path

    # ---- 소스별 파싱 ----

    def _from_events(self) -> Iterable[TimelineEntry]:
        if not self.events_path.exists():
            return []
        out: list[TimelineEntry] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = e.get("kind", "")
            actor = e.get("actor", "lead")
            ts = e.get("ts", "")
            icon, text = self._render_event(kind, e)
            out.append(TimelineEntry(ts=ts, actor=actor, icon=icon, text=text))
        return out

    @staticmethod
    def _render_event(kind: str, e: dict[str, Any]) -> tuple[str, str]:
        if kind == "hire":
            return "⚒", f'채용 {e.get("agent_id")} → 목표 "{_truncate(e.get("goal", ""), 80)}"'
        if kind == "reply":
            summary = _truncate(e.get("summary", ""), 100)
            return (
                "★",
                f'reply → {e.get("to")} (ref={e.get("ref")}): "{summary}"',
            )
        if kind == "verify_pass":
            return "✓", f"{e.get('agent_id')} verify pass ({e.get('checks', 0)} checks)"
        if kind == "verify_fail":
            return "✗", f"{e.get('agent_id')} verify fail: {_truncate(e.get('detail', ''), 120)}"
        if kind == "merge":
            return "⇨", (
                f"merge {e.get('agent_id')}: copied={e.get('copied', 0)} "
                f"conflicts={e.get('conflicts', 0)}"
            )
        if kind == "fire":
            return "⊘", f"{e.get('agent_id')} 해고: {_truncate(e.get('reason', ''), 100)}"
        if kind == "plan_update":
            return "✎", f"plan.md 갱신: {_truncate(e.get('note', ''), 120)}"
        if kind == "debate_decided":
            return "⚖", (
                f"토론 결정 ({e.get('debate_id', '?')}) for {e.get('agent_id')}: "
                f'"{_truncate(e.get("summary", ""), 120)}"'
            )
        if kind == "code_janitor":
            return "🧹", (
                f"code-janitor: archived={e.get('archived', 0)} "
                f"kept={e.get('kept', 0)} → {e.get('archive_dir', '')}"
            )
        if kind == "conflict_debated":
            return "🤝", (
                f"충돌 토론 ({e.get('debate_id', '?')}) "
                f"file={e.get('file', '?')} agent={e.get('agent_id', '?')}"
            )
        if kind == "error":
            return "⚠", f"{actor_or_blank(e)} error: {_truncate(e.get('error', ''), 150)}"
        return "·", f"{kind}: {_truncate(json.dumps(e, ensure_ascii=False), 150)}"

    def _from_mailboxes(self) -> Iterable[TimelineEntry]:
        from lead.mailbox import parse_messages

        out: list[TimelineEntry] = []
        if not self.agents_root.exists():
            return out
        for agent_dir in sorted(self.agents_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            for m in parse_messages(agent_dir / "mailbox.md"):
                if m.kind == "instruction":
                    icon = "★"
                    text = f'{m.from_} → {m.to} (instruction #{m.id}): "{_truncate(m.body, 120)}"'
                    actor = m.to
                elif m.kind == "question":
                    icon = "?"
                    text = f'{m.from_} → {m.to} (question #{m.id}): "{_truncate(m.body, 120)}"'
                    actor = m.from_
                elif m.kind == "reply":
                    icon = "↩"
                    body_preview = _truncate(m.body, 120)
                    text = f'{m.from_} → {m.to} (reply #{m.id} ref={m.ref}): "{body_preview}"'
                    actor = m.to
                elif m.kind == "status":
                    icon = "·"
                    text = f'{m.from_} status #{m.id}: "{_truncate(m.body, 120)}"'
                    actor = m.from_
                elif m.kind == "delivery":
                    icon = "🏁"
                    text = f'{m.from_} delivery #{m.id}: "{_truncate(m.body, 120)}"'
                    actor = m.from_
                else:
                    continue
                out.append(TimelineEntry(ts=m.ts, actor=actor, icon=icon, text=text))
        return out

    def _from_session_streams(self) -> Iterable[TimelineEntry]:
        out: list[TimelineEntry] = []
        if not self.session_logs_root.exists():
            return out
        for task_dir in sorted(self.session_logs_root.iterdir()):
            if not task_dir.is_dir():
                continue
            stream = task_dir / "stream.jsonl"
            if not stream.exists():
                continue
            actor = self._actor_from_task_id(task_dir.name)
            for entry in self._parse_stream(stream, actor, task_dir.name):
                out.append(entry)
        return out

    @staticmethod
    def _actor_from_task_id(task_id: str) -> str:
        # task_id 예: "M001" 또는 "M001-r2" → actor=M001
        return task_id.split("-", 1)[0] if task_id else "?"

    @staticmethod
    def _parse_stream(path: Path, actor: str, task_id: str) -> list[TimelineEntry]:
        entries: list[TimelineEntry] = []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return entries
        # stream.jsonl에는 ts가 명시 안 됨 — 파일 mtime을 base로, 라인 순서로 ms 더함
        base_mtime = path.stat().st_mtime
        for i, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = datetime.fromtimestamp(base_mtime - 1 + i * 0.001, tz=UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            etype = evt.get("type")
            rendered = TimelineRenderer._render_stream_event(evt, etype, actor, task_id)
            if rendered:
                icon, text2 = rendered
                entries.append(TimelineEntry(ts=ts, actor=actor, icon=icon, text=text2))
        return entries

    @staticmethod
    def _render_stream_event(
        evt: dict[str, Any], etype: str, actor: str, task_id: str
    ) -> tuple[str, str] | None:
        if etype == "system":
            model = evt.get("model") or evt.get("subtype") or "?"
            return "▶", f"세션 시작 ({task_id}, model={model})"
        if etype == "assistant":
            msg = evt.get("message") or {}
            content = msg.get("content") or []
            parts = []
            for c in content:
                ctype = c.get("type")
                if ctype == "text":
                    txt = c.get("text", "").strip()
                    if txt:
                        parts.append(("·", f"{_truncate(txt, 160)}"))
                elif ctype == "tool_use":
                    parts.append(TimelineRenderer._render_tool_use(c))
            if not parts:
                return None
            # 여러 part 있으면 마지막만 (timeline 너무 길어지는 거 방지)
            icon, t = parts[-1]
            return icon, t
        if etype == "user":
            # tool_result들
            msg = evt.get("message") or {}
            content = msg.get("content") or []
            for c in content:
                if c.get("type") == "tool_result":
                    is_err = c.get("is_error", False)
                    output = c.get("content", "")
                    if isinstance(output, list):
                        output = " ".join(x.get("text", "") for x in output if isinstance(x, dict))
                    return ("✗" if is_err else "↳"), f"tool result: {_truncate(str(output), 140)}"
            return None
        if etype == "result":
            if evt.get("is_error"):
                return "■", f"세션 오류 ({task_id}): {_truncate(evt.get('result', ''), 140)}"
            cost = evt.get("total_cost_usd", 0.0)
            turns = evt.get("num_turns", "?")
            return "■", f"세션 종료 ({task_id}, cost=${cost:.4f}, turns={turns})"
        return None

    @staticmethod
    def _render_tool_use(c: dict[str, Any]) -> tuple[str, str]:
        name = c.get("name", "?")
        inp = c.get("input", {}) or {}
        if name == "Bash":
            cmd = inp.get("command", "")
            return "$", f"$ {_truncate(cmd, 140)}"
        if name in ("Edit", "Write"):
            path = inp.get("file_path", "?")
            return "✎", f"{name} {path}"
        if name == "Read":
            path = inp.get("file_path", "?")
            return "📖", f"Read {path}"
        # 기타
        return "🛠", f"{name}({_truncate(json.dumps(inp, ensure_ascii=False), 100)})"


def actor_or_blank(e: dict[str, Any]) -> str:
    val = e.get("actor", "")
    return str(val) if val is not None else ""
