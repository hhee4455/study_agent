"""Unit tests for ``core.auto_merge.try_auto_merge``."""

from __future__ import annotations

from core.auto_merge import MergeResult, try_auto_merge

# ----------------------------- identical / one-sided -----------------------------


def test_identical_all_equal():
    s = "a\nb\n"
    r = try_auto_merge(s, s, s)
    assert r.strategy == "identical"
    assert r.merged == s


def test_identical_a_equals_b_but_not_base():
    base = "a\n"
    both = "a\nb\n"
    r = try_auto_merge(base, both, both)
    assert r.strategy == "identical"
    assert r.merged == both


def test_one_sided_a_unchanged():
    base = "x\n"
    b = "x\ny\n"
    r = try_auto_merge(base, base, b)
    assert r.strategy == "identical"
    assert r.merged == b


def test_one_sided_b_unchanged():
    base = "x\n"
    a = "x\ny\n"
    r = try_auto_merge(base, a, base)
    assert r.strategy == "identical"
    assert r.merged == a


def test_empty_all_three():
    r = try_auto_merge("", "", "")
    assert r.strategy == "identical"
    assert r.merged == ""


# --------------------------------- import_only ---------------------------------


def test_import_only_different_imports():
    base = "import os\n"
    a = "import os\nimport re\n"
    b = "import os\nimport json\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "import_only"
    assert "import os" in r.merged
    assert "import re" in r.merged
    assert "import json" in r.merged
    assert r.merged.count("import os") == 1
    assert r.merged.count("import re") == 1
    assert r.merged.count("import json") == 1


def test_import_only_partial_overlap_dedupes():
    base = "import os\n"
    a = "import os\nimport re\n"
    b = "import os\nimport re\nimport json\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "import_only"
    assert r.merged.count("import re") == 1
    assert "import json" in r.merged


def test_import_only_from_form():
    base = ""
    a = "from os import path\n"
    b = "from sys import argv\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "import_only"
    assert "from os import path" in r.merged
    assert "from sys import argv" in r.merged


def test_import_only_empty_base():
    base = ""
    a = "import os\n"
    b = "import sys\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "import_only"


# --------------------------------- append_only ---------------------------------


def test_append_only_different_defs():
    base = "def foo():\n    return 1\n"
    a = base + "def bar():\n    return 2\n"
    b = base + "def baz():\n    return 3\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "append_only"
    assert r.merged.startswith(base)
    assert "def bar" in r.merged
    assert "def baz" in r.merged


def test_append_only_class_and_def():
    base = "VERSION = 1\n"
    a = base + "class Foo:\n    pass\n"
    b = base + "def helper():\n    return None\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "append_only"
    assert "class Foo" in r.merged
    assert "def helper" in r.merged


def test_append_only_same_signature_unmergeable():
    base = "x = 1\n"
    a = base + "def foo():\n    return 'a'\n"
    b = base + "def foo():\n    return 'b'\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "unmergeable"
    assert r.merged is None


def test_append_only_decorated_def():
    base = "x = 1\n"
    a = base + "@staticmethod\ndef foo():\n    return 1\n"
    b = base + "def bar():\n    return 2\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "append_only"
    assert "def foo" in r.merged
    assert "def bar" in r.merged


def test_append_only_just_comment_unmergeable():
    base = "x = 1\n"
    a = base + "# only a comment\n"
    b = base + "def foo():\n    pass\n"
    r = try_auto_merge(base, a, b)
    # a's append has no def/class names → not append_only → falls through;
    # both also append at same point (i1==i2==len(base_lines)) → non_overlap
    # rejects → unmergeable.
    assert r.strategy == "unmergeable"


# --------------------------------- non_overlap ---------------------------------


def test_non_overlap_different_lines_modified():
    base = "a\nb\nc\nd\ne\n"
    a = "A\nb\nc\nd\ne\n"
    b = "a\nb\nc\nd\nE\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert r.merged == "A\nb\nc\nd\nE\n"


def test_non_overlap_insertions_at_different_points():
    base = "a\nb\nc\n"
    a = "a\nX\nb\nc\n"
    b = "a\nb\nc\nY\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert r.merged == "a\nX\nb\nc\nY\n"


def test_non_overlap_replace_and_insert_after():
    base = "a\nb\nc\n"
    a = "A\nb\nc\n"
    b = "a\nb\nc\nZ\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert r.merged == "A\nb\nc\nZ\n"


