"""V3 metadata graph view (Part B, P-8; PreSplink §28, §50, §63).

networkx (ADR 0009) over the P-1 metadata tables -- in-process traversal
built fresh from PostgreSQL rows on every call, never a second persistence
layer. Node types: ``source``, ``database``, ``schema``, ``table``,
``column``, ``canonical_field``. Edge types: ``contains`` (the structural
source->database->schema->table->column chain, confidence 1.0),
``relationship`` (P-6's ``md_relationship``, column->column), and
``field_mapping`` (P-7's ``md_field_mapping``, column->canonical_field).

This is strictly the METADATA graph -- tables and columns, never entity
records ("Airbus" the company, "John Smith" the person). Per §63 and ADR
0009's own consequence, the entity graph (P-9's job) is a different
networkx graph object over different tables; the two are never merged into
one, and this module has no code path that could produce an entity node.
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx  # type: ignore[import-untyped]  # networkx ships no type stubs

logger = logging.getLogger("merge-engine.tools.metadata_graph_view")


def build_metadata_graph(store: Any, *, run_id: str | None = None) -> nx.DiGraph:
    """Read every P-1 metadata table and build one ``networkx.DiGraph``.

    ``run_id``, when given, restricts ``relationship``/``field_mapping``
    edges to that run -- containment edges (source/database/schema/table/
    column) are never run-scoped, since a column's existence is a fact
    about the schema, not a per-run measurement.
    """
    graph = nx.DiGraph()

    for row in store.list_sources():
        graph.add_node(f"source:{row['id']}", node_type="source", label=row["name"], data=row)
    for row in store.list_databases():
        node_id = f"database:{row['id']}"
        graph.add_node(node_id, node_type="database", label=row["name"], data=row)
        graph.add_edge(f"source:{row['source_id']}", node_id, edge_type="contains", confidence=1.0)
    for row in store.list_schemas():
        node_id = f"schema:{row['id']}"
        graph.add_node(node_id, node_type="schema", label=row["name"], data=row)
        graph.add_edge(
            f"database:{row['database_id']}", node_id, edge_type="contains", confidence=1.0
        )
    for row in store.list_tables():
        node_id = f"table:{row['id']}"
        graph.add_node(node_id, node_type="table", label=row["name"], data=row)
        graph.add_edge(f"schema:{row['schema_id']}", node_id, edge_type="contains", confidence=1.0)
    for row in store.list_columns():
        node_id = f"column:{row['id']}"
        graph.add_node(node_id, node_type="column", label=row["name"], data=row)
        graph.add_edge(f"table:{row['table_id']}", node_id, edge_type="contains", confidence=1.0)
    for row in store.list_canonical_fields():
        node_id = f"canonical_field:{row['id']}"
        label = f"{row['entity_type']}.{row['field_name']}"
        graph.add_node(node_id, node_type="canonical_field", label=label, data=row)

    for row in store.list_relationships(run_id=run_id):
        from_id = f"column:{row['from_column_id']}"
        to_id = f"column:{row['to_column_id']}"
        if from_id not in graph or to_id not in graph:
            # A relationship can outlive the column it points at (e.g. a
            # schema re-discovery that dropped a column) -- skip rather
            # than let networkx silently create a phantom node with no
            # metadata behind it.
            logger.debug("Skipping relationship %s: endpoint column missing", row["id"])
            continue
        graph.add_edge(
            from_id,
            to_id,
            edge_type="relationship",
            relationship_type=row["relationship_type"],
            confidence=row["confidence"],
            coverage=row["coverage"],
            evidence=row["evidence"],
            review_status=row["review_status"],
            data=row,
        )
    for row in store.list_field_mappings(run_id=run_id):
        from_id = f"column:{row['source_column_id']}"
        to_id = f"canonical_field:{row['canonical_field_id']}"
        if from_id not in graph or to_id not in graph:
            logger.debug("Skipping field_mapping %s: endpoint missing", row["id"])
            continue
        graph.add_edge(
            from_id,
            to_id,
            edge_type="field_mapping",
            confidence=row["confidence"],
            origin=row["origin"],
            evidence=row["evidence"],
            review_status=row["review_status"],
            data=row,
        )
    return graph


def graph_to_json(graph: nx.DiGraph) -> dict[str, Any]:
    """Serialize for the frontend: one flat ``nodes``/``edges`` list, every
    field the UI needs to render without a second round-trip (click node ->
    metadata, click edge -> evidence, per the plan's own text)."""
    nodes = [
        {
            "id": node_id,
            "type": attrs.get("node_type"),
            "label": attrs.get("label"),
            "data": attrs.get("data"),
        }
        for node_id, attrs in graph.nodes(data=True)
    ]
    edges = [
        {
            "source": u,
            "target": v,
            "edge_type": attrs.get("edge_type"),
            "relationship_type": attrs.get("relationship_type"),
            "confidence": attrs.get("confidence"),
            "coverage": attrs.get("coverage"),
            "origin": attrs.get("origin"),
            "review_status": attrs.get("review_status"),
            "evidence": attrs.get("evidence"),
        }
        for u, v, attrs in graph.edges(data=True)
    ]
    return {"nodes": nodes, "edges": edges}


def filter_by_min_confidence(graph: nx.DiGraph, min_confidence: float) -> nx.DiGraph:
    """Drop edges below ``min_confidence`` -- ``contains`` edges (always
    1.0) and edges with no confidence recorded are never dropped by this
    filter, since "no measurement" is not the same claim as "measured low"."""
    keep = [
        (u, v)
        for u, v, attrs in graph.edges(data=True)
        if attrs.get("confidence") is None or attrs["confidence"] >= min_confidence
    ]
    return graph.edge_subgraph(keep).copy()


def filter_by_node_type(graph: nx.DiGraph, node_types: set[str]) -> nx.DiGraph:
    """Keep only nodes of the given types (and edges between surviving
    nodes)."""
    keep = [n for n, attrs in graph.nodes(data=True) if attrs.get("node_type") in node_types]
    return graph.subgraph(keep).copy()


def filter_by_source(graph: nx.DiGraph, source_ids: set[str]) -> nx.DiGraph:
    """Keep only nodes reachable (by containment) from the given source
    node ids, plus canonical_field nodes (source-independent by design, per
    P-7 -- they are never dropped just because the current source filter
    excludes every column mapped to them)."""
    keep: set[str] = set()
    for source_id in source_ids:
        node_id = f"source:{source_id}" if not source_id.startswith("source:") else source_id
        if node_id in graph:
            keep.add(node_id)
            keep.update(nx.descendants(graph, node_id))
    keep.update(
        n for n, attrs in graph.nodes(data=True) if attrs.get("node_type") == "canonical_field"
    )
    return graph.subgraph(keep).copy()


def filter_by_depth(graph: nx.DiGraph, focus_node_id: str, max_depth: int) -> nx.DiGraph:
    """Keep only nodes within ``max_depth`` hops of ``focus_node_id``,
    traversing edges in either direction (the metadata graph is directed
    for edge semantics, but "nearby" for a human exploring it means either
    direction)."""
    undirected = graph.to_undirected(as_view=True)
    if focus_node_id not in undirected:
        return graph.subgraph([]).copy()
    lengths = nx.single_source_shortest_path_length(undirected, focus_node_id, cutoff=max_depth)
    return graph.subgraph(lengths.keys()).copy()
