# merge-engine — Branch `feat/v3-discovery-er` Implementation TODO

Tracks Part B (V3 discovery/entity-resolution engine) up to BP-B3, per `Branch_Plan.md` at this
worktree's root. Scope override in effect: continue straight through BP-B1 and BP-B2 without
stopping or committing to `main`; stop at BP-B3 for live verification and human review.

---

## B.0 — Branch and worktree

- [x] **B.0** Branch `feat/v3-discovery-er` created off `main` (post-v2-tag), worktree at
  `.worktrees/v3-discovery-er`. `Branch_Plan.md` copied in verbatim (B.0 through end of B.3,
  with a scope-override header). This `TODO.md` created fresh (the inherited one was
  `fix-bugs`'s, not V3's).
  - *Evidence:* `git worktree list` shows `feat/v3-discovery-er` at `.worktrees/v3-discovery-er`;
    `git log --oneline -1` on the new branch matches `main`'s tip (`95c3295`).

## B.1 — Tool decisions written up (`docs/tool-evaluation.md` + ADRs)

- [ ] **B.1.1** `docs/tool-evaluation.md` — full capability matrix (adopted tools' verified API
  surface + rejected tools' reasons), drawn from `Branch_Plan.md`'s B.1 section, not re-derived.
- [ ] **B.1.2** `docs/adr/ADR-001` .. `ADR-011` — one per tool decision in B.1 (adopt Splink/
  Valentine/Desbordante/datasketch/Great Expectations/networkx+sqlglot+Jailer-model; reject
  OpenMetadata/DataHub/Neo4j; benchmark-only Informatica).

> BP-B1 (plan's own text: "stop for review") — **not a pause point on this branch** per the
> scope override above. Noted as reached, then continuing straight into B.2/B.3.

## B.2 — How V3 relates to the existing pipeline

- [ ] **B.2** No code — this is the reuse/new/hybrid/replace decision table already written in
  `Branch_Plan.md`. Referenced by each P-phase below as it lands; no separate TODO item beyond
  confirming each phase actually follows its assigned decision (reuse/replace/hybrid).

## B.3 — Pre-Splink phases (P-1 .. P-10)

Each phase: implement -> unit tests -> live check against the real ClickHouse/PostgreSQL boxes
(`Sample_Database` / `Sample_Database_Destination` Docker containers, per `merge-engine-real-world-test-dbs`
memory) -> update this file with independent evidence, never the app's own success message.

- [ ] **P-1** Domain model & metadata store (`metadata_graph.py` + PostgreSQL DDL: md_source,
  md_database, md_schema, md_table, md_column, md_profile, md_candidate_key, md_relationship,
  md_canonical_field, md_field_mapping, md_edge, md_run, md_override).
- [ ] **P-2** Connector capability matrix (`capabilities()`, `discover_indexes()`,
  `discover_statistics()`, `profile_column()`, `estimate_query_cost()` on the existing connector
  base).
- [ ] **P-3** Profiling engine (FULL/SAMPLED/APPROXIMATE, database-native per connector).
- [ ] **P-4** Semantic type inference (evidence-carrying, no LLM in the first pass).
- [ ] **P-5** Candidate & composite keys (declared PK/unique first, then inferred, then pruned
  composite search, optional Desbordante UCC pass).
- [ ] **P-6** Relationship discovery & scoring (extends `join_graph.py`, ten signals, no opaque
  score).

> **BP-B2** (plan's own text: "🚩 Commit to main if clean") — **do not commit to main** per the
> scope override above. Live-check both ClickHouse origins per the plan's own definition, note
> the result here, and continue straight into P-7.

- [ ] **P-7** Schema matching & canonical schema (Valentine first, deterministic pass merges in,
  LLM sees only what remains ambiguous).
- [ ] **P-8** Metadata graph + graph UI (networkx over P-1 tables, metadata graph vs entity
  graph kept strictly separate).
- [ ] **P-9** Entity anchors, subgraph, coverage, extraction (never auto-merges on name
  equality; traversal policy with depth/node/edge/record caps; preview before any expensive
  run).
- [ ] **P-10** Canonical matching dataset (the Splink handoff artefact, materialised in DuckDB,
  registered directly as a Splink input).

> **BP-B3** — the handoff contract is provable: for a given Airbus run, every canonical record
> answers "where did this come from?" by SQL alone. Live-verify, report back, **stop here** —
> do not proceed into B.4 (Splink phases) or touch `main` without explicit go-ahead.
