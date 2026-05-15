"""Budget — 시간/비용/턴 하드 리밋 + LLM 호출 단위 토큰/USD 누적기.

두 가지 표면을 제공한다:

1. ``BudgetManager`` / ``BudgetLimits`` / ``BudgetExceeded``
   기존 호출자 (lead, agent) 가 사용하는 하드 리밋 체커. 시그니처 호환을 위해
   유지. 새 호출자는 ``BudgetManager.record(...)`` 를 호출하면 내부에서 새
   누적기 ``record_usage`` 도 함께 호출하여 ``state/budget.json`` /
   ``state/events.jsonl`` 에 일관 누적된다.

2. ``record_usage`` / ``get_totals`` / ``get_recent_rate`` / ``estimate_eta``
   모듈 레벨 누적기. team_lead 30초 상태 라인, dashboard, 회귀 분석용.
   ``set_state_dir(<state_dir>)`` 로 한 번 초기화하면 모든 LLM 호출 지점이
   동일 파일에 append 한다 (lock + atomic rename).

파일 스키마:

* ``state/budget.json``::

    {
      "totals": {"input_tokens": int, "output_tokens": int,
                 "calls": int, "usd": float},
      "by_model": {"<model>": {"calls": int, "input_tokens": int,
                                "output_tokens": int, "usd": float}},
      "started_at": iso8601,
      "updated_at": iso8601
    }

* ``state/events.jsonl`` (호출당 1줄)::

    {"ts": iso8601, "call_id": str, "model": str,
     "input_tokens": int, "output_tokens": int, "cached_tokens": int,
     "usd": float|null, "meta": {...}}

USD 추정: ``meta.usd_known=True`` 면 그대로, ``usd=None`` 이면 가격표
``_PRICES`` 에서 모델 prefix 매칭으로 추정. 매칭 실패하면 ``usd=None`` 으로
event 에 기록하고, totals 에는 0 누적 + warning 로그 1줄.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


# ---------- Legacy 한도 체커 (BudgetManager) ----------

# Claude Opus 4.7 (2026.05 추정) per-1M token 단가. 새 누적기는 _PRICES 사용.
PRICE_INPUT_PER_MTOK = 15.0
PRICE_OUTPUT_PER_MTOK = 75.0


@dataclass
class BudgetLimits:
    max_hours: float = 12.0
    max_cost_usd: float = 50.0
    max_turns: int = 1000


@dataclass
class BudgetState:
    started_at: float
    turns: int = 0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class BudgetExceeded(Exception):
    """하드 리밋 초과 — 시스템 즉시 정지."""


class BudgetManager:
    """LLM 호출마다 ``record`` 호출. 한도 초과 시 즉시 ``BudgetExceeded``.

    ``record(tokens_in, tokens_out)`` 시그니처는 기존 호출자를 위해 유지.
    호출 시 가능하다면 ``record_usage`` (모듈 누적기) 도 함께 호출하여
    ``state/budget.json`` 의 새 스키마와 일관성을 유지한다.
    """

    def __init__(self, limits: BudgetLimits, checkpoint: Path | None = None):
        self.limits = limits
        self.checkpoint = checkpoint
        self.state = self._load()
        self._lock = threading.Lock()

    def _load(self) -> BudgetState:
        if self.checkpoint and self.checkpoint.exists():
            try:
                return BudgetState(**json.loads(self.checkpoint.read_text()))
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                _log.warning(
                    "BudgetManager checkpoint 손상 — 새로 시작: %s (%s)",
                    self.checkpoint,
                    e,
                )
        return BudgetState(started_at=time.time())

    def _save(self) -> None:
        if self.checkpoint:
            self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
            self.checkpoint.write_text(json.dumps(asdict(self.state), indent=2))

    def record(self, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self.state.turns += 1
            self.state.tokens_in += tokens_in
            self.state.tokens_out += tokens_out
            self.state.cost_usd += (
                tokens_in * PRICE_INPUT_PER_MTOK / 1_000_000
                + tokens_out * PRICE_OUTPUT_PER_MTOK / 1_000_000
            )
            self._save()
            self._check()

    def _check(self) -> None:
        elapsed_h = (time.time() - self.state.started_at) / 3600
        if elapsed_h > self.limits.max_hours:
            raise BudgetExceeded(f"시간 한도 초과: {elapsed_h:.2f}h")
        if (
            self.limits.max_cost_usd != float("inf")
            and self.state.cost_usd > self.limits.max_cost_usd
        ):
            raise BudgetExceeded(f"비용 한도 초과: ${self.state.cost_usd:.2f}")
        if self.state.turns > self.limits.max_turns:
            raise BudgetExceeded(f"턴 한도 초과: {self.state.turns}")

    def can_continue(self) -> bool:
        try:
            self._check()
            return True
        except BudgetExceeded:
            return False

    def status(self) -> dict[str, Any]:
        elapsed_h = (time.time() - self.state.started_at) / 3600
        return {
            "elapsed_h": round(elapsed_h, 2),
            "cost_usd": round(self.state.cost_usd, 2),
            "turns": self.state.turns,
            "tokens_in": self.state.tokens_in,
            "tokens_out": self.state.tokens_out,
        }


# ---------- 새 누적기 (record_usage) ----------

# per-1M token 단가 (USD). prefix 매칭으로 미세 모델 식별자(예 "opus-4-7") 흡수.
_PRICES: dict[str, tuple[float, float]] = {
    # claude opus 4.x
    "opus": (15.0, 75.0),
    # claude sonnet 4.x
    "sonnet": (3.0, 15.0),
    # claude haiku 4.x
    "haiku": (0.8, 4.0),
    # openai (대략값; 추정 전용)
    "gpt-": (5.0, 15.0),
    "o1": (15.0, 60.0),
    "o3": (15.0, 60.0),
    "o4": (15.0, 60.0),
    "codex-": (5.0, 15.0),
}

# 누적기 전역. set_state_dir 로 초기화하기 전 호출은 no-op.
_LOCK = threading.RLock()
_STATE_DIR: Path | None = None


def set_state_dir(state_dir: Path | str) -> None:
    """모듈 누적기의 출력 디렉토리를 지정한다. 없으면 생성."""
    global _STATE_DIR
    p = Path(state_dir)
    p.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _STATE_DIR = p


def _budget_path() -> Path | None:
    return None if _STATE_DIR is None else _STATE_DIR / "budget.json"


def _events_path() -> Path | None:
    return None if _STATE_DIR is None else _STATE_DIR / "events.jsonl"


def _now_iso() -> str:
    """microsecond 까지 포함한 UTC ISO-8601."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _empty_state() -> dict[str, Any]:
    return {
        "totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "calls": 0,
            "usd": 0.0,
        },
        "by_model": {},
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def _is_valid_shape(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    totals = raw.get("totals")
    if not isinstance(totals, dict):
        return False
    return all(k in totals for k in ("input_tokens", "output_tokens", "calls", "usd"))


def _load_state(bp: Path) -> dict[str, Any]:
    """budget.json 을 읽거나 빈 상태 반환. 손상 시 백업 + 빈 상태."""
    if not bp.exists():
        return _empty_state()
    try:
        raw = json.loads(bp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = bp.with_name(f"budget.corrupt.{int(time.time() * 1000)}.json")
        try:
            bp.replace(backup)
            _log.warning("budget.json 손상 — %s 로 백업, 새로 시작", backup.name)
        except OSError as e:
            _log.warning("손상된 budget.json 백업 실패: %s", e)
        return _empty_state()
    if not _is_valid_shape(raw):
        _log.warning("budget.json shape 불일치 — 새 누적으로 재시작")
        return _empty_state()
    return raw  # type: ignore[no-any-return]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """tmp+rename 으로 원자적 기록. 동일 디렉토리 tmp 사용 → 같은 파일시스템 보장."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _price_for(model: str) -> tuple[float, float] | None:
    """모델 이름에 대한 (input, output) per-1M USD 가격. prefix 매칭."""
    if model in _PRICES:
        return _PRICES[model]
    for key, price in _PRICES.items():
        if key.endswith("-"):
            if model.startswith(key):
                return price
        elif model.startswith(key):
            return price
    return None


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """모델 가격표가 있으면 USD 추정, 없으면 None."""
    price = _price_for(model)
    if price is None:
        return None
    p_in, p_out = price
    return (input_tokens * p_in + output_tokens * p_out) / 1_000_000.0


def record_usage(
    call_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    usd: float | None = None,
    cached_tokens: int = 0,
    meta: dict[str, Any] | None = None,
) -> None:
    """LLM 호출 1건의 토큰/USD 를 budget.json 에 누적 + events.jsonl 에 1줄 append.

    * ``usd=None`` 이면 ``estimate_usd`` 로 가격표 기반 추정.
    * 모델이 가격표에 없고 ``usd`` 도 None 이면 totals 에는 0 누적,
      event 라인의 ``usd`` 필드는 ``null``, warning 로그 1줄.
    * ``set_state_dir`` 가 호출되지 않은 상태에서는 조용히 no-op (라이브러리
      임포트만으로 부수효과 없도록).
    """
    if _STATE_DIR is None:
        return

    bp = _budget_path()
    ep = _events_path()
    assert bp is not None and ep is not None

    resolved_usd = usd
    if resolved_usd is None:
        resolved_usd = estimate_usd(model, input_tokens, output_tokens)
        if resolved_usd is None:
            _log.warning(
                "budget: 모델 '%s' 가격표 미상 — usd=0 으로 누적, event usd=null",
                model,
            )

    event = {
        "ts": _now_iso(),
        "call_id": call_id,
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cached_tokens": int(cached_tokens),
        "usd": float(resolved_usd) if resolved_usd is not None else None,
        "meta": dict(meta) if meta else {},
    }
    totals_usd_add = float(resolved_usd) if resolved_usd is not None else 0.0

    with _LOCK:
        state = _load_state(bp)
        totals = state.setdefault(
            "totals",
            {"input_tokens": 0, "output_tokens": 0, "calls": 0, "usd": 0.0},
        )
        totals["input_tokens"] = int(totals.get("input_tokens", 0)) + int(input_tokens)
        totals["output_tokens"] = int(totals.get("output_tokens", 0)) + int(output_tokens)
        totals["calls"] = int(totals.get("calls", 0)) + 1
        totals["usd"] = float(totals.get("usd", 0.0)) + totals_usd_add

        by_model = state.setdefault("by_model", {})
        bucket = by_model.setdefault(
            model,
            {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0},
        )
        bucket["calls"] = int(bucket.get("calls", 0)) + 1
        bucket["input_tokens"] = int(bucket.get("input_tokens", 0)) + int(input_tokens)
        bucket["output_tokens"] = int(bucket.get("output_tokens", 0)) + int(output_tokens)
        bucket["usd"] = float(bucket.get("usd", 0.0)) + totals_usd_add

        if "started_at" not in state:
            state["started_at"] = _now_iso()
        state["updated_at"] = _now_iso()

        _atomic_write_json(bp, state)

        ep.parent.mkdir(parents=True, exist_ok=True)
        with ep.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False))
            f.write("\n")


