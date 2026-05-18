"""TeamLead — Python 루프 + 짧은 LLM 호출 패턴의 팀장 에이전트.

매 tick:
  1. 새 메시지 스캔 (mailbox)
  2. WAITING 멤버 → LLM이 답변 작성 → mailbox.md에 reply append → 상태 RUNNING → 재spawn
  3. DONE 멤버 → verifier.run → 통과면 workspace merge → registry DONE/FAILED
  4. plan.md에 미할당 sub-goal 있으면 → LLM이 brief 작성 → 멤버 채용 → spawn
  5. timeline.md 재렌더
  6. 종료 조건: plan.md 모두 체크되거나 budget 초과

plan.md 형식:
    # Plan
    - [ ] G-foo: bootstrap python project
    - [x] G-bar: write README  (assigned: M001)
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.auto_merge import try_auto_merge
from core.budget import BudgetExceeded, BudgetManager
from core.health import HealthMonitor
from core.llm import MODEL_OPUS, MODEL_SONNET, LLMClient
from core.rate_limit import RateLimitExhausted
from core.schemas import (
    PLAN_BACKUP_KEEP,
    PlanSchema,
    ValidationFailure,
    validate_decomposer_output,
)
from core.schemas import (
    call_decomposer_with_validation as _call_decomposer_with_validation,
)
from core.schemas import (
    prune_plan_backups as _prune_plan_backups,
)
from core.similarity import (
    GATE_ACTION_BYPASS,
    GATE_ACTION_PASS,
    GATE_ACTION_REFINE,
    GATE_ACTION_SKIP,
    decide_gate,
)
from core.verifier import Check, Verifier
from lead.mailbox import (
    Message,
    append_message,
    build_refine_message,
    detect_terminal_status,
    parse_messages,
    scan_new,
)
from lead.member import (
    HireBrief,
    MemberSpawner,
    SpawnResult,
    is_pid_alive,
    read_pid_file,
)
from lead.prompts import render_split
from lead.registry import AgentRegistry
from lead.timeline import TimelineRenderer
from lead.workspace import WorkspaceMerger

# Plan goal 라인 형식
GOAL_LINE_RE = re.compile(
    r"^- \[(?P<done>[ xX])\] (?P<id>G-[A-Za-z0-9_-]+): (?P<title>.+?)"
    r"(?:\s+\(assigned: (?P<assigned>[A-Za-z0-9_-]+)\))?\s*$"
)

# Decomposer (hire-brief) 가 강제하는 sub-goal 분류 라벨.
# 예: kind="new" (신규 파일/모듈), kind="refine" (시드 정련), kind="extend" (기능 확장),
# kind="remove" (제거/정리). 누락/오타는 코드 차원에서 거부 후 재시도.
_VALID_BRIEF_KINDS: tuple[str, ...] = ("new", "refine", "extend", "remove")
_BRIEF_VALIDATION_MAX_ATTEMPTS = 3
# 멤버 spawn 시 lead 가 brief.model 로 선택 가능한 모델. 비/잘못된 값 → "sonnet" fallback (비용 최적화).
_VALID_MEMBER_MODELS: tuple[str, ...] = ("sonnet", "opus")
_DEFAULT_MEMBER_MODEL: str = "sonnet"
_MISSION_LABEL_RE = re.compile(r"^\s*\[kind=(?P<k>[a-zA-Z]+)\]\s*")


def _ensure_mission_label(mission: str, kind: str) -> str:
    """mission 첫 문장이 `[kind=KIND] ...` prefix 로 시작하도록 보정.

    이미 라벨이 있고 일치하면 그대로 반환.
    """
    m = _MISSION_LABEL_RE.match(mission)
    if m and m.group("k").lower() == kind:
        return mission
    body = mission[m.end() :] if m else mission.lstrip()
    return f"[kind={kind}] {body}"


# 단일 멤버 재spawn 안전 상한 (무한 핑퐁 방지용 마지막 방어선; budget이 1차 차단).
RESUME_SAFETY_CAP = 20

# 진행 정체로 판단할 연속 빈 tick 수 (멤버 spawn은 시간 걸리므로 너무 짧지 않게).
NO_PROGRESS_CAP = 10

# code-janitor 자율 판단 주기 (이만큼 hire 횟수마다 한 번 판단).
JANITOR_CHECK_EVERY_HIRES = 10

# 최종 점검 (pytest) 반복 한도 — 합격 못 하면 사람 결정 요청.
FINAL_VERIFICATION_MAX_ITERATIONS = 5
# pytest 실행 타임아웃 (초)
FINAL_VERIFICATION_TIMEOUT_SEC = 900

# 충돌 토론 동시성 한도 — 한 멤버 산출물의 충돌 파일 N개를 N개의 토론으로 동시에 처리.
# claude/codex CLI 외부 호출이 IO-bound 이므로 asyncio.Semaphore 로 충분.
DEBATE_MAX_PARALLEL = 4
# 한 충돌 1단계(sonnet) 토론 타임아웃 — 도달 시 opus escalate.
DEBATE_ROUND_TIMEOUT_SEC = 240
# 2단계(opus) escalate 타임아웃 — sonnet 보다 더 너그럽게.
DEBATE_ESCALATE_TIMEOUT_SEC = 360

# seed 유사도 게이트 — 같은 멤버에 refine 메시지가 누적 N회 보내졌으면 게이트 우회
# (= debate 로 회부). 무한 refine 핑퐁 방지용.
SEED_GATE_MAX_RESPAWNS = 2

# Goal 분할 마커. goal id 안에 들어있으면 "이미 분할된 sub-goal" 로 보고 다시 분할하지 않음.
# 한 멤버 세션의 max_turns 안에 못 끝나는 큰 goal 이 FAILED 로 떨어지면 첫 실패에
# 이 마커가 붙은 sub-goal 2개로 plan 을 갈아끼우고 재hire 흐름에 맡긴다.
GOAL_SPLIT_MARKER = "-split-"


@dataclass
class Goal:
    id: str
    title: str
    done: bool = False
    assigned: str = ""


@dataclass
class ConflictQueueItem:
    """G-011: 재시작 시 conflicts/*.md 에서 재로드되는 미처리 충돌 항목.

    `agent_id`: 충돌을 일으킨 멤버, `files`: 충돌 파일 상대경로 목록, `path`:
    원본 conflict 마크다운 (정리 후 unlink 용).
    """

    agent_id: str
    files: list[str]
    path: Path


# 멤버 dir 이름 패턴 (`M001`, `M042` …). conflict 파일/agent_id 추출에 재사용.
_AGENT_ID_RE = re.compile(r"^M\d+")

# conflict 마크다운의 "## 충돌 파일" 섹션에서 ``- `rel` —`` 형태 라인 추출.
_CONFLICT_FILE_LINE_RE = re.compile(r"^-\s+`([^`]+)`")


def parse_conflict_file(path: Path) -> ConflictQueueItem | None:
    """conflicts/*.md 한 개를 ConflictQueueItem 으로 변환. 빈 충돌이면 None.

    파싱 규칙:
      - agent_id: 파일명 prefix 가 `M\\d+` 인 것에서 추출 (예: `M003-20260515.md` → M003).
      - files: "## 충돌 파일" 다음의 ``- `rel` — …`` 패턴.
      - 다음 헤더 (`## …`) 만나면 섹션 종료.
    """
    name_match = _AGENT_ID_RE.match(path.name)
    if not name_match:
        return None
    agent_id = name_match.group(0)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    files: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            in_section = "충돌 파일" in stripped
            continue
        if not in_section:
            continue
        m = _CONFLICT_FILE_LINE_RE.match(stripped)
        if m:
            files.append(m.group(1))

    if not files:
        return None
    return ConflictQueueItem(agent_id=agent_id, files=files, path=path)


def mailbox_last_member_message(mailbox_path: Path, agent_id: str) -> Message | None:
    """mailbox.md 에서 `from_=agent_id` 인 마지막 메시지 반환. 없으면 None.

    restore_state 가 lead 재시작 시 멤버 마지막 동작 (delivery/question/status) 을
    한 줄로 분류하기 위한 헬퍼.
    """
    msgs = parse_messages(mailbox_path)
    for m in reversed(msgs):
        if m.from_ == agent_id:
            return m
    return None


def classify_mailbox_state(msg: Message | None) -> str:
    """마지막 멤버 메시지 → 상태 라벨.

    - delivery   → "DONE"
    - question   → "WAITING"
    - status + [STATUS:FAILED] body → "FAILED"
    - status (그 외)               → "RUNNING"
    - None                          → "UNKNOWN"
    """
    if msg is None:
        return "UNKNOWN"
    if msg.kind == "delivery":
        return "DONE"
    if msg.kind == "question":
        return "WAITING"
    if msg.kind == "status":
        token = detect_terminal_status(msg.body)
        if token == "FAILED":
            return "FAILED"
        if token == "DONE":
            return "DONE"
        return "RUNNING"
    return "RUNNING"


def parse_plan(plan_md: Path) -> list[Goal]:
    """plan.md를 파싱해 Goal 리스트 반환."""
    if not plan_md.exists():
        return []
    goals = []
    for line in plan_md.read_text(encoding="utf-8").splitlines():
        m = GOAL_LINE_RE.match(line.strip())
        if not m:
            continue
        goals.append(
            Goal(
                id=m.group("id"),
                title=m.group("title").strip(),
                done=m.group("done").lower() == "x",
                assigned=m.group("assigned") or "",
            )
        )
    return goals


def render_plan(plan_md: Path, header: str, goals: list[Goal]) -> None:
    lines = [f"# {header}", ""]
    for g in goals:
        check = "x" if g.done else " "
        assigned = f" (assigned: {g.assigned})" if g.assigned else ""
        lines.append(f"- [{check}] {g.id}: {g.title}{assigned}")
    plan_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TeamLead:
    def __init__(
        self,
        spec: str,
        spec_name: str,
        state_dir: Path,  # <state_dir>/ — session_logs/ 위치 결정
        lead_state_dir: Path,  # <state_dir>/lead/
        agents_root: Path,  # <state_dir>/agents/
        session_logs_root: Path,  # <state_dir>/session_logs/
        ws_root: Path,  # ws/members/ — 멤버 격리 ws 들의 부모
        ws_main: Path,  # ws/main/ (= args.workspace) — 머지 결과
        llm: LLMClient,
        budget: BudgetManager,
        health: HealthMonitor | None = None,
        default_model: str = "opus",
        enable_evaluator: bool = False,
        max_parallel: int = 3,
        replan: bool = False,
    ):
        self.spec = spec
        self.spec_name = spec_name
        self.state_dir = state_dir
        self.lead_state_dir = lead_state_dir
        self.agents_root = agents_root
        self.session_logs_root = session_logs_root
        self.ws_root = ws_root
        self.ws_main = ws_main
        self.llm = llm
        self.budget = budget
        self.health = health
        self.enable_evaluator = enable_evaluator
        self.max_parallel = max(1, max_parallel)
        self.replan = replan

        lead_state_dir.mkdir(parents=True, exist_ok=True)
        agents_root.mkdir(parents=True, exist_ok=True)
        ws_root.mkdir(parents=True, exist_ok=True)
        ws_main.mkdir(parents=True, exist_ok=True)

        self.plan_md = lead_state_dir / "plan.md"
        self.registry = AgentRegistry(lead_state_dir, agents_root)
        self.spawner = MemberSpawner(agents_root, ws_root, state_dir, default_model=default_model)
        self.merger = WorkspaceMerger(ws_main, lead_state_dir / "conflicts")
        self.timeline = TimelineRenderer(lead_state_dir, agents_root, session_logs_root)

        # 병렬 spawn: agent_id → 진행 중인 spawn future.
        # SessionManager는 각자 다른 cwd/log_dir 쓰니 동시 호출 OK.
        self._executor: ThreadPoolExecutor | None = None
        self._pending: dict[str, Future[Any]] = {}
        # 멤버 브리프 캐시 (resume 시 재구성용; system_prompt.md만으론 부족한 미션 등 보존)
        self._briefs: dict[str, HireBrief] = {}
        # janitor 판단 카운터 (hire 횟수 누적)
        self._hires_since_janitor = 0
        # 최종 점검 (pytest) 반복 카운터 — 한도 도달하면 사람 결정 코드 (5) 반환
        self._final_verification_count = 0
        # G-011: 재시작 복구. restore_state 가 PID 살아있는 멤버를 _reattached 에 추가.
        # _recover_zombies 는 _reattached 에 있는 멤버를 건너뜀 (실제 in-flight 자식).
        self._reattached: set[str] = set()
        # G-011: 미처리 충돌 파일 큐. restore_state 가 conflicts/*.md 글롭하여 채움.
        self.conflict_queue: list[ConflictQueueItem] = []
        # G-011: SIGTERM/SIGINT 핸들러가 set. run 루프가 polling 으로 감지 → drain.
        self._shutdown_requested: bool = False

    # ---------- 진입점 ----------

    def run(self) -> int:
        """tick 루프. 종료 조건: 모든 goal done | budget 초과 | 진행 불가."""
        self._log("=" * 60)
        self._log(f"팀장 시작 | spec={self.spec_name} | max_parallel={self.max_parallel}")
        self._log("=" * 60)

        # 이전 run 이 죽어 zombie 가 된 RUNNING 멤버 복구
        # (delivery 있으면 DONE 으로, 없으면 FAILED + plan 에서 unassign)
        self._recover_zombies()

        # --replan: 기존 plan.md 를 timestamp 붙여 백업하고 spec 기반 재분해.
        # "main 점진 강화" 흐름에서 새 spec 으로 다시 plan 분해할 때 사용.
        if self.replan and self.plan_md.exists():
            archive = self.plan_md.with_name(f"plan.replaced-{int(time.time())}.md")
            self.plan_md.rename(archive)
            _prune_plan_backups(self.lead_state_dir, keep=PLAN_BACKUP_KEEP)
            self._log(f"  📋 --replan: 기존 plan → {archive.name} 으로 백업, 재분해 진행")
            self.timeline.emit(
                "lead", "plan_update", note=f"--replan: 기존 plan archive → {archive.name}"
            )

        if not self.plan_md.exists():
            self._initial_plan()

        consecutive_no_progress = 0
        with ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="spawn") as ex:
            self._executor = ex
            try:
                while self.budget.can_continue() or self._pending or self._has_unverified_done():
                    try:
                        progressed = self._tick()
                    except BudgetExceeded:
                        # in-flight 다 끝나길 기다림
                        self._drain_pending()
                        raise
                    except RateLimitExhausted as e:
                        self._log(f"🚫 rate limit 한도: {e}")
                        self._drain_pending()
                        # ExitCode.RATE_LIMIT_EXHAUSTED — 더 이상 BUDGET 과 같은 4 가 아님.
                        # stderr hint 도 함께 흘려 사용자가 다음 행동을 알 수 있게.
                        from core.exit_codes import ExitCode, print_hint
                        print_hint(ExitCode.RATE_LIMIT_EXHAUSTED)
                        return int(ExitCode.RATE_LIMIT_EXHAUSTED)

                    self.timeline.render()

                    goals = parse_plan(self.plan_md)
                    if goals and all(g.done for g in goals) and not self._pending:
                        # 모든 goal 완료 → 팀장 최종 점검 (pytest 전체 실행)
                        passed, output = self._final_verification()
                        if passed:
                            self._log("\n🎉 최종 점검 통과 — 모든 goal 완료")
                            self.timeline.emit("lead", "final_verification_pass")
                            self.timeline.render()
                            return 0
                        # 실패 — 한도 검사 후 fix goal 자동 추가
                        self._final_verification_count += 1
                        if self._final_verification_count >= FINAL_VERIFICATION_MAX_ITERATIONS:
                            self._log(
                                f"\n⚠️ 최종 점검 {self._final_verification_count}회 실패 — "
                                "사람 결정 필요"
                            )
                            self.timeline.emit(
                                "lead",
                                "final_verification_exhausted",
                                iterations=self._final_verification_count,
                            )
                            self.timeline.render()
                            return 5
                        added = self._add_fix_goals_from_failures(output)
                        self._log(
                            f"\n🔁 최종 점검 {self._final_verification_count}회 — "
                            f"실패 분석 → fix goal {added}개 추가, 재진행"
                        )
                        self.timeline.emit(
                            "lead",
                            "final_verification_fail",
                            iterations=self._final_verification_count,
                            added_goals=added,
                        )
                        # 진행률 리셋 — 새 goal 들이 hire 될 시간 줌
                        consecutive_no_progress = 0
                        continue  # 다음 tick — 새 goal hire 진행

                    if not progressed:
                        consecutive_no_progress += 1
                        if consecutive_no_progress >= NO_PROGRESS_CAP and not self._pending:
                            summary = self._registry_summary()
                            self._log(f"⏸️  {NO_PROGRESS_CAP}회 연속 무진행. registry={summary}")
                            self.timeline.emit(
                                "lead", "error", error=f"no progress (registry={summary})"
                            )
                            return 3
                    else:
                        consecutive_no_progress = 0

                    # in-flight가 있는데 새 hire 없으면 짧게 쉬어 CPU 안 태움
                    if self._pending and not progressed:
                        time.sleep(0.5)

                self._log("⏰ budget 한도")
                return 4
            finally:
                self._executor = None

    # ---------- tick ----------

    def _tick(self) -> bool:
        """1 사이클. 어떤 변화가 있었으면 True."""
        progressed = False

        # 0) 완료된 spawn future 수집 → _post_spawn 처리
        if self._collect_completed_spawns():
            progressed = True

        # 1) 새 메시지 (status, question, delivery) 스캔
        # registry 에 없는 agent_id 의 메시지는 last_msg_id 갱신 못 해서 다음 tick 에 또
        # 잡힘 → 무한 로그 폭주. 이런 orphan(이전 run 자식 claude 잔여물)은 mailbox 디렉토리
        # 자체를 격리해 scan_new 가 더 못 보게 한다.
        new_msgs = scan_new(self.agents_root, self.registry.last_seen_map())
        orphan_ids: set[str] = set()
        for m in new_msgs:
            agent_id = m.from_ if m.to == "lead" else m.to
            rec = self.registry.get(agent_id)
            if rec is None:
                if agent_id not in self._pending:
                    orphan_ids.add(agent_id)
                continue
            self._log(f"  📨 {m.from_}→{m.to} {m.kind} #{m.id}")
            self.registry.update(agent_id, last_msg_id=m.id)
            progressed = True

        for orphan_id in orphan_ids:
            src = self.agents_root / orphan_id
            if not src.exists():
                continue
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            dst = self.lead_state_dir / "orphan_agents" / f"{orphan_id}-{ts}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                src.rename(dst)
                self._log(f"  🗑️  orphan {orphan_id} → orphan_agents/ 격리 (registry 없음)")
                self.timeline.emit(
                    "lead",
                    "orphan_archived",
                    agent_id=orphan_id,
                    archive=str(dst),
                )
                progressed = True
            except OSError as e:
                self._log(f"  ⚠️ orphan {orphan_id} 격리 실패: {e}")

        # 2) WAITING 멤버 → 답변 → 재spawn (비동기 submit)
        for rec in self.registry.by_status("WAITING"):
            if rec.agent_id in self._pending:
                continue  # 이미 spawn 진행 중
            if not self.budget.can_continue():
                break
            if self._handle_waiting(rec.agent_id):
                progressed = True

        # 3) DONE 멤버 → 검증 → merge
        for rec in self.registry.by_status("DONE"):
            if rec.agent_id in self._pending:
                continue  # 재spawn 중
            if rec.completed_at and self._already_merged(rec.agent_id):
                continue
            if self._verify_and_merge(rec.agent_id):
                progressed = True

        # 4) 미할당 goal → 채용 (in-flight 수가 max_parallel 미만일 때 반복)
        while len(self._pending) < self.max_parallel and self.budget.can_continue():
            if not self._hire_next_unassigned():
                break
            progressed = True
            self._hires_since_janitor += 1

        # 5) 주기적으로 lead 자체 판단: 지금 code-janitor 돌릴 시점?
        if self._hires_since_janitor >= JANITOR_CHECK_EVERY_HIRES:
            self._hires_since_janitor = 0
            if self._should_run_code_janitor():
                self._run_code_janitor()
                progressed = True

        return progressed

    # ---------- 최종 점검 (pytest) 게이트 ----------

    def _final_verification(self) -> tuple[bool, str]:
        """ws/main 에서 pytest 전체 실행. (passed, output) 반환.

        venv 가 있으면 그쪽 python 사용, 없으면 시스템 python3.
        timeout 도달 시 (False, 'timeout') 반환.
        """
        import subprocess

        self._log("=" * 60)
        self._log(
            f"🔍 팀장 최종 점검 — pytest 전체 실행 (반복 #{self._final_verification_count + 1})"
        )
        self._log("=" * 60)

        venv_py = self.ws_main / ".venv" / "bin" / "python"
        py_cmd = [str(venv_py)] if venv_py.exists() else ["python3"]
        cmd = [*py_cmd, "-m", "pytest", "--tb=short", "-q", "--no-header"]

        try:
            result = subprocess.run(
                cmd,
                cwd=self.ws_main,
                capture_output=True,
                text=True,
                timeout=FINAL_VERIFICATION_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return False, f"pytest timeout after {FINAL_VERIFICATION_TIMEOUT_SEC}s"
        except FileNotFoundError as e:
            return False, f"pytest 실행 불가: {e} — venv/의존성 설치 필요"

        # pytest exit codes: 0=all pass, 1=fail, 5=no tests collected.
        # 5 (no tests) 는 "검증할 게 없음" → 통과로 취급 (early-stage / non-Python 프로젝트).
        passed = result.returncode in (0, 5)
        # 출력 너무 크면 끝부분만 (실패 정보가 끝에 모임)
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        if len(output) > 8000:
            output = "... (앞부분 생략) ...\n" + output[-8000:]
        self._log(f"  pytest exit={result.returncode}  {'PASS' if passed else 'FAIL'}")
        return passed, output

    def _add_fix_goals_from_failures(self, pytest_output: str) -> int:
        """pytest 실패 출력 분석 → 새 plan goal 추가. 추가된 개수 반환.

        LLM (opus) 가 실패 로그 보고 수정 작업을 sub-goal 단위로 분해.
        """
        system = (
            "너는 팀장이다. pytest 실패 로그를 보고 어떤 수정 작업이 필요한지 정리해 "
            "각 수정을 별개 sub-goal 로 JSON 배열로 출력. JSON 외 텍스트 금지."
        )
        user = (
            f"# pytest 실패 출력\n```\n{pytest_output}\n```\n\n"
            "# 출력 (정확히 JSON 배열 하나)\n"
            '[{"id": "G-FIX-NNN-slug", "title": "수정 작업 1-2문장 설명"}, ...]\n\n'
            "규칙:\n"
            "- 각 goal 은 한 명의 멤버가 한 사이클에 끝낼 수 있는 단위.\n"
            "- 여러 테스트가 동일 원인이면 1 goal 로 묶기.\n"
            "- id 는 `G-FIX-001-...` 형식, 기존 plan 의 id 와 안 겹치게.\n"
            "- 실패가 없거나 분석 불가하면 빈 배열 `[]` 반환."
        )
        try:
            raw = self.llm.call(system, user, tier="opus")
        except Exception as e:
            self._log(f"  ⚠ 실패 분석 LLM 실패: {e}")
            return 0

        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            self._log("  ⚠ LLM 응답에 JSON 배열 없음")
            return 0
        try:
            new_goals = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            self._log(f"  ⚠ JSON 파싱 실패: {e}")
            return 0
        if not isinstance(new_goals, list):
            return 0

        # plan.md 에 추가 (id 중복 방지)
        goals = parse_plan(self.plan_md)
        existing_ids = {g.id for g in goals}
        added = 0
        for ng in new_goals:
            if not isinstance(ng, dict):
                continue
            gid = str(ng.get("id", "")).strip()
            title = str(ng.get("title", "")).strip()
            if not gid or not title or gid in existing_ids:
                continue
            goals.append(Goal(id=gid, title=title, done=False, assigned=""))
            existing_ids.add(gid)
            added += 1
        if added:
            render_plan(self.plan_md, "Plan", goals)
            self.timeline.emit(
                "lead", "plan_update", note=f"final verification 실패 → fix goal {added}개 추가"
            )
        return added

    # ---------- 좀비 복구 / plan 동기화 ----------

    def _recover_zombies(self) -> None:
        """startup 시 RUNNING 표기인데 spawn future 없는 멤버 = 이전 run 좀비.

        delivery.md 가 충분히 차있으면 → DONE 으로 승격 (verify+merge 경로 태움)
        delivery.md 비어있으면 → FAILED + plan 에서 unassign (다음 tick 에 재hire)
        """
        running = self.registry.by_status("RUNNING")
        if not running:
            return
        for rec in running:
            delivery = self.agents_root / rec.agent_id / "delivery.md"
            size = delivery.stat().st_size if delivery.exists() else 0
            if size >= 200:
                self.registry.set_status(rec.agent_id, "DONE")
                self._log(f"  🔄 좀비 복구: {rec.agent_id} RUNNING → DONE (delivery {size}B)")
                self.timeline.emit(
                    "lead",
                    "zombie_recovered",
                    agent_id=rec.agent_id,
                    to="DONE",
                    delivery_bytes=size,
                )
            else:
                self.registry.update(
                    rec.agent_id, status="FAILED", last_error="이전 run zombie (delivery 비어있음)"
                )
                self._unassign_from_plan(rec.agent_id)
                self._log(f"  🔄 좀비 복구: {rec.agent_id} RUNNING → FAILED + plan unassign")
                self.timeline.emit(
                    "lead",
                    "zombie_recovered",
                    agent_id=rec.agent_id,
                    to="FAILED",
                    delivery_bytes=size,
                )

    def _unassign_from_plan(self, agent_id: str) -> None:
        """plan.md 의 goals 중 이 agent 에게 assigned 된 미완료 goal 의 assigned 를 비움."""
        if not self.plan_md.exists():
            return
        goals = parse_plan(self.plan_md)
        changed = False
        for g in goals:
            if g.assigned == agent_id and not g.done:
                g.assigned = ""
                changed = True
        if changed:
            render_plan(self.plan_md, "Plan", goals)

    # ---------- goal 분할 (FAILED 1회 후) ----------

    def _maybe_split_failed_goal(self, agent_id: str, error: str) -> bool:
        """첫 실패면 goal 을 2개 sub-goal 로 분할해 plan 갈아끼움. 분할 적용 시 True."""
        rec = self.registry.get(agent_id)
        if rec is None:
            return False
        goal_id = rec.goal_id

        # 이미 분할된 sub-goal 이면 또 분할 금지 (무한 분할 방지).
        if GOAL_SPLIT_MARKER in goal_id:
            return False

        # 같은 goal_id 로 FAILED 가 이번이 첫 번째인가? (registry 는 방금 이번 실패를 반영한 상태)
        same_goal_failed = sum(
            1 for r in self.registry.all() if r.goal_id == goal_id and r.status == "FAILED"
        )
        if same_goal_failed > 1:
            return False  # 이전에 이미 시도했거나 분할 후 또 실패 → 그냥 unassign 흐름

        goals = parse_plan(self.plan_md)
        target = next((g for g in goals if g.id == goal_id), None)
        if target is None or target.done:
            return False

        sub_goals = self._llm_split_goal(target)
        if not sub_goals or len(sub_goals) != 2:
            return False

        new_goals: list[Goal] = []
        for g in goals:
            if g.id == goal_id:
                new_goals.extend(sub_goals)
            else:
                new_goals.append(g)
        render_plan(self.plan_md, "Plan", new_goals)
        self._log(
            f"  ✂️  goal 분할: {goal_id} → {sub_goals[0].id} + {sub_goals[1].id} "
            f"(원인: {error[:80]})"
        )
        self.timeline.emit(
            "lead",
            "goal_split",
            parent=goal_id,
            children=[g.id for g in sub_goals],
            reason=error[:140],
        )
        return True

    def _llm_split_goal(self, goal: Goal) -> list[Goal] | None:
        """LLM 으로 goal title 을 2개 sub-goal title 로 쪼갠다. opus 1콜."""
        system = (
            "너는 팀장의 plan 분해 보조. 큰 작업이 한 멤버 세션에서 안 끝나서 FAILED "
            "되었다. 이 goal 을 정확히 2개의 작은 sub-goal 로 분할하라. 각 sub-goal 은 "
            "독립적으로 한 멤버가 끝낼 수 있어야 하고, 둘을 합치면 원래 goal 을 덮어야 한다. "
            'JSON 한 줄만 출력: {"a": "첫 번째 sub-goal title", "b": "두 번째 sub-goal title"}. '
            "JSON 외 텍스트 금지. 각 title 은 한 문장."
        )
        user = (
            f"# 분할 대상\n"
            f"id: {goal.id}\n"
            f"title: {goal.title}\n\n"
            "# 출력 (정확히 JSON)\n"
            '{"a": "...", "b": "..."}'
        )
        try:
            from core.llm import parse_json_loose

            raw = self.llm.call(system, user, tier="opus")
            data = parse_json_loose(raw)
            a = str(data.get("a") or "").strip()
            b = str(data.get("b") or "").strip()
            if not a or not b:
                return None
            return [
                Goal(id=f"{goal.id}{GOAL_SPLIT_MARKER}a", title=a),
                Goal(id=f"{goal.id}{GOAL_SPLIT_MARKER}b", title=b),
            ]
        except Exception as e:
            self._log(f"  ⚠ goal 분할 LLM 실패: {e}")
            return None

    def _ws_main_summary(self, max_files: int = 60) -> str:
        """plan_initial / hire_brief 컨텍스트로 ws/main 의 .py 파일 목록.

        경로 + 라인 수 + 모듈 docstring 첫 줄.
        라인 수가 있으면 decomposer 가 *큰 파일=수정 대상* /
        *작은 stub=신규 작성 여지* 를 판단하기 쉬워진다.
        모듈 docstring 첫 줄이 있으면 함께 노출해 시드 정합성(refine vs new)
        판단 신호를 강화한다.
        """
        if not self.ws_main.exists():
            return "(비어있음)"
        files: list[str] = []
        for p in sorted(self.ws_main.rglob("*.py")):
            parts = p.relative_to(self.ws_main).parts
            if "__pycache__" in parts or ".venv" in parts or ".archive" in parts:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
            lines = len(text.splitlines())
            doc_summary = ""
            if text:
                try:
                    module_doc = ast.get_docstring(ast.parse(text))
                except (SyntaxError, ValueError):
                    module_doc = None
                if module_doc:
                    doc_summary = module_doc.splitlines()[0].strip()
            rel = p.relative_to(self.ws_main)
            if doc_summary:
                files.append(f"{rel} ({lines}L) — {doc_summary}")
            else:
                files.append(f"{rel} ({lines}L)")
            if len(files) >= max_files:
                break
        if not files:
            return "(.py 파일 없음)"
        return "\n".join(f"- {f}" for f in files)

    # ---------- LLM 결정 ----------

    def _initial_plan(self) -> None:
        """spec → plan.md 초기 분해 (한 번만). JSON 형식이면 strict 검증, 아니면 markdown."""
        self._log("🧭 plan.md 초기 분해")
        system, user = render_split(
            "plan_initial",
            spec_name=self.spec_name,
            spec=self.spec[:6000],
            ws_main_tree=self._ws_main_summary(),
        )

        def _extract_and_write(raw_text: str) -> int:
            """LLM 응답을 plan.md 에 저장하고 파싱된 goal 수 반환."""
            m = re.search(r"```[a-zA-Z]*\s*\n(.*?)\n```", raw_text, re.DOTALL)
            body = (m.group(1) if m else raw_text).strip()
            if not body.lower().startswith("# plan"):
                body = "# Plan\n" + body
            self.plan_md.write_text(body + "\n", encoding="utf-8")
            return len(parse_plan(self.plan_md))

        try:
            # 한 번만 호출되는 핵심 결정 — 모든 goal scope 가 여기서 결정됨. opus.
            raw = self.llm.call(system, user, tier="opus")
        except Exception as e:
            self._log(f"⚠️  초기 plan LLM 실패, fallback: {e}")
            self.plan_md.write_text(
                "# Plan\n- [ ] G-001-bootstrap: 요구서를 읽고 첫 작업 결정\n",
                encoding="utf-8",
            )
            self.timeline.emit("lead", "plan_update", note="fallback initial plan")
            return

        # JSON 경로: 응답이 JSON 객체로 시작하면 strict pydantic 검증 + 재시도 루프.
        # markdown 경로는 기존 로직 그대로. 두 경로는 서로 충돌하지 않게 분기.
        stripped = raw.lstrip()
        if stripped.startswith("{") or stripped.startswith("```json"):
            try:
                plan_schema = validate_decomposer_output(raw)
            except ValidationFailure as vf:
                self._log(f"⚠️  JSON plan 검증 실패 1회 — retry 루프 진입: {vf.reason[:160]}")
                try:
                    plan_schema = _call_decomposer_with_validation(
                        self.llm,
                        system,
                        user,
                        tier="opus",
                        log=self._log,
                    )
                except ValidationFailure as vf2:
                    self._log(
                        f"⚠️  JSON plan retry 모두 실패 — markdown 경로 fallback: {vf2.reason[:160]}"
                    )
                    plan_schema = None
            if plan_schema is not None:
                self._write_plan_from_schema(plan_schema)
                self.timeline.emit(
                    "lead",
                    "plan_update",
                    note=f"strict JSON plan ({len(plan_schema.sub_goals)} goals)",
                )
                return

        n = _extract_and_write(raw)
        if n == 0:
            # LLM 이 형식을 어김 (서술 단락만, goal 라인 없음). strict 재시도 1회.
            self._log("⚠️  초기 plan 출력에 goal 라인 0개 — strict 재시도")
            strict_system = system + (
                "\n\nABSOLUTE FORMAT: 응답 본문은 정확히 `# Plan` 한 줄로 시작하고, "
                "그 뒤에 `- [ ] G-NNN-id: title` 형식의 goal 라인만 나열한다. "
                "헤더/서술/요약/노트 일체 금지. 묶음 표기(G-001~003) 금지. "
                "이 형식을 어기면 시스템이 plan 을 파싱하지 못해 작업이 즉시 중단된다."
            )
            try:
                raw2 = self.llm.call(strict_system, user, tier="opus")
                n = _extract_and_write(raw2)
            except Exception as e:
                self._log(f"⚠️  strict 재시도 실패: {e}")
                n = 0
            if n == 0:
                self._log(
                    "❌ strict 재시도 후에도 goal 0개 — 안전 fallback 으로 1개 bootstrap goal 작성"
                )
                self.plan_md.write_text(
                    "# Plan\n- [ ] G-001-bootstrap: 요구서를 읽고 첫 작업 결정 "
                    "(LLM 형식 위반으로 자동 분해 실패)\n",
                    encoding="utf-8",
                )
                self.timeline.emit(
                    "lead", "plan_update", note="LLM 형식 위반 → fallback bootstrap goal"
                )
                return

        self.timeline.emit("lead", "plan_update", note=f"초기 plan 작성됨 ({n} goals)")

    def _write_plan_from_schema(self, plan: PlanSchema) -> None:
        """strict JSON 검증 통과한 PlanSchema 를 markdown plan.md 로 직렬화."""
        goals = [Goal(id=g.id, title=g.title, done=False, assigned="") for g in plan.sub_goals]
        render_plan(self.plan_md, "Plan", goals)

    def _hire_next_unassigned(self) -> bool:
        goals = parse_plan(self.plan_md)
        unassigned = [g for g in goals if not g.done and not g.assigned]
        if not unassigned:
            return False
        target = unassigned[0]
        self._log(f"👷 채용 시도 → {target.id}: {target.title}")

        brief_data = self._llm_hire_brief(target)
        if not brief_data:
            return False

        agent_id = self.registry.next_agent_id()
        brief = HireBrief(
            agent_id=agent_id,
            goal_id=target.id,
            mission=brief_data["mission"],
            deliverables=brief_data["deliverables"],
            verification_checks=list(brief_data.get("verification_checks") or []),
            system_prompt=brief_data["system_prompt"],
            allowed_tools=list(brief_data.get("allowed_tools") or []) or None,
            seed_files=list(brief_data.get("seed_files") or []) or None,
            verify=bool(brief_data.get("verify", False)),
            model=brief_data.get("model"),
        )
        self.spawner.write_brief(brief)
        self.registry.register(agent_id, goal_id=target.id, model=brief.model or "")
        self._log(f"  🎯 {agent_id} model={brief.model or '(default)'}")

        # plan.md 갱신 (assigned 마킹)
        target.assigned = agent_id
        render_plan(self.plan_md, "Plan", goals)
        self.timeline.emit(
            "lead",
            "hire",
            agent_id=agent_id,
            goal=f"{target.id}: {target.title}",
            model=brief.model or "",
        )

        # 첫 instruction
        append_message(
            self.agents_root / agent_id / "mailbox.md",
            from_="lead",
            to=agent_id,
            kind="instruction",
            body=f"# 첫 지시\n{brief.mission}\n\n작업 시작.",
        )

        self._submit_spawn(brief, resume_count=0)
        return True

    def _llm_hire_brief(self, goal: Goal) -> dict[str, Any] | None:
        """LLM에게 채용 brief(JSON) 작성 시키기.

        kind 라벨 (new|refine|extend|remove) 누락/오류 시 코드 차원 재시도
        (최대 _BRIEF_VALIDATION_MAX_ATTEMPTS 회). 모두 실패해도 mission 만 확보되면
        fallback kind="new" 로 진행 — brief 자체를 못 만드는 것보다 라벨 부정확이 회복 가능.
        반환 dict 의 mission 첫 문장은 항상 [kind=KIND] prefix 로 정규화된다.
        """
        from core.llm import parse_json_loose

        system, user = render_split(
            "hire_brief",
            spec=self.spec[:3000],
            goal_id=goal.id,
            goal_title=goal.title,
            ws_main_tree=self._ws_main_summary(),
        )
        # 재시도 시 prepend 할 strict 안내. LLM 에 정확한 형식 예시 노출.
        strict_suffix = (
            "\n\nHARD REQUIREMENT: 응답 JSON 에 `kind` 필드를 반드시 포함. "
            'kind="new" | kind="refine" | kind="extend" | kind="remove" 중 정확히 하나. '
            "누락/오타는 자동 거부 + 재시도 비용 발생. mission 첫 문장도 동일한 라벨 prefix "
            '(예: "[kind=refine] ...") 로 시작 권장.'
        )

        last_data: dict[str, Any] | None = None
        last_error: str = "no attempt completed"
        for attempt in range(1, _BRIEF_VALIDATION_MAX_ATTEMPTS + 1):
            sys_prompt = system if attempt == 1 else (system + strict_suffix)
            try:
                # 멤버 mission/검증 정의 — 너무 좁으면 부족, 너무 넓으면 멤버 헤맴. opus.
                # RateLimitExhausted/BudgetExceeded 는 run() 루프가 처리하도록 의도적으로 propagate.
                raw = self.llm.call(sys_prompt, user, tier="opus")
            except (RuntimeError, OSError, ValueError) as e:
                last_error = f"LLM 호출 실패: {e}"
                self._log(
                    f"  ⚠️ hire-brief 시도 {attempt}/{_BRIEF_VALIDATION_MAX_ATTEMPTS} LLM 실패: {e}"
                )
                continue

            # parse_json_loose 는 실패 시 {} 반환 (예외 안 던짐) — 별도 try 불필요.
            data = parse_json_loose(raw)
            if not data or "mission" not in data:
                last_error = "mission 필드 누락"
                self._log(
                    f"  ⚠️ hire-brief 시도 {attempt}/{_BRIEF_VALIDATION_MAX_ATTEMPTS} mission 누락"
                )
                continue

            last_data = data
            kind_raw = str(data.get("kind", "")).strip().lower()
            if kind_raw in _VALID_BRIEF_KINDS:
                return self._finalize_brief(data, kind_raw)

            last_error = f"kind 필드 누락/오류 ({kind_raw!r})"
            self._log(
                f"  ⚠️ hire-brief 시도 {attempt}/{_BRIEF_VALIDATION_MAX_ATTEMPTS} "
                f"kind 검증 실패 ({kind_raw!r}) — 재시도"
            )

        # 모든 시도 종료. mission 만이라도 확보됐으면 fallback kind="new" 로 진행.
        if last_data is not None:
            self._log(
                f"  ⚠️ hire-brief kind 재시도 {_BRIEF_VALIDATION_MAX_ATTEMPTS}회 실패 "
                f'— fallback kind="new" 적용 ({last_error})'
            )
            self.timeline.emit(
                "lead",
                "error",
                error=f"hire-brief kind 검증 fallback: {last_error}",
                goal=goal.id,
            )
            return self._finalize_brief(last_data, "new")

        self._log(f"  ⚠️ hire-brief 모든 시도 실패: {last_error}")
        self.timeline.emit("lead", "error", error=f"hire-brief: {last_error}", goal=goal.id)
        return None

    def _finalize_brief(self, data: dict[str, Any], kind: str) -> dict[str, Any]:
        """검증 통과한 brief data 의 후처리.

        기본값 채움, mission 라벨 prefix 보정, seed_files 자동 보완.
        """
        data["kind"] = kind
        data.setdefault("deliverables", [])
        data.setdefault("verification_checks", [])
        data.setdefault("system_prompt", "너는 능력 있는 엔지니어. 미션 완수 후 검증 기준 통과.")
        data["mission"] = _ensure_mission_label(str(data["mission"]), kind)

        # lead 가 결정한 멤버 모델 (sonnet/opus). 누락/오타는 sonnet 으로 떨어뜨려 비용 보호.
        model_raw = str(data.get("model", "")).strip().lower()
        if model_raw not in _VALID_MEMBER_MODELS:
            if model_raw:
                self._log(f"  🔧 model 잘못된 값 ({model_raw!r}) → {_DEFAULT_MEMBER_MODEL} fallback")
            model_raw = _DEFAULT_MEMBER_MODEL
        data["model"] = model_raw

        # 자동 보완: deliverables 의 파일 경로가 ws_main 에 실재하면 seed_files 강제 포함.
        # decomposer LLM 이 빠뜨려도 시드 누락 → 100% 충돌 패턴 방지.
        seed = list(data.get("seed_files") or [])
        seed_set = set(seed)
        added: list[str] = []
        for d in data["deliverables"]:
            path = str(d).split("—")[0].split(" - ")[0].strip()
            if not path or path in seed_set:
                continue
            if (self.ws_main / path).is_file():
                seed.append(path)
                seed_set.add(path)
                added.append(path)
        if added:
            data["seed_files"] = seed
            preview = ", ".join(added[:3]) + ("…" if len(added) > 3 else "")
            self._log(f"  🔧 seed_files 자동 보완 +{len(added)} ({preview})")
        return data

    def _handle_waiting(self, agent_id: str) -> bool:
        mbox = self.agents_root / agent_id / "mailbox.md"
        msgs = parse_messages(mbox)
        last_q = next((m for m in reversed(msgs) if m.kind == "question"), None)
        if last_q is None:
            # WAITING인데 question 없음 — 비정상. RUNNING으로 되돌리고 재spawn 시도
            self._log(f"  ⚠️ {agent_id} WAITING인데 question 없음, RUNNING 복귀")
            self.registry.set_status(agent_id, "RUNNING")
            return False

        # 이미 답변 보냈는지 (last_q 이후 reply 있나)
        if any(m.kind == "reply" and (m.ref == last_q.id) for m in msgs if m.id > last_q.id):
            # 답변은 줬는데 멤버가 아직 재spawn 안 됨 → 재spawn
            return self._resume_member(agent_id)

        # 1차 판단: 단순 답변으로 충분한가 vs 4-way 토론 필요한가
        # (간단한 yes/no는 직접, 보안/아키텍처/논쟁적 결정은 토론)
        is_high_stakes = self._is_high_stakes_question(last_q.body)
        if is_high_stakes:
            self._log(f"  ⚖️  {agent_id} 질문 high-stakes 판정 → 토론 소집")
            reply_body = self._convene_debate_for_question(agent_id, last_q, msgs)
        else:
            self._log(f"  💬 {agent_id} 질문 답변 작성")
            reply_body = self._llm_reply(agent_id, last_q, msgs)
        append_message(
            mbox, from_="lead", to=agent_id, kind="reply", body=reply_body, ref=last_q.id
        )
        self.timeline.emit("lead", "reply", to=agent_id, ref=last_q.id, summary=reply_body[:200])
        return self._resume_member(agent_id)

    def _is_high_stakes_question(self, question_body: str) -> bool:
        """LLM(haiku)에게 짧게 판단. 정책: 기본 = 토론, 단순 lookup 만 단독 답변.

        애매하면 토론 쪽으로 (belief entrenchment 완화 + 다관점 검토 비용 < 잘못된 결정 비용).
        호출자: _handle_waiting — 멤버가 [STATUS:WAITING] + question 보고했을 때.
        """
        system = (
            "너는 팀장의 분류 보조다. 팀원 질문이 (a) 단일 정답이 있는 단순 lookup 인지 "
            "(b) 여러 관점·trade-off 가 얽힌 결정인지 판정. 애매하면 (b) 로 분류해 "
            "토론으로 다관점 검토. JSON 한 줄만 출력."
        )
        user = (
            f"# 질문\n{question_body[:1500]}\n\n"
            "# 출력 (정확히 JSON)\n"
            '{"high_stakes": true|false, "reason": "한 줄"}\n\n'
            "**기본은 high_stakes=true** (토론 소집). false 는 아래 조건 *모두* 만족할 때만:\n"
            "  - 단일 정답이 있는 단순 사실/문서/스펙 인용 질문\n"
            "  - yes/no 한 단어로 답할 수 있는 명확한 방향 확인\n"
            "  - 한 줄 답변으로 충분하고 trade-off 가 전혀 없는 디테일\n\n"
            "**high_stakes=true 로 가는 예** (조금이라도 복잡하면 전부):\n"
            "  설계 선택, 라이브러리/패턴 선정, 테스트 전략, 인터페이스 변경, "
            "성능 vs 안전 trade-off, spec 모호함 해석, 우선순위, 에러 처리 방침, "
            "데이터 모델 결정, 명명 컨벤션 선택, 외부 의존성 선택 등."
        )
        try:
            from core.llm import parse_json_loose

            raw = self.llm.call(system, user, tier="haiku")
            data = parse_json_loose(raw)
            return bool(data.get("high_stakes"))
        except Exception as e:
            # 판정 실패 시 사용자 정책에 맞게 토론 쪽으로 fallback (안전한 쪽)
            self._log(f"  ⚠️ high-stakes 판정 실패, 토론으로 fallback: {e}")
            return True

    def _convene_debate_for_question(
        self, agent_id: str, question: Message, all_msgs: list[Message]
    ) -> str:
        """4-way 토론(claude×3 + codex×1) → 자동 결정 → reply 본문 반환."""
        from agents.debate import DebatePanel

        brief = (self.agents_root / agent_id / "brief.md").read_text(encoding="utf-8")
        recent = all_msgs[-6:]
        thread = "\n\n".join(f"### {m.from_}→{m.to} {m.kind} #{m.id}\n{m.body}" for m in recent)
        context = (
            f"# 멤버 brief\n{brief[:1500]}\n\n"
            f"# 최근 mailbox 스레드\n{thread[:2000]}\n\n"
            f"# spec 발췌\n{self.spec[:1500]}"
        )
        panel = DebatePanel(self.lead_state_dir / "debates", self.llm, max_rounds=2)
        debate_id = f"{agent_id}-q{question.id}-{int(time.time())}"
        try:
            outcome = panel.deliberate(
                question=question.body,
                context=context,
                debate_id=debate_id,
                auto_decide=True,
            )
        except Exception as e:
            self._log(f"  ⚠ 토론 실패, 단순 답변으로 fallback: {e}")
            self.timeline.emit("lead", "error", error=f"debate: {e}", agent_id=agent_id)
            return self._llm_reply(agent_id, question, all_msgs)

        self.timeline.emit(
            "lead",
            "debate_decided",
            agent_id=agent_id,
            debate_id=debate_id,
            summary=outcome.decision[:200],
        )
        return (
            f"## Reply (4-way 토론 결정)\n"
            f"_토론 전문: `{outcome.md_path}` — 사후 검토 가능_\n\n"
            f"{outcome.decision}"
        )

    def _llm_reply(self, agent_id: str, question: Message, all_msgs: list[Message]) -> str:
        brief = (self.agents_root / agent_id / "brief.md").read_text(encoding="utf-8")
        recent = all_msgs[-6:]
        thread = "\n\n".join(f"### {m.from_}→{m.to} {m.kind} #{m.id}\n{m.body}" for m in recent)
        system, user = render_split(
            "reply",
            brief=brief[:2000],
            thread=thread,
            q_id=question.id,
            q_body=question.body,
        )
        try:
            # 멤버 다음 작업 방향 좌우 — high-stakes 가 아니어도 팀장 답변은 opus 로.
            return self.llm.call(system, user, tier="opus").strip()
        except Exception as e:
            self.timeline.emit("lead", "error", error=f"reply LLM: {e}", agent_id=agent_id)
            return f"## Reply\n(자동 답변 실패: {e}) — 너의 판단으로 진행."

    def _resume_member(self, agent_id: str) -> bool:
        rec = self.registry.get(agent_id)
        if rec is None:
            self._log(f"  ⊘ {agent_id} registry 미등록 → FAILED")
            self.registry.update(agent_id, status="FAILED", last_error="registry miss")
            return False
        if rec.last_resume >= RESUME_SAFETY_CAP:
            self._log(f"  ⊘ {agent_id} resume 한도 초과 → FAILED")
            self.registry.update(agent_id, status="FAILED", last_error="resume 한도 초과")
            self.timeline.emit("lead", "fire", agent_id=agent_id, reason="resume 한도 초과")
            return False

        next_n = rec.last_resume + 1
        self.registry.update(agent_id, last_resume=next_n, status="RUNNING")
        brief = self._briefs.get(agent_id) or self._reconstruct_brief(agent_id)
        if not brief:
            self.registry.update(agent_id, status="FAILED", last_error="brief 복원 실패")
            return False

        self._submit_spawn(brief, resume_count=next_n)
        return True

    def _reconstruct_brief(self, agent_id: str) -> HireBrief | None:
        """brief.md + system_prompt.md에서 HireBrief 복원."""
        agent_dir = self.agents_root / agent_id
        sp = agent_dir / "system_prompt.md"
        if not sp.exists():
            return None
        rec = self.registry.get(agent_id)
        return HireBrief(
            agent_id=agent_id,
            goal_id=rec.goal_id if rec else "",
            mission="(brief.md 참조)",
            deliverables=[],
            verification_checks=[],
            system_prompt=sp.read_text(encoding="utf-8"),
            model=(rec.model or None) if rec else None,
        )

    def _spawn_member(self, brief: HireBrief, resume_count: int) -> SpawnResult:
        """동기 spawn (worker thread 내부에서 호출됨)."""
        self._log(f"  ▶ spawn {brief.agent_id} (r={resume_count})")
        try:
            return self.spawner.spawn(brief, resume_count=resume_count)
        except Exception as e:
            self._log(f"  ⚠ spawn 예외 ({brief.agent_id}): {e}")
            self.timeline.emit("lead", "error", error=f"spawn: {e}", agent_id=brief.agent_id)
            return SpawnResult(
                agent_id=brief.agent_id, status="FAILED", raw_output="", error=str(e)
            )

    def _submit_spawn(self, brief: HireBrief, resume_count: int) -> None:
        """Executor에 spawn submit. 메인 스레드에서만 호출. 등록 + 상태 RUNNING."""
        if self._executor is None:
            # fallback: 동기 (executor 컨텍스트 밖, 예: 테스트에서)
            result = self._spawn_member(brief, resume_count)
            self._post_spawn(brief.agent_id, result, brief)
            return
        agent_id = brief.agent_id
        if agent_id in self._pending:
            return  # 이미 진행 중
        self.registry.set_status(agent_id, "RUNNING")
        self._briefs[agent_id] = brief
        future = self._executor.submit(self._spawn_member, brief, resume_count)
        self._pending[agent_id] = future

    def _collect_completed_spawns(self) -> bool:
        """완료된 spawn future를 수집해 _post_spawn 처리. 진행 있었으면 True."""
        if not self._pending:
            return False
        done_ids = [aid for aid, fut in self._pending.items() if fut.done()]
        if not done_ids:
            return False
        for aid in done_ids:
            fut = self._pending.pop(aid)
            brief = self._briefs.get(aid)
            try:
                result = fut.result()
            except Exception as e:
                self._log(f"  ⚠ spawn future 예외 ({aid}): {e}")
                self.timeline.emit("lead", "error", error=f"future: {e}", agent_id=aid)
                self.registry.update(aid, status="FAILED", last_error=str(e)[:300])
                continue
            if brief is None:
                self._log(f"  ⚠ spawn brief 누락 ({aid}) → _post_spawn skip")
                continue
            self._post_spawn(aid, result, brief)
        return True

    def _drain_pending(self) -> None:
        """모든 in-flight future를 끝까지 기다리고 _post_spawn 처리. 종료 경로용."""
        if not self._pending:
            return
        self._log(f"  ⏳ in-flight {len(self._pending)} drain")
        # 끝날 때까지 폴링
        while self._pending:
            self._collect_completed_spawns()
            if self._pending:
                time.sleep(0.5)

    def _post_spawn(self, agent_id: str, result: SpawnResult, brief: HireBrief) -> None:
        # 누적 비용 + 마지막 session_id 기록 (status 와 무관하게 매 spawn 마다)
        if result.cost_usd or result.session_id:
            rec = self.registry.get(agent_id)
            updates: dict[str, Any] = {}
            if result.cost_usd:
                updates["cost_usd"] = (rec.cost_usd if rec else 0.0) + result.cost_usd
            if result.session_id:
                updates["last_session_id"] = result.session_id
            if updates:
                self.registry.update(agent_id, **updates)

        if result.status == "DONE":
            self.registry.set_status(agent_id, "DONE")
        elif result.status == "WAITING":
            self.registry.set_status(agent_id, "WAITING")
        elif result.status == "FAILED":
            self.registry.update(agent_id, status="FAILED", last_error=result.error[:300])
            # 첫 실패면 goal 을 2개로 분할 (큰 goal 은 한 세션 max_turns 안에 못 끝나
            # FAILED 누적 + 비용만 증가). 분할되면 원본 goal 라인이 plan 에서 사라지므로
            # _unassign_from_plan 불필요. 분할 실패 / 이미 분할된 sub-goal 이면 기존 흐름.
            if not self._maybe_split_failed_goal(agent_id, result.error):
                self._unassign_from_plan(agent_id)
            self.timeline.emit(
                "lead",
                "fire",
                agent_id=agent_id,
                reason=f"멤버가 FAILED 보고: {result.error[:140]}",
            )
        else:
            # 알 수 없는 상태 — 토큰 미부착. 일단 WAITING으로 두고 다음 사이클에 봄
            self.registry.set_status(agent_id, "WAITING")
            self._log(f"  ⚠ {agent_id} 상태 토큰 없음 ({result.status}); WAITING로 보류")

    # ---------- 검증 + 머지 ----------

    def _verify_and_merge(self, agent_id: str) -> bool:
        rec = self.registry.get(agent_id)
        agent_dir = self.agents_root / agent_id
        ws = self.ws_root / agent_id

        # brief.md에서 verification_checks 복원 — md 파싱하지 않고 brief.md에 inline JSON 추가
        # 간단화: 검증 checks가 비어있으면 통과로 간주 (멤버 자체 보고만 신뢰)
        checks_inline = agent_dir / "checks.json"
        checks: list[Check] = []
        if checks_inline.exists():
            try:
                raw = json.loads(checks_inline.read_text())
                checks = [Check.from_dict(c) for c in raw]
            except (json.JSONDecodeError, OSError, KeyError, TypeError):
                pass

        if checks:
            verifier = Verifier(ws)
            try:
                report = verifier.run(checks)
            except Exception as e:
                self.registry.update(agent_id, status="FAILED", last_error=f"verify 예외: {e}")
                self.timeline.emit("lead", "verify_fail", agent_id=agent_id, detail=f"예외: {e}")
                return True
            if not report.passed:
                self.registry.update(
                    agent_id, status="FAILED", last_error=report.failure_summary()[:300]
                )
                self.timeline.emit(
                    "lead", "verify_fail", agent_id=agent_id, detail=report.failure_summary()
                )
                return True
            self.timeline.emit("lead", "verify_pass", agent_id=agent_id, checks=len(checks))
        else:
            self.timeline.emit("lead", "verify_pass", agent_id=agent_id, checks=0)

        # Evaluator (Anthropic critique-refine 패턴) — 1 cycle만, 무한 루프 방지.
        # P4 결정(2026-05-13): 전역 --enable-evaluator는 디버그용(모든 hire 강제),
        # 평상시엔 brief.verify=true인 멤버만 평가 (lead가 hire 시점에 판단).
        # budget 한도 후엔 skip (graceful drain — verify/merge는 진행되도록).
        brief_cached = self._briefs.get(agent_id)
        per_hire_verify = bool(brief_cached and brief_cached.verify)
        evaluator_ok = (
            (self.enable_evaluator or per_hire_verify)
            and self.budget.can_continue()
            and not (agent_dir / ".evaluated").exists()
        )
        if evaluator_ok:
            critique = self._run_evaluator(agent_id, agent_dir, ws)
            (agent_dir / ".evaluated").write_text("1")
            if critique:
                # FAIL → 멤버 재spawn (critique을 instruction으로 전달)
                self._log(f"  🔍 Evaluator FAIL → {agent_id} 재spawn (1 cycle)")
                append_message(
                    agent_dir / "mailbox.md",
                    from_="lead",
                    to=agent_id,
                    kind="instruction",
                    body=(
                        f"# Evaluator critique\n{critique}\n\n"
                        "위 문제를 해결하고 다시 [STATUS:DONE]을 보고하라."
                    ),
                )
                self.timeline.emit(
                    "lead", "verify_fail", agent_id=agent_id, detail=f"evaluator: {critique[:140]}"
                )
                self.registry.update(agent_id, status="RUNNING")
                brief = self._briefs.get(agent_id) or self._reconstruct_brief(agent_id)
                if brief:
                    rec = self.registry.get(agent_id)
                    next_n = (rec.last_resume if rec else 0) + 1
                    self.registry.update(agent_id, last_resume=next_n)
                    self._submit_spawn(brief, resume_count=next_n)
                return True

        # merge
        merge_report = self.merger.merge(ws, agent_id)
        self.timeline.emit(
            "lead",
            "merge",
            agent_id=agent_id,
            copied=len(merge_report.copied),
            conflicts=len(merge_report.conflicts),
        )

        # 충돌 발생 시 먼저 시드 유사도 게이트로 폐기/재지시 판정 → 살아남은 충돌만 토론.
        if merge_report.conflicts:
            surviving = self._seed_similarity_gate(agent_id, merge_report.conflicts)
            if surviving:
                self._resolve_conflicts_via_debate(agent_id, surviving)

        # plan.md에서 해당 goal 체크
        goals = parse_plan(self.plan_md)
        for g in goals:
            if g.assigned == agent_id and not g.done:
                g.done = True
        render_plan(self.plan_md, "Plan", goals)

        # 머지 완료 마커
        (agent_dir / ".merged").write_text(merge_report.summary())
        return True

    def _already_merged(self, agent_id: str) -> bool:
        return (self.agents_root / agent_id / ".merged").exists()

    # ---------- 재시작 복구 + graceful shutdown (G-011) ----------

    def restore_state(self) -> None:
        """lead 재시작 시 디스크 상태로 in-flight 멤버/충돌 큐 복원.

        - 각 멤버: mailbox 의 마지막 멤버 메시지로 상태 분류 (DONE / WAITING /
          RUNNING / FAILED / UNKNOWN). PID 파일이 살아있으면 `_reattached` 에
          등록 (이후 `_recover_zombies` 가 건너뜀).
        - state/lead/conflicts/*.md 글롭하여 `conflict_queue` 재로드.
        """
        # 1) 충돌 큐 재로드
        self.conflict_queue = []
        conflicts_dir = self.lead_state_dir / "conflicts"
        if conflicts_dir.exists():
            for md in sorted(conflicts_dir.glob("*.md")):
                if md.name.endswith(".archive.md"):
                    continue
                item = parse_conflict_file(md)
                if item is not None:
                    self.conflict_queue.append(item)

        # 2) 멤버 상태 복원
        if not self.agents_root.exists():
            return
        for agent_dir in sorted(self.agents_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            agent_id = agent_dir.name
            if not _AGENT_ID_RE.match(agent_id):
                continue

            mailbox = agent_dir / "mailbox.md"
            last_msg = mailbox_last_member_message(mailbox, agent_id)
            state = classify_mailbox_state(last_msg)

            if state == "DONE":
                self.registry.update(agent_id, status="DONE")
                continue
            if state == "FAILED":
                self.registry.update(
                    agent_id, status="FAILED", last_error="restore: 마지막 메시지 FAILED"
                )
                continue
            if state == "WAITING":
                self.registry.update(agent_id, status="WAITING")
                continue
            if state == "UNKNOWN":
                continue

            # RUNNING: PID 살아있으면 reattach, 아니면 zombie recovery 에 위임
            pid = read_pid_file(agent_id, self.agents_root)
            if pid is not None and is_pid_alive(pid):
                self.registry.update(agent_id, status="RUNNING")
                self._reattached.add(agent_id)
            else:
                self.registry.update(agent_id, status="RUNNING")

    def graceful_shutdown(self, timeout: float) -> None:
        """SIGTERM/SIGINT 시 in-flight spawn future 들을 grace 동안 drain.

        호출 시점에 `_shutdown_requested` set → spawn future 결과를 회수 (자식 자체는
        kill 안 함, lead 종료 시 자연히 종료). timeout 내 미회수 항목은 남겨두고
        timeline 에 `shutdown_timeout` 이벤트 emit 후 반환.
        """
        self._shutdown_requested = True
        if not self._pending:
            return

        t0 = time.monotonic()
        while self._pending and (time.monotonic() - t0) < timeout:
            self._collect_completed_spawns()
            if not self._pending:
                break
            time.sleep(0.05)

        if self._pending:
            self.timeline.emit(
                "lead",
                "shutdown_timeout",
                pending=list(self._pending.keys()),
                elapsed=round(time.monotonic() - t0, 3),
            )

    # ---------- 시드 유사도 게이트 ----------

    def _seed_similarity_gate(self, agent_id: str, conflicts: list[str]) -> list[str] | None:
        """충돌 토론 비용 쓰기 전에 시드 의도에서 너무 멀어진 산출물을 폐기 + 재지시.

        반환:
          - list[str]: 통과/SKIP/BYPASS — 호출자가 그대로 debate 로 회부.
          - None: REFINE — 멤버 전체 폐기 + 재spawn 트리거됨. debate 회부 금지.
        """
        if not conflicts:
            return []

        brief = self._briefs.get(agent_id) or self._reconstruct_brief(agent_id)
        brief_kind = getattr(brief, "kind", "") if brief is not None else ""

        mailbox = self.agents_root / agent_id / "mailbox.md"
        prior_msgs = parse_messages(mailbox)
        refine_count = sum(1 for m in prior_msgs if m.kind == "refine" and m.from_ == "lead")

        decision = decide_gate(
            list(conflicts),
            ws_main=self.ws_main,
            agent_id=agent_id,
            brief_kind=brief_kind,
            refine_count=refine_count,
            max_respawns=SEED_GATE_MAX_RESPAWNS,
        )

        if decision.action in (GATE_ACTION_PASS, GATE_ACTION_SKIP):
            return list(decision.surviving_conflicts)

        if decision.action == GATE_ACTION_BYPASS:
            self.timeline.emit(
                "lead",
                "seed_gate_bypass",
                agent_id=agent_id,
                conflicts=len(conflicts),
                refine_count=refine_count,
            )
            return list(decision.surviving_conflicts)

        if decision.action != GATE_ACTION_REFINE:
            return list(decision.surviving_conflicts)

        # REFINE: 멤버 전체 폐기 + refine 재지시 + 재spawn
        worst = decision.worst_outcome
        if worst is None:
            return list(conflicts)

        for outcome in decision.all_outcomes:
            if outcome.stash_rel:
                (self.ws_main / outcome.stash_rel).unlink(missing_ok=True)

        extras = [o.rel for o in decision.failed_outcomes if o.rel != worst.rel]
        body = build_refine_message(
            seed_path=worst.rel,
            member_path=worst.stash_rel or f"{worst.rel}.from-{agent_id}",
            similarity=worst.similarity,
            diff_summary=worst.diff_summary,
            extra_files=extras,
        )
        append_message(
            mailbox,
            from_="lead",
            to=agent_id,
            kind="refine",
            body=body,
        )

        self.timeline.emit(
            "lead",
            "seed_gate_refine",
            agent_id=agent_id,
            worst=worst.rel,
            similarity=round(worst.similarity, 3),
            failed_count=len(decision.failed_outcomes),
        )

        if brief is not None:
            rec = self.registry.get(agent_id)
            next_n = (rec.last_resume if rec else 0) + 1
            self.registry.update(agent_id, last_resume=next_n)
            self._submit_spawn(brief, resume_count=next_n)

        return None

    # ---------- 충돌 자동 토론 + 통합 ----------

    def _resolve_conflicts_via_debate(self, new_agent_id: str, conflicts: list[str]) -> None:
        """충돌 파일들을 (1) 결정론적 auto_merge → (2) 병렬 LLM 토론으로 해소.

        병렬화: asyncio.Semaphore(DEBATE_MAX_PARALLEL) + gather — 파일당 4-5분 직렬화 제거.
        2단계 escalate: 1차 sonnet 토론에서 합의(consensus_reached) 못 하면 동일 충돌을
        opus 모델로 재토론. auto_merge 가 모두 해소했으면 토론은 한 번도 일어나지 않는다.
        """
        valid: list[tuple[str, Path, Path]] = []
        for rel in conflicts:
            if "symlink rejected" in rel:
                continue
            rel_clean = rel.split(" ", 1)[0]
            main_path = self.ws_main / rel_clean
            stash_path = main_path.with_name(f"{main_path.name}.from-{new_agent_id}")
            if main_path.exists() and stash_path.exists():
                valid.append((rel_clean, main_path, stash_path))

        if not valid:
            return

        # 1단계: 결정론적 auto_merge (시드 base 가 있으면 3-way) — 안전 전략 매칭되면 LLM 없이 해소.
        remaining = self._apply_auto_merge_pass(new_agent_id, valid)
        if not remaining:
            self._log(f"  ✓ auto_merge 가 모든 충돌 해소 ({len(valid)}개) — 토론 0회")
            return

        self._log(
            f"  🤝 병렬 토론 ({len(remaining)}/{len(valid)} 파일 잔여, "
            f"semaphore={DEBATE_MAX_PARALLEL}) ↔ from-{new_agent_id}"
        )
        asyncio.run(self._debate_remaining_async(new_agent_id, remaining))

    def _apply_auto_merge_pass(
        self,
        new_agent_id: str,
        valid: list[tuple[str, Path, Path]],
    ) -> list[tuple[str, Path, Path]]:
        """결정론적 3-way merge 시도.

        성공한 파일은 main 덮어쓰고 stash 제거 + remaining 에서 제외.
        """
        seed_root = self.ws_root / new_agent_id / ".seed"
        remaining: list[tuple[str, Path, Path]] = []
        for rel_clean, main_path, stash_path in valid:
            seed_path = seed_root / rel_clean
            if not seed_path.is_file():
                remaining.append((rel_clean, main_path, stash_path))
                continue
            try:
                base = seed_path.read_text(encoding="utf-8", errors="ignore")
                main_v = main_path.read_text(encoding="utf-8", errors="ignore")
                stash_v = stash_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                remaining.append((rel_clean, main_path, stash_path))
                continue
            result = try_auto_merge(base, main_v, stash_v, path=rel_clean)
            if result.merged is None:
                remaining.append((rel_clean, main_path, stash_path))
                continue
            try:
                main_path.write_text(result.merged, encoding="utf-8")
                stash_path.unlink(missing_ok=True)
            except OSError as e:
                self._log(f"  ⚠ auto_merge 쓰기 실패 {rel_clean}: {e}")
                remaining.append((rel_clean, main_path, stash_path))
                continue
            self._log(f"  ✓ auto_merge[{result.strategy}] {rel_clean}")
            self.timeline.emit(
                "lead",
                "conflict_auto_merged",
                agent_id=new_agent_id,
                file=rel_clean,
                strategy=result.strategy,
            )
        return remaining

    async def _debate_remaining_async(
        self,
        new_agent_id: str,
        remaining: list[tuple[str, Path, Path]],
    ) -> None:
        """잔여 충돌 N개를 병렬 토론 — Semaphore(DEBATE_MAX_PARALLEL) 한도 내에서 동시 실행."""
        sem = asyncio.Semaphore(DEBATE_MAX_PARALLEL)
        tasks = [
            self._debate_one_conflict_async(sem, new_agent_id, rel, main_p, stash_p)
            for rel, main_p, stash_p in remaining
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _debate_one_conflict_async(
        self,
        sem: asyncio.Semaphore,
        new_agent_id: str,
        rel_clean: str,
        main_path: Path,
        stash_path: Path,
    ) -> None:
        """단일 충돌 파일을 sonnet → (합의 실패 시) opus 로 escalate 토론, 통합본을 main 에 적용."""
        async with sem:
            # 1차: sonnet
            outcome = await asyncio.to_thread(
                self._run_conflict_debate,
                new_agent_id,
                rel_clean,
                main_path,
                stash_path,
                MODEL_SONNET,
                DEBATE_ROUND_TIMEOUT_SEC,
            )
            if outcome is not None and outcome.get("consensus") and outcome.get("merged"):
                self._apply_merged(
                    new_agent_id, rel_clean, main_path, stash_path, outcome, "sonnet"
                )
                return

            # 2차: opus escalate
            self._log(f"  ↑ escalate opus {rel_clean}")
            self.timeline.emit(
                "lead",
                "debate_escalate",
                agent_id=new_agent_id,
                file=rel_clean,
                from_model="sonnet",
                to_model="opus",
            )
            outcome2 = await asyncio.to_thread(
                self._run_conflict_debate,
                new_agent_id,
                rel_clean,
                main_path,
                stash_path,
                MODEL_OPUS,
                DEBATE_ESCALATE_TIMEOUT_SEC,
            )
            if outcome2 is not None and outcome2.get("merged"):
                self._apply_merged(new_agent_id, rel_clean, main_path, stash_path, outcome2, "opus")
                return

            self._log(f"  ⚠ 토론 escalate 후에도 통합 실패 — 보존: {rel_clean}")

    def _run_conflict_debate(
        self,
        new_agent_id: str,
        rel_clean: str,
        main_path: Path,
        stash_path: Path,
        model: str,
        _timeout_sec: int,
    ) -> dict[str, Any] | None:
        """단일 파일 충돌 토론을 동기로 실행. asyncio.to_thread 로 호출됨.

        반환: {'consensus': bool, 'merged': str | None, 'decision': str} 또는 None (예외).
        """
        from agents.debate import DebatePanel
        from agents.debate.panel import PERSONAS_FAST

        new_agent_dir = self.agents_root / new_agent_id
        try:
            new_brief = (new_agent_dir / "brief.md").read_text(encoding="utf-8")[:1500]
            new_delivery = (new_agent_dir / "delivery.md").read_text(encoding="utf-8")[:1000]
        except OSError:
            new_brief = new_delivery = ""

        try:
            main_v = main_path.read_text(encoding="utf-8", errors="ignore")[:6000]
            stash_v = stash_path.read_text(encoding="utf-8", errors="ignore")[:6000]
        except OSError as e:
            self._log(f"  ⚠ 충돌 파일 읽기 실패 {rel_clean}: {e}")
            return None

        question = (
            f"파일 `{rel_clean}` 충돌. (a) main 유지 / (b) {new_agent_id} 버전 채택 / "
            f"(c) 통합 — 셋 중 하나 결정."
        )
        context = (
            f"# {new_agent_id} brief\n{new_brief}\n\n"
            f"# {new_agent_id} delivery\n{new_delivery}\n\n"
            f"# Main 버전\n```\n{main_v}\n```\n\n"
            f"# {new_agent_id} 버전\n```\n{stash_v}\n```\n"
        )
        debate_id = (
            f"conflict-{new_agent_id}-{rel_clean.replace('/', '_')}-{model}-{int(time.time())}"
        )
        panel = DebatePanel(
            self.lead_state_dir / "debates",
            self.llm,
            max_rounds=2,
            personas=PERSONAS_FAST,
            model=model,
        )
        self.timeline.emit(
            "lead",
            "debate_start",
            agent_id=new_agent_id,
            file=rel_clean,
            model=model,
            debate_id=debate_id,
        )
        try:
            outcome = panel.deliberate(
                question=question,
                context=context,
                debate_id=debate_id,
                auto_decide=True,
                integrate_content=True,
                model=model,
            )
        except (RuntimeError, OSError, ValueError) as e:
            self.timeline.emit(
                "lead",
                "error",
                error=f"conflict debate ({model}): {e}",
                agent_id=new_agent_id,
                file=rel_clean,
            )
            return None

        if outcome.consensus_reached:
            self.timeline.emit(
                "lead",
                "debate_consensus",
                agent_id=new_agent_id,
                file=rel_clean,
                model=model,
            )

        merged = outcome.integrated_content
        if merged is None:
            # 통합 추출 실패 시 기존 추출기로 fallback (panel decision 으로부터 한 번 더 시도)
            merged = self._extract_merged_file(rel_clean, main_path, stash_path, outcome.decision)

        return {
            "consensus": outcome.consensus_reached,
            "merged": merged,
            "decision": outcome.decision,
        }

    def _apply_merged(
        self,
        new_agent_id: str,
        rel_clean: str,
        main_path: Path,
        stash_path: Path,
        outcome: dict[str, Any],
        model: str,
    ) -> None:
        """토론 결과 통합본을 main 에 기록하고 stash 정리."""
        merged = outcome.get("merged")
        if not isinstance(merged, str):
            self._log(f"  ⚠ 통합본 없음 → 보존: {rel_clean}")
            return
        try:
            main_path.write_text(merged, encoding="utf-8")
            stash_path.unlink(missing_ok=True)
        except OSError as e:
            self._log(f"  ⚠ 통합본 쓰기 실패 {rel_clean}: {e}")
            return
        self._log(f"  ✓ 통합 완료[{model}] {rel_clean}")
        self.timeline.emit(
            "lead",
            "conflict_debated",
            agent_id=new_agent_id,
            file=rel_clean,
            files=[rel_clean],
            file_count=1,
            model=model,
            consensus=bool(outcome.get("consensus")),
        )

    def _extract_merged_file(
        self,
        file_rel: str,
        main_path: Path,
        stash_path: Path,
        full_decision: str,
    ) -> str | None:
        """전체 결정문에서 해당 파일에 대한 결정만 적용해 통합본 작성. opus 1회."""
        try:
            main_v = main_path.read_text(encoding="utf-8", errors="ignore")
            stash_v = stash_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            self._log(f"  ⚠ 충돌 파일 읽기 실패 {file_rel}: {e}")
            return None

        system = (
            "너는 코드 통합 작성자. 토론 결정문은 여러 파일의 결정을 담고 있다. "
            "지정된 파일에 대한 결정만 찾아 적용해 최종 파일을 출력. "
            "출력은 정확히 ```...``` 코드 펜스 하나만; 펜스 밖 텍스트 금지. "
            "결정이 '한 쪽 채택' 이면 그 쪽 전체를 그대로 출력."
        )
        user = (
            f"# 전체 토론 결정 (여러 파일 포함)\n{full_decision}\n\n"
            f"# 대상 파일\n`{file_rel}`\n\n"
            f"# Main 버전\n```\n{main_v}\n```\n\n"
            f"# 충돌 버전\n```\n{stash_v}\n```\n\n"
            f"# 출력: `{file_rel}` 의 최종 통합 파일을 ```...``` 코드 펜스 하나로만 감싸서."
        )
        try:
            raw = self.llm.call(system, user, tier="opus")
        except Exception as e:
            self.timeline.emit("lead", "error", error=f"merge extract: {e}", file=file_rel)
            return None

        m = re.search(r"```(?:[a-zA-Z0-9_+\-.]*)\s*\n(.*?)\n```", raw, re.DOTALL)
        return m.group(1) if m else None

    def _has_unverified_done(self) -> bool:
        """budget 한도 후에도 verify+merge 처리해야 할 멤버 있나? graceful drain용."""
        for rec in self.registry.by_status("DONE"):
            if not self._already_merged(rec.agent_id):
                return True
        return False

    # ---------- code janitor 자율 호출 ----------

    def _should_run_code_janitor(self) -> bool:
        """lead가 ws/main 상태 보고 청소 필요 판단."""
        if not self.ws_main.exists():
            return False
        py_files = list(self.ws_main.rglob("*.py"))
        if len(py_files) < 5:
            return False  # 너무 작아서 청소 의미 없음
        # 가벼운 LLM 판단 (haiku) — ws/main 파일 목록 보고 결정
        sample = "\n".join(f"- {p.relative_to(self.ws_main)}" for p in py_files[:30])
        system = (
            "너는 팀장의 정리 보조다. 워크스페이스 .py 파일 목록을 보고 "
            "지금 미사용 코드 정리(code-janitor)를 돌릴 시점인지 yes/no 판정. "
            "JSON 한 줄만 출력."
        )
        user = (
            f"# ws/main의 .py 파일 ({len(py_files)}개)\n{sample}\n\n"
            "# 출력 (정확히 JSON)\n"
            '{"run": true|false, "reason": "한 줄"}\n\n'
            "run=true 기준: 파일 ≥10, 명백히 미완성/temp/old 이름 다수, 진입점 외 잡파일. "
            "run=false: 깔끔, 변동 중, 또는 판단 어려움."
        )
        try:
            from core.llm import parse_json_loose

            data = parse_json_loose(self.llm.call(system, user, tier="haiku"))
            decision = bool(data.get("run"))
            self._log(f"  🧹 code-janitor 판단: run={decision} ({data.get('reason', '?')[:60]})")
            return decision
        except Exception as e:
            self._log(f"  ⚠ janitor 판단 실패, skip: {e}")
            return False

    def _run_code_janitor(self) -> None:
        from agents.janitor import CodeJanitor

        try:
            janitor = CodeJanitor(
                self.ws_main,
                entrypoints=[],  # ws/main 산출물은 진입점 정의 없으니 빈 리스트
                dry_run=False,
            )
            report = janitor.run()
            self._log(f"  🧹 {report.summary()}")
            self.timeline.emit(
                "lead",
                "code_janitor",
                archived=len(report.archived),
                kept=len(report.kept),
                archive_dir=str(report.archive_dir) if report.archive_dir else "",
            )
        except Exception as e:
            self._log(f"  ⚠ code-janitor 실패: {e}")
            self.timeline.emit("lead", "error", error=f"code-janitor: {e}")

    def _run_evaluator(self, agent_id: str, agent_dir: Path, ws: Path) -> str | None:
        """AdversarialVerifier 1회 호출. FAIL이면 critique 문자열 반환, 아니면 None."""
        try:
            from agents.audit import AdversarialVerifier
        except ImportError as e:
            self._log(f"  ⚠ Evaluator import 실패: {e}")
            return None
        delivery = agent_dir / "delivery.md"
        artifacts = delivery.read_text(encoding="utf-8") if delivery.exists() else ""
        # ws 산출물 요약 (파일 목록 + 크기)
        ws_summary = []
        for p in sorted(ws.rglob("*")):
            if p.is_file():
                try:
                    ws_summary.append(f"- {p.relative_to(ws)} ({p.stat().st_size}B)")
                except OSError:
                    pass
                if len(ws_summary) >= 30:
                    break
        artifacts_summary = artifacts + "\n\n## Files\n" + "\n".join(ws_summary)
        av = AdversarialVerifier(self.lead_state_dir, self.llm)
        try:
            report = av.review(
                task_id=agent_id,
                task_title=f"agent {agent_id}",
                spec_excerpt=self.spec[:2000],
                artifacts_summary=artifacts_summary[:3000],
                verifier_log="",
            )
        except Exception as e:
            self._log(f"  ⚠ Evaluator 호출 실패 (무시): {e}")
            return None
        if not report.has_fail():
            return None
        return "\n".join(
            f"[{j.persona}] {j.evidence}" for j in report.judgements if j.verdict == "FAIL"
        )

    # ---------- 유틸 ----------

    def _registry_summary(self) -> str:
        recs = self.registry.all()
        counts: dict[str, int] = {}
        for r in recs:
            counts[r.status] = counts.get(r.status, 0) + 1
        return json.dumps(counts)

    def _log(self, msg: str) -> None:
        print(msg, flush=True)
        log_path = self.lead_state_dir / "lead.log"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
