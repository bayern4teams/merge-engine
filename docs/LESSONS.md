# Lessons — what was tried and did not work

One entry per approach that failed, was reverted, or was rejected by a review
or a measurement: what was tried, the measured result, why it was dropped,
what replaced it, and a link to the record. Links, not copies (J2). Where a
source does not say why, the entry says "reason not recorded".

<!-- verified: 4d33e0c -->

## Silent data loss

### Hard comparison budget truncated comparisons (before 2026-08)

- Tried: cap candidate-pair scoring at `_MAX_COMPARISONS` (10,000, later
  250,000) and break past it.
- Measured: on 2,484 records / 28,170 candidate pairs, 18,170 pairs were never
  compared and the run reported success; two identical runs produced 2,483 and
  2,482 output rows (unstable block iteration order, `llm_scored=0` both).
  Phase L made the loss counted; it was still loss — one entity later discarded
  711,670 pairs unexamined on a run that ended `completed`, and an enrichment
  run ended `completed` having dropped 9,556,382 pairs.
- Why dropped: data loss with no evidence it happened.
- Replaced by: lossy-by-design blocking (token blocks, filtering, purging,
  WNP meta-blocking) whose losses are properties of the block collection and
  are reported as upper bounds; `_MAX_CANDIDATE_PAIRS` (5M) kept only as an
  out-of-memory guard that fails the run loudly.
- Records: docs/architecture/data_pipeline.md §7,
  docs/architecture/known_problems.md §18.4,
  docs/decisions/0014-replace-budget-truncation-with-blocking-analysis.md.

### OFFSET pagination skipped rows (before 2026-08)

- Tried: extract with OFFSET/LIMIT.
- Measured: on a synthetic 300,000-row table with a 3-column key, 33% of rows
  were silently lost and the run reported success (a strict `>` boundary
  predicate dropped same-key rows at every batch edge). The `>=` fix then
  exposed a second bug: without a tiebreak, PostgreSQL reordered equal-key
  rows and duplicated 3 of 16,044 real Pagila rows.
- Why dropped: silent loss and silent duplication.
- Replaced by: keyset pagination — `>=` + a `boundary_seen` count + a
  deterministic md5/cityHash tiebreak column (`src/mcp_server/connectors/keyset.py`).
- Record: docs/architecture/data_pipeline.md §6.

### A blanket `*.gitignore` rule deleted real project inputs (2026, twice)

- Tried: bare `*.json` and `*.csv` lines in `.gitignore` (intended for data
  dumps).
- Measured: `src/ui/frontend/package.json` was silently untracked — a fresh
  clone could not `npm install`; then `tests/eval/pagila_staff.csv` was never
  committed to any branch and is unrecoverable from history — the eval
  harness degraded to a 2-row stub whose 3 distinct names hung Splink blocking
  forever, stalling every CI eval run.
- Why dropped: an ignore rule that matches source is a deletion you don't
  notice.
- Replaced by: narrower rules plus the `no-blanket-data-ignore` guard
  (pre-commit and `check.py guards`). The 1,500-row fixture still needs
  regeneration (recorded BLOCKED in `dc4d65b`).
- Records: AGENTS.md §"Rules that are mechanically enforced",
  docs/architecture/known_problems.md §18.1, README test-skips note.

### A decorated function lied about its signature (before 2026-08)

- Tried: `retry_on_transient` without `functools.wraps`.
- Measured: `inspect.signature(connector.extract)` saw the decorator's
  `(*args, **kwargs)`, so `_accepts_pk_columns` was always `False` — keyset
  pagination never activated on any connector, on any run, silently.
- Why dropped: invisible until something introspects the wrapper.
- Replaced by: `@functools.wraps` on all decorators.
- Record: docs/architecture/state_and_graph.md §4.

## LLM over-reach

### The destination schema grew by invention (found 2026-09, Phase 0)

- Tried: let field mapping create new destination columns for unmappable
  fields (prompted, not enforced: a prompt saying "prefer a new column" was
  obeyed).
- Measured: a manual inspection found the destination `company` table grown
  from 14 columns to 43 with invented columns.
- Why dropped: the destination schema is a contract for downstream readers;
  code growth per run is unbounded drift.
- Replaced by: `map`/`skip` only — an LLM-proposed `new_column` is refused in
  code (`src/mcp_server/tools/map_fields.py`), unmappable fields land in
  `unmapped_fields`.
- Records: AGENTS.md §"Rules that are mechanically enforced" (BUG A), README
  "Harness foundation" note, docs/verification/BASELINE_PRE_CHANGE.md.

### LLM metadata non-determinism changed schema shape between identical runs (Phase O, 2026)

- Tried: nothing — this was observed. Two runs of identical data produced
  identical candidate pairs (28,170), clusters (2,482) and row counts, but 15
  destination columns in one run and 13 in the other (the LLM folded
  `customer_id` differently).
- Why it matters: "re-run the same merge" was a coin flip on schema shape.
- Replaced by: run profiles pinning the four non-deterministic LLM decisions
  (`src/orchestrator/profiles.py::PINNED_KEYS`).
