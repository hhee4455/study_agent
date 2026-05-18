"""Tests: StaticFiles mount — no-static-dir and with-static-dir cases."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient


def test_no_static_dir_health_ok() -> None:
    """When web/static/ does not exist, the server starts and /health returns 200."""
    from web.server import app

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_static_index_served(tmp_path: Path) -> None:
    """When a static dir with index.html exists, GET / returns 200 with its content."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<html><body>Agent Dashboard</body></html>", encoding="utf-8"
    )

    fresh_app = FastAPI(title="test")

    @fresh_app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    if static_dir.exists() and static_dir.is_dir():
        fresh_app.mount(
            "/", StaticFiles(directory=str(static_dir), html=True), name="static"
        )

    client = TestClient(fresh_app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Agent Dashboard" in r.text
