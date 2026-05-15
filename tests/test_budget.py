"""core/budget.py — record_usage / get_totals / events.jsonl / 동시성 / 복구 테스트.

각 테스트는 set_state_dir(tmp_path) 로 모듈 상태를 격리한다. 모듈 전역 _LOCK 은
싱글톤이지만 _STATE_DIR 를 매 테스트마다 다른 디렉토리로 가리키므로 budget.json /
events.jsonl 은 테스트 간 충돌 없음.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

# `agent_system/` 을 sys.path 에 추가 — 패키지 형태로 import.
_AGENT_SYSTEM = Path(__file__).resolve().parent.parent
if str(_AGENT_SYSTEM) not in sys.path:
    sys.path.insert(0, str(_AGENT_SYSTEM))

# conftest 의 _install_stubs() 가 team_lead 테스트용으로 core.budget 의 경량 stub 을
# 미리 등록한다. 본 파일은 실제 budget 구현을 검증하므로 stub 을 비워 정식 모듈을
# 강제 로드한다.
for _mod_name in ("core.budget", "core"):
    sys.modules.pop(_mod_name, None)

from core import budget  # noqa: E402
from core.budget import (  # noqa: E402
    estimate_eta,
    format_status_line,
    get_recent_rate,
    get_totals,
    record_usage,
    set_state_dir,
)


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path: Path) -> Iterator[Path]:
    """모듈 _STATE_DIR 을 tmp_path 로 강제 + 테스트 후 복원."""
    prev = budget._STATE_DIR
    set_state_dir(tmp_path)
    try:
        yield tmp_path
    finally:
        budget._STATE_DIR = prev


def _budget_path(d: Path) -> Path:
    return d / "budget.json"


def _events_path(d: Path) -> Path:
    return d / "events.jsonl"


def _read_events(d: Path) -> list[dict]:
    p = _events_path(d)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------- record_usage 누적 ----------


def test_record_usage_accumulates_totals(tmp_path: Path) -> None:
    record_usage("c1", "opus", 100, 50, usd=0.01)
    record_usage("c2", "opus", 200, 80, usd=0.02)

    totals = get_totals()["totals"]
    assert totals["input_tokens"] == 300
    assert totals["output_tokens"] == 130
    assert totals["calls"] == 2
    assert totals["usd"] == pytest.approx(0.03)


def test_record_usage_by_model_separation(tmp_path: Path) -> None:
    record_usage("c1", "opus", 100, 50, usd=0.01)
    record_usage("c2", "sonnet", 100, 50, usd=0.001)
    record_usage("c3", "opus", 50, 25, usd=0.005)

    state = get_totals()
    by_model = state["by_model"]
    assert by_model["opus"]["calls"] == 2
    assert by_model["opus"]["input_tokens"] == 150
    assert by_model["sonnet"]["calls"] == 1
    assert by_model["sonnet"]["input_tokens"] == 100


def test_record_usage_estimates_usd_when_none(tmp_path: Path) -> None:
    # opus 단가: input 15 / output 75 per 1M
    record_usage("c1", "opus", 1_000_000, 1_000_000)
    totals = get_totals()["totals"]
    # 90 = 15 (input full) + 75 (output)
    assert totals["usd"] == pytest.approx(90.0, rel=1e-3)


def test_record_usage_unknown_model_skips_usd(tmp_path: Path) -> None:
    record_usage("c1", "mystery-model-xyz", 1_000, 500)
    state = get_totals()
    totals = state["totals"]
    assert totals["calls"] == 1
    # 단가 미상 → usd 0 으로 누적, event 의 usd 는 null
    assert totals["usd"] == 0.0
    events = _read_events(tmp_path)
    assert len(events) == 1
    assert events[0]["usd"] is None
    assert events[0]["model"] == "mystery-model-xyz"


# ---------- events.jsonl 1:1 ----------


def test_events_jsonl_one_line_per_call(tmp_path: Path) -> None:
    record_usage("c1", "opus", 10, 5, usd=0.001, meta={"src": "a"})
    record_usage("c2", "sonnet", 20, 10, usd=0.002)
    record_usage("c3", "haiku", 5, 1, usd=0.0001)

    events = _read_events(tmp_path)
    assert len(events) == 3
    ids = [e["call_id"] for e in events]
    assert ids == ["c1", "c2", "c3"]
    # 각 라인이 단일 JSON
    raw = _events_path(tmp_path).read_text(encoding="utf-8")
    assert raw.count("\n") == 3, f"각 line 끝 newline 보장 — got {raw!r}"


def test_events_meta_preserved(tmp_path: Path) -> None:
    record_usage("c1", "opus", 1, 1, usd=0.0, meta={"source": "claude_cli", "session_id": "abc"})
    events = _read_events(tmp_path)
    assert events[0]["meta"]["source"] == "claude_cli"
    assert events[0]["meta"]["session_id"] == "abc"


# ---------- 동시성 ----------


def test_concurrent_record_usage_totals_match(tmp_path: Path) -> None:
    THREADS = 10
    PER = 100

    def worker(tid: int) -> None:
        for i in range(PER):
            record_usage(f"t{tid}-{i}", "opus", 1, 1, usd=0.0001)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    totals = get_totals()["totals"]
    assert totals["calls"] == THREADS * PER
    assert totals["input_tokens"] == THREADS * PER
    assert totals["output_tokens"] == THREADS * PER
    assert totals["usd"] == pytest.approx(0.0001 * THREADS * PER, rel=1e-3)

    events = _read_events(tmp_path)
    assert len(events) == THREADS * PER
    # 각 call_id 가 유일한지 (race 로 빈 라인이나 잘림 없는지)
    call_ids = {e["call_id"] for e in events}
    assert len(call_ids) == THREADS * PER


# ---------- get_recent_rate ----------


def test_get_recent_rate_window_cutoff(tmp_path: Path) -> None:
    # 직접 events.jsonl 에 과거/현재 라인 작성 → 윈도우 컷오프 검증
    ep = _events_path(tmp_path)
    ep.parent.mkdir(parents=True, exist_ok=True)
    # 과거 (윈도우 밖): 1시간 전
    past_ts = time.time() - 3600
    past_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(past_ts))
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    with ep.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": past_iso,
                    "call_id": "old",
                    "model": "opus",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "usd": 1000.0,
                    "meta": {},
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "ts": now_iso,
                    "call_id": "new",
                    "model": "opus",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "usd": 6.0,
                    "meta": {},
                }
            )
            + "\n"
        )
    # 60s 윈도우: 과거 라인은 제외, 현재 라인 6.0 / 60s = 0.1 USD/s
    rate = get_recent_rate(window_sec=60.0)
    assert rate == pytest.approx(6.0 / 60.0, rel=1e-3)


def test_get_recent_rate_zero_when_no_events(tmp_path: Path) -> None:
    assert get_recent_rate(60.0) == 0.0
    record_usage("c1", "opus", 0, 0, usd=0.0)
    # event 는 있지만 usd=0 이므로 rate 0
    assert get_recent_rate(60.0) == 0.0


def test_get_recent_rate_zero_window(tmp_path: Path) -> None:
    record_usage("c1", "opus", 1, 1, usd=1.0)
    assert get_recent_rate(0.0) == 0.0


# ---------- estimate_eta 경계 ----------


def test_estimate_eta_none_when_zero_remaining(tmp_path: Path) -> None:
    record_usage("c1", "opus", 1, 1, usd=1.0)
    assert estimate_eta(0, 1.0) is None


def test_estimate_eta_none_when_zero_avg(tmp_path: Path) -> None:
    record_usage("c1", "opus", 1, 1, usd=1.0)
    assert estimate_eta(5, 0.0) is None


def test_estimate_eta_none_when_no_rate(tmp_path: Path) -> None:
    # event 없음 → rate 0 → eta None
    assert estimate_eta(5, 1.0) is None


def test_estimate_eta_positive(tmp_path: Path) -> None:
    # event 를 직접 작성해 60s 윈도우 rate 강제 (60 USD / 60s = 1 USD/s)
    ep = _events_path(tmp_path)
    ep.parent.mkdir(parents=True, exist_ok=True)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    with ep.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": now_iso,
                    "call_id": "x",
                    "model": "opus",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "usd": 60.0,
                    "meta": {},
                }
            )
            + "\n"
        )
    # remaining=2 goals × $5/goal = $10 / 1 USD/s = 10s
    eta = estimate_eta(2, 5.0)
    assert eta is not None
    assert eta == pytest.approx(10.0, rel=1e-2)


# ---------- 손상 복구 ----------


def test_corrupt_budget_json_recovered_with_backup(tmp_path: Path) -> None:
    bp = _budget_path(tmp_path)
    bp.write_text("{not valid json", encoding="utf-8")

    # 첫 record_usage → 손상 인지 → 백업 + 재초기화
    record_usage("c1", "opus", 100, 50, usd=0.01)

    # backup 파일 존재 확인
    backups = list(tmp_path.glob("budget.corrupt.*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8").startswith("{not valid")

    # 새 budget.json 은 유효
    state = get_totals()
    assert state["totals"]["calls"] == 1
    assert state["totals"]["usd"] == pytest.approx(0.01)


def test_wrong_shape_budget_json_resets_without_backup(tmp_path: Path) -> None:
    # 유효 JSON 이지만 totals 키 없음 → reset (backup 없이 — JSON 파싱 OK 라 손상 아님)
    bp = _budget_path(tmp_path)
    bp.write_text(json.dumps({"hello": "world"}), encoding="utf-8")

    record_usage("c1", "opus", 100, 50, usd=0.01)

    state = get_totals()
    assert state["totals"]["calls"] == 1


# ---------- 원자성 sanity ----------


def test_atomic_write_no_tmp_left_behind(tmp_path: Path) -> None:
    record_usage("c1", "opus", 10, 5, usd=0.001)
    leftover = list(tmp_path.glob(".budget.*.tmp"))
    assert leftover == []


def test_set_state_dir_creates_dir(tmp_path: Path) -> None:
    new_dir = tmp_path / "nested" / "deep"
    assert not new_dir.exists()
    set_state_dir(new_dir)
    assert new_dir.exists()


# ---------- format_status_line 포맷 ----------


def test_format_status_line_matches_verification_check() -> None:
    s = format_status_line(
        active=2,
        queued=3,
        conflicts=1,
        spent_usd=1.23,
        eta_minutes=5,
    )
    assert "active=2" in s
    assert "queued=3" in s
    assert "conflicts=1" in s
    assert "spent=$1.23" in s
    assert "eta=5m" in s


def test_format_status_line_eta_none_renders_question_mark() -> None:
    s = format_status_line(
        active=0,
        queued=0,
        conflicts=0,
        spent_usd=0.0,
        eta_minutes=None,
    )
    assert "eta=?" in s
    assert "spent=$0.00" in s


def test_format_status_line_eta_float_rounds_to_int() -> None:
    s = format_status_line(
        active=1,
        queued=1,
        conflicts=0,
        spent_usd=2.5,
        eta_minutes=12.4,
    )
    # 12.4 → 12, 12.6 → 13 — int(round(...)) 동작 확인
    assert "eta=12m" in s


def test_format_status_line_field_order() -> None:
    """포맷은 active → queued → conflicts → spent → eta 순서 고정."""
    s = format_status_line(
        active=7,
        queued=2,
        conflicts=4,
        spent_usd=9.876,
        eta_minutes=15,
    )
    # spent_usd 는 소수 2자리 절단 (9.88) 가 아니라 반올림 (9.88).
    assert s.startswith("active=7 queued=2 conflicts=4 spent=$9.88 eta=15m"), s


# ---------- record_usage state_dir 미설정 시 no-op ----------


def test_record_usage_noop_when_state_dir_unset(tmp_path: Path, monkeypatch) -> None:
    # 일시적으로 _STATE_DIR 을 None 으로
    monkeypatch.setattr(budget, "_STATE_DIR", None)
    # 예외 없이 통과해야 함
    record_usage("c1", "opus", 100, 50, usd=0.01)
    # 파일 미생성
    assert not _budget_path(tmp_path).exists()
    assert not _events_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# Extended cases — M033 신규 추가
# 누락된 budget.json graceful 초기화 / events.jsonl 각 라인 JSON 파싱 / 모델별
# 단가 정확도 (opus vs sonnet vs haiku) / threading race 가드 sanity / no-op
# 동작 보강.
# ---------------------------------------------------------------------------


def test_budget_json_missing_at_start_creates_fresh(tmp_path: Path) -> None:
    """budget.json 이 처음부터 없는 상태에서 get_totals 호출 → graceful 초기 상태."""
    bp = _budget_path(tmp_path)
    assert not bp.exists()
    state = get_totals()
    # _empty_state() 의 shape 검증
    assert "totals" in state
    assert state["totals"]["calls"] == 0
    assert state["totals"]["usd"] == 0.0
    # get_totals 자체는 디스크 기록하지 않음
    assert not bp.exists()


def test_events_jsonl_each_line_parses_as_json(tmp_path: Path) -> None:
    """events.jsonl 의 모든 라인은 단독으로 json.loads 가능해야 한다."""
    for i in range(5):
        record_usage(f"call-{i}", "opus", 10, 5, usd=0.001)

    ep = _events_path(tmp_path)
    raw = ep.read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    assert len(lines) == 5
    for line in lines:
        # json.loads 가 예외 없이 통과해야 — partial writes 가 없다.
        parsed = json.loads(line)
        assert isinstance(parsed, dict)
        assert "call_id" in parsed
        assert "ts" in parsed
        assert "model" in parsed


def test_sonnet_vs_opus_pricing_distinct(tmp_path: Path) -> None:
    """sonnet (3/15 per Mtok) 과 opus (15/75 per Mtok) 단가가 명확히 다르다."""
    record_usage("opus-call", "opus", 1_000_000, 1_000_000)
    record_usage("sonnet-call", "sonnet", 1_000_000, 1_000_000)

    state = get_totals()
    by_model = state["by_model"]
    # opus: 15 + 75 = 90 USD
    assert by_model["opus"]["usd"] == pytest.approx(90.0, rel=1e-3)
    # sonnet: 3 + 15 = 18 USD
    assert by_model["sonnet"]["usd"] == pytest.approx(18.0, rel=1e-3)
    # opus 가 sonnet 보다 5배 비싸야 한다
    assert by_model["opus"]["usd"] > by_model["sonnet"]["usd"] * 4


def test_haiku_pricing(tmp_path: Path) -> None:
    """haiku (0.8/4 per Mtok) — 가장 저렴한 모델."""
    record_usage("haiku-call", "haiku", 1_000_000, 1_000_000)
    state = get_totals()
    # haiku: 0.8 + 4.0 = 4.8 USD
    assert state["by_model"]["haiku"]["usd"] == pytest.approx(4.8, rel=1e-3)


def test_model_prefix_match_versioned_id(tmp_path: Path) -> None:
    """prefix 매칭: 'opus-4-7' 같은 버전 붙은 모델도 opus 단가 적용."""
    record_usage("call", "opus-4-7", 1_000_000, 0)
    state = get_totals()
    # 15 USD (input only)
    assert state["totals"]["usd"] == pytest.approx(15.0, rel=1e-3)


def test_record_usage_concurrent_no_truncated_lines(tmp_path: Path) -> None:
    """동시 record_usage 후 events.jsonl 의 모든 라인이 완전한 JSON 이어야 한다.

    test_concurrent_record_usage_totals_match 보다 더 엄격하게: 각 라인을
    json.loads 로 파싱해 라인 잘림이 없는지 명시 검증.
    """
    THREADS = 5
    PER = 30

    def worker(tid: int) -> None:
        for i in range(PER):
            record_usage(f"r{tid}-{i}", "sonnet", 1, 1, usd=0.0001)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raw = _events_path(tmp_path).read_text(encoding="utf-8")
    lines = [line for line in raw.splitlines() if line.strip()]
    assert len(lines) == THREADS * PER
    # 모든 라인이 valid json 이고 call_id 가 있어야
    seen: set[str] = set()
    for line in lines:
        parsed = json.loads(line)
        seen.add(parsed["call_id"])
    assert len(seen) == THREADS * PER


def test_format_status_line_only_uses_basic_ascii(tmp_path: Path) -> None:
    """상태 라인은 verification_check 등 외부 파서가 읽을 수 있도록 ASCII 전용."""
    s = format_status_line(
        active=1,
        queued=2,
        conflicts=0,
        spent_usd=0.5,
        eta_minutes=3,
    )
    assert s.isascii()
    # 명세된 키워드 5종 모두 존재
    for kw in ("active=", "queued=", "conflicts=", "spent=$", "eta="):
        assert kw in s


def test_format_status_line_negative_eta_renders_question_mark() -> None:
    """eta_minutes < 0 (계산 오류 등) 도 graceful 처리."""
    s = format_status_line(0, 0, 0, 0.0, eta_minutes=-1.5)
    assert "eta=?" in s


def test_estimate_eta_returns_seconds_not_minutes(tmp_path: Path) -> None:
    """estimate_eta 의 반환 단위는 초 (seconds), 분이 아님."""
    ep = _events_path(tmp_path)
    ep.parent.mkdir(parents=True, exist_ok=True)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    ep.write_text(
        json.dumps(
            {
                "ts": now_iso,
                "call_id": "x",
                "model": "opus",
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "usd": 120.0,
                "meta": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # rate = 120 / 60 = 2 USD/s
    # remaining=1, avg=10 → eta = 10 / 2 = 5 seconds
    eta = estimate_eta(1, 10.0)
    assert eta is not None
    assert eta == pytest.approx(5.0, rel=1e-2)


def test_record_usage_explicit_zero_usd_records_event(tmp_path: Path) -> None:
    """usd=0.0 (명시) 도 정상 호출 1건으로 카운트되고 event 1줄."""
    record_usage("c1", "opus", 0, 0, usd=0.0)
    state = get_totals()
    assert state["totals"]["calls"] == 1
    events = _read_events(tmp_path)
    assert len(events) == 1
    assert events[0]["usd"] == 0.0


def test_record_usage_with_cached_tokens_preserved(tmp_path: Path) -> None:
    """cached_tokens 필드도 event 라인에 보존되어야 한다 (회귀 가시화용)."""
    record_usage("c1", "opus", 100, 50, usd=0.01, cached_tokens=42)
    events = _read_events(tmp_path)
    assert events[0]["cached_tokens"] == 42


def test_atomic_write_survives_corrupt_during_record(tmp_path: Path) -> None:
    """budget.json 손상 → backup 생성 + 새 누적 → 다음 record 는 정상 작동."""
    bp = _budget_path(tmp_path)
    bp.write_text("garbage", encoding="utf-8")

    record_usage("c1", "opus", 10, 5, usd=0.001)
    record_usage("c2", "opus", 20, 10, usd=0.002)

    state = get_totals()
    # 손상 인지 후 새로 누적 → calls=2, usd=0.003
    assert state["totals"]["calls"] == 2
    assert state["totals"]["usd"] == pytest.approx(0.003)
    # backup 1개 (첫 record 가 인지)
    backups = list(tmp_path.glob("budget.corrupt.*.json"))
    assert len(backups) == 1


def test_set_state_dir_idempotent(tmp_path: Path) -> None:
    """같은 디렉토리로 두 번 set_state_dir 호출해도 OK."""
    set_state_dir(tmp_path)
    set_state_dir(tmp_path)
    record_usage("c1", "opus", 10, 5, usd=0.001)
    assert get_totals()["totals"]["calls"] == 1


def test_get_recent_rate_ignores_unparseable_lines(tmp_path: Path) -> None:
    """events.jsonl 에 손상 라인이 섞여 있어도 get_recent_rate 는 정상 라인만 합산."""
    ep = _events_path(tmp_path)
    ep.parent.mkdir(parents=True, exist_ok=True)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    lines = [
        "{ this is garbage",
        json.dumps(
            {
                "ts": now_iso,
                "call_id": "x",
                "model": "opus",
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "usd": 30.0,
                "meta": {},
            }
        ),
        "another garbage line",
    ]
    ep.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rate = get_recent_rate(window_sec=60.0)
    # 30 USD / 60s = 0.5 USD/s
    assert rate == pytest.approx(0.5, rel=1e-3)
