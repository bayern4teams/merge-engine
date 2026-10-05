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

Edges come from two tiers, descending confidence:

1. Declared foreign keys — real and authoritative when present, but ALWAYS
   EMPTY for ClickHouse and MongoDB origins (see clickhouse.py:248 /
   mongodb.py:172), which is what both real origins in the 4th run are.
2. Value-based inclusion-dependency (IND) discovery (P3) — the tier that
   actually works on these origins, and it reads NO names. Every column's
   distinct values are sampled; a pair (child column, parent column) is a
   candidate when the parent column is unique in its table and the two are
   not both their own table's primary key (PK subset of PK); each candidate
   is scored by containment |child ∩ parent| / |child| (the primitive from
   B4-matching-precision's identity_profile.py, pushed into SQL through each
   connector's `sample_distinct_values` rather than pulling tables into
   Python); and the ranked list is cut at its largest score gap, the "cliff"
   -- no threshold. The earlier name-pattern tier (`<table>id` against the
   surrogate key) is gone: it proposed `child.<parent>id -> parent._id`, which
   the data rightly rejects, and never proposed a child column whose name does not match.

With no connection to sample (scope planning before a live connection was
ever opened, or an unreachable origin) tier 2 proposes nothing: a guess from
names is exactly what it replaced.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from src.mcp_server.connectors import create_connector

logger = logging.getLogger("merge-engine.tools.join_graph")

_DEFAULT_SAMPLE_SIZE = 500
_DEFAULT_MAX_HOPS = 6
_HIGH_FANOUT_THRESHOLD = 2

# Weakest-to-strongest, used to rank a JoinPath by its least-trustworthy hop.
# "declared" and "heuristic" both carry overlap_ratio=None (nothing was
# sampled either way) but are NOT equally trustworthy -- one is a real FK
# constraint, the other an unverified name guess -- so ranking must go
# through this, never raw overlap_ratio alone.
_CONFIDENCE_RANK = {"heuristic": 0, "probable": 1, "verified": 2, "declared": 3}

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
    confidence: str  # "declared" | "verified" | "probable" | "heuristic"
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

    # dict[Node, None], not set[Node]: this graph is stored in orchestrator
    # state and must survive a LangGraph checkpoint round-trip across a HITL
    # gate. LangGraph's msgpack checkpoint serializer decodes a bare set's
    # elements as lists (msgpack has no tuple type outside dict keys), so
    # `set(tuple_of_node_tuples)` throws on resume ("unhashable type: list")
    # and the ext hook silently swallows it to None -- plan_scope.py then
    # crashes on `node not in graph.nodes` with a NoneType. Dict keys decode
    # correctly (proven: `_adjacency` below round-trips fine), so a dict
    # used as an ordered set sidesteps the bug entirely.
    nodes: dict[Node, None] = field(default_factory=dict)
    _adjacency: dict[Node, list[JoinEdge]] = field(default_factory=dict)

    def add_node(self, node: Node) -> None:
        self.nodes[node] = None

    def add_edge(self, edge: JoinEdge) -> None:
        child_node = (edge.child_origin, edge.child_table)
        parent_node = (edge.parent_origin, edge.parent_table)
        self.nodes[child_node] = None
        self.nodes[parent_node] = None
        self._adjacency.setdefault(child_node, []).append(edge)
        self._adjacency.setdefault(parent_node, []).append(edge)

    def neighbors(self, node: Node) -> list[JoinEdge]:
        """Every edge touching `node`, in either direction."""
        return list(self._adjacency.get(node, []))

    def find_edge(
        self, origin_id: str, table_a: str, column_a: str, table_b: str, column_b: str
    ) -> JoinEdge | None:
        """The edge directly connecting (table_a, column_a) and (table_b,
        column_b), regardless of which side was recorded as child/parent.

        For a caller that only knows two (table, column) pairs -- not which
        one looks like the foreign key -- this is what lets it confirm a
        claimed join is real without having to reason about direction
        itself (plan_scope.py's Phase 4.3 hop validation is exactly this:
        it must reject an LLM-proposed join that isn't backed by a real
        edge, and it should not have to guess which side is which).
        """
        a_t, a_c = table_a.lower(), column_a.lower()
        b_t, b_c = table_b.lower(), column_b.lower()
        for edge in self.neighbors((origin_id, table_a)):
            ct, cc = edge.child_table.lower(), edge.child_column.lower()
            pt, pc = edge.parent_table.lower(), edge.parent_column.lower()
            if (ct == a_t and cc == a_c and pt == b_t and pc == b_c) or (
                ct == b_t and cc == b_c and pt == a_t and pc == a_c
            ):
                return edge
        return None

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
    # No declared PK and no Mongo-style _id: a bulk-loaded/denormalized
    # origin table often has no PK constraint at all despite having an
    # obvious natural key -- e.g. "companyid" on "company", following
    # exactly the {table}id/{table}_id convention this module already uses
    # to match a CHILD column to ITS parent (see _find_candidate_edges).
    # Without this fallback, a PK-less origin (live: testdiffbot's
    # `diffbot` schema has zero declared PKs across all 13 tables) can
    # never be a valid candidate-edge PARENT at all -- _find_candidate_
    # edges' `if not other_pk: continue` guard skips every table, the
    # whole heuristic tier finds nothing, and every multi-hop entity
    # behind it (financials, industry, investors, relations, ...) gets
    # silently zero rows with no join-graph edge to explain why.
    table_name = str(table.get("name") or "").lower()
    if table_name:
        candidate_names = {table_name, _singularise_table(table_name)}
        lower_to_actual = {
            str(c.get("name", "")).lower(): c.get("name") for c in table.get("columns", []) or []
        }
        for name in candidate_names:
            for suffix in ("id", "_id"):
                actual = lower_to_actual.get(f"{name}{suffix}")
                if actual:
                    return [actual]
    return []


