"""Unit tests for destination dedup injection (P.5)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.nodes.resolve_entities import _inject_subject_destination_rows
from src.orchestrator.spool import RunSpool, entity_rows, make_handle


@pytest.fixture(autouse=True)
def _spool_isolation(tmp_path, monkeypatch):
    import src.orchestrator.spool as spool_mod
    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    yield


def _fake_connector(rows: list[dict[str, Any]]) -> MagicMock:
    conn = MagicMock()
    conn.list_tables.return_value = ["companies"]
    conn.read_entity_rows.return_value = rows
    return conn


def _state(active: bool = True) -> dict[str, Any]:
    return {
        "run_id": "dd_test",
        "merge_scope": {
            "active": active, "subject": "Nova", "scope_type": "company",
        },
        "destination": {"type": "postgresql", "host": "h", "database": "d"},
        "target_entities": ["company"],
    }


def test_matching_destination_row_is_injected_with_its_id() -> None:
    spool = RunSpool("dd_test")
    spool.reset_entity("company")
    spool.append("company", [
        {"_origin_id": "crm", "_origin_table": "companies",
         "_origin_id_row": "7", "id": 7, "name": "Nova"},
    ])
    entity_map: dict[str, dict[str, str]] = {}
    extracted = {"company": make_handle("dd_test", "company", 1)}

    conn = _fake_connector([
        {"id": "dest-nova-1", "name": "Nova Inc"},   # matches "Nova"
        {"id": "dest-other", "name": "Globex"},        # does not
    ])
    with patch(
        "src.mcp_server.tools.load_data._get_destination_connector",
        return_value=conn,
    ):
        _inject_subject_destination_rows(_state(), spool, entity_map, extracted)

    # Only the Nova destination row was injected, tagged and id-mapped.
    key = "__destination__:companies:dest-nova-1"
    assert entity_map["company"][key] == "dest-nova-1"
    rows = entity_rows({"extracted_data": extracted}, "company")
    injected = [r for r in rows if r.get("_origin_id") == "__destination__"]
    assert len(injected) == 1
    assert injected[0]["id"] == "dest-nova-1"
    # Globex was filtered out.
    assert all("Globex" not in str(r.get("name", "")) for r in rows)


def test_inactive_scope_injects_nothing() -> None:
    spool = RunSpool("dd_test")
    spool.reset_entity("company")
    entity_map: dict[str, dict[str, str]] = {}
    extracted = {"company": make_handle("dd_test", "company", 0)}
    conn = _fake_connector([{"id": "x", "name": "Nova"}])
    with patch(
        "src.mcp_server.tools.load_data._get_destination_connector",
        return_value=conn,
    ):
        _inject_subject_destination_rows(_state(active=False), spool, entity_map, extracted)
    assert entity_map == {}
    conn.read_entity_rows.assert_not_called()


def test_no_matching_destination_rows_is_a_noop() -> None:
    spool = RunSpool("dd_test")
    spool.reset_entity("company")
    entity_map: dict[str, dict[str, str]] = {}
    extracted = {"company": make_handle("dd_test", "company", 0)}
    conn = _fake_connector([{"id": "x", "name": "Globex"}])  # no Nova
    with patch(
        "src.mcp_server.tools.load_data._get_destination_connector",
        return_value=conn,
    ):
        _inject_subject_destination_rows(_state(), spool, entity_map, extracted)
    assert "company" not in entity_map or not entity_map.get("company")


@pytest.mark.asyncio
async def test_cluster_adopts_the_destination_id() -> None:
    """The point of the whole feature: an incoming subject that matches an
    existing destination row takes on that row's id, so the load updates it."""
    from src.mcp_server.tools.resolve_entities import resolve_entities_impl

    records = [
        {"_origin_id": "crm", "_origin_table": "companies", "_origin_id_row": "7",
         "name": "Nova Inc", "email": "info@nova.com", "tax_id": "NV-100"},
        {"_origin_id": "__destination__", "_origin_table": "companies",
         "_origin_id_row": "dest-1",
         "name": "Nova Inc", "email": "info@nova.com", "tax_id": "NV-100"},
    ]
    known = {"__destination__:companies:dest-1": "dest-1"}
    result = await resolve_entities_impl(
        entity_type="company", records=records, known_cluster_ids=known,
    )
    multi = [c for c in result["clusters"] if len(c.get("record_ids", [])) == 2]
    assert multi, "the incoming and destination Nova rows should have clustered"
    assert multi[0]["cluster_id"] == "dest-1"


def test_read_failure_never_raises() -> None:
    spool = RunSpool("dd_test")
    spool.reset_entity("company")
    entity_map: dict[str, dict[str, str]] = {}
    extracted = {"company": make_handle("dd_test", "company", 0)}
    conn = MagicMock()
    conn.list_tables.side_effect = RuntimeError("destination down")
    with patch(
        "src.mcp_server.tools.load_data._get_destination_connector",
        return_value=conn,
    ):
        # Must not raise — dedup is an enhancement, not a prerequisite.
        _inject_subject_destination_rows(_state(), spool, entity_map, extracted)
    assert entity_map == {}
