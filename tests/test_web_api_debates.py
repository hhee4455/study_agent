"""Tests: /api/debates endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.api.debates import get_ws_root
from web.server import app

# ── test fixtures ─────────────────────────────────────────────────────────────

_FOUR_PERSONA_MD = """\
# Debate: Which API design to use?

**Question**: RESTful vs GraphQL?
**Context**: Team is split.
**Started**: 2026-05-18T00:00:00.000000

---

## Round 1

[Agent-A / Pragmatist]
REST is simpler and well-understood by the whole team.

[Agent-B / Skeptic]
I question whether REST handles complex nested queries efficiently.

[Agent-C / Architect]
GraphQL provides better long-term flexibility at the cost of added complexity.

[Agent-D / Outsider]
From broader market adoption, REST remains the dominant safe choice.

---

## Summary
Lean toward REST for simplicity.

## 최종 결정
**결정**: Use REST.
"""

_ESCALATION_MD = """\
# Debate: Critical module rewrite?

**Question**: Should we rewrite the core module?
**Context**: High-stakes decision.

## Round 1

[Agent-A / Pragmatist]
A rewrite cleans up debt.

[Agent-B / Skeptic]
Full rewrite risks are too high.

---

## Escalation
This debate required escalation to a stronger model (opus) for deeper analysis.

## Summary
Escalated to opus; outcome: gradual migration.

## 최종 결정
**결정**: Gradual migration over full rewrite.
"""


@pytest.fixture()
def ws_root(tmp_path: Path) -> Path:
    (tmp_path / "state" / "lead" / "conflicts").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def client(ws_root: Path):
    app.dependency_overrides[get_ws_root] = lambda: ws_root
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── (1) empty directory → empty list ─────────────────────────────────────────


def test_empty_conflicts_dir_returns_empty_list(client: TestClient) -> None:
    r = client.get("/api/debates")
    assert r.status_code == 200
    assert r.json() == {"debates": []}


# ── (2) two conflict files → list with correct fields ────────────────────────


def test_two_conflict_files_list(client: TestClient, ws_root: Path) -> None:
    conflicts_dir = ws_root / "state" / "lead" / "conflicts"
    (conflicts_dir / "conflict-M001.md").write_text(
        "# Debate: First issue\nSome content.\n", encoding="utf-8"
    )
    (conflicts_dir / "conflict-M002.md").write_text(
        "# Debate: Second issue\nOther content.\n", encoding="utf-8"
    )

    r = client.get("/api/debates")
    assert r.status_code == 200
    data = r.json()
    debates = data["debates"]
    assert len(debates) == 2

    for item in debates:
        assert "conflict_id" in item
        assert "topic" in item
        assert "file" in item
        assert "consensus_reached" in item
        assert "escalated" in item

    ids = {d["conflict_id"] for d in debates}
    assert "conflict-M001" in ids
    assert "conflict-M002" in ids

    topics = {d["conflict_id"]: d["topic"] for d in debates}
    assert topics["conflict-M001"] == "Debate: First issue"
    assert topics["conflict-M002"] == "Debate: Second issue"


# ── (3) detail with 4 personas ────────────────────────────────────────────────


def test_four_personas_parsed(client: TestClient, ws_root: Path) -> None:
    conflicts_dir = ws_root / "state" / "lead" / "conflicts"
    (conflicts_dir / "debate-four.md").write_text(_FOUR_PERSONA_MD, encoding="utf-8")

    r = client.get("/api/debates/debate-four")
    assert r.status_code == 200
    data = r.json()

    assert data["conflict_id"] == "debate-four"
    assert data["topic"] == "Debate: Which API design to use?"
    assert "raw_markdown" in data

    personas = data["personas"]
    assert len(personas) == 4

    by_name = {p["name"]: p for p in personas}
    assert set(by_name) == {"Agent-A", "Agent-B", "Agent-C", "Agent-D"}

    assert by_name["Agent-A"]["stance"] == "Pragmatist"
    assert by_name["Agent-B"]["stance"] == "Skeptic"
    assert by_name["Agent-C"]["stance"] == "Architect"
    assert by_name["Agent-D"]["stance"] == "Outsider"

    assert "REST" in by_name["Agent-A"]["content"]


# ── (4) escalation block detected ────────────────────────────────────────────


def test_escalation_block_detected(client: TestClient, ws_root: Path) -> None:
    conflicts_dir = ws_root / "state" / "lead" / "conflicts"
    (conflicts_dir / "debate-esc.md").write_text(_ESCALATION_MD, encoding="utf-8")

    # detail endpoint
    r = client.get("/api/debates/debate-esc")
    assert r.status_code == 200
    data = r.json()
    assert data["escalation"] is not None
    assert data["escalation"]["detected"] is True
    assert data["escalation"]["snippet"] is not None

    # list endpoint should reflect escalated=True
    r2 = client.get("/api/debates")
    assert r2.status_code == 200
    items = {d["conflict_id"]: d for d in r2.json()["debates"]}
    assert items["debate-esc"]["escalated"] is True


# ── (5) unknown conflict_id → 404 ────────────────────────────────────────────


def test_missing_conflict_id_returns_404(client: TestClient) -> None:
    r = client.get("/api/debates/nonexistent-id")
    assert r.status_code == 404


# ── (6) path traversal rejected → 400 ────────────────────────────────────────


def test_traversal_conflict_id_rejected(client: TestClient) -> None:
    # ".." anywhere in the conflict_id must be rejected
    r = client.get("/api/debates/bad..name")
    assert r.status_code == 400
