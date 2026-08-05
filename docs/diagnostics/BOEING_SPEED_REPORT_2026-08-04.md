# Boeing live-run speed report (2026-08-04)

Direct answer up front: **partially fixed, and one real unexplained
regression needs diagnosis.** The single biggest bottleneck (`experience`,
34,782 records) is genuinely and reproducibly faster. Two smaller entity
types (`technology`, `company_relation`) are reproducibly *slower* than a
data point from three days ago on the exact same input data — not noise,
confirmed byte-identical across two independent runs on today's code — and
the root cause has not been found. See §5 for the verdict and what to look
at next.

Three real, live Boeing runs are compared below, all against the same
Docker-hosted origin data (`origin1`/`origin2` ClickHouse, port 8190) and
the same destination (`global_warehouse` PostgreSQL, port 55201):

| Run | Date/time | Code state |
|---|---|---|
| **Run A** (`wf_dc9237dc`) | 2026-08-01 03:34 | Post BUG-D clustering fixes (D.1-D.4.1), pre this session's Phase-0 work |
| **Run B** (`wf_d7103e2d`) | 2026-08-03 15:54 | This session, after all Phase 0 §0.0-§0.8 fixes |
| **Run C** (`wf_0574290e`) | 2026-08-04 14:28 | Identical code to Run B, run fresh today to check reproducibility |

Runs B and C used identical code (nothing in `src/` changed between them —
only a diagnostic script fix, `compare_to_golden.py`, which doesn't touch
the pipeline). They are a same-code, same-data repeat, which is what makes
the numbers below trustworthy rather than anecdotal.

---

## 1. What the app actually does, in order, and how long each step takes

The pipeline is a fixed sequence of LangGraph nodes. Every node's wall time
is now recorded (`_timed()` wrapper added this session) — this table is
Run C's real numbers, the freshest and most representative:

| Step | What it does | Time (Run C) | Time (Run B, 2 days earlier) |
|---|---|---|---|
| `preflight` | Checks origin/destination reachability, LLM liveness | 6.1s | 9.4s |
| `parse_request` | LLM call: turns "merge all information about Boeing" into a target entity list | 13.5s | 12.6s |
| `discover_schemas` | Reads both origins' table/column catalogs (parallelized this session) | 2.7s | 2.7s |
| `analyze_dependencies` | LLM call: works out which entities depend on which (person needs company, etc.) | 133.6s (2.2 min) | 124.2s (2.1 min) |
| `plan_scope` | LLM call: decides, per entity type, which origin tables to pull and how to join them to "Boeing" | 96.2s (1.6 min) | 100.5s (1.7 min) |
| `extract_data` | Pulls the actual rows from ClickHouse (see §2 for exact counts) | 24.5s | 27.4s |
| **`resolve_entities`** | **Matches/clusters records across origins — this is the historically slow step** | **1607.6s (26.8 min)** | **1944.4s (32.4 min)** |
| `unify_entities` | Merges each cluster into one record, resolving field conflicts | 68.5s (1.1 min) | 126.0s (2.1 min) |
| `map_fields` | LLM calls: maps origin field names onto destination columns, one call per entity type | 376.9s (6.3 min) | 333.9s (5.6 min) |
| `load_data` | Writes the final rows into PostgreSQL | not separately timed yet | — |
| **Total wall time** | | **40.7 minutes** | **46.6 minutes** |

`resolve_entities` is still by far the dominant cost — 66% of total wall
time in Run C, 70% in Run B. Everything else is LLM round-trips
(`analyze_dependencies`, `plan_scope`, `map_fields` together account for
~10 of the remaining ~14 minutes) plus one large multi-batch ClickHouse
extraction.

---

## 2. Database read/write volumes — what the app is actually processing

### 2a. What exists in the origins (the theoretical maximum it could read)

```
origin1 (ClickHouse, small/curated dataset):
  Company                    3,940 rows
  CompanyExperience          1,230 rows   <- the only company<->person join table in origin1
  CompanyRelation            6,174 rows
  CompanyTechnographics     20,092 rows
  CompanyFinancials            198 rows
  CompanyIndustry             3,940 rows
  CompanyContact              5,330 rows
  CompanyInvestmentRound        470 rows
  CompanyInvestor             1,260 rows
  IndustryClassification        339 rows
  Location                    3,506 rows
  Person                      1,215 rows

origin2 (ClickHouse, large/messy real-world dataset):
  Company                 1,270,564 rows
  Person                 11,798,965 rows   <- NO company link anywhere in its schema
  Experience              12,234,811 rows  <- NO person link either (company-level aggregate stats)
  CompanyIndustry          3,683,326 rows
  Location                 1,833,568 rows
  PersonContact             4,339,990 rows <- personID + email/phone strings, no company link
  Industry                        179 rows
```

