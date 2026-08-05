# Branch Plan — `feat/matching-precision` (B4)

Copied verbatim from [docs/plans/ONLY_PLAN_TO_IMPLEMENT.md](docs/plans/ONLY_PLAN_TO_IMPLEMENT.md)
(the full programme) at branch-creation time, 2026-08-05, from `main` commit
`e269a5f`. This file is self-contained — you should not need to check out
`main` or another branch to know what to do. If something here conflicts
with the source plan later, the source plan on `main` is the fallback of
last resort, but this file is what you implement against.

**Branch discipline (see `CLAUDE.md` on `main` — copied here for
self-containment):** once you are implementing this branch, `git checkout
feat/matching-precision` and stay there. Never `git checkout main` or
another branch mid-task, never merge/rebase/delete a branch, unless
explicitly told to by the user. Only modify files in the Owns column below
— a file outside it is a request back to `main`, not something to edit
directly. Update this branch's own `TODO.md` live, in the same style the
root `TODO.md` on `main` uses (checkbox + live-verification evidence, never
checked from the app's own success message).

---

## Ownership

| Owns (exclusive write) | Never touches |
|---|---|
| new `tools/identity_profile.py`, `tools/relational_evidence.py`, `tools/splink_matcher.py`; the scoring/evidence half of `resolve_entities.py` (`_build_comparison_vector`, `_comparison_level`, `_judge_pairs_with_llm`, engine dispatch); `state/matching.py`; `api/main.py` matching-rules fields; `PipelineScreen.tsx` advanced-matching disclosure | `_build_clusters`, `_partition_component`, `_average_linkage`, `_split_weakest_link`, `_assign_cluster_id` — **may call, must not modify** (settled on `main` by Phase 0 §0.0 BUG D.2) |

You may **read** any file in the repo, including all of
`resolve_entities.py`. You may only **modify** the *scoring/evidence* half
of that file — `_build_comparison_vector`, `_comparison_level`,
`_judge_pairs_with_llm`, and the engine-dispatch seam (`B4.4a` below). The
*clustering* half (`_build_clusters`, `_partition_component`,
`_average_linkage`, `_split_weakest_link`, `_assign_cluster_id`) is
`main`-owned and frozen for every branch, this one included — you may call
those functions, you may never change their bodies. If a task seems to
require changing clustering behavior, that is a request back to `main`, not
something to do here.

---

## Goal

Raise matching precision *and* recall structurally, not by tuning
thresholds. Phase 0 §0.0's C0–C3 fixes (already on `main`) make the matcher
**see** the data; this branch makes it **reason** over the data. Splink
(Phase Q's Q.1) becomes one candidate engine inside this branch rather than
a branch of its own, because it depends on exactly the same
canonical-projection work.

## Resources for this branch

- Splink docs: https://moj-analytical-services.github.io/splink/
- Splink API reference (`Linker`, `cluster_pairwise_predictions_at_threshold`): https://moj-analytical-services.github.io/splink/linker.html
- `desbordante` (UCC/FD discovery for B4.1's Step 1): https://desbordante.readthedocs.io/ and https://github.com/Mstrutov/Desbordante
- Fellegi-Sunter model background (already implemented in this repo — reference for
  understanding, not for reimplementation): https://en.wikipedia.org/wiki/Record_linkage#Fellegi%E2%80%93Sunter_model
- Ditto entity-matching serialization paper (`COL/VAL` format referenced in B4.3),
  VLDB 2020 — "Deep Entity Matching with Pre-Trained Language Models":
  https://arxiv.org/abs/2004.00584
- Collective entity resolution background (B4.2), Bhattacharya & Getoor, TKDD 2007 —
  "Collective Entity Resolution in Relational Data": https://dl.acm.org/doi/10.1145/1217299.1217304
- Reference implementations to *read, not adopt* (B4.2): `nishadi/RELATER`
  (https://github.com/nishadi/RELATER), `CGCL-codes/HierGAT`
  (https://github.com/CGCL-codes/HierGAT)
- AnyMatch (evaluated, deliberately not adopted — see B4.3's ADR requirement):
  https://arxiv.org/abs/2405.04870

## Task breakdown

### B4.0 — Build the measurement that makes all of the above falsifiable (do this FIRST)

**The current eval cannot see any of this.** `tests/eval/golden.py`'s 560 pairs are
all Pagila *people* from **one** source with synthetic corruptions (typos, nickname
swaps, email reformatting). There is **no cross-origin company pair, no divergent
field names, no relational evidence** anywhere in it. Every scenario in B4 is
therefore unmeasurable against it — it would report "no regression" while the real
cross-origin behaviour changed completely.

Build a second golden set from the **real** origin1/origin2 data: sample candidate
company and person pairs across the two origins for Boeing, Airbus and Lockheed,
label them by hand (the three hand-merge baselines — `docs/diagnostics/*_APP_VS_MANUAL_DIAGNOSIS.md`
plus the memory notes on each subject's manual merge — already establish ground
truth). Keep the existing 560 pairs unchanged as the general-dedup regression guard.

**Without B4.0, none of B4.1–B4.4 can be honestly claimed.** Do it first.

### B4.1 — Identity-key discovery from measured data, then LLM classification

The instinct — *"find some fact column with help of LLM"* — is right, but the
LLM must be given **measurements**, not asked to guess from column names. Two steps:

**Step 1 (data, no LLM): profile each origin table.** This is the well-studied
*unique column combination* (UCC) and *approximate functional dependency* problem.
`desbordante` (`pip install desbordante`, C++ core with Python bindings) does exact
and approximate UCC discovery with the g1 metric, plus exact/approximate FDs. Per
column, compute:
- uniqueness ratio **within** each origin,
- **value overlap across origins** (after normalisation),
- null rate, cardinality, and a sample.

That single cross-tabulation separates the three cases the matcher currently
conflates:

| within-origin unique | cross-origin overlap | role | how it must be used |
|---|---|---|---|
| ~1.0 | high | **identity key** (website, ticker, tax id, registration no.) | settles a pair; also a **blocking key** (already fixed for the built-in matcher by `main`'s C2) |
| ~1.0 | ~0.0 | **surrogate key** (`companyID`) | `not_comparable` — never evidence (already fixed by `main`'s C0b) |
| < 1.0 | any | ordinary signal | normal Fellegi–Sunter comparison |

**Step 2 (LLM): classify and align, grounded in that profile.** Feed the model the
*measured profile* — name, type, cardinality, uniqueness, cross-origin overlap,
samples — and ask for two outputs: a role per column
(`identity_key | strong_signal | weak_signal | surrogate_key | noise`) and a
cross-origin alignment (`origin1.entityName ≡ origin2.name`,
`origin1.fullName ≡ legal_name`). Grounded in statistics this is a task LLMs do
well; asked cold from column names it is the guesswork that produced the original
43-column mess (`main`'s BUG A/B).

Cache per `(origin, table)` and persist into `matching_rules` — which already
exists in `MergeState` and in `profiles.PINNED_KEYS`, and which Part 1.6.A.4
(on `main`) made reachable from the UI. This is also what makes a re-run
reproducible. New code goes in `tools/identity_profile.py`.

### B4.2 — Relational / collective entity resolution (the multi-signal idea)

The people example — *"check name, then contact information, then education
and experience and location; do all of them to increase confidence"* — is exactly
**collective entity resolution in relational data** and **relational match
propagation** (see the resources above). It splits into two pieces, and the first is
nearly free:

**(a) Multi-attribute accumulation — already implemented, currently starved.**
Fellegi–Sunter *is* the "check name, then contact, then education" accumulator: match
weight is additive in log₂ space,
`M = log₂(λ/(1−λ)) + Σᵢ log₂(mᵢ/uᵢ)`, one term per attribute, with term-frequency
adjustment so a rare value counts more than a common one. `fellegi_sunter.py`'s
`MatchModel.score`/`.probability`/`.explain` already do this. **`main`'s C0 fix
already turns the existing model into the accumulator described** — no new
algorithm needed. Verify this first and measure it before building anything more.
(`fellegi_sunter.py` is outside your ownership — you consume its model, you don't
modify it; if it turns out to need a change, that's a request to `main`.)

**(b) Neighbourhood evidence — the genuinely new capability.** Use *related*
entities as comparison features:
- two `person` records whose resolved **employer cluster-ids** overlap are more
  likely the same person (from `Experience`);
- two `company` records sharing a resolved **CEO/founder** person cluster, or an
  overlapping **contact** or **location** cluster, are more likely the same company.

Implement as a **second pass**, not a rewrite: resolve entities in the existing
`extraction_order` (already topologically sorted), then re-resolve injecting resolved
neighbour cluster-ids as extra comparison fields. Iterate to a fixed point or a small
cap (2–3 passes). The infrastructure is already there — the spool holds per-entity
rows, `merge_engine_entity_map` holds resolved cluster ids, and `extraction_order`
gives the dependency order. New code goes in `tools/relational_evidence.py`.

**Risk to state plainly:** relational evidence is how collective ER produces
*cascading* over-merge — one wrong company merge propagates into its people. Every
pass must re-run the precision gate, and the neighbour feature must be capped in
weight so it can never alone push a pair over `auto_match`.

### B4.3 — Better LLM adjudication of the borderline band

`_judge_pairs_with_llm` (in your owned scoring/evidence half of
`resolve_entities.py`) already has the right *shape* — bounded to
`_MAX_LLM_PAIRS = 200`, chunked at 40, concurrency 4, degrading to heuristic scores
on failure. The weakness is its **inputs**: it receives stripped raw field dicts,
which for a cross-origin pair means the same 4-field intersection `main`'s C0 fixed
at the scoring layer but which the LLM adjudication step still needs to consume
correctly.
- Feed it the **canonical projection** (already on `main` via C0a) plus the
  neighbour evidence from B4.2(b), serialized in the `COL/VAL` form the
  entity-matching literature converged on (Ditto, VLDB 2020 — see resources above).
- Ask for **structured evidence, not a verdict alone** — which attributes agreed,
  which conflicted — so it lands in the reconciliation report and at the
  `human_review_matches` gate instead of being an unexplained number.

**AnyMatch — evaluated and deliberately NOT adopted (record as an ADR in
`docs/decisions/`).** A small fine-tuned entity-matching model reporting F1 87.5 vs
GPT-4-based MatchGPT's 87.4 with ~3 orders of magnitude fewer parameters. Genuinely
impressive, and wrong for this project *right now*, for four reasons:

1. **It optimises a component that is not the constraint.** LLM adjudication is
   capped at `_MAX_LLM_PAIRS = 200` per entity type and only sees the borderline
   band. It is not the cost, latency, or accuracy bottleneck — C0 was. Swapping the
   adjudicator model while it is fed 4-field intersections would have changed
   nothing (and C0 is already fixed on `main`).
2. **Its benchmark does not measure this problem.** Those F1 numbers come from
   standard EM benchmarks (Walmart–Amazon, Abt–Buy, DBLP–Scholar) — pairs of
   **already schema-aligned** records. The failure here was *unaligned* cross-origin
   schemas. The benchmark is silent on the thing that was broken.
3. **Real infrastructure cost.** There is no local model serving today; everything
   goes through one OpenAI-compatible endpoint. Adopting it means a torch/
   transformers dependency, a GPU or CPU inference path, a versioned model artifact,
   and it breaks the single `LLMClient` choke point B1's DSPy adapter is built on.
4. **The genuinely useful parts are free.** Take the Ditto-style `COL/VAL`
   serialization (above) without the model, and record the strategic finding — *a
   small local model can match GPT-4 on entity matching* — for when it matters.

**Revisit trigger (write this into the ADR):** if, after B4.0–B4.2, the borderline
band exceeds ~5,000 pairs per run **and** LLM cost or latency is a *measured*
constraint, re-evaluate AnyMatch or a comparable small local adjudicator.

### B4.4 — Splink as an optional engine (the original Phase Q Q.1)

Unchanged in intent, but it now lands **after** C0a (already on `main`)/B4.1,
because Splink's single hardest integration problem — invariant 6, the
cross-origin schema divergence — is precisely what the canonical projection
already solves. Splink then consumes the projection directly.

**Goal:** Splink as a per-entity **optional** matching engine, default off, at the
`resolve_entities_impl` seam.

**B4.4a — The seam, not a rewrite.** Add
`engine = (matching_rules or {}).get("engine", "builtin")` near the top of
`resolve_entities_impl`; if `"splink"` and Splink imports, delegate to
`tools/splink_matcher.py::resolve_with_splink(...)` and return early. Otherwise fall
through unchanged. **Do not rewrite `resolve_entities_impl`.**

**B4.4b — Reproduce the result-dict contract exactly** (`entity_type`, `clusters[]`
with `cluster_id`/`record_ids`/`match_confidence`/`required_human_review`,
`total_records`, `auto_matched`, `auto_excluded`, `llm_scored`, `uncompared_pairs`,
`candidate_pairs`, `blocks_purged`, `pending_merge_candidates`).

**B4.4c — Layer on the eight invariants Splink does not provide.** These are what the
hand-rolled matcher earns its keep with; a naïve swap breaks all of them. The full
list is in `docs/plans/PHASE_Q_PLAN.md` §Q.1 on `main` — read it and copy the
invariants list into your own notes before starting. The three that decide the
design:
- **Splink replaces *scoring*, not *clustering*.** Splink's
  `cluster_pairwise_predictions_at_threshold` is raw transitive closure — exactly
  what `_partition_component`/`_split_weakest_link` deliberately reject. Run Splink
  for pairwise probabilities only, then feed them into the **existing**
  `_build_clusters`. **You may call it, you must not modify it — see Ownership
  above.**
- **Deterministic cluster IDs.** Splink's own `cluster_id` must be translated
  through the existing `_assign_cluster_id`, which reuses a destination id when a
  member already maps to one, else `deterministic_uuid(entity_type,
  *sorted_member_ids)`. Skip this and every re-run duplicates every destination row.
- **Cross-origin schema divergence.** Splink assumes one unified column schema; our
  records have divergent field names (`company_name` vs `org_title`) because
  `map_fields` runs *after* resolution. Build a **projected comparison view** — a
  small uniform 4–6 column frame per record (`name`, `email`, `ref`, name parts)
  using the same hint logic as `_blockable_fields`/`_best_name_field` (both in your
  owned scoring half of `resolve_entities.py`).

**B4.4d — Any Splink failure logs a warning and falls back to builtin.** Never crash a
run over an optional engine.

**B4.4e — `matching_rules` is now reachable (Part 1.6.A.4, on `main`, fixed this).**
Add a per-entity `engine` field to the Advanced panel's matching-rules UI
(`PipelineScreen.tsx`'s advanced-matching disclosure — yours to edit) so
`engine: "splink"` is actually selectable, default `Built-in`.

## Acceptance gates

- `python -m tests.eval.score` shows **no precision regression** on the original 560
  pairs at *every* step (B4.1, B4.2, B4.3, B4.4 each gated separately). C0–C3 (on
  `main`) and B4.2 all make the matcher compare far more pairs and accumulate more
  evidence, so over-merge risk rises monotonically — this gate is the whole safety
  mechanism.
- On the **new cross-origin golden set** (B4.0), report precision/recall/F1 before
  and after each step. The cross-origin Boeing↔Boeing pair must resolve as a match.
- B4.2's neighbour evidence never alone pushes a pair over `auto_match` — asserted
  by unit test, not by inspection.
- Extend `tests/eval/score.py` with a scenario running `evaluate(splink_scorer,
  pairs)` over the **same 560 golden pairs**, printed beside the builtin.
  **Bar: Splink precision ≥ builtin's and recall ≥ 0.99.**
- Same Boeing merge run twice — `engine: builtin` and `engine: splink` — cluster
  counts and inserted/updated rows match within a stated tolerance.
- Every existing Phase-L matcher test stays green (the builtin is unchanged and
  still default).
- **If Splink does not beat the builtin, it ships as a documented, default-off
  option. That is an acceptable outcome, not a failure.**

---

## Cross-branch rules (verbatim — apply to every branch)

**R1 — One verification command.** `python scripts/check.py all` must pass before
every commit that leaves your machine. Server CI runs the identical command. If it
passes locally and fails in CI, that's a bug in `check.py` — fix it, don't work
around it.

**R2 — Ownership is exclusive.** You may read any file; you may modify only files in
your branch's Owns column. Needing a change outside it is a request to `main`, not
an edit. Ownership violations are the #1 source of six-way conflicts. **This is also
the CLAUDE.md "Branch discipline" rule — `git checkout` your branch and stay there.**
**This branch has an extra internal boundary: the scoring half of
`resolve_entities.py` is yours, the clustering half is frozen `main` territory even
within the same file — treat that split as strictly as the file-level ownership
table.**

**R3 — Extend through registries, never through shared function bodies.** New state
key → your own `state/<feature>.py` fragment plus one line in the `MergeState` base
list. New node → one row in `NODE_SPECS` (build it if it doesn't exist yet for your
owned files). New report section → one `REPORT_CONTRIBUTORS` registration (same
caveat — note B3 may build this first; check before duplicating). If you find
yourself editing a shared function body with no registry available, build the
minimal registry first rather than reaching into the shared body.

**R4 — Never edit `pyproject.toml` or `requirements.txt`.** Every dependency you need
was pre-declared as an extras group in Phase 0.1 (`dspy`, `splink`, `dq`, `lineage`,
`swarm`, `perf`). Splink is the `splink` extra, `desbordante` needs to be added if
not already covered by an extra — check first, request from `main` if missing.

**R5 — Rebase on `main` immediately after every merge-back**, before writing another
line. Serialized integration only works if everyone re-baselines at once.

**R6 — New features ship default-off.** Splink, the swarm planner, and any
performance strategy change land behind a config flag whose default reproduces
current behaviour (`engine: builtin` is your default). A branch that cannot be
turned off cannot be merged safely.

**R7 — Output contracts are frozen.** Prompt output keys, `resolve_entities_impl`'s
result dict, the six HITL interrupt payloads, and `MergeState`'s existing keys do not
change shape without a `main`-level decision recorded in `docs/decisions/`. The
frontend and `WorkflowManager` both depend on these and neither is your file.

**R8 — House style is mechanical.** One file per node; typed `MergeState`;
`logger.debug("ENTER …")` / `"EXIT …"` in every node; tests co-located in
`tests/unit/test_*.py`; golden evals in `tests/eval/`. `scripts/lint_arch.py`
enforces the parts that can be enforced — if it doesn't complain, you're consistent.

**R9 — Live verification per task, not per phase.** A checkbox goes green only after
the thing ran against the real app and DBs and an *independent* check confirmed it —
direct SQL, log inspection, a regenerated report. Never the app's own success
message. If it can't be verified live, leave it unchecked and say why.

**R10 — Report numbers, not adjectives.** "Faster", "better", "more accurate" are not
results. Every claim carries a before/after number measured against
`docs/verification/BASELINE_V1.md`.

**R11 — Every failure becomes a rule.** When you hit a real bug, open a `B5` item for
the hook/lint/doc line that would have prevented it. This is the ratchet; it is the
single highest-leverage habit in the whole plan.

---

## Where this fits

Integration order (from the full plan): **B5 → B3 → B1 → B4 → B2**. This
branch (B4) merges **fourth** — after B5, B3, and B1 land, before B2 (the
largest expected quality win of the five, per the full plan's honest scope
note). Rebase on `main` after each of B5, B3, and B1 land, per R5.

Full programme context, the other four branches' specs, and the honest
scope note about priority: `docs/plans/ONLY_PLAN_TO_IMPLEMENT.md` on `main`.
