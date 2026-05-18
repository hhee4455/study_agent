# 팀장-팀원 자율 에이전트 시스템

요구서 `.md` 하나를 입력 받아 **팀장 에이전트가 팀원을 동적으로 채용**하면서 작업을 끝낼 때까지 돌리는 시스템.

**환경 가정**: Claude Code CLI (`claude login` 완료) + Claude Max 구독 가정. macOS / Linux.

## 디렉토리 구조 (옵션 A — 시스템과 작업물 완전 분리)

```
sentinel-deepactive/
├── agent_system/         # ★ 자가 검증/성장 시스템 (이 리포)
│   ├── core/ lead/ agents/  # 코드
│   ├── tests/ scripts/
│   ├── decisions/        # 시스템 진화 토론 결과 (p4/p5 등)
│   └── README.md
└── workspace/            # ★ 실 작업물 (별도 git 가능)
    ├── project.md        # 입력 spec (단일 source of truth — 매번 이것만 갱신)
    ├── state/            # 런타임 (lead 로그, agents.json, ...)
    └── ws/
        ├── main/         # 멤버 산출물 통합 결과
        └── members/      # 멤버별 격리 ws (M001, M002, ...)
```

## 30초 빠른 시작

```bash
# 1. 의존성 확인
claude --version && claude -p "ping"

# 2. 프로젝트 정의 작성 (workspace/project.md)
cat > ../workspace/project.md <<'EOF'
# 내 프로젝트
- hello.txt에 "안녕 세상" 한 줄
- README.md 한 단락
EOF

# 3. 백그라운드 실행 (agent_system/ 안에서)
./scripts/start.sh ../workspace/project.md --max-hours 0.5

# 4. 사람 친화 로그 실시간 보기 (다른 터미널)
tail -f ../workspace/state/lead/timeline.md

# 5. 정지
./scripts/stop.sh
```

## main 점진 강화 흐름

기존 ws/main 위에 새 기능/전략을 추가하고 싶을 때:

```bash
# 1. project.md 의 해당 섹션을 새 내용으로 수정 (전체 spec 통합 유지)
$EDITOR ../workspace/project.md

# 2. 이전 사이클의 멤버 ws / state 정리
./scripts/reset.sh --apply

# 3. --replan 으로 새 spec 기반 plan 재분해 + ws/main 트리 컨텍스트 자동 전달
./scripts/start.sh ../workspace/project.md --replan --max-hours 2 --max-parallel 2
```

- `--replan`: 기존 `state/lead/plan.md` 를 `plan.replaced-{ts}.md` 로 백업 후 새 spec 으로 재분해
- lead 가 ws/main 의 현재 파일 트리를 보고 "기존 수정 vs 신규 작성" 자체 판단 → goal 생성
- 멤버는 ws/main 의 기존 파일 경로 그대로 *수정본* 작성 → 단일 멤버 vs main 충돌은 4-페르소나 debate panel 이 자동 통합. 두 멤버가 같은 파일을 동시 수정한 경우는 순차 머지(먼저 머지된 버전 위에 두 번째 멤버의 변경이 충돌로 보존됨, 3-way 머지 없음 — 라인 168 참고).

## 동작 원리

1. **팀장(lead)**: Python 루프 + 짧은 LLM 호출. spec → `plan.md`로 sub-goal 분해.
2. **채용**: 미할당 goal 보면 LLM이 팀원의 페르소나/미션/검증기준을 `brief.md`로 작성.
3. **격리 spawn**: 각 팀원은 자기 `ws/{agent_id}/`에서 `claude -p` 서브프로세스로 동작. 메인 워크스페이스 못 건드림.
4. **메일박스 통신**: 팀원이 작업 중 결정 필요하면 `mailbox.md`에 `kind=question` 메시지 append + `[STATUS:WAITING]` 출력 → 즉시 세션 종료. 다음 사이클에 팀장이 LLM으로 답변 작성 → 멤버 재spawn (같은 cwd, 새 task_id).
5. **검증/머지**: 팀원이 `[STATUS:DONE]`로 종료 → `Verifier.run()` 통과 → (옵션) AdversarialVerifier 1회 critique → `ws/{agent_id}/` → `ws/main/` 머지. 충돌은 `<path>.from-{agent_id}`로 보존, 자동 머지 안 함.
6. **사람 친화 로그**: `workspace/state/lead/timeline.md`가 모든 이벤트(채용/spawn/질문/답변/머지)를 한 줄씩 사람 읽기 좋게 렌더.

## 모듈 구성 (agent_system/ 내부)

