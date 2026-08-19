"""Unit tests for the V3 discovery-engine metadata store (Part B, P-1).

All tests mock the psycopg2 connection/cursor -- no real PostgreSQL needed.
The behaviors under test are the ones the module's own docstring promises:
every table gets created, ids are stable and generated (never keyed on name
alone), origin/review_status are validated before any write reaches the
database, and a human override is always an additive INSERT into
md_override, never a mutation of the row it overrides.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.mcp_server.tools.metadata_graph import (
    VALID_KEY_TYPES,
    VALID_ORIGINS,
    VALID_REVIEW_STATUSES,
    VALID_RUN_STATUSES,
    MetadataStore,
    _validate_origin,
    _validate_review_status,
)


@pytest.fixture
def store_config() -> dict:
    return {
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "password": "pass",
        "database": "metadata",
        "schema": "public",
    }


@pytest.fixture
def store(store_config: dict) -> MetadataStore:
    s = MetadataStore(store_config)
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    conn.closed = 0
    s._connection = conn
    return s


def _cursor(store: MetadataStore) -> MagicMock:
    return store._connection.cursor.return_value


class TestValidators:
    def test_every_valid_origin_passes(self) -> None:
        for origin in VALID_ORIGINS:
            _validate_origin(origin)  # must not raise

    def test_an_unknown_origin_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="origin must be one of"):
            _validate_origin("GUESSED")

    def test_every_valid_review_status_passes(self) -> None:
        for status in VALID_REVIEW_STATUSES:
            _validate_review_status(status)  # must not raise

    def test_an_unknown_review_status_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="review_status must be one of"):
            _validate_review_status("MAYBE")


class TestEnsureSchema:
    def test_creates_every_md_table(self, store: MetadataStore) -> None:
        store.ensure_schema()
        cursor = _cursor(store)
        executed_sql = " ".join(call.args[0] for call in cursor.execute.call_args_list)
        for table in (
            "md_run",
            "md_source",
            "md_database",
            "md_schema",
            "md_table",
            "md_column",
            "md_profile",
            "md_candidate_key",
            "md_relationship",
            "md_canonical_field",
            "md_field_mapping",
            "md_edge",
            "md_override",
            "md_semantic_type",
        ):
            assert f'"{table}"' in executed_sql, f"{table} was never created"

    def test_commits_once_at_the_end(self, store: MetadataStore) -> None:
        store.ensure_schema()
        store._connection.commit.assert_called_once()

    def test_a_failure_rolls_back_and_never_commits(self, store: MetadataStore) -> None:
        cursor = _cursor(store)
        cursor.execute.side_effect = [None, RuntimeError("boom")]
        with pytest.raises(RuntimeError):
            store.ensure_schema()
        store._connection.rollback.assert_called_once()
        store._connection.commit.assert_not_called()


class TestRunLifecycle:
    def test_create_run_inserts_queued_status_and_returns_an_id(self, store: MetadataStore) -> None:
        run_id = store.create_run("PROFILE", configuration={"batch_size": 100})
        assert run_id  # a real, non-empty generated id
        cursor = _cursor(store)
        sql, params = cursor.execute.call_args.args
        assert "INSERT INTO" in sql
        assert "md_run" in sql
        assert params[0] == run_id
        assert params[1] == "PROFILE"
        assert json.loads(params[2]) == {"batch_size": 100}
        store._connection.commit.assert_called_once()

    def test_two_calls_produce_different_run_ids(self, store: MetadataStore) -> None:
        first = store.create_run("PROFILE")
        second = store.create_run("PROFILE")
        assert first != second

    def test_update_run_status_rejects_an_unknown_status(self, store: MetadataStore) -> None:
        with pytest.raises(ValueError, match="status must be one of"):
            store.update_run_status("run-1", "DONE")
        _cursor(store).execute.assert_not_called()

    def test_update_run_status_accepts_every_valid_status(self, store: MetadataStore) -> None:
        for status in VALID_RUN_STATUSES:
            store.update_run_status("run-1", status)
        assert _cursor(store).execute.call_count == len(VALID_RUN_STATUSES)


class TestStructuralBackbone:
    def test_upsert_table_uses_insert_on_conflict_returning(self, store: MetadataStore) -> None:
        cursor = _cursor(store)
        cursor.fetchone.return_value = ("table-id-1",)
        result = store.upsert_table("schema-1", "Company", row_count_estimate=42)
        assert result == "table-id-1"
        sql = cursor.execute.call_args.args[0]
        assert "ON CONFLICT (schema_id, name) DO UPDATE" in sql
        store._connection.commit.assert_called_once()

    def test_upsert_column_round_trips_type_and_nullability(self, store: MetadataStore) -> None:
        cursor = _cursor(store)
        cursor.fetchone.return_value = ("col-id-1",)
        result = store.upsert_column(
            "table-id-1", "companyID", data_type="String", ordinal_position=1, nullable=False
        )
        assert result == "col-id-1"
        sql, params = cursor.execute.call_args.args
        assert "md_column" in sql
        assert params[1] == "table-id-1"
        assert params[2] == "companyID"
        assert params[3] == "String"
        assert params[5] is False

    def test_upsert_database_and_schema_share_the_named_child_helper(
        self, store: MetadataStore
    ) -> None:
        cursor = _cursor(store)
        cursor.fetchone.return_value = ("db-id-1",)
        result = store.upsert_database("source-1", "origin2")
        assert result == "db-id-1"
        sql = cursor.execute.call_args.args[0]
        assert "md_database" in sql
        assert "source_id" in sql


class TestOverridesAreAdditive:
    def test_record_override_only_inserts_into_md_override(self, store: MetadataStore) -> None:
        """The whole point of ADR-shaped 'additive' overrides: this must
        never touch the target table/row, only ever insert a new row into
        md_override recording both the original and the human decision."""
        cursor = _cursor(store)
        store.record_override(
            "md_relationship",
            "rel-1",
            original_decision={"confidence": 0.6, "origin": "ALGORITHMIC"},
            human_decision={"confirmed": True},
            user_name="alice",
            reason="verified manually against the source schema",
        )
        sql, params = cursor.execute.call_args.args
        assert "INSERT INTO" in sql
        assert '"md_override"' in sql
        assert "UPDATE" not in sql
        assert params[1] == "md_relationship"
        assert params[2] == "rel-1"
        assert json.loads(params[3]) == {"confidence": 0.6, "origin": "ALGORITHMIC"}
        assert json.loads(params[4]) == {"confirmed": True}

    def test_original_decision_none_is_stored_as_sql_null_not_the_string_null(
        self, store: MetadataStore
    ) -> None:
        cursor = _cursor(store)
        store.record_override(
            "md_candidate_key", "key-1", original_decision=None, human_decision={"rejected": True}
        )
        params = cursor.execute.call_args.args[1]
        assert params[3] is None

    def test_get_overrides_returns_full_history_oldest_first(self, store: MetadataStore) -> None:
        cursor = _cursor(store)
        cursor.fetchall.return_value = [
            {"id": "ov-1", "original_decision": None, "human_decision": {"a": 1}},
            {"id": "ov-2", "original_decision": None, "human_decision": {"a": 2}},
        ]
        result = store.get_overrides("md_relationship", "rel-1")
        assert [r["id"] for r in result] == ["ov-1", "ov-2"]
        sql = cursor.execute.call_args.args[0]
        assert "ORDER BY created_at ASC" in sql


class TestBulkReaders:
    """P-8's metadata_graph_view.py builds a networkx graph from these --
    one query per table, so each must return every row unfiltered by
    default (run_id filtering is opt-in, only on relationship/field_mapping
    which are the only two run-scoped facts here)."""

    def test_list_sources_queries_md_source(self, store: MetadataStore) -> None:
        cursor = _cursor(store)
        cursor.fetchall.return_value = [{"id": "s1", "name": "origin1"}]
        result = store.list_sources()
        assert result == [{"id": "s1", "name": "origin1"}]
        assert '"md_source"' in cursor.execute.call_args.args[0]

    def test_list_columns_queries_md_column(self, store: MetadataStore) -> None:
        cursor = _cursor(store)
        cursor.fetchall.return_value = []
        store.list_columns()
        assert '"md_column"' in cursor.execute.call_args.args[0]

    def test_list_relationships_without_run_id_has_no_where_clause(
        self, store: MetadataStore
    ) -> None:
        cursor = _cursor(store)
        cursor.fetchall.return_value = []
        store.list_relationships()
        call = cursor.execute.call_args
        assert "WHERE" not in call.args[0]
        assert len(call.args) == 1

    def test_list_relationships_with_run_id_filters_by_it(self, store: MetadataStore) -> None:
        cursor = _cursor(store)
        cursor.fetchall.return_value = []
        store.list_relationships(run_id="run-1")
        call = cursor.execute.call_args
        assert "WHERE run_id = %s" in call.args[0]
        assert call.args[1] == ("run-1",)

    def test_list_field_mappings_with_run_id_filters_by_it(self, store: MetadataStore) -> None:
        cursor = _cursor(store)
        cursor.fetchall.return_value = []
        store.list_field_mappings(run_id="run-2")
        call = cursor.execute.call_args
        assert "WHERE run_id = %s" in call.args[0]
        assert call.args[1] == ("run-2",)


class TestEvidenceCarryingInserts:
    def test_insert_candidate_key_rejects_an_unknown_key_type(self, store: MetadataStore) -> None:
        with pytest.raises(ValueError, match="key_type must be one of"):
            store.insert_candidate_key("table-1", ["id"], "GUESSED_KEY")
        _cursor(store).execute.assert_not_called()

    def test_insert_candidate_key_accepts_every_valid_key_type(self, store: MetadataStore) -> None:
        for key_type in VALID_KEY_TYPES:
            store.insert_candidate_key("table-1", ["id"], key_type)
        assert _cursor(store).execute.call_count == len(VALID_KEY_TYPES)

    def test_insert_relationship_rejects_an_invalid_origin_before_any_write(
        self, store: MetadataStore
    ) -> None:
        with pytest.raises(ValueError, match="origin must be one of"):
            store.insert_relationship("col-a", "col-b", "FOREIGN_KEY", origin="GUESSED")
        _cursor(store).execute.assert_not_called()

    def test_insert_relationship_rejects_an_invalid_review_status_before_any_write(
        self, store: MetadataStore
    ) -> None:
        with pytest.raises(ValueError, match="review_status must be one of"):
            store.insert_relationship("col-a", "col-b", "FOREIGN_KEY", review_status="MAYBE")
        _cursor(store).execute.assert_not_called()

    def test_insert_relationship_stores_coverage_and_signals(self, store: MetadataStore) -> None:
        cursor = _cursor(store)
        store.insert_relationship(
            "col-a",
            "col-b",
            "FOREIGN_KEY",
            coverage=0.97,
            signals={"name_similarity": 0.8, "value_overlap": 0.97},
            confidence=0.9,
            run_id="run-1",
        )
        sql, params = cursor.execute.call_args.args
        assert '"md_relationship"' in sql
        # column order: id, run_id, from_column_id, to_column_id,
        # relationship_type, coverage, signals, confidence, evidence,
        # algorithm_version, origin, review_status
        assert params[1] == "run-1"
        assert params[2] == "col-a"
        assert params[3] == "col-b"
        assert params[4] == "FOREIGN_KEY"
        assert params[5] == 0.97
        assert json.loads(params[6]) == {"name_similarity": 0.8, "value_overlap": 0.97}
        assert params[7] == 0.9

    def test_insert_field_mapping_and_insert_edge_both_use_the_shared_evidence_path(
        self, store: MetadataStore
    ) -> None:
        cursor = _cursor(store)
        store.insert_field_mapping("col-a", "canon-1", confidence=0.75)
        store.insert_edge("column", "col-a", "column", "col-b", "SIMILAR_TO")
        assert cursor.execute.call_count == 2
        first_sql = cursor.execute.call_args_list[0].args[0]
        second_sql = cursor.execute.call_args_list[1].args[0]
        assert '"md_field_mapping"' in first_sql
        assert '"md_edge"' in second_sql

    def test_upsert_canonical_field_uses_insert_on_conflict_returning(
        self, store: MetadataStore
    ) -> None:
        cursor = _cursor(store)
        cursor.fetchone.return_value = ("canon-id-1",)
        result = store.upsert_canonical_field("company", "name", data_type="text")
        assert result == "canon-id-1"
        sql = cursor.execute.call_args.args[0]
        assert "ON CONFLICT (entity_type, field_name) DO UPDATE" in sql
