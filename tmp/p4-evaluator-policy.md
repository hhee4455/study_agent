# Debate: 팀장-팀원 에이전트 시스템에서 AdversarialVerifier (3-페르소나 회의주의 검토, 각 멤버 산출물에 critique-refine 

**Question**: 팀장-팀원 에이전트 시스템에서 AdversarialVerifier (3-페르소나 회의주의 검토, 각 멤버 산출물에 critique-refine 1 cycle)의 기본값을 어떻게 잡을까? (a) always-on: 모든 멤버 산출물에 자동, 비용 ~2배, 품질 ↑. (b) selective: lead가 LLM 판단으로 high-risk만 평가, 메타 복잡도 ↑. (c) off (현재): --enable-evaluator 옵션 켜야 작동, 단순. 어떤 기본값이 적절한지 + 그 선택의 trade-off?

**Context**:
현 시스템: lead가 멤버 hire → 격리 ws에서 spawn → [STATUS:DONE] 보고 → Verifier(객관 shell/file_exists/file_contains) 통과 → merge. AdversarialVerifier는 도구로 존재하지만 --enable-evaluator일 때만 호출. Verifier가 미세한 의미적 결함은 못 잡음. claude max 구독 비용은 토큰 카운트 0 이지만 weekly rate limit이 진짜 한도.

**Started**: 2026-05-13T05:25:21.256152

---

## Round 1

[Agent-A / Pragmatist]
(c) off가 맞는 기본값이다. 현재 병목은 의미적 품질이 아니라 rate limit이고, AdversarialVerifier를 always-on으로 켜면 같은 rate limit 예산으로 처리 가능한 실제 작업량이 절반으로 줄어든다. selective는 "LLM이 high-risk를 판단"하는 메타 레이어가 또 rate limit을 소모하며, 그 판단 자체가 틀릴 때 false confidence를 만든다. 기본값은 사용자가 바꾸지 않으면 쓰는 값인데, 검증 오버헤드가 필요 없는 단순 작업에까지 비용을 강제 부과하는 설계는 잘못됐다. --enable-evaluator로 명시 opt-in하게 두고, 검증이 필요한 도메인(코드 생성, 보안 관련)에 대해서만 켜는 게 실용적이다.

[Agent-B / Skeptic]
"비용 ~2배"라는 프레임 자체가 틀렸다. Claude Max에서 진짜 한도는 weekly rate limit이고, always-on은 trivial 산출물에도 3-페르소나 사이클을 돌려 rate budget을 소진한다 — 이는 정작 복잡한 작업이 올 때 시스템 전체를 throttle한다.

selective(b)의 치명적 약점은 "LLM이 high-risk를 판단한다"는 전제다. 메타 판단 자체가 틀릴 수 있고, lead가 고위험을 low-risk로 오분류하면 Verifier를 안 돌리는 것보다 나쁘다 — 검토했다는 착각만 생긴다.

그러나 off(c)를 기본값으로 유지하는 것도 방어할 수 없다. 옵션 플래그는 실제로 아무도 안 켠다 — 현재 Verifier가 "의미적 결함을 못 잡는다"고 이미 인정했다면 이는 known gap이고, 기본값 off는 그 gap을 영구히 무시하겠다는 선택이다.

결론: 세 옵션 모두 문제의 핵심을 회피한다. AdversarialVerifier가 실제로 의미적 결함을 잡는다는 empirical 근거가 먼저다 — 그 데이터 없이 기본값 논쟁은 설계 미신이다.

[Agent-C / Architect]
기본값은 (c) off 유지가 옳다. 시스템 경계의 원칙은 "검증은 객관성이 보장되는 층에서만 자동화한다"이며, 회의주의 페르소나 LLM은 본질적으로 비결정적·주관적 산출물이라 자동 게이트로 만들면 가짜 안정감만 준다. always-on은 rate limit이라는 진짜 희소자원을 모든 산출물에 균등 소모하므로 장기적으로 시스템 처리량을 깎고, selective는 lead의 메타-판단을 또 다른 미검증 LLM 결정에 의존시켜 책임 소재를 흐린다. 대신 AdversarialVerifier는 명시적 도구로 두고, 결함이 실제로 누수되는 패턴이 관찰되면 객관 Verifier 규칙으로 승격시키는 경로를 만들어야 한다 — 그것이 진짜 품질 개선이다. 옵션 플래그는 단순함이 아니라 "자동화할 만큼 신뢰 가능한가"라는 질문을 미루지 않고 매번 마주하게 하는 장치다.

