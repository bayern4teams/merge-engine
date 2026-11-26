# Why the app's Boeing merge is far worse than the hand-merge

**Date:** 2026-07-29  **Run:** `wf_58d843e1` — *"merge all information about Boeing"*,
origin1 + origin2 (ClickHouse) → `global_warehouse` (PostgreSQL), `full_remerge=true`.
**Deliverable:** diagnosis only. The destination was cleaned before and after; it is
back to pristine schema-only (25 tables, 0 rows). No code/prompt was changed.

---

## Headline

| | Hand-merge (by me, no app) | The app |
|---|---|---|
| Entity families handled | 11 (company, person, experience, relation, finance, technology, industry, location, email, phone, tech-category) | **2** (company, person) — product demanded but absent |
| Cross-origin Boeing match | 24 matches → **1 unified golden Boeing** @0.98 | **0 auto-matched / 141 auto-excluded** → origin1-Boeing and origin2-Boeing kept **separate** |
| Per-attribute conflict resolution | yes (origin1 legal_name/founding; origin2 revenue/employees) | **never ran** (0 merges → 0 conflicts) |
| Rows written to destination | **3,427** across 11 tables | **0** — the load **crashed and rolled back** |
| Run outcome | success | **FAILED** |
| Side effect on destination | none | **+23 columns added permanently** despite the rollback |

The app didn't just produce *less* — for this request it produced **nothing**, and it
mutated the destination schema on the way out. Below is exactly where each failure
happens, with the evidence captured live from the run.

---

## The pipeline, and where it broke (in order)

### 1. `parse_request` — breadth is capped here, at the very first step (ROOT CAUSE)

The LLM was healthy (it later decided field mappings correctly), so this is the
**prompt**, not a model failure. `request_parsing.system.txt` pins output to a fixed
**7-type enum** and its *"Company Wildcards"* rule expands *"all information about
X"* to only `company, person, product`. Captured at the deps gate:

```
target_entities  = ["company", "person", "product"]
entity_tables    = { company: [origin1.Company, origin2.Company],
                     person:  [origin1.Person,  origin2.Person],
                     product: [] }
note: "CompanyExperience, Experience, and Location are relationship or attribute
       tables rather than primary entity records"
```

Financials, technographics, company-relations, industries, investors,
investment-rounds, locations, contacts, employment/experience — **8 of the 11
families I loaded have no entity type at all**, so they are never selected, never
planned, never extracted. Everything downstream is powerless to recover a concept
`parse_request` never emitted.

### 2. `extract_data` — the wildcard's `product` turns "no products" into a hard error

There is no product table in these origins, so extraction raised
**`DataQualityError` (disposition: escalate)** and *halted the whole run* at an error
gate: *"no source table could be found for entity(s): product"* — repeated 8×. I had
to choose **Skip** to continue. So the unconditional `product` in the wildcard makes a
perfectly valid company-only corpus **fail by default**.

What it *did* extract for the two real entities: `company: 36` (18 origin1 + 18
origin2), `person: 39` (origin1 only — origin2 persons matched 0 under Boeing scope).

### 3. `resolve_entities` — cross-origin matching collapsed (SECOND ROOT CAUSE)

```
company: 36 records → 34 clusters   auto_matched=0  auto_excluded=141  llm_scored=0
```

Only 2 of 36 company records merged; the main Boeing from each origin stayed
**separate**. Why: the matcher's term-frequency weighting is correct for general
dedup but **backfires in a scoped single-subject merge** — the token *"Boeing"*
appears in *every* record, so its weight is driven to ~0 and the
`_has_discriminating_agreement` guard (frequency ≤ 0.05) **rejects the one signal
that matters**. With the subsidiaries differing on everything else, nothing clears
the 0.95 bar and everything is auto-excluded below 0.70 — so nothing even reached the
LLM adjudicator. My hand-merge matched Boeing↔Boeing trivially on exact-name + domain.
The matches HITL gate was then **auto-skipped**: *"0 auto-matched, 141 auto-excluded;
nothing was ambiguous."*

