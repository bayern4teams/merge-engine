"""Phase M: error taxonomy, escalation routing, preflight, atomic loads.

The through-line of every test here is the same: the pipeline must never
finish quietly having done less than it claims. A retry that hides a real
fault, a partial table merged as a whole one, a half-written destination
reported as a clean merge — each one produces output a user would trust and
shouldn't.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.graph import (
    NODE_SUCCESSOR,
    _error_gate_router,
    _fallible_router,
    _is_transient,
    _map_fields_router,
    _resolve_entities_router,
)
from src.orchestrator.utils.retry import (
    DataQualityError,
    MergeEngineError,
    PermanentError,
    TransientError,
    UserInputError,
    classify_exception,
)


class TestErrorTaxonomy:
    """Every failure has to be sorted into retry / stop / ask a human."""

    def test_the_taxonomy_stays_runtime_errors(self) -> None:
        """Existing `except RuntimeError` handlers must keep working."""
        for cls in (TransientError, PermanentError, DataQualityError, UserInputError):
            assert issubclass(cls, MergeEngineError)
            assert issubclass(cls, RuntimeError)

    def test_dispositions_are_distinct(self) -> None:
        assert TransientError("x").disposition == "retry"
        assert PermanentError("x").disposition == "fail"
        assert DataQualityError("x").disposition == "escalate"
        assert UserInputError("x").disposition == "fail"
        assert TransientError("x").retryable is True
        assert PermanentError("x").retryable is False

    @pytest.mark.parametrize(
        "message",
        [
            "connection timed out",
            "server closed the connection unexpectedly",
            "Too many connections",
            "429 rate limit exceeded",
            "deadlock detected",
        ],
    )
    def test_recognises_retryable_driver_failures(self, message: str) -> None:
        assert isinstance(classify_exception(Exception(message)), TransientError)

    @pytest.mark.parametrize(
        "message",
        [
            "password authentication failed for user postgres",
            "permission denied for table person",
            'relation "widgets" does not exist',
            "syntax error at or near SELECT",
        ],
    )
    def test_recognises_unretryable_failures(self, message: str) -> None:
        assert isinstance(classify_exception(Exception(message)), PermanentError)

    def test_permission_denied_beats_connection_wording(self) -> None:
        """Bad credentials must not be retried just for saying "connection".

        Three retries on a credentials problem only delays the one message
        that would let the user fix it.
        """
        exc = Exception("could not connect: permission denied for database")

        assert isinstance(classify_exception(exc), PermanentError)

    def test_unknown_failures_are_not_retried(self) -> None:
        """An unrecognised error should be read and fixed, not papered over."""
        assert isinstance(classify_exception(ValueError("???")), PermanentError)

    def test_already_classified_errors_pass_through_untouched(self) -> None:
        original = DataQualityError("bad row", entity="person", rows_affected=7)

        assert classify_exception(original) is original
        assert original.context()["rows_affected"] == 7

    def test_retry_predicate_matches_the_taxonomy(self) -> None:
        assert _is_transient(Exception("connection reset by peer")) is True
        assert _is_transient(Exception("syntax error near FROM")) is False


class TestFallibleRouting:
    """A node that recorded a failure must not be allowed to continue."""

    def test_escalatable_failure_routes_to_the_error_gate(self) -> None:
        route = _fallible_router("extract_data", "resolve_entities")
        state = {
            "failure": {
                "node": "extract_data",
                "error": "boom",
                "disposition": "escalate",
            }
        }

        assert route(state) == "error"

    def test_unrecoverable_failure_ends_the_run_without_a_gate(self) -> None:
        """A dead database has no honest "skip" — don't offer one.

        Escalating an unreachable origin would present a gate whose only
        safe answer is abort, and whose "skip" button walks the user into a
        run that cannot possibly work.
        """
        route = _fallible_router("preflight", "parse_request")
        state = {
            "failure": {
                "node": "preflight",
                "error": "origin unreachable",
                "disposition": "fail",
            }
        }

        assert route(state) == "abort"

    def test_missing_disposition_escalates(self) -> None:
        """Defaulting to a human is safer than defaulting to ending the run."""
        route = _fallible_router("extract_data", "resolve_entities")

        assert route({"failure": {"node": "extract_data"}}) == "error"

    def test_clean_state_continues(self) -> None:
        route = _fallible_router("extract_data", "resolve_entities")

        assert route({"failure": {}}) == "continue"
        assert route({}) == "continue"

    def test_another_node_s_failure_is_not_this_node_s_problem(self) -> None:
        route = _fallible_router("extract_data", "resolve_entities")

        assert route({"failure": {"node": "map_fields"}}) == "continue"

    def test_error_check_precedes_the_review_decision(self) -> None:
        """A failed resolve must escalate, not walk into the matches gate."""
        state = {"failure": {"node": "resolve_entities", "error": "x"}}

        assert _resolve_entities_router(state) == "error"

    def test_map_fields_error_check_precedes_review(self) -> None:
        state = {"failure": {"node": "map_fields", "error": "x"}}

        assert _map_fields_router(state) == "error"

    def test_preflight_failure_is_declared_unrecoverable(self) -> None:
        """Wiring check: preflight must emit `fail`, not `escalate`.

        If it ever emits `escalate` the run pauses at the error gate with a
        skip button that leads nowhere good.
        """
        import inspect

        from src.orchestrator.nodes import preflight as pf

        source = inspect.getsource(pf.preflight)
        assert '"disposition": "fail"' in source

    def test_review_decision_still_applies_when_healthy(self) -> None:
        with patch("src.orchestrator.graph.has_low_confidence_clusters", return_value=True):
            assert _resolve_entities_router({"failure": {}}) == "review"
        with patch("src.orchestrator.graph.has_low_confidence_clusters", return_value=False):
            assert _resolve_entities_router({"failure": {}}) == "continue"


class TestErrorGateRouting:
    """Where the run goes after a human looks at the failure."""

    def test_retry_returns_to_the_failed_node(self) -> None:
        state = {
            "handled_failures": [{"node": "extract_data", "decision": "retry"}],
            "retry_counts": {},
        }

        assert _error_gate_router(state) == "extract_data"

    def test_skip_continues_at_the_successor(self) -> None:
        state = {"handled_failures": [{"node": "extract_data", "decision": "skip"}]}

        assert _error_gate_router(state) == NODE_SUCCESSOR["extract_data"]

    def test_skip_on_plan_scope_does_not_silently_downgrade_to_abort(self) -> None:
        """Phase 8.1 regression: NODE_SUCCESSOR must cover plan_scope.

        Before this entry existed, `failed_node in NODE_SUCCESSOR` was False
        for "plan_scope", so a user choosing "skip" for a plan_scope failure
        silently fell through to "abort" instead — the exact "guessing
        carry-on is unsafe, but so is silently overriding an explicit skip"
        failure mode this whole router exists to prevent.
        """
        state = {"handled_failures": [{"node": "plan_scope", "decision": "skip"}]}

        assert _error_gate_router(state) == "extract_data"

    def test_abort_ends_the_run(self) -> None:
        state = {"handled_failures": [{"node": "extract_data", "decision": "abort"}]}

        assert _error_gate_router(state) == "abort"

    def test_no_decision_aborts_rather_than_continuing(self) -> None:
        """The run is already known to be incomplete.

        Defaulting to "carry on" is the single option that can turn a broken
        run into a silently wrong merge, so absence of an answer is a stop.
        """
        assert _error_gate_router({"handled_failures": []}) == "abort"
        assert _error_gate_router({}) == "abort"

    def test_endless_retries_are_capped(self) -> None:
        state = {
            "handled_failures": [{"node": "extract_data", "decision": "retry"}],
            "retry_counts": {"extract_data": 99},
        }

        assert _error_gate_router(state) == "abort"

    def test_every_route_target_is_a_real_destination(self) -> None:
        """A router returning an unmapped name would crash the graph."""
        from src.orchestrator.graph import _error_gate_destinations

        destinations = _error_gate_destinations()
        for node, successor in NODE_SUCCESSOR.items():
            assert node in destinations
            assert successor in destinations
        assert "abort" in destinations


class TestPreflight:
    """Fail in seconds with a specific message, not five minutes in."""

    @pytest.fixture
    def state(self) -> dict[str, Any]:
        return {
            "user_request": "merge people",
            "origins": [
                {
                    "id": "pg1",
                    "type": "postgres",
                    "connection": {
                        "host": "h",
                        "port": 5432,
                        "user": "u",
                        "password": "p",
                        "database": "d",
                    },
                }
            ],
            "destination": {
                "type": "postgres",
                "host": "h",
                "port": 5432,
                "user": "u",
                "password": "p",
                "database": "d",
            },
            "llm_config": {"provider": "openai", "model": "m"},
        }

    @pytest.mark.asyncio
    async def test_all_clear_records_no_failure(self, state: dict[str, Any]) -> None:
        from src.orchestrator.nodes import preflight as pf

        with (
            patch.object(pf, "_probe_reachable"),
            patch.object(pf, "_probe_writable"),
            patch.object(pf, "_probe_llm"),
        ):
            result = await pf.preflight(state)

        assert result["failure"] == {}
        assert result["preflight_report"]["failed"] == 0
        assert result["preflight_report"]["passed"] >= 6

    @pytest.mark.asyncio
    async def test_unreachable_origin_stops_the_run(self, state: dict[str, Any]) -> None:
        from src.orchestrator.nodes import preflight as pf

        def unreachable(db_type: str, cfg: dict[str, Any]) -> None:
            raise Exception("could not connect to server")

        with (
            patch.object(pf, "_probe_reachable", side_effect=unreachable),
            patch.object(pf, "_probe_writable"),
            patch.object(pf, "_probe_llm"),
        ):
            result = await pf.preflight(state)

        assert result["failure"]["node"] == "preflight"
        names = [c["name"] for c in result["failure"]["context"]["failed_checks"]]
        assert "origin 'pg1' reachable" in names

    @pytest.mark.asyncio
    async def test_read_only_destination_is_caught_before_extraction(
        self, state: dict[str, Any]
    ) -> None:
        """The whole point: don't extract for five minutes then fail on write."""
        from src.orchestrator.nodes import preflight as pf

        def denied(db_type: str, cfg: dict[str, Any]) -> None:
            raise Exception("permission denied for schema public")

        with (
            patch.object(pf, "_probe_reachable"),
            patch.object(pf, "_probe_writable", side_effect=denied),
            patch.object(pf, "_probe_llm"),
        ):
            result = await pf.preflight(state)

        assert result["failure"]
        checks = {c["name"]: c for c in result["preflight_report"]["checks"]}
        assert checks["destination writable"]["ok"] is False

    @pytest.mark.asyncio
    async def test_dead_llm_is_caught_before_extraction(self, state: dict[str, Any]) -> None:
        """An exhausted quota used to surface as an empty, "successful" run."""
        from src.orchestrator.nodes import preflight as pf

        async def forbidden(cfg: dict[str, Any]) -> None:
            raise Exception("403 quota exceeded")

        with (
            patch.object(pf, "_probe_reachable"),
            patch.object(pf, "_probe_writable"),
            patch.object(pf, "_probe_llm", side_effect=forbidden),
        ):
            result = await pf.preflight(state)

        checks = {c["name"]: c for c in result["preflight_report"]["checks"]}
        assert checks["LLM reachable"]["ok"] is False
        assert result["failure"]

    @pytest.mark.asyncio
    async def test_empty_request_is_rejected(self, state: dict[str, Any]) -> None:
        from src.orchestrator.nodes import preflight as pf

        state["user_request"] = "   "
        with (
            patch.object(pf, "_probe_reachable"),
            patch.object(pf, "_probe_writable"),
            patch.object(pf, "_probe_llm"),
        ):
            result = await pf.preflight(state)

        checks = {c["name"]: c for c in result["preflight_report"]["checks"]}
        assert checks["merge request"]["ok"] is False

    @pytest.mark.asyncio
    async def test_every_origin_is_probed_even_after_one_fails(self, state: dict[str, Any]) -> None:
        """One run should reveal every broken connection, not just the first."""
        from src.orchestrator.nodes import preflight as pf

        state["origins"].append(
            {
                "id": "pg2",
                "type": "postgres",
                "connection": {
                    "host": "h2",
                    "port": 5432,
                    "user": "u",
                    "password": "p",
                    "database": "d",
                },
            }
        )
        with (
            patch.object(pf, "_probe_reachable", side_effect=Exception("could not connect")),
            patch.object(pf, "_probe_writable"),
            patch.object(pf, "_probe_llm"),
        ):
            result = await pf.preflight(state)

        names = [c["name"] for c in result["preflight_report"]["checks"]]
        assert "origin 'pg1' reachable" in names
        assert "origin 'pg2' reachable" in names


