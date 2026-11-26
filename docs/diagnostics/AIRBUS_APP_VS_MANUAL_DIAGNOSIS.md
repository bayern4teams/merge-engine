# Why the app's Airbus merge is far worse than the hand-merge

**Date:** 2026-07-30  **Run:** `wf_203f5891` — *"merge all information about Airbus"*,
origin1 + origin2 (ClickHouse) → `global_warehouse` (PostgreSQL), `full_remerge=true`.
**Deliverable:** diagnosis only. The destination was cleaned before and after; it is
back to pristine schema-only (25 tables, 0 rows). No code/prompt was changed.

---

## Headline

| | Hand-merge (by me, no app) | The app |
|---|---|---|
| Entity families handled | 11 (company, person, experience, relation, finance, technology, tech-category, industry, location, phone, email) | **2** (company, person) — product demanded but absent |
| Company entity resolution | 20 origin records → **1 golden Airbus** (SE+SAS+2 origin2 shards unified) + 15 subs | **20 records → 20 clusters, 0 auto-merged** — SE, SAS, every subsidiary and origin2 shard kept **separate** |
| Intra-origin1 SE/SAS unification | yes (SE = holding, SAS folded in) | **never happened** (both kept as distinct companies) |
| Per-attribute conflict resolution | yes (identity←origin1 SE; revenue←origin1 financials EUR 73.4B) | company: **0 conflicts** (nothing merged); person: 7 conflicts by `first_seen`/`completeness` |
| Financial history | 28 rows (1999–2025, EUR) | **0** — finance is not even an entity type |
| Rows written to destination | **2,649** across 11 tables | **0** — the load **crashed and rolled back** |
| Run outcome | success | **FAILED** |
| Side effect on destination | none | **+21 columns added permanently** + `merge_engine_entity_map` table, despite the rollback |

The app produced **nothing** for this request and mutated the destination schema on
the way out — the same end-state as the Boeing run, reached by the same pipeline
failures, plus one Airbus-specific twist: the app's matcher **saw** the many Airbus
records but could not unify a single one of them.

---

## The pipeline, and where it broke (in order)

### 1. `parse_request` — breadth is capped at the first step (ROOT CAUSE, shared with Boeing)

Captured at the deps gate:

```
target_entities = ["company", "person", "product"]
entity_tables   = { company: [o1.Company, o2.Company],
                    person:  [o1.Person,  o2.Person],
                    product: [] }
table_selection_notes: "No product tables found; CompanyExperience and Experience
  contain person-related fields but are relationship/event tables rather than
  person entities."
```

The `request_parsing.system.txt` 7-type enum + "Company Wildcards" rule expands
*"all information about Airbus"* to only `company, person, product`. So **financials
(28 rows, 1999–2025), technographics (2,118), relations (572), industries, locations,
contacts, employee-categories — 8 of the 11 families I loaded have no entity type at
all** and are never extracted. Note the table selections came from `source: "heuristic"`
(alias matching, confidence 0.8), not even a considered LLM table pick.

### 2. `extract_data` — the wildcard's `product` turns "no products" into a hard error

```
DataQualityError (disposition: escalate)
"no source table could be found for entity(s): product"   (repeated 8×)
context.rows_kept = { company: 20, person: 32 }
```

Same as Boeing: the unconditional `product` in the wildcard makes a valid company-only
corpus **fail by default**. I had to choose **Skip** to continue.

### 3. `resolve_entities` — every Airbus record stayed separate (SECOND ROOT CAUSE)

From the reconciliation report:

```
company: extracted o1=7 + o2=13 = 20  →  clusters_formed=20
         candidate_pairs_compared=171  auto_merged_clusters=0  conflicts=0
```

**20 Airbus company records went in; 20 came out.** Airbus SE, Airbus SAS, the five
Defence-and-Space subsidiaries, and all 13 origin2 shards were each left as their own
"unified" record. This is the same term-frequency backfire that sank Boeing — the token
*"Airbus"* is in every record, so its weight collapses to ~0 and the discriminating-
agreement guard rejects the one signal that matters. My hand-merge unified SE+SAS into
one golden parent (same CEO, same founding date) and folded "Airbus Group" (former name
of Airbus SE) and "Airbus Helicopters" ($6.7 B) into it.

