# Ground Truth — FULL CORPUS Build Guide (enrichment origins, flat destination)

Scope amendment to GROUND_TRUTH_MANUAL_BUILD_GUIDE.md. That guide's METHOD
(stages, resolution rules, audit, verification discipline) still applies.
This file OVERRIDES its scope and destination sections. Read both; this file wins
wherever they conflict.

## 0. What changed vs the seed-limited build

| | Seed-limited (old) | FULL CORPUS (this guide) |
|---|---|---|
| Seeds | Boeing, Airbus, Thuraya, OHB, Capella | **EVERY company row in BOTH origins** |
| Closure | seeds + 3 hops | every company's family + 3 hops — in practice: the whole corpus, transitively |
| Expected output | 1 seed family | ALL ~115 origin company records resolved into clusters, plus every person/contact/record that belongs to them |
| Previous run | Thuraya-only build on :55201 | **TRUNCATE and rebuild from scratch** |

There is no "4 of 5 seeds absent" excuse anymore: the corpus IS the satellite-
operator dataset. Every company that exists in the origins must be merged,
linked, and represented. Nothing related to any company may remain out.

## 1. Connections (authoritative — ignore old hosts anywhere else)

- Origin 1: postgresql://enrichment:***@localhost:5436/enrichment_diffbot
  (schema public: company, companyexperience, companyindustry,
   companyinvestmentround, contracts, entities, event, evidence, institution,
   products, _schemas)
- Origin 2: postgresql://enrichment:***@localhost:5436/enrichment_zoominfo
  (schema public: company, companyindustry, contact_information, contracts,
   entities, event, evidence, experience, industry, institution, location,
   products, staff, source_profiles)
- Destination: postgresql://***@localhost:55201/ground_truth  (schema public)
  - `global_warehouse` on the same server is the EMPTY TEMPLATE (schema-only
    source). NEVER write into it. Verify it stays 0 rows at the end.
  - If ground_truth does not exist: CREATE DATABASE ground_truth, then
    pg_dump -h localhost -p 55201 -U postgres -d global_warehouse --schema=public
    --schema-only | psql ... -d ground_truth
  - Keep the audit tables from the previous run's DDL
    (artifacts/GT_BUILD_2026-09-24/audit_ddl.sql): merge_engine_entity_map,
    merge_engine_merge_audit, gt_build_extract_ledger, gt_build_unmapped_fields,
    gt_build_drop_list, gt_build_unresolved_links. Recreate them, then TRUNCATE
    everything (data tables + audit tables) before reload.

## 2. Build rules (unchanged in spirit, full-corpus in scope)

1. Origins are READ-ONLY. All writes to ground_truth only.
2. Rebuild from scratch: truncate all public tables in ground_truth first.
3. Seeds = all companies: every company row in either origin enters the graph.
   Resolution operates on the union of both origins' company records.
4. Blocking + comparison for company resolution (document every merge pair with
   score + rationale in merge_engine_merge_audit):
   - domain exact match (normalize: strip www, lowercase, punycode) = strong
   - legal/name similarity (Levenshtein/Jaro on normalized names, strip LegalForm
     suffixes: Inc/LLC/Ltd/S.A./PJSC/Co/Group/Holding/Communications...) = medium
   - shared country + industry = weak supporting signal only
   - NEVER merge two records whose domains are DIFFERENT real domains even if
     names match (Thales vs Thales Alenia Space stays split unless another rule
     proves same entity); NEVER merge parent with subsidiary just because related.
5. Over-merge is worse than split. Borderline → keep split + audit row.
   Confidence thresholds: merge at >= 0.85 with at least one strong signal, or
   >= 0.92 combined; 0.60-0.85 → audit "held_split".
6. Deterministic ids: cluster_id = uuid5(NAMESPACE_URL, 'kg:company:' + sorted
   member origin ids joined by '|'). Person/contact/location/product/contract
   ids likewise deterministic (uuid5 from origin source+id, or from canonical
   key for synthesized rows). Rebuild must be byte-identical (V9).
7. Cross-source person resolution: same normalized name + same company cluster
   + (same title or overlapping dates) → merge; email identity (same email
   address, normalized) overrides everything else.
8. Relation endpoints (investors, parents, contract counterparties) that do not
   resolve to a company cluster → stub company rows (mark in description
   'STUB:<origin key>'), give them names when surfaceform/origin gives one.
9. Exhaustiveness: for every company cluster carry over — all persons + their
   experience rows, all contact_email/contact_phone, locations, industries,
   products, contracts (with contract_entity links), investment rounds,
   events/evidence that reference them. If a row's FK target cannot be resolved,
   quarantine into gt_build_drop_list with reason (never silently drop).
10. Field mapping: map onto the flat destination columns only (company, person,
    experience, location, industry, contact_*, product*, contract*, finance,
    company_relation). Never invent columns. Unmappable source columns →
    gt_build_unmapped_fields.
11. company_relation rows: derive from origin relation tables (investment
    rounds, parent/subsidiary surface forms, contract counterparty pairs) with
    relation_type from: PARENT_OF, ULTIMATE_PARENT_OF, SUBSIDIARY_OF,
    INVESTS_IN, PARTNERS_WITH, LISTED_ON, RELATED_TO. Direction as in origin.

## 3. Verification gates (ALL must pass; independent recount SQL, not insert success)

V1  every origin company record (both origins) mapped exactly once in
    merge_engine_entity_map; map count == origin record count; no dup record_id.
V2  every cluster has >= 1 company row; every multi-record cluster has >= 2
    origin ids; audit row exists for every merge performed.
V3  zero dangling FKs: experience.person_id, experience.company_id,
    contact_*.person_id/company_id, product.category_id,
    contract_entity.contract_id/company_id, company_relation both endpoints,
    person.location_id, company.industry_id/location_id.
V4  no NULL required fields (company.name, person last/first or full name,
    contact_email.email format-valid).
V5  no merge of two records with different real domains without a strong
    justification audit row; spot-list all domain-mixing clusters and justify
    each.
V6  landed rows == independent recount from origins minus drop_list, per table.
V7  every drop_list row has a reason; drop_list + landed == origin rows per table.
V8  provenance walk: pick 10 random destination rows per major table, walk back
    to origin row via entity_map/evidence — must reproduce the value.
V9  determinism: rerun rebuild_all.sh into a scratch schema, compare hashes —
    byte-identical.
V10 coverage: report per-cluster member count table (which origin records fused)
    and per-table totals; every multi-record cluster listed explicitly in the
    report.

## 4. Deliverable

docs/verification/GROUND_TRUTH_BUILD_<date>_full.md:

- all V1–V10 gate numbers,
- full multi-record cluster list (merged origin ids + evidence for each),
- stub list, drop_list summary by reason, unmapped fields,
- rebuild script path,
- statement that global_warehouse template still has 0 rows and origins are
  unchanged (row counts re-queried at end).
