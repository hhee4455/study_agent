"""Tests: /api/llm-calls endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.api.llm_calls import router
from web.api.members import get_ws_root

_CALL_A = {
    "member": "M001",
    "kind": "plan",
    "timestamp": "2026-05-18T00:00:00Z",
    "model": "opus",
    "cost_usd": 0.05,
    "prompt_tokens": 1000,
    "completion_tokens": 500,
}

_CALL_B = {
    "member": "M002",
    "kind": "review",
    "timestamp": "2026-05-18T01:00:00Z",
    "model": "sonnet",
    "cost_usd": 0.02,
    "prompt_tokens": 500,
    "completion_tokens": 200,
}


def _make_client(ws_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_ws_root] = lambda: ws_root
    return TestClient(app)


@pytest.fixture()
def ws_root(tmp_path: Path) -> Path:
    logs_dir = tmp_path / "state" / "llm_logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "20260518-000000-call-a.json").write_text(
        json.dumps(_CALL_A), encoding="utf-8"
    )
    (logs_dir / "20260518-010000-call-b.json").write_text(
        json.dumps(_CALL_B), encoding="utf-8"
    )
    return tmp_path


# ── (1) 전체 목록 ─────────────────────────────────────────────────────────────


def test_list_all(ws_root: Path) -> None:
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls")
    assert r.status_code == 200
    data = r.json()
    assert "calls" in data
    calls = data["calls"]
    assert len(calls) == 2
    assert calls[0]["filename"] == "20260518-000000-call-a.json"
    assert calls[0]["member"] == "M001"
    assert calls[1]["filename"] == "20260518-010000-call-b.json"
    assert calls[1]["member"] == "M002"


# ── (2) ?member=X 필터 ────────────────────────────────────────────────────────


def test_filter_member(ws_root: Path) -> None:
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls?member=M001")
    assert r.status_code == 200
    calls = r.json()["calls"]
    assert len(calls) == 1
    assert calls[0]["member"] == "M001"
    assert calls[0]["kind"] == "plan"


def test_filter_member_no_match(ws_root: Path) -> None:
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls?member=M999")
    assert r.status_code == 200
    assert r.json()["calls"] == []


# ── (3) ?kind=Y 필터 ──────────────────────────────────────────────────────────


def test_filter_kind(ws_root: Path) -> None:
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls?kind=review")
    assert r.status_code == 200
    calls = r.json()["calls"]
    assert len(calls) == 1
    assert calls[0]["kind"] == "review"
    assert calls[0]["member"] == "M002"


def test_filter_kind_no_match(ws_root: Path) -> None:
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls?kind=nonexistent")
    assert r.status_code == 200
    assert r.json()["calls"] == []


# ── (4) 빈 디렉토리 ───────────────────────────────────────────────────────────


def test_empty_directory(tmp_path: Path) -> None:
    logs_dir = tmp_path / "state" / "llm_logs"
    logs_dir.mkdir(parents=True)
    client = _make_client(tmp_path)
    r = client.get("/api/llm-calls")
    assert r.status_code == 200
    assert r.json() == {"calls": []}


def test_missing_directory(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    r = client.get("/api/llm-calls")
    assert r.status_code == 200
    assert r.json() == {"calls": []}


# ── (5) 잘못된 JSON graceful skip ─────────────────────────────────────────────


def test_invalid_json_graceful_skip(ws_root: Path) -> None:
    logs_dir = ws_root / "state" / "llm_logs"
    (logs_dir / "20260518-005959-bad.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls")
    assert r.status_code == 200
    calls = r.json()["calls"]
    assert len(calls) == 2
    filenames = [c["filename"] for c in calls]
    assert "20260518-005959-bad.json" not in filenames
