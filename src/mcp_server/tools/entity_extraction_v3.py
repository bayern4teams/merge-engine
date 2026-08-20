"""V3 entity anchors, subgraph traversal, coverage, extraction (Part B, P-9;
PreSplink §30-37, §64, §65).

Extends ``join_graph.py``'s ``JoinGraph``/``find_paths`` (the same Jailer
subject+association model V2's ``plan_scope.py`` already traverses -- ADR
0009 -- not a replacement for it) with:

- Anchor discovery (`discover_anchors`): ranked candidates with evidence,
  **never** auto-picks a winner on name equality alone (PreSplink §31) --
  the caller (a human via the scope gate, or an explicit id) always makes
  the final choice.
- A traversal policy (`TraversalPolicy`) with explicit caps
  (``max_depth``/``max_nodes``/``max_edges``/``max_records``/
  ``min_edge_confidence``/``allowed_edge_types``/``blocked_edge_types``/
  ``allowed_sources``) and cycle-preventing, per-path-provenance BFS
  (`traverse_subgraph`, §65).
- A per-record context score (`compute_context_score`, §33): how directly a
  record connects to the anchor, not just whether it's reachable at all.
- A table-level and field-level entity coverage matrix
  (`build_coverage_matrix`, §34).
- An extraction planner (`plan_extraction`, §35, §55, §56) that pushes
  filters to the source and uses each connector's own cost estimate
  (P-2's ``estimate_query_cost``, an ``EXPLAIN``-backed number where the
  engine supports it) to produce a **preview** before anything expensive
  runs, and an executor (`execute_extraction`) that walks the traversal's
  real paths hop-by-hop (the same fixpoint shape V2's
  ``extract_data.py:_resolve_link_path`` already uses) and writes rows
  into the run's spool.

This module is entity-type-agnostic: it works purely in terms of
``join_graph.Node`` (``(origin_id, table_name)``) and leaves mapping a
table to a business concept ("company", "person") to its caller, since that
vocabulary (``src/orchestrator/utils/entity_aliases.py``) lives in a layer
above ``src/mcp_server/tools`` and this module must not import upward
(CLAUDE.md's layering rule).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.mcp_server.tools.join_graph import JoinEdge, JoinGraph, Node, PathHop

logger = logging.getLogger("merge-engine.tools.entity_extraction_v3")

_CONFIDENCE_RANK = {"heuristic": 0, "probable": 1, "verified": 2, "declared": 3}
_CONFIDENCE_SCORE = {"heuristic": 0.4, "probable": 0.7, "verified": 0.95, "declared": 1.0}

_DEFAULT_ANCHOR_LIMIT = 40
_DEFAULT_ANCHOR_SAMPLE = 2000


# ── §31: anchor discovery -- ranked candidates, never an auto-merge ────────


async def discover_anchors(
    connector: Any,
    origin_id: str,
    table_name: str,
    name_columns: list[str],
    subject: str,
    *,
    limit: int = _DEFAULT_ANCHOR_LIMIT,
    sample_size: int = _DEFAULT_ANCHOR_SAMPLE,
) -> list[dict[str, Any]]:
    """Ranked candidate anchor rows for ``subject`` in one (origin, table).

    Never returns a single "the" answer -- every candidate is returned with
    its own score and evidence (PreSplink §31: anchor discovery must not
    auto-merge on name equality). The caller decides which candidate(s)
    become the actual traversal anchor.

    Scoring is a simple, explainable ratio: exact case-insensitive match
    scores 1.0, a substring match scores by length ratio (the closer the
    subject is to the whole matched value, the higher), giving a real,
    inspectable evidence trail per PreSplink §22 rather than a fuzzy-match
    opaque score.
    """
    subject_norm = subject.strip().lower()
    if not subject_norm:
        return []

    candidates: list[dict[str, Any]] = []
    for column in name_columns:
        try:
            extracted = await connector.extract(
                table_name,
                [column],
                filter={column: {"op": "ilike", "value": f"%{subject}%"}},
                batch_size=sample_size,
            )
        except Exception as exc:
            logger.debug(
                "Anchor discovery failed for %s.%s.%s: %s", origin_id, table_name, column, exc
            )
            continue
        for row in extracted.get("rows", []):
            value = row.get(column)
            if value is None:
                continue
            value_norm = str(value).strip().lower()
            if subject_norm not in value_norm:
                continue
            if value_norm == subject_norm:
                score = 1.0
            else:
                score = round(len(subject_norm) / len(value_norm), 4)
            candidates.append(
                {
                    "origin_id": origin_id,
                    "table": table_name,
                    "matched_column": column,
                    "matched_value": value,
                    "row_id": row.get("id") or row.get(f"{table_name}ID"),
                    "score": score,
                    "evidence": {"match": "exact" if score == 1.0 else "substring"},
                }
            )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:limit]


# ── §65: traversal policy + subgraph traversal ─────────────────────────────


@dataclass(frozen=True)
class TraversalPolicy:
    """Every cap PreSplink §65 asks the traversal to respect. Defaults are
    conservative (small caps) -- an unbounded traversal over a real schema
    can fan out into millions of rows via a single high-cardinality hop, so
    a caller must opt into a larger budget, never get it by omission."""

    max_depth: int = 4
    max_nodes: int = 200
    max_edges: int = 400
    max_records: int = 100_000
    min_edge_confidence: str = "heuristic"
    allowed_edge_types: frozenset[str] | None = None
    blocked_edge_types: frozenset[str] = frozenset()
    allowed_sources: frozenset[str] | None = None

    def edge_allowed(self, edge: JoinEdge) -> bool:
        if self.allowed_sources is not None and (
            edge.child_origin not in self.allowed_sources
            or edge.parent_origin not in self.allowed_sources
        ):
            return False
        if self.allowed_edge_types is not None:
            return edge.confidence in self.allowed_edge_types
        if edge.confidence in self.blocked_edge_types:
            return False
        return _CONFIDENCE_RANK.get(edge.confidence, -1) >= _CONFIDENCE_RANK.get(
            self.min_edge_confidence, 0
        )


@dataclass
class NodeProvenance:
    """How one node was first reached: the direction-aware hop chain from
    an anchor (the same ``PathHop`` shape ``join_graph.py``'s ``find_paths``
    already uses, so a caller like ``plan_scope.py`` can feed this straight
    into its existing ``_entry_from_hops`` wire-shape builder), and the
    context score that chain implies (§33)."""

    path: list[PathHop]
    context_score: float


@dataclass
class TraversalResult:
    nodes: set[Node] = field(default_factory=set)
    edges_used: list[JoinEdge] = field(default_factory=list)
    provenance: dict[Node, NodeProvenance] = field(default_factory=dict)
    truncated: bool = False
    truncation_reason: str | None = None


def compute_context_score(path: list[PathHop]) -> float:
    """PreSplink §33: how directly a record connects to the anchor -- the
    product of each hop's confidence-tier score (a `declared` FK counts
    fully, a `heuristic` hop counts for much less), decayed further by hop
    count so a record five hops from the anchor never outranks one two
    hops away even if every hop happened to be `declared`."""
    if not path:
        return 1.0
    product = 1.0
    for hop in path:
        product *= _CONFIDENCE_SCORE.get(hop.edge.confidence, 0.4)
    return round(product * (0.85 ** (len(path) - 1)), 4)


def traverse_subgraph(
    graph: JoinGraph,
    anchor_nodes: list[Node],
    policy: TraversalPolicy,
) -> TraversalResult:
    """BFS from every anchor node simultaneously, respecting every cap in
    ``policy``. Cycle prevention is inherent to BFS-with-visited-set (a
    node, once reached, is never re-queued); per-path provenance records
    the first (shortest, since BFS explores level-by-level) chain of hops
    that reached each node, per PreSplink §65."""
    result = TraversalResult()
    frontier: list[tuple[Node, int, list[PathHop]]] = []
    for anchor in anchor_nodes:
        if anchor not in graph.nodes:
            continue
        if anchor not in result.nodes:
            result.nodes.add(anchor)
            result.provenance[anchor] = NodeProvenance(path=[], context_score=1.0)
            frontier.append((anchor, 0, []))

    depth_index = 0
    current_level = frontier
    while current_level:
        if depth_index >= policy.max_depth:
            if any(True for _ in current_level):
                result.truncated = True
                result.truncation_reason = f"max_depth={policy.max_depth} reached"
            break
        next_level: list[tuple[Node, int, list[PathHop]]] = []
        for node, _depth, path_so_far in current_level:
            for edge in graph.neighbors(node):
                if len(result.edges_used) >= policy.max_edges:
                    result.truncated = True
                    result.truncation_reason = f"max_edges={policy.max_edges} reached"
                    break
                if not policy.edge_allowed(edge):
                    continue
                child_node = (edge.child_origin, edge.child_table)
                parent_node = (edge.parent_origin, edge.parent_table)
                # "narrow" = crossed child -> parent (this node is the
                # child); "fanout" = crossed parent -> child.
                direction = "narrow" if node == child_node else "fanout"
                other = parent_node if node == child_node else child_node
                if other == node or other in result.nodes:
                    continue
                if len(result.nodes) >= policy.max_nodes:
                    result.truncated = True
                    result.truncation_reason = f"max_nodes={policy.max_nodes} reached"
                    continue
                hop = PathHop(edge=edge, direction=direction)
                new_path = [*path_so_far, hop]
                result.nodes.add(other)
                result.edges_used.append(edge)
                result.provenance[other] = NodeProvenance(
                    path=new_path, context_score=compute_context_score(new_path)
                )
                next_level.append((other, depth_index + 1, new_path))
            if len(result.edges_used) >= policy.max_edges:
                break
        current_level = next_level
        depth_index += 1

    return result


# ── §34: entity coverage matrix, table-level and field-level ──────────────


def build_coverage_matrix(traversal: TraversalResult, schemas: dict[str, Any]) -> dict[str, Any]:
    """Every table in ``schemas`` (the ``discover_schemas`` state key
    shape: ``{origin_id: {"tables": [{"name", "columns": [...]}]}}``),
    marked reached/not-reached, plus which of its columns are visible
    (declared in the schema) for every table the traversal actually
    reached -- table-level and field-level coverage in one structure."""
    table_coverage: dict[str, bool] = {}
    field_coverage: dict[str, dict[str, bool]] = {}

    for origin_id, schema_data in (schemas or {}).items():
        for table in (schema_data or {}).get("tables", []) or []:
            name = table.get("name")
            if not name:
                continue
            node_key = f"{origin_id}:{name}"
            reached = (origin_id, name) in traversal.nodes
            table_coverage[node_key] = reached
            if reached:
                field_coverage[node_key] = {
                    col.get("name"): True
                    for col in table.get("columns", []) or []
                    if col.get("name")
                }

    total = len(table_coverage)
    reached_count = sum(1 for v in table_coverage.values() if v)
    return {
        "table_coverage": table_coverage,
        "field_coverage": field_coverage,
        "tables_reached": reached_count,
        "tables_total": total,
        "coverage_ratio": round(reached_count / total, 4) if total else 0.0,
    }


# ── §35, §55, §56: extraction planning (preview) + execution ───────────────


@dataclass
class PlannedTable:
    node: Node
    context_score: float
    estimated_cost: dict[str, Any] | None


@dataclass
class ExtractionPlan:
    tables: list[PlannedTable]
    truncated: bool
    truncation_reason: str | None

    def total_estimated_rows(self) -> int | None:
        total = 0
        for t in self.tables:
            if not t.estimated_cost:
                return None
            rows = t.estimated_cost.get("estimated_rows")
            if rows is None:
                return None
            total += rows
        return total


async def plan_extraction(
    traversal: TraversalResult,
    connectors_by_origin: dict[str, Any],
) -> ExtractionPlan:
    """A preview: for every reached table, ask its own connector for a cost
    estimate (P-2's ``estimate_query_cost`` -- ``EXPLAIN``-backed where the
    engine supports it) for an unfiltered scan, the worst case a filtered
    extraction could ever cost. Issues no extraction queries itself."""
    planned: list[PlannedTable] = []
    for node in sorted(traversal.nodes):
        origin_id, table_name = node
        connector = connectors_by_origin.get(origin_id)
        cost: dict[str, Any] | None = None
        if connector is not None:
            try:
                cost = await connector.estimate_query_cost(table_name)
            except Exception as exc:
                logger.debug("Cost estimate failed for %s.%s: %s", origin_id, table_name, exc)
        provenance = traversal.provenance.get(node)
        planned.append(
            PlannedTable(
                node=node,
                context_score=provenance.context_score if provenance else 1.0,
                estimated_cost=cost,
            )
        )
    return ExtractionPlan(
        tables=planned, truncated=traversal.truncated, truncation_reason=traversal.truncation_reason
    )


async def execute_extraction(
    traversal: TraversalResult,
    anchor_nodes: list[Node],
    anchor_filter: dict[str, Any],
    connectors_by_origin: dict[str, Any],
    *,
    batch_size: int = 1000,
) -> dict[Node, list[dict[str, Any]]]:
    """Walk the traversal's real paths hop-by-hop, seeding from the anchor
    node(s)' own filtered rows and propagating known values one hop at a
    time -- the same fixpoint shape V2's ``extract_data.py:
    _resolve_link_path`` already uses, generalized to every node the
    traversal reached rather than one target table at a time.

    Returns ``{node: [rows]}`` for every reached table with at least one
    matching row. The caller (the orchestrator-layer flag branch) owns
    writing these into the run's ``RunSpool`` and building
    ``make_handle()`` entries, since this module has no spool/state
    knowledge (kept entity-type- and state-shape-agnostic).
    """
    results: dict[Node, list[dict[str, Any]]] = {}
    # Per-node, per-column value sets -- NOT a flat merged set of every
    # value in every column, which would let an unrelated column's values
    # (e.g. a phone number) leak into a filter meant for the join column.
    known_values: dict[Node, dict[str, set[Any]]] = {}

    def _index_by_column(rows: list[dict[str, Any]]) -> dict[str, set[Any]]:
        by_column: dict[str, set[Any]] = {}
        for row in rows:
            for col, val in row.items():
                if val is not None:
                    by_column.setdefault(col, set()).add(val)
        return by_column

    for anchor in anchor_nodes:
        if anchor not in traversal.nodes:
            continue
        origin_id, table_name = anchor
        connector = connectors_by_origin.get(origin_id)
        if connector is None:
            continue
        extracted = await connector.extract(
            table_name, ["*"], filter=anchor_filter, batch_size=batch_size
        )
        rows = extracted.get("rows", [])
        results[anchor] = rows
        known_values[anchor] = _index_by_column(rows)

    # BFS again, this time propagating actual values hop by hop -- a node
    # can only be filled in once every hop on its shortest path has run,
    # so process nodes in provenance-path-length order (shallowest first).
    ordered = sorted(
        (n for n in traversal.nodes if n not in results),
        key=lambda n: len(traversal.provenance[n].path) if n in traversal.provenance else 999,
    )
    for node in ordered:
        provenance = traversal.provenance.get(node)
        if not provenance or not provenance.path:
            continue
        last_hop = provenance.path[-1]
        # PathHop's own from_*/to_* properties are already direction-aware
        # ("select to_table rows whose to_column is in the known
        # from_table.from_column values") -- edges are always intra-origin
        # (JoinEdge's own contract), so `node`'s origin_id applies to both
        # sides.
        origin_id = node[0]
        seed_node = (origin_id, last_hop.from_table)
        seed_column = last_hop.from_column
        filter_column = last_hop.to_column

        seed_values = known_values.get(seed_node, {}).get(seed_column)
        if not seed_values:
            continue
        table_name = node[1]
        connector = connectors_by_origin.get(origin_id)
        if connector is None:
            continue
        # The RAW values, never normalize_val()'d -- that helper lowercases
        # and strips URL prefixes for cross-column equality COMPARISON
        # (join_graph.py's own inclusion-dependency check), which silently
        # broke every real query here: a case-sensitive id column like
        # "E-xSjHM5bMJmwwRfrYLwwkw" filtered on its lowercased form matches
        # nothing in the real (case-sensitive) database. Live-caught (BP-B,
        # P-9, 2026-08-19) against real ClickHouse origin1 -- a Boeing
        # anchor query returned 18 real companyID values, but every
        # downstream hop (CompanyContact, CompanyExperience, ...) came back
        # with 0 rows until this was fixed.
        filter_values = [v for v in seed_values if v is not None]
        if not filter_values:
            continue
        extracted = await connector.extract(
            table_name,
            ["*"],
            filter={filter_column: {"op": "in", "value": filter_values}},
            batch_size=batch_size,
        )
        rows = extracted.get("rows", [])
        if rows:
            results[node] = rows
            known_values[node] = _index_by_column(rows)

    return results
