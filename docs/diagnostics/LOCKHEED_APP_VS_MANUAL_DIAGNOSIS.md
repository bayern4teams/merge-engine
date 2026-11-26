# Why the app's Lockheed Martin merge is far worse than the hand-merge

**Date:** 2026-07-30  **Run:** `wf_991c8ef4` — *"merge all information about Lockheed
Martin"*, origin1 + origin2 (ClickHouse) → `global_warehouse` (PostgreSQL),
`full_remerge=true`. **Deliverable:** diagnosis only. The destination was cleaned before
and after; it is back to pristine schema-only (25 tables, 0 rows). No code/prompt changed.

---

## Headline

| | Hand-merge (by me, no app) | The app |
|---|---|---|
| Entity families handled | 9 (company, person, relation, technology, tech-category, industry, location, phone, email) — no finance (source gap) | **2** (company, person) — product demanded but absent |
| Intra-origin1 dedup of the 4 identical "Lockheed Martin Corporation" parents | yes → **1 golden parent** | **no** — 9 records → 9 clusters, 0 merged |
| Golden-record corrections | founded 1995-03-15 (real merger), status→public (NYSE:LMT) | **none** — no golden record was built |
| Per-attribute conflict resolution | yes (union of 4 duplicates) | **0 conflicts** (nothing merged — the weakest of all three runs) |
| Rows written to destination | **555** across 9 tables | **0** — the load **crashed and rolled back** |
| Run outcome | success | **FAILED** |
| Side effect on destination | none | **+15 columns added permanently** + `merge_engine_entity_map` table, despite the rollback |

Lockheed Martin is the hardest of the three subjects — origin1 holds **four duplicate
parent records** that must be deduped, origin2 contributes only a **single all-NULL stub**,
and there are **zero financials anywhere**. The app deduped none of the four, unified
nothing, and crashed at load — this time on a **different** type bug than Boeing/Airbus,
which is itself an important finding.

---

## The pipeline, and where it broke (in order)

### 1. `parse_request` — breadth capped at the first step (ROOT CAUSE, shared)

```
target_entities = ["company", "person", "product"]
entity_tables   = { company: [o1.Company, o2.Company],
                    person:  [o1.Person,  o2.Person], product: [] }
table_selection_notes: "No tables plausibly holding product records were found in
  either origin; other tables represent relationships (e.g., CompanyExperience,
  CompanyIndustry), locations, or classifications."
```

Same 7-type enum. So **relations (136 rows), technographics (294), industries, locations,
contacts, employee-categories — 7 of the 9 families I loaded have no entity type** and are
never extracted. Table picks again came from `source: "heuristic"`, not a considered LLM
selection.

### 2. `extract_data` — the wildcard's `product` is a hard error again

```
DataQualityError (disposition: escalate)
"no source table could be found for entity(s): product"
context.rows_kept = { company: 9, person: 3 }
```

Skipped to continue, as with Boeing and Airbus.

### 3. `resolve_entities` — the four duplicate parents were never deduped (SECOND ROOT CAUSE)

```
company: extracted o1=8 + o2=1 = 9  →  clusters_formed=9
         candidate_pairs_compared=36  auto_merged_clusters=0  conflicts=0
person:  extracted o1=3            →  clusters_formed=3   auto_merged=0  conflicts=0
```

This is the starkest matcher failure of the three. The four origin1 records that are
*byte-for-byte* "Lockheed Martin / Lockheed Martin Corporation / lockheedmartin.com",
differing only by a founding-year, were **each kept as its own company**. 36 pairs
compared, 0 merged, **0 conflicts in the entire run**. The term-frequency backfire is
absolute here — "Lockheed" and "Martin" are in every record, so the only distinguishing
tokens carry ~0 weight and nothing clears the bar. My hand-merge collapsed the four into
one golden parent by exact name+domain and unioned their satellite rows.

Two nuances worth recording:

- The app's **subject scope filter worked correctly on the noise**: of origin2's four
  "Lockheed" rows it pulled only the exact-name stub (`o2=1`) and excluded the unrelated
  "Lockheed Engineering Works" (Indian construction) and "SHPE at Lockheed" (nonprofit).
  That is the one thing it did better than a naive substring match — but the stub it kept
  is all-NULL, so it adds nothing.
