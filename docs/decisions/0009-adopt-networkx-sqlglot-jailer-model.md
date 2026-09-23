# ADR 0009: Adopt networkx + sqlglot + the Jailer subject/association model

**Status:** accepted; the Jailer-derived model was already adopted in V2 (Part A) and this ADR
records the decision retroactively alongside the two libraries P-1/P-8/P-9 add to it.

## Context

P-8 (metadata graph + graph UI) and P-9 (entity anchors, subgraph traversal, extraction) both
need in-process graph traversal over a graph sized at thousands of nodes (tables, columns,
relationships), not millions — and P-10's canonical matching dataset needs column-level lineage
from the field-mapping decisions already recorded in `field_mappings`.

## Decision

- **networkx** (BSD, in-process) for graph traversal over the P-1 metadata tables. No graph
  database — see ADR 0012 for why Neo4j was rejected for this size of graph. Storage stays
  PostgreSQL (the same tables P-1 defines); networkx builds an in-memory graph from those rows
  when a traversal is needed, it does not own persistence.
- **sqlglot** (already an installed dependency) for SQL parsing and column-level lineage —
  driven from this project's own `field_mappings`, not re-parsed from scratch, per P-10's
  lineage requirement (every canonical record answers "where did this come from?" by SQL alone,
  the BP-B3 success criterion).
- **Jailer's subject + association + restriction model** for scoped extraction. Already adopted
  in V2's `plan_scope`/`extract_data`/`join_graph.py` (the "everything about X" scoped-merge
  feature, live-verified against real Boeing/Airbus/Lockheed data). P-9 extends this model with
  entity anchors and traversal-policy caps (`max_depth`, `max_nodes`, `max_edges`,
  `max_records`, `min_edge_confidence`); it does not replace the underlying model.

## Consequences

- P-8 must maintain a strict separation between the **metadata graph** (tables, columns,
  relationships — structural) and the **entity graph** (specific real-world entities like
  "Airbus" or "John Smith" — instance data). The two are never the same networkx graph object,
  and the UI treats them as separate views.
- Because storage stays PostgreSQL rather than a dedicated graph database, every traversal P-8
  or P-9 needs must be expressible as "load the relevant rows, build a networkx graph, traverse
  in memory" — a traversal pattern that needs true graph-database features (variable-length
  path queries at scale, for example) would be a signal to revisit this ADR, not a reason to
  bolt one onto PostgreSQL informally.
