"""Unit tests for V3 canonical matching dataset (Part B, P-10).

Covers: assembling P-9's raw extraction results plus P-7's field mappings
into the Splink handoff shape (record_id/source_id/entity_type/
entity_anchor_id/canonical_entity_id/canonical_fields/original_fields/
source_table/source_record_id/context_score/graph_path/provenance/
discovery_evidence/quality_diagnostics), and materializing into a real
DuckDB file with a stable, updatable record_id.
"""

from __future__ import annotations

import json

import duckdb
import pytest

from src.mcp_server.tools.canonical_matching_dataset import (
    build_dataset_rows,
    materialize,
    serialize_graph_path,
)
from src.mcp_server.tools.join_graph import JoinEdge, PathHop


def _hop(child_table, child_column, parent_table, parent_column, *, confidence="verified"):
    edge = JoinEdge(
        child_origin="origin1",
        child_table=child_table,
        child_column=child_column,
        parent_origin="origin1",
        parent_table=parent_table,
        parent_column=parent_column,
        confidence=confidence,
        overlap_ratio=1.0,
        rationale="test edge",
    )
    # "narrow" (child -> parent) keeps from_/to_ matching the call's own
    # child/parent argument order, so a test reads naturally without
    # having to mentally invert fanout's parent-first semantics.
    return PathHop(edge=edge, direction="narrow")


class TestSerializeGraphPath:
    def test_empty_path_is_the_anchor_itself(self) -> None:
        assert serialize_graph_path([]) == []

    def test_one_hop_carries_the_full_join_and_confidence(self) -> None:
        result = serialize_graph_path([_hop("Company", "id", "Location", "company_id")])
        assert result == [
            {
                "from_table": "Company",
                "from_column": "id",
                "to_table": "Location",
                "to_column": "company_id",
                "direction": "narrow",
                "confidence": "verified",
                "rationale": "test edge",
            }
        ]


