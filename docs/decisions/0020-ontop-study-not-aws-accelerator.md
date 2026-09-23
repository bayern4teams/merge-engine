# ADR 0020: Ontop — study the OBDA idea, reject the AWS accelerator packaging

**Status:** the specific AWS accelerator rejected; Ontop itself a portable-idea reference, not
currently adopted.

## Context

Audit §13 #8 named an AWS-published accelerator bundling Ontop (an Ontology-Based Data Access /
OBDA engine — maps relational tables to a SPARQL/OWL view without materializing the data as
triples) together with Amazon Neptune (managed graph database) and Amazon Bedrock (managed LLM
service) as a reference architecture for schema-to-ontology mapping.

## Finding

The AWS accelerator's actual packaging couples Ontop to Neptune and Bedrock — both are managed,
cloud-only AWS services. This project's hard constraint (CLAUDE.md, this repo's standing rule
throughout `TODO.md`'s B.1–B.9) is local-only models and databases: local vLLM, local
Docker Postgres/ClickHouse, no cloud dependency for the core pipeline. Adopting the accelerator
as published would mean taking on exactly the two pieces of cloud coupling this project has
consistently avoided (ADR 0012 rejected Neo4j on similar operational grounds, for a different
reason — scale rather than cloud-coupling).

Ontop itself, separated from that packaging, is a portable idea: it is an open-source engine that
can run against any relational database (including this project's own PostgreSQL destination) and
does not require Neptune or Bedrock specifically. There is no concrete SPARQL/OWL requirement in
this project today — the destination's own PostgreSQL schema plus this project's `kg`
metadata graph already serve every query need identified so far (ADR 0012's reasoning applies
here too: no traversal or query pattern currently in scope needs a dedicated ontology layer).

## Decision

Reject the AWS accelerator as published (Neptune/Bedrock coupling violates the local-only
constraint). Do not adopt Ontop now — there is no concrete need for it yet. Keep it as a
reference: if a genuine SPARQL/OWL or formal-ontology requirement appears (e.g. an external
system this project must federate with speaks SPARQL), Ontop run standalone against PostgreSQL is
the portable option to evaluate first, well ahead of standing up a managed graph database.

## Consequences

- No Neptune, no Bedrock, no cloud dependency introduced by this decision.
- No Ontop dependency added today.
- The trigger to revisit: a concrete external SPARQL/OWL interoperability requirement, not a
  general enthusiasm for ontology tooling.
