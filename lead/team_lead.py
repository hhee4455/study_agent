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

import json
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.budget import BudgetExceeded, BudgetManager
from core.health import HealthMonitor
from core.llm import LLMClient
from core.rate_limit import RateLimitExhausted
from core.verifier import Check, Verifier

from lead.mailbox import Message, append_message, parse_messages, scan_new
from lead.member import HireBrief, MemberSpawner, SpawnResult
from lead.prompts import render_split
from lead.registry import AgentRegistry
from lead.timeline import TimelineRenderer
from lead.workspace import WorkspaceMerger


# Plan goal 라인 형식
GOAL_LINE_RE = re.compile(r"^- \[(?P<done>[ xX])\] (?P<id>G-[A-Za-z0-9_-]+): (?P<title>.+?)(?:\s+\(assigned: (?P<assigned>[A-Za-z0-9_-]+)\))?\s*$")

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


@dataclass
class Goal:
    id: str
    title: str
    done: bool = False
    assigned: str = ""


def parse_plan(plan_md: Path) -> list[Goal]:
    """plan.md를 파싱해 Goal 리스트 반환."""
    if not plan_md.exists():
        return []
    goals = []
    for line in plan_md.read_text(encoding="utf-8").splitlines():
        m = GOAL_LINE_RE.match(line.strip())
        if not m:
            continue
        goals.append(Goal(
            id=m.group("id"),
            title=m.group("title").strip(),
            done=m.group("done").lower() == "x",
            assigned=m.group("assigned") or "",
        ))
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
        state_dir: Path,              # <state_dir>/ — session_logs/ 위치 결정
        lead_state_dir: Path,         # <state_dir>/lead/
        agents_root: Path,            # <state_dir>/agents/
        session_logs_root: Path,      # <state_dir>/session_logs/
        ws_root: Path,                # ws/members/ — 멤버 격리 ws 들의 부모
        ws_main: Path,                # ws/main/ (= args.workspace) — 머지 결과
        llm: LLMClient,
        budget: BudgetManager,
        health: Optional[HealthMonitor] = None,
        default_model: str = "opus",
        enable_evaluator: bool = False,
        max_parallel: int = 3,
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
        self._executor: Optional[ThreadPoolExecutor] = None
        self._pending: dict[str, Future] = {}
        # 멤버 브리프 캐시 (resume 시 재구성용; system_prompt.md만으론 부족한 미션 등 보존)
        self._briefs: dict[str, HireBrief] = {}
        # janitor 판단 카운터 (hire 횟수 누적)
        self._hires_since_janitor = 0
        # 최종 점검 (pytest) 반복 카운터 — 한도 도달하면 사람 결정 코드 (5) 반환
        self._final_verification_count = 0

    # ---------- 진입점 ----------

    def run(self) -> int:
        """tick 루프. 종료 조건: 모든 goal done | budget 초과 | 진행 불가."""
        self._log("=" * 60)
        self._log(f"팀장 시작 | spec={self.spec_name} | max_parallel={self.max_parallel}")
        self._log("=" * 60)

        # 이전 run 이 죽어 zombie 가 된 RUNNING 멤버 복구 (delivery 있으면 DONE 으로, 없으면 FAILED + plan 에서 unassign)
        self._recover_zombies()

        if not self.plan_md.exists():
            self._initial_plan()

        consecutive_no_progress = 0
        with ThreadPoolExecutor(max_workers=self.max_parallel,
                                 thread_name_prefix="spawn") as ex:
            self._executor = ex
            try:
                while (
                    self.budget.can_continue()
                    or self._pending
                    or self._has_unverified_done()
                ):
                    try:
                        progressed = self._tick()
                    except BudgetExceeded:
                        # in-flight 다 끝나길 기다림
                        self._drain_pending()
                        raise
                    except RateLimitExhausted as e:
                        self._log(f"🚫 rate limit 한도: {e}")
                        self._drain_pending()
                        return 4

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
                                f"\n⚠️ 최종 점검 {self._final_verification_count}회 실패 — 사람 결정 필요"
                            )
                            self.timeline.emit(
                                "lead", "final_verification_exhausted",
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
                            "lead", "final_verification_fail",
                            iterations=self._final_verification_count, added_goals=added,
                        )
                        # 진행률 리셋 — 새 goal 들이 hire 될 시간 줌
                        consecutive_no_progress = 0
                        continue  # 다음 tick — 새 goal hire 진행

                    if not progressed:
                        consecutive_no_progress += 1
                        if consecutive_no_progress >= NO_PROGRESS_CAP and not self._pending:
                            summary = self._registry_summary()
                            self._log(f"⏸️  {NO_PROGRESS_CAP}회 연속 무진행. registry={summary}")
                            self.timeline.emit("lead", "error",
                                               error=f"no progress (registry={summary})")
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
        new_msgs = scan_new(self.agents_root, self.registry.last_seen_map())
        for m in new_msgs:
            self._log(f"  📨 {m.from_}→{m.to} {m.kind} #{m.id}")
            agent_id = m.from_ if m.to == "lead" else m.to
            rec = self.registry.get(agent_id)
            if rec:
                self.registry.update(agent_id, last_msg_id=m.id)
                progressed = True

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
        self._log(f"🔍 팀장 최종 점검 — pytest 전체 실행 (반복 #{self._final_verification_count + 1})")
        self._log("=" * 60)

        venv_py = self.ws_main / ".venv" / "bin" / "python"
        py_cmd = [str(venv_py)] if venv_py.exists() else ["python3"]
        cmd = py_cmd + ["-m", "pytest", "--tb=short", "-q", "--no-header"]

        try:
            result = subprocess.run(
                cmd, cwd=self.ws_main,
                capture_output=True, text=True,
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
            self._log(f"  ⚠ LLM 응답에 JSON 배열 없음")
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
            self.timeline.emit("lead", "plan_update",
                               note=f"final verification 실패 → fix goal {added}개 추가")
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
                self.timeline.emit("lead", "zombie_recovered",
                                   agent_id=rec.agent_id, to="DONE", delivery_bytes=size)
            else:
                self.registry.update(rec.agent_id, status="FAILED",
                                     last_error="이전 run zombie (delivery 비어있음)")
                self._unassign_from_plan(rec.agent_id)
                self._log(f"  🔄 좀비 복구: {rec.agent_id} RUNNING → FAILED + plan unassign")
                self.timeline.emit("lead", "zombie_recovered",
                                   agent_id=rec.agent_id, to="FAILED", delivery_bytes=size)

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

    def _ws_main_summary(self, max_files: int = 30) -> str:
        """hire_brief 컨텍스트로 ws/main 현 .py 파일 목록 (상위 N개). 새 멤버가 기존 import 경로 일관성 유지하도록."""
        if not self.ws_main.exists():
            return "(비어있음)"
        files: list[str] = []
        for p in sorted(self.ws_main.rglob("*.py")):
            parts = p.relative_to(self.ws_main).parts
            if "__pycache__" in parts or ".venv" in parts or ".archive" in parts:
                continue
            files.append(str(p.relative_to(self.ws_main)))
            if len(files) >= max_files:
                break
        if not files:
            return "(.py 파일 없음)"
        return "\n".join(f"- {f}" for f in files)

    # ---------- LLM 결정 ----------

    def _initial_plan(self) -> None:
        """spec → plan.md 초기 분해 (한 번만)."""
        self._log("🧭 plan.md 초기 분해")
        system, user = render_split(
            "plan_initial", spec_name=self.spec_name, spec=self.spec[:6000]
        )
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

        # 코드 펜스 안의 내용만 추출
        m = re.search(r"```[a-zA-Z]*\s*\n(.*?)\n```", raw, re.DOTALL)
        body = (m.group(1) if m else raw).strip()
        if not body.lower().startswith("# plan"):
            body = "# Plan\n" + body
        self.plan_md.write_text(body + "\n", encoding="utf-8")
        self.timeline.emit("lead", "plan_update", note="초기 plan 작성됨")

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
            verification_checks=brief_data.get("verification_checks", []),
            system_prompt=brief_data["system_prompt"],
            allowed_tools=brief_data.get("allowed_tools"),
            seed_files=brief_data.get("seed_files"),
            verify=bool(brief_data.get("verify", False)),
        )
        self.spawner.write_brief(brief)
        self.registry.register(agent_id, goal_id=target.id)

        # plan.md 갱신 (assigned 마킹)
        target.assigned = agent_id
        render_plan(self.plan_md, "Plan", goals)
        self.timeline.emit("lead", "hire", agent_id=agent_id, goal=f"{target.id}: {target.title}")

        # 첫 instruction
        append_message(
            self.agents_root / agent_id / "mailbox.md",
            from_="lead", to=agent_id, kind="instruction",
            body=f"# 첫 지시\n{brief.mission}\n\n작업 시작.",
        )

        self._submit_spawn(brief, resume_count=0)
        return True

    def _llm_hire_brief(self, goal: Goal) -> Optional[dict]:
        """LLM에게 채용 brief(JSON) 작성 시키기."""
        system, user = render_split(
            "hire_brief",
            spec=self.spec[:3000],
            goal_id=goal.id,
            goal_title=goal.title,
            ws_main_tree=self._ws_main_summary(),
        )
        try:
            from core.llm import parse_json_loose
            # 멤버 mission/검증 정의 — 너무 좁으면 부족, 너무 넓으면 멤버 헤맴. opus.
            raw = self.llm.call(system, user, tier="opus")
            data = parse_json_loose(raw)
            if not data or "mission" not in data:
                return None
            data.setdefault("deliverables", [])
            data.setdefault("verification_checks", [])
            data.setdefault("system_prompt", "너는 능력 있는 엔지니어. 미션 완수 후 검증 기준 통과.")
            return data
        except Exception as e:
            self._log(f"  ⚠️ hire-brief LLM 실패: {e}")
            self.timeline.emit("lead", "error", error=f"hire-brief: {e}", goal=goal.id)
            return None

    def _handle_waiting(self, agent_id: str) -> bool:
        rec = self.registry.get(agent_id)
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
        append_message(mbox, from_="lead", to=agent_id, kind="reply",
                       body=reply_body, ref=last_q.id)
        self.timeline.emit("lead", "reply", to=agent_id, ref=last_q.id,
                           summary=reply_body[:200])
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
        thread = "\n\n".join(
            f"### {m.from_}→{m.to} {m.kind} #{m.id}\n{m.body}" for m in recent
        )
        context = (
            f"# 멤버 brief\n{brief[:1500]}\n\n"
            f"# 최근 mailbox 스레드\n{thread[:2000]}\n\n"
            f"# spec 발췌\n{self.spec[:1500]}"
        )
        panel = DebatePanel(self.lead_state_dir / "debates", self.llm, max_rounds=2)
        debate_id = f"{agent_id}-q{question.id}-{int(time.time())}"
        try:
            outcome = panel.deliberate(
                question=question.body, context=context,
                debate_id=debate_id, auto_decide=True,
            )
        except Exception as e:
            self._log(f"  ⚠ 토론 실패, 단순 답변으로 fallback: {e}")
            self.timeline.emit("lead", "error", error=f"debate: {e}",
                               agent_id=agent_id)
            return self._llm_reply(agent_id, question, all_msgs)

        self.timeline.emit(
            "lead", "debate_decided", agent_id=agent_id,
            debate_id=debate_id, summary=outcome.decision[:200],
        )
        return (
            f"## Reply (4-way 토론 결정)\n"
            f"_토론 전문: `{outcome.md_path}` — 사후 검토 가능_\n\n"
            f"{outcome.decision}"
        )

    def _llm_reply(self, agent_id: str, question: Message, all_msgs: list[Message]) -> str:
        brief = (self.agents_root / agent_id / "brief.md").read_text(encoding="utf-8")
        recent = all_msgs[-6:]
        thread = "\n\n".join(
            f"### {m.from_}→{m.to} {m.kind} #{m.id}\n{m.body}" for m in recent
        )
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
        if rec.last_resume >= RESUME_SAFETY_CAP:
            self._log(f"  ⊘ {agent_id} resume 한도 초과 → FAILED")
            self.registry.update(agent_id, status="FAILED",
                                 last_error="resume 한도 초과")
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

    def _reconstruct_brief(self, agent_id: str) -> Optional[HireBrief]:
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
        )

    def _spawn_member(self, brief: HireBrief, resume_count: int) -> SpawnResult:
        """동기 spawn (worker thread 내부에서 호출됨)."""
        self._log(f"  ▶ spawn {brief.agent_id} (r={resume_count})")
        try:
            return self.spawner.spawn(brief, resume_count=resume_count)
        except Exception as e:
            self._log(f"  ⚠ spawn 예외 ({brief.agent_id}): {e}")
            self.timeline.emit("lead", "error", error=f"spawn: {e}", agent_id=brief.agent_id)
            return SpawnResult(agent_id=brief.agent_id, status="FAILED",
                               raw_output="", error=str(e))

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
            updates: dict = {}
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
            self.registry.update(agent_id, status="FAILED",
                                 last_error=result.error[:300])
            self._unassign_from_plan(agent_id)  # FAILED 즉시 plan 동기화 — 다음 tick 에 재hire 가능
            self.timeline.emit("lead", "fire", agent_id=agent_id,
                               reason=f"멤버가 FAILED 보고: {result.error[:140]}")
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
            except Exception:
                pass

        if checks:
            verifier = Verifier(ws)
            try:
                report = verifier.run(checks)
            except Exception as e:
                self.registry.update(agent_id, status="FAILED",
                                     last_error=f"verify 예외: {e}")
                self.timeline.emit("lead", "verify_fail", agent_id=agent_id,
                                   detail=f"예외: {e}")
                return True
            if not report.passed:
                self.registry.update(agent_id, status="FAILED",
                                     last_error=report.failure_summary()[:300])
                self.timeline.emit("lead", "verify_fail", agent_id=agent_id,
                                   detail=report.failure_summary())
                return True
            self.timeline.emit("lead", "verify_pass", agent_id=agent_id,
                               checks=len(checks))
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
                    from_="lead", to=agent_id, kind="instruction",
                    body=f"# Evaluator critique\n{critique}\n\n위 문제를 해결하고 다시 [STATUS:DONE]을 보고하라.",
                )
                self.timeline.emit("lead", "verify_fail", agent_id=agent_id,
                                   detail=f"evaluator: {critique[:140]}")
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
            "lead", "merge", agent_id=agent_id,
            copied=len(merge_report.copied), conflicts=len(merge_report.conflicts),
        )

        # 충돌 발생 시 4-way 토론으로 자동 통합 시도
        if merge_report.conflicts:
            self._resolve_conflicts_via_debate(agent_id, merge_report.conflicts)

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

    # ---------- 충돌 자동 토론 + 통합 ----------

    def _resolve_conflicts_via_debate(
        self, new_agent_id: str, conflicts: list[str]
    ) -> None:
        """충돌 파일마다 4-way 토론 → 결정문 → 통합본 LLM 추출 → main 덮어쓰기 + stash 정리."""
        for rel in conflicts:
            if "symlink rejected" in rel:
                continue
            rel_clean = rel.split(" ", 1)[0]
            main_path = self.ws_main / rel_clean
            stash_path = main_path.with_name(f"{main_path.name}.from-{new_agent_id}")
            if not (main_path.exists() and stash_path.exists()):
                continue
            self._log(f"  🤝 충돌 토론 {rel_clean} ↔ from-{new_agent_id}")
            merged = self._debate_one_conflict(rel_clean, main_path, stash_path, new_agent_id)
            if merged is None:
                self._log(f"  ⚠ 통합 실패 → main/stash 둘 다 보존 (수동 처리)")
                continue
            try:
                main_path.write_text(merged, encoding="utf-8")
                stash_path.unlink(missing_ok=True)
                self._log(f"  ✓ 통합 완료 {rel_clean}")
            except OSError as e:
                self._log(f"  ⚠ 통합본 쓰기 실패: {e}")

    def _debate_one_conflict(
        self, file_rel: str, main_path: Path, stash_path: Path, new_agent_id: str
    ) -> Optional[str]:
        """충돌 1건에 대한 토론 + 통합 코드 추출."""
        from agents.debate import DebatePanel

        try:
            main_v = main_path.read_text(encoding="utf-8", errors="ignore")
            stash_v = stash_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            self._log(f"  ⚠ 충돌 파일 읽기 실패: {e}")
            return None

        new_agent_dir = self.agents_root / new_agent_id
        try:
            new_brief = (new_agent_dir / "brief.md").read_text(encoding="utf-8")[:2000]
            new_delivery = (new_agent_dir / "delivery.md").read_text(encoding="utf-8")[:1500]
        except OSError:
            new_brief = new_delivery = ""

        question = (
            f"파일 `{file_rel}` 머지 충돌. main 버전(먼저 머지된 다른 멤버 작성)과 "
            f"{new_agent_id} 버전이 다름. 양쪽 의도를 분석해 통합본을 만들거나 "
            f"한 쪽 채택을 결정. 결정 본문에 통합 방향을 명확히 명시."
        )
        context = (
            f"# Main 버전 ({file_rel}, 먼저 머지됨)\n```\n{main_v[:5000]}\n```\n\n"
            f"# {new_agent_id} 버전 (이번 충돌)\n```\n{stash_v[:5000]}\n```\n\n"
            f"# {new_agent_id} brief\n{new_brief}\n\n"
            f"# {new_agent_id} delivery\n{new_delivery}\n\n"
            f"# spec 발췌\n{self.spec[:1500]}"
        )
        debate_id = f"conflict-{Path(file_rel).name.replace('.', '_')}-{int(time.time())}"
        panel = DebatePanel(self.lead_state_dir / "debates", self.llm, max_rounds=2)
        try:
            outcome = panel.deliberate(
                question=question, context=context,
                debate_id=debate_id, auto_decide=True,
            )
        except Exception as e:
            self.timeline.emit("lead", "error",
                               error=f"conflict debate: {e}", file=file_rel)
            return None

        self.timeline.emit(
            "lead", "conflict_debated",
            agent_id=new_agent_id, file=file_rel, debate_id=debate_id,
        )

        # 결정문 기반 통합본 추출 — opus 한 번 더
        system = (
            "너는 코드 통합 작성자. 토론 결정 본문을 보고 두 버전을 통합한 최종 파일을 "
            "출력. 출력은 정확히 ```...``` 코드 펜스 하나만; 펜스 밖 텍스트 금지. "
            "결정이 '한 쪽 채택'이면 그 쪽 전체를 그대로 출력."
        )
        user = (
            f"# 토론 결정\n{outcome.decision}\n\n"
            f"# Main 버전\n```\n{main_v}\n```\n\n"
            f"# 충돌 버전\n```\n{stash_v}\n```\n\n"
            f"# 출력: 통합 파일 전체를 ```...``` 코드 펜스 하나로만 감싸서."
        )
        try:
            raw = self.llm.call(system, user, tier="opus")
        except Exception as e:
            self.timeline.emit("lead", "error",
                               error=f"merge extract: {e}", file=file_rel)
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
                "lead", "code_janitor",
                archived=len(report.archived), kept=len(report.kept),
                archive_dir=str(report.archive_dir) if report.archive_dir else "",
            )
        except Exception as e:
            self._log(f"  ⚠ code-janitor 실패: {e}")
            self.timeline.emit("lead", "error", error=f"code-janitor: {e}")

    def _run_evaluator(self, agent_id: str, agent_dir: Path, ws: Path) -> Optional[str]:
        """AdversarialVerifier 1회 호출. FAIL이면 critique 문자열 반환, 아니면 None."""
        try:
            from agents.audit import AdversarialVerifier
        except Exception as e:
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
            f"[{j.persona}] {j.evidence}" for j in report.judgements
            if j.verdict == "FAIL"
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
