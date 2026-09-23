# Ground-Truth Manual Build Guide — 5 Company Families → `ground_truth.kg`

**What this is.** A complete, self-contained operating manual for an agent to rebuild
the ground truth in `postgresql://localhost:5434/ground_truth`, schema `kg`, **without
running the merge-engine app**. The agent does, by hand, in SQL, exactly what the app's
pipeline does: discover → scope/extract by FK chain → entity-resolve → unify/deduplicate
→ field-map → load → verify. Reference implementations of the same procedure (single
company, older destinations): `scripts/goldens/boeing_manual_merge.py`,
`airbus_manual_merge.py`, `lockheed_manual_merge.py`.

**Decisions locked by the user (2026-09-16):**
1. **Rebuild from scratch** — truncate `kg` fact/entity tables first; keep only the
   reference vocabularies (`taxonomy*`, `fact_predicate`, `relationship_type`).
2. **Seed families + 3-hop chain expansion.** Hop companies are real entities (full
   attributes extracted); neighbors beyond hop 3 are NOT entered.
3. **Persons/contacts: no cap.** Every person linked to any matched company is extracted.

**Seeds:** Boeing, Airbus, Thuraya, OHB SE, Capella Space.

---

## 0. Environments (verified live 2026-09-16)

| Role | Connection | Notes |
|---|---|---|
| Origin 1 (diffbot) | `postgresql://postgres:***@localhost:5433/testdiffbot`, schema `diffbot` | 13 tables |
| Origin 2 (zoominfo) | ClickHouse `http://localhost:8125`, user `postgres`, password `***`, database `testzoominfo` | 7 tables |
| Destination | `postgresql://postgres:***@localhost:5434/ground_truth`, schema `kg` | 250 tables incl. `_p0.._p15` partitions |

Client access used throughout:

```bash
export PGPASSWORD=supersecu...base -Atc "<sql>"
# ClickHouse (HTTP):
curl -s 'http://localhost:8125/?user=postgres&password=***&database=testzoominfo' \
  --data-binary "<sql>"
```

### 0.1 Origin 1 schema (`diffbot`, PostgreSQL) — all text ids, no declared FKs

| Table | Rows | Key columns (join keys in **bold**) |
|---|---|---|
| company | 3,940 | **companyid**, entityname, fullname, website, description, summary, alldescriptions, types, motto, foundingdate (`d`-prefixed), totalinvestment, totalinvestmentcurrency, ispublic, isnonprofit, isacquired, isdissolved, ceoname, foundername, logo, sourcecategory, metadata_confidence, explicit_origins |
| companycontact | 5,330 | **companyid → company**, contactid, contactstring, contacttype (`OFFICE`/`PROFESSIONAL`/`None`) |
| companyemployeecategory | — | **companyid**, empcatid, category, firsthiredate, confidence |
| companyexperience | — | **companyid**, **personid → person**, personname, role (`CEO`/`Founder`/`BoardMember`/…), types, confidence |
| companyfinancials | — | **companyid**, financialid, year (int), amount (bigint), currency, iscurrent, confidence |
| companyindustry | — | **companyid**, **classificationid → industryclassification** |
| companyinvestmentround | — | **companyid**, investmentid, date, amount, currency, confidence, explicit_origin |
| companyinvestor | 1,260 | **investmentid → companyinvestmentround**, **investorcompanyid (100% populated → company)**, surfaceform, summary, types, confidence |
| companyrelation | 6,174 | **companyid**, **relatedcompanyid**, relationtype ∈ {partnership 3397, subsidiarie 883, supplier 762, competitor 579, customer 553}, surfaceform, summary, types, confidence, explicit_origin |
| companytechnographics | — | **companyid**, techid, technologyname, technologytype, categories, confidence |
| industryclassification | — | **classificationid**, name, isprimary, confidence |
| location | 3,506 | **companyid**, locationid, street, addressfull, city, region, postalcode, country, latitude, longitude, precision, isprimary, iscurrent |
| person | 1,215 | **personid**, name, summary, role, types, confidence |

**No FK constraints exist in `diffbot`** — the joins above are the *de-facto* graph,
confirmed column-by-column. Do not rely on `information_schema` here; use this table.

### 0.2 Origin 2 schema (`testzoominfo`, ClickHouse)

| Table | Rows | Key columns |
|---|---|---|
| Company | 1,270,245 | **companyID** (String), companyName, companyDescription, companyDomain, website, companyEmployeeCountRange, companyPhone, companyRevenue ("$61.1 Billion"), companyRevenueIn000s (Int64), companyEmployees (Int64), companyRevenueRange, companyTicker, companyType, `_version` (DateTime) |
| CompanyIndustry | 3,696,483 | **companyID → Company**, **industryID → Industry**, isPrimary |
| Experience | 12,426,620 | **personID → Person**, **companyID → Company**, title, jobTitle, personHashtags |
| Industry | 37 | **industryID**, name, displayName |
| Location | 1,833,568 | **locationID**, Street, City, State, Zip, CountryCode, **companyID — Int64, not String!** |
| Person | 11,799,192 | **personID**, name, firstName, lastName, middleInitial |
| PersonContact | 4,502,966 | **personID**, freeMails (String; comma-separated emails; can be empty) |