class TestBuildDatasetRows:
    def test_mapped_columns_land_in_canonical_fields_unmapped_stay_in_original(self) -> None:
        extraction_results = {
            ("origin1", "Company"): [
                {"companyID": "c1", "entityName": "Boeing", "internal_flag": "x"}
            ]
        }
        field_mappings = {
            ("origin1", "Company"): {
                "companyID": {
                    "canonical_field": "company_id",
                    "confidence": 1.0,
                    "origin": "ALGORITHMIC",
                },
                "entityName": {
                    "canonical_field": "name",
                    "confidence": 0.8,
                    "origin": "ALGORITHMIC",
                },
            }
        }
        rows = build_dataset_rows(
            extraction_results,
            entity_type="company",
            entity_anchor_id="anchor-1",
            field_mappings_by_table=field_mappings,
            context_scores_by_table={("origin1", "Company"): 1.0},
            graph_paths_by_table={},
            record_id_column_by_table={("origin1", "Company"): "companyID"},
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["canonical_fields"] == {"company_id": "c1", "name": "Boeing"}
        assert row["original_fields"] == {
            "companyID": "c1",
            "entityName": "Boeing",
            "internal_flag": "x",
        }
        assert "internal_flag" not in row["canonical_fields"]

    def test_lineage_fields_are_fully_populated(self) -> None:
        extraction_results = {("origin1", "Company"): [{"companyID": "c1"}]}
        rows = build_dataset_rows(
            extraction_results,
            entity_type="company",
            entity_anchor_id="anchor-1",
            field_mappings_by_table={},
            context_scores_by_table={("origin1", "Company"): 0.95},
            graph_paths_by_table={("origin1", "Company"): [{"from_table": "Company"}]},
            record_id_column_by_table={("origin1", "Company"): "companyID"},
            run_id="run-42",
        )
        row = rows[0]
        assert row["record_id"] == "origin1:Company:c1"
        assert row["source_id"] == "origin1"
        assert row["entity_type"] == "company"
        assert row["entity_anchor_id"] == "anchor-1"
        assert row["canonical_entity_id"] is None
        assert row["source_table"] == "Company"
        assert row["source_record_id"] == "c1"
        assert row["context_score"] == 0.95
        assert row["graph_path"] == [{"from_table": "Company"}]
        assert row["provenance"] == {
            "origin_id": "origin1",
            "table": "Company",
            "record_id_column": "companyID",
        }
        assert row["run_id"] == "run-42"

    def test_a_missing_pk_column_still_produces_a_stable_record_id(self) -> None:
        extraction_results = {("origin1", "Weird"): [{"a": 1}, {"a": 2}]}
        rows = build_dataset_rows(
            extraction_results,
            entity_type="company",
            entity_anchor_id=None,
            field_mappings_by_table={},
            context_scores_by_table={},
            graph_paths_by_table={},
            record_id_column_by_table={},
        )
        assert rows[0]["record_id"] == "origin1:Weird:0"
        assert rows[1]["record_id"] == "origin1:Weird:1"
        assert rows[0]["source_record_id"] is None

    def test_a_non_unique_pk_column_falls_back_to_positional_ids_not_a_collision(self) -> None:
        """Live-caught (BP-B, real ClickHouse origin1, 2026-08-19): a
        caller passed "companyID" as CompanyExperience's own record-id
        column, but companyID is a foreign key there (many experience
        rows per company), not a primary key. Every colliding row got the
        same record_id, and materialize()'s upsert silently overwrote all
        but the last one -- real rows vanished with no error. The fix
        detects a non-unique pk_column per table and falls back to
        positional ids instead of trusting it blindly."""
        extraction_results = {
            ("origin1", "CompanyExperience"): [
                {"companyID": "c1", "personID": "p1"},
                {"companyID": "c1", "personID": "p2"},
                {"companyID": "c1", "personID": "p3"},
            ]
        }
        rows = build_dataset_rows(
            extraction_results,
            entity_type="company",
            entity_anchor_id=None,
            field_mappings_by_table={},
            context_scores_by_table={},
            graph_paths_by_table={},
            record_id_column_by_table={("origin1", "CompanyExperience"): "companyID"},
        )
        record_ids = [r["record_id"] for r in rows]
        assert len(record_ids) == len(set(record_ids)) == 3
        assert all(r["source_record_id"] is None for r in rows)
        assert all(r["provenance"]["record_id_column"] is None for r in rows)


class TestMaterialize:
    def test_a_row_with_no_source_record_id_is_still_materialized(self, tmp_path) -> None:
        """Live-caught (BP-B, real ClickHouse origin1, 2026-08-19): the
        very first real materialize() call after adding the non-unique-
        pk_column fallback above hit `NOT NULL constraint failed:
        source_record_id`, because build_dataset_rows() legitimately
        produces source_record_id=NULL for a table with no genuinely
        unique id column, but the DuckDB schema declared that column
        NOT NULL."""
        db_path = str(tmp_path / "nullable_spool.duckdb")
        rows = build_dataset_rows(
            {("origin1", "CompanyExperience"): [{"companyID": "c1"}, {"companyID": "c1"}]},
            entity_type="company",
            entity_anchor_id=None,
            field_mappings_by_table={},
            context_scores_by_table={},
            graph_paths_by_table={},
            record_id_column_by_table={("origin1", "CompanyExperience"): "companyID"},
        )
        assert all(r["source_record_id"] is None for r in rows)
        written = materialize(db_path, rows)
        assert written == 2

    def test_rows_are_written_and_sql_queryable(self, tmp_path) -> None:
        db_path = str(tmp_path / "test_spool.duckdb")
        rows = build_dataset_rows(
            {("origin1", "Company"): [{"companyID": "c1", "entityName": "Boeing"}]},
            entity_type="company",
            entity_anchor_id="anchor-1",
            field_mappings_by_table={
                ("origin1", "Company"): {
                    "entityName": {
                        "canonical_field": "name",
                        "confidence": 0.8,
                        "origin": "ALGORITHMIC",
                    }
                }
            },
            context_scores_by_table={("origin1", "Company"): 1.0},
            graph_paths_by_table={},
            record_id_column_by_table={("origin1", "Company"): "companyID"},
            run_id="run-1",
        )
        written = materialize(db_path, rows)
        assert written == 1

        con = duckdb.connect(db_path)
        try:
            result = con.execute(
                "SELECT record_id, source_id, source_table, source_record_id, "
                "canonical_fields FROM canonical_matching_dataset"
            ).fetchone()
        finally:
            con.close()
        assert result[0] == "origin1:Company:c1"
        assert result[1] == "origin1"
        assert result[2] == "Company"
        assert result[3] == "c1"
        assert json.loads(result[4]) == {"name": "Boeing"}

    def test_no_rows_is_a_safe_no_op(self, tmp_path) -> None:
        db_path = str(tmp_path / "empty_spool.duckdb")
        assert materialize(db_path, []) == 0

    def test_re_materializing_the_same_record_id_updates_not_duplicates(self, tmp_path) -> None:
        db_path = str(tmp_path / "test_spool2.duckdb")
        rows = build_dataset_rows(
            {("origin1", "Company"): [{"companyID": "c1"}]},
            entity_type="company",
            entity_anchor_id="anchor-1",
            field_mappings_by_table={},
            context_scores_by_table={("origin1", "Company"): 0.5},
            graph_paths_by_table={},
            record_id_column_by_table={("origin1", "Company"): "companyID"},
        )
        materialize(db_path, rows)

        updated_rows = build_dataset_rows(
            {("origin1", "Company"): [{"companyID": "c1"}]},
            entity_type="company",
            entity_anchor_id="anchor-1",
            field_mappings_by_table={},
            context_scores_by_table={("origin1", "Company"): 0.9},
            graph_paths_by_table={},
            record_id_column_by_table={("origin1", "Company"): "companyID"},
        )
        materialize(db_path, updated_rows)

        con = duckdb.connect(db_path)
        try:
            count = con.execute("SELECT count(*) FROM canonical_matching_dataset").fetchone()[0]
            score = con.execute(
                "SELECT context_score FROM canonical_matching_dataset WHERE record_id = 'origin1:Company:c1'"
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 1
        assert score == pytest.approx(0.9)