- Record: docs/architecture/llm_and_profiles.md §12.

### Per-row LLM scoring was never the plan (design, kept as a boundary)

- Boundary: every LLM call is O(schema) or O(borderline pairs), never
  O(rows) — "more LLM influence, not more LLM per row". Five call sites,
  listed in docs/architecture/overview.md §0. No measured failure; recorded
  so a future change doesn't cross it "reason not recorded" applies to any
  earlier per-row experiment — none is documented.

## Matching that merged too much

### Union-find over all above-threshold edges (before Phase L)

- Tried: chain every pair scoring above the bar into one cluster.
- Measured: one weak bridge merged whole connected components; merging on
  "not sure" evidence measurably destroyed distinct people.
- Replaced by: confident-edges-only clustering with WNP pruning, conflict
  repair on exclusive identifiers, and triangle support; borderline pairs go
  to the matches gate instead of an auto-merge (`_build_clusters`).
- Record: docs/architecture/data_pipeline.md §7.

### Average-linkage cohesion cut and name-coherence splits (Phase L era, later dropped from the main path)

- Tried: post-repair average-linkage cut on every component; splitting
  clusters whose names looked incoherent.
- Measured: a name-based split destroyed real merges (name coherence is a
  quality flag now, P16, not a cut); the cohesion cut left the main path
  (`_split_component` passes `min_cohesion=0`; the helpers remain only for a
  non-zero parameter).
- Why dropped: over-splitting cost real merges; precision is bought from WNP
  and repair instead.
