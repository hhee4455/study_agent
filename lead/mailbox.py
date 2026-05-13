"""Mailbox — 팀장↔팀원 markdown 메시지 프로토콜.

mailbox.md는 append-only 양방향 스레드. 각 메시지는 HTML 주석 마커로 경계.

Kind 5종:
  - instruction (lead → member): 지시
  - status      (member → lead): 자발적 진행 보고
  - question    (member → lead): 차단성 질문
  - reply       (lead → member): question 응답 (ref=원본 id)
  - delivery    (member → lead): 최종 산출물 보고 (delivery.md에도 미러)

메시지 형식:
    <!-- MSG id=2 from=M003 to=lead kind=question ts=2026-05-13T10:31:05Z -->
    ## Question
    본문 자유 markdown
    <!-- /MSG -->
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


MESSAGE_KINDS = {"instruction", "status", "question", "reply", "delivery"}

# 헤더는 한 줄, 닫는 마커도 한 줄. 본문은 사이 모든 줄.
_HEADER_RE = re.compile(
    r"<!--\s*MSG\s+"
    r"id=(?P<id>\d+)\s+"
    r"from=(?P<from>\S+)\s+"
    r"to=(?P<to>\S+)\s+"
    r"kind=(?P<kind>\S+)"
    r"(?:\s+ref=(?P<ref>\d+))?"
    r"\s+ts=(?P<ts>\S+)\s*-->"
)
_FOOTER = "<!-- /MSG -->"


@dataclass
class Message:
    id: int
    from_: str
    to: str
    kind: str
    ts: str  # ISO8601 UTC
    body: str  # 마커 사이 markdown (앞뒤 공백 제거된 상태)
    ref: Optional[int] = None
    source_path: Optional[Path] = None  # 어느 mailbox.md에서 왔는지

    def is_terminal(self) -> bool:
        return self.kind == "delivery"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_messages(mailbox_path: Path) -> list[Message]:
    """mailbox.md를 파싱해 Message 리스트 반환. 손상된 블록은 건너뜀."""
    if not mailbox_path.exists():
        return []
    text = mailbox_path.read_text(encoding="utf-8")
    messages: list[Message] = []
    pos = 0
    while True:
        m = _HEADER_RE.search(text, pos)
        if not m:
            break
        body_start = m.end()
        footer_idx = text.find(_FOOTER, body_start)
        if footer_idx < 0:
            # 닫는 마커 없으면 손상 — 무시하고 진행
            pos = body_start
            continue
        body = text[body_start:footer_idx].strip("\n")
        try:
            ref = int(m.group("ref")) if m.group("ref") else None
            messages.append(Message(
                id=int(m.group("id")),
                from_=m.group("from"),
                to=m.group("to"),
                kind=m.group("kind"),
                ts=m.group("ts"),
                body=body,
                ref=ref,
                source_path=mailbox_path,
            ))
        except (ValueError, KeyError):
            pass
        pos = footer_idx + len(_FOOTER)
    return messages


def next_msg_id(mailbox_path: Path) -> int:
    msgs = parse_messages(mailbox_path)
    return (max((m.id for m in msgs), default=0)) + 1


def append_message(
    mailbox_path: Path,
    *,
    from_: str,
    to: str,
    kind: str,
    body: str,
    ref: Optional[int] = None,
    ts: Optional[str] = None,
) -> Message:
    """mailbox.md에 새 메시지 append. id는 자동.

    `body`는 markdown 자유 형식. 코드 블록에 _FOOTER 문자열이 포함되면 파싱이
    깨질 수 있으므로 작성자는 메시지 본문에 정확히 `<!-- /MSG -->` 시퀀스를
    넣지 않도록 한다.
    """
    if kind not in MESSAGE_KINDS:
        raise ValueError(f"unknown kind: {kind} (expected one of {MESSAGE_KINDS})")
    mailbox_path.parent.mkdir(parents=True, exist_ok=True)
    msg_id = next_msg_id(mailbox_path)
    ts = ts or _now_iso()

    ref_attr = f" ref={ref}" if ref is not None else ""
    header = (
        f"<!-- MSG id={msg_id} from={from_} to={to} kind={kind}{ref_attr} ts={ts} -->"
    )
    block = f"{header}\n{body.rstrip()}\n{_FOOTER}\n\n"

    # append (with leading blank line if file already has content)
    if mailbox_path.exists() and mailbox_path.stat().st_size > 0:
        with mailbox_path.open("a", encoding="utf-8") as f:
            f.write(block)
    else:
        mailbox_path.write_text(block, encoding="utf-8")

    return Message(
        id=msg_id, from_=from_, to=to, kind=kind, ts=ts,
        body=body.rstrip(), ref=ref, source_path=mailbox_path,
    )


def scan_new(
    agents_root: Path, last_seen: dict[str, int]
) -> list[Message]:
    """모든 에이전트 mailbox.md를 훑어 last_seen 이후의 메시지 반환.

    `agents_root`: meta/state/agents/ 디렉토리
    `last_seen`: {agent_id: last_msg_id_processed}
    """
    out: list[Message] = []
    if not agents_root.exists():
        return out
    for agent_dir in sorted(agents_root.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent_id = agent_dir.name
        mailbox = agent_dir / "mailbox.md"
        if not mailbox.exists():
            continue
        threshold = last_seen.get(agent_id, 0)
        for m in parse_messages(mailbox):
            if m.id > threshold:
                # source_path는 이미 parse_messages가 채움; agent_id 식별은 부모 이름
                out.append(m)
    return out


def detect_terminal_status(text: str) -> Optional[str]:
    """팀원 세션 출력 끝부분에서 [STATUS:DONE|WAITING|FAILED] 토큰 감지.

    매칭 안 되면 None. 멤버는 출력 마지막 줄에 정확히 토큰 하나만 둬야 함.
    """
    tail = text[-512:]
    m = re.search(r"\[STATUS:(DONE|WAITING|FAILED)\]", tail)
    return m.group(1) if m else None