@dataclass(frozen=True)
class _EdgeCandidate:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    # Schema-qualified table names ("diffbot.company"), for verification
    # queries only -- the JoinEdge this candidate becomes still keys on the
    # bare name everywhere else in the pipeline. Falls back to the bare
    # name when a table dict carries no "full_name" (e.g. hand-built dicts
    # in tests). Without this, sample_distinct_values (called with only the
    # bare table name) can't tell which schema to query and silently
    # defaults to "public" -- live-reproduced: every one of testdiffbot's
    # tables actually lives in the "diffbot" schema, so every single
    # inclusion-dependency check failed with "relation public.<table> does
    # not exist", caught by _verify_inclusion's own try/except, and
    # returned None -- zero edges ever verified despite _find_candidate_
    # edges correctly proposing 10 of them.
    child_full_table: str = ""
    parent_full_table: str = ""

    def __post_init__(self) -> None:
        if not self.child_full_table:
            object.__setattr__(self, "child_full_table", self.child_table)
        if not self.parent_full_table:
            object.__setattr__(self, "parent_full_table", self.parent_table)


# A tiny, deliberately conservative singulariser, now used only by
# `_resolve_primary_key`'s PK-less fallback ("companies" -> "companyid"). A
# local copy of entity_grouping.py's _singularise, not an import from it --
# src/orchestrator/utils sits above src/mcp_server/tools in CLAUDE.md's
# layering rule, so importing it here would be a reverse import.
_IRREGULAR_PLURALS = {"people": "person", "categories": "category"}


def _singularise_table(name: str) -> str:
    if name in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[name]
    if name.endswith("ies") and len(name) > 3:
        return name[:-3] + "y"
    if name.endswith("es") and len(name) > 2 and name[-3] in "sxzh":
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss") and len(name) > 1:
        return name[:-1]
    return name


@dataclass(frozen=True)
class _ColumnProfile:
    """One column's sampled distinct values (see `_profile_columns`)."""

    raw: tuple[Any, ...]  # as returned by the connector, empty-after-normalise dropped
    norm: frozenset[str]  # normalize_val() of each
    # True when the sample hit `sample_size`: more distinct values may exist,
    # so this profile can be neither a complete value set nor a uniqueness proof.
    saturated: bool
    # Exact uniqueness, asked of the database for a saturated column only (a
    # complete sample already answers it); None = not established.
    unique: bool | None = None


