"""팀장(lead)이 옵션으로 호출하는 특화 도구 에이전트.

- debate/   : 3-페르소나 토론 패널 (high-stakes 결정 게이트)
- audit/    : Adversarial evaluator (Anthropic 패턴, critique-refine 1 cycle)
- janitor/  : 코드 정리 (사용 안 하는 .py 식별 → .archive로 이동)
"""