[Agent-D / Outsider]
기본값은 off가 맞습니다. weekly rate limit이 진짜 병목인 상황에서 always-on은 대부분의 작업에 과한 세금이고, selective는 lead의 LLM 판단에 또 다른 오판 지점을 얹어서 운영 복잡도만 키웁니다. 대신 고위험 작업만 명시적으로 켜는 opt-in 경로를 두는 편이 낫습니다. 트레이드오프는 의미적 결함 일부를 기본 경로에서 놓칠 수 있다는 점이지만, 그 대가로 처리량과 예측 가능성을 지킬 수 있습니다.

---

## Round 2

[Agent-A / Pragmatist]
weekly rate limit이 진짜 한도라면 always-on은 단순히 "비용 2배"가 아니라 큰 작업에서 시스템 자체가 멈추는 리스크다. selective는 lead가 "이게 high-risk인가"를 판단하는 데 또 LLM 호출이 필요하니 메타 비용이 숨어있다. 결론: (c) off가 맞다. 단, --enable-evaluator를 켜야 하는 진입장벽을 낮추려면 "처음 실행 시 한 번 물어보는" UX 정도면 충분하다. 완벽한 기본값보다 쓰기 쉬운 opt-in이 실제 사용률을 높인다.

[Agent-B / Skeptic]
"쓰기 쉬운 opt-in이 사용률을 높인다"는 근거 없는 낙관이다. 기본값 연구 전반에서 opt-in은 대부분 무시된다 — 사용자가 시스템을 신뢰하면 기본값을 바꾸지 않는다. 의미적 결함이 조용히 머지되면 lead도 문제를 인식 못 하니 --enable-evaluator를 켤 이유 자체를 모른다.

selective의 메타 비용 반론도 과장됐다. "high-risk 판단"이 반드시 별도 LLM 호출일 필요 없다 — 파일 유형, 태스크 키워드, 수정 범위 같은 규칙 기반 휴리스틱으로 구현 가능하다. Agent-A는 가장 비싼 구현을 가정해 selective를 기각했다.

rate limit 주장은 맞다. 하지만 그건 always-on을 기본값으로 쓰되 rate limit 근접 시 AdversarialVerifier를 자동 스킵하는 안전장치로 해결하면 되지, 기본값 자체를 off로 내리는 근거는 아니다.

[Agent-C / Architect]
동의 일부, 그러나 (b)로 가야 한다. *왜*: 기본값은 한 번 정하면 시스템의 정체성이 되니, "신뢰의 출처"를 어디 둘지가 핵심이다 — always-on은 검증을 시스템 속성으로, off는 사용자 책임으로 떠넘긴다.

Agent-B의 휴리스틱 selective가 옳다. 단 구현 원칙은 명확해야 한다: 규칙은 lead 코드에 박는 것이 아니라 멤버 산출물의 메타데이터(파일 변경 범위, 태스크 카테고리, Verifier 통과 신뢰도)에서 자동 도출돼야 한다 — 그래야 lead 프롬프트가 비대해지지 않고, 휴리스틱 자체를 나중에 교체 가능하다.

Agent-A의 rate limit 우려는 selective에서 자연히 해소된다. always-on의 "근접 시 자동 스킵"은 가장 검증이 필요한 큰 작업에서 검증이 사라지는 역설을 만든다. 휴리스틱 선별은 그 역설을 피한다.

장기적으로 AdversarialVerifier 호출 로그가 쌓이면 휴리스틱을 데이터로 보정할 수 있다 — 이건 always-on이나 off에서는 얻기 힘든 학습 신호다.

