"""프롬프트 외부화.

각 .md 파일에 `# SYSTEM` 과 `# USER` h1 섹션이 있으면 (system, user) 튜플로 분리한다.
섹션이 없는 단순 템플릿은 한 문자열로 반환 (예: driver.md).
HTML 주석(`<!-- ... -->`)으로 시작하는 메타 블록은 자동 제거 (사용처/변수 문서용).
"""
from __future__ import annotations

import re
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent
_LEADING_COMMENT_RE = re.compile(r"\A\s*<!--.*?-->\s*\n?", re.DOTALL)
_SECTION_RE = re.compile(r"^# (SYSTEM|USER)\s*$", re.MULTILINE)


def load(name: str) -> str:
    """`lead/prompts/<name>.md` 를 텍스트로 로드. 메타 코멘트는 제거."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found: {path}")
    text = path.read_text(encoding="utf-8")
    return _LEADING_COMMENT_RE.sub("", text)


def split(name: str) -> tuple[str, str]:
    """`# SYSTEM` / `# USER` 섹션 분리. 없으면 빈 system + 전체 user."""
    text = load(name)
    parts: list[tuple[str, int, int]] = []
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return "", text.strip()
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append((m.group(1), start, end))
    sections = {label: text[s:e].strip() for label, s, e in parts}
    return sections.get("SYSTEM", "").strip(), sections.get("USER", "").strip()


def render(name: str, **vars) -> str:
    """단일 템플릿 (섹션 없음) 변수 치환 + 반환."""
    return load(name).format(**vars)


def render_split(name: str, **vars) -> tuple[str, str]:
    """SYSTEM/USER 분리 + 각각 변수 치환."""
    system, user = split(name)
    return system.format(**vars), user.format(**vars)
