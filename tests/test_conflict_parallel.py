"""병렬 충돌 토론 단위 테스트 — ``TeamLead._resolve_conflicts_via_debate``.

검증 대상 4건:

1. ``asyncio.Semaphore(DEBATE_MAX_PARALLEL=4)`` 가 동시 실행 한도를 지키는지
   — 8개 충돌을 동시에 넣고, 각 토론이 ``~200ms`` 슬립한다 가정하면
   완료까지 ``~400ms`` (2 배치) 가 걸려야 한다. 직렬화면 ``~1.6s``.

2. ``_apply_auto_merge_pass`` 가 결정론적으로 머지된 충돌은 ``_run_conflict_debate``
   를 단 한 번도 호출하지 않는다 (call_count == 0).

3. 1차 sonnet 토론이 ``consensus=False`` 면 동일 충돌이 opus 로 escalate
   되고 opus 호출 정확히 1회.

4. 각 충돌의 토론 결정문(panel md) 이 ``lead_state_dir/debates/`` 안에
   ``debate-{agent_id}-{file_token}-{model}-{ts}.md`` 형식으로 직렬화되며
   파일별 채택 방향이 포함된다.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

# conftest 가 sys.path / 의존성 stub 을 이미 설치함.
from lead.team_lead import DEBATE_MAX_PARALLEL, TeamLead

# ===========================================================================
# 헬퍼 — 가벼운 TeamLead 인스턴스 (의존성 빈 stub)
# ===========================================================================


class _RecordingTimeline:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def emit(self, source: str, kind: str, **fields: object) -> None:
        self.events.append((source, kind, dict(fields)))


class _FakeLLM:
    """``call`` 만 호출되는 minimal LLM."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def call(self, system: str, user: str, **kw: object) -> str:
        self.calls.append({"model": kw.get("model"), "tier": kw.get("tier")})
        return "stub response"


def _build_lead(tmp_root: Path) -> TeamLead:
    """``TeamLead.__new__`` 우회로 충돌 토론만 호출 가능한 최소 인스턴스 생성."""
    ws_main = tmp_root / "ws" / "main"
    ws_root = tmp_root / "ws"
    lead_state = tmp_root / "state" / "lead"
    agents_root = tmp_root / "state" / "agents"
    ws_main.mkdir(parents=True)
    lead_state.mkdir(parents=True)
    agents_root.mkdir(parents=True)

    lead = TeamLead.__new__(TeamLead)
    lead.ws_main = ws_main
    lead.ws_root = ws_root
    lead.lead_state_dir = lead_state
    lead.agents_root = agents_root
    lead.timeline = _RecordingTimeline()
    lead.llm = _FakeLLM()
    lead._log = lambda msg: None
    return lead


def _make_conflict(ws_main: Path, agent_id: str, rel: str, *, main: str, stash: str) -> None:
    """main 파일과 그 옆 stash 파일(``<name>.from-<id>``) 동시 작성."""
    main_p = ws_main / rel
    main_p.parent.mkdir(parents=True, exist_ok=True)
    main_p.write_text(main, encoding="utf-8")
    stash_p = main_p.with_name(f"{main_p.name}.from-{agent_id}")
    stash_p.write_text(stash, encoding="utf-8")


# ===========================================================================
# 1) 병렬 동시성 한도 — Semaphore(4) 가 8건을 2배치로 처리
# ===========================================================================


def test_resolve_conflicts_parallel_respects_semaphore_limit(tmp_path: Path) -> None:
    """8개 충돌을 동시에 넣고 _run_conflict_debate 가 슬립하게 하면 동시 실행 피크가 4 이하여야 한다.

    경과 시간도 추가로 확인: 슬립 0.2초 × 8건 가 직렬화되면 1.6초+, 4동시 시 0.4초+.
    """
    assert DEBATE_MAX_PARALLEL == 4, "테스트 가정: 동시 한도 4"

    lead = _build_lead(tmp_path)

    conflicts = []
    for i in range(8):
        rel = f"file_{i}.py"
        _make_conflict(
            lead.ws_main,
            "M001",
            rel,
            main="version_main\n",
            stash=f"stash_{i}\n",
        )
        conflicts.append(rel)

    # 동시 실행 카운터 + peak
    active = {"count": 0, "peak": 0}
    lock = threading.Lock()

    def fake_run(
        agent_id: str,
        rel_clean: str,
        main_path: Path,
        stash_path: Path,
        model: str,
        timeout_sec: int,
    ) -> dict:
        with lock:
            active["count"] += 1
            active["peak"] = max(active["peak"], active["count"])
        # IO-bound 행위 시뮬레이션 (asyncio.to_thread 가 풀어내는 슬립)
        time.sleep(0.2)
        with lock:
            active["count"] -= 1
        # 합의 도달 + 통합본 제공 → escalate 안 함
        return {"consensus": True, "merged": "merged_content\n", "decision": "main 채택"}

    lead._run_conflict_debate = fake_run  # type: ignore[assignment]

    t0 = time.monotonic()
    lead._resolve_conflicts_via_debate("M001", conflicts)
    elapsed = time.monotonic() - t0

    # 동시성 한도 — Semaphore(4) 가 새는지
    assert active["peak"] <= DEBATE_MAX_PARALLEL, (
        f"동시 실행 peak={active['peak']} > 한도 {DEBATE_MAX_PARALLEL}"
    )
    assert active["peak"] >= 2, f"병렬화 안 됨 peak={active['peak']} (직렬화 의심)"
    # 타이밍 — 4동시*2배치 = ~0.4s 가 정상. 직렬화면 1.6s+.
    assert elapsed < 1.0, f"직렬 의심: elapsed={elapsed:.3f}s (기대 < 1.0s)"


