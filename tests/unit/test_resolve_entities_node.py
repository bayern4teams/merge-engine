"""Unit tests for resolve_entities node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.orchestrator.nodes.resolve_entities import has_low_confidence_clusters, resolve_entities
from src.orchestrator.spool import clusters_for


@pytest.fixture(autouse=True)
def _spool_isolation(tmp_path, monkeypatch):
    """Each test gets its own spool: clusters and unified records live there."""
    import src.orchestrator.spool as spool_mod

    monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
    yield


class TestResolveEntities:
    """Tests for the resolve_entities node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all people and companies",
            "target_entities": ["company", "person"],
            "extracted_data": {
                "company": [
                    {"id": 1, "name": "Acme Corp", "email": "info@acme.com"},
                    {"id": 2, "name": "Acme Corporation", "email": "contact@acme.com"},
                    {"id": 3, "name": "Beta Inc", "email": "info@beta.com"},
                ],
                "person": [
                    {"id": 1, "name": "John Doe", "email": "john@example.com"},
                    {"id": 2, "name": "Jane Smith", "email": "jane@example.com"},
                ],
            },
            "entity_clusters": {},
            "errors": [],
        }

    @pytest.fixture
    def mock_resolve_impl(self) -> AsyncMock:
        """Mock resolve_entities_impl."""
        return AsyncMock(
            return_value={
                "clusters": [
                    {
                        "cluster_id": "cluster-1",
                        "record_ids": ["postgres_main:companies:1", "mongo_analytics:companies:2"],
                        "match_confidence": 0.85,
                        "required_human_review": False,
                    },
                    {
                        "cluster_id": "cluster-2",
                        "record_ids": ["postgres_main:companies:3"],
                        "match_confidence": 1.0,
                        "required_human_review": False,
                    },
                ],
                "total_records": 3,
                "auto_matched": 1,
                "llm_scored": 1,
            }
        )

    @pytest.mark.asyncio
    async def test_resolve_entities_success(
        self, base_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        """Test successful entity resolution."""
        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_resolve_impl,
        ):
            result = await resolve_entities(base_state)

        assert "company" in result["entity_clusters"]
        assert len(clusters_for(result, "company")) == 2
        assert not result["errors"]

    @pytest.mark.asyncio
    async def test_comparison_budget_truncation_surfaces_as_quality_report(
        self, base_state: dict[str, Any]
    ) -> None:
        """Regression: uncompared_pairs was only ever a log line and a JSON
        field no UI rendered. It must now reach quality_report/
        has_quality_failure -- the same keys the final-approval gate
        already surfaces red flags from."""
        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            AsyncMock(
                return_value={
                    "clusters": [],
                    "total_records": 3,
                    "auto_matched": 0,
                    "llm_scored": 0,
                    "uncompared_pairs": 2_404_984,
                }
            ),
        ):
            result = await resolve_entities(base_state)

        report = result["quality_report"]
        assert report["severity"] == "warn"
        company_checks = report["entities"]["company"]["checks"]
        assert any(c["check"] == "comparison_budget" for c in company_checks)
        assert (
            "2404984" in company_checks[-1]["details"]
            or "2,404,984" in company_checks[-1]["details"]
        )
        # A "warn"-only finding must not force has_quality_failure -- only a
        # hard "fail" (validate_data's own domain) does that.
        assert result["has_quality_failure"] is False

    @pytest.mark.asyncio
    async def test_comparison_budget_truncation_never_clobbers_validate_data(
        self, base_state: dict[str, Any]
    ) -> None:
        """validate_data runs immediately before this node and already set
        quality_report/has_quality_failure -- neither has a LangGraph
        reducer, so a plain return here would silently discard
        validate_data's own findings for an unrelated entity."""
        base_state["quality_report"] = {
            "status": "completed",
            "severity": "fail",
            "entities": {"person": {"severity": "fail", "row_count": 0, "checks": []}},
        }
        base_state["has_quality_failure"] = True

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            AsyncMock(
                return_value={
                    "clusters": [],
                    "total_records": 3,
                    "auto_matched": 0,
                    "llm_scored": 0,
                    "uncompared_pairs": 500,
                }
            ),
        ):
            result = await resolve_entities(base_state)

        report = result["quality_report"]
        # person's pre-existing "fail" from validate_data must survive...
        assert report["entities"]["person"]["severity"] == "fail"
        # ...alongside company's new budget-truncation finding...
        assert report["entities"]["company"]["severity"] == "warn"
        # ...and the overall severity stays "fail" (the harder finding wins).
        assert report["severity"] == "fail"
        assert result["has_quality_failure"] is True

    @pytest.mark.asyncio
    async def test_no_truncation_leaves_quality_report_untouched(
        self, base_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        """No uncompared pairs -> this node must not even mention
        quality_report in its return dict, so LangGraph's per-key state
        update leaves whatever validate_data already set completely alone."""
        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_resolve_impl,
        ):
            result = await resolve_entities(base_state)

        assert "quality_report" not in result
        assert "has_quality_failure" not in result

    @pytest.mark.asyncio
    async def test_a_binding_pair_guard_fails_the_run(self, base_state: dict[str, Any]) -> None:
        """P2: the out-of-memory guard must stop the run, not just one entity —
        an entity left unresolved would let the run end `completed`."""
        from src.mcp_server.tools.resolve_entities import CandidatePairBudgetExceeded

        with (
            patch(
                "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
                AsyncMock(side_effect=CandidatePairBudgetExceeded("too many pairs")),
            ),
            pytest.raises(CandidatePairBudgetExceeded),
        ):
            await resolve_entities(base_state)

    @pytest.mark.asyncio
    async def test_resolve_entities_no_records_skipped(self, base_state: dict[str, Any]) -> None:
        """Test that entities with no records are skipped."""
        base_state["extracted_data"]["person"] = []

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            AsyncMock(
                return_value={
                    "clusters": [],
                    "total_records": 0,
                    "auto_matched": 0,
                    "llm_scored": 0,
                }
            ),
        ) as mock_impl:
            result = await resolve_entities(base_state)

        # person has no records, so it's set to [] and impl is not called for it
        assert clusters_for(result, "person") == []
        # company has records so impl IS called once for company
        assert mock_impl.call_count == 1
        # Verify person was not passed to impl (only company was)
        call_kwargs = mock_impl.call_args[1]
        assert call_kwargs["entity_type"] == "company"

    @pytest.mark.asyncio
    async def test_resolve_entities_resolution_failure(self, base_state: dict[str, Any]) -> None:
        """Test handling of resolution failure for one entity."""

        async def failing_impl(*args: Any, **kwargs: Any) -> dict[str, Any]:
            if kwargs.get("entity_type") == "person":
                raise Exception("Resolution service unavailable")
            return {"clusters": [], "total_records": 0, "auto_matched": 0, "llm_scored": 0}

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            side_effect=failing_impl,
        ):
            result = await resolve_entities(base_state)

        assert clusters_for(result, "company") == []
        assert clusters_for(result, "person") == []
        assert len(result["errors"]) == 1
        assert "person" in result["errors"][0]
        assert "failed" in result["errors"][0].lower()

    @pytest.mark.asyncio
    async def test_resolve_entities_preserves_existing_errors(
        self, base_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        """Existing errors survive a call to this node via MergeState's real
        operator.add reducer, not because the node re-returns the old
        history itself (Phase 0 Sec 0.8 - see analyze_dependencies' test of
        the same name for why the previous version of this test asserted
        the actual bug)."""
        base_state["errors"] = ["pre-existing error"]

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_resolve_impl,
        ):
            result = await resolve_entities(base_state)

        assert "pre-existing error" not in result.get("errors", [])
        combined = base_state["errors"] + result.get("errors", [])
        assert "pre-existing error" in combined

    @pytest.mark.asyncio
    async def test_resolve_entities_empty_target_entities(self, base_state: dict[str, Any]) -> None:
        """Test with no target entities."""
        base_state["target_entities"] = []
        base_state["extracted_data"] = {}

        result = await resolve_entities(base_state)
        assert result["entity_clusters"] == {}
        assert not result["errors"]

    @pytest.mark.asyncio
    async def test_resolve_entities_multiple_clusters_per_entity(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test resolution producing multiple clusters."""
        mock_multi = AsyncMock(
            return_value={
                "clusters": [
                    {
                        "cluster_id": "c1",
                        "record_ids": ["r1", "r2"],
                        "match_confidence": 0.9,
                        "required_human_review": False,
                    },
                    {
                        "cluster_id": "c2",
                        "record_ids": ["r3"],
                        "match_confidence": 1.0,
                        "required_human_review": False,
                    },
                    {
                        "cluster_id": "c3",
                        "record_ids": ["r4", "r5"],
                        "match_confidence": 0.5,
                        "required_human_review": True,
                    },
                ],
                "total_records": 5,
                "auto_matched": 2,
                "llm_scored": 2,
            }
        )

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_multi,
        ):
            result = await resolve_entities(base_state)

        assert len(clusters_for(result, "company")) == 3

    @pytest.mark.asyncio
    async def test_resolve_entities_stats_logged(
        self, base_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        """Test that resolution stats are correctly captured."""
        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_resolve_impl,
        ):
            result = await resolve_entities(base_state)

        clusters = clusters_for(result, "company")
        assert len(clusters) == 2

    @pytest.mark.asyncio
    async def test_resolve_entities_only_single_record_clusters(
        self, base_state: dict[str, Any]
    ) -> None:
        """Test resolution with only single-record clusters."""
        mock_single = AsyncMock(
            return_value={
                "clusters": [
                    {
                        "cluster_id": "c1",
                        "record_ids": ["r1"],
                        "match_confidence": 1.0,
                        "required_human_review": False,
                    },
                    {
                        "cluster_id": "c2",
                        "record_ids": ["r2"],
                        "match_confidence": 1.0,
                        "required_human_review": False,
                    },
                ],
                "total_records": 2,
                "auto_matched": 2,
                "llm_scored": 0,
            }
        )

        with patch(
            "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
            mock_single,
        ):
            result = await resolve_entities(base_state)

        assert len(clusters_for(result, "company")) == 2

    @pytest.mark.asyncio
    async def test_resolve_entities_with_no_records_key(self, base_state: dict[str, Any]) -> None:
        """Test when extracted_data doesn't have entity type key."""
        base_state["extracted_data"] = {}

        result = await resolve_entities(base_state)
        assert clusters_for(result, "company") == []
        assert clusters_for(result, "person") == []


class TestProcessPoolDispatch:
    """Unit tests for Part 1.5 B.3's pool-creation gating in the node.

    ProcessPoolExecutor itself is mocked throughout -- these tests assert
    *when* a pool is created and passed down, not the real multiprocessing
    behaviour (covered live in docs/verification/PROCESS_POOL_PROFILE.md).
    """

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        return {
            "target_entities": ["company", "person"],
            "extracted_data": {
                "company": [{"id": 1, "name": "Acme Corp"}, {"id": 2, "name": "Acme Corporation"}],
                "person": [{"id": 1, "name": "John Doe"}, {"id": 2, "name": "Jane Smith"}],
            },
            "entity_clusters": {},
            "errors": [],
        }

    @pytest.fixture
    def mock_resolve_impl(self) -> AsyncMock:
        return AsyncMock(
            return_value={
                "clusters": [
                    {
                        "cluster_id": "cluster-1",
                        "record_ids": ["o1:t:1"],
                        "match_confidence": 1.0,
                        "required_human_review": False,
                    }
                ],
                "total_records": 1,
                "auto_matched": 1,
                "llm_scored": 0,
            }
        )

    @pytest.fixture
    def two_large_entities_state(self) -> dict[str, Any]:
        big_a = [{"id": i, "name": f"Company {i}"} for i in range(600)]
        big_b = [{"id": i, "name": f"Org {i}"} for i in range(600)]
        return {
            "target_entities": ["company", "person"],
            "extracted_data": {"company": big_a, "person": big_b},
            "entity_clusters": {},
            "errors": [],
        }

    @pytest.fixture
    def one_large_one_small_state(self) -> dict[str, Any]:
        big = [{"id": i, "name": f"Company {i}"} for i in range(600)]
        small = [{"id": 1, "name": "Jane"}, {"id": 2, "name": "Jane S"}]
        return {
            "target_entities": ["company", "person"],
            "extracted_data": {"company": big, "person": small},
            "entity_clusters": {},
            "errors": [],
        }

    async def test_pool_created_when_two_entities_qualify(
        self, two_large_entities_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        with (
            patch(
                "src.orchestrator.nodes.resolve_entities.resolve_entities_impl", mock_resolve_impl
            ),
            patch("src.orchestrator.nodes.resolve_entities.ProcessPoolExecutor") as mock_pool_cls,
        ):
            mock_pool = mock_pool_cls.return_value
            await resolve_entities(two_large_entities_state)

        mock_pool_cls.assert_called_once()
        mock_pool.shutdown.assert_called_once_with(wait=True)
        # Every dispatched call was handed the pool instance.
        for call in mock_resolve_impl.call_args_list:
            assert call.kwargs["process_pool"] is mock_pool

    async def test_no_pool_when_only_one_entity_qualifies(
        self, one_large_one_small_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        with (
            patch(
                "src.orchestrator.nodes.resolve_entities.resolve_entities_impl", mock_resolve_impl
            ),
            patch("src.orchestrator.nodes.resolve_entities.ProcessPoolExecutor") as mock_pool_cls,
        ):
            await resolve_entities(one_large_one_small_state)

        mock_pool_cls.assert_not_called()
        for call in mock_resolve_impl.call_args_list:
            assert call.kwargs["process_pool"] is None

    async def test_no_pool_when_all_entities_small(
        self, base_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        with (
            patch(
                "src.orchestrator.nodes.resolve_entities.resolve_entities_impl", mock_resolve_impl
            ),
            patch("src.orchestrator.nodes.resolve_entities.ProcessPoolExecutor") as mock_pool_cls,
        ):
            await resolve_entities(base_state)

        mock_pool_cls.assert_not_called()

    async def test_pool_creation_failure_degrades_to_inline_for_every_entity(
        self, two_large_entities_state: dict[str, Any], mock_resolve_impl: AsyncMock
    ) -> None:
        with (
            patch(
                "src.orchestrator.nodes.resolve_entities.resolve_entities_impl", mock_resolve_impl
            ),
            patch(
                "src.orchestrator.nodes.resolve_entities.ProcessPoolExecutor",
                side_effect=RuntimeError("no worker processes available"),
            ),
        ):
            result = await resolve_entities(two_large_entities_state)

        # Must not crash the run -- both entities still resolved, inline.
        assert clusters_for(result, "company") != [] or clusters_for(result, "person") != []
        for call in mock_resolve_impl.call_args_list:
            assert call.kwargs["process_pool"] is None

    async def test_pool_always_shut_down_even_when_dispatch_raises(
        self, two_large_entities_state: dict[str, Any]
    ) -> None:
        async def raising_impl(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("worker crashed")

        with (
            patch(
                "src.orchestrator.nodes.resolve_entities.resolve_entities_impl",
                side_effect=raising_impl,
            ),
            patch("src.orchestrator.nodes.resolve_entities.ProcessPoolExecutor") as mock_pool_cls,
        ):
            mock_pool = mock_pool_cls.return_value
            result = await resolve_entities(two_large_entities_state)

        mock_pool.shutdown.assert_called_once_with(wait=True)
        # gather's return_exceptions=True means both entities' failures land
        # as per-entity errors, not an unhandled exception.
        assert len(result["errors"]) == 2


class TestHasLowConfidenceClusters:
    """Tests for the has_low_confidence_clusters conditional edge."""

    def test_true_with_low_confidence_multi_record_cluster(self) -> None:
        """Test returns True when there's a low-confidence multi-record cluster."""
        state = {
            "entity_clusters": {
                "company": [
                    {
                        "record_ids": ["r1", "r2"],
                        "required_human_review": True,
                        "cluster_id": "c1",
                    }
                ]
            }
        }
        assert has_low_confidence_clusters(state) is True

    def test_false_with_no_clusters(self) -> None:
        """Test returns False with no clusters."""
        state = {"entity_clusters": {}}
        assert has_low_confidence_clusters(state) is False

    def test_false_with_empty_entity_clusters(self) -> None:
        """Test returns False with empty entity cluster lists."""
        state = {
            "entity_clusters": {
                "company": [],
                "person": [],
            }
        }
        assert has_low_confidence_clusters(state) is False

    def test_false_with_single_record_cluster(self) -> None:
        """Test returns False even with required_human_review if single record."""
        state = {
            "entity_clusters": {
                "company": [
                    {
                        "record_ids": ["r1"],
                        "required_human_review": True,
                    }
                ]
            }
        }
        assert has_low_confidence_clusters(state) is False

    def test_false_without_required_human_review(self) -> None:
        """Test returns False when required_human_review is not set."""
        state = {
            "entity_clusters": {
                "company": [
                    {
                        "record_ids": ["r1", "r2"],
                        "required_human_review": False,
                    }
                ]
            }
        }
        assert has_low_confidence_clusters(state) is False

    def test_false_with_no_entity_clusters_key(self) -> None:
        """Test returns False with no entity_clusters key."""
        state = {}
        assert has_low_confidence_clusters(state) is False

    def test_true_with_multiple_entities_one_low_confidence(self) -> None:
        """Test returns True if any entity has low-confidence cluster."""
        state = {
            "entity_clusters": {
                "company": [
                    {
                        "record_ids": ["r1"],
                        "required_human_review": False,
                    }
                ],
                "person": [
                    {
                        "record_ids": ["r2", "r3"],
                        "required_human_review": True,
                    }
                ],
            }
        }
        assert has_low_confidence_clusters(state) is True
