"""Tests: /api/conflicts endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.api.conflicts import get_ws_root
from web.server import app

_FILE_A = "M001-20260518T083716Z.md"
_FILE_B = "M002-20260518T084031Z.md"
_CONTENT_A = "# Conflict M001\n\nAgent M001 has a conflict.\n"
_CONTENT_B = "# Conflict M002\n\nAgent M002 details here.\n"


@pytest.fixture()
def ws_root(tmp_path: Path) -> Path:
    conflicts_dir = tmp_path / "state" / "lead" / "conflicts"
    conflicts_dir.mkdir(parents=True)
    (conflicts_dir / _FILE_A).write_text(_CONTENT_A, encoding="utf-8")
    (conflicts_dir / _FILE_B).write_text(_CONTENT_B, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(ws_root: Path):
    app.dependency_overrides[get_ws_root] = lambda: ws_root
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── /api/conflicts (list) ─────────────────────────────────────────────────────


def test_list_conflicts_200(client: TestClient) -> None:
    r = client.get("/api/conflicts")
    assert r.status_code == 200
    data = r.json()
    assert "files" in data
    filenames = [f["filename"] for f in data["files"]]
    assert _FILE_A in filenames
    assert _FILE_B in filenames


def test_list_conflicts_has_metadata(client: TestClient) -> None:
    r = client.get("/api/conflicts")
    assert r.status_code == 200
    for item in r.json()["files"]:
        assert "filename" in item
        assert "size_bytes" in item
        assert "modified_at" in item


# ── /api/conflicts/{filename} (detail) ───────────────────────────────────────


def test_get_conflict_content_200(client: TestClient) -> None:
    r = client.get(f"/api/conflicts/{_FILE_A}")
    assert r.status_code == 200
    data = r.json()
    assert data["filename"] == _FILE_A
    assert "Conflict M001" in data["content"]


def test_get_conflict_content_full_text(client: TestClient) -> None:
    r = client.get(f"/api/conflicts/{_FILE_B}")
    assert r.status_code == 200
    assert r.json()["content"] == _CONTENT_B


def test_get_conflict_404(client: TestClient) -> None:
    r = client.get("/api/conflicts/nonexistent.md")
    assert r.status_code == 404


# ── Path traversal guards ─────────────────────────────────────────────────────


def test_path_traversal_dotdot_prefix(client: TestClient) -> None:
    # Filename starting with '..' is rejected before reaching the filesystem
    r = client.get("/api/conflicts/..secret.md")
    assert r.status_code == 400


def test_path_traversal_dotdot_middle(client: TestClient) -> None:
    r = client.get("/api/conflicts/foo..bar.md")
    assert r.status_code == 400


def test_path_traversal_backslash(client: TestClient) -> None:
    # %5C = backslash character
    r = client.get("/api/conflicts/foo%5Cbar.md")
    assert r.status_code == 400


def test_state_reader_path_escape(tmp_path: Path) -> None:
    """Secondary defense: state_reader raises ValueError on path escape."""
    from web.state_reader import read_conflict_content

    conflicts_dir = tmp_path / "state" / "lead" / "conflicts"
    conflicts_dir.mkdir(parents=True)
    (tmp_path / "state" / "lead" / "escape.md").write_text("secret", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        read_conflict_content(tmp_path, "../escape.md")


# ── Directory absent fallback ─────────────────────────────────────────────────


def test_list_conflicts_empty_when_dir_absent(tmp_path: Path) -> None:
    app.dependency_overrides[get_ws_root] = lambda: tmp_path
    c = TestClient(app)
    r = c.get("/api/conflicts")
    assert r.status_code == 200
    assert r.json()["files"] == []
    app.dependency_overrides.clear()
