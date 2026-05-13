# Debate: 팀원 워크스페이스 격리를 어디까지 강화할까? (a) 현 상태(cwd만): subprocess cwd=ws/{agent_id}/, OS 차원 격리

**Question**: 팀원 워크스페이스 격리를 어디까지 강화할까? (a) 현 상태(cwd만): subprocess cwd=ws/{agent_id}/, OS 차원 격리 없음, 단순. (b) macOS sandbox-exec: 시스템 호출 정책 파일로 파일/네트워크 제한, macOS 한정. (c) Docker 컨테이너: 진짜 격리, 멤버마다 컨테이너, 인프라 부담 ↑↑. (d) firejail/bubblewrap (Linux): macOS 부적합. claude max 사용자(주로 macOS) 기준 합리적 선택?

**Context**:
현재 위협: LLM이 hire_brief에 위험 verification_checks 생성 → verifier 실행. 이는 P1에서 sanity check (deny-list + whitelist) 으로 1차 차단됨. 추가 위협: 멤버 claude -p 세션 자체가 cwd 밖 파일 Read 시도 (claude CLI auto-mode classifier가 일부 차단 확인됨, 라이브에서). 네트워크 차단 X. 심볼릭링크 검사 X. 이 시스템은 1인 개발자가 자기 spec 돌리는 용도지 untrusted user-facing 아님.

**Started**: 2026-05-13T05:28:17.312305

---

## Round 1

[Agent-A / Pragmatist]
위협 모델이 "자기 spec 돌리는 1인 개발자"라면 (a)로 충분하다. Docker는 인프라 복잡도가 실익을 압도하고, sandbox-exec는 macOS 최신 버전에서 deprecated 수순이라 기술 부채다.

진짜 위협은 LLM이 cwd 밖 파일을 읽는 것인데, 이미 claude CLI auto-mode classifier가 라이브에서 일부 차단 확인됐다면 P1 deny-list와 조합으로 현실적 위험은 낮다. 네트워크는 LLM이 외부 데이터를 몰래 exfiltrate할 이유가 없는 self-use 시나리오에서 차단 우선순위가 낮다.

추가로 고려할 것이 있다면 Docker가 아니라 symlink 검사 한 줄 추가 + 허용 경로 whitelist를 cwd 기준으로 강제하는 게 훨씬 싸다. 격리 강화에 시간 쓸 바에 실제 기능 진도를 빼는 게 낫다.

[Agent-B / Skeptic]
sandbox-exec(b) 선택이 "합리적 중간"처럼 보이지만, macOS Ventura 이후 sandbox-exec는 deprecated 경고가 뜨고 Apple이 공식 지원을 사실상 중단했다. 이걸 지금 프로덕션 격리의 핵심으로 세우는 건 모래 위에 집 짓는 것이다. Docker(c)는 "진짜 격리"라고 하지만 1인 개발자가 자기 spec 돌리는 시스템에서 컨테이너 오버헤드는 치료가 병보다 심한 경우다. 그리고 가장 근본적인 문제: 위협이 "LLM이 cwd 밖 파일 읽기 시도"라면, 이미 P1 sanity check와 CLI auto-mode classifier가 부분 차단하고 있는데 추가 OS 격리가 실제로 막는 벡터가 뭔지 구체적으로 정의된 게 없다. 격리 강화 전에 실제 탈출 성공 케이스가 기록된 적 있는지부터 물어야 한다.

[Agent-C / Architect]
위협 모델이 "1인 개발자가 자기 spec 돌리는 용도"라면 (a)가 정답이고, sandbox-exec/Docker는 자기 자신으로부터 자신을 보호하는 과잉설계다. 다만 장기 원칙으로, cwd 격리에 두 가지만 추가하라 — 심볼릭링크 거부(realpath로 ws/{agent_id}/ prefix 검증)와 .env/~/.ssh/~/.aws 같은 민감 경로 deny-list. 이건 sandbox-exec 정책 파일 한 줄보다 싸고, 미래에 멀티테넌트로 가도 살아남는 불변식이다. sandbox-exec는 macOS에서 deprecated 경고가 붙은 지 오래라 거기에 베팅하면 부채가 된다. Docker는 "untrusted input을 실제로 받게 될 때" 한 번에 도입할 결정이지, 지금 점진적으로 끼워넣을 게 아니다.

