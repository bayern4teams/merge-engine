"""Unit tests for sqlglot column-level lineage (ADR 0009, fix-v3 B.1.5)."""

from __future__ import annotations

import pytest

from src.mcp_server.tools.sql_lineage import column_lineage, mapping_to_sql


class TestColumnLineage:
    def test_single_table_lineage_names_the_source_column(self):
        sql = "SELECT c.amount AS revenue FROM diffbot.companyfinancials c"
        result = column_lineage(sql, "revenue")
        assert len(result) == 1
        entry = result[0]
        assert entry["source_table"] == "diffbot.companyfinancials"
        assert entry["source_column"] == "amount"

    def test_multi_join_chain_traces_to_the_real_base_table(self):
        sql = (
            "SELECT p.name AS full_name FROM diffbot.person p "
            "JOIN diffbot.contact ct ON p.id = ct.person_id"
        )
        result = column_lineage(sql, "full_name")
        assert len(result) == 1
        assert result[0]["source_table"] == "diffbot.person"
        assert result[0]["source_column"] == "name"

    def test_unknown_column_raises(self):
        sql = "SELECT c.amount AS revenue FROM diffbot.companyfinancials c"
        with pytest.raises(Exception):
            column_lineage(sql, "does_not_exist")


class TestMappingToSql:
    def test_renders_select_as_from_the_field_mapping(self):
        field_mapping = {
            "amount": {
                "dest_column": "revenue",
                "dest_table": "finance",
                "table": "diffbot.companyfinancials",
                "source_field": "amount",
            }
        }
        sql = mapping_to_sql(field_mapping, "finance")
        result = column_lineage(sql, "revenue")
        assert result[0]["source_table"] == "diffbot.companyfinancials"
        assert result[0]["source_column"] == "amount"

    def test_empty_mapping_still_produces_parseable_sql(self):
        sql = mapping_to_sql({}, "company")
        assert "company" in sql