class TestAtomicLoad:
    """Either the whole merge lands, or the destination is untouched."""

    def _connector(self) -> Any:
        from src.mcp_server.connectors.postgresql_destination import (
            PostgreSQLDestinationConnector,
        )

        connector = PostgreSQLDestinationConnector(
            {
                "host": "h",
                "port": 5432,
                "user": "u",
                "password": "p",
                "database": "d",
                "schema": "public",
            }
        )
        connector._connection = MagicMock(closed=0)
        return connector

    def test_writes_inside_a_block_do_not_commit_individually(self) -> None:
        connector = self._connector()
        conn = connector._connection

        with connector.atomic():
            connector._commit(conn)
            connector._commit(conn)
            assert conn.commit.call_count == 0

        assert conn.commit.call_count == 1

    def test_a_failure_rolls_the_whole_block_back(self) -> None:
        """The entity that succeeded first must not survive on its own."""
        connector = self._connector()
        conn = connector._connection

        with pytest.raises(ValueError):
            with connector.atomic():
                connector._commit(conn)  # "person" loaded
                raise ValueError("company failed")

        assert conn.commit.call_count == 0
        assert conn.rollback.call_count == 1

    def test_inner_rollback_is_deferred_to_the_outer_block(self) -> None:
        """A per-entity rollback would undo the earlier entities silently."""
        connector = self._connector()
        conn = connector._connection

        with connector.atomic():
            connector._rollback(conn)

        assert conn.rollback.call_count == 0

    def test_dry_run_never_commits(self) -> None:
        connector = self._connector()
        conn = connector._connection

        with connector.atomic(dry_run=True):
            connector._commit(conn)

        assert conn.commit.call_count == 0
        assert conn.rollback.call_count == 1

    def test_outside_a_block_writes_commit_normally(self) -> None:
        connector = self._connector()
        conn = connector._connection

        connector._commit(conn)

        assert conn.commit.call_count == 1

    def test_transaction_flag_is_cleared_after_the_block(self) -> None:
        connector = self._connector()

        with connector.atomic():
            assert connector._in_transaction is True
        assert connector._in_transaction is False

    def test_nested_blocks_leave_the_outermost_in_charge(self) -> None:
        connector = self._connector()
        conn = connector._connection

        with connector.atomic():
            with connector.atomic():
                connector._commit(conn)
            assert conn.commit.call_count == 0

        assert conn.commit.call_count == 1

    @pytest.mark.asyncio
    async def test_node_reports_nothing_loaded_when_the_load_rolls_back(
        self,
    ) -> None:
        """The headline M.5 contract, at the node level.

        Two entities, the second fails. Previously the first stayed
        committed and the report credited it with rows — a destination
        holding half a merge, described as a partial success. Now the
        transaction is undone, so the report must credit nobody.
        """
        from src.orchestrator.nodes import load_data as node_mod

        state = {
            "load_approval": True,
            "run_id": "t-rollback",
            "unified_entities": {
                "person": [{"id": "p1", "name": "A"}],
                "company": [{"id": "c1", "name": "B"}],
            },
            "field_mappings": {},
            "extraction_order": ["person", "company"],
            "destination": {"type": "postgres", "host": "h", "database": "d"},
            "errors": [],
            "entity_clusters": {},
            "pending_schema_changes": [],
        }

        connector = self._connector()
        connector.list_tables = MagicMock(return_value=["person", "company"])
        connector.save_entity_map = MagicMock(return_value=0)
        connector.close = MagicMock()
        conn = connector._connection

        async def load_one(**kwargs: Any) -> dict[str, Any]:
            if kwargs["entity_type"] == "company":
                raise RuntimeError("disk full")
            return {"total_records": 1, "inserted": 1, "updated": 0}

        with (
            patch.object(node_mod, "_get_destination_connector", return_value=connector),
            patch.object(node_mod, "load_data_impl", side_effect=load_one),
            patch.object(node_mod, "_write_run_report", return_value=None),
        ):
            result = await node_mod.load_data_node(state)

        report = result["reconciliation_report"]
        assert report["load_rolled_back"] is True
        assert conn.commit.call_count == 0
        assert conn.rollback.call_count == 1
        # No entity may claim rows it does not have in the database.
        assert set(report.get("load_failed_entities") or []) == {"person", "company"}
        for entity in ("person", "company"):
            per_entity = (report.get("entities") or {}).get(entity, {})
            assert per_entity.get("loaded", 0) == 0

    @pytest.mark.asyncio
    async def test_schema_change_is_refused_at_the_real_connector_level(
        self,
    ) -> None:
        """Phase 0 Bug A, at the real-connector level.

        Before Bug A's fix, _apply_schema_changes opened its own standalone
        connector and its ALTER TABLE committed immediately — outside the
        load's transaction — so a failed run left the destination schema
        permanently polluted even though every row rolled back (company grew
        from 14 columns to 43 this way). Now the destination schema is a
        fixed contract: extend_schema_batch is never called at all, pending
        or not, and the refusal is recorded as an error.
        """
        from src.orchestrator.nodes import load_data as node_mod

        state = {
            "load_approval": True,
            "run_id": "t-schema-rollback",
            "unified_entities": {
                "person": [{"id": "p1", "name": "A"}],
                "company": [{"id": "c1", "name": "B"}],
            },
            "field_mappings": {},
            "extraction_order": ["person", "company"],
            "destination": {"type": "postgres", "host": "h", "database": "d"},
            "errors": [],
            "entity_clusters": {},
            "pending_schema_changes": [
                {"table": "person", "column": "phone", "data_type": "VARCHAR"},
            ],
        }

        connector = self._connector()
        connector.list_tables = MagicMock(return_value=["person", "company"])
        connector.save_entity_map = MagicMock(return_value=0)
        connector.close = MagicMock()
        conn = connector._connection

        async def load_one(**kwargs: Any) -> dict[str, Any]:
            if kwargs["entity_type"] == "company":
                raise RuntimeError("disk full")
            return {"total_records": 1, "inserted": 1, "updated": 0}

        with (
            patch.object(node_mod, "_get_destination_connector", return_value=connector),
            patch.object(node_mod, "load_data_impl", side_effect=load_one),
            patch.object(node_mod, "_write_run_report", return_value=None),
            patch.object(
                connector, "extend_schema_batch", wraps=connector.extend_schema_batch
            ) as schema_spy,
        ):
            result = await node_mod.load_data_node(state)

        # No ALTER is ever issued — the pending change is refused outright.
        schema_spy.assert_not_called()
        assert any("fixed contract" in e for e in result["errors"])
        report = result["reconciliation_report"]
        assert report["load_rolled_back"] is True
        # The real connection never committed — the failed company load
        # rolled back on the shared connection.
        assert conn.commit.call_count == 0
        assert conn.rollback.call_count == 1
        # Fix #6: the rolled-back load must escalate, not flow on silently.
        assert result["failure"]["error_class"] == "DataQualityError"
        assert result["failure"]["disposition"] == "escalate"

    def test_clickhouse_refuses_to_fake_a_dry_run(self) -> None:
        """Pretending to roll back would be worse than saying we can't."""
        from src.mcp_server.connectors.clickhouse_destination import (
            ClickHouseDestinationConnector,
        )

        connector = ClickHouseDestinationConnector({"host": "h", "database": "d"})

        with pytest.raises(NotImplementedError):
            with connector.atomic(dry_run=True):
                pass