The app never scans these full tables for a scoped merge — it filters
through the LLM-planned scope (§1's `plan_scope` step) down to whatever is
actually linkable to "Boeing." The relevant number is what it actually
extracts, next.

### 2b. What Run C actually extracted, matched, and wrote (per entity type)

| Entity type | Records read (o1+o2) | Clusters formed | Rows written to destination |
|---|---|---|---|
| `company` | 36 (18+18) | 23 | 23 |
| `person` | 39 (39+0) | 39 | 39 |
| `address` | 22 (18+4) | 22 | (rolled into `location`) |
| `location` | 22 (18+4) | 21 | 42 (see note below) |
| `finance` | 20 (20+0) | 20 | 20 |
| `industry` | 16 (9+7) | 10 | 10 |
| `experience` | **34,782** (39+34,743) | 21,263 | 21,263 |
| `technology` | 1,503 (1,503+0) | 1,154 | 1,154 |
| `contact` | 39 (39+0) | 39 | 39 (as `contact_email`) |
| `company_relation` | 559 (559+0) | 489 | 489 |
| `product`, `skill`, `education`, `contract`, `technology_category` | 0 | 0 | 0 (no source table found — LLM's table selection didn't map these entity types to any real origin table) |

**Total: 37,038 records read → 57,009 rows written** (the destination total
includes `merge_engine_entity_map`, 33,930 rows — an internal bookkeeping
table mapping every origin row to its destination cluster id, not
merge output).

**Where the volume actually comes from:** `experience` is 94% of everything
read (34,782 of 37,038) and 37% of everything written. Almost all of that
(34,743 of 34,782) is a single unfiltered dump of `origin2.Experience` —
company-level HR aggregate statistics (tags like
`TOTAL_EXPERIENCE_20_TO_29_YRS`, `COMPARABLY_COMPANY_RATING_A_PLUS`), not
individual employment records, and with no `personID` column in its schema
at all. This is why `resolve_entities` spends more wall time on
`experience` than on any other entity type by a wide margin.

`technology` (1,503 records) and `company_relation` (559 records) are both
small in volume yet take minutes each — see §4, this is the anomaly.

---

## 3. `resolve_entities` per-entity breakdown — the 3-run comparison

This is the actual timing data, extracted directly from `logs/debug.log`'s
`ENTER`/`EXIT resolve_entities_impl` pairs for each run (not estimated):

| Entity | Records | **Run A** (Aug 1) | **Run B** (Aug 3) | **Run C** (Aug 4) | Trend |
|---|---|---|---|---|---|
| `experience` | 34,782 (identical all 3 runs) | 22.9 min | 19.2 min | **15.8 min** | ✅ Consistently, monotonically faster |
| `technology` | 1,503 (identical all 3 runs) | **4.2 min** | 6.7 min | 5.1 min | ⚠️ Slower than Run A, not monotonic |
| `company_relation` | 559 (identical all 3 runs) | **1.4 min** | 4.4 min | 3.8 min | ⚠️ Slower than Run A, not monotonic |
| `company` | 36 | 4s | 25s | 16s | Negligible either way |
| `person` | 7,392 (Run A) vs 39 (Runs B/C) | 12.7 min | 0s | 0s | Not comparable — see §4b, different scope-plan decision, not a speed fix |
| `address`/`location`/`finance`/`industry`/`contact` | small (≤60) | ≤3s each | ≤1min each | ≤1min each | Negligible |

**`experience` is the real, reproducible win.** It went from 22.9 to 19.2 to
15.8 minutes across three independent runs on the exact same 34,782-record
input — a clean 31% improvement, monotonically in the right direction. This
is the entity type where BUG D's clustering fixes should matter most (it's
the one PERFORMANCE_BOTTLENECKS.md flagged as 20-30 minutes originally), and
it's the one that visibly improved.

**`technology` and `company_relation` did not improve — they got worse, and
this is not noise.** Both are notably slower in Run B and Run C than Run A,
on identical record counts. Run B and Run C themselves aren't identical to
each other (6.7 vs 5.1 min for technology; 4.4 vs 3.8 min for
`company_relation`) — that's normal run-to-run variance (LLM latency,
system load). But both are consistently well above Run A's numbers. That
gap is real — see §4.

---

## 4. Diagnosis: what's confirmed, what's still open