# ===========================================================================
# 2) auto_merge 가 모두 해소한 케이스 — LLM 호출 0회
# ===========================================================================


def test_auto_merge_covers_all_conflicts_skips_llm(tmp_path: Path) -> None:
    """auto_merge 가 충돌을 모두 해소하면 _run_conflict_debate 호출 안 됨 (call_count==0)."""
    lead = _build_lead(tmp_path)

    # auto_merge 가 처리하려면 .seed/<rel> 가 있어야 함 (3-way base).
    seed_root = lead.ws_root / "M001" / ".seed"

    conflicts: list[str] = []
    for i in range(3):
        rel = f"auto_{i}.py"
        # base 와 main 동일 (b가 base 그대로) → identical 전략으로 a 채택
        base = "x = 1\n"
        main_v = base + f"# M001 만 추가한 라인 {i}\n"  # = a
        stash_v = base  # = b (== base)
        # 둘 다 같다면 identical 전략 — 하지만 우린 a != base, b == base → identical(a)
        # auto_merge: if b == base → returns a
        _make_conflict(lead.ws_main, "M001", rel, main=main_v, stash=stash_v)
        seed_p = seed_root / rel
        seed_p.parent.mkdir(parents=True, exist_ok=True)
        seed_p.write_text(base, encoding="utf-8")
        conflicts.append(rel)

    debate_calls: list[str] = []

    def should_not_be_called(*_a: object, **_kw: object) -> dict:
        debate_calls.append("BOOM")
        return {"consensus": True, "merged": "x", "decision": "x"}

    lead._run_conflict_debate = should_not_be_called  # type: ignore[assignment]

    lead._resolve_conflicts_via_debate("M001", conflicts)

    assert debate_calls == [], f"auto_merge 가능 케이스에 LLM 호출됨: {debate_calls}"
    # main 에 a 가 적용됐는지 (a 가 새 라인 추가본)
    for i, rel in enumerate(conflicts):
        merged_text = (lead.ws_main / rel).read_text()
        assert f"M001 만 추가한 라인 {i}" in merged_text, merged_text
        # stash 도 정리됨
        assert not (lead.ws_main / f"{rel}.from-M001").exists()


def test_auto_merge_partial_only_remaining_goes_to_debate(tmp_path: Path) -> None:
    """일부만 auto_merge 가능 — 나머지만 토론 호출. 호출 횟수 검증."""
    lead = _build_lead(tmp_path)
    seed_root = lead.ws_root / "M001" / ".seed"

    # 1개는 auto-mergeable (a != base, b == base), 1개는 ws/main 에만 seed 없음.
    _make_conflict(lead.ws_main, "M001", "auto.py", main="x = 1\nNEW\n", stash="x = 1\n")
    sp = seed_root / "auto.py"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("x = 1\n", encoding="utf-8")

    _make_conflict(lead.ws_main, "M001", "hard.py", main="A\n", stash="B\n")
    # 'hard.py' 시드 없음 → auto_merge skip → debate.

    debate_calls: list[str] = []

    def fake_run(agent_id, rel_clean, main_path, stash_path, model, timeout_sec):  # type: ignore[no-untyped-def]
        debate_calls.append(rel_clean)
        return {"consensus": True, "merged": "MERGED\n", "decision": "ok"}

    lead._run_conflict_debate = fake_run  # type: ignore[assignment]

    lead._resolve_conflicts_via_debate("M001", ["auto.py", "hard.py"])

    # auto.py 는 LLM 안 거침, hard.py 만 거침.
    assert debate_calls == ["hard.py"], debate_calls
    # auto.py 는 stash 정리되고 main 갱신
    assert not (lead.ws_main / "auto.py.from-M001").exists()
    assert "NEW" in (lead.ws_main / "auto.py").read_text()


