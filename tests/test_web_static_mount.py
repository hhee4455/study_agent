"""Tests: StaticFiles mount — no-static-dir and with-static-dir cases."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.server import _setup_static


def test_no_static_dir_friendly_503(tmp_path: Path) -> None:
    """When web/static/ is absent, GET / returns 503 with build instructions."""
    fresh_app = FastAPI()

    @fresh_app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    _setup_static(fresh_app, tmp_path / "static")  # static subdir does NOT exist

    client = TestClient(fresh_app)
    r = client.get("/")
    assert r.status_code == 503
    assert "scripts/web-venv.sh build" in r.text


def test_static_index_served(tmp_path: Path) -> None:
    """When a static dir with index.html exists, GET / returns 200 with its content."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "assets").mkdir()
    (static_dir / "index.html").write_text(
        "<html><body>Agent Dashboard</body></html>", encoding="utf-8"
    )

    fresh_app = FastAPI()
    _setup_static(fresh_app, static_dir)

    client = TestClient(fresh_app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Agent Dashboard" in r.text