- Record: docs/architecture/data_pipeline.md §7 ("The average-linkage
  cohesion cut … no longer part of the main path").

### Unique-identifier veto wired to one code path only (Phase O.2, caught by a unit test)

- Tried: thread `extra_unique_ids` through `_score_pair` only.
- Measured: on datasets under the 30-pair model threshold, the heuristic path
  never received the rule — two records sharing a name but declaring
  different member IDs merged anyway.
- Replaced by: both paths receive the declared ids.
- Record: docs/architecture/data_pipeline.md §7.

### `u` from the EM's own blocked candidates (before P13)

- Tried: take the disagreement rate `u` from the unsupervised EM.
- Measured: the EM only sees blocked look-alikes, so `u` came out far too
  high and every agreement was under-weighted.
- Replaced by: `_fit_match_model` draws 3,000 random unblocked pairs
  (seeded, reproducible) for `u`; `m` is fitted on candidates and `u`
  swapped in afterwards.
- Record: docs/architecture/data_pipeline.md §7 (P13).

## State, checkpointing, resume

### Row data inside LangGraph state (before Phase N)

- Tried: carry extracted rows in the checkpointed state dict.
- Why dropped: SQLite would serialize a 43-million-row list after every node.
- Replaced by: the per-run DuckDB spool (`src/orchestrator/spool.py::RunSpool`).
- Record: docs/architecture/data_pipeline.md §5.

### Spool deletion by in-memory membership (Part 1 era)

- Tried: delete spool files for runs no longer in `_active_workflows`.
- Measured: a run parked at a HITL gate lost its spool while a new run was
  starting — the in-process dict is not a liveness record.
- Replaced by (symptom-level): a 6-hour age guard. The root cause (run state
  in process memory) is still open and documented as future risk §19.2/§19.3.
- Record: docs/architecture/known_problems.md §19.2–19.3.

### HITL gate bodies on resume (2026, historical bug)

- Tried: putting gate side effects in the gate node body.
- Measured: with `interrupt_before`, the gate body never runs on resume —
  only the router does, so resume-time work silently didn't happen.
- Replaced by: resume-time logic in shared helpers called by
  `WorkflowManager.resume_workflow` (e.g. `apply_band_decisions`).
- Records: AGENTS.md §"HITL resume trap",
  docs/architecture/orchestration_and_ui.md §13.

### Cost ledger keyed on a constant session name (Phase O.5)

- Tried: accumulate tokens under `session_name = "merge-engine"`.
- Measured: every per-run cost query returned 0 calls while the semantic
  cache correctly logged 18 lookups for the run.
- Replaced by: the real `run_id` threaded through `llm_config`
  (`src/orchestrator/graph.py::get_initial_state`).
- Record: docs/architecture/llm_and_profiles.md §11.

### Helper misindented inside the client class (2026)

- Tried: inserting `_estimate_tokens` mid-class in `client.py`.
- Measured: Python ended the class at the dedent — `call_json`/`call_batch`
  became module functions; 5 tests failed with a missing attribute, nothing
  raised at import.
- Replaced by: placement tests + the lesson that wrong indentation in a class
  body is a silent structural bug.
- Record: docs/architecture/llm_and_profiles.md §11.

## Rejected dependencies (measured or checked, with reasons)

| Tried | Result | Replaced by / outcome | Record |
|---|---|---|---|
| OpenMetadata as the metadata store | needs 4+ services minimum for a catalog UI this project would still wrap | own metadata tables (ADR 0011 model) | docs/decisions/0010-reject-openmetadata.md |
| DataHub as infrastructure | worse footprint (GMS, Elasticsearch, MySQL, Kafka, ZooKeeper…) | its source/dataset/column model adopted into PostgreSQL | docs/decisions/0011-reject-datahub-adopt-its-model.md |
| Neo4j for traversal | graphs here are thousands of nodes, not millions | PostgreSQL + networkx | docs/decisions/0012-reject-neo4j.md |
| Informatica | commercial product, never installable here | capability benchmark only | docs/decisions/0013-informatica-benchmark-only.md |
| HierGAT reference code | PyTorch 1.4 / Python 3.7 research code, unmaintained | relational signal adopted natively (B.5) | docs/decisions/0017-reject-hiergat.md |
| RELATER reference code | Python 2, 2-commit history | `_relational_agreement` natively | docs/decisions/0018-reject-relater-code-adopt-idea.md |
| Utopia (Rust KG system) | this repo's own `kg` schema already is its decision ledger | reference pointer only | docs/decisions/0019-utopia-reference-only.md |
| AWS Ontop accelerator | Neptune/Bedrock cloud coupling | Ontop kept as a portable OBDA reference if a SPARQL need appears | docs/decisions/0020-ontop-study-not-aws-accelerator.md |
| `semantica` rule engine | pre-1.0, dragged an unrelated audio/CV dependency tree, failed to install | native rules-as-data engine (B.4) | docs/decisions/0021-semantica-immature-native-rule-engine.md |
| Databricks destination connector | unreachable, untested, no live instance to verify | connector deleted 2026-09-23; rebuild is new work | docs/architecture/known_problems.md §20 item 15, README CI note |
| Splink 5.x | broke the Linker/DuckDBAPI surface `splink_*.py` use (9 mypy errors in CI) | pinned `>=4,<5` | `pyproject.toml` splink extra comment |
| numpy 2.5 stubs | PEP 695 syntax halted the whole `mypy src` run silently (93 files unchecked) | pinned narrowly in `pyproject.toml` | `pyproject.toml` numpy comment |

## CI, build, and environment

### "Mocked" integration tests making live LLM calls (2026-09)

- Tried: nothing deliberately — a fixture fell through to the production
  `config.yaml`, which pointed at a LAN vLLM box with no LLM timeout.
- Measured: the CI test stage hung 30 minutes while passing locally in 64s.
- Replaced by: test config isolation, LLM timeouts; CI green for the first
  time after four independent root causes (also: 47 unlinted ruff errors,
  the lost eval fixture, CI installing 5 of 8 extras).
- Record: README "CI, cleanup and tooling" note, git log 2026-09.

### Docker image shipped a missing import (2026)

- Tried: `requirements.txt` maintained by hand alongside `pyproject.toml`.
- Measured: it silently dropped `networkx`; the image raised
  `ModuleNotFoundError` on the metadata graph view — and because that import
  is lazy, the app booted fine, so a boot-and-curl smoke test passed.
- Replaced by: generated requirements (`scripts/generate_requirements.py`),
  the requirements/pyproject drift guard, and the docker workflow's import
  resolution assertion.
- Record: README "Continuous integration", `Dockerfile` comments.

### The app bound to 127.0.0.1 only (Part 1.6.C)

- Tried: default uvicorn bind.
- Measured: the Docker boot test failed — a container's port-forwarding
  cannot reach a loopback-only bind.
- Replaced by: 0.0.0.0 bind with the auth caveat (API is unauthenticated —
  keep it localhost/trusted-network).
- Record: README "Configurability, investigation UI, portability" note,
  docs/architecture/known_problems.md §19.8.

### Undeclared `python-dotenv` (found live 2026-08-10)

- Tried: rely on transitive installation for dotenv loading.
- Measured: a from-scratch bootstrap left every `${ENV_VAR}` placeholder in
  `config.yaml` unexpanded; the symptom was a spurious "invalid API key"
  error that was really "the key was never read from .env at all".
- Replaced by: explicit declaration + the M4 (declared-deps-used) discipline.
- Record: `pyproject.toml` python-dotenv comment.

## Reporting that lied

### `completed` treated as proof (measured 2026-09-24)

- Tried: trust the workflow status as the run's verdict.
- Measured: one run finished `completed` having discarded 9,556,382 candidate
  pairs unexamined, left 78 of 99 `company.name` values NULL, and produced
  zero field mappings for 8 of 16 entities. Separately, the matcher once
  reported `uncompared_pairs = 0` while having dropped 465 pairs.
- Why dropped: no stage can fail a run on its *results* — only on an
  exception. Status reports intent, not outcome.
- Replaced by: mandatory independent verification per task (AGENTS.md §"Live
  verification is mandatory"); pre-load invariants that refuse a bad load
  before writing; the write-the-scorer-before-the-run rule.
- Record: AGENTS.md §"Live verification is mandatory".

## Open items with no lesson learned yet

- Resolution at 43M-row scale: never measured; 20k records in 39 s is the
  best measured (docs/architecture/known_problems.md §19.1).
- The eval fixture regeneration is still BLOCKED (`dc4d65b`); baseline
  matcher numbers date from the full fixture.
- License not chosen (owner decision pending as of 2026-11-26; this docs pass
  flagged it).