Verified quirks:
- `Location.companyID` is `Int64` while `Company.companyID` is `String` — always join
  with `toString(l.companyID) = c.companyID`; a naive `WHERE companyID = '5128546'`
  returns 0 rows (type mismatch silently yields empty).
- ZoomInfo "primary key" is **not unique** — never trust it; ClickHouse's declared key
  is a sorting key only. Use `SELECT DISTINCT` / `GROUP BY` for identity work.
- `PersonContact.freeMails` for a person can be `''` — treat empty as no contact.
- Seed company IDs confirmed present: `5128546` Boeing, `345715253` Thuraya
  (thuraya.com), `28656225` OHB System (ohb.de), `398000639` Capella Space,
  `365384179` Airbus Helicopters, `1115568140` Airbus Group. Main "Airbus" entity is
  found by domain `airbus.com`, not by an exact name row.

### 0.3 Destination schema (`kg`, PostgreSQL) — the bitemporal KG contract

Core model (full 250-table inventory confirmed; partitioned tables are declarative
`PARTITION OF` children — always insert into/select from the **parent**):

| Table | Shape | Notes |
|---|---|---|
| `entity` | `entity_id uuid PK, entity_type text, created_at, deleted_at` | Supertype. `entity_type ∈ {COMPANY, PERSON, …}` |
| `organization` | `organization_id uuid PK → entity, organization_code` | Supertype of company |
| `company` | `company_id uuid PK` — **id column only** | FK → `organization` **and** → `entity`; trigger `assert_entity_type('COMPANY','company_id')` |
| `person` | `person_id uuid PK → entity` | id only |
| `entity_name` | `item_id uuid, entity_id, name, normalized_name, language_code, name_type_term_id, valid_from, valid_to, date_precision` | trigram index on `normalized_name` |
| `knowledge_item` | `item_id uuid, item_type, source_id → source, observed_at, confidence, extractor_name/version, supersedes_item_id, deleted_at` | **every** fact/relationship/row is a "item" with provenance |
| `knowledge_fact` | `item_id uuid PK → knowledge_item, subject_entity_id → entity, predicate_id → fact_predicate, value_kind, value_text/numeric/date/boolean/code/…, currency_id, normalized_amount, valid_from, valid_to, date_precision` | the only home for attributes |
| `company_company_relationship` (+`_p0.._p15`) | `item_id uuid, company_a_id, company_b_id, relationship_type_id, valid_from, valid_to, date_precision, description` | FKs → `company` |
| `person_company_relationship` (+ parts) | `item_id, company_id, person_id(?), relationship_type_id, …` | |
| `graph_edge` (+ parts) | `edge_id, source_entity_id, target_entity_id, relationship_type_id, relationship_item_id, valid_from, valid_to, confidence, is_reverse_projection` | materialized projection of relationships; symmetric types need a reverse row with `is_reverse_projection=true` |
| `source` | `source_id bigint PK, source_type_term_id, uri, title, …` | |
| `evidence` | `evidence_id uuid PK, source_id, evidence_type_term_id, text_snippet, locator, …` | |
| `knowledge_item_evidence` | `(item_id, evidence_id, is_primary)` | links items to evidence |
| `fact_predicate` | 23 canonical rows (`company.legal_name` … `company.ticker_symbol`) | see §8 |
| `relationship_type` | ids 58–81 (`OWNS`, `SUBSIDIARY_OF`, `INVESTS_IN`, `PARTNERS_WITH`, `SUPPLIES_TO`, `CUSTOMER_OF`, `COMPETES_WITH*` offering-level, `WORKS_FOR`, `FOUNDED`, `LEADS`, `BOARD_MEMBER`, …) | symmetric: `CONTRACTS_WITH`, `PARTNERS_WITH`, `CO_FOUNDED`, `CO_WORKED_WITH`, `COLLABORATED_ON`, `COMPETES_WITH`, `SIMILAR_TO` |
| `taxonomy` / `taxonomy_term` | 106 terms | name types: `PREFERRED`(37) `LEGAL`(38) `TRADE`(39) `FORMER`(40) `ACRONYM`(41); evidence types `TABLE_ROW`(35); source type `DATABASE`(29) |
| `schema_version` | | leave alone |

**There is NO `kg.location` table in `ground_truth`** (the `destination` DB has a stub).
All location data lands as `knowledge_fact`s (§8). Never invent destination columns —
the schema is a fixed contract.

---

## 1. Non-negotiable rules (each one is a scar the project already paid for)

1. **Deterministic ids only.** Cluster/entity UUIDs =
   `uuid5(NAMESPACE_URL, 'kg:company:' || sha256(sorted_member_record_ids))` — never
   random `gen_random_uuid()` per run. Random ids made identical re-runs duplicate every
   destination row (docs/architecture/data_pipeline.md §7).
2. **Never silently drop rows.** Every candidate pair considered, every extraction
   counted against the source count. A run that "succeeds" with fewer rows than the
   source had is a failure to explain, not a success.