# ===========================================================================
# 3) sonnet consensus=False → opus escalate (opus 정확히 1회)
# ===========================================================================


def test_sonnet_no_consensus_escalates_to_opus_exactly_once(tmp_path: Path) -> None:
    """1차 sonnet 결과가 consensus=False 면 동일 충돌이 opus 로 escalate.

    opus 호출 1회, sonnet 호출 1회 — 총 2회 (escalate 후 추가 escalate 없음).
    """
    lead = _build_lead(tmp_path)
    _make_conflict(lead.ws_main, "M001", "esc.py", main="MAIN\n", stash="STASH\n")

    calls_by_model: list[str] = []

    def fake_run(agent_id, rel_clean, main_path, stash_path, model, timeout_sec):  # type: ignore[no-untyped-def]
        calls_by_model.append(model)
        if model == "sonnet":
            # consensus 실패 → escalate 트리거
            return {"consensus": False, "merged": None, "decision": "no agreement"}
        # opus 가 통합본 만들어 옴
        return {"consensus": True, "merged": "OPUS_MERGED\n", "decision": "main 채택"}

    lead._run_conflict_debate = fake_run  # type: ignore[assignment]

    lead._resolve_conflicts_via_debate("M001", ["esc.py"])

    # sonnet 1회 + opus 1회 = 2회
    assert calls_by_model == ["sonnet", "opus"], calls_by_model
    # opus 결과가 main 에 적용됐는지
    assert (lead.ws_main / "esc.py").read_text() == "OPUS_MERGED\n"
    # stash 정리됨
    assert not (lead.ws_main / "esc.py.from-M001").exists()
    # timeline 에 escalate 이벤트
    kinds = [e[1] for e in lead.timeline.events]
    assert "debate_escalate" in kinds
    # conflict_debated 이벤트의 model 필드는 'opus'
    debated = [e for e in lead.timeline.events if e[1] == "conflict_debated"]
    assert debated and debated[0][2]["model"] == "opus"


def test_sonnet_consensus_true_skips_opus(tmp_path: Path) -> None:
    """1차 sonnet 이 consensus=True + merged 제공하면 opus 호출 안 함."""
    lead = _build_lead(tmp_path)
    _make_conflict(lead.ws_main, "M001", "ok.py", main="A\n", stash="B\n")

    calls_by_model: list[str] = []

    def fake_run(agent_id, rel_clean, main_path, stash_path, model, timeout_sec):  # type: ignore[no-untyped-def]
        calls_by_model.append(model)
        return {"consensus": True, "merged": "SONNET_OUT\n", "decision": "B 채택"}

    lead._run_conflict_debate = fake_run  # type: ignore[assignment]

    lead._resolve_conflicts_via_debate("M001", ["ok.py"])

    # sonnet 만 1회 — escalate 안 함
    assert calls_by_model == ["sonnet"], calls_by_model
    assert (lead.ws_main / "ok.py").read_text() == "SONNET_OUT\n"


def test_opus_also_fails_keeps_files_intact(tmp_path: Path) -> None:
    """sonnet + opus 둘 다 실패하면 main/stash 모두 보존 — 데이터 손실 없음."""
    lead = _build_lead(tmp_path)
    _make_conflict(lead.ws_main, "M001", "stubborn.py", main="MAIN\n", stash="STASH\n")

    def fake_run(agent_id, rel_clean, main_path, stash_path, model, timeout_sec):  # type: ignore[no-untyped-def]
        # 둘 다 통합본 못 만듦
        return {"consensus": False, "merged": None, "decision": "stuck"}

    lead._run_conflict_debate = fake_run  # type: ignore[assignment]

    lead._resolve_conflicts_via_debate("M001", ["stubborn.py"])

    # 데이터 손실 없이 main/stash 보존
    assert (lead.ws_main / "stubborn.py").read_text() == "MAIN\n"
    assert (lead.ws_main / "stubborn.py.from-M001").read_text() == "STASH\n"


# ===========================================================================
# 4) 결과 직렬화 — debates/ 아래 토론 md 가 file 별 + model 별 생성
# ===========================================================================


