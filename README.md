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
**[docs/architecture/overview.md](docs/architecture/overview.md)** (start of
the split `TECHNICAL_README.md`, indexed at
**[docs/INDEX.md](docs/INDEX.md)**).

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

- **Harness foundation (Phase 0, on `main`):** a manual inspection of a live
  destination found the matcher was comparing **zero** candidate pairs at
  real corpus size while reporting zero uncompared, and the loader had grown
  the `company` table from 14 columns to 43 by inventing new columns for
  unmappable source fields. Both fixed at the architecture level — field
  mapping is `map`/`skip` only (no `new_column`, enforced in code, not just
  the prompt), and blocking/scoring compare a canonical cross-origin
  projection instead of raw intersected field names. Also: one verification
  command (`python scripts/check.py all`), an architecture linter, a
  mypy/arch-lint ratchet, and `CLAUDE.md`/`docs/INDEX.md` as the
  repository's own map. See [TODO.md](TODO.md)'s "Phase 0" section and
  [docs/verification/BASELINE_V1.md](docs/verification/BASELINE_V1.md).
- **Speed + observability (Part 1.5, on `main`):** a full Boeing run went
  from 50.7 minutes to **23.4 minutes** — `resolve_entities` 2.44x faster
  (scoring-path waste removal + true multiprocessing for its CPU-bound
  core), `map_fields` 5.8x faster (parallel LLM calls across entity types),
  plus a mid-run resolve cache keyed by content hash. Langfuse tracing was
  rebuilt so one merge run is one trace with every node and LLM call nested
  correctly (was fragmented into disconnected spans) — automatically
  verified by `scripts/diagnostics/verify_langfuse_trace.py` against the
  real Langfuse API. See [TODO.md](TODO.md)'s "Part 1.5" section.
- **Configurability, investigation UI, portability (Part 1.6, on `main`):**
  run-by-run overrides (model, matching strictness, per-gate auto-approve)
  and platform defaults are now editable in the UI instead of requiring a
  config-file edit and restart. Four new read-only investigation tools — a
  match/cluster inspector, a run timeline with token cost, a bounded schema/
  data sampler, and an in-app diff against the hand-merge golden. The
  project now runs on any machine, not just this one: `python
  scripts/bootstrap.py` sets up a plain venv (no conda anywhere in it) from
  a fresh clone, and `docker build .` produces a working image — both
  proven live, including a real bug the Docker boot test caught (the app
  bound only to `127.0.0.1`, which a container's port-forwarding can't
  reach). See [TODO.md](TODO.md)'s "Part 1.6" section.

- **CI, cleanup and tooling (2026-09, on `main`):** GitHub Actions CI went
  green for the first time in the repo's history, after four independent
  root causes were fixed — 47 unlinted ruff errors in `scripts/ground_truth/`,
  an eval stage that hung forever (a blanket `*.csv` had untracked the eval
  fixture, degrading it to a 2-row stub whose 3 distinct names never returned
  from Splink blocking), CI installing 5 of 8 extras against a baseline built
  with all 8, and a test stage that hung 30 minutes in CI while passing in 64s
  locally (a tracked `config.yaml` pointing at a LAN vLLM box, no LLM timeout,
  and a fixture falling through to the production config — meaning the
  "mocked" integration tests had been making live model calls). The unreachable,
  untested Databricks connector was deleted, 56 skipped PostgreSQL destination
  tests were unskipped, and `graphify-out/` was untracked. See
  [TODO.md](TODO.md) and the git log.

Read [TODO.md](TODO.md) for the evidence behind every one of those claims —
it is the append-only working log this project was built from, phase by
phase, with real numbers rather than descriptions.

## Quick start

```bash
python scripts/bootstrap.py
```

That one command creates `.venv`, installs deps, builds the frontend, copies
`.env.example` → `.env`, installs the git hooks, and builds the code graphs.
**Run it before anything else.** `.git/hooks/` is never cloned — git excludes
hooks so cloning a repo cannot execute its code — so until you run this, no
hook in this repo does anything at all.

Fill in `LLM_API_KEY` in the new `.env`, then:

```bash
.venv/bin/python -m src.main --mode gradio --port 7860      # Linux / macOS
.venv\Scripts\python -m src.main --mode gradio --port 7860  # Windows
```

(`--mode gradio` is the legacy flag name — it serves FastAPI + the React
SPA. `--mode both` does not work; see `CLAUDE.md`.) Or build the Docker
image instead of a venv: `docker build -t merge-engine . && docker run -p
7860:7860 -e LLM_API_KEY=sk-... merge-engine`.

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
    connectors/    postgres, clickhouse, mongodb, + keyset/watermark helpers
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