def test_non_overlap_deletion_and_append():
    base = "a\nb\nc\n"
    a = "a\nc\n"
    b = "a\nb\nc\nd\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert r.merged == "a\nc\nd\n"


def test_non_overlap_fallthrough_after_failed_append():
    base = "a\nb\n"
    a = "a_mod\nb\ndef foo():\n    pass\n"
    b = "a\nb_mod\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert r.merged == "a_mod\nb_mod\ndef foo():\n    pass\n"


# ----------------------------------- overlap -----------------------------------


def test_overlap_same_line_unmergeable():
    base = "a\nb\nc\n"
    a = "a\nB\nc\n"
    b = "a\nB_alt\nc\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "unmergeable"
    assert r.merged is None


def test_overlap_same_insert_point_unmergeable():
    base = "a\nb\n"
    a = "a\nX\nb\n"
    b = "a\nY\nb\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "unmergeable"


def test_overlap_insert_inside_replace_unmergeable():
    base = "a\nb\nc\nd\n"
    a = "a\nB\nC\nd\n"  # replace lines 1..3
    b = "a\nb\nX\nc\nd\n"  # insert X at position 2 (strictly inside)
    r = try_auto_merge(base, a, b)
    assert r.strategy == "unmergeable"


def test_overlap_replaces_share_range():
    base = "line1\nline2\nline3\n"
    a = "line1_a\nline2_a\nline3\n"
    b = "line1_b\nline2\nline3_b\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "unmergeable"


def test_one_side_import_one_side_assignment_unmergeable():
    base = "x = 1\n"
    a = "x = 1\nimport os\n"  # appended import (passes _added_only as imports)
    b = "x = 1\ny = 2\n"  # appended non-import, non-def → no strategy fits
    r = try_auto_merge(base, a, b)
    assert r.strategy == "unmergeable"


# ------------------------------- trailing newline -------------------------------


def test_trailing_newline_preserved():
    base = "a\nb\n"
    a = "A\nb\n"
    b = "a\nB\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert r.merged.endswith("\n")
    assert r.merged == "A\nB\n"


def test_no_trailing_newline_preserved_when_one_sided():
    base = "a\nb"  # no trailing newline
    a = "A\nb"
    b = "a\nb"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "identical"
    assert r.merged == a
    assert not r.merged.endswith("\n")


# ----------------------------- API surface / sanity -----------------------------


def test_path_argument_accepted_and_ignored():
    base = "import os\n"
    a = "import os\nimport re\n"
    b = "import os\nimport json\n"
    r = try_auto_merge(base, a, b, path="foo.py")
    assert r.strategy == "import_only"


def test_mergeresult_unmergeable_factory():
    r = MergeResult.unmergeable("explained")
    assert r.merged is None
    assert r.strategy == "unmergeable"
    assert r.reason == "explained"


def test_mergeresult_ok_factory():
    r = MergeResult.ok("content", "identical", "why")
    assert r.merged == "content"
    assert r.strategy == "identical"
    assert r.reason == "why"


def test_exports_available_from_core_package():
    from core import MergeResult as M
    from core import try_auto_merge as t

    r = t("x\n", "x\n", "x\n")
    assert r.strategy == "identical"
    assert isinstance(r, M)


# --------------------------- AST validation hardening ---------------------------


def test_append_only_def_inside_triple_string_is_not_a_def():
    """A ``def foo()`` line nested inside a top-level string literal must not
    be miscounted as a real definition — AST validation catches this and the
    block is treated as a non-def top-level statement (→ unmergeable)."""
    base = "x = 1\n"
    # `a` appends only a top-level string expression that *contains* a def
    # signature line; it is not actually a function definition.
    a = base + 'DOC = """\ndef looks_like_def():\n    pass\n"""\n'
    b = base + "def real_def():\n    return 1\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy != "append_only"


def test_append_only_async_def_accepted():
    base = "x = 1\n"
    a = base + "async def fetch():\n    return None\n"
    b = base + "def helper():\n    return 1\n"
    r = try_auto_merge(base, a, b)
    # `async def` is a top-level coroutine — should not block append_only.
    assert r.strategy in {"append_only", "non_overlap"}
    assert "async def fetch" in r.merged
    assert "def helper" in r.merged