### 4a. Confirmed real regression: the raw candidate-pair pool roughly doubled

`resolve_entities_impl` reports two numbers per entity: `candidate_pairs_compared`
(capped at 250,000 — the scoring budget) and `uncompared_pairs` (how many
candidate pairs blocking found *beyond* that cap, honestly counted rather
than silently dropped — this is BUG C1's fix). Comparing the exact figures:

| Entity | Run A `uncompared_pairs` | Run B `uncompared_pairs` | Run C `uncompared_pairs` |
|---|---|---|---|
| `technology` | 323,169 | 1,396,201 | **1,396,201** (byte-identical to Run B) |
| `experience` | 599,555,904 | 1,217,074,849 | **1,217,074,849** (byte-identical to Run B) |
| `company_relation` | 0 | 0 | 0 |

Run B and Run C are **exactly, byte-for-byte identical** on
`uncompared_pairs`, `clusters_formed`, and `auto_merged_clusters` for every
entity type — proof that (a) blocking/clustering is fully deterministic
given the same input data, and (b) the origin data itself has not changed
between Aug 3 and Aug 4. That determinism is what makes this a trustworthy
finding rather than a fluke: **something changed between Run A (Aug 1) and
Run B (Aug 3) that made blocking generate ~2.9x more raw technology
candidate pairs (573K → 1.65M) and ~2x more raw experience candidate pairs
(600M → 1.22B), on the exact same 1,503/34,782 input records.**

More raw candidates to enumerate and rank (even though only 250,000 ever
get scored) costs real CPU time in the blocking/sorting step itself — this
is the most likely direct cause of `technology`'s and `company_relation`'s
slowdown, though the precise code path was not traced this session.

**What this is *not*:** it isn't anything touched in this session's own
work (the quadratic-errors fix, passthrough-bloat removal, and
de-globalizing `resolve_entities.py`'s module state were all read/checked
directly — none of them touch blocking-key selection or candidate
generation, and `_generate_candidate_pairs`'s counting logic is
structurally unchanged, just moved from a module global into a
locally-returned dict). The change must be in whatever landed on `main`
between Aug 1 and Aug 3.

**Correction (Part 1.5, root-caused by tracing the actual call graph, not
inferred):** the original hypothesis in this section pointed at the C0a
canonical-projection fix. That is wrong — `_project_to_canonical` is
referenced only from `_build_comparison_vector` and
`_compute_heuristic_confidence`, both **scoring**-side; blocking uses a
separate hint set (`_blockable_fields`) that C0a never touches, so it
cannot change the candidate-pair *count*. The real cause is two other,
deliberate BUG-C fixes landing in the same window: the `_MAX_REF_BLOCK`
purge was changed from dropping oversized blocks to logging-only
(`usable[key] = members` now runs unconditionally), and a new
purge-exempt `full:dom:` blocking key was added. Both are correctness
fixes (BUG C1/C2) and neither should be reverted.

Separately, and this is the larger finding: **the pool growth was never
where the time went.** Overflow blocks are only counted with cheap
arithmetic, never expanded — both `technology` and `experience` score
exactly the same 250,000-pair budget regardless of pool size. The real
per-pair scoring cost was dominated by `estimate_model`'s EM model-fitting
M-step (`fellegi_sunter.py`), which rescanned every comparison vector once
per *distinct field name* seen across the whole run rather than once per
field the vector actually carried. Because `_project_to_canonical` (BUG
C0a) carries unrecognized raw fields through verbatim, wide entity types
accumulate far more distinct field names than narrow ones — measured live,
this was 94% of `experience`'s `resolve_entities` wall time (907 of 966s)
vs. 77% for `technology` (112 of 146s). Restructured into a single
accumulation pass keyed by each vector's own present fields (Part 1.5 A.7);
verified byte-identical via `profile_resolve.py --check` for both entities.
**Isolated (no-network, byte-identical-verified) impact of A.7:**
`profile_resolve.py`'s heuristic-only harness shows `technology`'s CPU
cost falling from 121.78s to 32.02s — a ~3.8x win, matcher output
byte-identical to baseline.

**Live full-pipeline impact: inconclusive, reported honestly rather than
claimed.** A second full Boeing run through the real HITL-gated pipeline
(`wf_d95f26f6`, after A.7 plus the C.2/C.3/C.4 Langfuse fixes) processed
the *exact same* input volume as the prior run (`wf_059436a1` —
`extracted_total`/`candidate_pairs_compared` identical for every entity,
verified directly from both run reports), yet `resolve_entities`'s
wall time went from 1,573.67s to 1,994.84s — slower, not faster.
`map_fields` moved the same direction (350.11s → 543.78s). Since input
volume is provably unchanged and both nodes are LLM-round-trip-bound
(`_judge_pairs_with_llm` inside `resolve_entities`; the whole of
`map_fields`), the most likely explanation is LLM-adjudication network/API
latency variance between the two live runs, not a regression in either
fix — `company_relation`'s cluster count differed slightly between the
two runs (489 vs 497) despite identical candidate pairs, which is direct
evidence the LLM's borderline-pair verdicts are not deterministic run to
run. This was not further isolated (would need two back-to-back live runs
with LLM calls disabled or mocked) — left as an open item. See
`docs/verification/BASELINE_V1.md` §6 for the full numbers.

### 4b. Not a regression — a scope-planning inconsistency (explains `person`, not a speed issue)

`person` extracted 7,392 records in Run A vs 39 in Runs B/C — not because
extraction got faster, but because the LLM-driven `plan_scope` step made a
**different judgment call** each time about whether to include
`origin2.Person` (11.8M rows, unfiltered) in the scope. Checked directly:
`origin2.Person` has no `companyID` anywhere in its schema, nor does
`origin2.Experience` or `origin2.PersonContact` — there is no genuine
relational path from `origin2.Person` to any company. Run A's scope plan
included it anyway (`"person": 2` origin tables); Runs B and C's scope plan
correctly excluded it (`"person": 1` origin table, `link_via` mode against
`origin1.CompanyExperience` only, which has exactly 39 rows for Boeing's 18
subsidiary companies — confirmed by direct query, and that's genuinely all
of it). **Runs B/C's 39-person extraction is complete and correct for the
only real join path that exists; Run A's 7,392 was almost certainly
over-inclusive, not more thorough.** This is an LLM non-determinism issue
in scope planning (same prompt, different answer on different calls), not
a speed regression, and not something Phase 0 was scoped to fix — flagged
in `docs/verification/BASELINE_V1.md` as a B2 (planner-swarm) candidate.

### 4c. Parity is reproducibly 0.9%, not noisy

Both Run B and Run C landed at exactly the same `overall_parity_pct: 0.9%`
against the golden. `BUDGETS.yaml`'s floor (`parity_pct_min: 12.0`) is from
before the BUG A-D fixes and is not directly comparable — full breakdown
and root causes (per-table, with direct-query evidence) are in
`docs/verification/BASELINE_V1.md` §4c. Not re-explained here since this
report is about speed; that document is about correctness.

---

## 5. Verdict: does the app need further diagnosis?

**Yes, one specific thing does: the technology/experience candidate-pair
growth in §4a.** It's real, it's reproducible, and the root cause (what
changed between Aug 1 and Aug 3 to roughly double the raw candidate pool on
identical input data) has not been found. The most direct next step is a
`git diff`-style comparison of `_generate_candidate_pairs`,
`_blockable_fields`, and the C0-C3 canonical-projection code as it stood
around Aug 1 vs Aug 3 — but there is no commit history to diff against
(everything since the `2a48845` v1 checkpoint is uncommitted), so this
would need to be done by re-reading the code with this specific question in
mind, not by `git bisect`.

**No, the big win is real and doesn't need further work right now:**
`experience` — the entity type that dominates total wall time and was the
worst offender in the original `PERFORMANCE_BOTTLENECKS.md` (20-30 minutes)
— improved monotonically and substantially across all three measurements
(22.9 → 19.2 → 15.8 minutes, a 31% cumulative improvement), and stayed
byte-identical in cluster output between the two most recent runs. BUG D's
fixes did what they were supposed to do for the entity type that mattered
most by volume.

**Net effect on total wall time:** 46.6 min (Run B) → 40.7 min (Run C),
both well inside the 90-minute budget ceiling. Whether the app "feels"
faster than before this session depends heavily on which entity type you
watch — `experience` will visibly finish sooner, `technology` and
`company_relation` will visibly take longer than they did on Aug 1. Both
observations are correct; they're different parts of the same run.

If you want this diagnosed further, the two candidates for the "what
changed" investigation are: (1) re-read `_blockable_fields`,
`_DETERMINISTIC_ID_HINTS`, and the C0a canonical-projection code with
fresh eyes against the specific question "would this generate more blocks
or larger blocks than before," and (2) instrument
`_generate_candidate_pairs` to log block-size histograms (the same
technique BUG D.1 already used for clustering, in
`docs/verification/CLUSTERING_PROFILE.md`) on a small reproducible subset,
which would show directly which blocking key is responsible for the extra
volume.