- The scope filter also **dropped a real subsidiary**: it extracted 8 origin1 companies,
  not 9 — "Lockheed **Missiles** and Space Company" has no "Martin" token and fell outside
  the subject substring. The hand-merge kept it.

### 4. `unify_entities` — never engaged at all

`total_conflicts = 0` across every entity. No golden record, no survivorship, and none of
the corrections the hand-merge made (founded 1995-03-15; status→public). The app would
have shipped four identical "Lockheed Martin" rows, all wrongly `is_public=False`.

### 5. `map_fields` — 15 brand-new columns

Fewer than Airbus's 21 because origin2's stub is all-NULL, so revenue/employee/phone/rank
columns were never proposed. Still 15 `ADD COLUMN`s (company: industry_category, motto,
logo_url, total_investment, is_public, is_non_profit, is_acquired, is_dissolved, ceo_name,
founder_name, company_type; person: job_title, role, entity_type, confidence_score).

### 6. `load_data` — atomic load crashed on a **different** type mismatch; 0 rows committed

```
invalid input syntax for type bigint: "ba34c3bb1541495295e82f2db9ebe703"
  ...'lockheedmartin.com', '2026-06-15 05:01:18', 'ba34c3bb1541495295e82f2db9ebe703'...
Load failed for 2 entity type(s): company, person.
Nothing was committed for them; watermarks were not advanced.
```

This is the **key new finding**. Boeing and Airbus crashed on a *revenue string* going
into a numeric column. Lockheed's real record has NULL revenue, so it got one row further —
and crashed instead on a **32-char hex identifier being inserted into a `bigint` column**
(the mapper mapped a source `id`/reference field onto a numeric destination key with no
type check). Because the load is one atomic transaction, the entire company+person load
rolled back — **0 rows written**. Two different subjects, two different type-coercion
crashes, same root cause: **the mapper does not reconcile source value types against
destination column types, and the loader is all-or-nothing.**

### 7. Side effect — the schema ALTERs did **not** roll back

The 15 `ADD COLUMN`s committed **permanently** (company 14→25 cols, person 9→13) despite
the failed load, plus a `merge_engine_entity_map` table (26 tables). I dropped all 15
columns + the helper table to restore the pristine 25-table schema.

---

## Answers to the architecture / prompt / manager-agent questions (Lockheed-specific)

### Should we improve the prompts? — **Yes, but Lockheed shows it's the least sufficient here.**

Widening the enum recovers relations/tech/industries/locations/contacts. But even with
perfect breadth, Lockheed would still ship **four un-deduped parents**, **no golden
record**, **wrong `is_public`**, and a **crashed load** — none of which the prompt touches.
For this subject the prompt is necessary but the smallest part of the gap.

### Should we change the architecture? — **Yes; Lockheed is the strongest case for it.**

- **Intra-origin dedup is a first-class need, not an edge case.** The matcher must merge
  four identical records; instead it produced 9 clusters from 9 rows. A scoped-merge mode
  that trusts exact-name+domain (and ignores a differing founding-year) is the fix.
- **Type-aware, per-entity loads.** Lockheed proves the crash is not "the revenue bug" —
  it's a general "source value doesn't fit destination column type" bug, and the atomic
  transaction turns any single bad cell into a total 0-row outcome. Coerce at map/load time
  and isolate failures per entity (or per row).
- **Golden-record overrides / validation.** The hand-merge corrected founded-date and
  public-status from world knowledge; the app has no place to assert "LMT is public" or to
  flag "founded 2012 is implausible for a company with 1990s space heritage."

### Should we add a manager / orchestrator agent? — **Optional, but Lockheed is where it earns its keep.**

A planner that recognises the four parents as one entity (same domain, same legal name)
before matching, walks the FK graph for breadth, and knows the subject is a public
defense prime would close nearly the whole Lockheed gap. Still, the prompt + matcher +
type-coercing-load trio captures most of it first.

---

## Data-Analyst Analysis, Ideas & Suggestions

*Reading the app's output purely as a data analyst — what does the delivered dataset
actually look like, and what would I tell the data owner?*