3. **Over-merging is worse than splitting.** Borderline pairs stay SEPARATE (one entity
   per source record) and get flagged for human review in the audit table — never
   auto-merged.
4. **Declared unique identifiers veto a merge.** Different `website` roots, different
   `ticker`, different explicit company ids ⇒ do not merge no matter how similar names
   are ("Al Thuraya Rent A Car" ≠ "Thuraya").
5. **Sorted, deterministic intermediate lists.** Python `set()` iteration order changes
   per process (PYTHONHASHSEED) and silently changed a golden script's output
   (151 vs 255 rows). Always `sorted(set(...))`.
6. **Independent verification only.** A step is done when an independent SQL check
   confirms it — never "the insert didn't error".
7. **Load order is FK order** (§9): `source → evidence → entity → organization/person
   subtype → entity_name → knowledge_item → knowledge_fact → relationships → graph_edge
   → audit`.
8. Work only against the three listed connections. Never write to the two origins.

---

## 2. Stage A — Rebuild the destination from scratch

### 2.0 Database-default reference data — NEVER truncate these tables

These rows are the schema's seeded vocabulary/defaults (verified live 2026-09-16:
exact counts below). They are the controlled vocabularies every FK in §9 points at —
truncating one silently breaks the load and destroys ids other evidence cites
(e.g. name_type 37, evidence type 35, source type 29). Truncating any of them is a
build-killing mistake:

| Table | Rows (must stay) | What it is |
|---|---|---|
| `kg.taxonomy` | 19 | the 19 taxonomy trees (industry, name_type, evidence_type, …) |
| `kg.taxonomy_term` | 106 | every term other tables FK to by id (37 PREFERRED … 106 CONTRACT_TERMINATED) |
| `kg.taxonomy_allowed_entity_type` | 9 | which entity types each taxonomy accepts |
| `kg.fact_predicate` | 23 | canonical predicates (ids 1–23; §8 ADDS rows, never removes) |
| `kg.relationship_type` | 24 | ids 58–81; §7's open COMPETES_WITH decision may ADD a row |
| `kg.event_type` | 25 | event vocabulary |
| `kg.currency` | 14 | ISO 4217 rows (USD/EUR/…, cols: `currency_id, iso_code, name, numeric_code, minor_units`) — required by `knowledge_fact.currency_id` / `normalized_currency_id` |
| `kg.schema_version` | 1 | migration ledger row (`version=1, 'Initial canonical knowledge/graph-ready schema'`) — leave exactly as is |

(`kg.embedding_model` is part of the schema but currently 0 rows — nothing to preserve,
nothing to truncate. `kg.entity_embedding` holds derived vectors keyed to entities; it
is emptied by the CASCADE below, which is correct — embeddings are not default data.)

### 2.1 Database-default COLUMNS — do not fight them, do not null them

These columns carry DB-level defaults. The agent must **omit them** from inserts unless
the guide explicitly says to supply a value; writing an explicit `NULL` into one fails
the NOT NULL, and copying values from an old dump would override determinism:

| Column(s) | Default | Rule for this build |
|---|---|---|
| `entity.entity_id`, `knowledge_item.item_id`, `entity_name.item_id`, `graph_edge.edge_id`, `evidence.evidence_id`, `event_participant.item_id` | `uuidv7()` | **Exception, deliberate:** §6.4 requires deterministic `uuid5(...)` ids — supply them explicitly so re-runs upsert instead of duplicate. Everywhere else, let the default fire. |
| `created_at`, `observed_at` | `now()` | never set manually |
| `date_precision` (`knowledge_fact`, `entity_name`, all relationship tables, `entity_taxonomy_membership`) | `'DAY'` | omit unless the value is year/month-precision (founding dates with `XX`, financial years) — then set `'YEAR'`/`'MONTH'` |
| `knowledge_item.confidence` | `0.50000` | **always set explicitly** (0.9 origin-sourced / 0.85 judgment, §9.4) — the 0.5 default would silently downgrade every fact |
| `knowledge_item_evidence.is_primary`, `graph_edge.is_reverse_projection` | `false` | set only where §9 says otherwise |
| `fact_predicate.is_multi_valued` | `false` | set `true` for repeatable raw predicates (e.g. `person_free_mails`, yearly revenue facts) |

### 2.2 The truncate

Note the CASCADE blast radius: truncating `entity … CASCADE` also empties every table
whose FK points at it — including ones not listed here (`offering`, `contract`,
`person_education`, `event_participant`, all `_p0.._p15` partition children). That is
correct for a from-scratch rebuild; none of them hold default data. What CASCADE can
NOT touch is §2.0's vocabularies (nothing references `entity` from them, and they must
not be added to the list).