[Agent-D / Outsider]
cwd만으로는 경계가 너무 약합니다. 특히 symlink와 네트워크가 열려 있으면, “workspace 밖은 안 본다”는 가정이 쉽게 깨집니다. macOS 주 사용이라면 Docker보다 sandbox-exec 쪽이 비용 대비 효과가 낫고, 네트워크 차단과 workspace allowlist만 제대로 걸어도 체감 위험은 크게 줄어듭니다. 다만 이건 전면 격리가 아니라 운영상 안전장치라는 점을 인정해야 하고, untrusted 용도가 아니면 과도한 컨테이너화는 생산성만 깎을 가능성이 큽니다.

---

## Round 2

[Agent-A / Pragmatist]
sandbox-exec는 macOS 10.15부터 사실상 deprecated 경고가 뜨고 Apple Silicon에서 동작이 불안정하다 — 공식 지원이 끊긴 기술에 베팅하는 건 pragmatist가 아니라 gambler다. 1인 자기 spec용이라면 "cwd 탈출 Read"의 실제 피해는 민감한 파일을 읽는 것인데, `~/.ssh`, `~/.aws` 같은 고위험 경로만 deny-list에 추가하면 추가 인프라 없이 90%가 막힌다. Docker는 격리 품질은 최고지만 멤버마다 컨테이너 띄우면 로컬 맥북에서 메모리·스타트업 오버헤드가 실제 개발 흐름을 방해한다 — 이득보다 마찰이 크다. 따라서 현 상태(a) + 경로 deny-list 강화가 이 위협 모델에 비례하는 유일한 선택이다.

[Agent-B / Skeptic]
deny-list는 "알려진 민감 경로"만 막는데, 실제 위협은 LLM이 예측 못 한 경로를 읽는 것이다 — .env, 프로젝트 내 DB 크리덴셜, 브라우저 쿠키 등은 deny-list가 열거하기 전까지 무방비다. "90% 차단"은 출처 없는 수치이고, 나머지 10%가 정확히 고가치 타깃일 수 있다. Docker 오버헤드 주장은 맞지만 "멤버마다 컨테이너"가 아니라 단일 컨테이너에 agent별 volume mount를 격리하면 스타트업 비용은 한 번이다 — 이 옵션을 검토 없이 기각한 건 허수아비 논리다. 1인용이라는 이유로 격리 수준을 낮추는 건 위협 모델이 아니라 편의에 기반한 선택이다.

[Agent-C / Architect]
단일 Docker + per-agent volume mount는 흥미로운 절충이지만, claude CLI 자체가 컨테이너 안에서 host의 인증/세션을 어떻게 끌어올지가 비자명하다 — max 구독 인증을 컨테이너로 마운트하는 순간 격리의 실익이 절반 사라진다. 원칙적으로 격리는 "신뢰 경계"가 있을 때 비용을 지불할 가치가 있는데, 이 시스템은 사용자=피해자=운영자가 동일인이고 외부 공격면이 없다 — LLM의 오작동은 위협이 아니라 버그 클래스다. 따라서 격리 메커니즘보다 **불변식(invariant)**에 투자해야 한다: 모든 파일 접근을 ws/{agent_id}/ prefix로 정규화한 후 realpath 검사 한 줄, 심볼릭링크 거부, 그리고 verifier 출력의 path 필드를 구조적으로 제약. 이게 (a)를 유지하면서 Skeptic이 지적한 "열거 불가능한 민감 경로" 문제를 deny-list가 아닌 allow-list 방식으로 뒤집어 해결하는 길이다.

[Agent-D / Outsider]
나는 (a)만으로는 부족하다고 본다. 지금의 핵심 위험은 단순한 cwd 이탈 읽기보다, 에이전트가 워크스페이스 안에서 만든 부작용이 호스트 상태나 다음 라운드에 누적되는 점이라서, 파일 읽기 deny-list만으로는 경계가 얇다. macOS에서 굳이 sandbox-exec 전체에 베팅할 필요는 없지만, 최소한 에이전트별 disposable worktree를 강제로 쓰고 결과만 패치로 반영하는 방식은 들어가야 한다. 그래서 결론은 “강한 OS 샌드박스”가 아니라 “호스트와 분리된 임시 작업공간 + 반영 게이트” 쪽이 더 맞다.

