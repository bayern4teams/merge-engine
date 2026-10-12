[< back to docs/INDEX.md](../INDEX.md)

# Testing strategy and the design-decision index

## 16. Testing strategy

`tests/unit/` — one file per subsystem, 908 tests passing as of the last
full run (0 failed, 83 skipped; skips require a live database container).
`tests/eval/` — a **labelled-pair precision/recall harness**, deliberately
separate from `tests/unit/`, because "the matcher didn't crash" and "the
matcher is precise" are different claims requiring different kinds of
tests — the eval harness is what produces the actual precision/recall
numbers cited throughout this document and `TODO.md`, derived from real
databases with known ground truth (a genuine duplicate in Pagila's `staff`
table; repeated titles in `sample_mflix`) plus every human decision captured
at the match-review gate during live runs. `tests/prompts/` — golden-file
tests asserting the LLM prompt templates render as expected for representative
inputs, independent of whether the LLM itself is reachable.

**A repeated pattern worth internalising**: several "bugs" caught during
Phase L/O verification were actually bugs in the *test*, not the product —
a cache-key test padding the wrong string, a ClickHouse uniqueness test that
needed correcting after the deliberate design decision to never trust
ClickHouse's declared key, a cleanup test assuming membership-only sweeping
after that behaviour was intentionally changed. The discipline that mattered
was not treating "a test fails" as automatic proof the code is wrong —
each failure was traced to its actual cause before anything was changed.

---

## 17. Index of every design decision and its alternative

A quick-reference table for "why did it do X and not Y," each pointing back
to the section with the full reasoning.

| Decision | Alternative considered | Why not the alternative | Section |
|---|---|---|---|
| Rows in a per-run DuckDB spool | Keep rows in checkpointed graph state | Checkpoint size scales with source data; 43M rows cannot fit | §5 |
| DuckDB bulk insert via registered DataFrame | `executemany` | 1,300x slower; 43M rows would take 60+ hours | §5 |
| Age-based spool cleanup guard | Membership-only cleanup (`_active_workflows`) | In-memory set is empty after restart; deleted a live run's rows | §5 |
| Keyset (seek) pagination | `OFFSET`/`LIMIT` | Cost grows with depth; keyset stays flat | §6 |
| `>=` + boundary-seen skip-count | Strict `>` predicate | Silently drops rows sharing the boundary key (measured 33% loss) | §6 |
| Deterministic row-hash tiebreak in `ORDER BY` | `>=` alone | Equal-key rows aren't stably ordered across queries; duplicated 3/16,044 rows | §6 |
| ClickHouse never trusts its declared primary key | Shared fast-path with Postgres | Measured: sparse sort key, not unique (43.28M rows / ~42.97M distinct tuples) | §6 |
| Fellegi–Sunter + term frequency | Averaged string similarity vs. 2 constants | Can't express "rare value agreement is stronger evidence" | §7 |
| `u` from randomly sampled pairs, `m` from EM; name tokens IDF-weighted | EM for both; whole-value or binary-token similarity | EM sees only blocked look-alikes so `u` came out far too high; binary tokens turned every typo into DISAGREE | §7 |
| EM-estimated m/u, unsupervised | Hand-tuned constants / labelled training | No labels available; constants don't adapt per dataset | §7 |
| Token blocking + filtering/purging + WNP meta-blocking, with a raising memory guard | Hard comparison cap (`_MAX_COMPARISONS`); earlier layered keys | Cap silently dropped candidate pairs and still reported success; key-based blocking privileged some fields and lost CJK/typo'd names | §7 |
| Confident edges + WNP + conflict repair + triangle support | Raw transitive closure; average-linkage cohesion cut; name-coherence split | One bad edge chain-merges a component; the name split destroyed real merges (names are a flag, P16) | §7 |
| Uncertain band decided at the matches gate, answers optionally remembered | Band silently left separate | The reviewer never learned those pairs existed; decisions were asked again every run | §7 |
| Pre-load invariants with run-proposed thresholds, refusing the load | Final approval on counts alone | Nothing failed a run on its results; a hollow `completed` was possible | §10 |
| Deterministic cluster IDs (hash of sorted member IDs) | Random UUID per cluster per run | Re-running identical data duplicated every destination row | §7 |
| `strictness` only moves the auto-*match* bar | Also moving auto-exclude | User priority: over-merging is the worse error; only the recoverable direction should move by default | §7 |
| Configurable per-field conflict policy (`longest`, text only; `completeness` kept as alias) | Hardcoded priority→completeness→recency | User wants customisable survivorship; unconfigured entities keep the old chain exactly | §8 |
| Value-based inclusion-dependency join graph; roles only proposed at the deps gate | Name-pattern edges | Names proposed edges the data rejects and missed those it did not match | §6b |
| Pinned field mapping reconciles against live destination schema | Reuse pinned mapping as-is | A fresh destination never got its columns; 1-column table where derivation gave 9 | §9 |
| PostgreSQL destination: stage + atomic swap | Batch-by-batch upsert | Mid-load failure used to leave the destination half-written | §10 |
| Cache key includes model/temperature/format/json_mode | Cache key = prompt text only | A cache that can return an answer different from what the model would currently give is worse than no cache | §11 |
| Cost ledger records tokens, not currency | Hardcoded per-model $/token pricing | Model changes whenever a quota runs out; a wrong price is worse than none | §11 |
| `run_id` carried inside `llm_config` | Global/session-level cost tracking | Keying on a constant `session_name` made every run report 0 calls | §11 |
| Credentials restored by connection coordinates | Restored by list position/index | Reordering origins would silently repoint a profile at the wrong database | §12 |
| Profile capture only on clean completion | Capture on any run touching a profile | A failed/partial run would pin a half-formed mapping as if validated | §12 |
| UI polls `/status` every 2s | WebSocket/SSE push | Runs pause for human-timescale durations; nothing to push most of the time, and polling survives a refresh with no reconnect logic | §14 |
| Frontend built to a static bundle, served by FastAPI | Separate frontend dev server in production | One process, one port, no CORS, frontend/backend ship as one versioned artifact | §14 |
| Self-hosted Langfuse | Hosted SaaS tracer | Trace data derives from the user's real database rows; keep it off third-party infra by default | §15 |

---