```sql
-- psql -h localhost -p 5434 -U postgres -d ground_truth
BEGIN;
TRUNCATE TABLE
  kg.knowledge_fact, kg.knowledge_item_evidence, kg.knowledge_item,
  kg.entity_name, kg.graph_edge,
  kg.company_company_relationship, kg.person_company_relationship,
  kg.person_person_relationship, kg.company_offering_relationship,
  kg.offering_offering_relationship, kg.person_offering_relationship,
  kg.contract_offering, kg.contract_party, kg.entity_taxonomy_membership,
  kg.event, kg.event_participant, kg.person_education,
  kg.offering, kg.contract, kg.evidence,
  kg.company, kg.person, kg.organization, kg.institution, kg.entity_embedding,
  kg.entity, kg.source
RESTART IDENTITY CASCADE;
COMMIT;
-- Post-check: the §2.0 tables must still carry their exact counts
-- (19/106/9/23/24/25/14/1). Assert before proceeding:
SELECT (SELECT count(*) FROM kg.taxonomy),
       (SELECT count(*) FROM kg.taxonomy_term),
       (SELECT count(*) FROM kg.taxonomy_allowed_entity_type),
       (SELECT count(*) FROM kg.fact_predicate),
       (SELECT count(*) FROM kg.relationship_type),
       (SELECT count(*) FROM kg.event_type),
       (SELECT count(*) FROM kg.currency),
       (SELECT count(*) FROM kg.schema_version);
-- expect: 19 | 106 | 9 | 23 | 24 | 25 | 14 | 1 — any other number = restore the
-- pg_dump from §12 step 0 and stop.
```

Register the two origins (fixed ids used everywhere below):

```sql
INSERT INTO kg.source (source_id, source_type_term_id, uri, title)
VALUES (1, 29, 'postgresql://localhost:5433/testdiffbot',
        'origin1: PostgreSQL testdiffbot (diffbot schema)'),
       (2, 29, 'clickhouse://localhost:8125/testzoominfo',
        'origin2: ClickHouse testzoominfo')
ON CONFLICT (source_id) DO NOTHING;
```

(`source_id` is a `bigint` without a sequence — insert explicit ids and keep a
running max for analyst-judgment sources, e.g. 3 = manual merge judgment.)

---

## 3. Stage B — Seed identification (do this before any extraction)

For each seed, find ALL records in both origins that denote the seed's own company,
and pin the canonical record. `ILIKE` on name alone is not enough — it also returns
unrelated names ("OHB" matches "NohBell" in zoominfo substring search; "thuraya"
matches "Al Thuraya Rent A Car"). Apply the word-boundary + domain rules:

```sql
-- origin1 seeds (PostgreSQL): name-word match, to be manually adjudicated
SELECT companyid, entityname, fullname, website
FROM diffbot.company
WHERE entityname ~* '\y(boeing|airbus|thuraya|ohb|capella)\y'
   OR fullname  ~* '\y(boeing|airbus|thuraya|ohb|capella)\y';
```

```sql
-- origin2 seeds (ClickHouse): word-boundary regex, NOT bare substring
SELECT companyID, companyName, companyDomain
FROM Company
WHERE match(companyName, '(?i)\\b(boeing|airbus|thuraya|ohb|capella)\\b')
   OR match(companyDomain, '(?i)^(www\\.)?(boeing\\.com|airbus\\.com|airbus\\.se|thuraya\\.com|ohb\\.de|capellaspace\\.com)');
```

Adjudication rules (record the decision as an evidence row, type 31/MANUAL):
- A seed record must have the seed's **official domain** or exact legal name
  (e.g. Boeing: domain `boeing.com` → `5128546`, `EMAakygGHMUiRPfT6DPOr6Q`;
  OHB SE: diffbot `Ei7Rdy8jOPwSaA07eN7gnpw`; OHB System: zoominfo `28656225`, domain
  `ohb.de`; Capella Space: `Eab4Feq-pPeSUpmSKYuToDA` / `398000639`; Thuraya:
  `EW-cM595xNjS5v41pCDvgwQ`, `Eif_jUgzxOoWHqb_7EphYeQ`, `345715253`; Airbus family
  roots: `EXRo_XwecMo6TPp_x5JybaA` (Airbus SAS), `Ec_P38BE4MX6mRCDh9NWh_A` (Airbus SE)).
- Explicit false positives to exclude: `Capella University` (capella.edu),
  `Capella Graphics`, `Monterey Airbus` (a restaurant), `Al Thuraya Rent A Car`,
  `Al Thuraya Security`, `althuraya-usa.com`, `Thuraya (Kazakhstan reseller)` only if
  its domain isn't thuraya.com, all `*Sohbet*`/`NohBell`-type OHB substring noise.
  A near-name is only excluded from the *family root* — if it is its own distinct real
  company it may still enter via a 1-hop relation edge as a stub.
- Output of Stage B = `SEED_ROOTS`: a checked table the rest of the run joins against.
  Write it to a scratch schema (e.g. `gt_build.seed_roots` in ground_truth, drop at the
  end) with columns `(family text, origin text, record_id text, rationale text)`.

---

## 4. Stage C — Chain expansion (3 hops) — the "chain of ids" step

The graph to walk, by edge (all verified to exist):

| Edge | Origin | Join |
|---|---|---|
| company↔company | diffbot | `companyrelation.companyid / relatedcompanyid` |
| company→company (investment) | diffbot | `companyinvestor.investorcompanyid` ←joined on→ `companyinvestor.investmentid = companyinvestmentround.investmentid`, round's `companyid` = investee |
| person→company (employment) | zoominfo | `Experience.personID / companyID` |
| person→company (role) | diffbot | `companyexperience.personid / companyid` |