**Completeness vs the achievable baseline.** The app delivered **0 of 555 rows (0%)**.
Scoring what it staged before the crash: 9 companies + 3 persons across **2 of 9 tables**
(~2% of families), with **no golden record** and **four duplicate "Lockheed Martin" rows**
among the 9. A business user would get an empty warehouse; had the load not crashed, they'd
have gotten a subject that appears four times.

**Data-quality red flags an analyst would flag immediately:**

- **Un-deduped primary key.** Four identical "Lockheed Martin" companies means
  `COUNT(DISTINCT company)` overstates the subject 4×, and any join to relations/tech would
  fan out or split across the four arbitrary IDs. This is the single worst outcome for
  downstream analytics — worse than missing data, because it's *wrong* data that looks real.
- **Incorrect categorical fields.** All four parents carry `is_public=False` and
  founded=2011/2012; the real LMT is NYSE:LMT, formed 1995. Any "public vs private" or
  "company age" cut would be wrong for the subject.
- **Structural NULLs, not incidental.** revenue and employee_count are NULL because *no
  source has them* — an analyst must distinguish "missing because unmerged" (fixable) from
  "missing at source" (a collection gap to escalate to data acquisition).
- **Type-unsafe load.** A hex identifier reaching a `bigint` column is a schema-contract
  violation that a load-time validator should have caught pre-transaction, not discovered
  by Postgres mid-insert.

**Ideas & suggestions (analyst lens):**

1. **Duplicate-key guardrail** — after resolution, assert the subject entity resolves to
   ≤ a small K golden records; 9 clusters / 0 merges for a single subject should hard-fail
   the run with "subject not consolidated," not proceed to load.
2. **Type-contract pre-check** — before the atomic load, validate every mapped
   `(value, dest_column_type)` pair and report violations as a batch (hex→bigint,
   money-string→numeric) instead of crashing on the first one. Turns 2 fatal errors into a
   pre-load quality report.
3. **Source-coverage scorecard** — mark fields NULL-at-source (revenue, employee_count,
   financials for Lockheed) distinctly from NULL-because-unmerged, so the data owner sees
   that Lockheed's financial gap is a *collection* problem, not a pipeline bug.
4. **Golden-record assertion hooks** — allow simple validation rules ("a defense prime with
   space heritage is unlikely to be founded in 2012", "known-public tickers ⇒ status=public")
   to correct or flag obviously-wrong categoricals the way a human reviewer would.
5. **Dedup-rate metric** — track `1 - unified_records/extracted_total` for the subject.
   Lockheed scored 0 (9→9); Airbus 0 (20→20); Boeing ~0 (36→34). A persistent 0 across
   subjects is a clear, trackable signal that scoped-merge matching is broken.

---

## Ranked remediation menu (pick what to implement next)

| # | Fix | File(s) | Closes | Effort |
|---|---|---|---|---|
| 1 | Widen entity enum + rewrite "Company Wildcards"; make `product` conditional | `request_parsing.system.txt`, `entity_aliases.py` | breadth (7 missing families); the product hard-error | S |
| 2 | Scoped-merge matcher mode: exempt subject token from TF down-weighting; trust exact-name+domain so the 4 duplicate parents merge | `resolve_entities.py` | the 9→9 dedup collapse | M |
| 3 | Type-contract validation + coercion at map/load; per-entity (not all-or-nothing) transactions; batch-report violations | `map_fields.py`, `load_data.py`, connectors | the bigint-hex crash + 0-row rollback | M |
| 4 | Roll back (or defer) schema ALTERs when the load fails | `load_data.py`, `postgresql_destination.py` | permanent schema pollution on failure | S |
| 5 | Golden-record validation/override hooks (status, founding plausibility) | `unify_entity_group.py`, UI | wrong categoricals (is_public, founded) | M |
| 6 | Source-coverage vs unmerged-NULL scorecard + subject dedup-rate guardrail | new reconciliation output + UI | observability; catch the 9→9 pattern; separate source gaps | S |
| 7 | Schema-driven breadth + entity-graph planner ("manager") agent | new node + `graph.py` | breadth + duplicate-parent recognition generalised | L |

**Recommended order:** 1 → 2 → 3 → 4, then 5 → 6, then decide on 7. For Lockheed
specifically, **#2 and #3 matter most** — without them the run ships four duplicate,
mis-labelled parents and then loads nothing. None of this is implemented yet — awaiting
your direction.
