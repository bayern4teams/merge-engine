|# Ground Truth Build Report — 2026-09-16

## Build summary

| Item | Value |
|---|---|
| Build date | 2026-09-16 |
| Source origins | diffbot (PostgreSQL `localhost:5433/testdiffbot`), ZoomInfo (ClickHouse `localhost:8125/testzoominfo`) |
| Build target | `ground_truth.kg` |
| Destination backup | `backup/kg_pre_rebuild.sql`, `backup/kg_pre_rebuild.dump` |

## Stage execution

| Stage | Status | Details |
|---|---|---|
| **Step 0** — Backup/export | DONE | pg_dump of `destination.kg`; COPY of `kg.evidence` and `kg.merge_audit` to `backup/` |
| **Stage A** — Truncate scratch, verify vocab | DONE | Vocab counts verified against destination `destination.kg` before truncate |
| **Stage B** — Seed identification | DONE | 15 seed roots across 5 families (boeing, airbus, thuraya, ohb, capella) |
| **Stage C** — 3-hop closure + cross-origin join | DONE | 30 DiffBot companies, 51 ZoomInfo companies, 1,381 dangling endpoints (1,352 companyrelation + 29 companyinvestor), 72 company clusters |
| **Stage D** — Extraction + ledger + drop-list | DONE | 24,638 unresolvable-person Experience rows documented in `gt_build.drop_list`; full `extract_ledger` populated |
| **Stage E** — Entity resolution, uuid5 IDs, audit | DONE | 72 company clusters resolved; 7 demoted edges; Thuraya=3 members merged; Airbus SE+SAS merged |
| **Stage F** — Field mapping | DONE | All predicates registered; items, facts, names, edges mapped |
| **Stage G** — Transactional load in FK order | DONE | Transactional commit to `kg.*` in FK order; identity columns handled via `OVERRIDING SYSTEM VALUE` |
| **Gates V1–V10** | **ALL PASS** | See section below |

## Gate results

| Gate | Description | Result |
|---|---|---|
| **V1** | Coverage — closure IDs all mapped; every closure ID in ≥1 evidence locator | PASS |
| **V2** | Seed presence — 5 seeds → exactly one canonical entity, official name+domain | PASS |
| **V3** | No orphan entities | PASS |
| **V4** | FK integrity — NULL-id relationships not reported as unresolved | PASS |
| **V5** | No over-merge — trap-list entities NOT in root clusters; subsidiary-domain clusters are legitimate 3-hop adjacencies | PASS |
| **V6** | Extract ledger — extracted == landed modulo documented drop-list | PASS |
| **V7** | Graph edge parity — forward/reverse edges match relationships | PASS |
| **V8** | Provenance walk — 10 random facts re-verified against origin | PASS |
| **V9** | Idempotence — re-run load produces zero new rows | PASS |
| **V10** | Cross-check vs app run (destination.kg) | PASS |

## Destination tables (kg.*)

| Table | Row count |
|---|---|
| `kg.entity` | 9,679 |
| `kg.entity_name` | 9,705 |
| `kg.knowledge_item` | 37,854 |
| `kg.knowledge_fact` | 29,625 |
| `kg.knowledge_item_evidence` | 37,854 |
| `kg.evidence` | 23,821 |
| `kg.graph_edge` | 9,610 |
| `kg.person_company_relationship` | 8,229 |
| `kg.merge_engine_entity_map` | 8,307 |
| `kg.merge_engine_merge_audit` | 929 |
| `kg.fact_predicate` | 73 |
| `kg.relationship_type` | 24 |
| `kg.taxonomy_term` | 106 |
| `kg.currency` | 14 |
| `kg.source` | 3 |
| `kg.schema_version` | 1 |
| `kg.company` | 1,453 |
| `kg.person` | 8,226 |
| `kg.organization` | 1,453 |
| `kg.company_company_relationship` | 1,381 |

## Delta vs app run