Company closure is over the **two company-company edge sets** (rows 1–2). Persons are
attached to family companies (rows 3–4), they do not expand the company closure
(except that a diffbot person discovered via one family company may link a family
company *and* an employer outside the family — do NOT follow that second employer).

Pseudocode (run as SQL with a `gt_build.company_closure` working table):

```sql
-- level 0: family roots + family-name companies (a name match IS a family member,
-- e.g. all the "Boeing *"/"Airbus *" subsidiaries)
INSERT INTO gt_build.company_closure(family, origin, company_id, hop)
SELECT family, origin, company_id, 0 FROM gt_build.seed_roots
UNION
SELECT f.family, 'diffbot', c.companyid, 1
FROM gt_build.families f, diffbot.company c
WHERE c.entityname ~* ('\y' || f.name_pattern || '\y')
   OR c.fullname   ~* ('\y' || f.name_pattern || '\y');
-- (same shape for zoominfo with the (?i)\b regex)

-- hops: repeat 3 times (h=1..3), expanding ONLY via company-company edges:
INSERT INTO gt_build.company_closure
SELECT d.family, d.origin, edge.next_id, d.hop + 1
FROM (SELECT DISTINCT family, origin, company_id, hop FROM gt_build.company_closure
      WHERE hop = :h) d
JOIN <edge view> edge ON edge.from_id = d.company_id
WHERE NOT EXISTS (SELECT 1 FROM gt_build.company_closure x
                  WHERE x.origin = edge.to_origin AND x.company_id = edge.next_id)
  AND d.hop < 3;
```

Rules:
- Dedupe on `(origin, company_id)` globally — a company reached from two families keeps
  both `family` rows but must become ONE destination entity (§6).
- A hop company that is itself a name match (e.g. "Boeing Australia" reached via a
  relation edge) is hop 1 by name already — keep the minimum hop.
- 1–3 hop companies are **full entities**: extract all their child rows too
  (contacts, locations, financials, industries, tech, rounds, experience).
  Neighbor ids that appear ONLY as an edge endpoint and are absent from `Company`
  (dangling id) are recorded as unresolved-link evidence, not as entities.
- Expected magnitudes (measured): hop-1 alone adds ~559 distinct diffbot companies for
  Boeing. If total closure exceeds ~5,000 companies per family, STOP and report —
  that means the edge walk is leaking (usually: following a hub like a bank/PE firm
  that every portfolio company relates to; keep hubs as stub entities only).
- After closure, **cross-join the two origins within the closure** — a diffbot hop
  company whose normalized name/domain matches a zoominfo closure (or even non-closure)
  company makes the zoominfo one a closure member too (this is how zoominfo's
  `OHB System 28656225` joins the OHB family whose diffbot root is `Ohb Se`).

---

## 5. Stage D — Extraction (pull everything attached to the closure)

For every id in `gt_build.company_closure`, extract from BOTH origins. This is the
app's `extract_data` step; the join fan-out list is exhaustive — nothing attached may
be skipped:

**From `diffbot` (parameterize `:ids` = closure diffbot ids, chunk IN-lists ≤ 1,000):**

```sql
SELECT * FROM diffbot.company              WHERE companyid IN :ids;
SELECT * FROM diffbot.companycontact       WHERE companyid IN :ids;
SELECT * FROM diffbot.location             WHERE companyid IN :ids;
SELECT * FROM diffbot.companyfinancials    WHERE companyid IN :ids;
SELECT * FROM diffbot.companyindustry      WHERE companyid IN :ids;
SELECT * FROM diffbot.companyinvestmen...N  WHERE companyid IN :ids;
SELECT * FROM diffbot.companytechnographicsWHERE companyid IN :ids;
SELECT * FROM diffbot.companyemployeecategory WHERE companyid IN :ids;
SELECT * FROM diffbot.companyrelation      WHERE companyid IN :ids OR relatedcompanyid IN :ids;
SELECT * FROM diffbot.companyexperience    WHERE companyid IN :ids;
-- second-order ids, then fetch their parents:
SELECT DISTINCT investorcompanyid FROM diffbot.companyinvestor    WHERE investmentid IN :inv_ids;
SELECT DISTINCT classificationid  FROM diffbot.companyindustry     WHERE companyid IN :ids;  -- then industryclassification
SELECT DISTINCT personid          FROM diffbot.companyexperience   WHERE companyid IN :ids;  -- then diffbot.person
SELECT DISTINCT relatedcompanyid  FROM diffbot.companyrelation     WHERE companyid IN :ids;  -- (already in closure by Stage C)
```

**From `testzoominfo`:**

```sql
SELECT * FROM Company          WHERE companyID IN :zi_ids;
SELECT * FROM CompanyIndustry  WHERE companyID IN :zi_ids;
SELECT * FROM Experience       WHERE companyID IN :zi_ids;          -- no cap
SELECT * FROM Location         WHERE companyID IN :zi_ids;          -- Int64! cast
SELECT * FROM Industry         WHERE industryID IN :zi_ind_ids;
SELECT * FROM Person           WHERE personID IN :zi_person_ids;    -- from Experience
SELECT * FROM PersonContact    WHERE personID IN :zi_person_ids;
```

