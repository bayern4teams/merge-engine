[< back to docs/INDEX.md](../INDEX.md)

# Known problems, future risks, and ideas worth building

## 18. Known problems — what is actually wrong right now

Verified against the current tree, not remembered. Ordered by how much they'd
hurt. Nothing here is speculative — each was confirmed by running the check
shown.

### 18.1 🔴 The frontend cannot be built from a fresh clone

`.gitignore:39` has a blanket `*.json` rule (intended for data fixtures). It
also matches `src/ui/frontend/package.json`, `package-lock.json`, and
`tsconfig.json`. Confirmed:

```
$ git check-ignore -v src/ui/frontend/package.json
.gitignore:39:*.json    src/ui/frontend/package.json

$ git ls-files src/ui/frontend
(empty)
```

**Nothing in the frontend directory is tracked by git.** Anyone cloning this
repo gets no `package.json`, so `npm install` is impossible and the UI cannot
be rebuilt — and since `dist/` is also ignored (by the top-level `dist/`
rule), they get no pre-built bundle either. The app would start and serve
nothing.

This is the single most important thing to fix before handing the repo to
anyone else. It needs a negation rule (`!src/ui/frontend/package.json` etc.)
or a narrower data-fixture rule than blanket `*.json`.

### 18.2 🟠 `config/config.secrets.yaml` exists but is never loaded

The file sits in `config/`, and `_deep_merge()` exists in
`config_loader.py:113` to merge exactly that kind of overlay — but
`load_config()` **never calls it**. Confirmed: the only references to
`_deep_merge` in the whole codebase are its own recursion and one unit test
in `tests/integration/test_config_resolution.py`.

The danger is specific: a file named `config.secrets.yaml` invites someone to
put a real credential in it, believing it will be used. It will be silently
ignored, and the credential sits on disk doing nothing but existing. Either
wire it up or delete it — leaving a plausible-looking secrets file that
nothing reads is worse than having neither.

### 18.3 🟠 Saved connections store database passwords in plaintext

`config/recent_connections.json` stores each recent origin and destination
including `connection.password` as plaintext. Confirmed by reading the file:
every entry has a non-empty password field.

Two mitigating facts, both real: the file is gitignored (all of `config/` is),
so it never reaches the repo; and this is a local developer tool, not a
multi-tenant service. But note the **inconsistency** — `profiles.py` goes to
deliberate lengths to strip secrets (twice, §12), while the file sitting next
to it in the same directory keeps them raw. Anyone with filesystem access, or
anyone who copies a machine backup, has the database credentials.

### 18.4 🟡 The comparison budget still truncates — but no longer silently

`_MAX_COMPARISONS = 250_000` in `resolve_entities.py:49` is still enforced
(line 638), and pairs beyond it are still dropped (`pairs[:_MAX_COMPARISONS]`,
line 650). What changed in Phase L is that the count is now recorded in
`_LAST_BLOCKING_STATS["uncompared_pairs"]` and surfaced in the reconciliation
report, instead of being a warning nobody read.

So the honest status is: **this is a bounded, visible, reported limit rather
than silent data loss.** The budget was also raised 25× (10,000 → 250,000)
once memoisation made scoring 24× cheaper. But a large enough entity can
still exceed it, and when it does, the pairs beyond the cut are not compared.

### 18.5 🟡 ClickHouse destination loads are not atomic

Only `PostgreSQLDestinationConnector` does stage-then-swap. A ClickHouse load
that fails midway leaves a partially written destination. Documented
everywhere rather than papered over, but it is a real gap in the "all or
nothing" guarantee (§10).

### 18.6 🟡 Databricks is implemented but never verified

`src/mcp_server/connectors/databricks.py` is registered in the connector
factory and will be selected if someone picks that type. Unlike
PostgreSQL/ClickHouse/MongoDB, it has **never been exercised against a live
instance** in this project's verification runs. Treat it as untested code, not
a supported path.

### 18.7 🟡 Local state is growing without any retention policy

`logs/spool/` is at **3.4 GB** and `logs/debug.log` at **201 MB** right now.
The spool has a 6-hour age guard for *deletion safety* (§5) but nothing
enforces an upper bound on total size, and the logs have no rotation
configured at all. `data/checkpoints.db` (6.5 MB + 4 MB WAL) accumulates every
run's full state history with no pruning.

### 18.8 🟢 Repo hygiene issues

- **`.langgraph_api/*.pckl` are tracked in git** — binary runtime state that
  changes on every run, producing meaningless diffs. Needs `git rm --cached`;
  a `.gitignore` entry alone won't untrack them.
- **`node_modules/` is not ignored** (2,420 files, 65 MB) — this is the
  "1000+ files" seen when staging.
- **`.gitignore:75` says `.agetnt/`** — a typo. The real directory is
  `.agents/`, so the rule matches nothing.