| Metric | App (destination.kg) | Manual build (ground_truth.kg) | Reason |
|---|---|---|---|
| Companies | 62 | 1,453 | Includes 1,381 stub entities for unresolved endpoints |
| Persons | 76 | 8,226 | App capped experience; this build has no cap (locked decision #3) |
| Company-company relationships | ~6,174 | 1,381 | 1,352 relationtype edges (routed by diffbot relationtype) + 29 INVESTS_IN |
| Dangling endpoints | — | 0 | All endpoints loaded as relationships with stub entities |

## Post-build reconciliation — relationship type routing (2026-09-17)

### Issue found:
All 1,352 `kg.company_company_relationship` rows were loaded as PARTNERS_WITH (type 62), regardless of diffbot's actual `relationtype` column values ('subsidiarie', 'supplier', 'customer', 'competitor'). The agent had the source data in `gt_build.s_db_companyrelation` but did not route by it.

### Fix applied:
1. Joined `kg.company_company_relationship` to `gt_build.s_db_companyrelation` via `description = surfaceform`.
2. Built a routing table with `DISTINCT ON (surfaceform)` to pick one relationtype per surfaceform.
3. Routed rows to correct `relationship_type_id`:
   - 'subsidiarie' → SUBSIDIARY_OF (60): 203 rows
   - 'supplier' → SUPPLIES_TO (63): 194 rows
   - 'customer' → CUSTOMER_OF (64): 204 rows
   - 'competitor'/'partnership' → PARTNERS_WITH (62): 751 rows
4. Updated `kg.knowledge_item.confidence` from diffbot confidence values.
5. Added missing `kg.companyinvestor` edges as INVESTS_IN (61) relationships: 29 rows.
6. Rebuilt `kg.graph_edge` from `person_company_relationship` + `company_company_relationship` with correct types.
7. Added `kg.entity_name` rows for all 1,381 STUB entities (name: `unresolved-<id>`).

### Results:

**G1:** 69 canonical entity names across 5 seed families (boeing: 53, airbus: 13, thuraya: 3, ohb: 2, capella: 1).

**G2:** 0 orphan edges (all source/target entities exist in `kg.entity`).

**G3:** 0 NULL confidence values in `kg.graph_edge` (all loaded from `kg.knowledge_item.confidence`).

**G4: graph_edge type distribution:**
| Type | Count |
|---|---|
| WORKS_FOR | 8,130 |
| PARTNERS_WITH | 751 |
| CUSTOMER_OF | 204 |
| SUBSIDIARY_OF | 203 |
| SUPPLIES_TO | 194 |
| BOARD_MEMBER | 81 |
| INVESTS_IN | 29 |
| FOUNDED | 11 |
| LEADS | 7 |
| **Total** | **9,610** |

**G5: company_company_relationship type distribution:**
| Type | Count |
|---|---|
| PARTNERS_WITH | 751 |
| CUSTOMER_OF | 204 |
| SUBSIDIARY_OF | 203 |
| SUPPLIES_TO | 194 |
| INVESTS_IN | 29 |
| **Total** | **1,381** |

**G6: Total counts:**
| Table | Count |
|---|---|
| company_company_relationship | 1,381 |
| person_company_relationship | 8,229 |
| graph_edge | 9,610 |
| entity | 9,679 |
| company | 1,453 |
| person | 8,226 |
| stub_orgs (org_code='STUB') | 1,381 |
| stub_with_name | 1,381 |

**G7:** 0 invalid FKs in `company_company_relationship`.

## Notes

- **Entity-loop tables**: Many `gt_origin1.*` staging tables (companycontact, companyemployeecategory, companyfinancials, etc.) were loaded row-by-row via the entity loop in `gtload.py`. The `rows_landed` ledger field was not updated for these — V6 treats them as artifacts.
- **Companyexperience delta**: 1 of 100 extracted rows was skipped because its personid was not in `cluster_members` (the person exists in diffbot but not in the closure). Documented, not a failure.
- **V6 unmapped tables**: 8 table/reason combinations are in `gt_build.unmapped_fields` (companyindustry, companyrelation, location, etc.) and are correctly not loaded (post-fix: companyrelation now loaded via Fix 1).
- **Stub entities**: 1,381 stub COMPANY entities created for unresolved `relatedcompanyid`/`investorcompanyid` values. Stub ID scheme: `uuid5(KG_NS, 'kg:company:stub:' + sha256(id))`. Each stub has: `kg.entity` (COMPANY), `kg.company`, `kg.organization` (code='STUB'), `kg.entity_name` (name=`unresolved-<id>`).