Person matching *did* work partially — 32 → 30 clusters, `auto_merged_clusters=2`,
`conflicts=7` — but only origin1 persons were extracted (`extracted_per_origin={o1:32}`),
so this is intra-origin dedup, not cross-origin unification.

### 4. `unify_entities` — company survivorship never engaged; person used a weak rule

`company.conflicts=0` because nothing merged. For persons, the 7 conflicts were resolved
by `conflict_winning_rules = { first_seen: 5, completeness: 2 }` — i.e. "whichever record
we saw first" won 5 of 7 fields, and "longest string" won the other 2. There is no
per-attribute authority (identity←origin1, revenue←financials) of the kind the hand-merge
applied.

### 5. `map_fields` — 21 brand-new columns instead of aligning to the schema

The mapper proposed **21 `ADD COLUMN`s** (company: phone, rank, alexa_rank, revenue_range,
company_type, industry_category, motto, logo_url, tags, total_investment,
investment_currency, is_public, is_non_profit, is_acquired, is_dissolved, ceo_name,
founder_name; person: full_name, description, role, match_confidence). Critically it
mapped origin2's human-readable `revenue` **string** straight onto the numeric `revenue`
column with no coercion — the seed of the load crash.

### 6. `load_data` — atomic load crashed on a type mismatch; 0 rows committed

```
invalid input syntax for type double precision: "$6.1 Million"
  ...'www.airbusdshouston.com', '$6.1 Million'...
Load failed for 2 entity type(s): company, person.
Nothing was committed for them; watermarks were not advanced.
```

origin2's `revenue` is a human string (`"$6.1 Million"`, from *Airbus DS Houston*),
mapped into the numeric `revenue` column. Because the load is one atomic transaction,
the **entire** company+person load rolled back — **0 rows written**. (Airbus's data is
even more crash-prone than Boeing's: its shards carry `"$6.7 Billion"`, `"$51.8 Million"`,
`"$40.1 Million"` — any one triggers the same abort.)

### 7. Side effect — the schema ALTERs did **not** roll back

The 21 `ADD COLUMN`s committed **permanently** (company 14→31 cols, person 9→13) even
though the data load failed, and a `merge_engine_entity_map` table was created (26 tables).
A failed run still mutates the destination schema. (I dropped all 21 columns + the helper
table to restore the pristine 25-table schema.)

---

## Answers to the architecture / prompt / manager-agent questions (Airbus-specific)

### Should we improve the prompts? — **Yes, still the highest-ROI single change.**

The breadth ceiling is 100% the `request_parsing` prompt: 8 of 11 families never had an
entity type, so the app couldn't have loaded Airbus's 28-year financial history,
2,118 technologies, or 572 relations no matter what happened downstream. Widen the enum,
rewrite "Company Wildcards" to expand from the origin schema graph, make `product`
conditional. **But the prompt alone would still leave Airbus with 20 un-merged company
rows and a crashed load.**

### Should we change the architecture? — **Yes; Airbus proves two gaps the prompt can't fix.**

- **The matcher needs a scoped-merge mode.** Airbus is the cleanest possible demonstration:
  171 candidate pairs compared, **0 merged**, because the subject token is in every record.
  In a single-subject run the subject token must be exempt from TF down-weighting, and
  matching should lean on domain/website/identifier + the SE/SAS legal-entity signal.
- **Type-coercing, per-entity loads.** `"$6.7 Billion"` → numeric must happen at
  map/load time, and one bad row must not roll back the whole run. Airbus would have
  loaded *something* if the load were per-entity instead of all-or-nothing.

### Should we add a manager / orchestrator agent? — **Optional; it's the clean home for schema-driven breadth + entity-graph reasoning.**

A planner that walks the origin FK graph around "Airbus" would both (a) pull in the 8
missing families and (b) recognise SE↔SAS↔Defence-and-Space as one corporate group. Worth
it if you want this to generalise, but you can capture ~80% of the Airbus gap with the
prompt widening + matcher scoped-mode + type-coercing load first.

---

## Data-Analyst Analysis, Ideas & Suggestions

*Reading the app's output purely as a data analyst — what does the delivered dataset
actually look like, and what would I tell the data owner?*