---

## Summary
# 팀원 워크스페이스 격리 토론 요약

## 옵션 1: 현 상태 유지 (a)
**정의**: `subprocess cwd=ws/{agent_id}/` + OS 격리 없음  
**지지**: Agent-A (Pragmatist), Agent-C (Architect)  
**근거**:  
- 위협 모델이 "1인 자기 spec용"이면 인프라 복잡도가 실익을 압도
- 이미 P1 sanity check + CLI auto-mode classifier가 부분 차단
- 네트워크 exfiltration 위험이 self-use 시나리오에서 낮음

**비판** (Agent-B):  
- deny-list는 "알려진 경로"만 막음 → 예측 불가능한 민감 경로는 무방비
- "90% 차단"의 실제 효과 불명확

---

## 옵션 2: 현 상태 + 경로 deny-list 강화
**정의**: (a) + `~/.ssh`, `~/.aws`, `.env` 등 고위험 경로 명시적 차단  
**지지**: Agent-A (Pragmatist)  
**근거**:  
- 추가 인프라 없이 고가치 타깃 90% 차단 가능
- Docker 오버헤드(메모리, 스타트업)보다 경명, 운영 마찰 ↓

**비판** (Agent-B, Agent-C):  
- 열거 불가능한 민감 경로 존재 → whitelist가 더 견고
- 원칙 부재 (어떤 경로를 왜 차단할지 체계 없음)

---

## 옵션 3: 현 상태 + Allow-list 불변식 (Agent-C 제안)
**정의**: (a) + realpath 검사 + symlink 거부 + `ws/{agent_id}/` prefix 강제  
**지지**: Agent-C (Architect)  
**근거**:  
- deny-list 역반전: 명시된 경로만 접근 허용
- symlink 우회 차단
- 미래 멀티테넌트 전환 시에도 불변식으로 작동
- verifier 출력(path 필드)를 구조적으로 제약 가능

**차별점**: 운영 원칙을 세움 (whitelist 아키텍처)

---

## 옵션 4: macOS sandbox-exec (b)
**정의**: 시스템 호출 정책 파일로 파일/네트워크 제한  
**지지**: *(라운드 2에서 Agent-D가 언급했으나 명시 지지 없음)*  
**근거**: 파일/네트워크 OS 차원 제어, macOS 최적화

**비판** (Agent-A, Agent-B, Agent-C):  
- **macOS Ventura 이후 deprecated** (공식 지원 중단)
- Apple Silicon에서 동작 불안정성 알려짐
- 모래 위에 집 짓기 → 기술 부채화 위험
- 현재 프로덕션에 도입하면 유지보수 비용 ↑

---

## 옵션 5: Docker 컨테이너 (c)
**정의**: agent마다 또는 단일 컨테이너 + per-agent volume mount  
**지지**: Agent-B (Skeptic), Agent-D (일부)  
**근거**:  
- "진정한" 격리, 최고 수준의 경계 강도
- 단일 컨테이너 + volume mount면 스타트업 비용 한 번

**비판** (Agent-A, Agent-C):  
- 1인용 로컬 맥북에서 메모리·스타트업 오버헤드 → 개발 흐름 마찰 ↑
- **claude CLI가 컨테이너 안에서 호스트 인증을 끌어올 방법 비자명**
- 격리 이득 vs 세팅 복잡도 trade-off 불명확

---

## 옵션 6: Disposable worktree + 반영 게이트 (Agent-D 제안)
**정의**: agent별 임시 작업공간(worktree) 강제 + 호스트로 패치 반영 시 게이트  
**지지**: Agent-D (Outsider)  
**근거**:  
- OS 격리가 아니라 **부작용 격리** (다음 라운드 누적 방지)
- symlink/네트워크보다 상태 누적이 실제 위협
- 호스트와 분리된 임시 공간 → 에이전트 오작동 범위 제한

**차별점**: 위협 모델을 "파일 읽기"에서 "상태 누적"으로 재정의

---

## 합의된 부분
1. ✓ 위협 모델: "1인 개발자가 자기 spec 돌리는 용도" (untrusted user-facing ≠)
2. ✓ sandbox-exec는 **deprecated 기술** → 지금 도입하면 부채
3. ✓ LLM이 **cwd 밖 파일 읽기 시도**가 주요 벡터
4. ✓ P1 sanity check + CLI classifier가 **이미 부분 차단** (라이브 확인)
5. ✓ Docker는 격리 품질은 최고지만 로컬 개발 오버헤드 ↑

