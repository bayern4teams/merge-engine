"""
Unit tests for the orchestrator state module.

Tests MergeState structure and default state creation.
"""

from __future__ import annotations

import pytest

from src.orchestrator.state import MergeState


class TestMergeStateStructure:
    """Tests for MergeState state structure."""

    def test_state_has_required_input_fields(self) -> None:
        """State should have user_request and target_entities."""
        state: MergeState = {  # type: ignore
            "user_request": "import all products",
            "target_entities": ["product"],
            "origins": [],
            "destination": {},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
            "retry_counts": {},
        }
        assert state["user_request"] == "import all products"
        assert state["target_entities"] == ["product"]

    def test_state_with_all_fields(self) -> None:
        """State should hold all defined fields."""
        state: MergeState = {  # type: ignore
            "user_request": "Migrate persons from CRM",
            "target_entities": ["person"],
            "origins": [{"id": "crm", "type": "postgres"}],
            "destination": {
                "type": "databricks",
                "host": "test.databricks.com",
                "http_path": "/sql/1.0/warehouses/abc",
                "token": "test",
                "catalog": "main",
                "schema": "unified",
            },
            "schemas": {"crm": {"tables": []}},
            "dependency_tree": {"person": []},
            "extraction_order": ["person"],
            "extracted_data": {"person": [{"id": 1, "name": "Alice"}]},
            "entity_clusters": {"person": [{"cluster_id": "c1", "record_ids": ["1"]}]},
            "unified_entities": {"person": [{"id": "c1", "name": "Alice"}]},
            "field_mappings": {"person": {"name": ("first_name", 1.0)}},
            "pending_schema_changes": [{"column": "department", "data_type": "STRING", "table": "dim_person"}],
            "human_feedback": {"approved": True},
            "load_approval": True,
            "errors": ["minor warning"],
            "retry_counts": {"discover_schemas": 1},
            "interrupt_data": {"panel": "deps", "tree": {"A": []}},
        }
        assert state["user_request"] == "Migrate persons from CRM"
        assert len(state["extracted_data"]["person"]) == 1
        assert state["load_approval"] is True

    def test_state_empty_intermediate(self) -> None:
        """Intermediate state should have empty collections."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": [],
            "origins": [],
            "destination": {},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
            "retry_counts": {},
        }
        assert state["schemas"] == {}
        assert state["dependency_tree"] == {}
        assert state["pending_schema_changes"] == []
        assert state["errors"] == []

    def test_state_null_optional_fields(self) -> None:
        """Optional fields should support None values."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": [],
            "origins": [],
            "destination": {},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "human_feedback": None,
            "load_approval": None,
            "errors": [],
            "retry_counts": {},
            "interrupt_data": None,
        }
        assert state["human_feedback"] is None
        assert state["load_approval"] is None
        assert state["interrupt_data"] is None

    def test_state_errors_append_semantics(self) -> None:
        """Errors should support append-only semantics."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": [],
            "origins": [],
            "destination": {},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
            "retry_counts": {},
        }
        state["errors"].append("first error")
        state["errors"].append("second error")
        assert len(state["errors"]) == 2
        assert state["errors"][0] == "first error"
        assert state["errors"][1] == "second error"

    def test_state_retry_counts_tracking(self) -> None:
        """Retry counts should track per-node attempts."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": [],
            "origins": [],
            "destination": {},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
            "retry_counts": {},
        }
        state["retry_counts"]["discover_schemas"] = 1
        state["retry_counts"]["extract_data"] = 2
        state["retry_counts"]["discover_schemas"] = 2
        assert state["retry_counts"]["discover_schemas"] == 2
        assert state["retry_counts"]["extract_data"] == 2


