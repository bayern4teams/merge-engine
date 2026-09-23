# ADR 0011: Reject DataHub as infrastructure; adopt its metadata model

**Status:** rejected as infrastructure, model partially adopted (see Decision).

## Context

Same need as ADR 0010 (OpenMetadata): a place to store discovered metadata with
confidence/provenance. DataHub is the other major open-source metadata platform, evaluated
alongside OpenMetadata.

## Finding

Same shape as OpenMetadata, worse infrastructure footprint. DataHub's quickstart brings GMS
(the metadata service), the React frontend, Elasticsearch, MySQL, Kafka + ZooKeeper + Schema
Registry — documented minimum 8 GB RAM / 2 cores for dev, 16 GB+ for production. `acryl-datahub`'s
REST and Kafka emitters both require that server (or a Kafka broker) to be reachable; there is
no standalone library path.

DataHub does have the strongest metadata **model** of the two platforms evaluated: source ->
dataset -> column entities, edges carrying `confidence`/`origin`/`review_status`, column-level
lineage. That model maps closely onto what P-1's domain model needs.

## Decision

Reject DataHub as infrastructure — no server, no Elasticsearch, no Kafka. **Adopt its metadata
model**, implemented as this project's own PostgreSQL tables (P-1: `md_source`, `md_database`,
`md_schema`, `md_table`, `md_column`, plus the profiling/key/relationship/mapping/edge tables
layered on top), not as a DataHub client against a DataHub server.

Storing the model in this project's own tables, behind a project-owned metadata-graph module
rather than a vendored client, means nothing in this project couples to one specific graph
implementation (the same reasoning ADR 0009 applies to choosing networkx over a graph
database) — either platform could be adopted later, if ever warranted, by writing an emitter
that reads these tables and pushes to a running DataHub/OpenMetadata instance.

## Consequences

- P-1's schema is judged against DataHub's model for completeness (source/dataset/column
  entities, confidence/origin/review_status per edge, column-level lineage) even though no
  DataHub code or infrastructure is present.
- If DataHub or OpenMetadata infrastructure is ever adopted later, the migration path is
  "write an emitter from these tables," not "re-derive the metadata model from scratch."
