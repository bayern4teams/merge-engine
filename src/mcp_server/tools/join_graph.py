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
4. Heuristic (unverified) — when an origin has no connector config
   available to sample with (e.g. scope planning runs before a live
   connection was ever opened for that origin, or the origin is briefly
   unreachable), a name-heuristic candidate is kept anyway rather than
   silently dropped, tagged `confidence="heuristic"`. Losing the join
   entirely because a connector happened to be unavailable would be worse
   than surfacing an unverified hypothesis a human or a downstream check
   can still see and discount — this is the weakest tier and always ranks
   below `probable` when a `JoinPath` picks the strongest available route.
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


# A tiny, deliberately conservative singulariser used only to widen FK-
# column-name pattern matching: "companies" -> "company_id" is a common
# convention this repo's own schemas use (e.g. Pagila), and the raw table
# name alone ("companiesid"/"companies_id") would never match it. This is a
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
        self_names = {table_name.lower(), _singularise_table(table_name.lower())}
        for col in table.get("columns", []) or []:
            col_name = col.get("name", "")
            if not col_name:
                continue
            col_low = col_name.lower()
            # Skip this table's own primary key -- but only when that
            # column, alone, IS the primary key. A composite primary key
            # member is not excluded: (experienceID, companyID) as a
            # composite PK on a fact/junction-shaped table is a real,
            # common pattern, and companyID there is still a real FK to
            # Company -- found live: a ClickHouse Experience table with
            # exactly this composite PK meant every candidate on
            # companyID was silently skipped, so the whole structural
            # tier found nothing and the entire chain fell through to
            # the much weaker single-hop concept fallback.
            if col_low == "id" or own_pk == {col_low}:
                continue
            if any(col_low in (f"{n}id", f"{n}_id") for n in self_names):
                continue
            for other_name, other_pk in pk_by_table.items():
                if other_name == table_name or not other_pk:
                    continue
                other_names = {other_name.lower(), _singularise_table(other_name.lower())}
                if any(
                    col_low in (f"{n}id", f"{n}_id", f"{n}key", f"{n}_key") for n in other_names
                ):
                    candidates.append(
                        _EdgeCandidate(
                            child_table=table_name,
                            child_column=col_name,
                            parent_table=other_name,
                            # The other table's ACTUAL primary key -- never
                            # a hard-coded "id".
                            parent_column=other_pk[0],
                            child_full_table=str(table.get("full_name") or table_name),
                            parent_full_table=str(
                                table_by_name.get(other_name, {}).get("full_name") or other_name
                            ),
                        )
                    )
    return candidates


async def _verify_inclusion(
    connector: Any,
    candidate: _EdgeCandidate,
    sample_size: int,
) -> float | None:
    """Sample the child, then check those SPECIFIC values against the
    parent, and compute |child ∩ parent| / |child|.

    Deliberately NOT two independent samples. A parent table with (say)
    50,000 rows and a child table with 30 rows: an independent `LIMIT 500`
    sample of the parent has essentially no chance of coincidentally
    containing the exact 30 rows the child references, even when every
    single child row genuinely points at a real parent row — verified
    live 2026-08-10 against a real ClickHouse Company/CompanyFinancials
    pair (thousands of companies, 29 financial records): independent
    sampling scored 0.069 overlap for a relationship that is, by
    construction, 100% referentially intact. Querying the parent FILTERED
    to the child's own sampled values (`sample_distinct_values`'s
    `filter_values` parameter) answers the real question directly instead
    of hoping two arbitrary samples happen to intersect.

    Returns None if either side failed to sample or the child column has no
    usable values to test — never raises, since one bad candidate must not
    abort building the rest of the graph.
    """
    try:
        child_vals = await connector.sample_distinct_values(
            candidate.child_full_table, candidate.child_column, limit=sample_size
        )
    except Exception as exc:
        logger.debug(
            "Inclusion-dependency child sampling failed for %s.%s: %s",
            candidate.child_table,
            candidate.child_column,
            exc,
        )
        return None

    child_norm = {normalize_val(v) for v in child_vals if normalize_val(v)}
    if not child_norm:
        return None
    raw_child_vals = [v for v in child_vals if normalize_val(v)]

    try:
        parent_matches = await connector.sample_distinct_values(
            candidate.parent_full_table,
            candidate.parent_column,
            limit=sample_size,
            filter_values=raw_child_vals,
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

    parent_norm = {normalize_val(v) for v in parent_matches}
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
                "No origin config for '%s' -- %d candidate edge(s) added "
                "unverified (confidence=heuristic) since inclusion-dependency "
                "sampling has no connection to run against",
                origin_id,
                len(candidates),
            )
            for candidate in candidates:
                graph.add_edge(
                    JoinEdge(
                        child_origin=origin_id,
                        child_table=candidate.child_table,
                        child_column=candidate.child_column,
                        parent_origin=origin_id,
                        parent_table=candidate.parent_table,
                        parent_column=candidate.parent_column,
                        confidence="heuristic",
                        overlap_ratio=None,
                        rationale=(
                            f"{candidate.child_table}.{candidate.child_column} name-matches "
                            f"{candidate.parent_table}.{candidate.parent_column} "
                            "(unverified: no connection available to sample)"
                        ),
                    )
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