[Agent-D / Outsider]
selective가 제일 위험한 지점은 “누가 high-risk를 판정하느냐”가 아니라, 그 판정 기준이 성능 저하를 은근히 숨기는 쪽으로 drift한다는 점이다. 휴리스틱은 메타 복잡도를 줄이는 대신, 결국 검증이 필요한 경계 사례를 제일 먼저 놓치기 쉽고, 그 실패는 로그가 쌓여도 사후에만 보인다. 그래서 기본값은 off를 유지하고, 대신 특정 태스크 타입이나 변경량 같은 명시적 조건에서만 강제 활성화하는 하드 게이트가 더 낫다. 나는 이건 단순함 문제가 아니라, 시스템이 조용히 잘못되는 걸 막는 책임 분리 문제라고 본다.

---

## Summary
# 토론 결과 요약

## 옵션별 지지도

| 옵션 | 지지자 | 핵심 근거 |
|------|--------|---------|
| **(a) always-on** | 없음 | rate limit 대비 처리량 반감, 장기적 확장성 악화 |
| **(b) selective (휴리스틱)** | Skeptic (R2), Architect (R2) | 메타데이터 기반 자동 선별 → 메타 비용 절감 + 학습 신호 수집 |
| **(c) off (opt-in)** | Pragmatist, Outsider, Architect (R1) | 의도하지 않은 비용 강제 금지, 명시성 원칙 |

---

## 각 입장의 Trade-offs

### (c) off 기본값
**지지**: Pragmatist, Outsider (R1 Architect도)  
**근거**:
- ✅ Rate limit 남은 예산을 실제 작업에 쓸 수 있음
- ✅ 의미 없는 산출물에 검증 비용 없음  
- ✅ Opt-in 플래그로 기본값을 바꾸려는 의도를 명시적으로 드러냄

**약점** (Skeptic, Architect 지적):
- ❌ opt-in은 현실에서 무시됨 (기본값의 힘)
- ❌ Known gap (의미적 결함 미감지) → "영구히 무시하겠다"는 선택
- ❌ Lead가 AdversarialVerifier 필요성을 인식 못 하면 활성화 자체 안 함

---

### (b) selective (휴리스틱 기반)
**지지**: Skeptic (R2), Architect (R2)  
**근거**:
- ✅ Rule 기반 휴리스틱 (파일 타입, 태스크 키워드, 변경 범위) → 별도 LLM 호출 불필요
- ✅ 메타데이터에서 자동 도출 → lead 코드 비대 방지
- ✅ 호출 로그 축적 → 휴리스틱 보정의 학습 신호 수집 가능
- ✅ High-risk에 집중 → rate limit 상황에서 효율

**약점** (Pragmatist, Outsider 지적):
- ❌ "고위험 판단"이 틀릴 수 있음 → 경계 사례를 먼저 놓치는 drift
- ❌ 규칙의 점진적 이완 (성능 저하 은폐 위험)
- ❌ Outsider: rate limit 근접 시 검증이 필요한 큰 작업부터 스킵되는 역설 가능

---

### (a) always-on
**지지자**: 없음 (모두 비용 문제로 배제)  
**배제 근거** (전 합의):
- Rate limit이 희소 자원 → 모든 산출물에 균등 소모 = 처리량 악화
- Trivial 산출물에도 3-페르소나 사이클 강제 → 실제 필요 작업에서 throttle

---

## 합의된 부분

1. **Weekly rate limit이 진짜 병목** — "비용 ~2배"는 추상적, 실제는 처리량 차단
2. **기본값은 정체성을 정함** — 한 번 정하면 사용자가 거의 안 바꿈
3. **All options involve trade-offs** — 완벽한 기본값은 없음, 우선순위 선택

---

## 미해결 쟁점

| 쟁점 | 입장 A (off) | 입장 B (selective) |
|------|-------------|------------------|
| **High-risk 판정의 정확도** | 판정 자체가 위험 → hard gate 선호 | 휴리스틱은 사후 보정 가능 → 점진 개선 가능 |
| **Drift 위험** | 규칙은 점차 이완됨 | 호출 로그로 감지·교정 가능 |
| **Rate limit 근처 동작** | 미정 (off 입장에는 무관) | Selective에서도 "무엇을 먼저 skip?" 필요 |
| **Opt-in 실제 사용률** | Pragmatist는 UX 개선으로 해결 가능 | 근거 없는 낙관 (Skeptic 비판) |
| **학습 신호 수집** | 불가능 (기본 off) | 가능 (호출 로그) |

