"""Tests: /api/members endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.api.members import get_ws_root
from web.server import app

_AGENT_ID = "m-001"

_AGENTS_JSON = {
    _AGENT_ID: {
        "status": "DONE",
        "goal_id": "G-001-test",
        "last_msg_id": 1,
        "hired_at": "2026-05-18T00:00:00Z",
        "completed_at": "2026-05-18T01:00:00Z",
        "last_resume": 0,
        "last_error": "",
        "cost_usd": 0.5,
        "last_session_id": "sess-abc",
        "model": "opus",
    }
}

_MAILBOX_MD = (
    "<!-- MSG id=1 from=lead to=m-001 kind=instruction ts=2026-05-18T00:00:00Z -->\n"
    "Hello agent\n"
    "<!-- /MSG -->\n"
)


@pytest.fixture()
def ws_root(tmp_path: Path) -> Path:
    lead_dir = tmp_path / "state" / "lead"
    lead_dir.mkdir(parents=True)
    agent_dir = tmp_path / "state" / "agents" / _AGENT_ID
    agent_dir.mkdir(parents=True)

    (lead_dir / "agents.json").write_text(
        json.dumps(_AGENTS_JSON, indent=2), encoding="utf-8"
    )
    (agent_dir / "brief.md").write_text("# Brief\nTest brief content.", encoding="utf-8")
    (agent_dir / "mailbox.md").write_text(_MAILBOX_MD, encoding="utf-8")
    (agent_dir / "delivery.md").write_text("# Delivery\nDone.", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(ws_root: Path):
    app.dependency_overrides[get_ws_root] = lambda: ws_root
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── /api/members ──────────────────────────────────────────────────────────────


def test_list_members_200(client: TestClient) -> None:
    r = client.get("/api/members")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["agent_id"] == _AGENT_ID


def test_get_member_200(client: TestClient) -> None:
    r = client.get(f"/api/members/{_AGENT_ID}")
    assert r.status_code == 200
    assert r.json()["agent_id"] == _AGENT_ID
    assert r.json()["status"] == "DONE"


def test_get_member_404(client: TestClient) -> None:
    r = client.get("/api/members/nonexistent")
    assert r.status_code == 404


# ── /api/members/{id}/brief ───────────────────────────────────────────────────


def test_get_brief_200(client: TestClient) -> None:
    r = client.get(f"/api/members/{_AGENT_ID}/brief")
    assert r.status_code == 200
    data = r.json()
    assert "path" in data
    assert "content" in data
    assert "Brief" in data["content"]


def test_get_brief_404_unknown_agent(client: TestClient) -> None:
    r = client.get("/api/members/nonexistent/brief")
    assert r.status_code == 404


# ── /api/members/{id}/mailbox ─────────────────────────────────────────────────


def test_get_mailbox_200_thread(client: TestClient) -> None:
    r = client.get(f"/api/members/{_AGENT_ID}/mailbox")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 1
    msg = data[0]
    assert isinstance(msg, dict)
    assert msg["from"] == "lead"
    assert msg["to"] == _AGENT_ID
    assert msg["kind"] == "instruction"
    assert msg["ts"] == "2026-05-18T00:00:00Z"


def test_get_mailbox_empty_when_file_missing(client: TestClient, ws_root: Path) -> None:
    (ws_root / "state" / "agents" / _AGENT_ID / "mailbox.md").unlink()
    r = client.get(f"/api/members/{_AGENT_ID}/mailbox")
    assert r.status_code == 200
    assert r.json() == []


def test_get_mailbox_404_unknown_agent(client: TestClient) -> None:
    r = client.get("/api/members/nonexistent/mailbox")
    assert r.status_code == 404


# ── /api/members/{id}/delivery ────────────────────────────────────────────────


def test_get_delivery_200(client: TestClient) -> None:
    r = client.get(f"/api/members/{_AGENT_ID}/delivery")
    assert r.status_code == 200
    data = r.json()
    assert "path" in data
    assert "content" in data
    assert "Done" in data["content"]


def test_get_delivery_404_unknown_agent(client: TestClient) -> None:
    r = client.get("/api/members/nonexistent/delivery")
    assert r.status_code == 404