```
agent_system/
├── core/                    # 인프라 (모든 에이전트가 의존)
│   ├── budget.py            # 시간/턴 한도
│   ├── llm.py               # 모델 티어링 LLM 클라이언트
│   ├── session_manager.py   # claude -p 서브프로세스 spawn (격리 cwd)
│   ├── verifier.py          # shell/file_exists/file_contains 객관 검증
│   ├── rate_limit.py        # exponential backoff
│   ├── health.py            # 디스크/메모리 헬스체크
│   ├── cli_caller.py        # claude / codex CLI 호출 wrapper
│   └── path_guard.py        # 경로 escape 차단 (P5 결정)
│
├── lead/                    # 팀장 시스템 (진입점)
│   ├── main.py              # python -m lead.main
│   ├── team_lead.py         # tick 루프, LLM 결정
│   ├── member.py            # MemberSpawner (팀원 채용/spawn)
│   ├── mailbox.py           # 메시지 read/write/scan (HTML-주석 마커)
│   ├── registry.py          # agents.json 인덱스 + 디스크 rehydrate
│   ├── workspace.py         # ws 머지 + 충돌 보고
│   ├── timeline.py          # 사람 친화 timeline.md 렌더러
│   └── prompts/             # 외부화된 LLM 프롬프트
│       ├── plan_initial.md  # spec → plan 분해
│       ├── hire_brief.md    # 팀원 채용 brief JSON
│       ├── reply.md         # 질문 답변
│       └── driver.md        # 팀원 운영 규칙
│
├── agents/                  # 특화 도구 에이전트 (lead가 자율 호출)
│   ├── debate/panel.py      # 4-페르소나 토론 (claude×3 + codex×1), 팀장 자동 결정
│   ├── audit/adversarial.py # Adversarial Evaluator (per-hire 토글, P4 결정)
│   └── janitor/code_janitor.py # 사용 안 하는 .py를 .archive/로 이동 (lead 자율 호출)
│
├── decisions/               # 시스템 진화 토론 결과 (p4/p5 등 영구 기록)
├── scripts/                 # start.sh / stop.sh / status.sh
└── tests/test_lead.py       # 24개 테스트 (단위/integration/path_guard/sanity)
```

런타임 상태(`workspace/state/`, `workspace/ws/`)는 위 트리 밖, 부모 디렉토리의 형제 위치에 생성됨 — 30초 빠른 시작의 트리 참조.

## 빅테크 패턴 매핑

| 업계 패턴 | 우리 구현 |
|---|---|
| Lead → Specialist handoff (Anthropic Claude Agent SDK) | `mailbox.md` + `brief.md` |
| 구조화 artifact (no hidden state, OpenAI Agents SDK) | `brief.md` / `delivery.md` / `mailbox.md` |
| Guardrails (4-layer) | `SessionConfig.allowed_tools` + `shell_sanity_check` + 호스트 deny-list + `resolve_within` |
| Tracing | `timeline.md` (사람 친화) + `events.jsonl` (raw) |
| Evaluator critique-refine (Anthropic Apr 2026) | per-hire `verify` 필드 (P4 결정), 전역 `--enable-evaluator`는 디버그용 |
| Multi-source debate (belief entrenchment 완화) | 4 페르소나: claude×3(opus+sonnet×2) + codex×1(gpt-5.4-mini) |
| 자동 의사결정 hub | lead가 high-stakes 질문 자체 판별 → 토론 자동 소집 → 팀장 결정 |
| 자율 운영 | lead가 N=10 hire마다 code-janitor 필요 여부 LLM 판단 |

## 옵션

```bash
python -m lead.main \
  --spec /path/spec.md \
  --workspace /tmp/run/ws/main \
  --checkpoint /tmp/run/state \
  --max-hours 0.5 \
  --max-turns 500 \
  --model opus \
  --enable-evaluator   # 비용 ↑ 품질 ↑ (각 멤버 산출물에 회의주의 1 cycle)
```

### CLI Flags

| Flag | 기본값 | 설명 |
|------|--------|------|
| `--spec` | *(필수)* | 요구서 `.md` 파일 경로 |
| `--workspace` | *(필수)* | 메인 워크스페이스 경로 (`ws/main`) |
| `--checkpoint` | *(필수)* | 런타임 상태 저장 디렉토리 |
| `--max-parallel N` | `3` | 동시 실행 팀원 수. 클수록 속도 ↑ but burst rate limit · 충돌 ↑. `1`=직렬 안전 모드 |
| `--enable-evaluator` | `false` | 각 멤버 산출물에 AdversarialVerifier 1 cycle 추가 (비용 ↑, 품질 ↑) |
| `--replan` | `false` | 기존 `plan.md`를 `plan.replaced-{ts}.md`로 archive 후 새 spec으로 재분해. spec 변경 시 사용 |
| `--skip-preflight` | `false` | 시작 시 claude CLI 설치·로그인 확인 건너뜀. 빠른 재시작 또는 CI 환경용 |
| `--max-hours H` | `12.0` | 최대 실행 시간(시간 단위). 초과 시 exit code 4 |
| `--max-turns N` | `2000` | 전체 tick 루프 최대 횟수. 초과 시 exit code 4 |
| `--max-cost-usd N` | `∞` | 최대 누적 API 비용(USD). 초과 시 exit code 4 |
| `--model M` | `opus` | 팀장 의사결정 기본 모델 (`opus` / `sonnet` / `haiku`) |

**예시:**

```
# 직렬 안전 모드 + 평가자 활성화 (2시간 한도)
python -m lead.main --spec project.md --workspace ws/main --checkpoint state \
  --max-parallel 1 --enable-evaluator --max-hours 2

# spec 변경 후 plan 재분해 (preflight 생략)
python -m lead.main --spec project.md --workspace ws/main --checkpoint state \
  --replan --skip-preflight --max-parallel 2
```

