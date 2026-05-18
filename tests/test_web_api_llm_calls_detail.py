"""Tests: GET /api/llm-calls/{filename} detail endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.api.llm_calls import get_ws_root, router

_SAMPLE = {
    "member": "M001",
    "kind": "plan",
    "timestamp": "2026-05-18T00:00:00Z",
    "model": "opus",
    "cost_usd": 0.05,
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
    (logs_dir / "sample.json").write_text(json.dumps(_SAMPLE), encoding="utf-8")
    return tmp_path


# ── (1) 존재 파일 → 200, 필드 정확 ───────────────────────────────────────────


def test_detail_200_fields(ws_root: Path) -> None:
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls/sample.json")
    assert r.status_code == 200
    data = r.json()
    assert data["filename"] == "sample.json"
    assert data["member"] == "M001"
    assert data["kind"] == "plan"
    assert data["ts"] == "2026-05-18T00:00:00Z"
    assert data["size_bytes"] > 0
    assert isinstance(data["content"], dict)
    assert data["content"]["model"] == "opus"
    assert "parse_error" not in data


# ── (2) 없는 파일 → 404 ───────────────────────────────────────────────────────


def test_detail_404_missing(ws_root: Path) -> None:
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls/nonexistent.json")
    assert r.status_code == 404


# ── (3) traversal → rejected (400 from handler, or 404 from URL normalization) ─


def test_traversal_encoded_slash(ws_root: Path) -> None:
    # ..%2F is decoded to ../ by the HTTP layer which normalizes the URL path;
    # the request never reaches the handler but is still rejected (→ 404).
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls/..%2Ffoo")
    assert r.status_code in (400, 404)


def test_traversal_dotdot_path(ws_root: Path) -> None:
    # ../foo is normalized by the HTTP client before sending; rejected (→ 404).
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls/../foo")
    assert r.status_code in (400, 404)


def test_traversal_dotdot_in_name(ws_root: Path) -> None:
    # ..foo.json reaches the handler; our _validate_filename catches ".." → 400.
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls/..foo.json")
    assert r.status_code == 400


# ── (4) JSON parse 실패 → 200 + parse_error 노출 ──────────────────────────────


def test_parse_error_exposed(ws_root: Path) -> None:
    logs_dir = ws_root / "state" / "llm_logs"
    (logs_dir / "bad.json").write_text("{not valid json", encoding="utf-8")
    client = _make_client(ws_root)
    r = client.get("/api/llm-calls/bad.json")
    assert r.status_code == 200
    data = r.json()
    assert data["filename"] == "bad.json"
    assert "parse_error" in data
    assert isinstance(data["content"], str)
    assert data["size_bytes"] > 0
