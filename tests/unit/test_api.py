"""
Unit tests for the FastAPI backend (src/api/main.py).

The API is a thin wrapper over WorkflowManager + db_discovery +
connections; these tests verify the wiring (validation, delegation,
response shapes), mocking the manager so no real DB/graph runs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_api


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_api())


@pytest.fixture
def mock_manager():
    mgr = MagicMock()
    with patch("src.api.main.get_manager", return_value=mgr):
        yield mgr


# ── Connections ──────────────────────────────────────────────────────────────


def test_recent_connections_shape(client: TestClient):
    resp = client.get("/api/connections/recent")
    assert resp.status_code == 200
    body = resp.json()
    assert "recent_origins" in body
    assert "recent_destinations" in body
    assert isinstance(body["recent_origins"], list)


def test_add_recent_persists_and_labels(client: TestClient):
    item = {
        "type": "postgres",
        "connection": {"host": "h", "port": 5432, "user": "u", "database": "d"},
    }
    resp = client.post("/api/connections/recent", json={"kind": "origin", "item": item})
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "origin"
    assert body["items"], "expected at least the just-added item"
    assert "label" in body["items"][-1]
    assert "d" in body["items"][-1]["label"]

    # It should now appear in the recent list too.
    listed = client.get("/api/connections/recent").json()
    assert any(it["connection"]["database"] == "d" for it in listed["recent_origins"])


def test_connection_test_delegates(client: TestClient):
    fake = {
        "connected": True,
        "error": None,
        "databases": ["a"],
        "schemas": [],
        "tables": [],
        "table_columns": {},
        "table_row_counts": {},
    }
    with patch("src.api.main.test_and_discover_schemas", return_value=fake) as td:
        resp = client.post(
            "/api/connections/test",
            json={
                "conn_type": "postgres",
                "host": "h",
                "port": 5432,
                "user": "u",
                "password": "p",
                "database": "d",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["databases"] == ["a"]
    td.assert_called_once()


# ── Workflow ─────────────────────────────────────────────────────────────────


def test_start_requires_request(client: TestClient, mock_manager):
    resp = client.post(
        "/api/workflow/start",
        json={"request": "  ", "origins": [{"id": "o"}], "destination": {"type": "postgresql"}},
    )
    assert resp.status_code == 400


def test_start_requires_origins(client: TestClient, mock_manager):
    resp = client.post(
        "/api/workflow/start",
        json={"request": "merge", "origins": [], "destination": {"type": "postgresql"}},
    )
    assert resp.status_code == 400


def test_start_delegates_and_returns_thread_id(client: TestClient, mock_manager):
    mock_manager.start_workflow.return_value = "wf_abc123"
    resp = client.post(
        "/api/workflow/start",
        json={
            "request": "merge companies",
            "origins": [{"id": "o1", "type": "postgres", "connection": {}}],
            "destination": {"type": "postgresql", "host": "h"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["thread_id"] == "wf_abc123"
    mock_manager.start_workflow.assert_called_once()


def test_status_delegates(client: TestClient, mock_manager):
    mock_manager.get_workflow_status.return_value = {"status": "interrupted"}
    resp = client.get("/api/workflow/wf_1/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "interrupted"
    mock_manager.get_workflow_status.assert_called_with("wf_1")


def test_feedback_rejects_when_not_interrupted(client: TestClient, mock_manager):
    mock_manager.get_workflow_status.return_value = {"status": "running"}
    resp = client.post("/api/workflow/wf_1/feedback", json={"decision": "approve"})
    assert resp.status_code == 409
    mock_manager.resume_workflow.assert_not_called()


def test_feedback_passes_mapping_edits(client: TestClient, mock_manager):
    mock_manager.get_workflow_status.return_value = {
        "status": "interrupted",
        "current_interrupt_node": "human_review_mappings",
    }
    edits = [
        {
            "entity_type": "company",
            "source_field": "nm",
            "action": "map",
            "dest_column": "name",
            "confidence": 1.0,
        }
    ]
    resp = client.post(
        "/api/workflow/wf_1/feedback",
        json={"decision": "approve", "mapping_edits": edits},
    )
    assert resp.status_code == 200
    mock_manager.resume_workflow.assert_called_once()
    _, feedback = mock_manager.resume_workflow.call_args[0]
    assert feedback["approved"] is True
    assert feedback["node"] == "human_review_mappings"
    assert feedback["mapping_edits"] == edits


def test_feedback_passes_scope_plan(client: TestClient, mock_manager):
    mock_manager.get_workflow_status.return_value = {
        "status": "interrupted",
        "current_interrupt_node": "human_review_scope",
    }
    scope_plan = {
        "subject": "Nova",
        "subject_entity": "company",
        "candidates": [],
        "entities": {
            "person": [{"origin_id": "crm", "table": "public.people", "mode": "not_found"}]
        },
    }
    resp = client.post(
        "/api/workflow/wf_1/feedback",
        json={"decision": "approve", "scope_plan": scope_plan},
    )
    assert resp.status_code == 200
    mock_manager.resume_workflow.assert_called_once()
    _, feedback = mock_manager.resume_workflow.call_args[0]
    assert feedback["node"] == "human_review_scope"
    assert feedback["scope_plan"] == scope_plan


def test_abort_delegates(client: TestClient, mock_manager):
    resp = client.post("/api/workflow/wf_1/abort", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_manager.abort_workflow.assert_called_with("wf_1")


def test_active_delegates(client: TestClient, mock_manager):
    mock_manager.list_active_workflows.return_value = [{"thread_id": "wf_1", "status": "running"}]
    resp = client.get("/api/workflow/active")
    assert resp.status_code == 200
    assert resp.json()["active"][0]["thread_id"] == "wf_1"


# ── Saved run profiles (Phase O) ─────────────────────────────────────────────


@pytest.fixture
def isolated_profiles(tmp_path):
    """Point the profile store at a temp dir so tests never touch config/."""
    from src.orchestrator import profiles as profiles_mod

    original = profiles_mod.PROFILE_DIR
    profiles_mod.PROFILE_DIR = tmp_path / "profiles"
    yield profiles_mod
    profiles_mod.PROFILE_DIR = original


ORIGIN = {
    "id": "pg",
    "type": "postgresql",
    "priority": 1,
    "connection": {"host": "h", "port": 5432, "user": "u", "password": "s3cret", "database": "d"},
}
DEST = {
    "type": "postgresql",
    "host": "h",
    "port": 5432,
    "user": "u",
    "password": "s3cret",
    "database": "dest",
}


def test_profiles_list_is_empty_before_any_are_saved(client: TestClient, isolated_profiles):
    assert client.get("/api/profiles").json() == {"profiles": []}


def test_profile_save_then_list_then_get(client: TestClient, isolated_profiles):
    saved = client.post(
        "/api/profiles",
        json={
            "name": "nightly",
            "request": "merge people",
            "origins": [ORIGIN],
            "destination": DEST,
        },
    )
    assert saved.status_code == 200

    listed = client.get("/api/profiles").json()["profiles"]
    assert [p["name"] for p in listed] == ["nightly"]

    fetched = client.get("/api/profiles/nightly").json()
    assert fetched["user_request"] == "merge people"


def test_a_saved_profile_never_returns_a_password(client: TestClient, isolated_profiles):
    client.post(
        "/api/profiles",
        json={
            "name": "nightly",
            "request": "r",
            "origins": [ORIGIN],
            "destination": DEST,
        },
    )
    assert "s3cret" not in client.get("/api/profiles/nightly").text


def test_an_unnamed_profile_is_rejected(client: TestClient, isolated_profiles):
    resp = client.post(
        "/api/profiles",
        json={
            "name": "  ",
            "request": "r",
            "origins": [],
            "destination": {},
        },
    )
    assert resp.status_code == 422


def test_missing_profile_is_a_404(client: TestClient, isolated_profiles):
    assert client.get("/api/profiles/nope").status_code == 404
    assert client.delete("/api/profiles/nope").status_code == 404


def test_profile_delete(client: TestClient, isolated_profiles):
    client.post(
        "/api/profiles",
        json={
            "name": "nightly",
            "request": "r",
            "origins": [ORIGIN],
            "destination": DEST,
        },
    )
    assert client.delete("/api/profiles/nightly").status_code == 200
    assert client.get("/api/profiles").json() == {"profiles": []}


def test_starting_from_an_unknown_profile_is_a_404(
    client: TestClient, mock_manager, isolated_profiles
):
    """A typo in the profile name must not silently run an ad-hoc merge."""
    mock_manager.start_workflow.side_effect = ValueError("No saved profile named 'ghost'.")
    resp = client.post(
        "/api/workflow/start",
        json={
            "request": "merge people",
            "origins": [ORIGIN],
            "destination": DEST,
            "profile_name": "ghost",
        },
    )
    assert resp.status_code == 404


def test_start_passes_the_profile_through(client: TestClient, mock_manager, isolated_profiles):
    mock_manager.start_workflow.return_value = "wf_1"
    client.post(
        "/api/workflow/start",
        json={
            "request": "merge people",
            "origins": [ORIGIN],
            "destination": DEST,
            "profile_name": "nightly",
        },
    )
    assert mock_manager.start_workflow.call_args.kwargs["profile_name"] == ("nightly")


def test_start_passes_a_conflict_policy_override_through(
    client: TestClient, mock_manager, isolated_profiles
):
    """Phase 5 (O.3): an ad-hoc conflict_policy in the start request must
    reach WorkflowManager.start_workflow, so a caller can override
    survivorship for one run without first saving a profile."""
    mock_manager.start_workflow.return_value = "wf_1"
    policy = {"revenue": ["numeric_max"], "_default": ["priority", "recency"]}
    client.post(
        "/api/workflow/start",
        json={
            "request": "merge people",
            "origins": [ORIGIN],
            "destination": DEST,
            "conflict_policy": policy,
        },
    )
    assert mock_manager.start_workflow.call_args.kwargs["conflict_policy"] == policy


def test_start_without_a_conflict_policy_passes_none_through(
    client: TestClient, mock_manager, isolated_profiles
):
    """Omitting conflict_policy must not silently invent an empty override —
    None lets a saved profile's pinned policy (if any) still apply."""
    mock_manager.start_workflow.return_value = "wf_1"
    client.post(
        "/api/workflow/start",
        json={
            "request": "merge people",
            "origins": [ORIGIN],
            "destination": DEST,
        },
    )
    assert mock_manager.start_workflow.call_args.kwargs["conflict_policy"] is None


def test_save_as_profile_creates_it_before_the_run(
    client: TestClient, mock_manager, isolated_profiles
):
    mock_manager.start_workflow.return_value = "wf_1"
    resp = client.post(
        "/api/workflow/start",
        json={
            "request": "merge people",
            "origins": [ORIGIN],
            "destination": DEST,
            "save_as_profile": "nightly",
        },
    )
    assert resp.json()["profile_name"] == "nightly"
    assert client.get("/api/profiles/nightly").status_code == 200


# ── Cost reporting (Phase O.5) ───────────────────────────────────────────────


def test_cost_endpoint_reports_zero_for_an_unknown_run(client: TestClient):
    body = client.get("/api/workflow/never-ran/cost").json()
    assert body["run"]["calls"] == 0
    assert "hit_rate" in body["cache"]


def test_cost_endpoint_reports_recorded_tokens(client: TestClient):
    from src.llm.cost import COST_LEDGER

    COST_LEDGER.reset("wf_cost")
    COST_LEDGER.record("wf_cost", model="m", input_tokens=30, output_tokens=5)
    body = client.get("/api/workflow/wf_cost/cost").json()
    assert body["run"]["total_tokens"] == 35
    COST_LEDGER.reset("wf_cost")
