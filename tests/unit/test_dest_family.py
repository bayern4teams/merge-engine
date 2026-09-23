"""Unit tests for src/mcp_server/tools/dest_family.py (Phase 6.1).

Before this, map_fields.py only ever considered ONE destination table per
entity, so a source field with a home on a sibling table (finance,
contact_email, ...) was refused rather than mapped there. These tests
cover the real-FK-derived family graph that Phase 6.2's widened mapping
search will use to find that sibling.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.mcp_server.tools.dest_family import build_family_map


def _connector(tables: list[str], fks: dict[str, dict[str, str]]) -> Any:
    """A fake PostgreSQLDestinationConnector-shaped object.

    ``fks`` maps table_name -> {column: referenced_table}, matching
    get_foreign_keys' real return shape.
    """
    conn = MagicMock()
    conn.list_tables.return_value = tables
    conn.get_foreign_keys.side_effect = lambda t: fks.get(t, {})
    return conn


class TestBuildFamilyMap:
    def test_simple_fk_produces_bidirectional_family(self) -> None:
        conn = _connector(
            ["company", "finance"],
            {"finance": {"company_id": "company"}},
        )
        family = build_family_map(conn)
        assert family["company"] == {"finance"}
        assert family["finance"] == {"company"}

    def test_multiple_tables_sharing_a_referenced_table(self) -> None:
        """company gains every table with an FK to it, deduplicated by set
        semantics even when a table has more than one FK column pointing
        at the same referenced table (e.g. a self-referencing pair)."""
        conn = _connector(
            ["company", "finance", "industry", "location"],
            {
                "finance": {"company_id": "company"},
                "industry": {"company_id": "company"},
                "location": {"company_id": "company"},
            },
        )
        family = build_family_map(conn)
        assert family["company"] == {"finance", "industry", "location"}
        assert family["finance"] == {"company"}
        assert family["industry"] == {"company"}
        assert family["location"] == {"company"}

    def test_company_relation_two_fks_to_same_table_dedupes(self) -> None:
        """A self-referencing junction (company_a_id, company_b_id both ->
        company) must not produce a duplicate or crash -- set semantics
        handle this naturally."""
        conn = _connector(
            ["company", "company_relation"],
            {
                "company_relation": {
                    "company_a_id": "company",
                    "company_b_id": "company",
                }
            },
        )
        family = build_family_map(conn)
        assert family["company"] == {"company_relation"}
        assert family["company_relation"] == {"company"}

    def test_table_with_no_fks_is_present_with_empty_family(self) -> None:
        conn = _connector(["standalone"], {})
        family = build_family_map(conn)
        assert family == {"standalone": set()}

    def test_referenced_table_not_in_list_tables_is_still_recorded(self) -> None:
        """Schema drift mid-read (or a system table list_tables' own
        filter excludes) must not lose the relationship."""
        conn = _connector(
            ["finance"],
            {"finance": {"company_id": "company"}},
        )
        family = build_family_map(conn)
        assert family["finance"] == {"company"}
        assert family["company"] == {"finance"}

    def test_connector_without_get_foreign_keys_returns_empty(self) -> None:
        """The ClickHouse destination has no FK constraints to read at
        all -- must degrade gracefully, not raise."""
        conn = MagicMock(spec=["list_tables"])
        conn.list_tables.return_value = ["company"]
        family = build_family_map(conn)
        assert family == {}

    def test_list_tables_failure_degrades_to_empty(self) -> None:
        conn = MagicMock()
        conn.list_tables.side_effect = Exception("connection lost")
        family = build_family_map(conn)
        assert family == {}

    def test_one_table_get_foreign_keys_failure_does_not_abort_the_rest(self) -> None:
        conn = MagicMock()
        conn.list_tables.return_value = ["company", "finance", "broken"]

        def get_fks(table: str) -> dict[str, str]:
            if table == "broken":
                raise RuntimeError("boom")
            if table == "finance":
                return {"company_id": "company"}
            return {}

        conn.get_foreign_keys.side_effect = get_fks
        family = build_family_map(conn)
        assert family["company"] == {"finance"}
        assert family["finance"] == {"company"}
        assert family["broken"] == set()


class TestEntityGraphExclusions:
    """Live regression (Boeing, this session): a plain FK relationship to
    "entity" or a *_relationship table is real, but neither is a valid
    field-mapping target — see build_family_map's own comment for why.
    """

    def test_a_tables_own_entity_supertype_ancestors_are_excluded(self) -> None:
        conn = _connector(
            ["entity", "organization", "company", "finance"],
            {
                "organization": {"organization_id": "entity"},
                "company": {"company_id": "organization"},
                "finance": {"company_id": "company"},
            },
        )
        conn.get_entity_supertype_chain.side_effect = lambda t: {
            "company": ["entity", "organization"],
            "organization": ["entity"],
        }.get(t)

        family = build_family_map(conn)

        # "finance" (a genuine sibling) stays; "entity"/"organization" (its
        # own ancestors, populated by ensure_entity_chain, not by mapping)
        # do not.
        assert family["company"] == {"finance"}
        assert "company" not in family["entity"]
        assert "company" not in family["organization"]

    def test_a_relationship_item_table_is_excluded_entirely(self) -> None:
        conn = _connector(
            ["company", "finance", "company_company_relationship"],
            {
                "finance": {"company_id": "company"},
                "company_company_relationship": {
                    "company_a_id": "company",
                    "company_b_id": "company",
                },
            },
        )
        conn.get_entity_supertype_chain.return_value = None
        conn.get_table_schema.side_effect = lambda t: {
            "company_company_relationship": {"primary_key": "item_id"},
        }.get(t, {"primary_key": None})

        def get_fks(t: str) -> dict[str, str]:
            base = {
                "finance": {"company_id": "company"},
                "company_company_relationship": {
                    "company_a_id": "company",
                    "company_b_id": "company",
                    "item_id": "knowledge_item",
                },
            }
            return base.get(t, {})

        conn.get_foreign_keys.side_effect = get_fks

        family = build_family_map(conn)

        assert family["company"] == {"finance"}
        assert "company_company_relationship" not in family
        assert "company_company_relationship" not in family.get("company", set())