- **`src/tests/` and `src/config/` are empty duplicate directories** left over
  from a refactor; the real ones are at the project root. No code references
  either.
- **`.agents/skills/langfuse/` contains a nested `.git` repository** — a
  vendored third-party skill unrelated to this project's Langfuse tracing.
  Committing it as-is would create a broken submodule reference.

---

## 19. Future risks — what will break as this grows

These are not bugs today. They are the places where a reasonable next step —
more data, more users, a second server process — meets an assumption the
current design makes.

### 19.1 Entity resolution has never been measured at scale

**This is the largest honest gap in the project.** Phase N proved
*extraction* at 43M rows. It did not prove *resolution* at 43M rows, and the
two have fundamentally different cost curves: rows scale linearly, but
candidate pairs scale with the square of block size. The 43M-row proof covers
the data path only — extract, spool, reconcile.

The comparison budget (§18.4) means a 43M-row resolution wouldn't fail, it
would **truncate** — and report that it truncated. That's the designed
behaviour, but it means the practical answer to "can this deduplicate 43
million records?" is currently *unknown*, not *yes*. Measuring it is the
single most valuable next experiment.

### 19.2 `_active_workflows` is in-memory, so the app cannot scale beyond one process

Run tracking lives in a plain dict inside `WorkflowManager`. Start a second
uvicorn worker and the two processes have disjoint views of which runs exist:
a status poll load-balanced to the wrong worker reports a live run as
missing. This also means the app **cannot** be run with `--workers 2`.

This is the same in-memory assumption that caused the spool deletion bug
(§5) — that one is fixed at the symptom level with an age guard, but the root
cause (authoritative run state living in process memory rather than in the
checkpoint database) is still there.

### 19.3 The 6-hour spool guard is a heuristic, and heuristics have edges

A spool file untouched for 6 hours is assumed abandoned. A run genuinely
parked at a HITL gate for longer than that — over a weekend, say, which is
exactly what a human-in-the-loop system invites — becomes eligible for
deletion again the moment someone starts a new merge. The window is much
safer than the original membership-only check, but the correct fix is a real
liveness record in the checkpoint DB, not a longer timeout.

### 19.4 SQLite's single writer will become the concurrency ceiling

`data/checkpoints.db` is written after every node of every run. SQLite allows
one writer at a time; several concurrent merges will serialise on it and
eventually surface as lock timeouts. LangGraph ships a Postgres checkpointer
for exactly this, and swapping it is a config-level change — but nothing
today tells you when you're approaching the limit.

### 19.5 Prompt files fail open, not closed

`_read_prompt()` returns `""` for a missing file (§0). Rename a prompt, or
mistype a stage name, and that stage runs with an **empty system prompt** —
no role, no rules, no output contract — and the LLM will still return
*something*, which downstream code will then try to parse. The failure
surfaces as confusing JSON parse errors far from the actual cause. A missing
prompt should raise at import.

### 19.6 The semantic cache is per-process and dies on restart

`SEMANTIC_CACHE` is an in-memory `OrderedDict`. Restarting the app throws away
every cached response, and two workers would each keep their own. The measured
saving was real (20,494 tokens on one replay run), but it only ever applies
within a single process lifetime.

### 19.7 The learned matching model is discarded after every run

The EM-estimated m/u probabilities (§7) are refit from scratch on every run.
The model that a large, well-labelled run learned — genuinely valuable,
dataset-specific knowledge — is thrown away when that run ends. Nothing
persists it and nothing lets a later run start from it.

### 19.8 No authentication on the API

Every route in `src/api/main.py` is unauthenticated. Anyone who can reach the
port can enumerate database schemas, start merges, and abort running ones.
Fine for `localhost` single-user development, which is what it is today;
unacceptable the moment it's bound to anything else.

### 19.9 LLM provider drift

The model chain is configured in `config.yaml` (`llm.model` +
`llm.fallback_models`), and `LLMClient` now advances through it
automatically on a quota error (§11). Different models honour
`response_format` / JSON mode differently, so a fallback that keeps the
pipeline running can also quietly change output shape — and prompts tuned
against one model are not automatically valid against the next. One
concrete failure mode already seen live: a quota-exhausted model made
scope-planning fall back to a weaker heuristic that found zero subject
candidates, and the pipeline still reported `status=completed` with every
entity count at zero — a hollow success, not a crash. Automatic fallback
closes the *quota* half of this risk; it does nothing about output-shape
drift between models, which is still unmeasured.

### 19.10 Entity-cluster partitioning is O(n²) per connected component, unguarded