Extraction discipline (from the app's connector layer):
- ClickHouse reads: `SELECT` with an explicit `ORDER BY` on the id columns for
  determinism; never paginate a big table with `OFFSET`; the `IN` lists must be built
  from **sorted** sets (rule 5).
- Pull `Person`/`PersonContact` only for `personID`s reachable from `Experience` rows
  of closure companies — that is what "everything related" means for persons.
- Record counts per (origin, table) into `gt_build.extract_ledger`. **Gate: every
  closure id must appear in the `Company`/`company` extract.** Any closure id missing
  from its own origin's company table is a bug — fix before continuing.

---

## 6. Stage E — Entity resolution (dedup within origin, match across origins)

Goal: each real-world company/person becomes exactly one destination entity, backed by
a set of `(origin, record_id)` members.

**6.1 Within-origin dedup.** Same origin, two records, same company ⇒ merge
(diffbot has e.g. three "Airbus Defence and Space" variants, two "Boeing" rows,
"Boeing Co. The"/"The Boeing Company"). Key order:
1. identical normalized domain, or
2. identical normalized name (`lower`, strip `.,` and legal suffixes
   inc|ltd|llc|corp|corporation|limited|company|gmbh|se|sas|sa|spa|proprietaries|pty|
   the, collapse whitespace), or
3. name containment + same country-of-HQ signal.
Veto: different official domain ⇒ keep split.

**6.2 Cross-origin match.** Same normalized name (or one is a translation/abbreviation
of the other) AND (same domain root OR both public with same ticker) ⇒ merge with
confidence 0.98. Domain-only match ⇒ 0.92 with name-similarity ≥ 0.8. Everything below
⇒ DO NOT merge (rule 3); emit to `gt_build.borderline` for the report.
Work cluster-by-cluster per family (a few hundred pairs each) — brute-force all-pairs
inside a family is fine at this scale and is exactly what the Fellegi–Sunter scorer
does after blocking; blocking here = same family + first-token or domain block.

**6.3 Clustering.** Union-find over accepted pairs, then two repairs (mirroring
`resolve_entities.py`):
- **Repair:** if a component contains two members with conflicting unique ids
  (domains/tickers) → split and demote the weakest edge to borderline.
- **Cohesion cut:** if a component's weakest edge is a domain-only link, and the two
  endpoints' names are different families (e.g. an investment hub), split.

**6.4 Entity ids.** For each final cluster:
```
entity_id = uuid5(NAMESPACE_URL,
             'merge-engine:kg:' || entity_type_lower || ':' ||
             sha256(array_to_string(sort(member_ids), '|')))
```
Members sorted as `origin:record_id` strings. This is reproducible: same input data ⇒
same UUIDs, re-running the whole build is idempotent (upserts, no duplicates).

**6.5 Audit.** Every merge decision (accepted AND rejected borderline) goes to
`kg.merge_engine_merge_audit` — create it first if rebuilding from scratch truncated
it (it exists in `destination`; same shape):
`(run_id, entity_type, cluster_id, record_a, record_b, confidence, method, evidence jsonb, decided_by, human_override)`,
and every member mapping to `kg.merge_engine_entity_map`
(`entity_type, record_id, cluster_id, first_seen_run`) so any ground-truth entity can
be traced back to raw source rows.

---

## 7. Stage F — Field mapping (origin → `kg`) — fixed, no new columns

Destination has no attribute columns: every value becomes a `knowledge_fact` behind a
`knowledge_item`, and names become `entity_name` rows. Canonical mapping table (do not
invent predicates beyond these + the registered raw ones §8):

| Source field(s) | Destination |
|---|---|
| diffbot `entityname` / zoominfo `companyName` | `entity_name` (name_type 37 PREFERRED; zoominfo legal variant → 38 LEGAL) |
| diffbot `fullname` | `entity_name` (38 LEGAL) + `company.legal_name` fact |
| diffbot `website` / zi `companyDomain`,`website` | `company.website` (normalized: strip scheme/www/trailing-slash) |
| diffbot `foundingdate` (`d1916-07-15`, `XX`=unknown part) | `company.founded_date` DATE; `XX`→`01`, `date_precision='YEAR'` when month/day were XX; drop non-`d` values to raw fact |
| zi `companyEmployeeCountRange` ("Over 10,000", "100 - 250") | `company.employee_count` NUMERIC: `Over N`→N; `a - b`→(a+b)/2 |
| zi `companyRevenue` ("$61.1 Billion") | `company.revenue` MONEY: strip `$`/commas, Billion=1e9, Million=1e6 |
| zi `companyRevenueIn000s` | `company.revenue` ×1000 (prefer explicit `companyRevenue` when both present) |
| zi `companyTicker` | `company.ticker_symbol` TEXT; presence ⇒ `company.status`=ACTIVE/public |
| diffbot `ispublic` ('True'/'False') | `company.status` CODE (public/private) + taxonomy 5 PRIVATE when False |
| diffbot `description`,`summary`,`alldescriptions` / zi `companyDescription` | `company.description` — one fact, longest non-empty wins |
| diffbot `motto`,`ceoname`,`foundername`,`types`,`logo`,`sourcecategory`,`totalinvestment`,`isnonprofit`,`isacquired`,`isdissolved`,`metadata_confidence` | registered raw predicates `company_motto` etc. (see §8) |
| diffbot `location.*` | raw predicates `company_location_city` etc. per (company,locationid) — or one composite `company.headquarters_country` CODE when `isprimary=true` |
| zi `Location.*` | same raw-predicate path |
| diffbot `companycontact` OFFICE | `company_phone` raw predicate; PROFESSIONAL → `company_contact_email` raw predicate; `None` → raw predicate with type in value |
| zi `Company.companyPhone` | `company_phone` |
| zi `PersonContact.freeMails` (comma-split, drop `''`) | person fact `person_email` raw predicate |
| zi `Person.firstName/lastName/middleInitial` + `name` | person `entity_name` (37) |
| diffbot `person.name/role/summary` | person `entity_name` (37) + `person_role`/`person_summary` raw predicates |
| diffbot `companyexperience.role` | relationship type: `CEO`→LEADS(69), `Founder`→FOUNDED(68), `BoardMember`→BOARD_MEMBER(70), else WORKS_FOR(67) |
| zi `Experience.title/jobTitle` | WORKS_FOR(67) relationship; title into the relationship row's `description` |
| diffbot `companyrelation.relationtype` | `subsidiarie`→SUBSIDIARY_OF(60) (companyid is the subsidiary? verify orientation against `surfaceform` text!), `partnership`→PARTNERS_WITH(62), `supplier`→SUPPLIES_TO(63), `customer`→CUSTOMER_OF(64), `competitor`→COMPETES_WITH — NOTE `COMPETES_WITH(80)` is OFFERING-level only; use PARTNERS_WITH(62)+description='competitor (source: diffbot)' OR register a COMPANY-level COMPETES_WITH relationship_type row — flag as an open vocabulary decision in the report, do not silently misuse 80 |
| diffbot investor edge | INVESTS_IN(61): investorcompanyid → investee companyid |
| diffbot `companyindustry` / zi `CompanyIndustry` | `entity_taxonomy_membership` rows (taxonomy term for industry name if present in `taxonomy_term`; else raw predicate `company_industry_<n>`) + industry name kept as fact |
| diffbot `companyinvestmentround` | INVESTS_IN facts: amount/currency/date as facts on the investor→investee relationship item if the table carries `description`; else raw predicates `company_investmentround_*` on the investee |
| diffbot `companyfinancials` (year, amount, currency) | `company.revenue` MONEY facts with `valid_from`=year-01-01, `valid_to`=year-12-31, one fact per year |

**Unmappable field ⇒ `gt_build.unmapped_fields` report row. NEVER create a destination
column.**

---

## 8. Registering raw predicates (the app's own escape hatch)

`fact_predicate` currently has 23 canonical rows. The app's loader extended it with
per-source raw predicates (`company_companyid`, `company_fullname`, … ids 55–8x in
`destination.kg`). Do the same here — for any source field with no canonical predicate,
insert `fact_predicate` rows named `<entitytype>_<lowercased_source_field>`
(e.g. `company_motto`, `company_technologyname`, `person_free_mails`) with
`value_kind` from the value (TEXT/NUMERIC/DATE/BOOLEAN). Record every registration in
the build report; keep canonical predicates preferred when both would fit.

Value coercion rules (from `value_coercion.py`):
- `''` ⇒ NULL for any non-text kind.
- Diffbot `d`-dates: strip `d`, `XX`→`01`, `date_precision` accordingly.
- Money strings as §7; `totalinvestment` bigint stays NUMERIC with currency fact.
- Numeric-looking text ids (`companyid`) stay TEXT facts — never coerced to numeric.

---

## 9. Stage G — Load (strict FK order, transactional per family)

For each family, one transaction. Order inside it:

1. **entity** — `INSERT ... ON CONFLICT (entity_id) DO NOTHING` with
   `entity_type='COMPANY'|'PERSON'`, `created_at=now()`.
2. **organization** (per COMPANY), then **company** — order matters (triggers + FKs).
   `person` after `entity`.
3. **entity_name** — one row per distinct observed name per cluster; `item_id =
   uuid5(NS,'kg_name:'||entity_id||':'||name)`; `language_code='en'`;
   `normalized_name` = the §6.1 normalizer.
4. **knowledge_item** — one per fact-bearing source record-field group: `item_type =
   'FACT'|'RELATIONSHIP'`, `source_id` 1/2/3, `observed_at = now()`, `confidence` =
   source-record confidence (parse diffbot `confidence` text; default 0.9 origin-sourced,
   0.85 analyst-judgment), `extractor_name='manual-ground-truth-build'`.
5. **knowledge_fact** — via `item_id`; set `subject_entity_id` = cluster entity id.
6. **Relationship rows** in `company_company_relationship` /
   `person_company_relationship` (parent tables) — FK values must be **cluster
   entity ids**, never raw source ids (the app's `_translate_foreign_keys`: any raw id
   without a resolved cluster ⇒ NULL + report line, never a dangling FK).
7. **graph_edge** — mirror every relationship forward; for symmetric types (§0.3) also
   the reverse row with `is_reverse_projection=true`.
8. **evidence + knowledge_item_evidence** — one evidence row per supporting source
   record: `source_id` 1/2, `evidence_type_term_id=35` (TABLE_ROW),
   `text_snippet` = the key source fields verbatim (`entityname='…' fullname='…'
   website=…`), `locator` = `diffbot.company:EXRo_XwecMo6TPp_x5JybaA` or
   `testzoominfo.Company:5128546`; merge-judgment evidence uses source 3, type 31
   (MANUAL). Attach to the items it backs, `is_primary=true` for the primary witness.
9. **merge_engine_entity_map / merge_engine_merge_audit** (§6.5) — create-both if
   truncate removed them; these tables are what make the ground truth auditable.

Staging pattern per family (Postgres-side): load into `gt_build.<table>__stg`, run the
consistency gates of §10 against the staging data, then
`INSERT INTO kg.<table> SELECT ... ON CONFLICT DO NOTHING|UPDATE` in one transaction.
Kill-safe: a failed family transaction leaves nothing half-written.

---

## 10. Verification gates (all must pass; keep outputs as evidence files)

Run against the real `ground_truth` DB after the load. Every gate prints PASS/FAIL with
numbers; store as `gt_build_report.md` + raw SQL outputs.

| # | Gate | Check |
|---|---|---|
| V1 | **Coverage — nothing related left out** | For each family: `closure company count == SELECT count(DISTINCT member) FROM merge_engine_entity_map WHERE entity_type='company'` per origin; and every closure id appears in ≥1 evidence locator. |
| V2 | **Seed presence** | Each of the 5 seeds resolves to exactly one canonical entity with `entity_name` matching the official name and domain fact = official domain. |
| V3 | **No orphan entities** | Every `entity` row has ≥1 `entity_name` and ≥1 `knowledge_fact` or relationship; every COMPANY has `organization`+`company` supertype rows (trigger would have caught missing ones — check anyway). |
| V4 | **FK integrity of relationships** | 0 relationship rows with NULL a/b id that aren't explicitly reported unresolved-links. |
| V5 | **No over-merge** | No cluster contains two members with *different* official domains. Spot-list of known traps must be split, not merged: `Monterey Airbus`, `Al Thuraya Rent A Car`, `Capella University`, `Boeing Mesa Fire Department` (independent? check — it's a fire dept named after Boeing: it may legitimately be a 1-hop stub, but NOT the Boeing root). |
| V6 | **Completeness per record type** | `extract_ledger` rows-extracted == rows-landed per (origin, table), modulo a documented drop-list (each drop has a reason row). |
| V7 | **graph_edge parity** | forward edges == relationships; reverse rows == symmetric-type relationships. |
| V8 | **Provenance walk** | Pick 10 random `knowledge_fact`s → item → source + evidence locator → re-run that source SQL → the value matches. 10/10 or the build fails. |
| V9 | **Idempotence** | Re-run the whole load path once more → zero new rows (`count(*)` identical on all kg tables). |
| V10 | **Cross-check vs app output** | Compare the Boeing + Airbus entity counts against `destination.kg` (the app's run: 62 companies / 76 persons there) and `scripts/goldens/*_manual_merge.py` results; explain any delta in the report — deltas are allowed, unexplained ones are not. |

---

## 11. Report deliverable

`docs/verification/GROUND_TRUTH_BUILD_<date>.md` containing:
- per-family: clusters, members per origin, merged/rejected pairs with reasons,
  borderline list for human review, unresolved-link (NULL FK) list,
  `unmapped_fields` list, raw predicates registered, V1–V10 outputs;
- exact run id, source row counts pulled, and every SQL used (or the script path).

Then drop the scratch schema only after the report is committed:
`DROP SCHEMA gt_build CASCADE;`

## 12. Known pitfalls recap (read before starting)

- diffbot ids are opaque strings — quote them in SQL, never cast to numbers.
- ClickHouse `Location.companyID` Int64 vs `Company.companyID` String (§0.2).
- ClickHouse `IN` with unsorted list ⇒ non-deterministic downstream order (§1.5).
- `relationtype='subsidiarie'` orientation must be checked against `surfaceform` before
  mapping SUBSIDIARY_OF vs OWNS — do not guess.
- `PersonContact.freeMails=''` is not an email.
- Two diffbot "Thuraya" rows (`THURAYA`, `Thuraya Telecommunications`) plus zoominfo
  `Thuraya` = one real company → one cluster with 3 members.
- Airbus SE vs Airbus SAS vs "Airbus Group": the prior analyst pass merged SE+SAS on
  shared-CEO evidence (see `ground_truth.kg.evidence` id
  `01a094ea-daec-7906-9bd8-c10813df3734` before truncation — export it first! §2 wipes
  the DB; `COPY (SELECT * FROM kg.evidence) TO` a file before Stage A).
- Truncation is irreversible: snapshot first
  `pg_dump -h localhost -p 5434 -U postgres -d ground_truth -n kg > backup/kg_pre_rebuild.sql`.