**Completeness vs the achievable baseline.** The app delivered **0 of 2,649 rows (0%)**.
Even setting the load crash aside and scoring what the pipeline *assembled* in memory
before load, it staged 20 companies + 30 persons = 50 records across **2 of 11 tables** —
**~1.9% of the entity families** and none of the high-value quantitative data
(financials, technographics, relations). A business user asking "tell me everything about
Airbus" would receive an empty warehouse.

**Data-quality red flags an analyst would flag immediately:**

- **Duplicate-entity explosion.** 20 "Airbus" companies with no golden record means any
  `GROUP BY company` or `COUNT(DISTINCT company)` metric would be inflated ~20× for the
  subject. Revenue/employee roll-ups would be meaningless (they'd sum 20 fragments, or
  more likely sum nothing because revenue failed to load).
- **Scale incoherence.** The origin2 shards range from `$3.2 M` (Airbus UTM) to
  `$6.7 B` (Airbus Helicopters) — a 2,000× spread — all labelled "Airbus". Without
  unification, a dashboard can't tell the €70 B group from a 20-person innovation lab.
- **Wrong-type revenue.** `revenue TEXT` values like `"$6.7 Billion"` are unusable for
  any arithmetic; they must be parsed to a numeric magnitude with a currency dimension.
- **Silent identity loss.** Person conflicts resolved by `first_seen` (5/7 fields) means
  the "winning" attribute is an artifact of scan order, not correctness — non-deterministic
  and unauditable.

**Ideas & suggestions (analyst lens):**

1. **Emit a data-quality scorecard per run** — rows loaded / rows staged / rows dropped,
   by table; % of subject records unified; count of type-coercion failures. This run would
   score 0% loaded and would have surfaced the crash as a red KPI, not an 8-line error log.
2. **A "subject unification ratio" metric** — `unified_records / extracted_total` for the
   subject entity. Airbus scored 20/20 = 1.0 (worst possible); a healthy scoped merge
   should trend toward a small number of golden records. Track it as a guardrail.
3. **Currency/units normalization at ingest** — parse `"$X Billion/Million"` into
   `(amount_numeric, currency)` and store both a raw and a normalized column; never load a
   money string into a numeric column.
4. **Revenue reconciliation** — the hand-merge's golden Airbus carries a defensible
   EUR 73.4 B (2025) from origin1's 28-year financial series; the app carries nothing.
   A simple validation rule ("subject company revenue must be non-null and within an
   order of magnitude of its financial-history max") would catch both the crash and the
   missing-financials gap.
5. **De-dup guardrail before load** — block a load when the subject entity produces > N
   clusters (here 20) with 0 merges; that pattern is the matcher's failure signature and
   should page a human, not silently ship 20 rows.

---

## Ranked remediation menu (pick what to implement next)

| # | Fix | File(s) | Closes | Effort |
|---|---|---|---|---|
| 1 | Widen entity enum + rewrite "Company Wildcards"; make `product` conditional | `request_parsing.system.txt`, `entity_aliases.py` | breadth (8 missing families); the product hard-error | S |
| 2 | Scoped-merge matcher mode: exempt the subject token from TF down-weighting / prefer domain+identifier; recognise SE/SAS legal-entity links | `resolve_entities.py` | the 20→20 company match collapse | M |
| 3 | Type-coercing load: parse `"$6.7 Billion"` etc. to numeric on map/load; per-entity (not all-or-nothing) transactions | `map_fields.py`, `load_data.py`, connectors | the load crash + 0-row rollback | M |
| 4 | Roll back (or defer) schema ALTERs when the load fails | `load_data.py`, `postgresql_destination.py` | permanent schema pollution on failure | S |
| 5 | Per-attribute conflict policy defaults (identity←priority origin, revenue←financials) instead of `first_seen` | `unify_entity_group.py`, `api/main.py`, UI | golden-record quality | M |
| 6 | Data-quality scorecard + subject-unification-ratio guardrail | new reconciliation output + UI | observability; catch the 20→20 pattern | S |
| 7 | Schema-driven breadth discovery / planner ("manager") agent | new node + `graph.py` | breadth + entity-graph unification generalised | L |

**Recommended order:** 1 → 2 → 3 → 4, then 5 → 6, then decide on 7. Items 1–4 are what
stand between the app and a non-empty, correctly-unified Airbus; 5–6 raise quality and
observability; 7 generalises. None of this is implemented yet — awaiting your direction.