---

## 운영자 주의사항

🚩 **Belief entrenchment 가능성**:
- Pragmatist, Outsider 같은 off 입장이 "rate limit = 종국의 근거" 반복 (이미 동의됨)
- Architect가 초반 (c) → R2 (b)로 전환했지만, **전환 근거가 "메타데이터라면 괜찮다"는 조건부** → 실제 구현 복잡도 미검증
- **핵심 미충돌**: 휴리스틱 drift 측정법이 없는 상태에서 selective를 "데이터로 보정 가능"이라고 가정

---

## 필요한 실증 데이터

다음 중 하나라도 확보되면 선택지 좁혀짐:
1. **AdversarialVerifier의 실제 catch rate** — 객관 Verifier 대비 의미적 결함 포착 비율
2. **High-risk 휴리스틱의 정밀도** — precision/recall (false negative, false positive 비율)
3. **Rate limit 실제 도달 빈도** — weekly budget 소진 시나리오

## 최종 결정
**결정**: (c) off를 기본값으로 유지하되, 단순 플래그가 아니라 "lead가 task spawn 시점에 멤버별로 `verify=true` 인자를 명시적으로 전달"하는 per-hire 옵션 형태로 격상한다. 전역 `--enable-evaluator`는 폐기 또는 디버그용으로만 남긴다.

**근거**: Architect의 R1 원칙("검증은 객관성이 보장되는 층에서만 자동화한다")과 Outsider의 "조용히 잘못되는 걸 막는 책임 분리" 논점을 채택했다. Selective(b)는 Architect R2의 메타데이터 휴리스틱 아이디어가 매력적이지만, Outsider가 지적한 drift 측정법 부재와 요약의 "휴리스틱 정밀도" 미실증 때문에 지금 시스템 정체성으로 박기엔 이르다. Always-on(a)은 weekly rate limit 병목에서 전 합의로 배제. Skeptic의 "opt-in은 무시된다" 우려는 전역 플래그가 아니라 lead가 hire 시점에 멤버별로 결정하게 만들어, lead 프롬프트에 "고위험 산출물(코드 생성·보안·외부 영향)은 `verify=true`로 hire하라"는 책임을 명시함으로써 부분 해소한다 — 이는 휴리스틱을 lead의 판단(이미 LLM 호출 안에 있음)으로 흡수해 추가 메타 호출 0이다. Skeptic이 요구한 empirical 근거는 후속 작업으로 분리.

**즉시 실행**: TBD (lead의 hire API 시그니처와 프롬프트 두 곳을 동시에 손대야 하므로 단일 한 줄 변경 아님 — 후속 작업 참조)

**후속 작업**:
- lead의 hire/spawn API에 `verify: bool = False` 인자 추가, true일 때만 AdversarialVerifier가 해당 멤버 산출물에 critique-refine 1 cycle 실행
- lead 시스템 프롬프트에 verify=true를 켜야 하는 조건 명시 (코드 생성, 보안 관련, 외부 영향, 되돌리기 어려운 작업)
- 전역 `--enable-evaluator` 플래그는 "모든 hire에 verify=true 강제"하는 디버그 옵션으로 의미 축소 (제거하지 않음 — 데이터 수집용)
- AdversarialVerifier 호출 시 입력/출력/critique 결과를 로그로 남겨, 3개월 후 (1) 실제 catch rate (2) lead의 verify=true 판단 정밀도 데이터 확보 → 그 데이터로 selective(b) 자동화 또는 always-on 승격 재검토
- 로그 기반으로 "객관 Verifier 규칙으로 승격 가능한 패턴"(Architect R1) 정기 리뷰 — AdversarialVerifier는 영구 의존이 아니라 객관 규칙 발굴 채널로 위치 지정
