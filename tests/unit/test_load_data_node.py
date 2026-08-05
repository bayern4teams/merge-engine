"""Unit tests for load_data node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mcp_server.tools.load_data import _translate_foreign_keys
from src.orchestrator.nodes.load_data import (
    _source_id_to_cluster_id,
    load_data_node,
    update_extraction_state_node,
)


class TestLoadDataNode:
    """Tests for the load_data_node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all companies",
            "target_entities": ["company"],
            "extraction_order": ["company"],
            "unified_entities": {
                "company": [
                    {
                        "id": "cluster-1",
                        "name": "Acme Corp",
                        "email": "info@acme.com",
                    },
                    {
                        "id": "cluster-2",
                        "name": "Beta Inc",
                        "email": "info@beta.com",
                    },
                ],
            },
            "field_mappings": {
                "company": {
                    "name": {"dest_column": "name", "requires_schema_change": False},
                    "email": {"dest_column": "email", "requires_schema_change": False},
                },
            },
            "destination": {
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "tables": {
                    "company": "merged_companies",
                },
            },
            "pending_schema_changes": [],
            "errors": [],
            # The node refuses to write unless the final-approval gate
            # explicitly approved the load; these tests exercise the
            # approved path.
            "load_approval": True,
        }

    @pytest.fixture
    def mock_load_impl(self) -> AsyncMock:
        """Mock load_data_impl."""
        return AsyncMock(
            return_value={
                "total_records": 2,
                "inserted": 2,
                "updated": 0,
            }
        )

    @pytest.mark.asyncio
    async def test_load_data_success(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test successful data loading."""
        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        assert not result["errors"]
        mock_load_impl.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_data_applies_field_mapping(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test that field mappings are applied to transformed records."""
        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            await load_data_node(base_state)

        call_kwargs = mock_load_impl.call_args[1]
        assert call_kwargs["entity_type"] == "company"
        assert len(call_kwargs["records"]) == 2

    @pytest.mark.asyncio
    async def test_load_data_skips_empty_entity(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test that entities with no unified records are skipped."""
        base_state["unified_entities"]["company"] = []

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        mock_load_impl.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_data_load_failure(self, base_state: dict[str, Any]) -> None:
        """Test handling of load failure."""

        async def fail_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise Exception("Load service error")

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            side_effect=fail_impl,
        ):
            result = await load_data_node(base_state)

        assert len(result["errors"]) == 1
        assert "Load failed" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_load_data_preserves_existing_errors(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Existing errors survive a call to this node via MergeState's real
        operator.add reducer, not because the node re-returns the old
        history itself (Phase 0 Sec 0.8 - see analyze_dependencies' test of
        the same name for why the previous version of this test asserted
        the actual bug)."""
        base_state["errors"] = ["pre-existing"]

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        assert "pre-existing" not in result.get("errors", [])
        combined = base_state["errors"] + result.get("errors", [])
        assert "pre-existing" in combined

    @pytest.mark.asyncio
    async def test_load_data_refuses_schema_changes(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """The destination schema is a fixed contract: pending schema changes
        are refused and recorded as an error, never applied."""
        base_state["pending_schema_changes"] = [
            {
                "column": "phone",
                "data_type": "VARCHAR",
                "table": "merged_companies",
                "entity_type": "company",
            }
        ]

        mock_connector = MagicMock()
        mock_connector.extend_schema_batch = MagicMock(return_value={"added": 1, "failed": 0})

        mock_dest_config = MagicMock()
        mock_dest_config.model_dump = MagicMock(
            return_value={
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
            }
        )

        mock_destination = MagicMock()
        mock_destination.type = "postgresql"
        mock_destination.tables = {"company": "merged_companies"}
        mock_destination.connection = mock_dest_config

        base_state["destination"] = mock_destination

        with (
            patch(
                "src.orchestrator.nodes.load_data.load_data_impl",
                mock_load_impl,
            ),
            patch(
                "src.mcp_server.tools.load_data._get_destination_connector",
                return_value=mock_connector,
            ),
        ):
            result = await load_data_node(base_state)

        mock_connector.extend_schema_batch.assert_not_called()
        assert any("fixed contract" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_load_data_dict_destination(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test with dict-style destination."""
        base_state["destination"] = {
            "type": "postgresql",
            "tables": {"company": "test_table"},
        }

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            await load_data_node(base_state)

        mock_load_impl.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_data_entity_not_in_mapping(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test loading entity with no field mapping."""
        base_state["field_mappings"]["company"] = {}

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        mock_load_impl.assert_called_once()
        call_kwargs = mock_load_impl.call_args[1]
        assert "records" in call_kwargs

    @pytest.mark.asyncio
    async def test_load_data_skips_schema_change_fields(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test that fields requiring schema changes are excluded from load."""
        base_state["field_mappings"]["company"] = {
            "name": {"dest_column": "name", "requires_schema_change": False},
            "phone": {"dest_column": "phone", "requires_schema_change": True},
        }

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            await load_data_node(base_state)

        mock_load_impl.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_data_multiple_entities_in_order(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """Test loading multiple entities in extraction order."""
        base_state["extraction_order"] = ["company", "person"]
        base_state["unified_entities"]["person"] = [
            {"id": "cluster-3", "name": "John Doe"},
        ]
        base_state["field_mappings"]["person"] = {
            "name": {"dest_column": "name", "requires_schema_change": False},
        }

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            await load_data_node(base_state)

        assert mock_load_impl.call_count == 2


class TestSchemaRollbackAndEscalation:
    """Fix #4 (Phase 4.5): schema ALTERs join the load's own transaction and
    roll back with the data instead of committing on a second, independent
    connection. Fix #6 (Phase 4.6): a rolled-back load populates
    ``state['failure']`` so the existing _fallible_router/NODE_SUCCESSOR
    machinery in graph.py escalates to the human error gate instead of
    flowing on silently to update_extraction_state."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state (mirrors TestLoadDataNode.base_state)."""
        return {
            "user_request": "Import all companies",
            "target_entities": ["company"],
            "extraction_order": ["company"],
            "unified_entities": {
                "company": [
                    {"id": "cluster-1", "name": "Acme Corp", "email": "info@acme.com"},
                    {"id": "cluster-2", "name": "Beta Inc", "email": "info@beta.com"},
                ],
            },
            "field_mappings": {
                "company": {
                    "name": {"dest_column": "name", "requires_schema_change": False},
                    "email": {"dest_column": "email", "requires_schema_change": False},
                },
            },
            "destination": {
                "type": "postgresql",
                "host": "localhost",
                "port": 5432,
                "tables": {"company": "merged_companies"},
            },
            "pending_schema_changes": [],
            "errors": [],
            "load_approval": True,
        }

    @pytest.fixture
    def mock_load_impl(self) -> AsyncMock:
        """Mock load_data_impl."""
        return AsyncMock(
            return_value={
                "total_records": 2,
                "inserted": 2,
                "updated": 0,
            }
        )

    @pytest.fixture
    def mock_connector(self) -> MagicMock:
        from contextlib import contextmanager

        connector = MagicMock()
        connector.list_tables = MagicMock(return_value=["merged_companies"])
        connector.extend_schema_batch = MagicMock(return_value={"added": 1, "failed": 0})
        connector.ensure_table = MagicMock()
        connector.close = MagicMock()
        # An int, not an auto-generated MagicMock — `saved += ...` in
        # _persist_entity_map would otherwise raise a TypeError unrelated to
        # what these tests are actually checking.
        connector.save_entity_map = MagicMock(return_value=0)

        transaction_log: list[str] = []
        connector.transaction_log = transaction_log

        @contextmanager
        def fake_atomic(dry_run: bool = False):
            transaction_log.append("enter")
            try:
                yield
            except Exception:
                transaction_log.append("rollback")
                raise
            else:
                transaction_log.append("commit")

        connector.atomic = fake_atomic
        return connector

    @pytest.mark.asyncio
    async def test_schema_change_is_refused_not_applied(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock, mock_connector: MagicMock
    ) -> None:
        """The destination schema is a fixed contract (Phase 0 Bug A): a
        pending schema change — however it got there — is never applied.
        It is recorded as an error, and the rest of the load (which does not
        depend on the missing column) still commits."""
        base_state["pending_schema_changes"] = [
            {"table": "merged_companies", "column": "phone", "data_type": "VARCHAR"}
        ]
        get_connector_mock = MagicMock(return_value=mock_connector)

        with (
            patch(
                "src.orchestrator.nodes.load_data.load_data_impl",
                mock_load_impl,
            ),
            patch(
                "src.orchestrator.nodes.load_data._get_destination_connector",
                get_connector_mock,
            ),
            patch(
                "src.mcp_server.tools.load_data._get_destination_connector",
                get_connector_mock,
            ),
        ):
            result = await load_data_node(base_state)

        mock_connector.extend_schema_batch.assert_not_called()
        assert any("fixed contract" in e for e in result["errors"])
        assert "commit" in mock_connector.transaction_log
        assert "rollback" not in mock_connector.transaction_log

    @pytest.mark.asyncio
    async def test_refused_schema_change_does_not_block_a_failed_loads_rollback(
        self, base_state: dict[str, Any], mock_connector: MagicMock
    ) -> None:
        """A refused schema change is just an error entry, not an exception —
        it must not mask or interfere with a genuine load failure rolling
        back the transaction."""
        base_state["pending_schema_changes"] = [
            {"table": "merged_companies", "column": "phone", "data_type": "VARCHAR"}
        ]

        async def fail_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise Exception("destination rejected the batch")

        get_connector_mock = MagicMock(return_value=mock_connector)

        with (
            patch(
                "src.orchestrator.nodes.load_data.load_data_impl",
                side_effect=fail_impl,
            ),
            patch(
                "src.orchestrator.nodes.load_data._get_destination_connector",
                get_connector_mock,
            ),
            patch(
                "src.mcp_server.tools.load_data._get_destination_connector",
                get_connector_mock,
            ),
        ):
            result = await load_data_node(base_state)

        mock_connector.extend_schema_batch.assert_not_called()
        assert any("fixed contract" in e for e in result["errors"])
        assert "rollback" in mock_connector.transaction_log
        assert "commit" not in mock_connector.transaction_log
        assert result["reconciliation_report"]["load_rolled_back"] is True

    @pytest.mark.asyncio
    async def test_rolled_back_load_sets_an_escalating_failure(
        self, base_state: dict[str, Any]
    ) -> None:
        """A rolled-back load must populate state['failure'] with an
        escalating disposition so the run stops at the human error gate
        instead of flowing on to update_extraction_state as if nothing
        had happened."""

        async def fail_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise Exception("Load service error")

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            side_effect=fail_impl,
        ):
            result = await load_data_node(base_state)

        assert result["failure"]
        assert result["failure"]["node"] == "load_data"
        assert result["failure"]["error_class"] == "DataQualityError"
        assert result["failure"]["disposition"] == "escalate"
        assert result["reconciliation_report"]["load_rolled_back"] is True

    @pytest.mark.asyncio
    async def test_successful_load_sets_no_failure(
        self, base_state: dict[str, Any], mock_load_impl: AsyncMock
    ) -> None:
        """A clean run must not populate 'failure' — only genuine rollbacks
        may escalate, or every successful run would also route to
        handle_error."""
        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        assert result["failure"] == {}
        assert result["reconciliation_report"]["load_rolled_back"] is False


class TestPreLoadGuardrail:
    """Phase 6.2, at the node level: a scoped merge whose subject entity
    formed several clusters with zero auto-merges must be refused BEFORE any
    connector is opened — not just detected after data has already loaded."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        return {
            "user_request": "Merge everything about Acme",
            "target_entities": ["company"],
            "extraction_order": ["company"],
            "merge_scope": {"active": True, "subject": "Acme", "scope_type": "company"},
            "entity_clusters": {
                "company": [
                    {"cluster_id": f"c{i}", "record_ids": ["a"], "required_human_review": False}
                    for i in range(6)
                ],
            },
            "unified_entities": {
                "company": [{"id": f"cluster-{i}", "name": f"Acme {i}"} for i in range(6)],
            },
            "field_mappings": {
                "company": {"name": {"dest_column": "name", "requires_schema_change": False}},
            },
            "destination": {"type": "postgresql", "host": "localhost", "tables": {}},
            "pending_schema_changes": [],
            "errors": [],
            "load_approval": True,
        }

    @pytest.mark.asyncio
    async def test_guardrail_refuses_the_load_before_touching_a_connector(
        self,
        base_state: dict[str, Any],
    ) -> None:
        mock_load_impl = AsyncMock(return_value={"total_records": 1, "inserted": 1, "updated": 0})
        mock_get_connector = MagicMock()

        with (
            patch(
                "src.orchestrator.nodes.load_data.load_data_impl",
                mock_load_impl,
            ),
            patch(
                "src.orchestrator.nodes.load_data._get_destination_connector",
                mock_get_connector,
            ),
        ):
            result = await load_data_node(base_state)

        mock_load_impl.assert_not_called()
        mock_get_connector.assert_not_called()
        assert result["failure"]["error_class"] == "DataQualityError"
        assert result["failure"]["disposition"] == "escalate"
        assert result["reconciliation_report"]["guardrail_fired"] is True

    @pytest.mark.asyncio
    async def test_a_healthy_scoped_merge_is_not_blocked(
        self,
        base_state: dict[str, Any],
    ) -> None:
        """Only the zero-auto-merge signature fires the guardrail — a scoped
        merge where clusters DID auto-merge must load normally."""
        base_state["entity_clusters"]["company"][0]["record_ids"] = ["a", "b"]
        mock_load_impl = AsyncMock(return_value={"total_records": 6, "inserted": 6, "updated": 0})

        with patch(
            "src.orchestrator.nodes.load_data.load_data_impl",
            mock_load_impl,
        ):
            result = await load_data_node(base_state)

        mock_load_impl.assert_called_once()
        assert result["failure"] == {}


class TestUpdateExtractionStateNode:
    """Tests for update_extraction_state_node.

    Origins are read-only; incremental extraction state is tracked in the
    destination's merge_engine_extraction_state table (one UPSERT per
    tracked table, not one call per extracted row).
    """

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state with observed watermarks from extraction."""
        return {
            "run_id": "wf_test123",
            "destination": {"type": "postgresql", "host": "localhost", "database": "main_dest"},
            "extraction_watermarks": {
                "postgres_main:companies": {
                    "origin_id": "postgres_main",
                    "table_name": "companies",
                    "watermark_column": "id",
                    "watermark_kind": "numeric",
                    "last_value": "150",
                    "rows_extracted": 150,
                },
                "mongo_analytics:leads": {
                    "origin_id": "mongo_analytics",
                    "table_name": "leads",
                    "watermark_column": "created_at",
                    "watermark_kind": "timestamp",
                    "last_value": "2026-07-19T00:00:00",
                    "rows_extracted": 42,
                },
            },
            "errors": [],
        }

    @pytest.mark.asyncio
    async def test_updates_extraction_state_success(self, base_state: dict[str, Any]) -> None:
        """Test successful extraction-state updates."""
        mock_connector = MagicMock()
        mock_connector.update_extraction_state = MagicMock()

        with patch(
            "src.orchestrator.nodes.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            result = await update_extraction_state_node(base_state)

        assert not result["errors"]
        assert mock_connector.update_extraction_state.call_count == 2  # once per tracked table

    @pytest.mark.asyncio
    async def test_passes_run_id_and_watermark_fields(self, base_state: dict[str, Any]) -> None:
        """Test that run_id and watermark fields are passed through."""
        mock_connector = MagicMock()
        mock_connector.update_extraction_state = MagicMock()

        with patch(
            "src.orchestrator.nodes.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            await update_extraction_state_node(base_state)

        calls = mock_connector.update_extraction_state.call_args_list
        kwargs_by_origin = {c.kwargs["origin_id"]: c.kwargs for c in calls}
        assert kwargs_by_origin["postgres_main"]["run_id"] == "wf_test123"
        assert kwargs_by_origin["postgres_main"]["last_value"] == "150"
        assert kwargs_by_origin["postgres_main"]["rows_extracted"] == 150

    @pytest.mark.asyncio
    async def test_handles_update_failure(self, base_state: dict[str, Any]) -> None:
        """Test handling of an update_extraction_state failure."""
        mock_connector = MagicMock()
        mock_connector.update_extraction_state = MagicMock(side_effect=Exception("write failed"))

        with patch(
            "src.orchestrator.nodes.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            result = await update_extraction_state_node(base_state)

        assert len(result["errors"]) >= 1
        assert any("Failed to update extraction state" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_preserves_existing_errors(self, base_state: dict[str, Any]) -> None:
        """Existing errors survive a call to this node via MergeState's real
        operator.add reducer, not because the node re-returns the old
        history itself (Phase 0 Sec 0.8 - see analyze_dependencies' test of
        the same name for why the previous version of this test asserted
        the actual bug)."""
        base_state["errors"] = ["pre-existing"]
        mock_connector = MagicMock()
        mock_connector.update_extraction_state = MagicMock()

        with patch(
            "src.orchestrator.nodes.load_data._get_destination_connector",
            return_value=mock_connector,
        ):
            result = await update_extraction_state_node(base_state)

        assert "pre-existing" not in result.get("errors", [])
        combined = base_state["errors"] + result.get("errors", [])
        assert "pre-existing" in combined

    @pytest.mark.asyncio
    async def test_no_watermarks_skips_connector_creation(self, base_state: dict[str, Any]) -> None:
        """Test that no connector is created when nothing was observed."""
        base_state["extraction_watermarks"] = {}

        with patch(
            "src.orchestrator.nodes.load_data._get_destination_connector",
        ) as mock_create:
            result = await update_extraction_state_node(base_state)

        assert not result["errors"]
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_connector_creation_failure_recorded(self, base_state: dict[str, Any]) -> None:
        """Test that a destination-connector creation failure is recorded."""
        with patch(
            "src.orchestrator.nodes.load_data._get_destination_connector",
            side_effect=Exception("bad destination config"),
        ):
            result = await update_extraction_state_node(base_state)

        assert len(result["errors"]) >= 1


class TestForeignKeyTranslation:
    """Fix #7: a child row's FK columns carry raw source ids, but the parent
    row lands under its cluster id — untranslated, Postgres rejects the whole
    atomic load with "Key (company_a_id)=(...) is not present in table company".
    """

    def test_source_id_to_cluster_id_indexes_both_full_and_bare_ids(self) -> None:
        state = {
            "entity_clusters": {
                "company": [
                    {"cluster_id": "cid-1", "record_ids": ["o1:Company:ABC", "o2:Company:XYZ"]},
                ]
            }
        }
        lookup = _source_id_to_cluster_id(state, "company")
        assert lookup["o1:Company:ABC"] == "cid-1"
        assert lookup["ABC"] == "cid-1"
        assert lookup["XYZ"] == "cid-1"

    def test_cluster_without_id_is_skipped(self) -> None:
        state = {
            "entity_clusters": {"company": [{"cluster_id": "", "record_ids": ["o1:Company:ABC"]}]}
        }
        assert _source_id_to_cluster_id(state, "company") == {}

    def test_fk_is_rewritten_to_the_parents_cluster_id(self) -> None:
        records = [{"company_a_id": "ABC", "relation_type": "subsidiary"}]
        translated = _translate_foreign_keys(
            records,
            {"company_a_id": "company"},
            {"company": {"ABC": "cid-1"}},
        )
        assert translated == 1
        assert records[0]["company_a_id"] == "cid-1"

    def test_unresolvable_fk_is_nulled_not_left_dangling(self) -> None:
        # Keeping the row is right; keeping a link to a row that was never
        # written is not — it would abort the entire load.
        records = [{"company_a_id": "GHOST"}]
        translated = _translate_foreign_keys(
            records, {"company_a_id": "company"}, {"company": {"ABC": "cid-1"}}
        )
        assert translated == 1
        assert records[0]["company_a_id"] is None

    def test_null_and_empty_fks_are_left_alone(self) -> None:
        records = [{"company_a_id": None, "company_b_id": ""}]
        translated = _translate_foreign_keys(
            records,
            {"company_a_id": "company", "company_b_id": "company"},
            {"company": {"ABC": "cid-1"}},
        )
        assert translated == 0
        assert records[0]["company_a_id"] is None
        assert records[0]["company_b_id"] == ""

    def test_already_correct_fk_is_not_counted_as_translated(self) -> None:
        records = [{"company_a_id": "cid-1"}]
        translated = _translate_foreign_keys(
            records, {"company_a_id": "company"}, {"company": {"cid-1": "cid-1"}}
        )
        assert translated == 0
        assert records[0]["company_a_id"] == "cid-1"

    def test_missing_lookup_for_referenced_table_leaves_value_untouched(self) -> None:
        # No cluster data for the parent at all: nulling every FK would be
        # worse than leaving it for the DB to judge.
        records = [{"company_a_id": "ABC"}]
        translated = _translate_foreign_keys(records, {"company_a_id": "company"}, {})
        assert translated == 0
        assert records[0]["company_a_id"] == "ABC"
