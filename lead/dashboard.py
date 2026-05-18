"""Dashboard — state/ 디렉토리에서 멤버/goal/충돌/예산 요약을 생성.

team_lead 의 30초 주기 상태 루프에서 호출 가능하도록 순수·재진입 함수로 설계.

소스 (모두 state_dir 기준):
  - state/budget.json                ← 전체 USD/tokens/turns
  - state/lead/plan.md               ← 남은/완료 goal
  - state/lead/agents.json           ← 멤버 인덱스 (없으면 agents/ 디스크 스캔)
  - state/agents/{M-id}/status       ← 디스크의 상태 파일 (rehydrate fallback)
  - state/agents/{M-id}/mailbox.md   ← 마지막 메시지 ts
  - state/lead/conflicts/*.md        ← 미해결 충돌 큐
  - state/lead/events.jsonl          ← (옵션) 모델별 비용 — 라인에 model 필드 있으면 집계

산출: state/dashboard.md
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "BudgetSummary",
    "ConflictEntry",
    "DashboardState",
    "GoalRow",
    "MemberRow",
    "collect_state",
    "render_dashboard",
    "write_dashboard",
]


_log = logging.getLogger(__name__)

# plan.md 의 goal 한 줄 형식 (team_lead.GOAL_LINE_RE 와 동일).
_GOAL_LINE_RE = re.compile(
    r"^- \[(?P<done>[ xX])\] (?P<id>G-[A-Za-z0-9_-]+): "
    r"(?P<title>.+?)(?:\s+\(assigned: (?P<assigned>[A-Za-z0-9_-]+)\))?\s*$"
)

# mailbox.md 헤더 — ts 만 필요해서 최소 필드만 캡처.
_MSG_HEADER_RE = re.compile(
    r"<!--\s*MSG\s+id=(?P<id>\d+)\s+from=\S+\s+to=\S+\s+kind=\S+"
    r"(?:\s+ref=\d+)?\s+ts=(?P<ts>\S+)\s*-->"
)


@dataclass
class MemberRow:
    """대시보드 멤버 표 한 줄."""

    agent_id: str
    status: str
    goal_id: str
    last_msg_ts: str
    cost_usd: float


@dataclass
class GoalRow:
    """남은(미완료) goal 표 한 줄."""

    id: str
    title: str
    assigned: str
    done: bool


@dataclass
class ConflictEntry:
    """state/lead/conflicts/ 의 미해결 충돌 파일 한 건."""

    name: str
    mtime_iso: str


@dataclass
class BudgetSummary:
    """예산 집계 — state/budget.json + events.jsonl 모델별 보조."""

    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    turns: int = 0
    elapsed_h: float = 0.0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class DashboardState:
    """render_dashboard 입력 — collect_state 의 출력."""

    generated_at: str
    members: list[MemberRow] = field(default_factory=list)
    goals_pending: list[GoalRow] = field(default_factory=list)
    goals_done_count: int = 0
    conflicts: list[ConflictEntry] = field(default_factory=list)
    budget: BudgetSummary = field(default_factory=BudgetSummary)
    goals_completed_last_hour: int = 0


# ---------- 진입점 (파일 I/O) ----------


def collect_state(ws_root: Path) -> DashboardState:
    """ws_root/state 하위 파일을 읽어 DashboardState 를 만든다.

    파일이 없거나 깨졌으면 해당 섹션은 빈 값으로 둔다 — 절대 raise 하지 않음.
    """
    state_dir = ws_root / "state"
    lead_dir = state_dir / "lead"
    agents_root = state_dir / "agents"
    conflicts_dir = lead_dir / "conflicts"

    members = _collect_members(lead_dir / "agents.json", agents_root)
    goals_pending, goals_done = _collect_goals(lead_dir / "plan.md")
    conflicts = _collect_conflicts(conflicts_dir)
    budget = _collect_budget(state_dir / "budget.json", lead_dir / "events.jsonl")
    recent_completions = _count_recent_completions(lead_dir / "agents.json")

    return DashboardState(
        generated_at=_now_iso(),
        members=members,
        goals_pending=goals_pending,
        goals_done_count=goals_done,
        conflicts=conflicts,
        budget=budget,
        goals_completed_last_hour=recent_completions,
    )


def render_dashboard(state: DashboardState) -> str:
    """DashboardState → markdown 문자열 (파일 I/O 없음, 순수 함수)."""
    lines: list[str] = []
    lines.append("# Dashboard")
    lines.append("")
    lines.append(f"_렌더: {state.generated_at}_")
    lines.append("")
    lines.extend(_render_summary(state))
    lines.append("")
    lines.extend(_render_members(state.members))
    lines.append("")
    lines.extend(_render_goals(state.goals_pending, state.goals_done_count))
    lines.append("")
    lines.extend(_render_conflicts(state.conflicts))
    lines.append("")
    lines.extend(_render_budget(state.budget))
    lines.append("")
    lines.append(_format_eta(len(state.goals_pending), state.goals_completed_last_hour))
    return "\n".join(lines) + "\n"


def write_dashboard(ws_root: Path) -> Path:
    """collect_state → render_dashboard → ws_root/state/dashboard.md 기록. 경로 반환."""
    state = collect_state(ws_root)
    md = render_dashboard(state)
    out = ws_root / "state" / "dashboard.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    return out


# ---------- 수집 helpers ----------


def _collect_members(agents_json: Path, agents_root: Path) -> list[MemberRow]:
    """agents.json 우선, 깨졌으면 agents/ 디스크 스캔으로 fallback."""
    rows: dict[str, MemberRow] = {}
    index: dict[str, dict[str, object]] = {}

    if agents_json.exists():
        try:
            raw = json.loads(agents_json.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                index = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("agents.json 읽기 실패 — 디스크 스캔으로 fallback: %s", e)

    for aid, rec in index.items():
        rows[aid] = MemberRow(
            agent_id=aid,
            status=_safe_str(rec.get("status"), "HIRED"),
            goal_id=_safe_str(rec.get("goal_id"), ""),
            last_msg_ts=_last_msg_ts(agents_root / aid / "mailbox.md"),
            cost_usd=_safe_float(rec.get("cost_usd"), 0.0),
        )

    # agents.json 에 없는 디렉토리도 흡수 (재기동 직후 rehydrate 전 상태)
    if agents_root.exists():
        try:
            entries = sorted(agents_root.iterdir())
        except OSError as e:
            _log.warning("agents/ 디렉토리 스캔 실패: %s", e)
            entries = []
        for d in entries:
            if not d.is_dir() or d.name in rows:
                continue
            status_file = d / "status"
            status = "HIRED"
            if status_file.exists():
                try:
                    status = status_file.read_text(encoding="utf-8").strip() or "HIRED"
                except OSError:
                    pass
            rows[d.name] = MemberRow(
                agent_id=d.name,
                status=status,
                goal_id="",
                last_msg_ts=_last_msg_ts(d / "mailbox.md"),
                cost_usd=0.0,
            )

    return sorted(rows.values(), key=lambda r: r.agent_id)


def _collect_goals(plan_md: Path) -> tuple[list[GoalRow], int]:
    """plan.md → (pending_goals, done_count). 파싱 실패 라인은 무시."""
    pending: list[GoalRow] = []
    done = 0
    if not plan_md.exists():
        return pending, done
    try:
        text = plan_md.read_text(encoding="utf-8")
    except OSError as e:
        _log.warning("plan.md 읽기 실패: %s", e)
        return pending, done
    for line in text.splitlines():
        m = _GOAL_LINE_RE.match(line.strip())
        if not m:
            continue
        is_done = m.group("done").lower() == "x"
        if is_done:
            done += 1
            continue
        pending.append(
            GoalRow(
                id=m.group("id"),
                title=m.group("title").strip(),
                assigned=m.group("assigned") or "",
                done=False,
            )
        )
    return pending, done


def _collect_conflicts(conflicts_dir: Path) -> list[ConflictEntry]:
    """state/lead/conflicts/*.md 의 미해결 충돌 큐."""
    out: list[ConflictEntry] = []
    if not conflicts_dir.exists():
        return out
    try:
        entries = sorted(conflicts_dir.glob("*.md"))
    except OSError as e:
        _log.warning("conflicts/ 스캔 실패: %s", e)
        return out
    for f in entries:
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append(
            ConflictEntry(
                name=f.name,
                mtime_iso=_ts_to_iso(mtime),
            )
        )
    return out


def _collect_budget(budget_json: Path, events_jsonl: Path) -> BudgetSummary:
    """budget.json 전체 합계 + events.jsonl 의 model 필드 있는 라인을 모델별 집계.

    스키마 두 가지를 모두 받는다:
      - 새 (G-009): ``{"totals": {input_tokens, output_tokens, calls, usd},
                        "by_model": {...}, "started_at": iso, "updated_at": iso}``
      - 레거시 BudgetManager checkpoint: ``{"cost_usd", "tokens_in", "tokens_out",
                                            "turns", "started_at": float}``
    """
    summary = BudgetSummary()
    if budget_json.exists():
        try:
            raw = json.loads(budget_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("budget.json 파싱 실패 — 빈 예산으로 렌더: %s", e)
            raw = None
        if isinstance(raw, dict):
            totals = raw.get("totals")
            if isinstance(totals, dict):
                # G-009 새 스키마
                summary.cost_usd = _safe_float(totals.get("usd"), 0.0)
                summary.tokens_in = _safe_int(totals.get("input_tokens"), 0)
                summary.tokens_out = _safe_int(totals.get("output_tokens"), 0)
                summary.turns = _safe_int(totals.get("calls"), 0)
                # ISO 문자열 / unix epoch 둘 다 허용
                started_raw = raw.get("started_at")
                started = _started_at_to_unix(started_raw)
                if started > 0:
                    summary.elapsed_h = round(
                        (datetime.now(tz=UTC).timestamp() - started) / 3600.0, 2
                    )
                by_model_raw = raw.get("by_model")
                if isinstance(by_model_raw, dict):
                    summary.by_model = _normalize_by_model(by_model_raw)
            else:
                # 레거시 스키마
                summary.cost_usd = _safe_float(raw.get("cost_usd"), 0.0)
                summary.tokens_in = _safe_int(raw.get("tokens_in"), 0)
                summary.tokens_out = _safe_int(raw.get("tokens_out"), 0)
                summary.turns = _safe_int(raw.get("turns"), 0)
                started = _safe_float(raw.get("started_at"), 0.0)
                if started > 0:
                    summary.elapsed_h = round(
                        (datetime.now(tz=UTC).timestamp() - started) / 3600.0, 2
                    )

    # G-009 에서 호출당 events.jsonl 1줄을 기록. by_model 이 budget.json 에
    # 이미 있어도 events.jsonl 이 더 세밀하면 (예: legacy budget.json) 보조 집계.
    if events_jsonl.exists() and not summary.by_model:
        summary.by_model = _aggregate_by_model(events_jsonl)
    return summary


def _normalize_by_model(raw: dict[str, object]) -> dict[str, dict[str, float]]:
    """G-009 budget.json 의 ``by_model`` 을 dashboard 표준 형식으로 변환.

    소스 키 ``calls/input_tokens/output_tokens/usd`` →
    표준 키 ``calls/tokens_in/tokens_out/cost_usd`` (기존 _aggregate_by_model 과 통일).
    """
    out: dict[str, dict[str, float]] = {}
    for model, bucket in raw.items():
        if not isinstance(bucket, dict):
            continue
        out[model] = {
            "calls": _safe_float(bucket.get("calls"), 0.0),
            "tokens_in": _safe_float(bucket.get("input_tokens"), 0.0),
            "tokens_out": _safe_float(bucket.get("output_tokens"), 0.0),
            "cost_usd": _safe_float(bucket.get("usd"), 0.0),
        }
    return out


def _started_at_to_unix(value: object) -> float:
    """ISO-8601 문자열 또는 unix epoch float → unix epoch. 실패 시 0.0."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        s = value
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _aggregate_by_model(events_jsonl: Path) -> dict[str, dict[str, float]]:
    """events.jsonl 에서 kind == 'llm_call'|'budget'|'token_record' 등 model 필드 있는 라인 집계."""
    out: dict[str, dict[str, float]] = {}
    try:
        text = events_jsonl.read_text(encoding="utf-8")
    except OSError as e:
        _log.warning("events.jsonl 읽기 실패: %s", e)
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(evt, dict):
            continue
        model = evt.get("model")
        if not isinstance(model, str) or not model:
            continue
        bucket = out.setdefault(
            model, {"cost_usd": 0.0, "tokens_in": 0.0, "tokens_out": 0.0, "calls": 0.0}
        )
        bucket["cost_usd"] += _safe_float(evt.get("cost_usd"), 0.0)
        bucket["tokens_in"] += _safe_float(evt.get("tokens_in"), 0.0)
        bucket["tokens_out"] += _safe_float(evt.get("tokens_out"), 0.0)
        bucket["calls"] += 1.0
    return out


def _count_recent_completions(agents_json: Path) -> int:
    """agents.json 에서 최근 3600초 내 DONE 된 agent(=goal) 수 반환.

    completed_at 타임스탬프가 없거나 파싱 불가인 항목은 집계에서 제외.
    파일 I/O 오류 발생 시 0 반환 (예외 없음).
    """
    cutoff = datetime.now(tz=UTC).timestamp() - 3600.0
    count = 0
    if not agents_json.exists():
        return count
    try:
        raw = json.loads(agents_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("agents.json 읽기 실패 (ETA 산정 불가): %s", e)
        return count
    if not isinstance(raw, dict):
        return count
    for rec in raw.values():
        if not isinstance(rec, dict):
            continue
        if _safe_str(rec.get("status"), "") != "DONE":
            continue
        completed_at = rec.get("completed_at")
        if not isinstance(completed_at, str) or not completed_at:
            continue
        ts = _started_at_to_unix(completed_at)
        if ts > cutoff:
            count += 1
    return count


def _format_eta(pending: int, completed_last_hour: int) -> str:
    """최근 1h 완료 속도 기반 ETA 문자열 반환. 속도 0·데이터 부족 시 fallback.

    pending  : 남은 goal 수
    completed_last_hour : 최근 1h 내 완료된 goal 수 (= rate goals/h)
    """
    try:
        if pending < 0 or completed_last_hour <= 0:
            return "ETA: 산정 불가 (최근 1h 진행 없음)"
        if pending == 0:
            return "ETA: ~0m (모든 goal 완료)"
        eta_h = pending / completed_last_hour
        h = int(eta_h)
        m = int(round((eta_h - h) * 60))
        if m == 60:
            h += 1
            m = 0
        if h > 0 and m > 0:
            eta_str = f"~{h}h {m}m"
        elif h > 0:
            eta_str = f"~{h}h"
        else:
            eta_str = f"~{max(1, m)}m"
        return f"ETA: {eta_str} (최근 1h 기준 {completed_last_hour} goals/h)"
    except Exception:
        return "ETA: 산정 불가 (최근 1h 진행 없음)"


def _last_msg_ts(mailbox_md: Path) -> str:
    """mailbox.md 의 마지막 메시지 ts (최대 id 의 ts)."""
    if not mailbox_md.exists():
        return ""
    try:
        text = mailbox_md.read_text(encoding="utf-8")
    except OSError as e:
        _log.warning("mailbox 읽기 실패 %s: %s", mailbox_md, e)
        return ""
    best_id = -1
    best_ts = ""
    for m in _MSG_HEADER_RE.finditer(text):
        try:
            mid = int(m.group("id"))
        except ValueError:
            continue
        if mid > best_id:
            best_id = mid
            best_ts = m.group("ts")
    return best_ts


# ---------- 렌더 helpers (순수) ----------


def _goal_progress_bar(done: int, total: int) -> str:
    """10칸 고정 진행률 바: █ (U+2588) 채움 / ░ (U+2591) 빈칸."""
    if total == 0:
        return "░░░░░░░░░░ 0/0"
    filled = round(done / total * 10)
    bar = "█" * filled + "░" * (10 - filled)
    pct = int(done / total * 100)
    return f"{bar} {done}/{total} ({pct}%)"


def _render_summary(state: DashboardState) -> list[str]:
    """Goals 진행률 바 + 모델별 비용 합계 표 (by_model)."""
    total = len(state.goals_pending) + state.goals_done_count
    bar = _goal_progress_bar(state.goals_done_count, total)

    lines: list[str] = ["## Summary", ""]
    lines.append(f"Goals: {bar}")
    lines.append("")

    by_model = state.budget.by_model
    if by_model:
        lines.append("| Model | Cost (USD) | Tokens |")
        lines.append("| --- | ---: | ---: |")
        for model in sorted(by_model.keys()):
            d = by_model[model]
            tokens = int(d.get("tokens_in", 0)) + int(d.get("tokens_out", 0))
            lines.append(f"| {model} | ${d.get('cost_usd', 0.0):.4f} | {tokens:,} |")
    else:
        lines.append("_no model usage yet_")

    return lines


def _render_members(rows: list[MemberRow]) -> list[str]:
    out = ["## Members", ""]
    if not rows:
        out.append("- (없음)")
        return out
    out.append("| agent | status | goal | last_msg_ts | cost_usd |")
    out.append("| --- | --- | --- | --- | ---: |")
    for r in rows:
        out.append(
            f"| {r.agent_id} | {r.status} | {_md_cell(r.goal_id) or '-'} | "
            f"{r.last_msg_ts or '-'} | ${r.cost_usd:.4f} |"
        )
    return out


def _render_goals(pending: list[GoalRow], done_count: int) -> list[str]:
    out = [f"## Goals (남은 {len(pending)} / 완료 {done_count})", ""]
    if not pending:
        out.append("- (남은 goal 없음)")
        return out
    for g in pending:
        assigned = f" — assigned: {g.assigned}" if g.assigned else " — (미할당)"
        out.append(f"- `{g.id}`: {g.title}{assigned}")
    return out


def _render_conflicts(conflicts: list[ConflictEntry]) -> list[str]:
    out = [f"## Conflicts ({len(conflicts)} 파일 미해결)", ""]
    if not conflicts:
        out.append("- (없음)")
        return out
    for c in conflicts:
        out.append(f"- `{c.name}` _(mtime: {c.mtime_iso})_")
    return out


def _render_budget(b: BudgetSummary) -> list[str]:
    out = ["## Budget", ""]
    out.append(f"- 누적 비용: **${b.cost_usd:.4f} USD**")
    out.append(f"- 토큰: in={b.tokens_in:,} / out={b.tokens_out:,}")
    out.append(f"- 턴: {b.turns}")
    out.append(f"- 경과: {b.elapsed_h:.2f}h")
    if b.by_model:
        out.append("")
        out.append("### By model")
        out.append("")
        out.append("| model | calls | cost_usd | tokens_in | tokens_out |")
        out.append("| --- | ---: | ---: | ---: | ---: |")
        for model in sorted(b.by_model.keys()):
            d = b.by_model[model]
            out.append(
                f"| {model} | {int(d.get('calls', 0))} | "
                f"${d.get('cost_usd', 0.0):.4f} | "
                f"{int(d.get('tokens_in', 0)):,} | "
                f"{int(d.get('tokens_out', 0)):,} |"
            )
    return out


# ---------- 작은 util ----------


def _now_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_to_iso(ts: float) -> str:
    if ts <= 0:
        return "-"
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _md_cell(value: str) -> str:
    """markdown 표 셀에서 파이프(|) 깨짐 방지."""
    return value.replace("|", r"\|")


def _safe_str(value: object, default: str) -> str:
    return value if isinstance(value, str) else default


def _safe_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _safe_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default