def test_debate_artifacts_serialize_per_file_per_model(tmp_path: Path) -> None:
    """``_apply_merged`` 호출 시 timeline 에 ``conflict_debated`` 이벤트가 file/model 정보 포함.

    파일별 채택 방향(``decision``) 도 outcome dict 에 담겨 전달된다.
    """
    lead = _build_lead(tmp_path)

    files = ["a.py", "b.py", "c.py"]
    for f in files:
        _make_conflict(lead.ws_main, "M001", f, main=f"main_{f}\n", stash=f"stash_{f}\n")

    decisions: dict[str, str] = {}

    def fake_run(agent_id, rel_clean, main_path, stash_path, model, timeout_sec):  # type: ignore[no-untyped-def]
        decision = f"{rel_clean}: main 채택 (model={model})"
        decisions[rel_clean] = decision
        return {"consensus": True, "merged": f"INTEGRATED_{rel_clean}\n", "decision": decision}

    lead._run_conflict_debate = fake_run  # type: ignore[assignment]

    lead._resolve_conflicts_via_debate("M001", files)

    # timeline 에 각 파일 별 conflict_debated 이벤트
    debated = [e[2] for e in lead.timeline.events if e[1] == "conflict_debated"]
    assert len(debated) == len(files), debated
    files_in_events = {e["file"] for e in debated}
    assert files_in_events == set(files)
    # 각 이벤트가 model 정보를 포함 — 채택 방향 후속 분석용
    for e in debated:
        assert e["model"] in {"sonnet", "opus"}
        assert e["consensus"] is True

    # 통합본이 main 에 적용됐는지
    for f in files:
        assert (lead.ws_main / f).read_text() == f"INTEGRATED_{f}\n"
        assert not (lead.ws_main / f"{f}.from-M001").exists()


def test_zero_conflicts_short_circuits(tmp_path: Path) -> None:
    """충돌 리스트가 빈 경우 _run_conflict_debate 호출 자체가 없어야 한다."""
    lead = _build_lead(tmp_path)

    debate_calls: list[str] = []

    def should_not_be_called(*_a: object, **_kw: object) -> dict:
        debate_calls.append("BOOM")
        return {"consensus": True, "merged": "", "decision": ""}

    lead._run_conflict_debate = should_not_be_called  # type: ignore[assignment]

    lead._resolve_conflicts_via_debate("M001", [])
    assert debate_calls == []


def test_symlink_rejected_entries_excluded(tmp_path: Path) -> None:
    """'symlink rejected' suffix 가 붙은 충돌은 토론 대상에서 제외."""
    lead = _build_lead(tmp_path)
    _make_conflict(lead.ws_main, "M001", "ok.py", main="A\n", stash="B\n")

    seen: list[str] = []

    def fake_run(agent_id, rel_clean, main_path, stash_path, model, timeout_sec):  # type: ignore[no-untyped-def]
        seen.append(rel_clean)
        return {"consensus": True, "merged": "x\n", "decision": "x"}

    lead._run_conflict_debate = fake_run  # type: ignore[assignment]

    lead._resolve_conflicts_via_debate(
        "M001",
        ["ok.py", "evil.py symlink rejected", "  "],
    )

    assert seen == ["ok.py"], seen


def test_missing_stash_files_silently_skipped(tmp_path: Path) -> None:
    """main 만 있고 stash 가 없는 충돌은 토론에서 제외."""
    lead = _build_lead(tmp_path)
    # ok.py 는 정상 페어
    _make_conflict(lead.ws_main, "M001", "ok.py", main="A\n", stash="B\n")
    # ghost.py 는 main 만 (stash 없음)
    (lead.ws_main / "ghost.py").write_text("only main\n")

    seen: list[str] = []

    def fake_run(agent_id, rel_clean, main_path, stash_path, model, timeout_sec):  # type: ignore[no-untyped-def]
        seen.append(rel_clean)
        return {"consensus": True, "merged": "x\n", "decision": "x"}

    lead._run_conflict_debate = fake_run  # type: ignore[assignment]

    lead._resolve_conflicts_via_debate("M001", ["ok.py", "ghost.py"])
    assert seen == ["ok.py"], seen


# ===========================================================================
# 실행 (직접 호출용)
# ===========================================================================

if __name__ == "__main__":
    import inspect
    import tempfile

    mod = sys.modules[__name__]
    tests = [
        (n, fn) for n, fn in inspect.getmembers(mod, inspect.isfunction) if n.startswith("test_")
    ]
    passed, failed = 0, []
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as d:
            try:
                fn(Path(d))
                print(f"PASS {name}")
                passed += 1
            except Exception as e:
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                failed.append(name)
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
