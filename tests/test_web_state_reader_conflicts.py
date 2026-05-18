"""Unit tests for iter_conflict_files() and read_conflict_content() in state_reader."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from web.state_reader import iter_conflict_files, read_conflict_content


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_conflicts_dir(state_root: Path) -> Path:
    d = state_root / "lead" / "conflicts"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── iter_conflict_files ───────────────────────────────────────────────────────


def test_iter_returns_both_files(tmp_path: Path) -> None:
    d = _make_conflicts_dir(tmp_path)
    f1 = d / "old.md"
    f2 = d / "new.md"
    f1.write_text("old content", encoding="utf-8")
    f2.write_text("new content", encoding="utf-8")
    # set distinct mtimes: f1 older than f2
    t_old = time.time() - 10
    t_new = time.time()
    os.utime(f1, (t_old, t_old))
    os.utime(f2, (t_new, t_new))

    result = iter_conflict_files(tmp_path)

    names = [r["name"] for r in result]
    assert "old.md" in names
    assert "new.md" in names
    # mtime desc → new.md first
    assert names[0] == "new.md"
    assert names[1] == "old.md"


def test_iter_returns_required_keys(tmp_path: Path) -> None:
    d = _make_conflicts_dir(tmp_path)
    (d / "a.md").write_text("x", encoding="utf-8")

    result = iter_conflict_files(tmp_path)

    assert len(result) == 1
    item = result[0]
    assert "name" in item
    assert "path" in item
    assert "size" in item
    assert "mtime" in item
    assert item["name"] == "a.md"
    assert item["path"] == "lead/conflicts/a.md"
    assert isinstance(item["size"], int)
    assert isinstance(item["mtime"], float)


def test_iter_excludes_non_md_files(tmp_path: Path) -> None:
    d = _make_conflicts_dir(tmp_path)
    (d / "keep.md").write_text("ok", encoding="utf-8")
    (d / "skip.txt").write_text("ignored", encoding="utf-8")

    result = iter_conflict_files(tmp_path)

    assert len(result) == 1
    assert result[0]["name"] == "keep.md"


def test_iter_missing_dir_returns_empty(tmp_path: Path) -> None:
    # no lead/conflicts/ created
    result = iter_conflict_files(tmp_path)
    assert result == []


def test_iter_ttl_cache_hit(tmp_path: Path) -> None:
    d = _make_conflicts_dir(tmp_path)
    (d / "c.md").write_text("cached", encoding="utf-8")

    first = iter_conflict_files(tmp_path)
    # add a second file — should NOT appear while cache is warm
    (d / "d.md").write_text("late", encoding="utf-8")
    second = iter_conflict_files(tmp_path)

    assert first == second


# ── read_conflict_content ─────────────────────────────────────────────────────


def test_read_returns_content(tmp_path: Path) -> None:
    d = _make_conflicts_dir(tmp_path)
    (d / "issue.md").write_text("hello conflict", encoding="utf-8")

    content = read_conflict_content("issue.md", tmp_path)

    assert content == "hello conflict"


def test_read_utf8_errors_replaced(tmp_path: Path) -> None:
    d = _make_conflicts_dir(tmp_path)
    (d / "bad.md").write_bytes(b"ok \xff end")

    content = read_conflict_content("bad.md", tmp_path)

    assert content is not None
    assert "ok" in content
    assert "end" in content


def test_read_missing_file_returns_none(tmp_path: Path) -> None:
    _make_conflicts_dir(tmp_path)
    assert read_conflict_content("ghost.md", tmp_path) is None


def test_read_non_md_returns_none(tmp_path: Path) -> None:
    d = _make_conflicts_dir(tmp_path)
    (d / "foo.txt").write_text("ignored", encoding="utf-8")
    assert read_conflict_content("foo.txt", tmp_path) is None


# ── path-traversal guards ─────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_name", [
    "../etc/passwd",
    "../../secret.md",
    "/abs/path",
    "/abs.md",
    "sub/dir.md",
])
def test_read_path_traversal_returns_none(tmp_path: Path, bad_name: str) -> None:
    _make_conflicts_dir(tmp_path)
    assert read_conflict_content(bad_name, tmp_path) is None
