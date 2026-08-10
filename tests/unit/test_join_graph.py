"""Unit tests for the real join graph (Phase 4.1 of the fix-bugs follow-up
plan) — src/mcp_server/tools/join_graph.py.

The concrete failure this closes: the 4th live run's origin_2 schema
(Company, CompanyIndustry, Experience, Industry, Location, Person,
PersonContact — Bugs/Logs/4th_try_05_18_local_enrichprompt.txt L165-170)
has no declared foreign keys at all (ClickHouse never returns any), and
PersonContact is reachable from Company only via
Company -> Experience -> Person -> PersonContact, a 3-hop chain the old
plan_scope.py's single-junction `_find_junction` could never express.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from src.mcp_server.tools.join_graph import (
    JoinGraph,
    _find_candidate_edges,
    _resolve_primary_key,
    build_join_graph,
    normalize_val,
)


class TestNormalizeVal:
    def test_strips_protocol_and_www(self) -> None:
        assert normalize_val("https://www.Airbus.com/") == "airbus.com"

    def test_none_is_empty_string(self) -> None:
        assert normalize_val(None) == ""

    def test_plain_value_lowercased_and_trimmed(self) -> None:
        assert normalize_val("  Airbus SE  ") == "airbus se"


class TestResolvePrimaryKey:
    def test_uses_declared_primary_key(self) -> None:
        table = {"name": "Company", "primary_key": ["companyID"], "columns": []}
        assert _resolve_primary_key(table) == ["companyID"]

    def test_mongo_falls_back_to_bare_id(self) -> None:
        """MongoDB's discover_schema never sets "primary_key" at all --
        _id shows up as an ordinary column."""
        table = {"name": "companies", "columns": [{"name": "_id"}, {"name": "name"}]}
        assert _resolve_primary_key(table) == ["_id"]

    def test_no_pk_and_no_id_column_is_empty(self) -> None:
        table = {"name": "weird", "columns": [{"name": "a"}, {"name": "b"}]}
        assert _resolve_primary_key(table) == []


class TestFindCandidateEdges:
    def test_matches_id_style_column_to_real_pk(self) -> None:
        tables = [
            {"name": "Company", "primary_key": ["companyID"], "columns": [{"name": "companyID"}]},
            {
                "name": "Experience",
                "primary_key": ["experienceID"],
                "columns": [{"name": "experienceID"}, {"name": "companyID"}],
            },
        ]
        candidates = _find_candidate_edges(tables)
        matches = [c for c in candidates if c.child_table == "Experience"]
        assert len(matches) == 1
        assert matches[0].child_column == "companyID"
        assert matches[0].parent_table == "Company"
        # The parent's ACTUAL pk, never a hard-coded "id".
        assert matches[0].parent_column == "companyID"

    def test_does_not_match_via_bare_startswith(self) -> None:
        """The exact bug this removal fixes: a column that merely starts
        with another table's name (but isn't "<table>id"/"<table>_id"/
        "<table>key"/"<table>_key") must not be fabricated into an edge --
        e.g. "Person.company_name" must not point at "Company"."""
        tables = [
            {"name": "Company", "primary_key": ["companyID"], "columns": [{"name": "companyID"}]},
            {
                "name": "Person",
                "primary_key": ["personID"],
                "columns": [{"name": "personID"}, {"name": "company_name"}],
            },
        ]
        candidates = _find_candidate_edges(tables)
        assert not any(c.child_column == "company_name" for c in candidates)

    def test_skips_own_table_primary_key(self) -> None:
        tables = [
            {"name": "Company", "primary_key": ["companyID"], "columns": [{"name": "companyID"}]},
        ]
        candidates = _find_candidate_edges(tables)
        assert candidates == []

    def test_no_candidate_when_other_table_has_no_resolvable_pk(self) -> None:
        tables = [
            {"name": "Weird", "columns": [{"name": "a"}, {"name": "b"}]},
            {
                "name": "Experience",
                "primary_key": ["experienceID"],
                "columns": [{"name": "experienceID"}, {"name": "weirdID"}],
            },
        ]
        candidates = _find_candidate_edges(tables)
        assert candidates == []


def _schemas_for_issue_6() -> dict[str, Any]:
    """origin_2's real 7-table schema from the 4th live run, minimal
    columns -- enough to exercise the 3-hop chain."""
    return {
        "origin_2": {
            "origin_type": "clickhouse",
            "tables": [
                {
                    "name": "Company",
                    "primary_key": ["companyID"],
                    "columns": [{"name": "companyID"}, {"name": "name"}],
                    "foreign_keys": [],
                },
                {
                    "name": "Experience",
                    "primary_key": ["experienceID"],
                    "columns": [
                        {"name": "experienceID"},
                        {"name": "companyID"},
                        {"name": "personID"},
                    ],
                    "foreign_keys": [],
                },
                {
                    "name": "Person",
                    "primary_key": ["personID"],
                    "columns": [{"name": "personID"}, {"name": "name"}],
                    "foreign_keys": [],
                },
                {
                    "name": "PersonContact",
                    "primary_key": ["contactID"],
                    "columns": [{"name": "contactID"}, {"name": "personID"}, {"name": "email"}],
                    "foreign_keys": [],
                },
            ],
        }
    }


class TestBuildJoinGraph:
    ORIGINS = [{"id": "origin_2", "type": "clickhouse", "connection": {}}]

    async def _run_with_overlaps(self, overlaps: dict[tuple[str, str], list[Any]]):
        """overlaps maps (table, column) -> the values sample_distinct_values
        should return for that column, so the test controls exactly which
        candidate edges verify."""

        async def fake_sample(table_name, column, limit=500):
            return overlaps.get((table_name, column), [])

        fake_connector = AsyncMock()
        fake_connector.sample_distinct_values = AsyncMock(side_effect=fake_sample)
        fake_connector.close = AsyncMock()

        with patch(
            "src.mcp_server.tools.join_graph.create_connector",
            return_value=fake_connector,
        ):
            graph = await build_join_graph(_schemas_for_issue_6(), self.ORIGINS)
        return graph, fake_connector

    async def test_declared_fk_is_trusted_without_sampling(self) -> None:
        schemas = {
            "origin_1": {
                "tables": [
                    {"name": "Company", "primary_key": ["id"], "columns": [{"name": "id"}]},
                    {
                        "name": "Experience",
                        "primary_key": ["id"],
                        "columns": [{"name": "id"}, {"name": "company_id"}],
                        "foreign_keys": [
                            {"column": "company_id", "ref_table": "Company", "ref_column": "id"}
                        ],
                    },
                ]
            }
        }
        fake_connector = AsyncMock()
        fake_connector.sample_distinct_values = AsyncMock(return_value=[])
        fake_connector.close = AsyncMock()
        with patch(
            "src.mcp_server.tools.join_graph.create_connector",
            return_value=fake_connector,
        ):
            graph = await build_join_graph(
                schemas, [{"id": "origin_1", "type": "postgresql", "connection": {}}]
            )

        edges = graph.neighbors(("origin_1", "Experience"))
        declared = [e for e in edges if e.confidence == "declared"]
        assert len(declared) == 1
        assert declared[0].parent_table == "Company"
        assert declared[0].overlap_ratio is None

    async def test_the_3hop_chain_all_verify(self) -> None:
        """The exact issue-6 chain: Company -> Experience -> Person ->
        PersonContact, each hop backed by real (mocked) overlapping sampled
        values."""
        graph, _ = await self._run_with_overlaps(
            {
                ("Experience", "companyID"): ["c1", "c2"],
                ("Company", "companyID"): ["c1", "c2", "c3"],
                ("Experience", "personID"): ["p1", "p2"],
                ("Person", "personID"): ["p1", "p2", "p3"],
                ("PersonContact", "personID"): ["p1"],
            }
        )

        company_edges = graph.neighbors(("origin_2", "Company"))
        assert any(
            e.child_table == "Experience" and e.parent_table == "Company" for e in company_edges
        )

        person_edges = graph.neighbors(("origin_2", "Person"))
        to_experience = [e for e in person_edges if e.child_table == "Experience"]
        assert to_experience and to_experience[0].confidence == "verified"

        contact_edges = graph.neighbors(("origin_2", "PersonContact"))
        assert any(
            e.child_table == "PersonContact" and e.parent_table == "Person" for e in contact_edges
        )
        # All 3 hops present somewhere in the graph.
        all_pairs = {
            (e.child_table, e.parent_table) for node in graph.nodes for e in graph.neighbors(node)
        }
        assert ("Experience", "Company") in all_pairs
        assert ("Experience", "Person") in all_pairs
        assert ("PersonContact", "Person") in all_pairs

    async def test_full_overlap_is_verified_not_probable(self) -> None:
        graph, _ = await self._run_with_overlaps(
            {
                ("Experience", "companyID"): ["c1", "c2"],
                ("Company", "companyID"): ["c1", "c2", "c3"],
            }
        )
        edges = [
            e for e in graph.neighbors(("origin_2", "Experience")) if e.parent_table == "Company"
        ]
        assert edges[0].confidence == "verified"
        assert edges[0].overlap_ratio == 1.0

    async def test_partial_overlap_is_probable(self) -> None:
        graph, _ = await self._run_with_overlaps(
            {
                ("Experience", "companyID"): ["c1", "c2"],
                ("Company", "companyID"): ["c1"],  # only half of the child's values match
            }
        )
        edges = [
            e for e in graph.neighbors(("origin_2", "Experience")) if e.parent_table == "Company"
        ]
        assert edges[0].confidence == "probable"
        assert edges[0].overlap_ratio == 0.5

    async def test_no_overlap_is_discarded_not_a_low_confidence_edge(self) -> None:
        graph, _ = await self._run_with_overlaps(
            {
                ("Experience", "companyID"): ["c1", "c2"],
                ("Company", "companyID"): ["zzz", "yyy"],  # disjoint
            }
        )
        edges = [
            e for e in graph.neighbors(("origin_2", "Experience")) if e.parent_table == "Company"
        ]
        assert edges == []

    async def test_empty_child_sample_is_discarded(self) -> None:
        """A column that samples to no values at all yields no evidence
        either way -- must not be silently treated as a match."""
        graph, _ = await self._run_with_overlaps({})
        edges = graph.neighbors(("origin_2", "Experience"))
        assert edges == []

    async def test_connector_is_closed_even_on_sampling_error(self) -> None:
        fake_connector = AsyncMock()
        fake_connector.sample_distinct_values = AsyncMock(side_effect=RuntimeError("boom"))
        fake_connector.close = AsyncMock()
        with patch(
            "src.mcp_server.tools.join_graph.create_connector",
            return_value=fake_connector,
        ):
            graph = await build_join_graph(_schemas_for_issue_6(), self.ORIGINS)

        fake_connector.close.assert_awaited_once()
        # No candidate could verify, but the graph is still returned (all
        # nodes present, just no edges) rather than raising.
        assert ("origin_2", "PersonContact") in graph.nodes

    async def test_missing_origin_config_skips_verification_gracefully(self) -> None:
        with patch("src.mcp_server.tools.join_graph.create_connector") as mock_create:
            graph = await build_join_graph(_schemas_for_issue_6(), origins=[])
        mock_create.assert_not_called()
        assert ("origin_2", "Company") in graph.nodes

    async def test_all_nodes_present_even_with_no_edges(self) -> None:
        graph, _ = await self._run_with_overlaps({})
        assert graph.nodes == {
            ("origin_2", "Company"),
            ("origin_2", "Experience"),
            ("origin_2", "Person"),
            ("origin_2", "PersonContact"),
        }


class TestJoinGraphDataStructure:
    def test_add_edge_registers_both_endpoints(self) -> None:
        from src.mcp_server.tools.join_graph import JoinEdge

        graph = JoinGraph()
        edge = JoinEdge(
            child_origin="o1",
            child_table="Experience",
            child_column="companyID",
            parent_origin="o1",
            parent_table="Company",
            parent_column="companyID",
            confidence="verified",
            overlap_ratio=1.0,
            rationale="test",
        )
        graph.add_edge(edge)

        assert edge in graph.neighbors(("o1", "Experience"))
        assert edge in graph.neighbors(("o1", "Company"))
        assert len(graph) == 1

    def test_neighbors_of_unknown_node_is_empty(self) -> None:
        graph = JoinGraph()
        assert graph.neighbors(("o1", "Nonexistent")) == []
