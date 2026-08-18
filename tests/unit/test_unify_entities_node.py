"""Unit tests for unify_entities node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.orchestrator.nodes.unify_entities import unify_entity_groups
from src.orchestrator.spool import unified_for


@pytest.fixture(autouse=True)
def _spool_isolation(tmp_path, monkeypatch):
    """Each test gets its own spool: clusters and unified records live there."""
    import src.orchestrator.spool as spool_mod

    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    yield


class TestUnifyEntityGroups:
    """Tests for the unify_entity_groups node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all people and companies",
            "target_entities": ["company"],
            "extracted_data": {
                "company": [
                    {
                        "id": 1,
                        "name": "Acme Corp",
                        "email": "info@acme.com",
                        "_origin_id": "postgres_main",
                        "_origin_table": "companies",
                        "_origin_id_row": "1",
                    },
                    {
                        "id": 2,
                        "name": "Acme Corporation",
                        "email": "contact@acme.com",
                        "_origin_id": "mongo_analytics",
                        "_origin_table": "companies",
                        "_origin_id_row": "2",
                    },
                ],
            },
            "entity_clusters": {
                "company": [
                    {
                        "cluster_id": "cluster-1",
                        "record_ids": [
                            "postgres_main:companies:1",
                            "mongo_analytics:companies:2",
                        ],
                    },
                ],
            },
            "unified_entities": {},
            "errors": [],
        }

    @pytest.fixture
    def mock_unify_impl(self) -> AsyncMock:
        """Mock unify_entity_group_impl."""
        return AsyncMock(
            return_value={
                "unified_record": {
                    "name": "Acme Corp",
                    "email": "info@acme.com",
                    "sources": ["postgres_main", "mongo_analytics"],
                },
                "conflicts": [
                    {
                        "field": "name",
                        "values": ["Acme Corp", "Acme Corporation"],
                        "resolution": "first",
                    }
                ],
                "null_filled_fields": ["phone"],
            }
        )

    @pytest.mark.asyncio
    async def test_unify_entity_groups_success(
        self, base_state: dict[str, Any], mock_unify_impl: AsyncMock
    ) -> None:
        """Test successful entity unification."""
        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            mock_unify_impl,
        ):
            result = await unify_entity_groups(base_state)

        assert "company" in result["unified_entities"]
        assert len(unified_for(result, "company")) == 1
        unified = unified_for(result, "company")[0]
        assert unified["id"] == "cluster-1"
        assert unified["_entity_type"] == "company"
        assert not result["errors"]

    @pytest.mark.asyncio
    async def test_measured_field_reliability_is_computed_and_passed_through(
        self, base_state: dict[str, Any], mock_unify_impl: AsyncMock
    ) -> None:
        """Phase A6: field_reliability must be measured ONCE from the
        entity's full record set (not per-cluster -- a single cluster is
        usually too small a sample) and threaded into
        unify_entity_group_impl as a real kwarg, not silently dropped."""
        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            mock_unify_impl,
        ):
            await unify_entity_groups(base_state)

        call_kwargs = mock_unify_impl.call_args.kwargs
        assert "field_reliability" in call_kwargs
        reliability = call_kwargs["field_reliability"]
        # Both origins in base_state's fixture are 100% complete for both
        # fields they carry -- computed from the SAME extracted_data the
        # node already had, not re-fetched or faked.
        assert reliability["postgres_main"]["name"] == 1.0
        assert reliability["mongo_analytics"]["email"] == 1.0

    @pytest.mark.asyncio
    async def test_unify_entity_groups_sets_real_source_ids(
        self, base_state: dict[str, Any], mock_unify_impl: AsyncMock
    ) -> None:
        """Phase 7.2: _source_ids must be the cluster's real member record
        ids, not load_data.py's self-referencing {"cluster_id": ...}
        fallback -- that fallback only ever fires when this key is absent."""
        import json

        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            mock_unify_impl,
        ):
            result = await unify_entity_groups(base_state)

        unified = unified_for(result, "company")[0]
        assert json.loads(unified["_source_ids"]) == [
            "postgres_main:companies:1",
            "mongo_analytics:companies:2",
        ]

    @pytest.mark.asyncio
    async def test_unify_entity_groups_assigns_uuid(
        self, base_state: dict[str, Any], mock_unify_impl: AsyncMock
    ) -> None:
        """Test that unified record gets cluster_id as UUID."""
        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            mock_unify_impl,
        ):
            result = await unify_entity_groups(base_state)

        unified = unified_for(result, "company")[0]
        assert unified["id"] == "cluster-1"

    @pytest.mark.asyncio
    async def test_unify_entity_groups_adds_conflict_metadata(
        self, base_state: dict[str, Any], mock_unify_impl: AsyncMock
    ) -> None:
        """Test that conflicts are added as metadata."""
        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            mock_unify_impl,
        ):
            result = await unify_entity_groups(base_state)

        unified = unified_for(result, "company")[0]
        assert "_conflicts" in unified
        assert len(unified["_conflicts"]) == 1

    @pytest.mark.asyncio
    async def test_unify_entity_groups_adds_null_filled_metadata(
        self, base_state: dict[str, Any], mock_unify_impl: AsyncMock
    ) -> None:
        """Test that null-filled fields are added as metadata."""
        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            mock_unify_impl,
        ):
            result = await unify_entity_groups(base_state)

        unified = unified_for(result, "company")[0]
        assert "_null_filled" in unified
        assert "phone" in unified["_null_filled"]

    @pytest.mark.asyncio
    async def test_unify_entity_groups_skips_cluster_with_no_records(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test that clusters with no matching records are skipped."""
        base_state["entity_clusters"]["company"][0]["record_ids"] = ["nonexistent:table:1"]

        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            AsyncMock(
                return_value={
                    "unified_record": {},
                    "conflicts": [],
                    "null_filled_fields": [],
                }
            ),
        ) as mock_impl:
            result = await unify_entity_groups(base_state)

        # Cluster skipped because no matching records found
        assert len(unified_for(result, "company")) == 0
        mock_impl.assert_not_called()

    @pytest.mark.asyncio
    async def test_unify_entity_groups_unification_failure(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test handling of unification failure."""

        async def failing_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise Exception("Unification service error")

        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            side_effect=failing_impl,
        ):
            result = await unify_entity_groups(base_state)

        assert unified_for(result, "company") == []
        assert len(result["errors"]) == 1
        assert "Unify failed" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_unify_entity_groups_preserves_existing_errors(
        self, base_state: dict[str, Any], mock_unify_impl: AsyncMock
    ) -> None:
        """Existing errors survive a call to this node via MergeState's real
        operator.add reducer, not because the node re-returns the old
        history itself (Phase 0 Sec 0.8 - see analyze_dependencies' test of
        the same name for why the previous version of this test asserted
        the actual bug)."""
        base_state["errors"] = ["pre-existing error"]

        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            mock_unify_impl,
        ):
            result = await unify_entity_groups(base_state)

        assert "pre-existing error" not in result.get("errors", [])
        combined = base_state["errors"] + result.get("errors", [])
        assert "pre-existing error" in combined

    @pytest.mark.asyncio
    async def test_unify_entity_groups_no_records_in_cluster(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test cluster with empty record_ids."""
        base_state["entity_clusters"]["company"][0]["record_ids"] = []

        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            AsyncMock(
                return_value={
                    "unified_record": {},
                    "conflicts": [],
                    "null_filled_fields": [],
                }
            ),
        ) as mock_impl:
            result = await unify_entity_groups(base_state)

        assert len(unified_for(result, "company")) == 0
        mock_impl.assert_not_called()

    @pytest.mark.asyncio
    async def test_unify_entity_groups_multiple_clusters(self, base_state: dict[str, Any]) -> None:
        """Test multiple clusters for the same entity."""
        base_state["entity_clusters"]["company"] = [
            {
                "cluster_id": "cluster-1",
                "record_ids": ["postgres_main:companies:1"],
            },
            {
                "cluster_id": "cluster-2",
                "record_ids": ["mongo_analytics:companies:2"],
            },
        ]
        # Also add matching records
        base_state["extracted_data"]["company"].append(
            {
                "id": 2,
                "name": "Beta Inc",
                "_origin_id": "mongo_analytics",
                "_origin_table": "companies",
                "_origin_id_row": "2",
            }
        )

        mock_call_count = [0]

        async def mock_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            mock_call_count[0] += 1
            return {
                "unified_record": {"name": f"Record {mock_call_count[0]}"},
                "conflicts": [],
                "null_filled_fields": [],
            }

        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            side_effect=mock_impl,
        ):
            result = await unify_entity_groups(base_state)

        assert len(unified_for(result, "company")) == 2

    @pytest.mark.asyncio
    async def test_unify_entity_groups_no_target_entities(self, base_state: dict[str, Any]) -> None:
        """Test with empty target_entities."""
        base_state["target_entities"] = []

        result = await unify_entity_groups(base_state)
        assert result["unified_entities"] == {}

    @pytest.mark.asyncio
    async def test_unify_entity_groups_entity_no_clusters(self, base_state: dict[str, Any]) -> None:
        """Test entity with no clusters produces empty unified list."""
        base_state["entity_clusters"]["company"] = []

        result = await unify_entity_groups(base_state)
        assert unified_for(result, "company") == []

    @pytest.mark.asyncio
    async def test_unify_entity_groups_record_lookup_by_origin_id(
        self, base_state: dict[str, Any], mock_unify_impl: AsyncMock
    ) -> None:
        """Test that records are looked up using origin_id-based keys."""
        with patch(
            "src.orchestrator.nodes.unify_entities.unify_entity_group_impl",
            mock_unify_impl,
        ):
            await unify_entity_groups(base_state)

        # Verify the mock was called with the correct records
        mock_unify_impl.assert_called_once()
        call_kwargs = mock_unify_impl.call_args
        assert len(call_kwargs[1]["records"]) == 2