### 4. `unify_entities` — survivorship never engaged

`total_conflicts = 0`. Because nothing merged, there were no field conflicts to
resolve, so the per-attribute policy (and its known "completeness = string length"
weakness) never even ran. There was no golden record to build.

### 5. `map_fields` — first-5-record sampling drops fields; columns proliferate

The person mapping mapped origin1's `name` → a **new** `full_name` column, but
origin2's `firstName/lastName/middleName` **were not in the sampled unified schema**
(built from the first 5 records / 3 values) and so were absent from the mapping — they
would be dropped at load and the destination's native `first_name/last_name` left
empty. The mapper also proposed **23 brand-new columns** instead of aligning to the
existing schema (company: phone, rank, motto, ceo_name, total_investment, …; person:
full_name, role, …).

### 6. `load_data` — atomic load crashed on a type mismatch; 0 rows committed

```
invalid input syntax for type double precision: "$1 Million"
Load failed for entity 'company'  →  load_rolled_back: true
load_failed_entities: [company, person]
```

origin2's `revenue` is a human-readable **string** (`"$1 Million"`, from *Boeing
Employees Tennis Club*), mapped straight into the numeric `revenue` column with no
parsing. Because the load is one atomic transaction, the **entire** company+person
load rolled back — **0 rows written**.

### 7. Side effect — the schema ALTERs did **not** roll back

The 23 `ADD COLUMN`s committed **permanently** even though the data load failed
(schema changes commit outside the data transaction), and a `merge_engine_entity_map`
table was created. A failed run still mutates the destination schema. (I dropped all
23 columns + the helper table to restore the pristine 25-table schema.)

---

## Answers to your three questions

### Should we improve the prompts? — **Yes. Highest ROI, smallest change.**

The breadth ceiling is 100% the `request_parsing` prompt. Widening the enum and
rewriting the "Company Wildcards" rule so *"all information about X"* expands to the
full related-concept set (and making `product` conditional on a product table
existing) is the single biggest win and touches one file
(`src/llm/prompts/request_parsing.system.txt`, mirrored in
`entity_aliases.py ALIAS_GROUPS`). **But the prompt alone is not enough** — even the
company entity it *did* handle failed to match and failed to load.

### Should we change the architecture? — **Partially, and it's the durable fix.**

Two architectural gaps the prompt can't fix:

- **Breadth should be schema-driven, not enum-driven.** "Everything about X" should be
  derived from the *actual origin FK/reference graph* around the subject (company →
  its financials, technographics, relations, people, locations, contacts…), not from a
  hard-coded 7-type list. This is a new discovery stage feeding the existing DAG.
- **The matcher needs a scoped-merge mode.** When the whole run is about one subject,
  the subject token must be **exempt from term-frequency down-weighting** (or matching
  should lean on domain/website/identifiers), otherwise single-subject merges will keep
  collapsing. This is a targeted change in `resolve_entities.py`, not a rewrite.

### Should we add a manager / orchestrator agent? — **Optional; it's the clean home for the above, not a prerequisite.**

A planner ("manager") agent that, given a subject, walks the origin schema graph and
emits the full table-closure + per-entity match/link plan is the natural place to put
schema-driven breadth. It's worth it if you want this to generalize beyond companies —
but you can capture ~80% of the gap with the prompt widening + the matcher scoped-mode
fix first, then decide whether the planner earns its complexity.

---

## Data-Analyst Analysis, Ideas & Suggestions

*Reading the app's output purely as a data analyst — what does the delivered dataset
actually look like, and what would I tell the data owner?*