async def _profile_columns(
    connector: Any, tables: list[dict[str, Any]], sample_size: int
) -> dict[tuple[str, str], _ColumnProfile]:
    """Sample every column's distinct values once: the data every later step reads.

    A column that fails to sample is simply absent from the result -- one bad
    column must not abort the graph. Sequential on purpose.
    """
    profiles: dict[tuple[str, str], _ColumnProfile] = {}
    for table in tables:
        table_name = table.get("name")
        if not table_name:
            continue
        full_name = str(table.get("full_name") or table_name)
        for col in table.get("columns", []) or []:
            col_name = col.get("name", "")
            if not col_name:
                continue
            try:
                vals = await connector.sample_distinct_values(
                    full_name, col_name, limit=sample_size
                )
            except Exception as exc:
                logger.debug("Column sampling failed for %s.%s: %s", table_name, col_name, exc)
                continue
            raw = tuple(v for v in vals if normalize_val(v))
            if not raw:
                continue
            saturated = len(vals) >= sample_size
            unique = None
            if saturated:
                # ponytail: a FULL scan per saturated column -- the price of an
                # exact RHS-uniqueness answer on a table bigger than the sample.
                # A connector without profile_column (MongoDB) leaves it None.
                try:
                    stats = await connector.profile_column(full_name, col_name, mode="FULL")
                    unique = stats["distinct_count"] == stats["total_count"] - stats["null_count"]
                except Exception as exc:
                    logger.debug(
                        "Uniqueness check unavailable for %s.%s: %s", table_name, col_name, exc
                    )
            profiles[(table_name, col_name)] = _ColumnProfile(
                raw=raw,
                norm=frozenset(normalize_val(v) for v in raw),
                saturated=saturated,
                unique=unique,
            )
    return profiles


def _find_candidate_edges(
    tables: list[dict[str, Any]],
    profiles: dict[tuple[str, str], _ColumnProfile],
) -> list[_EdgeCandidate]:
    """Every (child column, parent column) pair worth testing for inclusion.

    Value-based IND discovery, name-agnostic: nothing here reads a column or
    table NAME to decide anything -- only the sampled values in `profiles`
    and the table's resolved primary key. Two parameter-free filters
    (Hoppe/Papenbrock "HoPF"; see TECHNICAL_REPORT P3):

    * RHS-uniqueness: a parent column must be unique in its table. A column
      is unique when the database says so (saturated columns), else when its
      distinct count equals the table's largest distinct count (the table's
      row count, whenever some column is a key). Without this a repeated
      value column becomes a "parent" of everything inside it.
    * PK subset of PK: a column whose values equal its own table's primary
      key IS that key, and an edge between two keys is a coincidence of two
      surrogate sequences (1..60 inside 1..200), not a reference. This is what
      rejects the surrogate-key false edge, by the data.

    The acyclicity rule HoPF also uses is deliberately NOT applied: it would
    reject junction tables and self-references, which P4/P7 must find.

    ponytail: unary INDs only (no composite keys). Uniqueness is only
    decidable below the sampling cap or with a connector that can profile a
    column, and only if the table has a unique column at all; otherwise the
    filter passes vacuously and the containment test decides alone. A genuine FK that is itself equal to its own
    table's key (a shared-key 1:1 extension table) is dropped by the PK-subset
    filter; a declared FK still finds it. A nullable unique column in a
    small table has fewer distinct values than the key and is not treated as
    unique.
    """
    full_names = {t["name"]: str(t.get("full_name") or t["name"]) for t in tables if t.get("name")}
    table_size: dict[str, int] = {}
    for (t, _c), p in profiles.items():
        table_size[t] = max(table_size.get(t, 0), len(p.norm))

    key_values: dict[str, frozenset[str]] = {}
    for table in tables:
        name = table.get("name")
        pk = _resolve_primary_key(table)
        if name and len(pk) == 1 and (name, pk[0]) in profiles:
            key_values[name] = profiles[(name, pk[0])].norm

    def is_key(table: str, column: str) -> bool:
        return table in key_values and profiles[(table, column)].norm == key_values[table]

    candidates: list[_EdgeCandidate] = []
    for (parent_t, parent_c), parent in profiles.items():
        unique = (
            parent.unique if parent.unique is not None else len(parent.norm) == table_size[parent_t]
        )
        if not unique:
            continue  # RHS-uniqueness prefilter
        for (child_t, child_c), _child in profiles.items():
            if (child_t, child_c) == (parent_t, parent_c):
                continue
            if is_key(child_t, child_c) and is_key(parent_t, parent_c):
                continue  # PK subset of PK
            candidates.append(
                _EdgeCandidate(
                    child_table=child_t,
                    child_column=child_c,
                    parent_table=parent_t,
                    parent_column=parent_c,
                    child_full_table=full_names[child_t],
                    parent_full_table=full_names[parent_t],
                )
            )
    return candidates