def get_totals() -> dict[str, Any]:
    """현재 budget.json 의 상태 dict 사본 반환. 미초기화 시 빈 상태."""
    bp = _budget_path()
    if bp is None or not bp.exists():
        return _empty_state()
    with _LOCK:
        return _load_state(bp)


def get_recent_rate(window_sec: float) -> float:
    """최근 ``window_sec`` 초 동안의 USD 소비율 (USD/sec).

    events.jsonl 의 최신 라인부터 ts 가 윈도우 밖이 될 때까지 합산.
    ``window_sec <= 0`` 이거나 events 가 없으면 0.0 반환.
    """
    if window_sec <= 0:
        return 0.0
    ep = _events_path()
    if ep is None or not ep.exists():
        return 0.0
    cutoff = time.time() - window_sec
    total_usd = 0.0
    with _LOCK:
        try:
            text = ep.read_text(encoding="utf-8")
        except OSError as e:
            _log.warning("events.jsonl 읽기 실패: %s", e)
            return 0.0
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
        ts = _parse_iso(evt.get("ts"))
        if ts is None or ts < cutoff:
            continue
        u = evt.get("usd")
        if isinstance(u, (int, float)):
            total_usd += float(u)
    return total_usd / window_sec if total_usd > 0 else 0.0


def _parse_iso(ts: object) -> float | None:
    """ISO-8601 (Z 또는 +00:00) 문자열을 unix epoch 초로. 실패 시 None."""
    if not isinstance(ts, str):
        return None
    s = ts
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def estimate_eta(
    remaining_goals: int,
    avg_usd_per_goal: float,
    window_sec: float = 60.0,
) -> float | None:
    """남은 goal 들을 마치는 데 걸릴 시간 (초). 추정 불가하면 None.

    공식: ``remaining_goals * avg_usd_per_goal / get_recent_rate(window_sec)``.
    rate 가 0 이거나 평균 비용이 0 이거나 남은 goal 0 이면 None.
    """
    if remaining_goals <= 0 or avg_usd_per_goal <= 0:
        return None
    rate = get_recent_rate(window_sec)
    if rate <= 0:
        return None
    return remaining_goals * avg_usd_per_goal / rate


# ---------- 상태 라인 ----------


def format_status_line(
    active: int,
    queued: int,
    conflicts: int,
    spent_usd: float,
    eta_minutes: float | None,
) -> str:
    """team_lead 의 30초 주기 상태 라인.

    포맷 (verification_check.status_line_format 통과 필수)::

        active=N queued=N conflicts=N spent=$X.XX eta=Ym

    ``eta_minutes`` 가 None 이거나 음수면 ``eta=?``. 정수면 정수, 아니면 정수
    반올림 (라인 가독성). spent 는 소수 2자리.
    """
    if eta_minutes is None or eta_minutes < 0:
        eta_part = "eta=?"
    else:
        eta_int = round(eta_minutes)
        eta_part = f"eta={eta_int}m"
    return (
        f"active={int(active)} "
        f"queued={int(queued)} "
        f"conflicts={int(conflicts)} "
        f"spent=${float(spent_usd):.2f} "
        f"{eta_part}"
    )
