"""Tests: /health endpoint and SKIP_DIRS frontend-artifact coverage."""

from fastapi.testclient import TestClient

from lead.workspace import SKIP_DIRS
from web.server import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_skip_dirs_covers_frontend_node_modules() -> None:
    assert any("web/frontend/node_modules" in s for s in SKIP_DIRS)


def test_skip_dirs_covers_frontend_dist() -> None:
    assert any("web/frontend/dist" in s for s in SKIP_DIRS)
