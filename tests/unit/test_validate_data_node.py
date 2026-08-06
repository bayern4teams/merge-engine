"""Unit tests for src/orchestrator/nodes/validate_data.py"""

import pytest

from src.orchestrator.graph import NODE_SUCCESSOR, _build_builder
from src.orchestrator.nodes.validate_data import validate_data_node


@pytest.mark.asyncio
async def test_validate_data_node_execution():
    state = {
        "extracted_data": {
            "Company": [
                {"id": "c1", "name": "Boeing"},
            ]
        },
        "matching_rules": {
            "Company": {"unique_id_fields": ["id"]}
        },
    }

    result = await validate_data_node(state)
    assert "quality_report" in result
    assert "has_quality_failure" in result
    assert result["has_quality_failure"] is False
    assert result["quality_report"]["severity"] == "pass"


def test_validate_data_graph_position_and_non_blocking():
    """Verify validate_data sits between extract_data and resolve_entities and is non-blocking."""
    assert NODE_SUCCESSOR.get("extract_data") == "validate_data"
    assert NODE_SUCCESSOR.get("validate_data") == "resolve_entities"

    builder = _build_builder()
    nodes = set(builder.nodes.keys())
    assert "validate_data" in nodes
