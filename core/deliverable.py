"""Deliverable predelivery sanity — brief 의 Deliverables 목록이 cwd 안에
실재(size>0) 하는지 검사.

호출자: lead/member.py `_predelivery_sanity_check` ([STATUS:DONE] 직후, evaluator
호출 전). 위반 발견 시 mailbox 로 피드백 후 evaluator 를 건너뛴다.

분류:
  - "outside_cwd": 절대경로이거나 상위 이동 등 cwd 외부로 해석되는 경로
  - "missing":     cwd 안 경로이지만 파일이 없거나 크기 0
"""

from __future__ import annotations

from pathlib import Path

from core.path_guard import is_within_cwd


def missing_or_outside(paths: list[Path], cwd: Path) -> list[tuple[Path, str]]:
    """`paths` 중 위반만 (입력 path, 사유) 형태로 반환. 정상 항목은 결과에서 빠진다."""
    issues: list[tuple[Path, str]] = []
    for p in paths:
        if not is_within_cwd(p, cwd):
            issues.append((p, "outside_cwd"))
            continue
        target = p if p.is_absolute() else (cwd / p)
        try:
            if not target.is_file() or target.stat().st_size == 0:
                issues.append((p, "missing"))
        except OSError:
            issues.append((p, "missing"))
    return issues