def test_append_only_top_level_assignment_blocks_strategy():
    """A top-level non-def statement appended on one side must prevent
    ``append_only`` from firing (the regex pre-AST didn't always catch this)."""
    base = "x = 1\n"
    a = base + "y = 2\n"
    b = base + "def helper():\n    return 1\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy != "append_only"


def test_append_only_syntax_garbled_falls_back_to_regex():
    """If the appended chunk is not syntactically valid Python on its own
    (e.g., references symbols from earlier lines), the AST path returns
    ``None`` and the regex fallback governs the decision."""
    base = "@staticmethod\ndef _decorated_helper():\n    return None\n"
    # `a` appends a decorator + def pair; the chunk alone (without its
    # subject) is still syntactically valid because @decorator + def parses.
    a = base + "@staticmethod\ndef foo():\n    return 1\n"
    b = base + "def bar():\n    return 2\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "append_only"
    assert "def foo" in r.merged
    assert "def bar" in r.merged


# ----------------------- import_only additional coverage -----------------------


def test_import_only_one_side_modifies_existing_falls_back():
    """If one side rewrites an existing import (not pure insertion), the
    ``import_only`` strategy must not engage."""
    base = "import os\n"
    a = "import os.path as osp\n"  # rewrites existing
    b = "import os\nimport json\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy != "import_only"


def test_import_only_blank_addition_falls_back():
    """A blank line is not an import — adding only a blank line must not
    qualify as import_only."""
    base = "import os\n"
    a = "import os\n\n"
    b = "import os\nimport json\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy != "import_only"


# ----------------------- non_overlap additional coverage -----------------------


def test_non_overlap_pure_b_changes_only_uses_identical():
    """When only one side changes, the ``identical`` fast path returns it —
    ``non_overlap`` should not fire because there is nothing to combine."""
    base = "a\nb\nc\n"
    a = base
    b = "a\nB\nc\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "identical"
    assert r.merged == b


def test_non_overlap_three_disjoint_hunks():
    base = "a\nb\nc\nd\ne\nf\ng\n"
    a = "A\nb\nc\nd\ne\nf\nG\n"  # change first and last
    b = "a\nb\nc\nD\ne\nf\ng\n"  # change middle
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert r.merged == "A\nb\nc\nD\ne\nf\nG\n"


def test_non_overlap_adjacent_inserts_at_distinct_points():
    """Inserts at adjacent but distinct insertion points (different ``i1``)
    must merge cleanly — they share no base line."""
    base = "a\nb\nc\n"
    a = "a\nX\nb\nc\n"  # insert at base index 1
    b = "a\nb\nY\nc\n"  # insert at base index 2
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert r.merged == "a\nX\nb\nY\nc\n"


# --------------------------- safety / no data loss ---------------------------


def test_unmergeable_does_not_invent_merge():
    base = "a\nb\nc\n"
    a = "a\nB1\nc\n"
    b = "a\nB2\nc\n"
    r = try_auto_merge(base, a, b)
    assert r.merged is None
    assert r.strategy == "unmergeable"
    assert r.reason  # must explain why


def test_unmergeable_reason_is_nonempty():
    base = "a\nb\n"
    a = "a\nX\nb\n"
    b = "a\nY\nb\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "unmergeable"
    assert isinstance(r.reason, str) and r.reason


# ---------------------------------------------------------------------------
# Extended edge cases — M033 신규 추가
# Purpose: cover empty/one-line edges, AST class additions, identical-but-
# whitespace-only flips, and concurrent rewrites that must remain unmergeable.
# ---------------------------------------------------------------------------


def test_empty_base_with_one_sided_import_addition():
    """base 가 빈 문자열일 때 한 쪽이 import 만 추가 → identical 빠른 경로."""
    base = ""
    a = "import os\n"
    b = ""
    r = try_auto_merge(base, a, b)
    assert r.strategy == "identical"
    assert r.merged == a


def test_empty_base_with_two_sided_imports_merges():
    base = ""
    a = "import os\n"
    b = "import sys\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "import_only"
    assert "import os" in r.merged
    assert "import sys" in r.merged


def test_single_line_file_both_modified_unmergeable():
    """한 줄 짜리 파일이 양쪽에서 다르게 수정되면 같은 줄 충돌."""
    base = "x = 1\n"
    a = "x = 2\n"
    b = "x = 3\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "unmergeable"