`_build_clusters` → `_partition_component` / `_average_linkage` /
`_split_weakest_link` (`resolve_entities.py`, §7) run three unguarded O(n²)
passes over every connected component formed by union-find on the scored
pairs. Unlike blocking/scoring, which is capped at `_MAX_COMPARISONS`, there
is no size cap here — if auto-matched pairs chain into one large component,
partitioning cost explodes independent of how good the earlier budget cap
was. Live evidence (Boeing, 2026-08-01): `technology` (1,503 records, small)
took ~9-11 minutes to cluster — nearly as long as `person` (~7,000
records) — meaning a handful of pathologically large/dense components, not
a general per-record cost. This was the dominant runtime cost of every
Boeing verification run that night, well above blocking/scoring or the load
itself. Full detail and four other bottlenecks (serial per-entity LLM
mapping calls, non-vectorized scoring, chunked-IN-filter round-trips, no
mid-run resume) in `docs/diagnostics/PERFORMANCE_BOTTLENECKS.md` — documented, not yet
fixed, pending a dedicated profiling pass.

---

## 20. Ideas and features worth building next

Grouped by what they'd get you. Rough effort in brackets. Nothing here is
started; this is the honest "what would I do with another month" list.

### Directly closes a gap named above

1. **Measure resolution at scale** [M] — run the matcher over 1M, then 10M
   records and record pair counts, wall clock, memory, and truncation rate.
   Turns §19.1 from unknown into a number. *Highest value item on this list.*
2. **Move run tracking into the checkpoint DB** [M] — kills §19.2 and §19.3
   at the root, and unlocks multi-worker deployment.
3. **Postgres checkpointer option** [S] — removes the SQLite write ceiling
   (§19.4). Mostly configuration; LangGraph supports it directly.
4. **Persist the learned m/u model per profile** [M] — §19.7. A profile
   already pins the LLM's decisions; pinning the *matcher's* learned model is
   the natural completion of that idea, and makes re-runs cheaper as well as
   more reproducible.
5. **Shared cache backend (Redis/SQLite)** [S] — makes §19.6's savings
   survive restarts and span workers.
6. **Fail-closed prompt loading + a prompt contract test** [S] — §19.5.
7. **API authentication** [S] — §19.8. Even a single static token would move
   this from "unacceptable off localhost" to "deployable internally."
8. **Retention policies** for spools, logs and checkpoints [S] — §18.7.

### Makes the matching better

9. **Embedding + ANN blocking layer (HNSW/FAISS)** [L] — the one blocking
   layer from the original Phase L plan that was left out, gated behind a
   config flag because it adds a model dependency. It's the recall layer for
   records no deterministic or phonetic rule catches: different transliteration,
   nicknames, reordered name parts.
10. **Active learning from gate decisions** [L] — every keep/split/merge a
    human makes at the match gate is a free labelled example. They're already
    captured for the eval harness; feeding them back as supervision would let
    the model improve with use rather than starting cold each run.
11. **Explainability in the review UI** [S] — `MatchModel.explain()` already
    returns per-field evidence in bits, sorted by magnitude, and the UI
    doesn't show it. This is nearly free and would make the match gate far
    more reviewable: "merged because tax_id matched exactly (+18.2 bits)"
    beats "confidence 0.97."
12. **Per-entity threshold tuning from the eval harness** [M] — suggest
    strictness per entity from measured precision/recall instead of asking a
    user to guess between loose/normal/strict.

### Makes it more useful operationally

13. **Scheduled / triggered runs** [M] — profiles (§12) were explicitly built
    as "the foundation for scheduled runs" and nothing schedules them yet.
    Combined with watermarks (§6), this gives genuine incremental sync.
14. **Dry-run diff mode** [M] — the plan called for a `dry_run` flag that
    stages everything and reports the exact diff without committing. Partly
    present in the staging path; not exposed as a user-facing mode.
15. **Destination connectors: MongoDB, Databricks, S3/Parquet** [M each] —
    Mongo is origin-only today; Databricks is unverified (§18.6).
16. **ClickHouse atomic loads** [M] — insert into a temp table then
    `EXCHANGE TABLES` / partition move, closing §18.5.
17. **Run comparison view** [S] — two runs of the same profile, diffed:
    row counts, cluster counts, schema. The 15-vs-13-column discovery that
    motivated all of Phase O was found by hand; it should be a button.
18. **Cost budget enforcement** [S] — the ledger measures tokens but nothing
    acts on them. A per-run ceiling that pauses at a gate rather than
    silently spending would make "use the LLM more" a bounded decision.

### Bigger bets

19. **Streaming / CDC ingestion** [XL] — watermarks give batch incrementality;
    consuming a change stream (Debezium, logical replication) would make this
    continuous instead of scheduled.
20. **Multi-destination fan-out** [L] — the single-destination limit is a
    deliberate product constraint, not a technical one. Writing the same
    unified result to several destinations is a natural extension.
21. **A rules DSL for survivorship** [L] — §8's policy chain is expressive but
    positional. Real MDM systems eventually need conditional rules ("prefer
    origin A for this field, unless its value is older than 30 days").
22. **Golden-record lineage** [L] — record which origin row contributed each
    surviving field value, and expose it. Currently the merge decision is
    made and the provenance is not retained per field, which is the first
    thing a data steward asks for.
