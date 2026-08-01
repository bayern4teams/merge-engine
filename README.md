# merge-engine

Merge data from multiple heterogeneous databases (PostgreSQL, ClickHouse,
MongoDB) into one destination, with an LLM doing the schema understanding and
ambiguous judgment calls, and a human approving four checkpoints before
anything is written. Built to survive real third-party data: verified live
against Pagila (PostgreSQL), a 43-million-row ClickHouse cell-tower table, and
MongoDB's `sample_mflix`.

For a step-by-step setup (Docker, databases, running your first merge), see
**[User_Guide.md](User_Guide.md)**. For a full technical walkthrough of every
module and the reasoning behind each design choice, see
**[TECHNICAL_README.md](TECHNICAL_README.md)**.

## What it does

1. You point it at 1+ origin databases and exactly 1 destination.
2. You describe the merge in plain English ("merge customers from both DBs").
3. An LLM parses the request, proposes which tables map to which business
   entities, and proposes a field mapping to the destination schema.
4. merge-engine extracts, deduplicates ("is this row in DB A the same
   real-world entity as that row in DB B?"), and unifies the data.
5. You review and approve at four checkpoints — entity/table selection,
   match/cluster decisions, field mapping, and final approval — before any
   write happens. Rejecting at any gate writes nothing.
6. The result lands in your destination database with a reconciliation
   report (rows in / rows out / conflicts resolved / LLM cost).

## Architecture at a glance

```
React (Vite/TS) UI  ──HTTP──>  FastAPI (src/api)  ──>  WorkflowManager
                                                            │
                                                     LangGraph StateGraph
                                                    (src/orchestrator/graph.py)
                                                            │
        ┌───────────────────────────────────────────────────────────────────┐
        │ preflight → parse_request → discover_schemas → [HITL: entities]   │
        │   → extract_data → resolve_entities → [HITL: matches] → unify     │
        │   → map_fields → [HITL: mapping] → [HITL: final] → load_data      │
        └───────────────────────────────────────────────────────────────────┘
                     │                              │                  │
              DuckDB spool                    LLM client          Origin/dest
          (logs/spool/<run>.duckdb)      (semantic cache +        connectors
                                           cost ledger)      (postgres/clickhouse/mongo)
```

Every node is a plain async function operating on a typed `MergeState` dict;
LangGraph's SQLite checkpointer persists that state after every node, which is
what makes the human-review pauses (`interrupt_before`) survive an app
restart. Row **data** does not live in that checkpointed state — it lives in
a per-run DuckDB file (the "spool"), because a 43-million-row list inside a
checkpoint is not something SQLite should be asked to serialize on every step.

## Status

All planned phases (0 through O) are implemented, unit-tested, and verified
live against real databases — see the phase-by-phase evidence in
[TODO.md](TODO.md). In summary:

- **Correctness (Phase K):** reject actually stops the run at all 4 gates;
  match-gate decisions (keep/split/merge) are applied, not discarded;
  deterministic cluster IDs make re-running the same data idempotent instead
  of duplicating it; entity→table selection considers the whole schema, not
  a hardcoded synonym list.
- **Matching precision (Phase L):** Fellegi–Sunter probabilistic scoring with
  term-frequency adjustment (a match on a rare surname counts for more than a
  match on a common first name) replaces two magic similarity constants;
  layered blocking (deterministic + phonetic + sorted-neighborhood) replaced
  a hard comparison cap that used to silently drop candidate pairs and still
  report success; clustering adds conflict-repair and a cohesion cut so one
  weak chain can't merge an entire connected component. Measured: pairwise
  precision 1.0000, recall 1.0000 on the eval harness.
- **Resilience (Phase M):** a typed error taxonomy
  (`TransientError`/`PermanentError`/`DataQualityError`/`UserInputError`)
  drives per-node retry policy and routes genuinely unrecoverable errors to a
  real human escalation gate; preflight checks reachability and permissions
  in seconds, before extraction starts; loads are transactional — a mid-load
  failure leaves the destination and watermarks untouched.
- **Scale (Phase N):** keyset (seek) pagination and a DuckDB staging spool
  replace OFFSET pagination and in-checkpoint row lists. Proven against a
  real 43,276,150-row ClickHouse table: 44.6 minutes, 16,184 rows/s, 380.8 MB
  peak memory, verified row-for-row (not just by the script's own report —
  independently against the source count, spool sequence contiguity, and
  distinct-payload count).
- **Customisation (Phase O):** saved run profiles pin the LLM's non-deterministic
  metadata decisions (a re-run of identical data can otherwise land on 15
  destination columns one time and 13 the next); per-entity matching rules
  (blocking keys, unique identifiers, strictness); configurable per-field
  conflict-resolution policy; an LLM semantic cache and per-run token ledger.
- **Targeted merges (Phase P):** "Merge all information about Nova" now extracts
  *only* that company — the LLM plans how each entity links to the subject
  (direct name match, foreign-key link, or a two-hop link through a junction
  table like `employment`, so "who works at Nova" is followed automatically), a
  new scope-confirmation gate shows which company was matched, and existing
  destination rows are deduplicated so a re-run updates instead of duplicating.
  Live-proven: a scoped Nova merge took `main_dest` from 732 to 742 companies —
  exactly the 10 Nova rows, zero non-Nova pollution. Also fixes a watermark bug
  where deleting rows from the destination and re-running extracted nothing.

Read [TODO.md](TODO.md) for the evidence behind every one of those claims —
it is the append-only working log this project was built from, phase by
phase, with real numbers rather than descriptions.

## Quick start

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e .
copy .env.example .env      # fill in LLM_API_KEY
python -m src.main --mode both --port 7860
```

Then open `http://localhost:7860`. Full setup, including standing up your
own origin/destination databases in Docker, is in
**[User_Guide.md](User_Guide.md)**.

## Project layout

```
src/
  api/            FastAPI app — HTTP surface over WorkflowManager (src/api/main.py)
  orchestrator/    LangGraph graph, nodes, MergeState, spool, profiles
    nodes/         one file per graph node (preflight, parse_request, ... load_data)
    utils/         retry taxonomy, dependency sort, entity aliases, cluster edits
    graph.py       StateGraph wiring: nodes, edges, HITL interrupts, RetryPolicy
    spool.py       per-run DuckDB row storage (RunSpool)
    profiles.py    saved run profiles (pin non-deterministic LLM decisions)
    workflow_manager.py   thread/run lifecycle, resume, profile capture
  mcp_server/
    connectors/    postgres, clickhouse, mongodb, databricks, + keyset/watermark helpers
    tools/         the actual logic each node calls (discover_schema, extract_data,
                   resolve_entities, fellegi_sunter, unify_entity_group, map_fields,
                   load_data, select_entity_tables)
  llm/            LLM client (LangChain), prompts/, semantic cache + cost ledger
  ui/frontend/    React (Vite + TypeScript) UI, built to dist/ and served by FastAPI
tests/
  unit/           pytest, one file per subsystem (908 tests as of the last full run)
  eval/           labelled-pair precision/recall harness for the matcher
  prompts/        golden-file tests for LLM prompt templates
config/
  config.yaml     pipeline-level settings (LLM, retry, batch sizes) — no credentials
  profiles/       saved run profiles (gitignored — local to your databases)
docker/
  langfuse-compose.yml   self-hosted observability stack
```

## Observability

Tracing is a self-hosted [Langfuse](https://langfuse.com) stack
(`docker/langfuse-compose.yml`) — every LangGraph node becomes a `CHAIN` span
and every LLM call becomes a `GENERATION` span, keyed by the run's thread ID.
Start it with:

```bash
docker compose -f docker/langfuse-compose.yml --env-file docker/langfuse.env up -d
```

Then set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` in
`.env` (the stack auto-creates these on first boot).

## Testing

```bash
pytest
```

908 tests passing as of the last full run (0 failed, 83 skipped — the skips
are tests that require a live database container). Run
`tests/eval/` separately for matcher precision/recall against labelled data.

## Known, documented limitations

- ClickHouse destination loads are **not** transactional (only PostgreSQL
  gets full stage-then-swap atomic loads) — the app says so rather than
  pretending otherwise.
- MongoDB is origin-only; there is no MongoDB destination connector.
- Databricks has a connector but has not been exercised in this project's own
  live verification runs the way PostgreSQL/ClickHouse/MongoDB have.