async def _verify_inclusion(
    connector: Any,
    candidate: _EdgeCandidate,
    profiles: dict[tuple[str, str], _ColumnProfile],
    sample_size: int,
) -> float | None:
    """|child ∩ parent| / |child| over the child's sampled distinct values.

    When the parent's value set was sampled completely the answer is read from
    memory. When it was truncated (a big table), the parent is asked directly
    about the child's own sampled values (`filter_values`) rather than hoping
    two independent samples intersect: verified live 2026-08-10, independent
    sampling scored 0.069 for a 100% referentially intact pair (thousands of
    parents, 29 children).

    Returns None if the lookup failed -- never raises, since one bad candidate
    must not abort building the rest of the graph.

    ponytail: one filtered query per candidate pair against a saturated
    parent; the RHS-uniqueness filter bounds how many parents that is.
    """
    child = profiles[(candidate.child_table, candidate.child_column)]
    parent = profiles[(candidate.parent_table, candidate.parent_column)]
    if not parent.saturated:
        return len(child.norm & parent.norm) / len(child.norm)
    try:
        matches = await connector.sample_distinct_values(
            candidate.parent_full_table,
            candidate.parent_column,
            limit=sample_size,
            filter_values=list(child.raw),
        )
    except Exception as exc:
        logger.debug(
            "Inclusion-dependency parent lookup failed for %s.%s -> %s.%s: %s",
            candidate.child_table,
            candidate.child_column,
            candidate.parent_table,
            candidate.parent_column,
            exc,
        )
        return None
    return len(child.norm & {normalize_val(v) for v in matches}) / len(child.norm)


def _cut_at_cliff(scored: list[tuple[float, _EdgeCandidate]]) -> list[tuple[float, _EdgeCandidate]]:
    """Keep the candidates ranked above the largest gap between consecutive
    scores. No parameter: the ranking itself says where it breaks (HoPF: 7.67e7
    candidate key combinations to 1 on TPC-H, losing no true key). A list with
    no gap (one candidate, or all scores equal) is kept whole.

    ponytail: containment is the only score, so a coincidental IND with
    overlap 1.0 (a low-cardinality column inside a surrogate sequence) ties
    with the real ones and survives, and when an origin has no real FK the best
    junk candidate is kept; a genuine FK with dirty data (overlap just below
    the others) is cut whenever the gap above it is the largest. Ties for the
    largest gap keep the higher group. The
    deferred remedy is a classifier on non-name features (coverage ratio,
    value distribution) trained on confirmed FKs after Part 3.
    """
    ranked = sorted(scored, key=lambda s: -s[0])
    gaps = [a[0] - b[0] for a, b in pairwise(ranked)]
    return ranked[: gaps.index(max(gaps)) + 1] if gaps and max(gaps) else ranked


