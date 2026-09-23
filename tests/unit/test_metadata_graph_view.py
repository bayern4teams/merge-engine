"""Unit tests for V3 metadata graph view (Part B, P-8).

Covers the containment chain (source->database->schema->table->column),
the two semantic edge types (relationship, field_mapping), JSON
serialization, and the four filters (confidence/type/source/depth).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.mcp_server.tools.metadata_graph_view import (
    build_metadata_graph,
    filter_by_depth,
    filter_by_min_confidence,
    filter_by_node_type,
    filter_by_source,
    graph_to_json,
)


def _mock_store(
    *,
    sources=None,
    databases=None,
    schemas=None,
    tables=None,
    columns=None,
    canonical_fields=None,
    relationships=None,
    field_mappings=None,
) -> MagicMock:
    store = MagicMock()
    store.list_sources.return_value = sources or []
    store.list_databases.return_value = databases or []
    store.list_schemas.return_value = schemas or []
    store.list_tables.return_value = tables or []
    store.list_columns.return_value = columns or []
    store.list_canonical_fields.return_value = canonical_fields or []
    store.list_relationships.return_value = relationships or []
    store.list_field_mappings.return_value = field_mappings or []
    return store


class TestBuildMetadataGraphContainment:
    def test_full_containment_chain_is_built(self) -> None:
        store = _mock_store(
            sources=[{"id": "s1", "name": "origin1", "type": "clickhouse", "host": "", "port": 0}],
            databases=[{"id": "d1", "source_id": "s1", "name": "origin1"}],
            schemas=[{"id": "sc1", "database_id": "d1", "name": "origin1"}],
            tables=[{"id": "t1", "schema_id": "sc1", "name": "Company", "row_count_estimate": 100}],
            columns=[
                {
                    "id": "c1",
                    "table_id": "t1",
                    "name": "companyID",
                    "data_type": "String",
                    "ordinal_position": 1,
                    "nullable": False,
                }
            ],
        )
        graph = build_metadata_graph(store)
        assert graph.has_node("source:s1")
        assert graph.has_node("database:d1")
        assert graph.has_node("schema:sc1")
        assert graph.has_node("table:t1")
        assert graph.has_node("column:c1")
        assert graph.has_edge("source:s1", "database:d1")
        assert graph.has_edge("database:d1", "schema:sc1")
        assert graph.has_edge("schema:sc1", "table:t1")
        assert graph.has_edge("table:t1", "column:c1")
        assert graph["table:t1"]["column:c1"]["edge_type"] == "contains"
        assert graph["table:t1"]["column:c1"]["confidence"] == 1.0

    def test_canonical_fields_are_nodes_with_no_containment_parent(self) -> None:
        store = _mock_store(
            canonical_fields=[
                {
                    "id": "cf1",
                    "entity_type": "company",
                    "field_name": "company_id",
                    "data_type": "String",
                    "description": "",
                }
            ]
        )
        graph = build_metadata_graph(store)
        assert graph.nodes["canonical_field:cf1"]["node_type"] == "canonical_field"
        assert graph.nodes["canonical_field:cf1"]["label"] == "company.company_id"
        assert graph.in_degree("canonical_field:cf1") == 0


class TestBuildMetadataGraphSemanticEdges:
    def _base_columns_store(self, **kwargs) -> MagicMock:
        return _mock_store(
            columns=[
                {
                    "id": "c1",
                    "table_id": "t1",
                    "name": "companyID",
                    "data_type": "String",
                    "ordinal_position": 1,
                    "nullable": False,
                },
                {
                    "id": "c2",
                    "table_id": "t2",
                    "name": "companyID",
                    "data_type": "String",
                    "ordinal_position": 1,
                    "nullable": False,
                },
            ],
            canonical_fields=[
                {
                    "id": "cf1",
                    "entity_type": "company",
                    "field_name": "company_id",
                    "data_type": "String",
                    "description": "",
                }
            ],
            **kwargs,
        )

    def test_relationship_edge_carries_full_evidence(self) -> None:
        store = self._base_columns_store(
            relationships=[
                {
                    "id": "r1",
                    "from_column_id": "c1",
                    "to_column_id": "c2",
                    "relationship_type": "FOREIGN_KEY",
                    "coverage": 1.0,
                    "signals": {"datatype": {"score": 1.0}},
                    "confidence": 0.91,
                    "evidence": {"aggregation": "average"},
                    "origin": "ALGORITHMIC",
                    "review_status": "AUTO_ACCEPTED",
                    "run_id": None,
                }
            ]
        )
        graph = build_metadata_graph(store)
        edge = graph["column:c1"]["column:c2"]
        assert edge["edge_type"] == "relationship"
        assert edge["relationship_type"] == "FOREIGN_KEY"
        assert edge["confidence"] == 0.91
        assert edge["coverage"] == 1.0
        assert edge["evidence"] == {"aggregation": "average"}

    def test_field_mapping_edge_points_at_a_canonical_field(self) -> None:
        store = self._base_columns_store(
            field_mappings=[
                {
                    "id": "fm1",
                    "source_column_id": "c1",
                    "canonical_field_id": "cf1",
                    "confidence": 1.0,
                    "evidence": {},
                    "origin": "ALGORITHMIC",
                    "review_status": "AUTO_ACCEPTED",
                    "run_id": None,
                }
            ]
        )
        graph = build_metadata_graph(store)
        edge = graph["column:c1"]["canonical_field:cf1"]
        assert edge["edge_type"] == "field_mapping"
        assert edge["origin"] == "ALGORITHMIC"

    def test_a_relationship_pointing_at_a_missing_column_is_skipped_not_a_crash(self) -> None:
        store = self._base_columns_store(
            relationships=[
                {
                    "id": "r1",
                    "from_column_id": "c1",
                    "to_column_id": "does-not-exist",
                    "relationship_type": "FOREIGN_KEY",
                    "coverage": 1.0,
                    "signals": {},
                    "confidence": 0.9,
                    "evidence": {},
                    "origin": "ALGORITHMIC",
                    "review_status": "AUTO_ACCEPTED",
                    "run_id": None,
                }
            ]
        )
        graph = build_metadata_graph(store)
        assert not graph.has_edge("column:c1", "column:does-not-exist")

    def test_run_id_is_passed_through_to_the_store_queries(self) -> None:
        store = self._base_columns_store()
        build_metadata_graph(store, run_id="run-42")
        store.list_relationships.assert_called_once_with(run_id="run-42")
        store.list_field_mappings.assert_called_once_with(run_id="run-42")


class TestGraphToJson:
    def test_nodes_and_edges_are_flattened_with_full_metadata(self) -> None:
        store = _mock_store(
            sources=[{"id": "s1", "name": "origin1", "type": "clickhouse", "host": "", "port": 0}],
            databases=[{"id": "d1", "source_id": "s1", "name": "origin1"}],
        )
        graph = build_metadata_graph(store)
        payload = graph_to_json(graph)
        node_ids = {n["id"] for n in payload["nodes"]}
        assert "source:s1" in node_ids
        assert "database:d1" in node_ids
        edge = next(e for e in payload["edges"] if e["source"] == "source:s1")
        assert edge["edge_type"] == "contains"
        assert edge["confidence"] == 1.0


class TestFilters:
    def _sample_graph(self):
        store = _mock_store(
            sources=[{"id": "s1", "name": "origin1", "type": "clickhouse", "host": "", "port": 0}],
            databases=[{"id": "d1", "source_id": "s1", "name": "origin1"}],
            schemas=[{"id": "sc1", "database_id": "d1", "name": "origin1"}],
            tables=[{"id": "t1", "schema_id": "sc1", "name": "Company", "row_count_estimate": 100}],
            columns=[
                {
                    "id": "c1",
                    "table_id": "t1",
                    "name": "companyID",
                    "data_type": "String",
                    "ordinal_position": 1,
                    "nullable": False,
                },
                {
                    "id": "c2",
                    "table_id": "t1",
                    "name": "companyID2",
                    "data_type": "String",
                    "ordinal_position": 2,
                    "nullable": False,
                },
            ],
            relationships=[
                {
                    "id": "r1",
                    "from_column_id": "c1",
                    "to_column_id": "c2",
                    "relationship_type": "POSSIBLE_JOIN",
                    "coverage": 0.4,
                    "signals": {},
                    "confidence": 0.4,
                    "evidence": {},
                    "origin": "ALGORITHMIC",
                    "review_status": "NEEDS_REVIEW",
                    "run_id": None,
                }
            ],
        )
        return build_metadata_graph(store)

    def test_min_confidence_drops_weak_edges_but_keeps_containment(self) -> None:
        graph = self._sample_graph()
        filtered = filter_by_min_confidence(graph, 0.7)
        assert not filtered.has_edge("column:c1", "column:c2")
        assert filtered.has_edge("table:t1", "column:c1")

    def test_node_type_filter_keeps_only_requested_types(self) -> None:
        graph = self._sample_graph()
        filtered = filter_by_node_type(graph, {"table", "column"})
        node_types = {attrs["node_type"] for _, attrs in filtered.nodes(data=True)}
        assert node_types == {"table", "column"}

    def test_source_filter_keeps_only_that_sources_descendants(self) -> None:
        graph = self._sample_graph()
        filtered = filter_by_source(graph, {"s1"})
        assert "column:c1" in filtered.nodes
        assert "source:s1" in filtered.nodes

    def test_depth_filter_bounds_the_neighborhood(self) -> None:
        graph = self._sample_graph()
        filtered = filter_by_depth(graph, "table:t1", max_depth=1)
        assert "column:c1" in filtered.nodes
        assert "schema:sc1" in filtered.nodes
        assert "source:s1" not in filtered.nodes

    def test_depth_filter_on_a_missing_focus_node_returns_empty(self) -> None:
        graph = self._sample_graph()
        filtered = filter_by_depth(graph, "table:does-not-exist", max_depth=2)
        assert len(filtered.nodes) == 0
