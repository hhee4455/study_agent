"""Tests for web/state_reader.py and web/server.py."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from web import state_reader  # noqa: E402
from lead.dashboard import DashboardState  # noqa: E402


@pytest.fixture(autouse=True)
def reset_cache():
    state_reader.invalidate_cache()
    yield
    state_reader.invalidate_cache()


@pytest.fixture
def stub_collect(monkeypatch):
    mock = MagicMock(return_value=DashboardState(generated_at="2026-05-18T00:00:00Z"))
    monkeypatch.setattr(state_reader, "collect_state", mock)
    return mock


@pytest.fixture
def client(tmp_path, stub_collect):
    """TestClient fixture — skipped automatically when httpx is not installed."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from web.server import app, get_ws_root

    app.dependency_overrides[get_ws_root] = lambda: tmp_path
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# (a) /health returns 200
def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# (b) /api/state returns a dict that json.dumps can serialize
def test_api_state_returns_serializable_dict(client):
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    json.dumps(data)  # must not raise


# (c) two calls within TTL window — collect_state called only once
def test_cache_hit_within_ttl(tmp_path, stub_collect):
    state_reader.get_state(tmp_path, now=1.0)
    state_reader.get_state(tmp_path, now=4.9)
    assert stub_collect.call_count == 1


# (d) call after TTL expiry triggers a fresh collect_state
def test_cache_miss_after_ttl(tmp_path, stub_collect):
    state_reader.get_state(tmp_path, now=1.0)
    state_reader.get_state(tmp_path, now=6.1)
    assert stub_collect.call_count == 2


# (e) dataclass with Path and datetime fields serializes without error
def test_dataclass_and_path_serialize(tmp_path, monkeypatch):
    @dataclasses.dataclass
    class Inner:
        p: Path
        ts: datetime

    @dataclasses.dataclass
    class Outer:
        inner: Inner
        tags: set

    raw = Outer(
        inner=Inner(p=tmp_path / "x.txt", ts=datetime(2026, 5, 18, 12, 0, 0)),
        tags={"alpha", "beta"},
    )
    monkeypatch.setattr(state_reader, "collect_state", lambda _: raw)
    result = state_reader.get_state(tmp_path, now=999.0)
    serialized = json.dumps(result)
    assert str(tmp_path / "x.txt") in serialized
    assert "2026-05-18" in serialized