**Completeness vs the achievable baseline.** The app delivered **0 of 3,427 rows (0%)**.
(Baseline corrected 2026-07-30 from an original 3,185 after fixing a hash-randomization
non-determinism bug in the hand-merge script itself — see
merge-engine-boeing-manual-merge in project memory. The conclusion is unaffected either
way: 0 rows loaded vs a golden baseline in the thousands.)
Even scoring what the pipeline *assembled* before the load crash, it staged 36 company
records + 39 persons across **2 of 11 tables** — and because cross-origin matching
produced **0 merges**, there wasn't even a single Boeing golden record. A business user
asking "tell me everything about Boeing" would receive an empty warehouse.

**Data-quality red flags an analyst would flag immediately:**

- **No unified subject.** origin1-Boeing and origin2-Boeing stayed as two separate rows
  (141 pairs auto-excluded, 0 matched). Any `COUNT(DISTINCT company='Boeing')` is ≥2, and
  the NYSE:BA financial profile (origin2) never joins the legal/founding profile (origin1).
- **Wrong-type revenue.** origin2 `revenue` strings like `"$1 Million"` (from *Boeing
  Employees Tennis Club*) were mapped onto the numeric `revenue` column — the crash cause.
- **Sibling contamination.** The 36 "Boeing" records include employee clubs and small
  unrelated firms; with no golden record, they'd pollute any Boeing-scoped aggregate.
- **Silent field drop.** origin2 `firstName/lastName` weren't in the first-5-record
  mapping sample, so destination-native name columns would have loaded empty.

**Ideas & suggestions (analyst lens):**

1. **Emit a data-quality scorecard per run** — rows loaded / staged / dropped by table;
   % of subject records unified; type-coercion failure count. This run scores 0% loaded.
2. **A "subject unification ratio"** (`unified_records / extracted_total` for the subject)
   — Boeing scored 36/34 ≈ 1.0 (worst case: no consolidation). Track it as a guardrail.
3. **Currency/units normalization at ingest** — parse money strings to `(amount, currency)`;
   never load a money string into a numeric column.
4. **De-dup guardrail before load** — block when the subject entity yields many clusters
   with 0 merges (the matcher's failure signature) and page a human.
5. **Cross-origin identity keys** — lean on domain (`boeing.com`) + ticker (`NYSE:BA`) as
   deterministic match keys for the subject, bypassing the TF-weighted scorer entirely.

## Ranked remediation menu (pick what to implement next)

| # | Fix | File(s) | Closes | Effort |
|---|---|---|---|---|
| 1 | Widen entity enum + rewrite "Company Wildcards"; make `product` conditional | `request_parsing.system.txt`, `entity_aliases.py` | breadth (8 missing families); the product hard-error | S |
| 2 | Scoped-merge matcher mode: exempt the subject token from TF down-weighting / prefer domain+identifier | `resolve_entities.py` | cross-origin match collapse (0/141) | M |
| 3 | Type-coercing load: parse `"$1 Million"`/`"Over 10,000"` etc. to numeric on map/load; per-entity (not all-or-nothing) transactions | `map_fields.py`, `load_data.py`, connectors | the load crash + 0-row rollback | M |
| 4 | Roll back (or defer) schema ALTERs when the load fails | `load_data.py`, `postgresql_destination.py` | permanent schema pollution on failure | S |
| 5 | Sample the unified mapping schema from all records (or a union), not first 5 | `map_fields_node.py` | silently dropped fields (first/last name) | S |
| 6 | Per-attribute conflict policy defaults + a UI/API path that writes it | `unify_entity_group.py`, `api/main.py`, UI | golden-record quality (once #2 makes merges happen) | M |
| 7 | Schema-driven breadth discovery / planner ("manager") agent | new node + `graph.py` | breadth generalised beyond companies | L |

**Recommended order:** 1 → 2 → 3 → 4 → 5, then 6, then decide on 7. Items 1–4 are what
stand between the app and a non-empty, correctly-unified Boeing; 5–7 raise quality and
generality. None of this is implemented yet — awaiting your direction.
