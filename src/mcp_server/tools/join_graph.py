"""
Real join graph over discovered origin schemas (Phase 4.1 of the fix-bugs
follow-up plan).

`plan_scope.py`'s `_find_junction` can express exactly one junction table
(two hops) before this, and `analyze_dependencies.py`'s coverage matrix is
pure column-name guessing with no verification at all — together the reason
issue 6 from the 4th live run (Bugs/Logs/4th_try_05_18_local_enrichprompt.txt)
could not find `Company -> Experience -> Person -> PersonContact` (3 hops):
the join graph to search for that path did not exist, and even a 2-hop
`Company -> Experience -> Person` chain was only a name-matching guess, never
checked against real data.

Edges come from three tiers, descending confidence:

1. Declared foreign keys — real and authoritative when present, but ALWAYS
   EMPTY for ClickHouse and MongoDB origins (see clickhouse.py:248 /
   mongodb.py:172), which is what both real origins in the 4th run are.
2. Name heuristics — a column named like `<OtherTable>ID` naming a
   candidate parent, matched against that table's ACTUAL primary key
   (never a hard-coded "id"), and never a bare `.startswith()` match (that
   clause used to fabricate edges like `Person.company_name -> Company.id`
   whenever the column merely started with another table's name).
3. Inclusion-dependency verification — the tier that actually works on
   these origins. Every name-heuristic candidate is checked against real
   sampled data before being trusted: does the child column's value set
   sit inside the parent key's value set? Built from B4-matching-precision's
   identity_profile.py (normalize_val, verbatim), reshaped from a
   bag-of-every-column-in-the-foreign-table (which cannot tell a real
   inclusion dependency from an unrelated column collision — see
   docs/plans, the country_code/ticker example) into a genuine per-column-
   pair containment ratio, and pushed into SQL via each connector's new
   `sample_distinct_values` rather than pulling full tables into Python.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from src.mcp_server.connectors import create_connector

logger = logging.getLogger("merge-engine.tools.join_graph")

_DEFAULT_SAMPLE_SIZE = 500
_VERIFIED_THRESHOLD = 0.95
_PROBABLE_THRESHOLD = 0.5
_DEFAULT_MAX_HOPS = 6
_HIGH_FANOUT_THRESHOLD = 2

# Node = (origin_id, table_name).
Node = tuple[str, str]


def normalize_val(val: Any) -> str:
    """Normalize a value for cross-column overlap comparison.

    Verbatim from B4-matching-precision's identity_profile.py — strips
    protocol/www prefixes and a trailing slash so "https://airbus.com/" and
    "airbus.com" compare equal, lowercases and trims.
    """
    if val is None:
        return ""
    s = str(val).strip().lower()
    for prefix in ("https://", "http://", "www."):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    return s.strip("/")


@dataclass(frozen=True)
class JoinEdge:
    """One verified (or declared) join key between two tables.

    Always directed child -> parent (many-to-one): `child_column` on
    `child_table` is expected to contain values found in `parent_column`
    (the parent's key) on `parent_table`. Both tables are always in the
    same origin — cross-origin "same entity" relationships are Phase 3's
    entity_grouping's job, not a join-graph edge.
    """

    child_origin: str
    child_table: str
    child_column: str
    parent_origin: str
    parent_table: str
    parent_column: str
    confidence: str  # "declared" | "verified" | "probable"
    overlap_ratio: float | None
    rationale: str


@dataclass
class JoinGraph:
    """Nodes = (origin_id, table). Edges are stored on both endpoints so a
    traversal starting from either the child or the parent side finds them —
    Phase 4.2's BFS distinguishes "child->parent" (narrows) from
    "parent->child" (fans out) by comparing the current node against each
    edge's own child_table/parent_table fields, not by which adjacency list
    it came from.
    """

    nodes: set[Node] = field(default_factory=set)
    _adjacency: dict[Node, list[JoinEdge]] = field(default_factory=dict)

    def add_node(self, node: Node) -> None:
        self.nodes.add(node)

    def add_edge(self, edge: JoinEdge) -> None:
        child_node = (edge.child_origin, edge.child_table)
        parent_node = (edge.parent_origin, edge.parent_table)
        self.nodes.add(child_node)
        self.nodes.add(parent_node)
        self._adjacency.setdefault(child_node, []).append(edge)
        self._adjacency.setdefault(parent_node, []).append(edge)

    def neighbors(self, node: Node) -> list[JoinEdge]:
        """Every edge touching `node`, in either direction."""
        return list(self._adjacency.get(node, []))

    def __len__(self) -> int:
        return sum(len(v) for v in self._adjacency.values()) // 2


def _resolve_primary_key(table: dict[str, Any]) -> list[str]:
    """A table's real primary key columns.

    MongoDB's discover_schema never sets a "primary_key" key at all (it has
    no schema-level concept of one) — `_id` is the implicit unique key, and
    it always shows up as an ordinary field inside "columns" since it's just
    another key on the sampled documents.
    """
    pk = table.get("primary_key")
    if pk:
        return [str(c) for c in pk]
    column_names = {c.get("name") for c in table.get("columns", []) or []}
    if "_id" in column_names:
        return ["_id"]
    return []


@dataclass(frozen=True)
class _EdgeCandidate:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str


def _find_candidate_edges(
    tables: list[dict[str, Any]],
) -> list[_EdgeCandidate]:
    """Name-heuristic candidates within one origin — tier 2, feeding tier 3.

    Same matching rule analyze_dependencies.py's coverage matrix used to
    use, MINUS the `col_low.startswith(ot_low)` clause: that clause matched
    any column merely starting with another table's name (fabricating, e.g.,
    "Person.company_name" -> "Company.id" — a real column, a real table, and
    nothing at all to do with each other). Every remaining candidate here
    still has to survive the inclusion-dependency check in build_join_graph
    before becoming a real edge.
    """
    table_by_name: dict[str, dict[str, Any]] = {
        str(t.get("name")): t for t in tables if t.get("name")
    }
    pk_by_table = {name: _resolve_primary_key(t) for name, t in table_by_name.items()}

    candidates: list[_EdgeCandidate] = []
    for table in tables:
        table_name = table.get("name")
        if not table_name:
            continue
        own_pk = {c.lower() for c in pk_by_table.get(table_name, [])}
        for col in table.get("columns", []) or []:
            col_name = col.get("name", "")
            if not col_name:
                continue
            col_low = col_name.lower()
            # Skip this table's own primary key -- it identifies this
            # table's own rows, it does not point elsewhere.
            if col_low in own_pk or col_low in (
                "id",
                f"{table_name.lower()}id",
                f"{table_name.lower()}_id",
            ):
                continue
            for other_name, other_pk in pk_by_table.items():
                if other_name == table_name or not other_pk:
                    continue
                ot_low = other_name.lower()
                if col_low in (f"{ot_low}id", f"{ot_low}_id", f"{ot_low}key", f"{ot_low}_key"):
                    candidates.append(
                        _EdgeCandidate(
                            child_table=table_name,
                            child_column=col_name,
                            parent_table=other_name,
                            # The other table's ACTUAL primary key -- never
                            # a hard-coded "id".
                            parent_column=other_pk[0],
                        )
                    )
    return candidates


async def _verify_inclusion(
    connector: Any,
    candidate: _EdgeCandidate,
    sample_size: int,
) -> float | None:
    """Sample both sides and compute |child ∩ parent| / |child|.

    Returns None if either side failed to sample or the child column has no
    usable values to test — never raises, since one bad candidate must not
    abort building the rest of the graph.
    """
    try:
        child_vals = await connector.sample_distinct_values(
            candidate.child_table, candidate.child_column, limit=sample_size
        )
        parent_vals = await connector.sample_distinct_values(
            candidate.parent_table, candidate.parent_column, limit=sample_size
        )
    except Exception as exc:
        logger.debug(
            "Inclusion-dependency sampling failed for %s.%s -> %s.%s: %s",
            candidate.child_table,
            candidate.child_column,
            candidate.parent_table,
            candidate.parent_column,
            exc,
        )
        return None

    child_norm = {normalize_val(v) for v in child_vals if normalize_val(v)}
    parent_norm = {normalize_val(v) for v in parent_vals if normalize_val(v)}
    if not child_norm:
        return None
    return len(child_norm & parent_norm) / len(child_norm)


async def build_join_graph(
    schemas: dict[str, Any],
    origins: list[dict[str, Any]],
    *,
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
) -> JoinGraph:
    """Build the real join graph a subject-first extraction searches over.

    Parameters
    ----------
    schemas : dict
        The ``schemas`` state key: ``{origin_id: {"tables": [...]}}`` as
        produced by ``discover_schemas``.
    origins : list[dict]
        ``state["origins"]`` — needed to open a connector per origin for
        the inclusion-dependency tier. Each dict has ``id``/``type``/
        ``connection``.
    sample_size : int
        How many distinct values to sample per column when verifying an
        inclusion dependency. Bounded and pushed into SQL/aggregation by
        each connector's ``sample_distinct_values`` — never a full read of
        an origin table, some of which run to tens of millions of rows.

    Returns
    -------
    JoinGraph
    """
    graph = JoinGraph()
    origins_by_id = {str(o.get("id")): o for o in origins or []}

    for origin_id, schema_data in (schemas or {}).items():
        for table in (schema_data or {}).get("tables", []) or []:
            name = table.get("name")
            if name:
                graph.add_node((origin_id, name))

    for origin_id, schema_data in (schemas or {}).items():
        tables = (schema_data or {}).get("tables", []) or []
        table_by_name = {t.get("name"): t for t in tables if t.get("name")}

        # Tier 1: declared FKs. Always empty for ClickHouse/MongoDB origins
        # (clickhouse.py:248, mongodb.py:172) -- real and unconditionally
        # trusted when present (PostgreSQL).
        for table in tables:
            table_name = table.get("name")
            if not table_name:
                continue
            for fk in table.get("foreign_keys", []) or []:
                ref_table = fk.get("ref_table")
                ref_column = fk.get("ref_column")
                column = fk.get("column")
                if not ref_table or ref_table not in table_by_name or not column or not ref_column:
                    continue
                graph.add_edge(
                    JoinEdge(
                        child_origin=origin_id,
                        child_table=table_name,
                        child_column=column,
                        parent_origin=origin_id,
                        parent_table=ref_table,
                        parent_column=ref_column,
                        confidence="declared",
                        overlap_ratio=None,
                        rationale=f"Declared FK: {table_name}.{column} -> {ref_table}.{ref_column}",
                    )
                )

        # Tiers 2+3: name-heuristic candidates, verified against real data.
        candidates = _find_candidate_edges(tables)
        if not candidates:
            continue

        origin_config = origins_by_id.get(origin_id)
        if origin_config is None:
            logger.warning(
                "No origin config for '%s' -- skipping inclusion-dependency "
                "verification for %d candidate edge(s)",
                origin_id,
                len(candidates),
            )
            continue

        connector = create_connector(
            origin_config.get("type", ""), origin_config.get("connection", {})
        )
        try:
            for candidate in candidates:
                overlap = await _verify_inclusion(connector, candidate, sample_size)
                if overlap is None:
                    continue
                if overlap >= _VERIFIED_THRESHOLD:
                    confidence = "verified"
                elif overlap >= _PROBABLE_THRESHOLD:
                    confidence = "probable"
                else:
                    continue
                graph.add_edge(
                    JoinEdge(
                        child_origin=origin_id,
                        child_table=candidate.child_table,
                        child_column=candidate.child_column,
                        parent_origin=origin_id,
                        parent_table=candidate.parent_table,
                        parent_column=candidate.parent_column,
                        confidence=confidence,
                        overlap_ratio=round(overlap, 4),
                        rationale=(
                            f"{candidate.child_table}.{candidate.child_column} values found in "
                            f"{candidate.parent_table}.{candidate.parent_column} "
                            f"({overlap:.0%} of sampled distinct values)"
                        ),
                    )
                )
        finally:
            await connector.close()

    logger.info(
        "Join graph built: %d node(s), %d edge(s)",
        len(graph.nodes),
        len(graph),
    )
    return graph


# ── BFS path finder (Phase 4.2) ──────────────────────────────────────────
#
# `plan_scope.py`'s `_find_junction` (pre-Phase-4) is a flat scan returning
# exactly one junction table — it can express a 2-hop chain and nothing
# longer, which is why the 4th live run's `Company -> Experience -> Person
# -> PersonContact` (3 hops) was structurally unreachable rather than an LLM
# mistake. `find_paths` below replaces that with real graph search.
#
# Modelled on Jailer's "subject + associations" traversal: shortest-first,
# cycle-guarded, ranked. Implementation is a two-pass shortest-path search
# rather than naive all-paths DFS, which is what keeps it bounded on a real
# schema instead of exploding combinatorially on diamond-shaped join graphs:
#   Pass 1 — plain BFS from `subject_node` records each node's shortest hop
#            distance (`dist`), visited-guarded so cycles simply terminate
#            the moment a node is revisited, never explored twice.
#   Pass 2 — backtrack forward from `subject_node`, only stepping onto an
#            edge whose far side sits exactly one hop further out
#            (`dist[other] == dist[node] + 1`). That constraint makes this a
#            walk over a DAG by construction — no separate cycle check is
#            needed here, `dist` strictly increases along any followed edge.
#            This still finds every *shortest* path, including ties, which
#            is what "ranked paths" (plural) means: same hop count, ranked
#            against each other by confidence and fan-out next.


def _hop_from(edge: JoinEdge, node: Node) -> list[tuple[str, Node]]:
    """The node(s) `edge` leads to when standing on `node`, with direction.

    `child -> parent` is "narrow" (many-to-one: exactly one parent row per
    child row). `parent -> child` is "fanout" (one-to-many: this is the
    direction that turns "everything about Airbus" into half the database
    once two or more fan-out hops compound — Airbus employees -> their
    other jobs -> those companies).
    """
    child_node = (edge.child_origin, edge.child_table)
    parent_node = (edge.parent_origin, edge.parent_table)
    hops: list[tuple[str, Node]] = []
    if node == child_node:
        hops.append(("narrow", parent_node))
    if node == parent_node:
        hops.append(("fanout", child_node))
    return hops


@dataclass(frozen=True)
class PathHop:
    """One step of a `JoinPath`: the edge used and which way it was crossed."""

    edge: JoinEdge
    direction: str  # "narrow" | "fanout"


@dataclass(frozen=True)
class JoinPath:
    """A verified route from a subject node to a target node.

    `nodes` always has `len(hops) + 1` entries — `nodes[0]` is the subject,
    `nodes[-1]` is the target. A zero-hop path (`subject_node == target_node`)
    has `nodes == (subject_node,)` and `hops == ()`.
    """

    nodes: tuple[Node, ...]
    hops: tuple[PathHop, ...]

    @property
    def hop_count(self) -> int:
        return len(self.hops)

    @property
    def fanout_hop_count(self) -> int:
        return sum(1 for h in self.hops if h.direction == "fanout")

    @property
    def high_fanout(self) -> bool:
        """True once >= 2 fan-out hops compound — these default UNCHECKED
        at the scope gate (Phase 5.1), since two one-to-many hops in a row
        is exactly what silently balloons "everything about Airbus" into
        unrelated companies."""
        return self.fanout_hop_count >= _HIGH_FANOUT_THRESHOLD

    @property
    def min_overlap_ratio(self) -> float | None:
        """The weakest link in the chain — a path is only as trustworthy as
        its least-verified hop. `None` only when every hop is a `declared`
        FK (no sampled ratio at all, but no less trustworthy for it)."""
        ratios = [h.edge.overlap_ratio for h in self.hops if h.edge.overlap_ratio is not None]
        return min(ratios) if ratios else None

    def rank_key(self) -> tuple[int, float, int]:
        """Sort ascending on this: fewer hops first, then higher confidence
        (inverted so ascending sort == descending confidence), then fewer
        fan-out hops. Matches the plan's "hop count, then minimum
        overlap_ratio, then fan-out estimate" ranking exactly."""
        overlap = self.min_overlap_ratio
        return (self.hop_count, -(overlap if overlap is not None else 1.0), self.fanout_hop_count)


