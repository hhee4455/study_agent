# 팀장-팀원 자율 에이전트 시스템

요구서 `.md` 하나를 입력 받아 **팀장 에이전트가 팀원을 동적으로 채용**하면서 작업을 끝낼 때까지 돌리는 시스템.

**환경 가정**: Claude Code CLI (`claude login` 완료) + Claude Max 구독 가정. macOS / Linux.

## 30초 빠른 시작

```bash
# 1. 의존성 확인
claude --version
claude -p "ping"

# 2. 요구서 작성 (이미 meta/requirements.md 있으면 그거 사용)
cat > meta/requirements.md <<'EOF'
# 내 프로젝트
- hello.txt에 "안녕 세상" 한 줄
- README.md 한 단락
EOF

# 3. 백그라운드 실행 (산출물은 meta/ws/main, 상태는 meta/state)
WORKSPACE=./meta/ws/main CHECKPOINT=./meta/state \
  ./scripts/start.sh meta/requirements.md --max-hours 0.5

# 4. 사람 친화 로그 실시간 보기 (다른 터미널)
tail -f ./meta/state/lead/timeline.md

# 5. 정지
./scripts/stop.sh
```

## 동작 원리

1. **팀장(lead)**: Python 루프 + 짧은 LLM 호출. spec → `plan.md`로 sub-goal 분해.
2. **채용**: 미할당 goal 보면 LLM이 팀원의 페르소나/미션/검증기준을 `brief.md`로 작성.
3. **격리 spawn**: 각 팀원은 자기 `ws/{agent_id}/`에서 `claude -p` 서브프로세스로 동작. 메인 워크스페이스 못 건드림.
4. **메일박스 통신**: 팀원이 작업 중 결정 필요하면 `mailbox.md`에 `kind=question` 메시지 append + `[STATUS:WAITING]` 출력 → 즉시 세션 종료. 다음 사이클에 팀장이 LLM으로 답변 작성 → 멤버 재spawn (같은 cwd, 새 task_id).
5. **검증/머지**: 팀원이 `[STATUS:DONE]`로 종료 → `Verifier.run()` 통과 → (옵션) AdversarialVerifier 1회 critique → `ws/{agent_id}/` → `ws/main/` 머지. 충돌은 `<path>.from-{agent_id}`로 보존, 자동 머지 안 함.
6. **사람 친화 로그**: `meta/state/lead/timeline.md`가 모든 이벤트(채용/spawn/질문/답변/머지)를 한 줄씩 사람 읽기 좋게 렌더.

## 폴더 구조

```
study_agent/
├── core/                    # 인프라 (모든 에이전트가 의존)
│   ├── budget.py            # 시간/턴 한도
│   ├── llm.py               # 모델 티어링 LLM 클라이언트
│   ├── session_manager.py   # claude -p 서브프로세스 spawn (격리 cwd)
│   ├── verifier.py          # shell/file_exists/file_contains 객관 검증
│   ├── rate_limit.py        # exponential backoff
│   ├── health.py            # 디스크/메모리 헬스체크
│   ├── cli_caller.py        # claude CLI 호출 stream-json 파서
│   └── incidents.py         # 구조화 이벤트 로그
│
├── lead/                    # 팀장 시스템 (진입점)
│   ├── main.py              # python -m lead.main
│   ├── team_lead.py         # tick 루프, LLM 결정
│   ├── member.py            # MemberSpawner (팀원 채용/spawn)
│   ├── mailbox.py           # 메시지 read/write/scan (HTML-주석 마커)
│   ├── registry.py          # agents.json 인덱스 + 디스크 rehydrate
│   ├── workspace.py         # ws 머지 + 충돌 보고
│   ├── timeline.py          # 사람 친화 timeline.md 렌더러
│   ├── tools.py             # 옵션 도구 wrapper (debate/evaluator/janitor)
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
├── meta/                    # 런타임 상태 (gitignore 추천)
├── scripts/                 # start.sh / stop.sh / status.sh
└── tests/test_lead.py       # 23개 테스트 (단위/integration/path_guard/sanity)
```

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

## 종료 코드

| 코드 | 의미 |
|------|------|
| 0    | 모든 plan goal 완료 |
| 3    | 진행 정체 (10회 연속 tick에서 변화 없음) |
| 4    | budget(시간/턴) 한도 또는 rate limit 한도 |
| 6    | claude CLI 미설치/로그인 안 됨 |
| 130  | 사용자 중단 |

## 테스트

```bash
python3 tests/test_lead.py
# 15/15 passed (단위 12 + integration 3)
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