## Working on this repo

Three code-graph tools are the primary way to understand this codebase;
reading source is the fallback, not the default. Go down the ladder and stop
at the first tier that answers:

| Tier | Tool | Typical use |
|---|---|---|
| 1 | **graphify** | `graphify query "<question>"`, `graphify affected "X"` (what breaks if X changes), `graphify god-nodes` |
| 2 | **graft** | `graft ask "<query>"` → ranked nodes with exact `file:line`; `graft skeleton <file>` for an API surface |
| 3 | **codebase-memory-mcp** | `search_graph`, `trace_path`, `get_code_snippet` over MCP |
| 4 | raw source | grep and read — always correct, so use it to confirm anything surprising from 1–3 |

All three are **gitignored local caches**, never committed — `graphify-out/`
alone was 750k lines of tracked JSON, three quarters of the whole tree.
`scripts/bootstrap.py` builds them on a fresh clone, and a `post-commit` hook
refreshes all three in the background after each commit (~8s, no LLM, no API
key — `scripts/refresh_graphs.py`). If they are missing, build them:

```bash
python scripts/refresh_graphs.py
```

Never use the LLM paths (`graphify extract --mode deep`, `graphify label`,
`graft build --deep`); the free tiers already answer what this repo needs.

[Ponytail](https://github.com/dietrichgebert/ponytail) is installed and sets
the standing brief for *how much* to build. `/ponytail-audit` pairs well with
the graph tiers — but "no callers in the graph" is not proof of no callers,
so confirm with grep before deleting anything.

`CLAUDE.md` and `AGENTS.md` carry the full rules for agents working here.

## Observability

Tracing is a self-hosted [Langfuse](https://langfuse.com) stack
(`docker/langfuse-compose.yml`) — every LangGraph node becomes a `CHAIN` span
and every LLM call becomes a `GENERATION` span, keyed by the run's thread ID.
Start it with:

```bash
docker compose -f docker/langfuse-compose.yml --env-file docker/langfuse.env up -d
```

Then set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` in
`.env` (the stack auto-creates these on first boot). Every HITL resume lands
in the same trace (one merge run = one trace, not one per gate), and
`resolve_entities`'s internal blocking/model-fit/scoring/clustering phases
show up as their own child spans. Verify a run's trace automatically with:

```bash
python -m scripts.diagnostics.verify_langfuse_trace <thread_id>
```

## Testing and CI

```bash
pytest                          # 2081 passed, 2 skipped, 63s
python scripts/check.py all     # the full 11-stage gate, ~90s
```

`check.py all` is *the* verification command, and it is exactly what CI runs.
Eleven stages, in order: `lint`, `format`, `arch`, `docs`, `portability`,
`guards`, `types`, `frontend`, `test`, `eval`, `prompts`. If it passes
locally and fails in CI, that is a bug in `check.py`, not something to work
around.

`guards` runs the custom pre-commit hooks (blanket-gitignore guard,
`sample_databases/` guard, `.format()`-on-prompts guard, bare-`except`-`pass`
ratchet, and a requirements/pyproject drift check). They used to live only in
`.pre-commit-config.yaml`, which meant `--no-verify` skipped them and CI never
ran them at all.

**The two remaining skips are environmental, not disabled tests:**

- `tests/eval/test_matcher_recall.py:158` — needs the 1500-row
  `tests/eval/pagila_staff.csv`. A blanket `*.csv` line in `.gitignore` meant
  that file was **never committed to any branch**, so it is not recoverable
  from git history (recorded BLOCKED in `dc4d65b`). The `.gitignore` rule is
  fixed and guarded, but the data itself still has to be regenerated.
- `tests/eval/test_matcher_recall.py:237` — needs a live PostgreSQL on
  `localhost:55201` for the parity leg.

### Continuous integration

Two GitHub Actions workflows, both green:

- **CI** (`.github/workflows/ci.yml`) — runs `python scripts/check.py all` on
  every push and PR. ~3.5 min.
- **Docker** (`.github/workflows/docker.yml`) — builds the image, runs
  `pip check`, and asserts every runtime import resolves. Path-filtered to
  the files that can change the image. This exists because `requirements.txt`
  had silently drifted from `pyproject.toml` and omitted `networkx`, so the
  built image raised `ModuleNotFoundError` the moment anyone opened the
  metadata graph view — and because that import is *lazy*, the app booted
  fine and a boot-and-curl smoke test would never have caught it.

## Known, documented limitations

- ClickHouse destination loads are **not** transactional (only PostgreSQL
  gets full stage-then-swap atomic loads) — the app says so rather than
  pretending otherwise.
- MongoDB is origin-only; there is no MongoDB destination connector.