---

## 미해결 쟁점

| 쟁점 | 정보 부족 |
|------|---------|
| **deny-list 실제 효과** | "90% 차단"은 정량화 필요; 예측 불가능한 민감 경로 재고 |
| **부작용 누적 vs 읽기 제어** | Agent-D가 "상태 누적"을 위협으로 지목했으나, 다른 Agent들은 "읽기 탈출"에 집중 → 실제 피해 경로가 뭔지 명확화 필요 |
| **실제 탈출 성공 사례** | Agent-B: "추가 OS 격리가 실제로 막는 벡터가 구체적으로 정의된 게 없다" |
| **claude CLI 컨테이너화의 비자명성** | max 구독 인증, 세션 관리를 컨테이너와 어떻게 연결할지 아키텍처 불명 |
| **심볼릭링크 공격** | allow-list 방식이 symlink를 차단하는가? realpath 검사 구현 상세 필요 |

---

## 운영자 주의

⚠️ **Belief entrenchment 신호 가능성**:  
- Agent-A는 pragma로, Agent-C는 아키텍처 원칙으로, Agent-B는 최악 가정으로 각각 다른 이유로 최종 선택에 수렴할 수 있음  
- 실제 의사결정 전에 **"실제 위협 벡터가 뭔가"** (읽기 vs 상태 누적 vs 네트워크)를 1차로 정의하고, **기존 방어층(P1 sanity check, CLI classifier)이 이미 막는 것**을 재확인 후 추가 투자 판단 권장

---

다음 스텝 제안:
- 실제 탈출 성공 케이스 로그 조사 → 없으면 현 상태에서 모니터링 강화
- allow-list 방식(옵션 3)의 구현 비용 정량화 (symlink 검사, realpath 비용 측정)
- 단일 Docker + volume mount(옵션 5 일부)의 claude CLI 호환성 PoC

## 최종 결정
**결정**: 옵션 1(a) 현 상태 유지 + 옵션 3의 allow-list 불변식(realpath 정규화 + `ws/{agent_id}/` prefix 강제 + symlink 거부) + `~/.ssh`, `~/.aws`, `.env` 등 고위험 deny-list 최소 1줄 추가. sandbox-exec(b)/Docker(c)는 지금 도입하지 않는다.

**근거**: 위협 모델이 "1인 개발자 self-use"로 합의됐고, 사용자=피해자=운영자가 동일인이라 OS 차원 격리의 ROI가 낮다는 Agent-C의 신뢰 경계 논리를 채택한다. Agent-B의 "예측 불가능한 민감 경로" 우려는 deny-list가 아니라 Agent-C의 allow-list 불변식(prefix + realpath)으로 구조적으로 뒤집어 해결한다 — 열거가 아니라 경계 정규화이기 때문이다. sandbox-exec는 deprecated 부채 위험에 세 페르소나가 일치했고, Docker는 claude CLI의 max 인증 컨테이너 마운트 문제가 미해결이라 격리 실익이 절반 사라진다. Agent-D의 "상태 누적" 우려는 타당하지만 현재 단계에선 ws/{agent_id}/가 라운드별로 재생성되는지 확인하는 운영 점검으로 충분하고, disposable worktree 전면 도입은 실제 누적 피해가 관측된 후 결정한다.

**즉시 실행**: `core/`에 path 가드 헬퍼 추가 — `resolve_safe(path, agent_id)`가 `Path(path).resolve()`로 realpath 정규화 후 `ws/{agent_id}/` prefix 아니면 reject, symlink 발견 시 reject; verifier 실행 직전과 모든 파일 I/O 진입점에서 호출.

**후속 작업**:
- `tests/`에 path 가드 단위 테스트 추가 (symlink, `..`, 절대경로, `~` 확장 케이스)
- 라운드 종료 시 `ws/{agent_id}/` 디렉토리 상태 로그 1줄 (생성/유지/삭제) → Agent-D의 누적 위협 관측 데이터 확보
- 라이브에서 claude CLI auto-mode classifier의 차단 로그 샘플링 → "실제 탈출 시도" 베이스라인 확보 후 90일 뒤 격리 강화 재검토
