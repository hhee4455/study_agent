<!--
사용처: team_lead._initial_plan
변수: {spec_name} {spec} {ws_main_tree}
-->
# SYSTEM
요구서를 sub-goal 리스트로 분해해 plan.md 작성. 각 goal 은 한 명이 격리 ws 에서 끝낼 단위.

# USER
# 요구서 ({spec_name})
```
{spec}
```

# 현재 ws/main 파일 트리 (기존 코드 재사용 우선)
```
{ws_main_tree}
```

# 분해 규칙
- 트리에 이미 있는 파일을 바꿔야 하면 *수정* goal (예: `G-NNN-sizing: 기존 src/policies/sizing.py 의 X 를 Y 로 교체`).
- 트리에 없는 새 기능만 *신규* goal.
- 같은 파일을 두 멤버가 동시에 건드릴 가능성 있으면 영역 분리 또는 직렬화.
- goal id: `G-NNN-소문자_제목`. 3-15개 사이.

# 출력 형식 (어기면 plan 파싱 실패 → 무진행 종료)
```
# Plan
- [ ] G-001-bootstrap: 한 줄 설명
- [ ] G-002-foo: ...
```

- 각 goal 한 줄, `[ ]` 미체크.
- `# Plan` 첫 줄 외 다른 헤더/서술 단락/요약 노트 금지.
- 범위 묶음 표기(`G-001~003`) 금지 — 한 줄씩 개별.
