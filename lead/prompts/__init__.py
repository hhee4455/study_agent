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


def render(name: str, **vars: object) -> str:
    """단일 템플릿 (섹션 없음) 변수 치환 + 반환."""
    return load(name).format(**vars)


def render_split(name: str, **vars: object) -> tuple[str, str]:
    """SYSTEM/USER 분리 + 각각 변수 치환."""
    system, user = split(name)
    return system.format(**vars), user.format(**vars)


# ---------- 모든 멤버에 공통으로 주입되는 베이스라인 행동 방침 ----------
# PreToolUse hook (path_guard) 가 OS 레벨에서 cwd 밖 fileop 을 차단하지만,
# 멤버 LLM 이 이를 미리 인지하면 reject 사이클을 줄일 수 있다.
PATH_GUARD_NOTICE = (
    "파일 작업 시 cwd 하위 경로만 허용 (PreToolUse hook 으로 강제) — "
    "cwd 밖 시도는 즉시 reject 된다."
)
QUESTION_KIND_HINT = (
    "의사결정 의문이 생기면 망설이지 말고 mailbox 에 `kind=question` 보내라. "
    "작은 trade-off (알고리즘/임계값/시그니처/의존성/에러 정책) 도 lead 가 "
    "4-way 토론으로 답한다. option A vs B + 너의 선호 + trade-off."
)


# ---------- refine 라벨용 write-guard closure ----------
# brief.kind == 'refine' 인 멤버에게 추가되는 행동 방침.
# - 시드 파일은 이미 ws 에 복사돼 있으므로 Write 가 아니라 Edit 으로 수정해야 함.
# - 시드 외 신규 파일을 만들기 전에는 lead 에 question 먼저.
# - allowed_tools 차원에서는 Write 를 빼지 않는다 (new/extend 호환). 프롬프트로만 강제.
REFINE_WRITE_GUARD = f"""\
## refine closure — Write 금지 / Edit 강제

이 작업은 **refine** 이다. 시드 파일은 이미 ws 에 복사되어 있다.

- **Write 도구 사용 금지** — 시드 파일은 반드시 **Edit** 으로 수정한다.
  (Write 로 덮어쓰면 머지 단계에서 다른 멤버 변경과 충돌이 폭증한다.)
- **seed_files 에 없는 새 파일을 만들지 마라.** 새 파일이 꼭 필요하면
  mailbox 에 `kind=question` 으로 lead 에게 먼저 묻고 승인 후 만든다.
- 시드의 디렉토리 구조를 그대로 유지하라 — 새 root prefix 추가 금지.
- {PATH_GUARD_NOTICE}
- {QUESTION_KIND_HINT}
"""


def build_refine_write_guard(seed_paths: list[str] | None = None) -> str:
    """refine brief 용 closure 문자열 빌더.

    seed_paths 가 주어지면 가드 말미에 bullet 로 노출 — 멤버가 어떤 파일을
    Edit 해야 하는지 즉시 알 수 있도록.
    """
    text = REFINE_WRITE_GUARD
    if seed_paths:
        bullets = "\n".join(f"- {p}" for p in seed_paths)
        text = f"{text}\n### 시드 파일 (Edit 대상)\n{bullets}\n"
    return text
