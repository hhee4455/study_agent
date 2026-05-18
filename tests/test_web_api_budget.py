"""Tests: /api/budget endpoint."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.api.members import get_ws_root
from web.server import app

_STARTED_AT = 1_000_000.0
_ELAPSED = 3600.0  # 1 hour
_NOW = _STARTED_AT + _ELAPSED

_BUDGET_NEW = {
    "started_at": _STARTED_AT,
    "totals": {
        "usd": 2.0,
        "input_tokens": 1000,
        "output_tokens": 500,
        "calls": 10,
    },
    "by_model": {
        "claude-opus-4-5": {
            "usd": 1.5,
            "input_tokens": 800,
            "output_tokens": 400,
            "calls": 7,
        },
        "claude-haiku-4-5": {
            "usd": 0.5,
            "input_tokens": 200,
            "output_tokens": 100,
            "calls": 3,
        },
    },
}

_BUDGET_WITH_LIMIT = {**_BUDGET_NEW, "limit_usd": 10.0}


@pytest.fixture()
def ws_root(tmp_path: Path) -> Path:
    (tmp_path / "state").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def client(ws_root: Path):
    app.dependency_overrides[get_ws_root] = lambda: ws_root
    yield TestClient(app)
    app.dependency_overrides.clear()


def _write_budget(ws_root: Path, data: object) -> None:
    (ws_root / "state" / "budget.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ── normal budget.json with by_model ──────────────────────────────────────────


def test_budget_normal(client: TestClient, ws_root: Path, monkeypatch) -> None:
    _write_budget(ws_root, _BUDGET_NEW)
    monkeypatch.setattr(time, "time", lambda: _NOW)

    r = client.get("/api/budget")
    assert r.status_code == 200
    data = r.json()

    assert data["total_usd"] == pytest.approx(2.0)
    assert data["total_tokens_in"] == 1000
    assert data["total_tokens_out"] == 500

    assert "claude-opus-4-5" in data["by_model"]
    assert "claude-haiku-4-5" in data["by_model"]
    opus = data["by_model"]["claude-opus-4-5"]
    assert opus["usd"] == pytest.approx(1.5)
    assert opus["tokens_in"] == 800
    assert opus["tokens_out"] == 400
    assert opus["calls"] == 7

    assert data["elapsed_sec"] == pytest.approx(_ELAPSED)
    assert data["hourly_usd"] == pytest.approx(2.0)  # 2.0 USD / 1h
    assert data["limit_usd"] is None
    assert data["limit_progress"] is None
    assert data["eta_seconds_to_limit"] is None
    assert data["started_at"] == str(_STARTED_AT)


# ── budget.json missing → 200 with zero defaults ───────────────────────────────


def test_budget_file_missing(client: TestClient) -> None:
    r = client.get("/api/budget")
    assert r.status_code == 200
    data = r.json()

    assert data["total_usd"] == 0.0
    assert data["total_tokens_in"] == 0
    assert data["total_tokens_out"] == 0
    assert data["by_model"] == {}
    assert data["limit_usd"] is None
    assert data["elapsed_sec"] is None
    assert data["hourly_usd"] is None
    assert data["started_at"] is None


# ── corrupted JSON → 200 with zero defaults (no 500) ──────────────────────────


def test_budget_corrupted_json(client: TestClient, ws_root: Path) -> None:
    (ws_root / "state" / "budget.json").write_text("{not valid json", encoding="utf-8")

    r = client.get("/api/budget")
    assert r.status_code == 200
    data = r.json()

    assert data["total_usd"] == 0.0
    assert data["total_tokens_in"] == 0
    assert data["by_model"] == {}
    assert data["started_at"] is None


# ── limit_usd present → progress and ETA computed ─────────────────────────────


def test_budget_with_limit(client: TestClient, ws_root: Path, monkeypatch) -> None:
    _write_budget(ws_root, _BUDGET_WITH_LIMIT)
    monkeypatch.setattr(time, "time", lambda: _NOW)

    r = client.get("/api/budget")
    assert r.status_code == 200
    data = r.json()

    assert data["limit_usd"] == pytest.approx(10.0)
    assert data["limit_progress"] == pytest.approx(0.2)  # 2.0 / 10.0
    # hourly_usd=2.0, remaining=8.0 → ETA = 8.0/2.0 * 3600 = 14400s
    assert data["eta_seconds_to_limit"] == pytest.approx(14400.0)


# ── limit already exceeded → ETA = 0, progress > 1 ───────────────────────────


def test_budget_limit_exceeded(client: TestClient, ws_root: Path, monkeypatch) -> None:
    _write_budget(ws_root, {**_BUDGET_NEW, "limit_usd": 1.0})
    monkeypatch.setattr(time, "time", lambda: _NOW)

    r = client.get("/api/budget")
    assert r.status_code == 200
    data = r.json()

    assert data["eta_seconds_to_limit"] == pytest.approx(0.0)
    assert data["limit_progress"] == pytest.approx(2.0)  # 200% — exceeded


# ── legacy schema (no totals) → parsed from cost_usd / tokens_in / tokens_out ─


def test_budget_legacy_schema(client: TestClient, ws_root: Path, monkeypatch) -> None:
    legacy = {
        "started_at": _STARTED_AT,
        "cost_usd": 1.4058,
        "tokens_in": 60,
        "tokens_out": 18732,
        "turns": 10,
    }
    _write_budget(ws_root, legacy)
    monkeypatch.setattr(time, "time", lambda: _NOW)

    r = client.get("/api/budget")
    assert r.status_code == 200
    data = r.json()

    assert data["total_usd"] == pytest.approx(1.4058)
    assert data["total_tokens_in"] == 60
    assert data["total_tokens_out"] == 18732
    assert data["by_model"] == {}
    assert data["elapsed_sec"] == pytest.approx(_ELAPSED)
    assert data["hourly_usd"] == pytest.approx(1.4058)