def _index_origins(origins: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(o.get("id")): o for o in origins or []}


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
    origins_by_id = _index_origins(origins)

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

        # Tier 2: value-based IND discovery over the sampled data.
        origin_config = origins_by_id.get(origin_id)
        if origin_config is None:
            # ponytail: a name guess is the one thing this tier must not do, so
            # with nothing to sample there is nothing to propose. plan_scope's
            # LLM-proposed hops still reach the graph through its own path.
            logger.warning(
                "No origin config for '%s' -- inclusion-dependency discovery has no "
                "connection to sample, so no value-based edges were inferred",
                origin_id,
            )
            continue

        connector = create_connector(
            origin_config.get("type", ""), origin_config.get("connection", {})
        )
        try:
            profiles = await _profile_columns(connector, tables, sample_size)
            scored: list[tuple[float, _EdgeCandidate]] = []
            for candidate in _find_candidate_edges(tables, profiles):
                overlap = await _verify_inclusion(connector, candidate, profiles, sample_size)
                if overlap:  # None (lookup failed) and 0.0 (no containment) both drop out
                    scored.append((overlap, candidate))
        finally:
            await connector.close()

        for overlap, candidate in _cut_at_cliff(scored):
            graph.add_edge(
                JoinEdge(
                    child_origin=origin_id,
                    child_table=candidate.child_table,
                    child_column=candidate.child_column,
                    parent_origin=origin_id,
                    parent_table=candidate.parent_table,
                    parent_column=candidate.parent_column,
                    # Full containment is what an inclusion dependency IS;
                    # anything less that survived the cliff is "probable".
                    confidence="verified" if overlap == 1 else "probable",
                    overlap_ratio=round(overlap, 4),
                    rationale=(
                        f"{candidate.child_table}.{candidate.child_column} values found in "
                        f"{candidate.parent_table}.{candidate.parent_column} "
                        f"({overlap:.0%} of sampled distinct values)"
                    ),
                )
            )

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
    """One step of a `JoinPath`: the edge used and which way it was crossed.

    `from_*`/`to_*` express the hop the way an extraction filter actually
    needs it regardless of direction: "select `to_table` rows whose
    `to_column` value is in the set of known `from_table.from_column`
    values" — the same fixpoint shape whether the hop narrows (child to
    parent) or fans out (parent to child). Consumers (plan_scope.py,
    extract_data.py) never need to branch on `direction` themselves.
    """

    edge: JoinEdge
    direction: str  # "narrow" | "fanout"

    @property
    def from_table(self) -> str:
        return self.edge.parent_table if self.direction == "fanout" else self.edge.child_table

    @property
    def from_column(self) -> str:
        return self.edge.parent_column if self.direction == "fanout" else self.edge.child_column

    @property
    def to_table(self) -> str:
        return self.edge.child_table if self.direction == "fanout" else self.edge.parent_table

    @property
    def to_column(self) -> str:
        return self.edge.child_column if self.direction == "fanout" else self.edge.parent_column


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

    @property
    def confidence(self) -> str:
        """The path's overall confidence — the WEAKEST confidence among its
        hops via `_CONFIDENCE_RANK`, since a chain is only as trustworthy as
        its least-verified link. A zero-hop (self) path is trivially
        "declared": there is nothing to verify."""
        if not self.hops:
            return "declared"
        weakest = min(self.hops, key=lambda h: _CONFIDENCE_RANK.get(h.edge.confidence, 0))
        return weakest.edge.confidence

    def rank_key(self) -> tuple[int, int, float, int]:
        """Sort ascending on this: fewer hops first, then higher confidence
        (declared/verified beat probable/heuristic — `declared` and
        `heuristic` both carry `overlap_ratio=None`, so confidence must be
        compared before overlap_ratio, not folded into it), then higher
        minimum overlap ratio among hops that have one, then fewer fan-out
        hops. Matches the plan's "hop count, then minimum overlap_ratio,
        then fan-out estimate" ranking, extended with the confidence tier
        the plan's spec predates (Phase 4.3's heuristic-tier addition)."""
        overlap = self.min_overlap_ratio
        return (
            self.hop_count,
            -_CONFIDENCE_RANK.get(self.confidence, 0),
            -(overlap if overlap is not None else 1.0),
            self.fanout_hop_count,
        )


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