## 종료 코드

| 코드 | 의미 |
|------|------|
| 0    | 모든 plan goal 완료 |
| 3    | 진행 정체 (10회 연속 tick에서 변화 없음) |
| 4    | budget(시간/턴) 한도 또는 rate limit 한도 |
| 6    | claude CLI 미설치/로그인 안 됨 |
| 130  | 사용자 중단 |

## 문제 해결 (Troubleshooting)

### 증상별 빠른 진단

| 증상 | 원인 | 권장 조치 |
|------|------|-----------|
| 시스템이 멈추고 exit code 3 ("진행 가능 작업 없음" = 진행 정지) | 10회 연속 tick에서 상태 변화 없음 — 모든 goal이 WAITING이거나 실행 가능 멤버 없음 | `state/lead/timeline.md`로 최근 tick 확인 → WAITING 멤버의 `mailbox.md` 질문에 직접 답변 후 재실행. plan 자체가 소진된 경우 `--replan`으로 재분해 |
| `plan.md`가 비어있거나 goal이 생성되지 않음 (빈 plan / empty plan) | spec(`project.md`) 내용이 너무 짧거나 불명확 → LLM이 sub-goal 분해 실패 | `project.md` 내용 보완 후 `./scripts/reset.sh --apply`로 상태 초기화 → 재실행 시 `--replan` 추가 |
| 동일 멤버가 WAITING → 재spawn → WAITING을 반복 (member 무한 루프 / infinite loop) | 팀장 자동 답변이 멤버 질문을 해소하지 못하거나, brief 검증 기준이 달성 불가능 | `state/agents/{agent_id}/mailbox.md` 직접 확인 → 질문이 모호하면 brief 수정 또는 `--max-turns`를 낮춰 자연 종료 유도 |
| `ws/main/`에 `.from-{agent_id}` 파일이 누적 (conflict 누적) | 두 멤버가 같은 파일을 동시 수정 → 자동 3-way 머지 없음, 충돌 파일 보존 방식 | `ws/main/conflicts/{ts}.md` 보고서 확인 → 충돌 파일 직접 수정 후 `.from-{agent_id}` 제거. 재발 방지는 `--max-parallel 1`로 직렬 실행 |
| exit code 6 또는 stderr에 "claude CLI 미설치/로그인 안 됨" (인증 실패 / auth failure) | `claude` CLI가 PATH에 없거나 `claude login`이 미완료 | `claude login`으로 재인증 → `claude -p "ping"`으로 동작 확인. 미설치 시 `npm i -g @anthropic-ai/claude-code`. 빠른 재시작 시 `--skip-preflight` 가능 (단, 런타임에서 같은 오류 재발 가능) |

### CLI Flag 요약

| Flag | 기본값 | 설명 |
|------|--------|------|
| `--max-parallel` | `3` | 동시 실행 팀원 수. 클수록 속도 ↑ but burst rate limit · 충돌 ↑. `1`=직렬 안전 모드 |
| `--enable-evaluator` | `false` | 각 멤버 산출물에 AdversarialVerifier 1 cycle 추가 (비용 ↑, 품질 ↑) |
| `--replan` | `false` | 기존 `plan.md`를 `plan.replaced-{ts}.md`로 archive 후 새 spec으로 재분해. spec 변경 시 사용 |
| `--skip-preflight` | `false` | 시작 시 claude CLI 설치/로그인 확인 건너뜀. 빠른 재시작 또는 CI 환경용 |
| `--max-turns` | `2000` | 전체 tick 루프 최대 횟수. 초과 시 exit code 4 |
| `--max-hours` | `12.0` | 최대 실행 시간(시간 단위). 초과 시 exit code 4 |
| `--max-cost-usd` | `∞` | 최대 누적 API 비용(USD). 초과 시 exit code 4 |

## 테스트

```bash
python3 tests/test_lead.py
# 24/24 passed (mailbox/registry/workspace/timeline/team_lead/verifier/path_guard/code_janitor)
```

Integration test는 stub LLM + stub spawner로 전체 cycle 검증:
- `test_team_lead_full_cycle`: plan → hire → spawn(DONE) → verify → merge → 종료
- `test_team_lead_question_reply_cycle`: 첫 spawn WAITING+question → reply → 재spawn DONE
- `test_team_lead_member_failed`: spawn FAILED → registry 갱신 → 진행 정체로 종료

## 알려진 한계

1. **검증기는 객관적**: shell exit / file_exists / file_contains만. 시맨틱 정확도는 보장 못 함. `--enable-evaluator`로 보완 가능하나 비용 증가.
2. **Weekly rate limit**: Max 구독도 무한 아님. 24h 풀가동 시 도달 가능.
3. **2층 hierarchy**: 팀원이 서브-팀원 채용 못 함. 의도된 단순화 (디버깅 가능성 ↑).
4. **3-way 머지 없음**: 충돌 시 자동 해결 안 함, `<path>.from-{agent_id}`로 보존 + `conflicts/{ts}.md` 보고.