def _bfs_distances(graph: JoinGraph, subject_node: Node, max_hops: int) -> dict[Node, int]:
    """Plain shortest-hop-count BFS from `subject_node`. Visited-guarded, so
    a cycle in the underlying graph just stops expanding once every member
    of the cycle has been reached — it can never loop."""
    dist: dict[Node, int] = {subject_node: 0}
    queue: deque[Node] = deque([subject_node])
    while queue:
        node = queue.popleft()
        depth = dist[node]
        if depth >= max_hops:
            continue
        for edge in graph.neighbors(node):
            for _direction, other in _hop_from(edge, node):
                if other not in dist:
                    dist[other] = depth + 1
                    queue.append(other)
    return dist


def _enumerate_shortest_paths(
    graph: JoinGraph,
    subject_node: Node,
    target_node: Node,
    dist: dict[Node, int],
) -> list[JoinPath]:
    """Every distinct shortest path from `subject_node` to `target_node`,
    found by only following edges that step exactly one hop further out
    per `dist` (pass 1's output) — a DAG walk, so no cycle is possible
    regardless of what the underlying graph looks like."""
    if target_node not in dist:
        return []

    results: list[JoinPath] = []
    nodes_so_far: list[Node] = [subject_node]
    hops_so_far: list[PathHop] = []

    def backtrack(node: Node) -> None:
        if node == target_node:
            results.append(JoinPath(nodes=tuple(nodes_so_far), hops=tuple(hops_so_far)))
            return
        for edge in graph.neighbors(node):
            for direction, other in _hop_from(edge, node):
                if dist.get(other) != dist[node] + 1:
                    continue
                hops_so_far.append(PathHop(edge=edge, direction=direction))
                nodes_so_far.append(other)
                backtrack(other)
                nodes_so_far.pop()
                hops_so_far.pop()

    backtrack(subject_node)
    return results


def find_paths(
    graph: JoinGraph,
    subject_node: Node,
    target_node: Node,
    *,
    max_hops: int = _DEFAULT_MAX_HOPS,
    max_paths: int = 20,
) -> list[JoinPath]:
    """Every shortest verified route from `subject_node` to `target_node`,
    ranked best-first (hop count, then confidence, then fan-out).

    Returns `[]` when either node is absent from the graph or no route
    exists within `max_hops` — never raises, since "unreachable" is a
    legitimate outcome the scope gate (Phase 5) must be able to show as a
    `not_found` row rather than a crash.
    """
    if subject_node not in graph.nodes or target_node not in graph.nodes:
        return []
    if subject_node == target_node:
        return [JoinPath(nodes=(subject_node,), hops=())]

    dist = _bfs_distances(graph, subject_node, max_hops)
    paths = _enumerate_shortest_paths(graph, subject_node, target_node, dist)
    paths.sort(key=lambda p: p.rank_key())
    return paths[:max_paths]
