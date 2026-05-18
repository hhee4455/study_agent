"""Tests: /api/plan endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.api.plan import get_ws_root
from web.server import app

_PLAN_MD = """\
# Plan

- [ ] G-001-setup: Set up project structure
- [x] G-002-db: Configure database (assigned: M001)
- [ ] G-003-api: Build API layer (assigned: M002)
"""


@pytest.fixture()
def ws_root(tmp_path: Path) -> Path:
    lead_dir = tmp_path / "state" / "lead"
    lead_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def client(ws_root: Path):
    app.dependency_overrides[get_ws_root] = lambda: ws_root
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_plan_200_with_goals(client: TestClient, ws_root: Path) -> None:
    (ws_root / "state" / "lead" / "plan.md").write_text(_PLAN_MD, encoding="utf-8")
    r = client.get("/api/plan")
    assert r.status_code == 200
    data = r.json()
    assert "goals" in data
    assert len(data["goals"]) == 3


def test_get_plan_missing_file_returns_empty(client: TestClient) -> None:
    r = client.get("/api/plan")
    assert r.status_code == 200
    assert r.json() == {"goals": []}


def test_get_plan_assigned_done_model_serialization(
    client: TestClient, ws_root: Path
) -> None:
    (ws_root / "state" / "lead" / "plan.md").write_text(_PLAN_MD, encoding="utf-8")
    r = client.get("/api/plan")
    goals = r.json()["goals"]

    g0 = goals[0]  # not done, not assigned
    assert g0["id"] == "G-001-setup"
    assert g0["title"] == "Set up project structure"
    assert g0["done"] is False
    assert g0["assigned"] is None
    assert g0["model"] is None

    g1 = goals[1]  # done, assigned M001
    assert g1["id"] == "G-002-db"
    assert g1["title"] == "Configure database"
    assert g1["done"] is True
    assert g1["assigned"] == "M001"
    assert g1["model"] is None

    g2 = goals[2]  # not done, assigned M002
    assert g2["id"] == "G-003-api"
    assert g2["done"] is False
    assert g2["assigned"] == "M002"
    assert g2["model"] is None
