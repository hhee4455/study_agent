"""3-way line-based auto-merge.

``try_auto_merge(base, a, b)`` tries safe, deterministic merges between two
divergent versions ``a`` and ``b`` of a common ``base`` using line-level
heuristics only (``difflib`` + regex). When no strategy applies with
confidence, the result is ``unmergeable`` — never a guessed merge.

Strategies, tried in order:

1. ``identical``     — ``a == b`` or one side equals ``base``.
2. ``import_only``   — both sides only insert ``import``/``from`` lines.
3. ``append_only``   — both sides only append top-level ``def``/``class``
   blocks whose names do not collide.
4. ``non_overlap``   — base-relative diff hunks from ``a`` and ``b`` cover
   mutually disjoint regions of ``base``.

Anything else is ``unmergeable``.
"""

from __future__ import annotations

import ast
import difflib
import logging
import re
from dataclasses import dataclass
from typing import Literal

Strategy = Literal[
    "identical",
    "import_only",
    "append_only",
    "non_overlap",
    "unmergeable",
]

_IMPORT_RE = re.compile(r"^(import |from )")
_DEF_RE = re.compile(r"^(def |class )([A-Za-z_][A-Za-z0-9_]*)")

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a 3-way auto-merge attempt."""

    merged: str | None
    strategy: Strategy
    reason: str

    @classmethod
    def ok(cls, merged: str, strategy: Strategy, reason: str = "") -> MergeResult:
        """Build a successful merge result."""
        return cls(merged=merged, strategy=strategy, reason=reason)

    @classmethod
    def unmergeable(cls, reason: str) -> MergeResult:
        """Build an explicit unmergeable sentinel result."""
        return cls(merged=None, strategy="unmergeable", reason=reason)


def try_auto_merge(
    base: str,
    a: str,
    b: str,
    *,
    path: str | None = None,
) -> MergeResult:
    """Try to safely 3-way merge ``a`` and ``b`` against ``base``.

    ``path`` is accepted for forward compatibility (per-extension heuristics)
    but is currently unused.
    """
    del path

    if a == b:
        return MergeResult.ok(a, "identical", "a == b")
    if a == base:
        return MergeResult.ok(b, "identical", "a unchanged; b adopted")
    if b == base:
        return MergeResult.ok(a, "identical", "b unchanged; a adopted")

    base_lines = base.splitlines(keepends=True)
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)

    for strategy_fn in (_try_import_only, _try_append_only, _try_non_overlap):
        result = strategy_fn(base_lines, a_lines, b_lines)
        if result is not None:
            return result

    return MergeResult.unmergeable("no safe strategy matched")


def _extract_imports(lines: list[str]) -> list[str]:
    """Return the subset of ``lines`` that look like Python imports, in order."""
    return [line for line in lines if _IMPORT_RE.match(line)]


def _extract_top_level_defs(lines: list[str]) -> list[str] | None:
    """Names of top-level ``def``/``class`` declarations in ``lines``.

    Validates via :mod:`ast` when possible (a stray ``def ...`` inside a
    triple-quoted string must not be miscounted as a real definition); falls
    back to a regex scan that tolerates blank, comment, decorator and indented
    body lines. Returns ``None`` when any non-def/class top-level statement is
    found.
    """
    ast_names = _ast_top_level_defs("".join(lines))
    if ast_names is not None:
        return ast_names

    # AST 파싱 실패 시 사용 (regex fallback).
    _LOG.debug("AST parse failed in _extract_top_level_defs; using regex fallback")
    names: list[str] = []
    for line in lines:
        if line.startswith((" ", "\t")):
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("@"):
            continue
        m = _DEF_RE.match(line)
        if not m:
            return None
        names.append(m.group(2))
    return names


def _ast_top_level_defs(source: str) -> list[str] | None:
    """AST-validated names of top-level ``def``/``class`` nodes, or ``None``.

    Returns ``None`` when the source fails to parse (caller falls back to the
    regex scan) or when any non-def/class top-level statement exists.
    """
    if not source.strip():
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
            continue
        return None
    return names


def _added_only(base_lines: list[str], ver_lines: list[str]) -> list[str] | None:
    """If ``ver_lines`` differs from ``base_lines`` only by insertions, return
    the inserted lines concatenated in order; otherwise return ``None``.
    """
    sm = difflib.SequenceMatcher(a=base_lines, b=ver_lines, autojunk=False)
    added: list[str] = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            added.extend(ver_lines[j1:j2])
            continue
        return None
    return added


def _appended_at_end(base_lines: list[str], ver_lines: list[str]) -> list[str] | None:
    """Return lines appended verbatim after ``base_lines``, else ``None``.

    Strictly requires ``ver_lines[:len(base_lines)] == base_lines``; any
    mid-file change disqualifies the input.
    """
    n = len(base_lines)
    if len(ver_lines) < n:
        return None
    if ver_lines[:n] != base_lines:
        return None
    return ver_lines[n:]


def _ranges_overlap(ha: tuple[int, int], hb: tuple[int, int]) -> bool:
    """Whether two base-relative ``[i1, i2)`` hunk ranges conflict.

    Zero-width ranges represent insertions at a single point; they conflict
    with each other only at the same point and with ranged hunks only when
    strictly inside (not at the endpoints).
    """
    a1, a2 = ha
    b1, b2 = hb
    a_empty = a1 == a2
    b_empty = b1 == b2
    if a_empty and b_empty:
        return a1 == b1
    if a_empty:
        return b1 < a1 < b2
    if b_empty:
        return a1 < b1 < a2
    return a2 > b1 and b2 > a1


def _hunks_against_base(
    base_lines: list[str], ver_lines: list[str]
) -> list[tuple[int, int, list[str]]]:
    """Return non-equal diff hunks from ``base`` to ``ver`` as ``(i1, i2, new)``."""
    out: list[tuple[int, int, list[str]]] = []
    sm = difflib.SequenceMatcher(a=base_lines, b=ver_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        out.append((i1, i2, ver_lines[j1:j2]))
    return out


def _non_overlapping_hunks(
    base_lines: list[str],
    a_lines: list[str],
    b_lines: list[str],
) -> list[tuple[int, int, list[str]]] | None:
    """Combine non-equal hunks of ``a`` and ``b`` against ``base`` if they are
    mutually disjoint. Returns the merged, sorted hunk list, or ``None`` when
    any pair overlaps.
    """
    a_hunks = _hunks_against_base(base_lines, a_lines)
    b_hunks = _hunks_against_base(base_lines, b_lines)
    if not a_hunks or not b_hunks:
        return None

    for ha in a_hunks:
        for hb in b_hunks:
            if _ranges_overlap((ha[0], ha[1]), (hb[0], hb[1])):
                return None

    combined = a_hunks + b_hunks
    combined.sort(key=lambda h: (h[0], 0 if h[0] == h[1] else 1))
    return combined


def _try_import_only(
    base_lines: list[str],
    a_lines: list[str],
    b_lines: list[str],
) -> MergeResult | None:
    a_added = _added_only(base_lines, a_lines)
    b_added = _added_only(base_lines, b_lines)
    if a_added is None or b_added is None:
        return None
    if _extract_imports(a_added) != a_added:
        return None
    if _extract_imports(b_added) != b_added:
        return None

    result_lines = list(a_lines)
    existing = set(result_lines)

    insert_pos = 0
    for idx, line in enumerate(result_lines):
        if _IMPORT_RE.match(line):
            insert_pos = idx + 1

    for line in b_added:
        if line in existing:
            continue
        result_lines.insert(insert_pos, line)
        existing.add(line)
        insert_pos += 1

    return MergeResult.ok(
        "".join(result_lines),
        "import_only",
        f"merged {len(a_added)} + {len(b_added)} import lines",
    )


def _try_append_only(
    base_lines: list[str],
    a_lines: list[str],
    b_lines: list[str],
) -> MergeResult | None:
    a_app = _appended_at_end(base_lines, a_lines)
    b_app = _appended_at_end(base_lines, b_lines)
    if a_app is None or b_app is None:
        return None

    a_names = _extract_top_level_defs(a_app)
    b_names = _extract_top_level_defs(b_app)
    if a_names is None or b_names is None:
        return None
    if not a_names or not b_names:
        return None
    if set(a_names) & set(b_names):
        return None

    merged_lines = list(base_lines) + list(a_app) + list(b_app)
    return MergeResult.ok(
        "".join(merged_lines),
        "append_only",
        f"appended defs/classes: {a_names + b_names}",
    )


def _try_non_overlap(
    base_lines: list[str],
    a_lines: list[str],
    b_lines: list[str],
) -> MergeResult | None:
    hunks = _non_overlapping_hunks(base_lines, a_lines, b_lines)
    if hunks is None:
        return None

    out: list[str] = []
    cursor = 0
    for i1, i2, new_lines in hunks:
        if cursor > i1:
            return None
        out.extend(base_lines[cursor:i1])
        out.extend(new_lines)
        cursor = i2
    out.extend(base_lines[cursor:])
    return MergeResult.ok(
        "".join(out),
        "non_overlap",
        f"{len(hunks)} disjoint hunks applied",
    )