class TestMergeStateIntegration:
    """Tests for state usage in workflow context."""

    def test_state_initialization_from_request(self) -> None:
        """State should be initializable from a user request."""
        state: MergeState = {  # type: ignore
            "user_request": "Import all companies from CRM and HR system",
            "target_entities": ["company"],
            "origins": [{"id": "crm", "type": "postgres"}, {"id": "hr", "type": "mongodb"}],
            "destination": {
                "type": "databricks",
                "host": "test.cloud.databricks.com",
                "http_path": "/sql/1.0/warehouses/abc",
                "token": "test",
                "catalog": "main",
                "schema": "unified",
                "tables": {"company": "dim_company"},
            },
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
            "retry_counts": {},
        }
        assert state["user_request"] == "Import all companies from CRM and HR system"
        assert state["target_entities"] == ["company"]
        assert len(state["origins"]) == 2

    def test_state_schema_discovery_result(self) -> None:
        """State should hold discovered schema results."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": ["person"],
            "origins": [{"id": "crm"}],
            "destination": {"type": "databricks", "host": "h", "http_path": "/sql/1.0/warehouses/a", "token": "t", "catalog": "c", "schema": "s"},
            "schemas": {
                "crm": {
                    "origin_id": "crm",
                    "origin_type": "postgres",
                    "tables": [
                        {
                            "name": "persons",
                            "columns": [
                                {"name": "id", "type": "INTEGER"},
                                {"name": "name", "type": "VARCHAR"},
                                {"name": "email", "type": "VARCHAR"},
                            ],
                        }
                    ],
                }
            },
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
            "retry_counts": {},
        }
        assert "crm" in state["schemas"]
        assert len(state["schemas"]["crm"]["tables"]) == 1
        assert len(state["schemas"]["crm"]["tables"][0]["columns"]) == 3

    def test_state_dependency_tree(self) -> None:
        """State should hold dependency tree analysis."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": ["person", "company"],
            "origins": [{"id": "crm"}],
            "destination": {"type": "databricks", "host": "h", "http_path": "/sql/1.0/warehouses/a", "token": "t", "catalog": "c", "schema": "s"},
            "schemas": {},
            "dependency_tree": {
                "person": ["company", "country"],
                "company": ["country"],
                "country": [],
            },
            "extraction_order": ["country", "company", "person"],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
            "retry_counts": {},
        }
        assert state["dependency_tree"]["person"] == ["company", "country"]
        assert state["dependency_tree"]["country"] == []
        assert state["extraction_order"] == ["country", "company", "person"]

    def test_state_entity_resolution_result(self) -> None:
        """State should hold entity resolution clusters."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": ["person"],
            "origins": [{"id": "crm"}],
            "destination": {"type": "databricks", "host": "h", "http_path": "/sql/1.0/warehouses/a", "token": "t", "catalog": "c", "schema": "s"},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {"person": [
                {"_origin_id": "crm", "id": 1, "name": "Alice"},
                {"_origin_id": "hr", "id": 101, "name": "Alice"},
            ]},
            "entity_clusters": {
                "person": [
                    {
                        "cluster_id": "abc123",
                        "record_ids": ["crm:persons:1", "hr:employees:101"],
                        "match_confidence": 0.95,
                        "required_human_review": False,
                    }
                ]
            },
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
            "retry_counts": {},
        }
        assert len(state["entity_clusters"]["person"]) == 1
        cluster = state["entity_clusters"]["person"][0]
        assert cluster["cluster_id"] == "abc123"
        assert len(cluster["record_ids"]) == 2
        assert cluster["match_confidence"] == 0.95

    def test_state_unified_entities_result(self) -> None:
        """State should hold unified entity records."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": ["person"],
            "origins": [{"id": "crm"}],
            "destination": {"type": "databricks", "host": "h", "http_path": "/sql/1.0/warehouses/a", "token": "t", "catalog": "c", "schema": "s"},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {
                "person": [
                    {
                        "_cluster_id": "abc123",
                        "_source_count": 2,
                        "name": "Alice",
                        "email": "alice@example.com",
                        "conflicts": [
                            {"field": "phone", "values": [
                                {"origin": "crm", "value": "555-1234"},
                                {"origin": "hr", "value": "555-5678"},
                            ]}
                        ],
                    }
                ]
            },
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [],
            "retry_counts": {},
        }
        unified = state["unified_entities"]["person"][0]
        assert unified["_cluster_id"] == "abc123"
        assert unified["_source_count"] == 2
        assert len(unified["conflicts"]) == 1

    def test_state_field_mapping_result(self) -> None:
        """State should hold field mapping results."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": ["person"],
            "origins": [{"id": "crm"}],
            "destination": {"type": "databricks", "host": "h", "http_path": "/sql/1.0/warehouses/a", "token": "t", "catalog": "c", "schema": "s"},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {
                "person": {
                    "name": {"dest_column": "full_name", "confidence": 0.95},
                    "email": {"dest_column": "email", "confidence": 1.0},
                    "department": {"dest_column": "department", "confidence": 0.0, "requires_schema_change": True},
                }
            },
            "pending_schema_changes": [{"column": "department", "data_type": "STRING", "table": "dim_person"}],
            "errors": [],
            "retry_counts": {},
        }
        mapping = state["field_mappings"]["person"]
        assert mapping["email"]["confidence"] == 1.0
        assert mapping["department"]["requires_schema_change"] is True

    def test_state_human_feedback_result(self) -> None:
        """State should hold human feedback from HITL."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": ["person"],
            "origins": [{"id": "crm"}],
            "destination": {"type": "databricks", "host": "h", "http_path": "/sql/1.0/warehouses/a", "token": "t", "catalog": "c", "schema": "s"},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "human_feedback": {
                "dependency_tree": {"person": [], "company": ["person"]},
                "approved": True,
            },
            "load_approval": True,
            "errors": [],
            "retry_counts": {},
            "interrupt_data": {"panel": "final_approval", "summary": {"total": 100}},
        }
        assert state["human_feedback"]["approved"] is True
        assert state["load_approval"] is True
        assert state["interrupt_data"]["panel"] == "final_approval"

    def test_state_with_multiple_errors(self) -> None:
        """State should accumulate errors across workflow."""
        state: MergeState = {  # type: ignore
            "user_request": "test",
            "target_entities": ["person"],
            "origins": [{"id": "crm"}],
            "destination": {"type": "databricks", "host": "h", "http_path": "/sql/1.0/warehouses/a", "token": "t", "catalog": "c", "schema": "s"},
            "schemas": {},
            "dependency_tree": {},
            "extraction_order": [],
            "extracted_data": {},
            "entity_clusters": {},
            "unified_entities": {},
            "field_mappings": {},
            "pending_schema_changes": [],
            "errors": [
                "Connection timeout on crm:5432",
                "Schema discovery retry for hr-mongo",
            ],
            "retry_counts": {"discover_schemas": 2, "extract_data": 1},
        }
        assert len(state["errors"]) == 2
        assert state["retry_counts"]["discover_schemas"] == 2