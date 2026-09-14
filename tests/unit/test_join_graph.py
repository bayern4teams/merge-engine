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
    JoinEdge,
    JoinGraph,
    JoinPath,
    _find_candidate_edges,
    _hop_from,
    _resolve_primary_key,
    build_join_graph,
    find_paths,
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

    def test_falls_back_to_the_tables_own_natural_key_column(self) -> None:
        """Live regression: testdiffbot's `diffbot` schema has zero declared
        PKs across all 13 tables, so a table like "company" (columns:
        companyid, entityname, ...) used to resolve to no primary key at
        all -- making it impossible to ever be a candidate-edge PARENT, and
        every multi-hop entity behind it (financials, industry, investors,
        ...) got silently zero rows. A "{table}id" column, following this
        module's own child-column-matching convention, is now the fallback."""
        table = {
            "name": "company",
            "columns": [{"name": "companyid"}, {"name": "entityname"}],
        }
        assert _resolve_primary_key(table) == ["companyid"]

    def test_natural_key_fallback_is_case_insensitive_and_singularises(self) -> None:
        table = {
            "name": "Companies",
            "columns": [{"name": "CompanyID"}, {"name": "name"}],
        }
        assert _resolve_primary_key(table) == ["CompanyID"]

    def test_declared_pk_still_wins_over_the_natural_key_fallback(self) -> None:
        table = {
            "name": "company",
            "primary_key": ["surrogate_pk"],
            "columns": [{"name": "surrogate_pk"}, {"name": "companyid"}],
        }
        assert _resolve_primary_key(table) == ["surrogate_pk"]


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

    def test_candidate_carries_schema_qualified_names_for_verification(self) -> None:
        """Live regression: sample_distinct_values is called with only the
        bare table name (matching discover_schemas' "name" field), and
        silently defaults to the "public" schema when the name has no dot
        in it. testdiffbot's real tables all live in "diffbot" -- every
        verification query failed with "relation public.<table> does not
        exist", caught by _verify_inclusion's own try/except, and every
        candidate this correctly found (10 of them) came back unverified.
        _EdgeCandidate must carry each side's schema-qualified full_name so
        _verify_inclusion can actually query the right schema."""
        tables = [
            {
                "name": "company",
                "full_name": "diffbot.company",
                "primary_key": [],
                "columns": [{"name": "companyid"}],
            },
            {
                "name": "companyfinancials",
                "full_name": "diffbot.companyfinancials",
                "primary_key": [],
                "columns": [{"name": "companyid"}],
            },
        ]
        candidates = _find_candidate_edges(tables)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.child_full_table == "diffbot.companyfinancials"
        assert c.parent_full_table == "diffbot.company"

    def test_candidate_full_table_falls_back_to_the_bare_name(self) -> None:
        """A hand-built table dict with no "full_name" key (or a real
        origin whose discover_schema never sets one) must not crash --
        the candidate just uses the bare name, matching today's already-
        working behaviour."""
        tables = [
            {"name": "Company", "primary_key": ["companyID"], "columns": [{"name": "companyID"}]},
            {
                "name": "Experience",
                "primary_key": ["experienceID"],
                "columns": [{"name": "experienceID"}, {"name": "companyID"}],
            },
        ]
        candidates = _find_candidate_edges(tables)
        assert candidates[0].child_full_table == "Experience"
        assert candidates[0].parent_full_table == "Company"

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

    def test_composite_pk_member_is_still_a_candidate_fk(self) -> None:
        """Found live 2026-08-10: a real ClickHouse Experience table has a
        COMPOSITE primary key (experienceID, companyID) -- companyID is
        simultaneously part of Experience's own identity AND a real FK to
        Company. The old `col_low in own_pk` check treated ANY membership
        in the pk as disqualifying, so this column was silently skipped
        and the whole structural tier found nothing for this table --
        exactly the deep-chain scenario (Company->Experience->Person) this
        whole join graph exists to support."""
        tables = [
            {"name": "Company", "primary_key": ["companyID"], "columns": [{"name": "companyID"}]},
            {
                "name": "Experience",
                "primary_key": ["experienceID", "companyID"],
                "columns": [{"name": "experienceID"}, {"name": "companyID"}],
            },
        ]
        candidates = _find_candidate_edges(tables)
        matches = [c for c in candidates if c.child_column == "companyID"]
        assert len(matches) == 1
        assert matches[0].parent_table == "Company"

    def test_single_column_pk_is_still_excluded(self) -> None:
        """A column that IS the whole (single-column) primary key is still
        excluded -- only composite-key membership stops disqualifying."""
        tables = [
            {"name": "Company", "primary_key": ["companyID"], "columns": [{"name": "companyID"}]},
            {
                "name": "Experience",
                "primary_key": ["companyID"],
                "columns": [{"name": "companyID"}],
            },
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

    def test_matches_pluralised_table_name_convention(self) -> None:
        """A common real convention this repo's own fixtures use (Pagila-
        style): a plural table "companies" with FK columns named after the
        SINGULAR concept ("company_id"), not the plural table name itself
        ("companiesid"/"companies_id" would never occur in practice)."""
        tables = [
            {"name": "companies", "primary_key": ["id"], "columns": [{"name": "id"}]},
            {
                "name": "employees",
                "primary_key": ["id"],
                "columns": [{"name": "id"}, {"name": "company_id"}],
            },
        ]
        candidates = _find_candidate_edges(tables)
        matches = [c for c in candidates if c.child_column == "company_id"]
        assert len(matches) == 1
        assert matches[0].parent_table == "companies"

    def test_irregular_plural_people_matches_person_id(self) -> None:
        tables = [
            {"name": "people", "primary_key": ["person_id"], "columns": [{"name": "person_id"}]},
            {
                "name": "jobs",
                "primary_key": ["job_id"],
                "columns": [{"name": "job_id"}, {"name": "person_id"}],
            },
        ]
        candidates = _find_candidate_edges(tables)
        matches = [
            c for c in candidates if c.child_column == "person_id" and c.child_table == "jobs"
        ]
        assert len(matches) == 1
        assert matches[0].parent_table == "people"


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

        async def fake_sample(table_name, column, limit=500, filter_values=None):
            values = overlaps.get((table_name, column), [])
            if filter_values is not None:
                return [v for v in values if v in filter_values]
            return values

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

    async def test_inclusion_check_is_column_pair_not_bag_of_values(self) -> None:
        """B4's identity_profile.py flattened EVERY column of the parent
        table into one set, so its overlap ratio only answered "does this
        value appear ANYWHERE in the other table" -- not a real inclusion
        dependency. It demonstrably promoted a child column to a verified
        FK against a completely unrelated parent column just because the
        values happened to coincide somewhere else in that table (a
        `country_code` of {"BA","AIR","LMT"} against a `ticker` column, per
        the fix-bugs plan's own account of the bug). This constructs the
        same shape: CompanyRelation.companyID candidate-matches against
        Company (by name), but its sampled values only overlap with
        Company's UNRELATED foundedYear column, not with Company's real
        primary key (companyID) that _verify_inclusion is supposed to
        check. A column-pair-correct implementation must find zero overlap
        against the real target column and discard the edge -- not go
        looking elsewhere in the table for a coincidental match.
        """
        schemas = {
            "origin_1": {
                "tables": [
                    {
                        "name": "Company",
                        "primary_key": ["companyID"],
                        "columns": [{"name": "companyID"}, {"name": "foundedYear"}],
                    },
                    {
                        "name": "CompanyRelation",
                        "primary_key": ["id"],
                        "columns": [{"name": "id"}, {"name": "companyID"}],
                    },
                ]
            }
        }

        async def fake_sample(table_name, column, limit=500, filter_values=None):
            # Company's real PK (companyID) and CompanyRelation's candidate
            # FK column are disjoint -- the correct answer is "no verified
            # edge". Company.foundedYear coincidentally holds the SAME
            # values CompanyRelation.companyID does; a bag-of-values
            # approach would wrongly "verify" against that instead.
            table = {
                ("Company", "companyID"): [1, 2, 3],
                ("Company", "foundedYear"): [77, 88, 99],
                ("CompanyRelation", "companyID"): [77, 88, 99],
            }
            values = table.get((table_name, column), [])
            if filter_values is not None:
                return [v for v in values if v in filter_values]
            return values

        fake_connector = AsyncMock()
        fake_connector.sample_distinct_values = AsyncMock(side_effect=fake_sample)
        fake_connector.close = AsyncMock()
        with patch(
            "src.mcp_server.tools.join_graph.create_connector",
            return_value=fake_connector,
        ):
            graph = await build_join_graph(
                schemas, [{"id": "origin_1", "type": "postgresql", "connection": {}}]
            )

        edges = [
            e
            for e in graph.neighbors(("origin_1", "CompanyRelation"))
            if e.parent_table == "Company"
        ]
        assert edges == []

    async def test_filtered_parent_lookup_finds_real_overlap_independent_sampling_would_miss(
        self,
    ) -> None:
        """The exact live bug found 2026-08-10: a large parent table's
        arbitrary LIMIT-N sample essentially never happens to contain the
        exact values a small child table references, even when the FK
        relationship is 100% real (verified against a real ClickHouse
        Company/CompanyFinancials pair: 0.069 overlap under independent
        sampling for a genuinely-intact relationship). `_verify_inclusion`
        must query the parent FILTERED to the child's own sampled values
        (`filter_values=`), not sample both sides independently -- this
        test's fake parent deliberately returns different results
        depending on whether it was asked "give me an arbitrary sample"
        (which excludes the child's real referenced ids) vs. "do these
        specific ids exist" (which correctly finds them)."""
        # A "large" parent table an arbitrary LIMIT sample would draw from
        # -- deliberately does NOT include c1/c2 in that arbitrary slice,
        # even though c1/c2 are real rows that exist in the full table.
        arbitrary_parent_sample = [f"c{i}" for i in range(3, 100)]
        full_parent_table = set(arbitrary_parent_sample) | {"c1", "c2"}

        async def fake_sample(table_name, column, limit=500, filter_values=None):
            if table_name == "Experience" and column == "companyID":
                return ["c1", "c2"]
            if table_name == "Company" and column == "companyID":
                if filter_values is not None:
                    return [v for v in filter_values if v in full_parent_table]
                return arbitrary_parent_sample
            return []

        fake_connector = AsyncMock()
        fake_connector.sample_distinct_values = AsyncMock(side_effect=fake_sample)
        fake_connector.close = AsyncMock()

        with patch(
            "src.mcp_server.tools.join_graph.create_connector",
            return_value=fake_connector,
        ):
            graph = await build_join_graph(_schemas_for_issue_6(), self.ORIGINS)

        edges = [
            e for e in graph.neighbors(("origin_2", "Experience")) if e.parent_table == "Company"
        ]
        assert len(edges) == 1
        assert edges[0].confidence == "verified"
        assert edges[0].overlap_ratio == 1.0

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


def _edge(
    child_table: str,
    child_column: str,
    parent_table: str,
    parent_column: str,
    *,
    origin: str = "o1",
    confidence: str = "verified",
    overlap_ratio: float | None = 1.0,
) -> JoinEdge:
    return JoinEdge(
        child_origin=origin,
        child_table=child_table,
        child_column=child_column,
        parent_origin=origin,
        parent_table=parent_table,
        parent_column=parent_column,
        confidence=confidence,
        overlap_ratio=overlap_ratio,
        rationale="test edge",
    )


def _node(table: str, origin: str = "o1") -> tuple[str, str]:
    return (origin, table)


class TestHopFrom:
    def test_child_side_is_narrow(self) -> None:
        edge = _edge("Experience", "companyID", "Company", "companyID")
        hops = _hop_from(edge, _node("Experience"))
        assert hops == [("narrow", _node("Company"))]

    def test_parent_side_is_fanout(self) -> None:
        edge = _edge("Experience", "companyID", "Company", "companyID")
        hops = _hop_from(edge, _node("Company"))
        assert hops == [("fanout", _node("Experience"))]

    def test_unrelated_node_yields_nothing(self) -> None:
        edge = _edge("Experience", "companyID", "Company", "companyID")
        assert _hop_from(edge, _node("Person")) == []


class TestFindPaths:
    def test_same_node_is_a_zero_hop_path(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        paths = find_paths(graph, _node("Company"), _node("Company"))
        assert len(paths) == 1
        assert paths[0].hop_count == 0
        assert paths[0].nodes == (_node("Company"),)

    def test_missing_subject_or_target_returns_empty(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        assert find_paths(graph, _node("Nonexistent"), _node("Company")) == []
        assert find_paths(graph, _node("Company"), _node("Nonexistent")) == []

    def test_one_hop_narrow_path(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        paths = find_paths(graph, _node("Experience"), _node("Company"))
        assert len(paths) == 1
        assert paths[0].hop_count == 1
        assert paths[0].hops[0].direction == "narrow"
        assert paths[0].fanout_hop_count == 0

    def test_one_hop_fanout_path(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        paths = find_paths(graph, _node("Company"), _node("Experience"))
        assert len(paths) == 1
        assert paths[0].hops[0].direction == "fanout"
        assert paths[0].fanout_hop_count == 1

    def test_the_issue_6_3hop_chain(self) -> None:
        """Company -> Experience -> Person -> PersonContact: exactly the
        chain the old single-junction _find_junction could never express."""
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        graph.add_edge(_edge("Experience", "personID", "Person", "personID"))
        graph.add_edge(_edge("PersonContact", "personID", "Person", "personID"))

        paths = find_paths(graph, _node("Company"), _node("PersonContact"))

        assert len(paths) == 1
        path = paths[0]
        assert path.hop_count == 3
        assert path.nodes == (
            _node("Company"),
            _node("Experience"),
            _node("Person"),
            _node("PersonContact"),
        )
        # Company->Experience (fanout), Experience->Person (narrow),
        # Person->PersonContact (fanout) -- two fan-out hops, which is
        # exactly the compounding the high_fanout flag exists to catch.
        assert [h.direction for h in path.hops] == ["fanout", "narrow", "fanout"]
        assert path.fanout_hop_count == 2
        assert path.high_fanout is True

    def test_no_path_within_max_hops_returns_empty(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        graph.add_edge(_edge("Experience", "personID", "Person", "personID"))
        graph.add_edge(_edge("PersonContact", "personID", "Person", "personID"))

        assert find_paths(graph, _node("Company"), _node("PersonContact"), max_hops=2) == []

    def test_unreachable_target_returns_empty(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        graph.add_node(_node("Location"))  # present in the graph, but disconnected
        assert find_paths(graph, _node("Company"), _node("Location")) == []

    def test_cycle_terminates_and_finds_shortest_path(self) -> None:
        """A -> B -> C -> D -> A is a genuine 4-node cycle -- must not
        infinite-loop. Both directions around the ring are 2 hops from A
        to C, so both are legitimate equal-length shortest paths; a naive
        global-visited BFS that only ever records the FIRST route found
        would silently drop one of them, which is exactly what the
        two-pass shortest-path-DAG design in find_paths avoids."""
        graph = JoinGraph()
        graph.add_edge(_edge("A", "b_id", "B", "id"))
        graph.add_edge(_edge("B", "c_id", "C", "id"))
        graph.add_edge(_edge("C", "d_id", "D", "id"))
        graph.add_edge(_edge("D", "a_id", "A", "id"))

        paths = find_paths(graph, _node("A"), _node("C"))

        assert len(paths) == 2
        assert all(p.hop_count == 2 for p in paths)
        middle_nodes = {p.nodes[1] for p in paths}
        assert middle_nodes == {_node("B"), _node("D")}

    def test_ranks_by_hop_count_first(self) -> None:
        graph = JoinGraph()
        # Direct 1-hop route.
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        # Longer 2-hop route to the same target via a detour.
        graph.add_edge(_edge("Experience", "locationID", "Location", "id"))
        graph.add_edge(_edge("Location", "companyID", "Company", "companyID"))

        paths = find_paths(graph, _node("Experience"), _node("Company"))

        assert paths[0].hop_count == 1

    def test_ties_ranked_by_confidence_then_fanout(self) -> None:
        """Two equal-length routes: the one with the weaker verified
        overlap ratio, and among equal ratios the one with fewer fan-out
        hops, must sort first... i.e. worse candidates sort last."""
        graph = JoinGraph()
        # Route 1: A -> B, weak overlap (0.6, probable).
        graph.add_edge(_edge("A", "b_id", "B", "id", overlap_ratio=0.6, confidence="probable"))
        graph.add_edge(_edge("B", "target_id", "Target", "id", overlap_ratio=1.0))
        # Route 2: A -> C, strong overlap (1.0, verified), same hop count.
        graph.add_edge(_edge("A", "c_id", "C", "id", overlap_ratio=1.0))
        graph.add_edge(_edge("C", "target_id2", "Target", "id2", overlap_ratio=1.0))

        paths = find_paths(graph, _node("A"), _node("Target"))

        assert len(paths) == 2
        assert all(p.hop_count == 2 for p in paths)
        # The route through C (min overlap 1.0) ranks ahead of the route
        # through B (min overlap 0.6, the weakest link in that chain).
        assert paths[0].nodes[1] == _node("C")
        assert paths[1].nodes[1] == _node("B")

    def test_declared_edges_have_no_overlap_ratio_but_still_rank(self) -> None:
        graph = JoinGraph()
        graph.add_edge(
            _edge(
                "Experience",
                "companyID",
                "Company",
                "companyID",
                confidence="declared",
                overlap_ratio=None,
            )
        )
        paths = find_paths(graph, _node("Experience"), _node("Company"))
        assert paths[0].min_overlap_ratio is None

    def test_max_paths_caps_the_result(self) -> None:
        graph = JoinGraph()
        # Three independent equal-length routes from A to Target.
        for name in ("B1", "B2", "B3"):
            graph.add_edge(_edge("A", f"{name.lower()}_id", name, "id"))
            graph.add_edge(_edge(name, "target_id", "Target", "id"))

        paths = find_paths(graph, _node("A"), _node("Target"), max_paths=2)
        assert len(paths) == 2

    def test_cross_origin_nodes_never_connect(self) -> None:
        """Edges are always same-origin (join_graph.py's own docstring) --
        a node from a different origin must be unreachable even if a
        same-named table exists in the graph."""
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID", origin="o1"))
        graph.add_node(_node("Company", origin="o2"))
        assert (
            find_paths(graph, _node("Experience", origin="o1"), _node("Company", origin="o2")) == []
        )


class TestJoinPath:
    def test_rank_key_orders_ascending_by_hop_count(self) -> None:
        short = JoinPath(nodes=(("o1", "A"), ("o1", "B")), hops=(_path_hop("A", "B"),))
        long = JoinPath(
            nodes=(("o1", "A"), ("o1", "X"), ("o1", "B")),
            hops=(_path_hop("A", "X"), _path_hop("X", "B")),
        )
        assert short.rank_key() < long.rank_key()

    def test_empty_hops_has_none_overlap_ratio(self) -> None:
        path = JoinPath(nodes=(("o1", "A"),), hops=())
        assert path.min_overlap_ratio is None
        assert path.fanout_hop_count == 0
        assert path.high_fanout is False

    def test_zero_hop_path_is_declared_confidence(self) -> None:
        path = JoinPath(nodes=(("o1", "A"),), hops=())
        assert path.confidence == "declared"

    def test_confidence_is_the_weakest_hop(self) -> None:
        strong = _path_hop("A", "B", confidence="verified", overlap_ratio=1.0)
        weak = _path_hop("B", "C", confidence="probable", overlap_ratio=0.6)
        path = JoinPath(nodes=(("o1", "A"), ("o1", "B"), ("o1", "C")), hops=(strong, weak))
        assert path.confidence == "probable"

    def test_declared_and_heuristic_are_not_ranked_equal(self) -> None:
        """Both carry overlap_ratio=None, but a real FK (declared) must
        outrank an unverified name guess (heuristic) -- this is the exact
        bug rank_key would have if it only compared overlap_ratio."""
        declared_path = JoinPath(
            nodes=(("o1", "A"), ("o1", "B")),
            hops=(_path_hop("A", "B", confidence="declared", overlap_ratio=None),),
        )
        heuristic_path = JoinPath(
            nodes=(("o1", "A"), ("o1", "C")),
            hops=(_path_hop("A", "C", confidence="heuristic", overlap_ratio=None),),
        )
        assert declared_path.rank_key() < heuristic_path.rank_key()


class TestJoinGraphFindEdge:
    def test_finds_edge_regardless_of_which_side_is_named_first(self) -> None:
        graph = JoinGraph()
        edge = _edge("Experience", "companyID", "Company", "companyID")
        graph.add_edge(edge)

        assert graph.find_edge("o1", "Experience", "companyID", "Company", "companyID") is edge
        # Same edge, arguments given in the opposite order.
        assert graph.find_edge("o1", "Company", "companyID", "Experience", "companyID") is edge

    def test_returns_none_when_no_such_edge(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        assert graph.find_edge("o1", "Experience", "companyID", "Person", "personID") is None

    def test_column_mismatch_does_not_match(self) -> None:
        graph = JoinGraph()
        graph.add_edge(_edge("Experience", "companyID", "Company", "companyID"))
        assert graph.find_edge("o1", "Experience", "wrongColumn", "Company", "companyID") is None


class TestPathHopDirectionAwareProperties:
    def test_narrow_hop_from_child_to_parent(self) -> None:
        from src.mcp_server.tools.join_graph import PathHop

        edge = _edge("Experience", "companyID", "Company", "companyID")
        hop = PathHop(edge=edge, direction="narrow")
        assert hop.from_table == "Experience"
        assert hop.from_column == "companyID"
        assert hop.to_table == "Company"
        assert hop.to_column == "companyID"

    def test_fanout_hop_from_parent_to_child(self) -> None:
        from src.mcp_server.tools.join_graph import PathHop

        edge = _edge("Experience", "companyID", "Company", "companyID")
        hop = PathHop(edge=edge, direction="fanout")
        assert hop.from_table == "Company"
        assert hop.from_column == "companyID"
        assert hop.to_table == "Experience"
        assert hop.to_column == "companyID"


class TestBuildJoinGraphHeuristicTier:
    async def test_missing_origin_config_adds_heuristic_edges_instead_of_dropping(self) -> None:
        """The old behaviour silently lost the join whenever a connector
        wasn't available for an origin -- unverified is a better outcome
        than absent, since a human or a downstream check can still see and
        discount a low-confidence edge, but cannot act on one that was
        never recorded at all."""
        with patch("src.mcp_server.tools.join_graph.create_connector") as mock_create:
            graph = await build_join_graph(_schemas_for_issue_6(), origins=[])

        mock_create.assert_not_called()
        edges = [
            e for e in graph.neighbors(("origin_2", "Experience")) if e.parent_table == "Company"
        ]
        assert len(edges) == 1
        assert edges[0].confidence == "heuristic"
        assert edges[0].overlap_ratio is None

    async def test_verify_inclusion_queries_the_schema_qualified_table(self) -> None:
        """End-to-end regression for the real Boeing bug: a PK-less,
        schema-qualified origin (testdiffbot's "diffbot" schema) used to
        build a join graph with 0 edges -- not because the candidates
        weren't found, but because sample_distinct_values was called with
        the bare table name ("company"), silently defaulted to "public",
        and every verification query failed. Both must now be right: the
        natural-key PK fallback finds the candidate, AND the connector is
        called with "diffbot.company" / "diffbot.companyfinancials", not
        the bare names."""
        tables = [
            {
                "name": "company",
                "full_name": "diffbot.company",
                "columns": [{"name": "companyid"}],
            },
            {
                "name": "companyfinancials",
                "full_name": "diffbot.companyfinancials",
                "columns": [{"name": "companyid"}],
            },
        ]
        schemas = {"origin_1": {"tables": tables}}
        calls: list[tuple[str, str]] = []

        async def fake_sample(table_name, column, limit=500, filter_values=None):
            calls.append((table_name, column))
            return ["c1"]

        fake_connector = AsyncMock()
        fake_connector.sample_distinct_values = AsyncMock(side_effect=fake_sample)
        fake_connector.close = AsyncMock()

        with patch(
            "src.mcp_server.tools.join_graph.create_connector",
            return_value=fake_connector,
        ):
            graph = await build_join_graph(
                schemas, [{"id": "origin_1", "type": "postgresql", "connection": {}}]
            )

        assert ("diffbot.companyfinancials", "companyid") in calls
        assert ("diffbot.company", "companyid") in calls
        assert not any(t == "company" for t, _c in calls)  # never the bare, unqualified name
        edges = [
            e
            for e in graph.neighbors(("origin_1", "companyfinancials"))
            if e.parent_table == "company"
        ]
        assert len(edges) == 1
        assert edges[0].confidence == "verified"


def _path_hop(
    child_table: str,
    parent_table: str,
    *,
    confidence: str = "verified",
    overlap_ratio: float | None = 1.0,
):
    from src.mcp_server.tools.join_graph import PathHop

    edge = _edge(
        child_table, "x_id", parent_table, "id", confidence=confidence, overlap_ratio=overlap_ratio
    )
    return PathHop(edge=edge, direction="narrow")
