# ADR 0012: Reject Neo4j

**Status:** rejected.

## Context

P-8's metadata graph and P-9's entity subgraph traversal both need graph traversal. Neo4j is
the default answer many teams reach for when "traversal" appears in a spec.

## Finding

The graphs in question are sized at thousands of nodes (tables, columns, relationships across
this project's origins and destination) — not the millions- or billions-of-nodes scale that
justifies running and operating a dedicated graph database server. Every traversal need
identified in the plan (metadata-graph navigation, entity-anchor subgraph expansion with
depth/node/edge caps) is expressible as "load the relevant rows from PostgreSQL, build an
in-memory graph with networkx, traverse" — see ADR 0009.

## Decision

Do not adopt Neo4j. PostgreSQL (storage, already the destination database in this project) +
networkx (in-process traversal, already adopted per ADR 0009) covers every traversal this
project's discovery/entity-resolution engine needs.

## Consequences

- If a future graph genuinely reaches a size or a traversal pattern (deep variable-length path
  queries at scale, for example) where in-memory networkx traversal becomes the bottleneck, that
  is the trigger to revisit this ADR — not a general preference for graph databases in the
  abstract.
