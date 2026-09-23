# ADR 0010: Reject OpenMetadata

**Status:** rejected.

## Context

P-1's domain model needs a place to store discovered metadata (tables, columns, profiles,
candidate keys, relationships, canonical field mappings) with confidence/evidence/provenance on
every row. OpenMetadata is an established open-source metadata platform that does something
adjacent to this.

## Finding

A Docker deployment of OpenMetadata needs, at minimum: the OpenMetadata server itself, a
MySQL/PostgreSQL metadata database, **Elasticsearch or OpenSearch**, and an Airflow container
if UI-managed ingestion is wanted. The `openmetadata-ingestion` Python package is a *client* —
it discovers metadata and pushes it to a running server; it is not a library this project could
import and call for its profiling/discovery algorithms standalone.

So the cost of adopting OpenMetadata is four new services and a sync contract to keep this
project's own state and OpenMetadata's in agreement. The benefit is a catalogue UI this project
would still have to wrap in its own frontend, because OpenMetadata's domain model has no
equivalent for this project's specific concepts: entity anchors, join paths, per-record context
scores, canonical matching datasets. OpenMetadata solves cataloguing well; cataloguing is not
this project's bottleneck — discovery, matching, and scoped extraction are.

## Decision

Do not adopt OpenMetadata, as a server or as a dependency. Build the metadata store in-process
(PostgreSQL tables, defined in P-1) instead.

## Consequences

- If a future need for a real metadata *catalog* (as opposed to this project's own
  discovery-and-matching metadata) emerges, this ADR's finding — the model gap, not just the
  infrastructure cost — should be re-read before revisiting.
