# Merge-engine — technical report

**Date:** 2026-09-26
**Author:** Claude Opus 5 (diagnostic session)
**Status:** diagnosis and design. No code modified. CLAUDE.md and AGENTS.md
edited under explicit approval (see §4.4).

---

## Contents

- [§0 Evidence base and measured outcome](#0-evidence-base-and-measured-outcome)
- [PART 1 — Defects and fixes to apply now](#part-1--defects-and-fixes-to-apply-now)
  - [P1 Field-name tokeniser](#p1--field-name-tokeniser-cannot-split-lowercase-identifiers)
  - [P2 Comparison budget](#p2--the-comparison-budget-is-a-silent-truncation)
  - [P3 Join discovery](#p3--join-inference-targets-the-surrogate-key)
  - [P4 Dimension materialisation](#p4--eight-of-sixteen-entities-produced-zero-field-mappings)
  - [P5 Blocking and closure](#p5--blocking-is-not-selective-and-closure-amplifies-it) — incl. §5.4 blocking bias
  - [P6 Parent/subsidiary](#p6--no-parentsubsidiary-discrimination)
  - [P7 Relationship derivation](#p7--no-relationship-derivation-from-the-origins)
  - [P8 LLM adjudication](#p8--llm-adjudication-spends-its-budget-on-the-wrong-pairs)
  - [P9 Decision memory](#p9--no-cross-run-memory-of-human-decisions)
  - [P10 Quality gate (part 1 of 2)](#p10--no-stage-can-fail-a-run-on-quality-grounds)
  - [P11 map_fields concurrency](#p11--map_fields-is-44--of-runtime-run-serially)
  - [P12 Metadata exclusion](#p12--catalog-tables-and-metadata-columns-are-analysed-and-mapped)
  - [Ordering and dependencies](#part-1-ordering-and-dependencies)
- [PART 2 — Research, cited](#part-2--research-cited) — §2.1 blocking survey ·
  §2.2 semantic graph blocking · §2.3 JEV · §2.4 Zingg · §2.5 library comparison ·
  §2.6 key selection · §2.7 embeddings · §2.8 ReAct · §2.9 orchestration ·
  **§2.10 blocking + architecture survey** · **§2.11 ERBlox / matching dependencies**
- [PART 3 — Architecture](#part-3--architecture)
- [PART 4 — Appendices](#part-4--appendices)
- [References](#references)

---

## §0 Evidence base and measured outcome

### 0.1 The run

One full autonomous run over two PostgreSQL origins into `global_warehouse`,
scored against the hand-built `ground_truth` database.

| | |
|---|---|
| wall clock | 828.1 s |
| peak RSS | 0.9 GB (cap 6.5 GB, box has 27 GB and **no swap**) |
| terminal status | `completed` |
| gates traversed | `deps` 3.2 s → `mappings` 367.2 s → `matches` 808.4 s → `final_approval` 814.9 s |
| gate skipped | `human_review_scope` — *"This merge targets no single organisation, so there was no scope to confirm"* (correct) |
| LLM | `Qwen/Qwen3.6-35B-A3B-FP8` via llama.cpp, 1.4 s/call measured through the app's own `LLMClient`, JSON mode verified, 262 144 context |
| tracing | Langfuse v4 in `events_only` mode; ingestion confirmed live |

### 0.2 Reproducibility caveat — stated plainly

Since the run, `ground_truth` has been **dropped**, `global_warehouse` has been
**wiped** (`merge_engine_entity_map` no longer exists), and the `enrichment`
origin credentials have **changed**. Nothing below was re-verified against live
databases after those changes. Every finding rests on artifacts captured during
the run and still on disk:

| artifact | what it proves |
|---|---|
| `phase3/state.json` | `field_mappings`, `unmapped_fields`, `resolution_stats`, `timings`, `entity_tables` |
| `phase3/gates.jsonl` | every gate payload exactly as a human would have seen it |
| `score.json` (1.0 MB) | every TP/FP/FN pair, named |
| `gt_score.py`, `gt_rows.py`, `run_merge.py` | harnesses, re-runnable once databases are restored |

Where a claim could not be re-verified after the environment changed, it is
marked **[unverified post-wipe]**.

The scoring harness was written **before** the run and proven in both
directions — identity scoring returns a perfect result; injected FP and FN are
caught and named:

```
identity     → company TP 13 FP 0 FN 0 | person TP 7 FP 0 FN 0 | 0 unmapped
inject 1 FP  → FP 1, exact pair named
inject 1 FN  → FN 1, exact pair named
```

### 0.3 Measured outcome

| entity | TP | FP | FN | verdict |
|---|---|---|---|---|
| company | 13 / 13 | 4 | 0 | recall 1.000, precision 0.765 |
| person | 3 / 7 | **14 382** | 4 | unusable |

| | value |
|---|---|
| rows short of expected | **10 847** |
| `company.name` NULL | **78 / 99** |
| `legal_name`, `industry_id`, `location_id`, `employee_count` | **100 % NULL** |
| entities producing zero field mappings | **8 of 16** |
| candidate pairs discarded unexamined | **9 556 382** |
| over-merged person clusters | 1 401 |
| **run status** | **`completed`** |

The last row is the most important number in this report. The pipeline
destroyed most of its own output and reported success.

### 0.4 Ground-truth scale, and why rates are the wrong unit

`merge_engine_entity_map` held 13 712 records; only **18 clusters** had more
than one member.

| entity | multi-member clusters | true pairs | composition |
|---|---|---|---|
| company | 13 × size-2 | **13** | every one cross-source |
| person | 4 × size-2, 1 × size-3 | **7** | 5 within-source, 2 cross-source |

Person pair space is C(13 518, 2) = **91 355 403** with 7 positives —
prevalence 7.7 × 10⁻⁸.

With 7 positives, recall moves in steps of 1/7 = 14.3 pp and a Wilson 95 %
interval on 7/7 spans roughly [0.65, 1.00]. **Report confusion counts, not
rates**, and stratify cross-source vs within-source — 13 of the 15 cross-source
positives are companies, so an aggregate figure hides cross-source collapse.

`Fβ` with β = 0.5 (precision weighted 2×, because a false merge is written to
the destination while a false split leaves a visible duplicate) remains correct
as a **tiebreaker between two configurations**, not as an optimisation target.
Tuning a threshold against 20 labelled pairs overfits immediately. Use the
floors that `splink_workbench.evaluate` already accepts (`precision_floor`,
`recall_floor`) instead.

**Report review rate beside every precision figure.** Precision is trivially
gameable by widening the review band; a precision of 0.99 can mean "everything
hard was deferred to a human."

### 0.5 The unifying finding

**The matcher is not naive. It is starved.**

`resolve_entities.py` contains four well-built defences against precisely the
failures this run produced. **Every one is keyed on a field the empty join
graph prevents from arriving.**

| defence | keyed on | fired? | why not |
|---|---|---|---|
| `_UNIQUE_IDENTITY_HINTS` — different non-null email ⇒ different entity | `email` | **no** | email is in `contact_information` (3 781 rows), never joined to `staff` |
| `_DETERMINISTIC_ID_HINTS` — matching domain/ticker settles identity | domain, ticker | **no** | not present on `staff` |
| `_shared_neighbors_beyond_common_parent` — siblings under one parent are not the same entity | `company_relation` rows | **no** | no relation table in either origin ⇒ `relationship_index` empty |
| term-frequency down-weighting of accidental agreement | value distribution | partial | with ~4 usable fields it cannot carry the decision alone |

The parent/subsidiary guard that would have blocked **all four** company false
merges is inert for this reason alone. Its docstring even cites the Airbus
"Defence and Space" failure it was written to prevent.

**Consequence for prioritisation:** P3 is not one repair among twelve. It is the
precondition that activates four existing defences simultaneously. Most of the
algorithmic work needed is already written and never executes.

---

# PART 1 — Defects and fixes to apply now

All twelve are defects in existing code, fixable without changing the
architecture. Ordered by impact on result quality.

---

## P1 — Field-name tokeniser cannot split lowercase identifiers

**Severity: critical. Blast radius: every multi-origin field on every
PostgreSQL origin.**

### Evidence

```
_tokenize_field('companyName')  -> ['company', 'name']  -> _safe_coalesce_target = True
_tokenize_field('companyname')  -> ['companyname']      -> _safe_coalesce_target = False
_tokenize_field('company_name') -> ['company', 'name']  -> _safe_coalesce_target = True
_tokenize_field('entityname')   -> ['entityname']       -> _safe_coalesce_target = False
_tokenize_field('companyemployees') -> ['companyemployees'] -> False
```

`map_fields._safe_coalesce_target(field, dest_col)` requires
`len(field_tokens) == len(dest_tokens) + 1`, then checks the single extra token
against `_SAFE_COALESCE_QUALIFIERS`. **That allowlist already contains
`"company"`** — the design is correct and deliberately conservative (its
comment explains that meaning-changing qualifiers like `full`, `legal`, `ceo`
are excluded on purpose, having once corrupted a real cross-origin Boeing pair).
It never gets the chance to run.

### Mechanism

1. `diffbot.entityname → company.name` was assigned at confidence 0.80;
   `company.name` marked **taken**.
2. `zoominfo.companyname` reached for the same column, found it taken, and fell
   to the coalesce check — the mechanism built for exactly this case.
3. The length test failed. `companyname` left unmapped.

### Measured cost

```
diffbot    22 records -> 22 named
zoominfo   93 records -> 15 named   (only those clustered with a diffbot record)
```

Every company name in the warehouse came from diffbot. **Zero came from
zoominfo.** `is_coalesced` is `false` on all ten company mappings. The same
failure nulls `employee_count` and `legal_name`.

### Why it is the worst defect in the system

The data was **present in the origin and correct**. The pipeline discarded it
with no warning and reported success. A matching error gives you a wrong answer
you can find; this gives you a missing answer you cannot.

PostgreSQL folds unquoted identifiers to lowercase (SQL:2011 / PostgreSQL
identifier rules), so *any* PostgreSQL origin defeats the mechanism. The
docstring's worked example is `entityName` — camelCase — which is what was
tested against.

### Fix

Longest-match segmentation against a **closed vocabulary**, assembled from:

1. destination column names and their tokens
2. destination table names
3. `_SAFE_COALESCE_QUALIFIERS`
4. origin table names

Accept a split **only when every produced segment is in the vocabulary**.
`companyname` → `company|name` (both known) succeeds. An unknown concatenation
stays a single token and fails closed exactly as today, preserving the existing
conservative posture. Prefer the split with the fewest segments; on a tie,
prefer the one whose segments have the highest vocabulary frequency.

Add a regression test per origin-style: camelCase, snake_case, lowercase
concatenation, SCREAMING_SNAKE.

**Quality impact: very high. Speed impact: none.**

---

## P2 — The comparison budget is a silent truncation

**Severity: critical. Class: silent failure.**

### Evidence

`resolve_entities.py:59` — `_MAX_COMPARISONS = 250_000`. From
`resolution_stats`:

| entity | records | candidate pairs | **uncompared** |
|---|---|---|---|
| experience | 13 518 | 250 000 (capped) | **8 792 591** |
| staff | 13 518 | 250 000 (capped) | **711 670** |
| product | 1 592 | 250 000 (capped) | 52 121 |

Three entities bound at the cap exactly. For `staff`, blocking generated
≈962 k pairs and **74 % were discarded without being compared**.

`uncompared_pairs` reported the loss honestly. **Nothing consumes it.**

### Why this loss is uniquely bad

Recall lost here is unrecoverable by any downstream mechanism — no threshold
change, no better model, no reviewer can recover a pair that was never scored.
In the survey's terms this is a collapse in **Pair Completeness** with no
compensating gain, since the 250 000 retained pairs are an arbitrary prefix
rather than the most promising ones [R1 §2].

### Your objection, and the resolved answer

You observed correctly that a fixed cap is wrong **and** comparing everything is
wrong. The literature resolves this. Three formal measures [R1 §2]:

```
Pair Completeness   PC(B) = |D(B)| / |D(E)|        ∈ [0,1]   (recall)
Pairs Quality       PQ(B) = |D(B)| / ‖B‖           ∈ [0,1]   (precision)
Reduction Ratio     RR(B,E) = 1 − ‖B‖ / ‖E‖        ∈ [0,1]   (efficiency)
```

Blocking is defined as clustering such that **PC, PQ and RR are simultaneously
maximised** [R1 Definition 3]. A global cap maximises RR by destroying PC —
the one trade the definition forbids.

The survey notes PC is an *optimistic* estimate of recall (it presumes an
oracle) while PQ is *pessimistic* (repeated comparisons between duplicates
count as false positives) [R1 §2] — worth knowing when reading any published
figure.

### Fix — replace the cap with a three-stage workflow

The survey's canonical structure is **Block Building → Block Cleaning →
Comparison Cleaning** [R1 §3–4, Fig. 1(b)].

**Stage 1 — Block Building: multi-pass disjunctive keys.** See P5.

**Stage 2 — Block Cleaning: purge and filter by size, not by global total.**

> *"the larger a block is, the less likely it is to contain unique duplicates,
> i.e., matches that share no other block. Such large blocks are typically
> produced by lazy schema-agnostic techniques and correspond to stop words"*
> — [R1 §4.1]

- **Block Purging** discards blocks exceeding a cardinality or size limit
  [R1 §4.1].
- **Block Filtering** applies the same logic per entity: *"removing every
  entity from the largest blocks that contain it … it retains every entity in
  r % of its smallest blocks"* [R1 §4.1].
- Recommended combination and parameter: *"Block Filtering is typically applied
  after Block Purging by lowering r to 50 % instead of 80 %, which is the best
  configuration when applied independently"* [R1 §4.1].

The empirical result that answers your question directly:

> *"both Block Filtering and Block Purging boost PQ and RR by orders of
> magnitude, while exhibiting a low computational cost and a negligible impact
> on PC"* — [R1 §4.1]

**Orders of magnitude fewer comparisons, negligible recall loss.** That is the
requirement, met.

**Stage 3 — Comparison Cleaning: weight and prune the surviving pairs.**

Weighting schemes, verbatim [R1 §4.2]:

| scheme | definition |
|---|---|
| **ARCS** | *"sums the inverse cardinalities of the common blocks, giving higher weights to entity pairs that co-occur in smaller blocks"* |
| CBS | counts blocks shared by two entities |
| ECBS | CBS, discounting entities placed in many blocks |
| JS | Jaccard coefficient of the two block lists |
| EJS | JS, discounting entities in many non-redundant comparisons |
| Pearson χ² | tests whether two entities appear independently in blocks |

**ARCS is tailor-made for this failure.** A pair agreeing only inside a
60-member surname block scores low; a pair agreeing inside a 2-member email
block scores high. Reported pruning efficacy: Graph Partitioning *"prunes 50 %
more comparisons for practically no impact on recall"*, and Reciprocal Pruning
*"retain[s] only the edges that are important for both adjacent entities"*
[R6].

### The ordering constraint — getting this wrong makes things worse

Block Cleaning's safety rests on the collection being **redundancy-positive**:
entities appear in multiple blocks, so a true pair lost from an oversized block
survives in a smaller one. The survey is explicit that Comparison Cleaning
*"applied … to a redundancy-positive block collection deprives it from its
co-occurrence patterns and renders all other techniques inapplicable"* — i.e.
at most one Comparison Cleaning method per workflow [R1 §4.2].

**merge-engine is effectively single-key for persons today. Purging alone would
lose recall.** Multi-pass (P5) **must** land before purging (P2 stage 2).

### Concrete change

1. Remove `_MAX_COMPARISONS` as a truncation. Move the budget to configuration.
2. If, after Cleaning, candidate volume still exceeds the budget, that is a
   **reportable failure of blocking selectivity** — surface it and fail the
   quality gate (P10). Never silently compare a prefix.
3. Record per-entity PC-proxy, PQ-proxy and RR in `resolution_stats`.

**Quality impact: high. Speed impact: neutral-to-faster once P5 lands.**

---

## P3 — Join inference targets the surrogate key

**Severity: critical. Precondition for four dormant defences.**

### Evidence

`join_graph._find_candidate_edges` (line 266) proposes
`child.<parent>id → parent.<primary key>`, taking the parent column from
`_resolve_primary_key()`. In these origins the PK is the surrogate `_id`; the
join key is the business key.

| candidate | proposed edge | outcome |
|---|---|---|
| `companyexperience.companyid` | → `company._id` | proposed, then **correctly rejected** — `EiZfZ…` values cannot be contained in `6ab0e75c…` oids |
| `staff.main_entity_id` | needs a table named `main_entity` | never proposed |
| `companyexperience.personid` | needs a table named `person` | never proposed |

Result: `dependency_tree = {}` and `column_dependencies = []` from 26 tables.
Zero declared foreign keys exist in either origin (0 FK constraints, 11 and 15
PK constraints respectively), so inference was the only path.

The join is real: **`staff.main_entity_id = company.companyid` joins all
13 518 rows.**

The inclusion-dependency check is functioning correctly. It is fed the wrong
parent column.

### Fix — value-based inclusion-dependency discovery

1. **Profile every column**: distinct count, null ratio, min/max length,
   inferred type, value-length distribution. (This profile is reused by P5 for
   key selection and by P12 for metadata detection — one pass, three consumers.)
2. **Generate candidates** from *all* column pairs with compatible type and a
   plausible cardinality ratio — keep the name heuristic as a *ranker* to order
   the search, never as the gate.
3. **Test containment** on a sample: accept an edge when ≥ 95 % of sampled child
   values appear in the parent column, recording the measured ratio as
   confidence. Cast-compatible comparison (`main_entity_id::text` vs
   `companyid`) must be attempted, since the type mismatch here is incidental.
4. **Stop using `_resolve_primary_key()` as the parent column.** Test every
   column of the candidate parent.

This finds `staff.main_entity_id ⊆ company.companyid` at 100 % immediately and
does not care what either column is named.

This is *Semantic Graph Blocking*'s prerequisite too: that method is *"based
exclusively on the relations between entities, including foreign keys in a
database"* [R2], so the edges must exist before the technique is available.

**Quality impact: very high. Speed impact: one profiling pass, amortised.**

---

## P4 — Eight of sixteen entities produced zero field mappings

**Severity: critical.**

### Evidence

```
field_mappings counts:
  company 10   product 10   contract 8   location 7
  entity   4   experience 4   staff   4   contact_information 2
  schema   0   company_experience 0   company_industry 0
  company_investment_round 0   event 0   evidence 0
  institution 0   source_profile 0
```

Half the extracted entities had **no path into the destination at all**. And
`staff` — source of all 13 591 expected `person` rows — produced **four**
mappings for a nine-column destination table.

### Root cause (a): no dimension-table materialisation

Filling `company.industry_id` requires **three** operations:

1. read the industry **label** (from `zoominfo.industry`, via `classificationid`)
2. **create** a row in destination `industry` and obtain its generated `id`
3. write that generated id into `company.industry_id`, for the company found by
   joining `companyindustry.companyid → company`

The mapper's entire vocabulary is two statements: *"source column X →
destination column Y"* or *"skip X"*. **None of the three is expressible.**

So it maps labels **into FK columns**:

| source | mapped to | confidence |
|---|---|---|
| `products.category` | `product.category_id` | 0.90 |
| `products.industry_name` | `company.industry_id` | 0.90 |
| `products.manufacturer_name` | `company.name` | 0.90 |

A category *name* in a `category_id` column populates nothing. Result:
`industry` **0 of 41**, `product_category` **0 of 388**.

### Root cause (b): junction tables have no destination column

`company_industry` bundles three source tables of two shapes —
`diffbot.companyindustry` and `zoominfo.companyindustry` (junction: two ids)
plus `zoominfo.industry` (dimension). 544 records, zero mappings. Recorded
reasons:

```
companyid        -> "No matching destination column found; destination schema is authoritative"
classificationid -> "No matching destination column found; destination schema is authoritative"
main_entity_id   -> "LLM proposed skipping: duplicate of companyid"
```

**The mapper's judgment is correct, not buggy.** `classificationid`'s *value*
never appears anywhere in the destination — the destination stores its **own
generated** `industry.id`. A junction table means an **edge**, and edges are
expressed as a dimension row plus an FK, which the mapper cannot emit.

### Fix

Add a third mapping outcome beyond `{column, skip}`: **`dimension`**.

```
{ kind: "dimension",
  source_field: "category",
  dimension_table: "product_category",
  dimension_label_column: "name",
  referencing_table: "product",
  referencing_fk: "category_id" }
```

`load_data` then, deterministically and with no LLM:
1. upsert the dimension row, deduplicating on the **normalised** label
2. resolve the FK on the referencing row

Handle junction tables explicitly: once P3 works, a table whose columns are
*all* foreign keys is an **edge table**; it maps to an FK assignment on a
parent, never to a destination table of its own.

**This depends on P3** for step 3's join.

The mapper already emits the right signal — `category → product.category_id`
names both the label and the FK. Only the *outcome vocabulary* is missing.

**Quality impact: very high. Speed impact: none — replaces LLM calls with
deterministic upserts.**

---

## P5 — Blocking is not selective, and closure amplifies it

**Severity: critical for `person`.**

### Evidence

The largest predicted person cluster fused **42 distinct people whose only
shared attribute is the surname "Kumar"** — Amanda, Satheesh, Christian,
Hemalatha, Vijesh, Pavan, Amritesh, Varun, Sandeep, Prem, Anand, Sanjay and
others — across five different `main_entity_id` values (five employers).

13 518 records → 10 092 clusters: **3 426 records absorbed into someone else's
identity.** 14 382 false pairs. 1 401 over-merged clusters.

`staff` offers only `firstname`, `lastname`, `middleinitial`, `name`,
`personid`, `_version`, `main_entity_id`.

### 5.1 Multi-pass disjunctive blocking (approved; selectable, not fixed)

**The governing property.** In a disjunctive scheme a pair is a candidate if
**any** pass agrees, so recall is the **union** across passes while cost is the
**sum**. Zingg states the trade explicitly: use keys *"as a union rather than an
intersection"* — *"more comparisons, but fewer missed matches"* [R4].

**This dissolves your objection about people common to two companies.** The
employer pass does not need to catch them; the **email pass** does. No single
pass must be complete. Zingg makes the same point about incomplete keys: if
surname is 15 % missing, *"that field alone shouldn't gate all comparisons"* —
the model learns to *"route around"* gaps by weighting alternatives [R4].

**Your diagnosis, restated precisely: the problem was never that surname
blocking is too big. It is that surname was used *alone*.** The fix is
**conjunctive terms within a pass, disjunctive across passes**:

| pass | key | catches | expected block size |
|---|---|---|---|
| A | exact normalised email | GT's 4 email-identity pairs, incl. job-changers | 1–2 |
| B | employer + lastname | same-employer duplicates (**your idea**) | small |
| C | lastname + firstname initial | **separates Amanda Kumar from Satheesh Kumar** | small |
| D | exact normalised phone | cross-employer, cross-origin | 1–2 |

**Pass C alone would have prevented most of the 14 382 false pairs at the
blocking stage** — no scoring, no threshold, no LLM call.

**Employer alone is insufficient.** One `main_entity_id` in this corpus carried
a large roster; C(3 000, 2) = 4 499 500 from one company. Employer must be
conjoined with a name term.

**Your idea is published, with numbers.** *Semantic Graph Blocking* blocks on
inter-object relationships — exactly "same employer". Measured on the Citeseer
reference database, Weak RL, partition Par3 [R2]:

| method | pairs generated | true positives | recall | precision |
|---|---|---|---|---|
| Standard Blocking | 1 669 | 21 | 55.3 % | 1.3 % |
| Sorted Neighborhood | 480 | 20 | 52.6 % | 4.2 % |
| **Semantic Graph Blocking** | **32** | **22** | **57.9 %** | **68.8 %** |

**52× fewer pairs and one more true positive than Standard Blocking**, with
false positives reduced *"by two orders of magnitude"* in optimal cases, and
requiring **one pass** where SB/SN need several [R2]. Strict RL on Par1: SGB 14
true matches, 31.6 % recall, 87.5 % precision. The authors recommend SGB
**whenever relationship data exists** — which, after P3 and P7, it will.

**The technique has a formal name and a formalism.** ERBlox calls it
**MD-based collective blocking** and states the underlying problem exactly
[R15 §1]:

> *"similarity between blocking key values alone may fail to capture the
> relationships that naturally hold in the data and could be used for blocking.
> Thus, entity blocking based only on similarities of blocking key values may
> cause low recall. **This is a major drawback of traditional blocking
> techniques.**"*

Its definition of collective blocking is precisely your employer idea
generalised: *"Blocking can be performed on each of the participating entities,
and the way records for an entity, say Author, are placed in blocks may
influence the way the records for another entity, say Paper, are assigned to
blocks"* [R15 §1].

### Conflicting evidence on cost — stated rather than cherry-picked

SGB reports **52× fewer** pairs [R2]. **ERBlox reports the opposite
direction** [R15 §8]:

> *"MD-based collective blocking has lower reduction ratio than standard
> MD-based blocking, which may lead to better ER results, but may impact
> computational cost: larger blocks may be produced, and then, more candidate
> duplicate record-pairs become inputs for the classifier. In blocking, this is
> a common tradeoff that needs to be considered."*

Both are relational blocking, and they disagree because they **compose
differently**:

| | composition | effect on candidate count |
|---|---|---|
| SGB [R2] | relation-only blocking **replacing** key blocking | **fewer**, higher-precision candidates |
| ERBlox [R15] | relational MD conditions **added on top of** key blocking | **more** candidates, higher recall |

**Recommended composition for merge-engine: ERBlox's — additive.** Reasoning:
the passes in the table above are already narrow (each conjoins two terms), so
adding the employer pass as pass B of a disjunction raises recall without
producing large blocks, and Block Cleaning (P2 stage 2) absorbs any that do
appear. Replacing key blocking entirely, SGB-style, would discard the email and
phone passes — the two highest-selectivity keys available, and the ones that
carry GT's documented email-identity pairs. ERBlox's own verdict supports the
additive form: *"the quality of MD-based collective blocking dominates standard
blocking, both in its key-based and MD-based forms, for the three datasets"*
[R15 §8].

Consequence to plan for: the additive form **increases** candidate volume before
Cleaning reduces it. That is another reason the ordering constraint below is not
optional.

### Cascaded blocking — a refinement worth adding

Beyond the ensemble (union) form, [R14 §10.1] names a second hybrid:

> *"**Ensemble blocking:** Union of candidate sets from multiple blockers
> (improves recall). **Cascaded blocking:** Apply fast, coarse blocker first,
> then refine with more expensive method (balances speed and quality)."*

Applied here: run the four cheap exact/conjunctive passes first, then apply an
expensive refinement (trigram similarity via `pg_trgm`, already half-built
behind `matching.trigram_blocking_enabled`) **only to the survivors** rather
than to all 13 518 records. That is how the expensive key becomes affordable,
and it pairs naturally with the additive composition above.

**Key selection — profile-driven, human-approved, per entity type.** The
published formula [R5]: each attribute gets a **discriminability** score
(distinct values ÷ all values) and a **coverage** score (portion of entities
carrying it); the key score is the **harmonic mean** of the two. Candidates
above a threshold are proposed; a human approves the final set.

**Profile alone would not have saved this run — this matters.** `lastname`
scores *well*: coverage ≈ 1.0, discriminability perhaps 0.6, harmonic mean
≈ 0.74. It failed because individual **values** vary wildly in frequency, and
"Kumar" behaves like a stop word — precisely the condition Block Purging exists
for [R1 §4.1]. **Attribute-level selection picks the key; value-level purging
handles bad values inside a good key. Neither alone suffices.**

Configuration lives in `matching_rules`, keyed by **entity type**, because after
unification both origins' records occupy one pool and a key must be evaluable
across origins. Per-origin *column aliasing* is supported (diffbot's `entityname`
and zoominfo's `companyname` both feed the logical `name` term), but the key
itself is defined on the logical entity.

### 5.2 Cap block size, not global comparisons (approved)

**Worked example.** Surname "Kumar" yields a block of ~60 staff → C(60,2) =
1 770 comparisons from one key value. Today the *global* total is capped at
250 000 and truncated arbitrarily. Instead: if any single key **value** yields a
block larger than N (start at 50, tune per entity), that key is
non-discriminative *for that value* — purge the block and **report it**.

`_MAX_REF_BLOCK = 25` already implements exactly this for reference blocks; it
simply is not applied to the general case. The comment there —
*"A ref block bigger than this is a non-discriminative key … skip it rather
than flood comparisons"* — is the right instinct, unextended.

The difference in kind: a purged oversized block is a **named, diagnosable
condition** ("surname is not selective enough for value X"); global truncation
is silent and arbitrary. See P2 stage 2 for the parameters (`r = 50 %` after
purging) and the redundancy-positive precondition.

### 5.3 Bound transitive closure (approved)

**Worked example from this run.** GT holds
`diffbot:EWapPjU…(Thales)` + `zoominfo:143048837(Thales)` as a true pair. The
app also matched `diffbot:EuJ2H1…(Thales Alenia Space France)` to both, so
closure fused **three** records into one cluster — turning one correct pair into
three pairs, two of them wrong. That is the amplification mechanism: one bad
edge does not add one error, it adds an error per existing member.

Three controls:

1. **Higher bar for closure than for a pairwise match.** A pair may match at
   0.95; extending an existing cluster should require more, or corroboration
   from a second blocking pass (ARCS weight ≥ threshold).
2. **Cluster-size ceiling.** Any cluster exceeding it goes to review rather than
   forming silently. A 42-member person cluster should be impossible to create
   unattended.
3. **Prefer a graph-clustering step with a stopping criterion** over naive
   connected components. Transitive LSH does this — converting blocks into a
   blocking graph and applying community detection, finishing *"when the size of
   the largest cluster is lower than a predetermined"* bound [R1 §4.2]. JEV's
   use of Weakly Connected Components is safe only because its edges carry
   precision 0.916 [R3]; at merge-engine's current person precision, WCC is
   what produced the 42-member cluster.

### 5.4 Blocking on surname is a fairness problem, not only a quality one

**This is the one finding in the report that is not about correctness.**

[R14 §10.4] lists among the open challenges of blocking:

> *"**Fairness and bias:** Blocking may introduce demographic biases if keys
> correlate with protected attributes."*

Surname is exactly such a key. Surname frequency distributions differ sharply by
ethnic and national origin — a handful of surnames cover a very large share of
some populations and a small share of others. So a surname-keyed block is not
merely "sometimes too large"; it is **systematically larger for some
populations than others**, and the consequences are unequal:

- populations with high surname concentration are **over-merged** — distinct
  people collapsed into one identity
- populations with dispersed surnames are **under-merged** — genuine duplicates
  left separate

The 42-member "Kumar" cluster is the visible instance. The same key would have
behaved very differently on a set of 13 518 records with a flatter surname
distribution, which means **the error rate this pipeline produces depends on
whose records are being merged.** In a warehouse used for decisions about
people or companies, that is a defect of a different kind from a missing NULL.

Two things follow, and neither is expensive:

1. **The block-size cap (P5.2) is also the bias control.** Purging oversized
   blocks bounds the worst case for the most-affected population, not just the
   average case. This is an argument for setting the cap on measured
   distribution rather than on a round number.
2. **Report block-size distribution per key, not just the maximum**, and check
   it for skew. Zingg's `verifyBlocking` diagnostic — *"block size counts"* and
   *"top 10 % of records by block size"* with sample inspection [R4] — is
   exactly the right instrument, and the pipeline currently reports none of it.

A high-selectivity pass (email, phone) mitigates this too, because it does not
depend on name distribution at all. That is a second reason to prefer the
additive composition in §5.1 over replacing key blocking.

**Quality impact: high. Speed impact: faster** — selective keys shrink the pair
space, which is what makes raising the P2 budget affordable.

---

## P6 — No parent/subsidiary discrimination

**Severity: high. All four company false merges.**

### Evidence

| app merged | ground-truth verdict |
|---|---|
| diffbot `Thales Alenia Space France` + zoominfo `Thales` | **"NEVER-merge rule"** |
| diffbot `Thales Alenia Space France` + diffbot `Thales` | rejected — subsidiary vs group |
| diffbot `SPEEDCAST FRANCE SPEEDCAST` + zoominfo `Speedcast` | `held_split` — parent ≠ subsidiary |
| diffbot `Intelsat Global Service` + zoominfo `Intelsat US` | parent/subsidiary |

**All four are documented GT hard negatives, and all four are one failure mode.**

**Aggravating factor:** `resolution_stats["company"]["llm_scored"] = 0`. All 17
company merges fired at ≥ 0.95 auto-match. The heuristic was not uncertain — it
was **confidently wrong**, so adjudication never saw them. Raising the threshold
would not have helped; these scored above any plausible bar.

### The formalism these rules belong to — matching dependencies

The `never_merge` / `always_merge` tables below, and the learned
parent/subsidiary signal, are an implementation of a published formalism rather
than an invention. **Matching dependencies (MDs)** are, verbatim:

> *"declarative logical rules that tell us under what conditions of similarity
> between attribute values, any two records must have certain attribute values
> merged (or matched), i.e. made identical."* — [R15 §1]

ERBlox uses MDs at **both** ends of the pipeline — *"Use of MDs for supporting
the blocking phase"* and *"MD-based duplicate merging … when and how to merge is
specified by matching dependencies, which invoke matching functions"* [R15
abstract, §1] — enforced declaratively in LogiQL, a Datalog extension.

Three consequences for the design:

1. **It settles the pair-level-vs-generalised question cleanly.** An MD *is* the
   generalised rule: a similarity condition over attribute values, not a pair of
   record ids. So the two forms are not rival designs — the pair-level row is a
   fully-instantiated MD, and the proposed general rule is the same MD with its
   constants relaxed to a similarity predicate. Store both in one table with a
   `scope` discriminator (`pair` | `predicate`) rather than two tables.
2. **The same rule store can drive blocking, not just merging.** Once an MD
   expresses "records of these two companies are related but never identical",
   it is usable as a *negative* blocking condition too — the pair need never be
   generated. That is strictly cheaper than generating and then rejecting it.
3. **Do not adopt Datalog/LogiQL.** ERBlox's contribution is the formalism; its
   engine choice is incidental and adding a Datalog runtime here would be a
   large dependency for a small rule set. SQL predicates evaluated in
   `resolve_entities` are sufficient at this rule count, and the formalism is
   what carries the value.

### Fix — learned by default, human configuration auxiliary

**Learned signal, computed from the origins (primary path).** Three
deterministic features, no model required:

1. **Legal-form / region suffix asymmetry.** Normalise both names; detect
   whether one is the other plus a jurisdiction or legal token (`France`, `US`,
   `SAS`, `Berhad`, `Global`, `Group`, `Holdings`, `Berhad`). **Asymmetric
   containment with a region or legal qualifier is evidence of
   parent/subsidiary — the opposite of evidence of identity.**
   This is the exact mirror of `_SAFE_COALESCE_QUALIFIERS` (P1): that allowlist
   deliberately excludes meaning-changing qualifiers, and the same taxonomy
   drives this signal. One shared vocabulary, two consumers.
2. **Domain disagreement under name containment.** `thalesaleniaspace.com` vs
   `thalesgroup.com`; `optus.com.au` vs `singtel.com`. GT's own reasoning cites
   precisely this. Different registrable domain (eTLD+1) plus name containment
   ⇒ related, not same.
3. **Derived relationship edges** (P7) feeding the existing
   `_shared_neighbors_beyond_common_parent` guard.

**Human configuration, auxiliary, read from tables not files.** Look for an
operator-maintained relation table by convention, treat absence as **normal**,
and let its rows override the learned signal when present.

**Output shape — directional hierarchy in `company_relation` (approved).**

```
company_a_id = parent, company_b_id = child, relation_type = 'subsidiary'
```

**Both companies remain separate rows in `company`.** GT does exactly this — its
102 company clusters keep Thales and Thales Alenia Space France distinct, which
is why its `company` table holds 114 rows (102 clusters + 12 stubs). Neither
becomes an attribute of the other.

**Learned rows are written tagged** (`origin = 'learned'`, plus confidence and
evidence) into the app-managed `merge_engine.derived_relations`. On human
acceptance at the matches gate, the row is promoted to `company_relation`
**instantly** — no batch step. This keeps inferred and asserted data separable
in the consumer-facing table while still making accepted knowledge available
immediately.

**Precedence, highest first:**

```
1. merge_engine.never_merge / always_merge     explicit decision (human or, later, agent)
2. operator-maintained relation table          if present
3. company_relation (asserted rows)            promoted, human-confirmed
4. merge_engine.derived_relations              learned, tagged
5. learned name/domain signal                  computed this run
6. heuristic + LLM pair score                  lowest
```

### The never-merge table, and its scope (approved)

`merge_engine.never_merge` and `merge_engine.always_merge`, written at
`human_review_matches`, **read by `resolve_entities` before clustering.**

**Scope: pair-level applied automatically, generalisation proposed for separate
approval.**

- *Pair-level* — "record `diffbot:EuJ2H1…` must never merge with
  `zoominfo:143048837`." Exact, safe, no over-application. Weakness: a third
  Thales entity later ("Thales Alenia Space Italy") is not covered, and it does
  not survive record ids changing on re-extraction.
- *Generalised* — "any record normalising to `thales alenia space*` must never
  merge with any normalising to `thales`." Covers new records automatically, but
  can wrongly block two legitimate duplicate *Thales Alenia Space* records from
  different origins.

So: store pair-level and apply it; **propose** the general rule and activate it
only on separate approval. Because record ids can change, store the normalised
names alongside the ids so a pair-level rule can be re-anchored after
re-extraction.

In MD terms (above) these are one table with a `scope` column — `pair` rows
apply automatically, `predicate` rows only once approved.

**This is the single cheapest high-value change in the report.** It permanently
eliminates 4/4 company false merges, is fully deterministic, and closes P9.

**Quality impact: high. Speed impact: negligible.**

---

## P7 — No relationship derivation from the origins

**Severity: high (in scope, confirmed). Feeds P6 and P5 pass B.**

`_shared_neighbors_beyond_common_parent` is a working, carefully reasoned guard
whose docstring cites the Airbus "Defence and Space" blob failure it prevents.
It receives an empty index because neither origin ships a relation table.

### Fix — derive the edges

| source | edge |
|---|---|
| `companyexperience` (companyid, personid) | company ↔ person employment |
| `staff.main_entity_id` | person → employer (available once P3 resolves it) |
| `contracts` counterparty references | company ↔ company contractual |
| `companyinvestmentround` | investor ↔ investee |
| `institution` | company ↔ institution |

Materialise into the shape `build_relationship_index` already consumes
(`companyid` / `relatedcompanyid`) and pass it in. The guard is already correct
about the subtle part — two subsidiaries of one parent share exactly one
neighbour, which is evidence of **sibling**, not identity — so feeding it real
edges is safe.

**These same edges are pass B of P5.** One derivation, two consumers.

**Must tolerate non-entities.** GT's build produced 67
`gt_build_unresolved_links` rows — counterparty references such as *"Malaysian
Government / Remote Areas"*, *"Direct-To-Home (DTH) platforms"*, *"and telecom
operators"* — recorded as *"vague non-organisation reference; no
contract_entity row (reported)"*. Derivation must **report** these, never
fabricate company rows for them. This is also a likely source of the run's
`contract_entity` +69 and `contract_amendment` +27 inflation.

**Quality impact: high. Speed impact: small extraction cost.**

---

## P8 — LLM adjudication spends its budget on the wrong pairs

**Severity: medium-high. Design defect.**

### Evidence

```
_MAX_LLM_PAIRS  = 200   # per entity type, "closest to auto-match first" (line 181)
_LLM_CHUNK_SIZE = 40    # pairs per prompt (line 186)
_LLM_CONCURRENCY = 4    # (line 189)
_AUTO_MATCH = 0.95, _AUTO_EXCLUDE = 0.70
```

Three entities show `llm_scored: 200` — bound at the cap exactly
(`company_experience`, `product`, `company_industry`). Everything past rank 200
kept its heuristic score unadjudicated. Meanwhile **company got
`llm_scored: 0`**, because its four errors sat *above* the band entirely.

### Why the selection rule is backwards

Pairs nearest the auto-match line are the ones the heuristic is **most
confident** about. The genuinely ambiguous pairs sit mid-band and are never
seen.

### Why 40-per-prompt defeats the purpose

Batching forty pairs dilutes attention and makes per-pair **calibration
impossible** — you cannot threshold a score produced as one item in a list of
forty. This is the sharpest contrast with JEV, which *"answers one typed yes/no
question per pair — record_a and record_b describe the same real-world
person — and returns a calibrated probability instead of text"*, achieving
F1 0.943 / P 0.916 / R 0.971 at 368 ms median and $0.018 per 1 000 pairs [R3].

### Fix

1. **One pair per call**, typed binary verdict **plus** calibrated probability —
   not a free-form score. At 1.4 s/call with concurrency 4, 200 pairs ≈ 70 s per
   entity type.
2. **Select from the middle of the band**, or by nearest-to-0.5 calibrated
   uncertainty once calibration exists.
3. **Additionally route pairs that auto-matched with asymmetric name
   containment** (the P6 signal) — the exact class that produced 4/4 company
   errors and received zero adjudication. This is the highest-value new LLM
   application in the system.
4. Raise concurrency; the endpoint sustains it (262 144 context, 137 tok/s
   generation, 248 tok/s prompt, speculative decoding active).

### Independent endorsement of exactly this design

[R14 §10.3] reaches the same conclusion from the opposite direction — surveying
LLM use across the whole ER problem rather than auditing one pipeline:

> *"However, LLMs have prohibitive cost for blocking (due to quadratic
> comparisons). Future work may integrate LLMs selectively, e.g., **for refining
> ambiguous candidates post-blocking**."*

That is P8's design stated as a general principle: keep the model out of
blocking entirely, and spend it only on the ambiguous survivors. merge-engine
already places the model in the right stage — the defects are *which* survivors
it picks (nearest-threshold rather than mid-band) and *how many per call* (40
rather than 1).

The same source notes LLMs can do *"few-shot matching"* and *"in-context
clustering"* — packing multiple records into one prompt for direct clustering
[R14 §10.3]. **In-context clustering is the one LLM pattern to avoid here**: it
is what 40-pairs-per-prompt approximates, and it is precisely what makes
per-pair calibration impossible.

**Quality impact: medium-high. Speed impact: more calls, bounded and parallel.**

---

## P9 — No cross-run memory of human decisions

**Severity: medium-high.**

### Evidence

`human_split_pairs` is initialised to `[]` in `get_initial_state`
(`graph.py:900` and `:1001`) on **every** run. Splits are written to
`merge_engine_merge_audit` at `load_data.py:876`. That table is read only by the
UI lineage view — the connector's own comment states it is *"never used by the
merge."*

### Consequence

A reviewer who rejects the Thales / Thales-Alenia merge today is shown the
identical merge on the next run, forever. Human review is a one-shot filter, not
an accumulating asset.

Persisted today: cluster assignments (`merge_engine_entity_map` — required for
incremental correctness), the LLM response cache
(`config/.llm_cache.sqlite3`), and named run profiles.

### Fix

The `never_merge` / `always_merge` tables from P6, read before clustering. Plus
a **run ledger** (P10 / §3.2).

This is also what Zingg provides in another form: an *"interactive learner to
rapidly build training sets"* with active learning on *"frugally small training
samples"* [R4]. merge-engine's HITL gates already generate exactly those labels
and then discards them.

**And it is a precondition for replacing the reviewer with an agent** (§3.4):
once every decision is an auditable, replayable row, an agent's decisions can be
measured against the human decisions already recorded before it is trusted.
Without that layer, swapping in an agent yields unauditable decisions at scale.

**Quality impact: high and compounding. Speed impact: negligible.**

---

## P10 — No stage can fail a run on quality grounds

**Severity: high. This is why every defect above shipped silently.**

`validate_data` and `validate_graph_consistency` are both documented as
deliberately never setting `state["failure"]` — a hard severity is surfaced at
`human_final_approval` for a human to judge. With gates auto-approved nothing
stops the run, and `uncompared_pairs = 9 556 382` stopped nothing either.

**The pipeline can only fail on exceptions, never on results.**

### Fix, part 1 of 2 — the pre-load gate (no architecture change)

**Four of the five checks are computable *before* `load_data` writes anything.**
Only destination column fill-rate needs the write. So a pre-load gate delivers
most of the protection **and prevents the bad write entirely**, with no
structural change:

| check | source | available pre-load? | this run |
|---|---|---|---|
| `uncompared_pairs == 0` per entity | `resolution_stats` | **yes** | 9 556 382 → fail |
| no non-metadata entity with zero mappings | `field_mappings` | **yes** | 8 of 16 → fail |
| cluster collapse ratio within bounds | `resolution_stats` | **yes** | 13 518 → 10 092 → fail |
| multi-origin field has `is_coalesced` where expected | `field_mappings` | **yes** | every coalesce refused → fail |
| mapped destination column NULL rate below ceiling | post-load query | no — see §3.2 | `company.name` 78 % → would fail |

**On this run the pre-load gate alone fails at four independent points, before
a single row is written.**

Thresholds declarative in `config.yaml`, per entity where needed. Verdict
recorded in state and surfaced at `human_final_approval` so a human sees the
breach list rather than a bare "proceed?".

**Part 2 — staging, promotion and the run ledger — requires architectural
change and is deferred to §3.2.**

**Quality impact: high. Speed impact: negligible.**

---

## P11 — `map_fields` is 44 % of runtime, run serially

```
resolve_entities            434.408 s   52.5 %
map_fields                  363.607 s   43.9 %
load_data                    12.841 s
extract_data                  3.474 s
unify_entities                2.839 s
validate_data                2.576 s
discover_schemas              0.293 s
analyze_dependencies          0.074 s
preflight                     0.027 s
validate_graph_consistency    0.016 s
parse_request                 0.0014 s
plan_scope                    0.0000098 s
update_extraction_state       0.0000068 s
```

`map_fields` spends ~364 s across 16 entity types — including the eight that
produced zero mappings and the `_schemas` catalog entity.

### Fix — technical detail

1. Entity types are independent: no cross-entity state. Wrap the per-entity loop
   in `asyncio.Semaphore(4)` + `asyncio.gather`, the pattern
   `resolve_entities._judge_borderline` already uses (`_LLM_CONCURRENCY = 4`,
   `asyncio.gather` at line 2327).
2. **Verify no shared mutable state first.** CLAUDE.md already forbids
   module-level mutable state in `src/mcp_server/tools/` and
   `scripts/lint_arch.py` enforces it against `arch-lint-baseline.txt`, so this
   is likely already safe — but `map_fields`'s `taken` set (P1) is per-entity
   and must stay that way. Concurrency across entities is safe; concurrency
   *within* one entity's mapping is not, because `taken` is order-dependent.
3. Raise the bound above 4 once P12 removes the junk entities; the endpoint
   sustains it.

Combined with P12's removal of eight no-op entity passes:
**364 s → ~90–100 s.**

**Quality impact: none. Speed impact: ≈ 270–300 s, ≈ 33–36 % of total runtime.**

---

## P12 — Catalog tables and metadata columns are analysed and mapped

**Severity: low-medium for output; meaningful for cost and noise.**

### Evidence

`diffbot._schemas` and `zoominfo._schemas` became an entity named `schema`,
entered the extraction order, and consumed LLM field-mapping calls on
`keys_json` and `properties_json` before being correctly skipped as *"schema
metadata containing type definitions and constraints, not analytical data."*

**The model already gets these right — it just gets them right *after* the call
is paid for.** Other correctly-skipped examples from the run:
`run_timestamp` and `session_id` (*"pipeline metadata with no analytical
value"*), `user_prompt`, `collection`.

### Fix — two categories, and conflating them breaks things

| category | examples | effect |
|---|---|---|
| **don't extract** | `_schemas` table; `session_id`, `run_timestamp`, `user_prompt`, `collection` | never read |
| **don't map, but retain** | `_id`, `companyid`, `personid`, `main_entity_id` | not mapped to a destination column, but **required** as record identity, as join keys for P3, and as blocking keys for P5 |

Blanket-excluding `id`/`_id` would destroy P3's join discovery, P5 pass B, and
the entity map. And `created_at` must **not** be blanket-excluded — the
destination has a `created_at` column, and the run mapped
`diffbot.company.created_at → company.created_at` at confidence 1.0, correctly.

### Configuration — three layers, per-origin supported (approved)

```yaml
extraction:
  # layer 1 — built-in defaults (shipped, overridable)
  exclude_tables:   ["_*", "pg_*", "information_schema.*"]
  exclude_columns:  ["session_id", "run_timestamp", "user_prompt", "_version"]
  identity_columns: ["_id", "*id"]        # retained, never mapped

  # layer 2 — per-entity override
  per_entity:
    company:
      exclude_columns: ["sourcecategory"]

  # layer 3 — per-origin override (required)
  per_origin:
    diffbot:
      exclude_columns: ["classificationid"]   # opaque hash, no name source
    zoominfo:
      include_columns: ["companyemployees"]   # re-admit something a default excluded
```

Precedence: per-origin ▸ per-entity ▸ built-in. `include_*` always beats
`exclude_*` at the same level, so a default can be re-admitted without editing
shipped configuration.

**Profile-driven detection as a fourth signal.** The P3 column profile already
computes what is needed to *propose* metadata columns automatically: a column
whose values are all distinct and opaque (high entropy, uniform length, no
destination counterpart) is a surrogate; one whose value is constant per
extraction run is pipeline metadata. Propose, never auto-apply — a human or the
config confirms.

`diffbot.classificationid` is the cautionary case: GT dropped it as
*"an opaque hash with no industry-name source in origin; unmappable"* — a
correct human judgment that the profile can *suggest* but should not make alone.

**Quality impact: low-medium (removes junk rows and wasted calls).
Speed impact: small saving, compounding with P11.**

---

## Part 1 ordering and dependencies

| # | fix | quality | speed | depends on |
|---|---|---|---|---|
| 1 | Tokeniser vocabulary split (P1) | very high | — | none |
| 2 | Column profiling + value-based join discovery (P3) | very high | small cost | none |
| 3 | **Pre-load quality gate** (P10 pt 1) | high | — | none |
| 4 | `never_merge` / `always_merge` + read before clustering (P6, P9) | high | — | `merge_engine` schema |
| 5 | Exclude catalog tables + metadata columns (P12) | low-med | small saving | P3 profile (layer 4 only) |
| 6 | Parallelise `map_fields` (P11) | — | **≈ 33–36 %** | P12 (amplifier) |
| 7 | Dimension materialisation + junction handling (P4) | very high | — | **P3** |
| 8 | Relationship derivation (P7) | high | small cost | **P3** |
| 9 | Multi-pass disjunctive blocking, additive composition (P5.1) | high | faster | **P3, P7** |
| 9b | Cascaded refinement — `pg_trgm` on survivors only (P5.1) | medium | small cost | 9, 10 |
| 10 | Block purging + filtering, r = 50 % (P2, P5.2) | high | faster | **P5.1 (redundancy-positive)** |
| 11 | ARCS weighting + pruning (P2 stage 3) | high | faster | P5.1 |
| 12 | Budget → config + hard error (P2) | high | — | P5.1, P5.2 |
| 13 | Bounded transitive closure (P5.3) | high | — | none |
| 14 | One-pair-per-call, mid-band + containment routing (P8) | med-high | bounded | none |

**Items 1, 3, 13 and 14 are fully independent and can land immediately.**
**Item 10 must not precede item 9** — purging a single-key collection loses
recall [R1 §4.2].

### Net speed effect of Part 1 (quality-first, per §Q3 decision)

| change | Δ |
|---|---|
| `map_fields` concurrency + junk removal | **−270 s** |
| multi-pass with narrow keys | likely **fewer** total comparisons than today's 250 000 |
| uncapping the budget | ≈ free once blocking is selective |
| column profiling (new) | +30–60 s |
| one-pair-per-call adjudication | +200–300 s |
| dimension materialisation | faster — deterministic upserts replace LLM calls |

**Net: same order as today's 828 s, plausibly less.** Prioritising quality costs
essentially nothing here, because the two dominant nodes both have large wins
available. This is unusual and worth exploiting.

---

# PART 2 — Research, cited

## 2.0 Source access — disclosed

Reached and used: [R1] [R2] [R3] [R4] [R5] [R6] [R7] [R8] [R9] [R10] [R11]
[R12] [R13] [R14] [R15].

**[R14] and [R15] were initially unreachable over the network and were supplied
as local PDFs** (`docs/entity_resolution_blocking_survey.pdf`,
`docs/1-s2.0-S0888613X17300439-main.pdf`). Both are now read in full and cited
to section throughout; §2.10 and §2.11 cover them.

**Still not reached; not cited as evidence:**

| source | outcome |
|---|---|
| `medium.com/neo4j/i-tested-jev-on-entity-resolution-e7249f219ca6` | HTTP 403 — findings taken from the companion repository [R3] instead |
| `sciencedirect.com/science/article/abs/pii/S0020025525010898` | HTTP 403 — reached only as a search-result summary [R5b] |

[R1], [R14] and [R15] were extracted from PDF to text locally; section numbers
refer to each paper's own numbering.

## 2.1 Blocking and filtering — the canonical survey [R1]

**Formal measures** [R1 §2], verbatim:

```
Pair Completeness   PC(B)   = |D(B)| / |D(E)|     ∈ [0,1]
Pairs Quality       PQ(B)   = |D(B)| / ‖B‖        ∈ [0,1]
Reduction Ratio     RR(B,E) = 1 − ‖B‖ / ‖E‖       ∈ [0,1]
```

> *"Higher values for PC indicate higher effectiveness of the blocking scheme,
> while higher values for PQ and RR indicate higher efficiency. Note that PC
> provides an optimistic estimation of recall, presuming the existence of an
> oracle, while PQ provides a pessimistic estimation of precision, treating as
> false positives the repeated comparisons between duplicates."*

**Definition 3 (Blocking)** [R1]: *"Given an entity collection E, Blocking
clusters similar entities into a block collection B such that PC(B), PQ(B) and
RR(B, E) are simultaneously maximized."* The paper adds that blocking is
*"typically treated as an engineering task"* because true duplicates are
unknown — which is what ER computes.

**Workflow** [R1 §3–4, Fig. 1(b)]: **Block Building (BlBu) → Block Cleaning
(BlCl) → Comparison Cleaning (CoCl)**.

**Block Building taxonomy** [R1 §3]: Standard Blocking; Sorted Neighborhood
(sliding window over a sorted order); Q-gram blocking; Suffix Arrays; Canopy
Clustering (cheap approximate distance to form overlapping canopies); LSH.

**Block Cleaning** [R1 §4.1] — static vs dynamic. The governing assumption,
verbatim:

> *"the larger a block is, the less likely it is to contain unique duplicates,
> i.e., matches that share no other block. Such large blocks are typically
> produced by lazy schema-agnostic techniques and correspond to stop words."*

- **Block Purging** — *"discards blocks that exceed an upper limit on block
  cardinality or size."*
- **Block Filtering** — *"applies this assumption to individual entities,
  removing every entity from the largest blocks that contain it. In other
  words, it retains every entity in r % of its smallest blocks."*
- Also: Size-based Block Clustering (hierarchical, merges small / splits large
  under a penalty function); MapReduce dynamic blocking; `MaxIntersectionMerge`
  (ensures every block has ≥ |b|min entities); Rollup Canopies (learning-based
  greedy merge of small blocks to raise PC).
- **Dynamic**: Block Scheduling *"optimizes the processing order of blocks"*;
  Block Pruning extends it by exploiting *"the decreasing density of detected"*
  duplicates.

**The result that answers the P2 question** [R1 §4.1]:

> *"both Block Filtering and Block Purging boost PQ and RR by orders of
> magnitude, while exhibiting a low computational cost and a negligible impact
> on PC."*

With the parameter guidance:

> *"Block Filtering is typically applied after Block Purging by lowering r to
> 50 % instead of 80 %, which is the best configuration when applied
> independently."*

And the caution that they are complementary — *"multiple ones can be applied
consecutively in a single blocking workflow."*

**Comparison Cleaning** [R1 §4.2] — mutually **exclusive**, unlike Block
Cleaning:

> *"applying any Comparison Cleaning technique to a redundancy-positive block
> collection deprives it from its co-occurrence patterns and renders all other
> techniques inapplicable. These techniques also involve a much higher
> computational cost than Block Cleaning methods, due to their finer level of
> granularity."*

Cornerstone method: **Comparison Propagation**, which *"propagates all executed"*
comparisons to avoid repeats. Meta-blocking was *"formalized into a more
principled approach"* subsequently.

**Edge-weighting schemes** [R1 §4.2], verbatim:

> *"ARCS sums the inverse cardinalities of the common blocks, giving higher
> weights to entity pairs that co-occur in smaller blocks. CBS counts the number
> of blocks shared by two entities, with ECBS extending it to discount the
> contribution from entities placed in many blocks. JS corresponds to the
> Jaccard coefficient of two block lists, while EJS extends it to discount the
> contribution from entities appearing in many non-redundant comparisons.
> Finally, Pearson's χ² test assesses whether two adjacent entities appear
> independently in blocks."*

Also noted: **Transitive LSH** converts LSH blocks into an unweighted blocking
graph and applies community detection, finishing *"when the size of the largest
cluster is lower than a predetermined"* bound — the model for P5.3.

**Pruning efficacy** [R6]: *"Graph Partitioning prunes 50 % more comparisons
for practically no impact on recall"*; *"Reciprocal Pruning applies to any
blocking graph, retaining only the edges that are important for both adjacent
entities."*

### Direct application

| finding | application |
|---|---|
| PC / PQ / RR, maximise simultaneously | a global comparison cap maximises RR by destroying PC — the one trade the definition forbids (P2) |
| large blocks ≈ stop words, unlikely to hold unique duplicates | justifies per-value block purging over global truncation (P5.2) |
| purge + filter: orders of magnitude on PQ/RR, negligible PC loss | the answer to "reduce comparisons without dropping real ones" (P2) |
| r = 50 % after purging | concrete starting parameter |
| redundancy-positive precondition | **multi-pass must precede purging** — ordering constraint in Part 1 |
| ARCS weights pairs in *smaller* blocks higher | discriminates the 2-member email block from the 60-member Kumar block (P2 stage 3) |
| Transitive LSH stopping criterion | model for bounded closure (P5.3) |

## 2.2 Semantic Graph Blocking [R2]

Evaluates Standard Blocking (SB), Sorted Neighborhood (SN) and **Semantic Graph
Blocking (SGB)** — blocking *"based exclusively on the relations between
entities, including foreign keys in a database or links in RDF data"* — on
synthetic databases and the Citeseer reference database across partitions
Par1–Par3, under Strict and Weak Record Linkage.

**Weak RL, Par3:**

| method | pairs found | true positives | recall | precision |
|---|---|---|---|---|
| SB | 1 669 | 21 | 55.3 % | 1.3 % |
| SN | 480 | 20 | 52.6 % | 4.2 % |
| **SGB** | **32** | **22** | **57.9 %** | **68.8 %** |

**Strict RL, Par1:** SGB — 14 true matches, 31.6 % recall, 87.5 % precision.

Reported further: SGB imposed *"the smallest review effort … significantly
better than SB and SN because of smaller chances to accept false positives"*;
reduced false-positive ratios *"by two orders of magnitude"* in optimal cases;
and needs *"only one pass versus multiple passes needed by SB/SN."* The authors
recommend SGB **wherever relationship data exists.**

The related notion of **collective blocking** — where *"the way records for an
entity are placed in blocks may influence the way the records for another
entity are assigned to blocks"* [R7] — is the generalisation: blocking persons
by employer while blocking companies simultaneously.

### Direct application

This **is** the "block within company first" idea, published and measured.
52× fewer pairs with one *more* true positive than Standard Blocking, and
precision from 1.3 % → 68.8 %. It becomes available to merge-engine only after
P3 (join discovery) and P7 (relationship derivation) — which is the strongest
argument for their priority.

## 2.3 JEV + Neo4j — closest comparable end-to-end result [R3]

Three stages:

1. **Graph blocking** — Neo4j finds candidates sharing a **phone number OR a
   last-name key**, reducing to **1 822 candidate pairs, a 135× reduction**.
2. **Pairwise judgment** — TypeSafe AI "System One" *"answers one typed yes/no
   question per pair — record_a and record_b describe the same real-world
   person — and returns a calibrated probability instead of text."*
3. **Graph transitive closure** — Neo4j GDS **Weakly Connected Components**
   merges pairwise edges into clusters.

Against 1 822 synthetic pairs with known ground truth:

| metric | value |
|---|---|
| F1 | **0.943** |
| Precision | **0.916** |
| Recall | **0.971** |
| Adjusted Rand Index | 0.888 |
| Median latency | 368 ms / pair |
| Cost | $0.018 / 1 000 pairs |

Outperformed Claude Haiku (F1 0.894) on speed and cost at comparable clustering
quality.

### Point-by-point against this run

| dimension | JEV | merge-engine |
|---|---|---|
| blocking keys | **phone OR last-name** — high-selectivity key in the disjunction | last-name alone |
| candidate pairs | 1 822 | ≈ 962 000 (`staff`) |
| pairs compared | **all** | 250 000; **711 670 dropped** |
| LLM granularity | **1 pair / call** | 40 pairs / prompt |
| LLM output | **calibrated probability** | free-form score |
| pairs adjudicated | all candidates | 200 / entity, *nearest auto-match* |
| closure | WCC over P = 0.916 edges | transitive closure over low-precision edges |
| person precision | 0.916 | ≈ 0.0002 |

**Transferability caveat, stated honestly:** 1 822 synthetic pairs is a small
set, so the absolute F1 should not be read as what merge-engine would achieve.
The three *architectural* contrasts transfer, and all three are cheap.

## 2.4 Zingg [R4], [R8]

Frames the same problem — *"the number of comparisons increase quadratically as
the number of input record increases"* — and reduces the search to
**0.05 %–1 % of the possible problem space** [R8].

**Two-model architecture** [R8]: a **blocking model**, *"a clustering/blocking
model which indexes near similar records"*, described as **auto-learning**; and
a **similarity model**, a classifier that *"predicts similarity between records
that are not exactly the same."*

**Active learning** [R8]: an *"interactive learner to rapidly build training
sets"*, building models on *"frugally small training samples to high accuracy."*
Requires Spark 3.5.0.

**On blocking practice** [R4]: use keys *"as a union rather than an
intersection"* — records matching **either** key are compared, so *"records
with matching surname sounds but different postcodes (due to relocation) remain
candidates."* The explicit trade: *"more comparisons, but fewer missed
matches."* Warns against *"pathologically large"* blocks as a sign of an overly
permissive model, and provides a `verifyBlocking` diagnostic reporting *"block
size counts"* and *"top 10 % of records by block size"* with sample inspection.
On incomplete keys: if surname is 15 % missing, *"that field alone shouldn't
gate all comparisons"* — the model learns to *"route around"* the gap.

### Direct application — a useful calibration

For 13 518 `staff` records, all-pairs is C(13 518, 2) = **91 355 403**. Zingg's
0.05–1 % band is **45 678 – 913 554 pairs**. merge-engine's ≈962 000 candidates
sit **just above the top of that band.**

**So the pair *volume* is not absurd — the *selectivity* is.** merge-engine
produces roughly the right number of candidates using a key that groups the
wrong records together, then discards 74 % of them. That is a precise diagnosis:
the fix is key composition, not volume reduction.

The `verifyBlocking` diagnostic is also the model for the observability
merge-engine lacks (P10) — block-size distribution is exactly the signal that
would have surfaced the Kumar block.

## 2.5 Library comparison — Splink / Zingg / Dedupe [R9]

**On blocking.** Splink exposes blocking explicitly with *"interactive
diagnostics."* The central risk, stated in one line: *"bad blocking can miss
real matches or create overloaded blocks."*

**merge-engine is doing both at once** — overloaded blocks (surname) *and*
missed matches (the 250 k cap). These are normally a trade-off; here both
failure modes are present simultaneously.

**On scale.** Splink spans *"DuckDB for local work"* through *"100M+ record
jobs"* on Spark/Athena. Zingg requires Spark 3.5.0. Dedupe's convenience methods
target *"small-to-moderate datasets"*; larger ones *"require custom pair
generation, scoring and engineering."*

**merge-engine already uses Splink on DuckDB, so the backend is nowhere near its
limit at 13 518 records. No part of this run's failure is backend-attributable.**

**On outgrowing these libraries.** The stated transition point is when *"the
resolved entity has to become a real-time identity service"* — needing an
authenticated API, entity-graph maintenance, incremental updates and
low-latency access. merge-engine is a batch pipeline with a HITL loop, so **this
threshold does not apply and there is no case for replacing Splink.**

## 2.6 Unsupervised blocking key selection [R5], [R5b]

Each attribute is assigned a **discriminability** score — *"the portion of
distinct values over all values in the given dataset"*; for each attribute with
high discriminability, **coverage** is estimated — *"the portion of entities
that contain it"* — and *"the harmonic mean of discriminability and coverage is
then computed for all valid attributes"* [R5].

Motivation: *"To make the entity resolution process less dependent on human
domain knowledge, automatic selection of optimal blocking keys is required."*
[R5b] notes that existing approaches are *"either based on supervised methods
(which require preexisting labels) or explore solutions guided by specific
goals and restrictions, making them unsuitable for user-configured scenarios"* —
which is the argument for a hybrid: automatic proposal, human ratification.

### Direct application — and its limit

This is the formula behind P5's key selection, and it validates the
profile-driven design. **But it would not alone have saved this run**, and the
reason is instructive: `lastname` scores *well* on it (coverage ≈ 1.0,
discriminability ≈ 0.6, harmonic mean ≈ 0.74). Attribute-level statistics cannot
see that individual *values* are Zipf-distributed and "Kumar" behaves as a stop
word.

**Attribute-level selection picks the key; value-level purging [R1 §4.1]
handles bad values inside a good key. Both are required.** merge-engine had a
plausible key and no mechanism for the second.

## 2.7 Semantic / embedding-based entity resolution [R10]

Uses language models *"to bring an increased level of automation to schema
alignment, blocking, matching and even merging duplicate nodes and edges."*
**Semantic blocking** encodes records with sentence-transformer models into
dense vectors and clusters on cosine similarity, which *"leverages the rich
contextual understanding of modern language models to capture deeper
relationships between the fields of records, even when their strings differ
dramatically"* — versus traditional reliance on *"string distance, static rules
or complex ETL."*

Benchmark cited: **Ditto** (2020, BERT-based) *"beating previous benchmarks by
as much as 29 %"*; the author reports using it for *"billions of nodes"* in
2021. A demo matched 39 company records *"without a single mistake."*

### Assessment — deliberately not recommended yet

The demo is a proof of concept, not a benchmark, and the article gives **no cost
comparison** against rule-based blocking. Embedding blocking would be a
reasonable *additional pass* in the disjunctive scheme later — it is well suited
to the `SPEEDCAST FRANCE SPEEDCAST` / `Speedcast` string-divergence case — but
it is the wrong first move here, for two reasons: it requires embedding
infrastructure merge-engine does not have, and it would not fix any of the
twelve defects, which are wiring and parameter faults. Revisit once Part 1 is
landed and residual recall loss is measured.

### A third reason, from the survey — and it is specific to this pipeline

[R14 §6.4.4] documents that CRUD support in vector indices is *"non-trivial"*:

> *"**Insert:** FAISS indices support add, but some (e.g., IVF) require periodic
> retraining. **Update:** No native UPDATE; must delete old vector and insert
> new one. Requires maintaining external mapping from entity IDs to FAISS
> offsets. **Delete:** Some indices support remove_ids; others require
> mark-and-rebuild."*

The recommended mitigation is *"a bidirectional mapping between entity IDs and
FAISS internal IDs"* using `IDMap`/`IDMap2` wrappers [R14 §6.4.4].

**merge-engine is an incremental pipeline.** Its correctness already depends on
persisting cluster assignments across runs (§3.1), and an embedding index would
add a *second* piece of cross-run state with weaker update semantics — no native
UPDATE, periodic retraining, and an external id mapping to keep consistent with
`entity_map`. That is a meaningful new failure surface for a system whose
present defining weakness is that it cannot detect its own inconsistency (P10).

[R14 §6.6] adds the operational cost: *"Training data requirements"* (supervised
methods need labelled pairs — which merge-engine does not yet retain, see P9),
*"Computational cost: Neural encoding is slower than hash-based blocking"*, and
*"Infrastructure complexity: Requires GPU-accelerated training and specialized
ANN libraries."*

Balanced against that, the survey's own benchmark section reports that
*"vector-based methods with neural embeddings achieve superior recall and
reduction ratios compared to classical approaches"*, with DeepBlocker and
SC-Block achieving the better recall-efficiency trade-offs [R14 §6.5]. The named
lineage is DeepER → AutoBlock → DeepBlocker → Ditto → Sudowoodo → SC-Block
[R14 §6.2].

**So the verdict is "not yet", not "no".** The sequence that makes it viable:
land Part 1 → accumulate labelled pairs via P9's decision memory → measure
residual recall loss → then evaluate an embedding pass with training data that
exists and an incremental story that has been thought through.

## 2.8 ReAct [R11], [R12]

**The loop** [R11]: **Thought** (verbal reasoning traces), **Action**
(task-specific commands to an environment), **Observation** (environment
feedback). The key property, verbatim:

> *"reasoning traces do not affect the external environment. Instead, they
> affect the internal state of the model by reasoning over the context."*

**Versus chain-of-thought** [R11]: CoT reasons self-containedly with no
environmental grounding and relies on internal knowledge, producing
hallucination — the paper's example has CoT confidently asserting a false film
release date. ReAct retrieves facts to support reasoning while *using* reasoning
to *"target what to retrieve next."*

**Stated strengths** [R11]: interpretability through visible trajectories; gains
on QA, fact verification and interactive decision-making; human-in-the-loop
collaboration via editable reasoning traces.

**Stated limitation** [R11]: ReAct requires *"sparse reasoning"* for
decision-making tasks with many actions — it degrades where actions are dense.

**Components** [R12]: an LLM core *"as the agent's 'brain'"* requiring *"highly
capable models with advanced reasoning and instruction-following ability"*;
**tools**; a **scratchpad**; and **optional memory** to *"learn from past
mistakes and successes."*

**When ReAct is the wrong choice** [R12], verbatim: function calling is
preferable for *"straightforward, predictable tasks"* and *"can execute faster,
save tokens, and be simpler to implement than a ReAct agent."* ReAct's iterative
loops are *"inefficient"* for deterministic pipelines. Mitigations offered: a
maximum loop-iteration count, smaller models for delegated subtasks, confidence
thresholds for early termination.

## 2.9 Orchestration and multi-agent systems [R13], [R7]

**Patterns** [R13]: **centralized** (one orchestrator as *"the 'brain' of the
system"*), **decentralized** (independent or consensus decisions),
**hierarchical** (*"layers, resembling a tiered command structure"*),
**federated** (*"without fully sharing data or relinquishing control"*).

**Documented failure modes** [R13]:
- *"widespread failure"* where agents share vulnerabilities
- coordination failures — agents *"working against each other or duplicating efforts"*
- scalability collapse — *"delays or system failures"* under load
- single points of failure at a centralized orchestrator
- data-security exposure, since agents *"frequently process and share sensitive information"*

**Multi-agent advantages** [R7]: they *"tend to outperform single-agent systems
due to the larger pool of shared resources"*, the mechanism being that
*"instead of multiple agents learning the same policies, one can share learned
experiences to optimize time complexity and efficiency"*; each agent *"can hold
specific domain expertise."*

**Caveat, stated honestly:** [R7] argues the *advantages* of multi-agent systems
and offers **no explicit criteria for when a task should not be split.** The
negative recommendation in §3.3 therefore does **not** rest on it. It rests on
[R12]'s explicit "predictable tasks" guidance, [R13]'s failure-mode list, and
this run's measured evidence.

## 2.10 Blocking survey and architecture analysis [R14]

Gurevich, December 2024. Categorises blocking into **logic-based** (key/hash,
rule-based), **statistical** (token-based, q-gram), **cryptographic** (Bloom
filters), **LSH**, and **learned** (vector-based, neural) approaches, then adds
an architecture analysis for distributed ER.

**Complexity figures** (§2–§7):

| method | time | space |
|---|---|---|
| Standard Blocking | O(N) | O(N) |
| Sorted Neighborhood | O(N log N + N·w) | O(N) |
| Token-based | O(N · m̄) where m̄ = avg tokens/record | O(N · m̄) |
| K-means blocking | O(N · K · d · T) | — |

On k-means as a blocker (§7.1), the stated dilemma is the same trade in another
form: *"Selecting K: too small yields large blocks (inefficient); too large
risks splitting true matches."* Hierarchical and density-based alternatives
*"generally scale poorly (O(N²) or worse)"* (§7.2).

**Reference architecture** (§9.1) — the seven JedAI phases, tabulated against
merge-engine in §3.6. The missing phase is **Block Filtering**.

**Meta-blocking** (§9.3): *"Block merging: Merge highly overlapping blocks
(Jaccard similarity > τ). Pair weighting: Assign weights to pairs based on
co-occurrence frequency; prune low-weight pairs."*

**Transitive closure** (§9.4, Algorithm 5): build G = (V, E) over match edges,
compute connected components; for large graphs use distributed graph processing
(GraphX, Giraph).

**Incremental / streaming ER** (§9.5): Insert adds to the index and matches
against existing blocks; Update is remove-then-insert; Delete removes from index
and clusters. Named challenges: *"Maintaining index consistency (especially for
vector indices requiring retraining)"* and *"Propagating updates through entity
clusters (transitive closure)."*

**Hybrid approaches** (§10.1): **ensemble** (union of blockers, improves recall)
and **cascaded** (*"fast, coarse blocker first, then refine with more expensive
method"*).

**Active learning** (§10.2): parameter selection for LSH K and L, k-means K, and
similarity thresholds *"is challenging"*; active learning *"can guide parameter
tuning by soliciting labels for ambiguous pairs."*

**LLMs** (§10.3): few-shot matching and in-context clustering are possible, but
*"LLMs have prohibitive cost for blocking (due to quadratic comparisons). Future
work may integrate LLMs selectively, e.g., for refining ambiguous candidates
post-blocking."*

**Neural blocking** (§6.2–6.6): lineage DeepER → AutoBlock → DeepBlocker → Ditto
→ Sudowoodo → SC-Block; benchmarks favour DeepBlocker and SC-Block on the
recall-efficiency trade-off (§6.5); limitations are labelled-data requirements,
encoding cost, and *"Infrastructure complexity: Requires GPU-accelerated
training and specialized ANN libraries"* (§6.6); vector-index CRUD is
*"non-trivial"* (§6.4.4).

**Open challenges** (§10.4): *"Heterogeneous schemas: Most methods assume fixed
schemas; adapting to schema drift remains difficult"* — directly relevant, since
merge-engine's entire problem is two origins with different spellings of the
same concept; *"Multi-modal data"*; and *"Fairness and bias: Blocking may
introduce demographic biases if keys correlate with protected attributes."*

### Direct application

| finding | application |
|---|---|
| JedAI seven phases; Block Filtering absent in merge-engine | reframes P2 as a missing architectural phase, not a missing technique (§3.6) |
| cascaded blocking | new refinement added to P5.1 — cheap passes first, `pg_trgm` only on survivors |
| LLMs selectively, post-blocking only | independent endorsement of P8's stage placement |
| in-context clustering as an LLM pattern | the pattern to **avoid** — it is what 40-pairs-per-prompt approximates (P8) |
| vector-index CRUD difficulty | third, pipeline-specific reason embedding blocking is "not yet" (§2.7) |
| incremental ER: propagate closure updates | supports persisting `entity_map` (§3.1) |
| fairness and bias | new §5.4 — surname blocking is a fairness defect, not only a quality one |
| heterogeneous schemas as an open challenge | context for P1/P4 being genuinely hard, not merely unfinished |

## 2.11 ERBlox — matching dependencies with machine learning [R15]

Bahmani, Bertossi & Vasiloglou, *International Journal of Approximate
Reasoning* 83 (2017) 118–141. Integrates four components: an ML classifier for
duplicate/non-duplicate pairs; **MDs supporting the blocking phase**; record
merging from classifier results; and LogiQL (a Datalog extension on LogicBlox)
for specification and enforcement.

**The problem statement** (§1) — the clearest articulation of this run's person
failure found in any source:

> *"Although blocking will discard many record pairs that are obvious
> non-duplicates, some true duplicate pairs might be [lost due to] low
> sensitivity of blocking keys. More interestingly, similarity between blocking
> key values alone may fail to capture the relationships that naturally hold in
> the data and could be used for blocking. Thus, entity blocking based only on
> similarities of blocking key values may cause low recall. This is a major
> drawback of traditional blocking techniques."*

**Collective blocking** (§1):

> *"Records for different entities may be related via attributes in common and
> referential constraints, something the blocking mechanism could take advantage
> of. Blocking can be performed on each of the participating entities, and the
> way records for an entity, say Author, are placed in blocks may influence the
> way the records for another entity, say Paper, are assigned to blocks. This is
> called 'collective blocking'. … So, blocking decision making forms a
> collective and intertwined process involving several entities."*

Their construction is *"based on blocking keys **and** the enforcement of
semantic information about the relational closeness of entities"*, captured as
MDs — hence **MD-based collective blocking**.

**Matching dependencies** (§1):

> *"Matching dependencies are declarative logical rules that tell us under what
> conditions of similarity between attribute values, any two records must have
> certain attribute values merged (or matched), i.e. made identical."*

Example MD form: `Dept[Dep] ≈ Dept[Dep] → Dept[City] ≐ Dept[City]` — similarity
on the left, forced identity on the right. MDs also drive the merge phase:
*"when and how to merge is specified by matching dependencies, which invoke
matching functions"* (§1).

**Experiments** (§8). MAS dataset: 250 K authors, 2.5 M papers; author training
and test sets of 3 739 and 2 244 cases; 70/30 split, cross-validated; same SVM
throughout; three datasets including DBLP.

Findings, in their own terms:

- Standard key-equality blocking has the **highest reduction ratio and highest
  precision** but *"is very conservative, and has a very low rate of recall,
  i.e. many of the true candidate duplicate record-pairs are not identified as
  such."*
- *"Precision and recall of MD-based collective blocking are higher than the two
  standard blocking techniques. This emphasizes the importance of MDs that
  support collective blocking, and shows that **blocking based on string
  similarity alone fails to capture the semantic interrelationships that
  naturally hold in the data.**"*
- The cost, stated plainly: *"MD-based collective blocking has lower reduction
  ratio than standard MD-based blocking … larger blocks may be produced, and
  then, more candidate duplicate record-pairs become inputs for the classifier.
  In blocking, this is a common tradeoff that needs to be considered."*
- Overall: *"the quality of MD-based collective blocking dominates standard
  blocking, both in its key-based and MD-based forms, for the three datasets."*

**Related work** (§9) notes an unsupervised collective-deduplication approach
building *"a relationship graph … whose nodes are the entities (records), and
edges indicate entities which co-occur"*, where *"the similarity between two
nodes is calculated as the weighted sum of the attribute-value similarity and
their relational similarity."* That weighted-sum form is essentially what
`_shared_neighbors_beyond_common_parent` computes, once P7 supplies it edges.

### Direct application

| finding | application |
|---|---|
| MD-based collective blocking | the formal name for the employer-blocking idea (§5.1) |
| "string similarity alone fails to capture semantic interrelationships" | the precise diagnosis of the 42-Kumar failure |
| MDs as declarative merge/non-merge rules | the formalism behind `never_merge` / `always_merge`; settles pair-vs-predicate as one table with a `scope` column (P6) |
| MDs usable for blocking *and* merging | a never-merge rule can suppress candidate generation, cheaper than generate-then-reject (P6) |
| lower reduction ratio for collective blocking | **contradicts [R2]'s 52× reduction** — resolved in §5.1 by distinguishing additive from replacing composition |
| relational similarity as a weighted sum | the shape P7's edges should feed into |
| LogiQL / Datalog enforcement | **not adopted** — formalism taken, engine choice rejected as disproportionate (P6) |

---

# PART 3 — Architecture

## 3.1 App-managed state — the `merge_engine` schema (approved)

### The fact that motivates it

**The app already creates two tables in the destination's `public` schema.**
`postgresql_destination.py:1168` and `:1245` execute
`CREATE TABLE IF NOT EXISTS "{self._schema}"."merge_engine_entity_map"` and
`…merge_engine_merge_audit`; `self._schema` defaults to `public`. This run
created both (34 098 and 47 745 rows). `ground_truth` carried them too.

So the "no new tables in the destination" rule is **already violated, by design
and out of necessity.** `ensure_entity_map`'s own docstring explains why it must
persist: under incremental extraction a later run sees a subset of records, so a
record that previously clustered with others *"derives a different id, and is
inserted as a NEW destination row instead of updating the existing one."*

This is a recognised requirement, not a local quirk. [R14 §9.5] lists among the
named challenges of incremental ER *"propagating updates through entity clusters
(transitive closure)"* alongside *"maintaining index consistency"* — i.e. the
cluster assignment is state that must survive between runs, because the closure
that produced it cannot be recomputed from a partial re-read. Persisting
`entity_map` is the correct answer to that requirement; the only question is
**where** it lives, which is what the schema below settles.

### The design

```
global_warehouse.public.*          ← the contract. zero new tables, zero new columns.
global_warehouse.merge_engine.*    ← all app-managed state:
                                     entity_map        (moved from public)
                                     merge_audit       (moved from public)
                                     never_merge       (new)
                                     always_merge      (new)
                                     derived_relations (new, tagged)
                                     run              (new — the ledger)
                                     column_profile    (new — P3/P5/P12 cache)
```

**Why this design:**

1. `public` becomes a **pure** contract — and this *removes* the existing
   violation rather than adding to it.
2. The machinery exists: `DestinationConfig.schema_name` (aliased `schema`,
   default `public`) and the connector already interpolates `self._schema`
   everywhere. The change is a second configured schema, not new plumbing.
3. Same server ⇒ app state is in the **same transaction scope as the load**,
   which §3.2's promote step requires.
4. Invisible to anything reading `public`.

**Rejected alternative:** a SQLite sidecar beside `config/.llm_cache.sqlite3`.
It breaks incremental extraction across machines and loses transactional
coupling with the load. The LLM cache can be local because losing it costs only
a re-computation; losing `entity_map` costs destination-row duplication.

**Migration:** create the schema, copy both existing tables, repoint the
connector, keep read-fallback to `public` for one release so an existing
deployment is not stranded.

## 3.2 Stage-then-promote, and the run ledger (P10, part 2)

**The problem the pre-load gate does not solve.** Destination column fill-rate —
the check that catches `company.name` at 78 % NULL — is only computable **after**
the write. A gate that runs after `load_data` finds the corruption already
committed.

**Design:**

```
load_data        → writes to merge_engine_staging.<table>   (not public)
quality_gate     → pre-load checks (already passed) + post-write fill-rate checks
   pass          → promote: transactional INSERT…SELECT / schema swap into public
   fail          → public UNTOUCHED; staging retained for inspection;
                   ledger row written with the breach list; route to handle_error
```

**Run ledger** — `merge_engine.run`:

```
run_id, started_at, finished_at,
status            'passed' | 'failed' | 'aborted',
breached_checks   jsonb,      -- which checks failed, with measured values
stats             jsonb,      -- resolution_stats + fill rates, for trend comparison
promoted          boolean
```

This is what makes a failed run **recorded as failed** rather than
indistinguishable from an approved one — your point, and correct.

**Retry carrying context.** `MergeState` already has `retry_hints` and
`gate_retry_counts`, and `handle_error` already exists as a node. A failed run's
`breached_checks` becomes the next run's `retry_hints`, so a rerun knows what
failed and why rather than repeating it blind. `_fallible_router` and
`_error_gate_router` already implement the routing; the gate only needs to set
`state["failure"]`, which is the thing no current stage does.

**Why deferred to Part 3 and not Part 1:** promotion semantics, a staging
schema, and the ledger are structural. The pre-load gate (P10 part 1) delivers
four of five checks with no structural change and prevents the bad write
entirely — so the sequencing costs little.

## 3.3 Control flow — keep the deterministic DAG

**Recommendation: do not add a ReAct controller. Do not add sub-agents.**

### Reason 1 — the evidence: no defect was a planning failure

The run executed all 18 nodes in the correct order, passed five gates and
reported `completed`. Classifying all twelve defects:

| class | count | would a reasoning controller have prevented it? |
|---|---|---|
| wrong constant (`_MAX_COMPARISONS`, `_MAX_LLM_PAIRS`, `_LLM_CHUNK_SIZE`) | 3 | No — it inherits the same constants |
| wrong algorithm (tokeniser, join inference, blocking key) | 3 | No — it calls the same functions |
| missing capability (dimension materialisation, relationship derivation) | 2 | No — nothing to invoke |
| missing memory (never-merge, human corrections) | 2 | Partly — but a table solves it deterministically and auditably |
| missing failure condition (quality gate) | 1 | No — a threshold comparison |
| junk input (`_schemas`) | 1 | Marginally |

**Zero of twelve are planning failures.** Every one is a parameter, wiring or
capability gap.

### Reason 2 — the literature, for this task shape

[R12] is explicit that function calling beats ReAct for *"straightforward,
predictable tasks"*, executing faster with fewer tokens, and that ReAct loops
are *"inefficient"* for deterministic pipelines. Entity resolution against a
fixed destination schema is exactly that.

[R11]'s own limitation compounds it: ReAct degrades where actions are dense, and
merging 13 518 records is dense. Per-record reasoning is prohibitive;
per-batch reasoning is what the pipeline already does deterministically and more
cheaply.

### Reason 3 — orchestration's documented costs buy nothing measurable here

[R13]'s failure modes are coordination failures, duplicated effort, single points
of failure and scalability collapse. **None of this run's problems are
coordination problems** — one procedure, one ordering, no contention. Paying
coordination cost for no coordination benefit is a straightforward negative.

### Reason 4 — the weakness is observability, not autonomy

The defining failure is not that the pipeline chose wrongly. It is that it
**could not tell that what it did was wrong**: 9.5 M pairs dropped, 78 % of names
nulled, eight entities unmapped — status `completed`.

**Autonomy does not fix blindness. Instrumentation does.** That is why §3.2's
gate and ledger rank above any agent work.

### Sub-agents and parallelism

**Sub-agents: no.** There is no reasoning task here that benefits from
specialised agents conferring; it is one domain with a fixed procedure.

**Parallelism: yes — as plain concurrency.** `map_fields`'s entity types are
independent and do not communicate. That is concurrency (P11), not
collaboration. Wrapping each partition in an agent adds a reasoning loop, prompt
overhead and non-determinism to a loop over a list.

## 3.4 The HITL gates, and replacing the human later

Your stated intent: real HITL now because quality matters, agents at the gates
later. Agreed, with one ordering constraint.

### The change to make now, at no cost — an agent-agnostic decision contract

Define the gate interaction as a typed contract:

```
GateProposal  { gate, items[], evidence_per_item, allowed_decisions[], payload_version }
GateDecision  { gate, per_item: approve | reject | split | defer,
                reason, decided_by: 'human:<id>' | 'agent:<name>@<version>' }
```

`_build_interrupt_data` already produces something close to `GateProposal`, and
`resume_workflow` already accepts a decision vocabulary (`approve`, `edit`,
`retry`, `reject`, `abort`). Formalising it means a human UI and an agent
reviewer become **two implementations of one interface**, and the later swap is
configuration rather than surgery. Cost now: near zero.

### The ordering constraint — do not replace the reviewer before reducing the review volume

This run's `human_review_matches` proposed **14 252 staff merges from 13 518
records** — more merges than records. No human can review that, and **an agent
cannot either**; it would rubber-stamp it faster. That is precisely what
auto-approve did, and it produced the 14 382 false pairs.

So: **fix precision first** (P1–P7). The review queue shrinks from 14 252 to
the tens, and *then* either reviewer works — and the agent becomes genuinely
useful rather than an accelerant for bad decisions.

Corollary, worth stating as policy: **review volume is itself a quality
metric.** Any precision figure must be reported beside its review rate, or
"precision 0.99" can mean "everything difficult was deferred."

### Why the memory layer is a precondition, not a companion

Once every decision — human or agent — is an auditable, replayable row in
`merge_engine.never_merge` / `always_merge` with `decided_by`, an agent's
decisions can be **measured against the human decisions already recorded**
before it is trusted. Replay the agent over historical gate payloads, compare to
the human verdict, measure agreement, and promote it only when agreement is
adequate.

Without that layer, swapping in an agent yields unauditable decisions at scale —
strictly worse than a slow human. This is the strongest argument for P6/P9's
priority, beyond their immediate effect on the four company false merges.

[R12] is also relevant here: it recommends *"smaller, efficient models for
delegated subtasks"* and *"confidence thresholds for early loop termination"* —
i.e. an agent reviewer should defer to a human above an uncertainty threshold
rather than decide everything, which the `defer` verb above allows for.

## 3.5 Where the LLM should and should not be used

**Earned its place:**
- Field mapping — plausible mappings with sound skip reasons (*"schema metadata
  … not analytical data"*). Failures around it were structural (P1, P4), not
  judgment.
- Borderline pair adjudication — right idea, wired wrongly (P8).

**Should be removed or bounded:**
- `parse_request` took **0.0014 s** and `plan_scope` **9.8 µs** — effectively
  nothing on a full-corpus merge, and `human_review_scope` was correctly skipped
  as *"no single organisation."* For unscoped merges these are ceremony.
- Field mapping on catalog entities (P12) — calls spent then discarded.

**Should be used and currently is not:**
- Pairs that auto-matched with **asymmetric name containment** — the class that
  produced 4/4 company errors and received `llm_scored: 0`. One typed
  question per pair, calibrated [R3]. Highest-value new LLM application here.

## 3.6 Target pipeline shape

### Measured against a reference architecture

[R14 §9.1] gives the JedAI framework as a representative ER architecture, in
seven phases. merge-engine maps onto it almost exactly — which is worth knowing,
because it means the pipeline is not structurally unusual:

| # | JedAI phase [R14 §9.1] | merge-engine |
|---|---|---|
| 1 | Data Ingestion | `extract_data` |
| 2 | Schema Matching — *"align attributes across datasets"* | `discover_schemas` + `map_fields` |
| 3 | Blocking | `_generate_candidate_pairs` |
| 4 | **Block Filtering** — *"prune redundant or low-quality blocks (meta-blocking)"* | **absent** |
| 5 | Pairwise Comparison | scoring in `resolve_entities` |
| 6 | Classification — *"threshold or ML classifier"* | `_AUTO_MATCH` / `_AUTO_EXCLUDE` bands + Splink |
| 7 | Clustering — *"transitive closure"* | `_build_clusters` |

**Phase 4 is the one merge-engine does not have at all.** That reframes P2: it
is not "add a technique to blocking", it is **a whole reference phase is
missing**, and `_MAX_COMPARISONS` is the improvised substitute standing in for
it. A global truncation is what you get when the architecture has no place to
prune blocks properly.

Two further notes from the same source:

- Phase 2 sits **before** blocking in the reference design. In merge-engine
  schema alignment is split — `discover_schemas` early, `map_fields` *after*
  resolution — which is why entity identity is never reconciled against the
  destination schema in time to matter (P0-5 / P4). Moving all of phase 2 before
  phase 3 is a larger change than Part 1 contemplates; noted as a known
  divergence rather than proposed here.
- Phase 7's canonical implementation is connected components over the match
  graph [R14 §9.4, Algorithm 5] — which is what merge-engine does, and which is
  safe only with high-precision edges. See P5.3.

### The shape

Same paradigm; three new nodes, one new terminal gate, changed internals in
three existing nodes.

```
preflight → parse_request → discover_schemas
  → [NEW] profile_columns          value profiles: join discovery + key selection + metadata detection
  → analyze_dependencies            ** name heuristic → value-based inclusion dependency (P3) **
  → [NEW] derive_relationships     edge tables → relationship_index (P7)
  → human_review_deps → plan_scope → human_review_scope
  → extract_data                    ** metadata/table exclusion applied (P12) **
  → validate_data
  → resolve_entities                ** never_merge/always_merge read first (P6,P9);
                                       multi-pass disjunctive blocking (P5.1);
                                       block purge + filter r=50% (P2,P5.2);
                                       ARCS weighting + pruning (P2);
                                       bounded closure (P5.3);
                                       one-pair-per-call adjudication (P8) **
  → human_review_matches            ** writes never_merge/always_merge;
                                       promotes derived_relations (P6) **
  → unify_entities
  → map_fields                      ** concurrent across entities (P11);
                                       vocabulary tokeniser (P1);
                                       emits `dimension` outcome (P4) **
  → [NEW] materialize_dimensions   lookup upserts + FK resolution (P4)
  → human_review_mappings → human_final_approval
  → load_data                       ** writes to staging (§3.2) **
  → [NEW] quality_gate             ** CAN FAIL THE RUN; promotes on pass (P10,§3.2) **
  → validate_graph_consistency → update_extraction_state
```

## 3.7 Expected impact

Quality impact is given as direction plus mechanism, never a predicted score —
with 20 ground-truth pairs a predicted F1 would be false precision (§0.4).

| change | quality mechanism | speed |
|---|---|---|
| Tokeniser vocabulary split (P1) | recovers `name` on 78 rows, `employee_count`, `legal_name`; fixes every PG origin | — |
| Value-based join discovery (P3) | activates 4 dormant defences; unlocks email identity + FK columns | +30–60 s |
| Pre-load quality gate (P10 pt 1) | 4 independent failure points instead of a green light | — |
| Decision memory (P6, P9) | eliminates 4/4 company FPs permanently; compounds per review | — |
| Dimension materialisation (P4) | recovers `industry`, `product_category`; widens `person`, `company` | faster |
| Multi-pass blocking (P5.1) | attacks the 14 382 FP and the 42-Kumar cluster at source | **faster** |
| Purge + filter + ARCS (P2, P5.2) | orders-of-magnitude PQ/RR gain at negligible PC cost [R1 §4.1] | **faster** |
| Budget → config + hard error (P2) | 9.5 M unexamined pairs become examined or reported | — |
| Bounded closure (P5.3) | stops one bad edge becoming N bad pairs | — |
| Relationship derivation (P7) | feeds the guard that would have blocked Thales | small cost |
| One-pair-per-call (P8) | calibrated probabilities; adjudicates the pairs that actually err | +200–300 s |
| `map_fields` concurrency (P11) | — | **−270 s** |
| Metadata exclusion (P12) | removes junk rows and wasted calls | small saving |
| Staging + promote + ledger (§3.2) | a failed run cannot corrupt `public` | — |
| Gate decision contract (§3.4) | makes agent review possible *and measurable* later | — |

## 3.8 Recommended against, with reasons

| proposal | verdict | reason |
|---|---|---|
| ReAct controller over the pipeline | **No** | 0 of 12 defects are planning failures; [R12] favours function calling for predictable tasks; [R11] degrades on dense actions; adds non-determinism to a pipeline whose weakness is blindness |
| Sub-agents per entity type | **No** | partitions do not communicate — concurrency, not collaboration; [R13]'s coordination costs with no coordination benefit |
| Replacing Splink | **No** | Splink spans DuckDB → 100M+ records on the backend already in use [R9]; nothing here is backend-attributable at 13 518 records |
| Agent-held memory instead of tables | **No** | constraints must be exact, auditable and queryable by a deterministic matcher; tables also enable measuring an agent before trusting it (§3.4) |
| Embedding/semantic blocking now | **Not yet** | no cost data in [R10]; would fix none of the twelve defects; vector indices have **no native UPDATE** and need retraining plus an external id mapping [R14 §6.4.4], adding a second piece of fragile cross-run state to an incremental pipeline; needs labelled pairs P9 does not yet retain [R14 §6.6]. Reasonable as an *additional pass* after Part 1 — benchmarks do favour DeepBlocker/SC-Block [R14 §6.5] |
| Raising `_MAX_COMPARISONS` alone | **No** | without P5 it trades silent recall loss for a large slowdown; and purging before multi-pass loses recall [R1 §4.2] |
| Agent reviewers at gates before precision is fixed | **No** | 14 252 proposals cannot be meaningfully reviewed by anyone; an agent would rubber-stamp faster (§3.4) |
| A repo skill for the runtime | **No** | the workflow is code, not prompt; existing skills serve the developer |
| New destination tables in `public` | **No** | §3.1 — `merge_engine` schema instead, which also removes the existing violation |

---

# PART 4 — Appendices

## 4.1 Constants that govern output quality

All module-level in `resolve_entities.py`; **none is configurable.** For a
system whose output quality turns on these numbers, module scope is the wrong
home.

| constant | line | value | effect this run |
|---|---|---|---|
| `_MAX_COMPARISONS` | 59 | 250 000 | **9 556 382 pairs discarded** |
| `_AUTO_MATCH` | 62 | 0.95 | all 4 company FPs fired above it |
| `_AUTO_EXCLUDE` | 63 | 0.70 | band floor |
| `_MAX_REF_BLOCK` | ~177 | 25 | correct idea, not generalised (P5.2) |
| `_MAX_LLM_PAIRS` | 181 | 200 | bound exactly on 3 entities |
| `_LLM_CHUNK_SIZE` | 186 | 40 | defeats calibration (P8) |
| `_LLM_CONCURRENCY` | 189 | 4 | the one that is right |
| `_MIN_PAIRS_FOR_MODEL` | ~193 | 30 | below this, heuristic replaces the model |

## 4.2 Per-entity resolution statistics (source: `resolution_stats`)

| entity | records | cand. pairs | auto-matched | llm_scored | clusters | uncompared |
|---|---|---|---|---|---|---|
| experience | 13 518 | 250 000 | 31 911 | 1 | 9 070 | **8 792 591** |
| staff | 13 518 | 250 000 | 14 252 | 156 | 10 092 | **711 670** |
| contact_information | 3 781 | 5 526 | 4 | 5 | 3 777 | 0 |
| product | 1 592 | 250 000 | 2 567 | **200** | 1 146 | 52 121 |
| company_industry | 544 | 34 252 | 160 | **200** | 539 | 0 |
| evidence | 517 | 9 005 | 0 | 0 | 517 | 0 |
| entity | 115 | 36 | 0 | 0 | 115 | 0 |
| **company** | **115** | **4 315** | **17** | **0** | **99** | **0** |
| event | 112 | 272 | 3 | 3 | 109 | 0 |
| location | 103 | 1 154 | 28 | 2 | 82 | 0 |
| company_experience | 84 | 784 | 11 | **200** | 75 | 0 |
| contract | 47 | 142 | 0 | 0 | 47 | 0 |
| schema | 24 | 276 | 0 | 0 | 24 | 0 |
| institution | 16 | 7 | 0 | 2 | 16 | 0 |
| company_investment_round | 11 | 13 | 13 | 0 | 5 | 0 |
| source_profile | 1 | 0 | 1 | 0 | 1 | 0 |

Company's 17 auto-matches = 13 TP + 4 FP exactly.

## 4.3 Row-level outcome vs ground truth

`expected` = GT rows minus what the app provably cannot produce.
`ground_truth.company` holds 114 rows = **102 resolved clusters + 12
`stub-created` relation endpoints absent from both origins**; the arithmetic is
exact, so the app's target is **102, never 114**. Comparing against 114 invents
12 misses.

| table | GT | expected | app | Δ |
|---|---|---|---|---|
| company | 114 | **102** | 99 | −3 |
| person | 13 591 | 13 591 | 10 092 | **−3 499** |
| experience | 13 600 | 13 600 | 9 070 | **−4 530** |
| contact_email | 5 696 | 5 696 | 3 777 | **−1 919** |
| product | 1 592 | 1 592 | 1 146 | −446 |
| product_category | 388 | 388 | **0** | −388 |
| industry | 41 | 41 | **0** | −41 |
| location | 103 | 103 | 82 | −21 |
| contact_phone | 83 | 83 | 83 | 0 |
| contract | 33 | 33 | 47 | **+14** |
| contract_entity | 46 | 46 | 115 | **+69** |
| contract_product | 0 | 0 | 1 019 | **+1 019** |
| contract_amendment | 0 | 0 | 27 | **+27** |
| finance | 0 | 0 | 21 | **+21** |
| company_relation | 11 | 11 | 21 | +10 |

**Total short of expected: 10 847.** The `+` rows are P1-8's stray
cross-table mappings (e.g. `company.companyid → contact_email.company_id` at
0.85; `company.totalinvestmentcurrency → finance.currency` at 0.80).

The 12 unreachable companies, for the record: *abu dhabi securities exchange
adx, carnival, euroconsult, gulfstream aerospace, iridium, kongsberg defense &
aerospace, mubadala investment, nbn, norwegian coastal administration, norwegian
cruise lines, public investment fund, royal caribbean international.*

## 4.4 CLAUDE.md and AGENTS.md — assessment and changes applied

**Assessment.** CLAUDE.md is unusually good. The "every line traceable to a real
past failure" discipline works, and the concrete citations (43-column schema
drift, `_LAST_BLOCKING_STATS`, the `*.csv` gitignore incident that degraded the
eval set) make rules stick where abstract style guidance does not. The
code-lookup ladder demonstrably saves time — ~19 500 tokens on a single turn of
this session via `graft skeleton` alone. The branch-discipline and ponytail
sections should not change.

**Changes applied under approval:**

1. **CLAUDE.md, "Live verification is mandatory".** This is the file's most
   important rule and **it did not fire on this run** — exactly the enforcement
   gap the file warns about elsewhere. Added the measured evidence: a run that
   finished `completed` having discarded 9 556 382 pairs, left 78 of 99
   `company.name` NULL and produced zero mappings for 8 of 16 entities; plus
   the instruction to write the scorer *before* the run, since one written
   afterwards gets fitted to the result.
2. **AGENTS.md header.** It claimed *"same content"* as CLAUDE.md while
   containing materially different abridged content — the kind of inaccuracy the
   repo's own standards flag. Now describes itself accurately as a pointer plus
   the few restated rules.
3. **AGENTS.md OpenWiki block.** Reduced from nine lines to four; it was
   explaining at length that something does not exist.

**Not applied, and why.** I did **not** add the new rules this run suggests
(no silent comparison truncation; no destination column fed by one origin when
both have it) to the "mechanically enforced" list. Nothing enforces them yet,
and listing an unenforced rule there is precisely the defect that section exists
to prevent. They belong there once `check.py guards` or the quality gate
actually runs them.

---

# References

**[R1]** Papadakis, G., Skoutas, D., Thanos, E., Palpanas, T. —
*"A Survey of Blocking and Filtering Techniques for Entity Resolution."*
arXiv:1905.06167. <https://arxiv.org/pdf/1905.06167>
Retrieved as PDF and extracted to text locally; §2 (metrics, Definitions 2–3),
§3 (Block Building taxonomy), §4.1 (Block Cleaning, Purging, Filtering), §4.2
(Comparison Cleaning, weighting schemes, Transitive LSH).
Also published as: *ACM Computing Surveys* 53(2), 2020 —
<https://helios2.mi.parisdescartes.fr/~themisp/publications/csur20-blockingfiltering.pdf>

**[R2]** *Semantic Graph Blocking* — Universitat Politècnica de Catalunya.
<https://upcommons.upc.edu/server/api/core/bitstreams/073b090a-24a3-4438-b22f-b1fb888118d5/content>
Comparative evaluation of SB, SN and SGB on synthetic databases and the Citeseer
reference database (Par1–Par3, Strict and Weak RL).

**[R3]** *jev-neo4j-entity-resolution.*
<https://github.com/Kervin-Hu-Neo4j/jev-neo4j-entity-resolution>
Graph blocking + typed pairwise judgment + GDS Weakly Connected Components;
F1 0.943, P 0.916, R 0.971, ARI 0.888, 368 ms median, $0.018/1k pairs.
*(Companion Medium article <https://medium.com/neo4j/i-tested-jev-on-entity-resolution-e7249f219ca6> returned HTTP 403 and was not used.)*

**[R4]** Zingg — *"Entity Resolution at Scale, Part 3: Blocking — Making
Billion-Record Matching Tractable."*
<https://www.zingg.ai/post/entity-resolution-at-scale-part-3-blocking>
Union-not-intersection blocking; `verifyBlocking` diagnostics; block-size
control; routing around incomplete keys.

**[R5]** *Unsupervised Blocking Key Selection for Real-Time Entity Resolution.*
Springer, LNCS. <https://link.springer.com/chapter/10.1007/978-3-319-18032-8_45>
Discriminability, coverage, and their harmonic mean as the key-selection score.

**[R5b]** *A generalized approach to perform unsupervised blocking key selection
for entity resolution.* Information Sciences.
<https://www.sciencedirect.com/science/article/abs/pii/S0020025525010898>
**HTTP 403 — reached only as a search-result summary; cited for framing only.**

**[R6]** Papadakis, G., et al. — *Meta-Blocking* and *Supervised Meta-blocking.*
<https://dl.acm.org/doi/10.14778/2733085.2733098> ·
<https://arxiv.org/pdf/2204.08801>
Graph Partitioning and Reciprocal Pruning results.

**[R7]** IBM — *What is a multiagent system?*
<https://www.ibm.com/think/topics/multiagent-system>
Shared-resource advantage, domain specialisation, collective blocking framing.
**Offers no criteria for when not to split a task — noted explicitly in §2.9.**

**[R8]** Zingg — project repository.
<https://github.com/zinggAI/zingg/>
0.05–1 % of the problem space; blocking model + similarity model; active
learning; Spark 3.5.0.

**[R9]** Tilores — *"Best open-source entity resolution and record linkage
libraries: Splink, Zingg, Dedupe — and when to move beyond them."*
<https://tilores.io/content/best-open-source-entity-resolution-and-record-linkage-libraries-splink-zingg-dedupe-and-when-to-move-beyond-them/>
Blocking failure modes; Splink DuckDB → 100M+ scale; the real-time
identity-service threshold.

**[R10]** *The Rise of Semantic Entity Resolution.* Towards Data Science.
<https://towardsdatascience.com/the-rise-of-semantic-entity-resolution/>
Sentence-transformer blocking; Ditto (2020) reported at up to +29 % over prior
benchmarks.

**[R11]** Google Research — *"ReAct: Synergizing Reasoning and Acting in
Language Models."*
<https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/>
Thought/Action/Observation loop; CoT grounding failure; the "sparse reasoning"
limitation.

**[R12]** IBM — *What is a ReAct agent?*
<https://www.ibm.com/think/topics/react-agent>
Components (LLM core, tools, scratchpad, optional memory); function calling
preferable for predictable tasks; loop-bounding mitigations.

**[R13]** IBM — *What is AI agent orchestration?*
<https://www.ibm.com/think/topics/ai-agent-orchestration>
Centralized / decentralized / hierarchical / federated patterns; documented
failure modes.

**[R14]** Gurevich, V. — *"Blocking Techniques for Large-Scale Entity
Resolution: A Comprehensive Survey and Architecture Analysis."* December 2024.
Local copy: `docs/entity_resolution_blocking_survey.pdf`
(originally at `raw.githubusercontent.com/imvladikon/imvladikon/master/articles/entity_resolution_blocking_survey.pdf`,
which returned unreadable binary over the network).
§2 (logic-based blocking, complexity), §3 (statistical), §4 (Bloom filters),
§5 (LSH, MinHash, SimHash), §6.2 (neural lineage), §6.4.4 (vector-index CRUD),
§6.5–6.6 (benchmarks, limitations), §7 (clustering-based blocking),
§9.1 (JedAI reference architecture), §9.3 (meta-blocking), §9.4 (transitive
closure), §9.5 (incremental/streaming ER), §10.1 (ensemble vs cascaded),
§10.2 (active learning), §10.3 (LLMs for ER), §10.4 (open challenges incl.
fairness and bias).

**[R15]** Bahmani, Z., Bertossi, L., Vasiloglou, N. — *"ERBlox: Combining
matching dependencies with machine learning for entity resolution."*
*International Journal of Approximate Reasoning* **83** (2017) 118–141.
<https://www.sciencedirect.com/science/article/pii/S0888613X17300439>
Local copy: `docs/1-s2.0-S0888613X17300439-main.pdf` (the publisher URL returned
HTTP 403 over the network).
§1 (matching dependencies, collective blocking, the four integrated components),
§8 (experiments on MAS and DBLP; reduction-ratio trade-off), §9 (related work —
relational similarity propagation).

**Not reached, not used as evidence:**
`medium.com/neo4j/i-tested-jev-on-entity-resolution-e7249f219ca6` (HTTP 403;
companion repository [R3] used instead) ·
`sciencedirect.com/science/article/abs/pii/S0020025525010898` (HTTP 403; search
summary only, cited as [R5b] for framing).

---

## Primary evidence (this repository and run)

| claim | source |
|---|---|
| tokeniser behaviour | live call to `map_fields._tokenize_field` / `_safe_coalesce_target` |
| `_MAX_COMPARISONS`, `_MAX_LLM_PAIRS`, `_LLM_CHUNK_SIZE` | `src/mcp_server/tools/resolve_entities.py:59, 181, 186` |
| join-candidate generation | `src/mcp_server/tools/join_graph.py:266` |
| coalesce allowlist | `src/mcp_server/tools/map_fields.py:860–915` |
| entity-map creation in `public` | `src/mcp_server/connectors/postgresql_destination.py:1168, 1245` |
| `human_split_pairs` reset each run | `src/orchestrator/graph.py:900, 1001` |
| split written, never read by matching | `src/orchestrator/nodes/load_data.py:876`; `src/ui/components/db_discovery.py:521–523` |
| node timings, mappings, stats | `phase3/state.json` |
| gate payloads | `phase3/gates.jsonl` |
| every TP/FP/FN pair | `score.json` |
| scorer correctness | identity run + injected FP/FN, `gt_score.py` |
