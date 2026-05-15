"""Janitor — 코드 정리.

미참조 .py 파일을 정적 분석 (AST + grep) 으로 식별 → `<ws>/.archive/<ts>/`로 이동.
자동 삭제 금지. 7일 grace + 진입점 보호.
"""

from agents.janitor.code_janitor import CodeJanitor

__all__ = ["CodeJanitor"]
