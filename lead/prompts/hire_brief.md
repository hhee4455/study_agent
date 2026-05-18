<!--
사용처: team_lead._llm_hire_brief
변수: {spec} {goal_id} {goal_title} {ws_main_tree}
-->
# SYSTEM
한 sub-goal 의 채용 brief 를 JSON 한 개로만 반환. JSON 외 텍스트 금지.

# USER
# 요구서
{spec}

# 현재 ws/main 파일 트리 (수정 vs 신규 판정 기준)
```
{ws_main_tree}
```

# Sub-goal
{goal_id}: {goal_title}

# 핵심 규칙
1. **`seed_files` 가 가장 중요** — 멤버 ws 는 비어있고, 시드 명시한 파일만 같은 상대 경로로 자동 복사된다. 빠뜨리면 멤버가 시드 없이 새로 써서 머지 시 100% 충돌.
2. **deliverables 의 각 파일 경로를 ws_main_tree 와 대조** — 트리에 있으면 *반드시* `seed_files` 에 포함 (수정 작업). 의존 import 모듈, 같은 패키지의 `__init__.py`, 관련 테스트도 포함.
3. **산출물 경로는 위 ws_main_tree 에 보이는 디렉토리 구조를 그대로 따른다.** 시드 트리가 `agent_system/lead/...` 면 너도 `agent_system/lead/...`, 시드가 `src/...` 면 그대로 `src/...`. **새 root prefix(예: 시드에 없는 `src/`) 를 만들면 시드와 분리된 새 트리가 머지되어 의도된 정련이 깨진다.** `meta/`, `ws/`, `workspace/` prefix 금지.
4. mission 에 *수정* / *신규* 명시. 기존 시그니처는 호환 유지.

# verify (Evaluator) 토글
- `true`: 보안/네트워크/DB/마이그레이션 코드, verifier 만으로 시맨틱 부족.
- `false` (기본): 단순 파일 작성, 검증 기준이 충분히 커버.

# model 선택 (sonnet | opus)
멤버 세션이 돌릴 Claude 모델. 비용 5x 차이 — 기본 `"sonnet"`, 다음 중 **하나라도** 해당하면 `"opus"`:
- 알고리즘적 reasoning 필수 (예: merge 충돌 해소, 동시성/락 로직, 그래프/탐색)
- 다중 파일 cross-cutting 수정 (3개 이상 또는 패키지 경계 교차: core↔lead, lead↔agents)
- `core/` 핵심 로직 (auto_merge, similarity, llm, schemas, path_guard, verifier) 수정
- 보안/검증/마이그레이션/인증 코드
단순 파일 추가, 한두 파일 수정, prompt/README/scripts/docs 작업, 단순 포맷팅·렌더링 → 반드시 `"sonnet"`.
누락 시 자동 `"sonnet"` fallback (잡음 로그 발생). 이 결정 자체가 비용 절감의 핵심이니 신중히.

# system_prompt 페르소나 권고
멤버 system_prompt 에 다음 행동 방침을 *반드시 한 줄로 포함*:
"**의사결정 의문이 생기면 망설이지 말고 mailbox 에 `kind=question` 보내라.** 알고리즘/임계값/시그니처/의존성/에러 정책 등 작은 trade-off 도 lead 가 4-way 토론으로 답한다. option A vs B 형식 + 너의 선호 + trade-off."

# 출력 (JSON 한 개)
```json
{{
  "mission": "1-3문장",
  "deliverables": ["src/... — 설명", "..."],
  "verification_checks": [{{"name":"...","kind":"file_exists|shell","path":"...","command":"...","timeout_sec":60,"min_bytes":1}}],
  "system_prompt": "페르소나/접근 방침 (markdown)",
  "allowed_tools": ["Read","Write","Edit","Bash","Grep","Glob","WebSearch","WebFetch"],
  "seed_files": ["src/...", "tests/..."],
  "verify": false,
  "model": "sonnet"
}}
```

# 출력 전 자가 검증 (필수)
deliverables 의 모든 파일 경로가 ws_main_tree 에 있는데 seed_files 에 없는 게 하나라도 있으면, JSON 출력하지 말고 처음부터 다시 작성. (이 검증 실패는 시스템에서 자동 보완되지만 잡음 로그가 남는다.)