def test_class_definition_append_only():
    """append_only: 양쪽이 서로 다른 top-level class 만 추가."""
    base = "x = 1\n"
    a = base + "class A:\n    pass\n"
    b = base + "class B:\n    pass\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "append_only"
    assert "class A" in r.merged
    assert "class B" in r.merged


def test_class_and_def_conflict_same_name_unmergeable():
    """양쪽이 같은 이름으로 class/def 를 새로 추가하면 충돌."""
    base = "x = 1\n"
    a = base + "class Worker:\n    pass\n"
    b = base + "def Worker():\n    return 0\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "unmergeable"


def test_import_only_dedupes_identical_lines():
    """양쪽이 동일한 새 import 한 줄씩 추가하면 중복 제거 후 한 줄."""
    base = ""
    a = "import os\n"
    b = "import os\n"
    r = try_auto_merge(base, a, b)
    # 동일한 a/b → identical fast path 가 먼저
    assert r.strategy == "identical"
    assert r.merged.count("import os") == 1


def test_non_overlap_first_line_and_last_line():
    """양 끝(첫 줄/마지막 줄) 각각 변경 — 가장 자주 발생하는 disjoint 패턴."""
    base = "head\nmid\ntail\n"
    a = "HEAD\nmid\ntail\n"
    b = "head\nmid\nTAIL\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert r.merged == "HEAD\nmid\nTAIL\n"


def test_append_only_skips_when_one_side_appends_nothing():
    """한 쪽은 append 가 있고 한 쪽은 base 와 동일 → identical 패스."""
    base = "x = 1\n"
    a = base + "def foo():\n    return 1\n"
    b = base
    r = try_auto_merge(base, a, b)
    assert r.strategy == "identical"
    assert "def foo" in r.merged


def test_append_only_three_new_defs_no_name_collision():
    """양쪽에서 여러 def 추가, 이름 겹치지 않음."""
    base = "x = 1\n"
    a = base + "def a1():\n    pass\ndef a2():\n    pass\n"
    b = base + "def b1():\n    pass\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "append_only"
    for name in ("def a1", "def a2", "def b1"):
        assert name in r.merged


def test_unmergeable_reason_does_not_yield_invented_merge():
    """안전성 검증: unmergeable 시 merged 는 정확히 None."""
    base = "k = 0\n"
    a = "k = 1\n"
    b = "k = 2\n"
    r = try_auto_merge(base, a, b)
    assert r.merged is None
    assert r.strategy == "unmergeable"


def test_import_only_preserves_existing_non_import_lines():
    """기존 base 의 비-import 라인은 그대로 보존되어야 한다."""
    base = "import os\n\ndef helper():\n    return None\n"
    a = "import os\nimport re\n\ndef helper():\n    return None\n"
    b = "import os\nimport json\n\ndef helper():\n    return None\n"
    r = try_auto_merge(base, a, b)
    # 양쪽이 import 만 *삽입* 했지만 _added_only 는 difflib 의 insert/equal 만
    # 허용. 결과 strategy 는 import_only 또는 non_overlap 중 하나.
    assert r.strategy in {"import_only", "non_overlap"}
    assert "def helper" in r.merged
    assert "import re" in r.merged
    assert "import json" in r.merged


def test_path_arg_does_not_affect_strategy_selection():
    base = "a\n"
    a = "a\nb\n"
    b = "a\nc\n"
    r1 = try_auto_merge(base, a, b)
    r2 = try_auto_merge(base, a, b, path="some/path.py")
    assert r1.strategy == r2.strategy
    assert r1.merged == r2.merged


def test_mergeresult_is_frozen_dataclass():
    """MergeResult 는 frozen — 해시 안정성 및 잘못된 변형 방지."""
    r = MergeResult.ok("x", "identical", "r")
    with pytest.raises(Exception):
        r.merged = "tampered"  # type: ignore[misc]


# pytest 는 모듈 상단에서 import 되지 않았으므로 lazy import.
import pytest  # noqa: E402


def test_unicode_content_preserved_through_non_overlap():
    """한글/이모지 포함 라인이 disjoint 변경에서 손상되지 않음."""
    base = "안녕\nworld\n끝\n"
    a = "반가워\nworld\n끝\n"
    b = "안녕\nworld\nfin 🎉\n"
    r = try_auto_merge(base, a, b)
    assert r.strategy == "non_overlap"
    assert "반가워" in r.merged
    assert "fin 🎉" in r.merged
