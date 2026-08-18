"""Unit tests for src/orchestrator/nodes/validate_data.py (salvaged from
B3-quality-lineage, Phase 4.5 of the fix-bugs follow-up plan).
"""

from __future__ import annotations

import pytest

from src.orchestrator.graph import NODE_SUCCESSOR, _build_builder
from src.orchestrator.nodes.validate_data import validate_data_node
from src.orchestrator.spool import RunSpool, make_handle


class TestValidateDataNode:
    @pytest.mark.asyncio
    async def test_inline_row_list_is_validated_directly(self) -> None:
        """The pre-spool shape (a plain list, still used by most unit
        tests and any caller that hands rows in directly) is handled
        without ever touching a spool file."""
        state = {
            "extracted_data": {"company": [{"id": "c1", "name": "Boeing"}]},
            "matching_rules": {"company": {"unique_id_fields": ["id"]}},
        }
        result = await validate_data_node(state)
        assert "quality_report" in result
        assert "has_quality_failure" in result
        assert result["has_quality_failure"] is False
        assert result["quality_report"]["severity"] == "pass"
        assert result["quality_report"]["entities"]["company"]["row_count"] == 1

    @pytest.mark.asyncio
    async def test_spool_handle_reads_real_rows_not_the_handle_itself(
        self, tmp_path, monkeypatch
    ) -> None:
        """The critical fix vs the B3 branch this was salvaged from: B3's
        validate_data_node assumed extracted_data held row lists directly.
        Since the Phase N spool refactor, extracted_data[entity] is a
        handle dict — validating it verbatim (as B3's code would, via its
        isinstance(data, dict) fallback treating the WHOLE HANDLE as one
        "row") would check uniqueness against handle keys like "run_id"
        instead of any real data. This proves the real spooled rows are
        read instead."""
        import src.orchestrator.spool as spool_mod

        monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")

        run_id = "validate-data-spool-test"
        spool = RunSpool(run_id)
        spool.reset_entity("person")
        spool.append(
            "person",
            [
                {"id": "p1", "name": "Alice", "_origin_id": "o1", "_origin_table": "People"},
                {"id": "p2", "name": "Bob", "_origin_id": "o1", "_origin_table": "People"},
            ],
        )
        handle = make_handle(run_id, "person", 2)

        state = {
            "extracted_data": {"person": handle},
            "matching_rules": {"person": {"unique_id_fields": ["id"]}},
        }
        result = await validate_data_node(state)

        entity = result["quality_report"]["entities"]["person"]
        assert entity["row_count"] == 2
        # Real column names from the spooled rows, not handle keys like
        # "run_id"/"__spool__" -- proves real rows were read and checked.
        checks = {c["check"] for c in entity["checks"]}
        assert "uniqueness:id" in checks
        assert result["has_quality_failure"] is False

    @pytest.mark.asyncio
    async def test_spool_handle_with_zero_rows_fails_loudly(self, tmp_path, monkeypatch) -> None:
        """origin_2.Person / origin_2.PersonContact from the 4th live run:
        a scoped join path that silently resolved to nothing. The row_count
        must come from entity_row_count (exact, from the handle), not from
        sampling zero rows and shrugging."""
        import src.orchestrator.spool as spool_mod

        monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")

        run_id = "validate-data-empty-spool-test"
        spool = RunSpool(run_id)
        spool.reset_entity("person")  # created, but nothing ever appended
        handle = make_handle(run_id, "person", 0)

        state = {"extracted_data": {"person": handle}}
        result = await validate_data_node(state)

        assert result["has_quality_failure"] is True
        assert result["quality_report"]["entities"]["person"]["severity"] == "fail"

    @pytest.mark.asyncio
    async def test_large_entity_is_sampled_not_fully_read(self, tmp_path, monkeypatch) -> None:
        """Deep checks must never materialise a huge entity in memory --
        exactly the cost the spool refactor exists to avoid. The sample is
        capped well below a large row count, and the report says so."""
        import src.orchestrator.nodes.validate_data as validate_data_mod
        import src.orchestrator.spool as spool_mod

        monkeypatch.setattr(spool_mod, "SPOOL_DIR", tmp_path / "spool")
        monkeypatch.setattr(validate_data_mod, "_SAMPLE_SIZE", 10)

        run_id = "validate-data-sample-test"
        spool = RunSpool(run_id)
        spool.reset_entity("person")
        spool.append("person", [{"id": f"p{i}", "name": f"P{i}"} for i in range(50)])
        handle = make_handle(run_id, "person", 50)

        state = {"extracted_data": {"person": handle}}
        result = await validate_data_node(state)

        entity = result["quality_report"]["entities"]["person"]
        assert entity["row_count"] == 50
        assert entity["sample_size"] == 10
        assert entity["sampled"] is True

    @pytest.mark.asyncio
    async def test_no_extracted_data_is_a_clean_pass(self) -> None:
        result = await validate_data_node({})
        assert result["has_quality_failure"] is False
        assert result["quality_report"]["severity"] == "pass"

    @pytest.mark.asyncio
    async def test_never_sets_failure_even_on_fail_severity(self) -> None:
        """The never-auto-abort posture, proven directly: no matter how bad
        the quality report is, this node's return dict must never include a
        "failure" key -- that is the ONLY thing _fallible_router or the
        error-gate machinery would react to, and validate_data's edge in
        the graph is a plain (non-conditional) edge specifically because it
        must never be able to stop the run by itself."""
        state = {"extracted_data": {"person": []}}
        result = await validate_data_node(state)
        assert result["has_quality_failure"] is True  # a real fail severity
        assert "failure" not in result  # yet nothing here can abort the run


class TestValidateDataGraphPosition:
    def test_sits_between_extract_data_and_resolve_entities(self) -> None:
        assert NODE_SUCCESSOR.get("extract_data") == "validate_data"
        assert NODE_SUCCESSOR.get("validate_data") == "resolve_entities"

    def test_node_is_registered_in_the_graph(self) -> None:
        builder = _build_builder()
        assert "validate_data" in set(builder.nodes.keys())
