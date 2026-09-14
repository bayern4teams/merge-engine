# Plan: `fix-v3` — implement all §13 + §14 CTO recommendations

**Goal:** Implement every §13 (architecture/library) and §14 (algorithm-first node wiring)
recommendation from the Merge Engine Forensic Audit in CTO-priority order, TDD, with
per-phase evidence.

**DONE (2026-09-14) — former Steps 0–1, removed from this plan:**
- §00a/§00b audit fixes committed group-by-group on `feat/v3-discovery-er` (11 grouped
  commits, `b9c5e5b..4aded86`); branch `fix-v3` created from that tip and checked out.
- `TODO.md` rewritten on `fix-v3` (commit `8c28be8`): Part A = preserved branch history,
  **Part B = the live roadmap** (B.0–B.9). Label map: plan C.x ⇒ TODO.md B.x (C.1⇒B.1 …
  C.9⇒B.9). TODO.md Part B is the checkbox/evidence tracker; this plan keeps the
  implementation detail.
- `artifact.txt` remains untracked (unignored — user left it in place).
- Baseline note for B.0.2/C.1.1: `python scripts/check.py all` currently FAILS on the
  `test_schema_matching.py` collection error — that is C.1.1's recorded red state.

## Current context / assumptions (verified live 2026-09-14; still valid)

- Repo: `/home/bayern/Projects/merge-engine`, branch `fix-v3` (clean tree),
  HEAD `8c28be8` on top of the grouped audit-fix commits.
- Verified anchors in the current tree:
  - `scripts/bootstrap.py:72` installs `.[dev,perf]` (missing `schema_matching` extra).
  - `pyproject.toml`: `dspy = ["dspy-ai>=2.6"]` (line 77), `splink = ["splink>=4.0.0"]`, `schema_matching = ["valentine>=0.2", "pandas>=2.2"]` (line 86). **No** extras yet for `great_expectations`, `dedupe`, `semantica`, `graphiti`, `langgraph-swarm`.
  - `src/mcp_server/tools/map_fields.py`: `_HEURISTIC_ACCEPT = 0.85` (line 30), Phase 1/2/3 structure (lines 142–291), `_incompatible_with_column` (310), `_has_unsafe_role_prefix` (760), `_heuristic_match` (772). Zero references to `schema_matching`.
  - `src/mcp_server/tools/schema_matching.py:63 run_valentine_candidates(...)` exists, zero callers.
  - `src/mcp_server/tools/join_graph.py:390 build_join_graph(...)` async, built only by `plan_scope`.
  - `src/orchestrator/nodes/analyze_dependencies.py` calls the LLM with the unverified `_build_coverage_matrix`; no fingerprint skip. `parse_request.py:125` / `plan_scope.py:45` show the `stage_fingerprint` pattern to copy.
  - `src/mcp_server/tools/resolve_entities.py`: `_LARGE_COMPONENT_THRESHOLD = 200` (line ~204), `_compute_heuristic_confidence` (1785), `_has_discriminating_agreement` (1376).
  - `src/mcp_server/tools/data_quality.py:99 run_data_quality_checks(...)`, severity `pass|warn|fail` fold-in pattern.
  - `src/orchestrator/graph.py`: `builder.add_node(...)` lines 354–397, edges through `update_extraction_state` → `END` (498).
  - `src/ui/connections.py` stores `config/recent_connections.json` (plaintext — out of §13/§14 scope; do NOT touch here, it's §00b/action-list territory).
  - `scripts/check.py all` = the one verification command (10 gates); `scripts/lint_arch.py` forbids module-level mutable state.
- Live infra needed for verification (may not be up on any given day): Docker `Sample_Database` (ClickHouse :8190) + destination Postgres (the `kg` schema DB), local vLLM at `10.10.9.45:8000`. Tasks that need these are marked **[LIVE]**; each still has a unit-test gate that must pass without them.
- `tests/eval/pagila_staff.csv` (the 560-pair gate fixture) is missing from the checkout — required before any Splink promotion attempt (C.3.1).

**Standing rules carried from CLAUDE.md / audit discipline:** TDD red→green per task; one commit per task; evidence in TODO.md must come from independently-run SQL/test output, never the app's own success message; no module-level mutables; prompts substituted only via literal `str.replace()`; local-only models/DBs.

---

## Phase C.1 — "Finish the sentence" quick wins

### C.1.1 Unbreak pytest collection (2-line fix; do first — everything else's test runs depend on it)

**Files:** `tests/unit/test_schema_matching.py`

- RED (already red): `python -m pytest tests/unit --collect-only -q`
  → `1891 tests collected, 1 error` — `ModuleNotFoundError: No module named 'valentine'` in `test_schema_matching.py`.
- Fix: insert after `import pytest`:
  ```python
  pytest.importorskip("valentine", reason="schema_matching optional extra not installed")
  ```
- GREEN: `python -m pytest tests/unit --collect-only -q` → `N tests collected` with **0 errors**.
- Also run `python scripts/check.py test` → pass.
- Commit: `fix(tests): importorskip valentine in test_schema_matching (unblocks check.py on standard bootstrap)`.

### C.1.2 Install the `schema_matching` extra by default

**Files:** `scripts/bootstrap.py` (line ~72)

- Change `f"{ROOT}[dev,perf]"` → `f"{ROOT}[dev,perf,schema_matching]"`; update the two echoed strings (`"Installing merge-engine[dev,perf] ..."` and the `_fail` message on line 76) to match.
- Verify: `python -m pip install -e ".[dev,perf,schema_matching]" && python -c "import valentine; print('ok')"` → `ok`.
- Verify the real path: `python scripts/bootstrap.py` (or just the pip line) then `python -m pytest tests/unit/test_schema_matching.py -q` → `18 passed` (skips disappear once installed).
- Commit: `chore(bootstrap): install schema_matching extra (ADR 0005 module must be exercised by CI)`.

### C.1.3 pg_trgm + GIN trigram blocking (§13 #13 — highest-leverage speed item, zero new deps)

**Files:** `scripts/schema/` (new migration `003_pg_trgm.sql` if the repo has a schema dir — else `docs/diagnostics/` note + connector-level ensure), `src/mcp_server/tools/resolve_entities.py`, `tests/unit/test_trigram_blocking.py` (new).

- Design (from audit §13 #13 + §00b): for entities whose candidate generation currently runs the in-Python `_build_blocking_keys`/pairwise pass above a size threshold, replace candidate **pair generation** with an indexed SQL blocking query run against the destination (or a staging temp table):
  ```sql
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
  CREATE INDEX IF NOT EXISTS idx_block_name_trgm ON <staging> USING gin (name gin_trgm_ops);
  SELECT a.rec_id AS l, b.rec_id AS r
    FROM <staging> a JOIN <staging> b
   ON a.name % b.name AND a.rec_id < b.rec_id;
  ```
  `%` uses `pg_trgm.similarity_threshold` (tunable; start 0.6 — conservative, since this only *proposes* pairs; scoring is unchanged).
- Implementation shape (keep layering: the tool function receives a connector, never a module-level connection):
  1. New helper `def trigram_candidate_pairs(connector, staging_table: str, name_col: str = "name", threshold: float = 0.6) -> list[tuple[str, str]]` — where staging lives depends on current extract flow: **inspect `resolve_entities.py` first**; if entities are resolved from in-memory records, stage them via the destination connector into a `pg_temp`/`merge_engine_staging` table per run (the destination is Postgres and already reachable from the load path). Guard with config flag `matching.trigram_blocking_enabled: false` (default off) + size threshold `matching.trigram_blocking_min_records: 1000` — YAGNI: small entities keep the existing path.
  2. Union (don't replace) with existing blocking output → dedupe pair set. Replacing outright would risk recall loss on non-Postgres origins; union is safe and measurable.
  3. Config keys go where `matching.*` config already lives (`src/utils/config_loader.py` + `config/config.yaml`).
- TDD:
  - Unit test with a fake connector recording SQL: assert the emitted query contains `gin_trgm_ops` (setup), `%`, `a.rec_id < b.rec_id`, and that pairs union-dedupe with heuristic pairs; assert disabled below threshold records.
  - **[LIVE]** against real destination: create extension, index, insert the 5 real Boeing name variants (`Boeing`, `Boeing Co`, `The Boeing Company`, `Boeing Co. The`, `Boeing Satellite Systems, Inc.`), run the join, assert the true near-duo pairs (0↔3) are proposed. Record wall-clock vs the Python loop for the 1,503-record technology component (regression benchmark, per audit's "measured proof" standard).
- Commit: `feat(matching): pg_trgm GIN trigram candidate blocking (flag-gated, unioned)`.

### C.1.4 Great Expectations format-level checks (finish ADR 0008)

**Files:** `src/mcp_server/tools/data_quality.py`, `pyproject.toml` (add `data_quality = ["great-expectations>=1.0"]` to extras — verify exact current pin intent in ADR 0008 first), `tests/unit/test_data_quality_gx.py` (new), `scripts/bootstrap.py` (append `data_quality` extra).

- New function, called from `run_data_quality_checks` after existing hand-written checks (per audit: *not* replacing them):
  ```python
  def _run_format_expectations(sample: list[dict], columns: list[str]) -> list[dict]:
      """GX 1.x ephemeral format checks (ADR 0008). Returns entries shaped
      exactly like the existing hand-written check entries:
      {"check": <name>, "entity": ..., "severity": "fail"|"warn", "detail": ...}."""
  ```
  API per audit: `gx.get_context(mode="ephemeral")`; `add_pandas("sample")`; `ExpectColumnValuesToMatchRegex` on email/phone-shaped columns — reuse the existing hint/classification helpers (`src/utils/column_naming.py` normalizer + any `_DOMAIN_FIELD_HINTS`-style hints; if none exist in this file, import `column_naming` rather than inventing new hints); `ExpectColumnValuesToBeDateutilParseable` for date-shaped columns. Wrap the whole GX pass in `try/except Exception` **with a degradation log + count** (this codebase's audit lesson: never a silent fallback — see §00b DegradationLedger; minimum viable: `logger.warning` + a `gx_checks_skipped: true` field in the returned report).
  - Fold failures into the same `entity_severity` mechanism (lines ~133–249) — one reconciliation report, not two.
- TDD: fixture with one bad email + one unparseable date → report contains the two GX entries with `severity == "fail"` per ADR 0008 scope; clean sample → `pass`; no-gx-installed env (importorskip for the gx-dependent tests).
- **[LIVE]** catch-the-historic-bug test: run against a sample with `revenue = "$1 Million"` → check fails (the exact §7 failure mode GX exists to catch).
- Commit: `feat(data-quality): GX ephemeral format expectations per ADR 0008 (folded into existing gate)`.

### C.1.5 sqlglot column lineage (finish ADR 0009)

**Files:** `src/mcp_server/tools/sql_lineage.py` (new), `tests/unit/test_sql_lineage.py` (new), plus surface in review UI later (defer UI to C.9 optional — YAGNI: prove the API first).

- ```python
  def column_lineage(mapping_sql: str, target_column: str, schema: str | None = None) -> list[dict]:
      """sqlglot.lineage.lineage(column=target_column, sql=mapping_sql, schema=schema)
      flattened to [{node, source_table, source_column, kind}] — ADR 0009."""
  ```
  `sqlglot` is already installed (verified: ADR says 30.x imports clean — re-verify `python -c "import sqlglot; print(sqlglot.__version__)"`).
- Data source: `field_mappings` decisions already recorded by `map_fields` runs (and P-7's `md_field_mapping` rows) → a tiny helper `mapping_to_sql(field_mapping: dict, dest_table: str) -> str` that renders the mapping as `SELECT <src> AS <dest> FROM <table> ...` so lineage has SQL to parse. Keep it pure/string-building, no DB.
- TDD: `SELECT c.amount AS revenue FROM diffbot.companyfinancials c` lineage for `revenue` → one entry naming `diffbot.companyfinancials.amount`; multi-join mapping → correct chain; unknown column → raises/clear error.
- Commit: `feat(lineage): sqlglot column-level lineage (ADR 0009 finish)`.

**Phase C.1 exit gate:** `python scripts/check.py all` → 10/10. Update TODO.md C.1 evidence slots. Commit: `docs: C.1 evidence`.

---

## Phase C.2 — RELATER-style relational signal (§13 #3 — the actual quality fix)

**Files:** `src/mcp_server/tools/relationship_index.py` (new), `src/mcp_server/tools/resolve_entities.py`, `tests/unit/test_relationship_index.py` (new), `tests/unit/test_relational_agreement.py` (new).

1. `build_relationship_index(records: list[dict]) -> dict[str, set[str]]` — maps record id → asserted related-entity **surface-form** set. Input shape: records already carrying relation rows extracted for the `company_relation` entity family (verify actual field names via a quick read of the extracted-record shape flowing into `resolve_entities`; audit names origin columns `companyid, relatedcompanyid, relationtype, confidence`). Normalize surface forms with `src/utils/column_naming`-style normalization **only for comparison keys**, keep originals for the DSPy context string.
2. In `resolve_entities.py`, add exactly the audit's function (bounded additive boost, never sole justification):
   ```python
   def _relational_agreement(
       rec1: dict, rec2: dict, relationship_index: dict[str, set[str]],
   ) -> float:
       n1 = relationship_index.get(_record_id(rec1), set())
       n2 = relationship_index.get(_record_id(rec2), set())
       shared = n1 & n2
       if not shared:
           return 0.0
       return min(0.15 * len(shared), 0.3)
   ```
   Wire into `_compute_heuristic_confidence` (line 1785) weighted sum — same discipline as `_has_discriminating_agreement`: boost alone must never cross `_AUTO_MATCH`. Index is built **once per entity-resolution call** and passed down (parameter threading, like `matching_rules` — no module state, `lint_arch` compliant).
   - ⚠️ The audit itself flags the semantics subtlety (shared-parent ≠ same entity; that's what *caused* the Airbus blob). Mitigation baked into this design: the boost applies to the pair's *own* asserted neighbor overlap only after neighbor surface forms resolve to **distinct** known clusters ≥ 2 (i.e. pair shares neighbors *and* isn't just two subsidiaries of the same parent). Implement `_shared_neighbors_beyond_common_parent(index, rec1, rec2, parent_forms) -> set[str]` and test that exact Airbus case as a **negative** fixture (Airbus SE + Airbus Helicopters must NOT get a boost from companyrelation's parent link alone).
3. **[LIVE] regression target (this is the acceptance test for the whole phase):** re-run the Airbus company resolution (the audit's own harness pattern — `scripts/ground_truth/` scripts exist) and verify the `f62c560d` failure shape does **not** recur: Airbus Helicopters stays its own cluster; Boeing BDS trio merges; the 15-correct Airbus clusters stay correct. Record precision/recall deltas vs. the audit baseline in TODO.md.
- TDD order per function; each gets its red run first.
- Commit(s): `feat(matching): relational-agreement signal (RELATER-style, bounded)`, then `feat(matching): common-parent guard for relational signal`.

**Exit gate:** full unit suite green; **[LIVE]** Airbus/Boeing ground-truth comparison recorded.

---

## Phase C.3 — Splink promotion track (§13 #1 continuation)

**Files:** `src/mcp_server/tools/splink_matcher.py`, `tests/eval/pagila_staff.csv` (restore), `tests/eval/test_matcher_recall.py`, `config/config.yaml`, `logs/splink_models/company_diffbot_zoominfo/v1/` (already trained).

Ordered sub-tasks (each its own commit):
1. **Restore the 560-pair gate fixture.** `tests/eval/pagila_staff.csv` is missing. Options in order: recover from git history (`git log --all --diff-filter=D -- tests/eval/pagila_staff.csv` → `git checkout <sha>^ -- ...`); else regenerate from the pagila sample DB if present in Docker. Verify: `python -m pytest tests/eval/test_matcher_recall.py -q --collect-only` succeeds. Do NOT hand-author fake pairs under any circumstance — if unrecoverable, stop the phase and report; the gate must not run on invented data.
2. **Token-based blocking** alongside the fixed prefix rule: add `block_on("substr(lower(name),1,4)")`-style **plus** a token-overlap rule via `sqlglot`-free Splink `custom_name` rules; use `splink_blocking.py`'s costed recommender (S-E) to cost candidate rules, keep the costed table in the commit message.
3. **Train-once wiring:** extend `matching_rules` to accept `{"engine": "splink", "model_profile": "company_diffbot_zoominfo/v1"}`; when set, `resolve_entities` loads the saved profile (ADR 0016 file-store pattern already exists in `splink_model.py`) and calls predict-only (no EM). Default path unchanged.
4. **Re-run the 560-pair gate.** Record real numbers into TODO.md. **Only if** recall ≥ builtin (0.995) does `tests/eval/last_gate.json` change and `tests/unit/test_splink_default_invariant.py` flip the default. If not met (likely), the phase's honest output is the measured table + the gap analysis — **do not hand-override the invariant test.**
- Commit per sub-task; final `docs: C.3 splink gate measurement`.

---

## Phase C.4 — Human-feedback loop (§13 #2 DSPy, #12 dedupe)

**Files:** `pyproject.toml` (dedupe extra `dedupe = ["dedupe>=3.0"]`; dspy extra already present), `src/llm/dspy_lm.py`, `src/llm/dspy_programs/__init__.py`, `src/llm/dspy_programs/entity_match.py`, `src/mcp_server/tools/dedupe_matcher.py`, `scripts/dspy/optimize_entity_match.py`, tests for each.

1. **Label collection first (data prerequisite, do this even before 2–3):** add a thin writer so *human* gate decisions land in `kg.merge_engine_merge_audit.human_override` (currently schema-ready but empty; `decided_by='auto'` = 115,614 rows). Find where `human_review_matches` applies overrides (grep `human_override` in `src/`), add the INSERT/update with independent-SQL verification. **[LIVE]**: approve one real match manually, then `SELECT count(*) FROM kg.merge_engine_merge_audit WHERE human_override IS NOT NULL` > 0.
2. `src/llm/dspy_lm.py` — verbatim from the audit guide (`configured_lm()` with `max_tokens=4096`, `api_base` from `config["llm"]`).
3. `entity_match.py` — the audit's `EntityMatchJudgment` Signature verbatim (its `relationship_context` input is fed from C.2's index — the two recommendations share a signature by design).
4. Integration at `resolve_entities.py`'s LLM chunk-scoring site (~line 2049 per §00b): replace raw prompt call with `entity_match_judge(...)` inside `dspy.context(lm=configured_lm())`; keep the exact same fallback contract (any failure → heuristic score) **plus** a warning-level degradation count (feeds the reconciliation `entities.*` dict so reviewers see `llm_fallback_count`).
5. `optimize_entity_match.py`: pull labels where `human_override IS NOT NULL`, `match_metric` as in the audit, `dspy.MIPROv2(...).compile(...)`, `compiled.save("logs/dspy_programs/entity_match_v1.json")` (same versioned-artifact convention as `splink_model.py`).
6. `dedupe_matcher.py`: `train_dedupe_classifier(labelled_pairs)` → score added as **one more weighted input** to `_compute_heuristic_confidence`'s sum (ensemble member, not a fourth engine; flag-gated `matching.dedupe_enabled: false` default).
- TDD: signature/parsing with a stub LM; optimizer runs end-to-end against a tiny synthetic labelled set (not the real endpoint); dedupe trains on 200 synthetic labelled pairs and produces sane monotone scores.
- **[LIVE]** once labels exist: real MIPROv2 compile + before/after judgment accuracy on held-out pairs, numbers into TODO.md.
- Commits: `feat(labels): persist human gate overrides to merge_audit`, `feat(llm): dspy typed judgment layer (flag-gated)`, `feat(matching): dedupe ensemble member`, `chore(dspy): MIPROv2 optimizer script`.

---

## Phase C.5 — Algorithm-first nodes (§14, both guides)

### C.5.1 map_fields Phase 0 (Valentine wiring)

**Files:** `src/mcp_server/tools/map_fields.py`, `tests/unit/test_map_fields_valentine.py` (new).

- New `_build_valentine_frames(unified_schema, dest_candidates, connector)` per audit: source_df from `unified_schema[field]["sample_values"]` (already collected); target_df via `connector.sample_distinct_values(table, column, limit=N)` — the same primitive `join_graph._verify_inclusion` uses (do not invent sampling).
- Insert **Phase 0** before Phase 1 (line ~142): for each undecided field, Valentine top candidate → **must pass `_incompatible_with_column`** (line 310) and the `taken` coalesce-guard → accept at `>= _HEURISTIC_ACCEPT` (0.85, reuse constant). Record `field_mapping[field]["mapped_by"] = "valentine"` (existing vocabulary: `"coalesce"`/`"llm"`).
- Phase 3 LLM shape unchanged; it just receives a smaller `undecided` list.
- TDD (red first): field `homepage` → destination `website` (zero name overlap, real value overlap via fake connector) — fails today, accepted by Phase 0 after; a Valentine top pick that `_incompatible_with_column` refuses must fall through to later phases (guard non-bypass test); empty destination column → Coma degrades, no crash.
- Commit: `feat(map_fields): Phase 0 Valentine tier behind existing safety gates (ADR 0005 wiring)`.

### C.5.2 analyze_dependencies — verified graph first, LLM as fallback (FrugalGPT cascade)

**Files:** `src/orchestrator/nodes/analyze_dependencies.py`, `src/orchestrator/fingerprint.py` (reuse, likely no change), `tests/unit/test_analyze_dependencies_join_graph.py` (new).

Per the audit's 5 steps:
1. In-node `await build_join_graph(state["schemas"], state["origins"], sample_size=...)` → store in `state["join_graph"]` (same key plan_scope reads; signature verified: `join_graph.py:390`).
2. `_join_graph_to_dependency_tree(graph, target_entities, entity_tables)` — walk `graph.neighbors()` restricted to confidence `("declared","verified","probable")`; lift table→entity resolution logic out of `_build_coverage_matrix`.
3. Coverage gate: if every target entity is covered at confidence ≠ heuristic (or legitimately root — reuse `_table_may_be_related`), **skip the LLM call entirely**, return tree tagged `dependency_analysis_origin: "ALGORITHMIC"`. Partial coverage: LLM call as today but prompt includes `CONFIRMED (verified against live data): {child}.{col} -> {parent}.{col} ({overlap:.0%} overlap)` lines before the unverified rows.
4. Fingerprint skip copied exactly from `parse_request.py:125` pattern: `stage_fingerprint("dependency_tree", origins_fp=origins_fingerprint(origins, schemas), target_entities=sorted(target_entities))`.
5. plan_scope reuse: verify (test) that when `state["join_graph"]` is already present+fingerprint-matched, `plan_scope.py:45` skips its rebuild.
- TDD: fake join graph fully covering 3 entities → **assert zero LLM calls** (mock `LLMClient` `assert_not_called`); partial coverage → LLM called once with a prompt containing a `CONFIRMED` line; second identical run → fingerprint hit, zero LLM + zero rebuild.
- Trade-off note into TODO.md verbatim (sampling moves earlier; nothing adds a pass).
- Commit: `feat(deps): verified join-graph-first dependency tree + fingerprint skip; LLM demoted to fallback`.

**Phase C.5 exit:** `python scripts/check.py all` 10/10; **[LIVE]** one Boeing scope run: log line count shows 0 LLM dependency calls for covered schemas.

---

## Phase C.6 — Semantica rule engine, narrowly (§13 #9)

**Files:** `pyproject.toml` (`consistency = ["semantica"]` extra — **first verify the PyPI package installs and its Rete/Datalog engine works standalone**; if the library is immature/broken at install time, fall back to implementing the same rules-as-data contract with `python-datalog`-free plain-Python clause evaluation and record the deviation as an ADR — do not silently swap), `src/orchestrator/nodes/validate_graph_consistency.py` (new), `config/consistency_rules.yaml` (new), `src/orchestrator/graph.py` (add node after `load_data`, before `update_extraction_state`).

- Node reads back just-written entities via destination connector; evaluates rules-as-data, e.g.:
  ```yaml
  - id: acquired_no_late_investment
    when: "company.status == 'ACQUIRED' AND investment_round.valid_from > company.status.valid_from"
    severity: warn
  ```
- Violations → appended to the same `data_quality.red_flags` list (GX fold-in pattern, C.1.4). One report.
- TDD: node with fake store — one violating fact → one red flag; clean load → none; rule file missing → node logs + passes (fail-open on *absent config* only, never on a parsed rule).
- **[LIVE]** load one ACQUIRED company + late investment_round fact into destination → flag appears in run report JSON (read from `logs/run_reports/`, not the UI).
- Commit: `feat(quality): post-merge consistency rule validation node`.

## Phase C.7 — Bounded pilots (§13 #10, #5) — both behind flags, evaluation-first

**C.7a Graphiti/FalkorDB mirror pilot.** Files: `pyproject.toml` (`graphiti = ["graphiti-core"]`), `src/mcp_server/connectors/postgresql_destination.py` (new `mirror_fact_to_graphiti(entity_id, predicate, value, source)` called *alongside* — never instead of — `write_knowledge_fact`), `config/config.yaml` (`pilots.graphiti.enabled: false`), `docs/diagnostics/GRAPHITI_PILOT.md` (the side-by-side temporal-query comparison protocol + results slot). **[LIVE]** FalkorDB via Docker + local vLLM per audit config. Acceptance = the comparison doc filled with real numbers for ≥ 5 temporal queries vs hand-rolled `valid_from/valid_to` CTEs. No ADR-0012 change without those numbers.

**C.7b LangGraph Swarm contested-cluster advisor.** Files: `pyproject.toml` (`swarm = ["langgraph-swarm"]`), `src/orchestrator/subgraphs/cluster_resolution_swarm.py` (new; `create_swarm` two specialists per audit snippet), `src/orchestrator/graph.py` new node `resolve_contested_cluster` between `resolve_entities` and `human_review_matches`, firing only when heuristic vs. relational signals disagree by > configurable margin (`matching.contested_margin: 0.15`). Output attached to `pending_merge_candidates` as advisory evidence — **never auto-decides** (hard assertion test: the node's output cannot set a merge decision field; router tests prove every cluster still reaches `human_review_matches`).
- Commit per pilot.

## Phase C.8 — Decisions recorded (reference-only leads, §13 #4/#8/#11 + RELATER-code)

**Files:** `docs/decisions/0017-reject-hiergat.md`, `0018-reject-relater-code-adopt-idea.md`, `0019-utopia-reference-only.md`, `0020-ontop-study-not-aws-accelerator.md`, `docs/INDEX.md` (index all four; ADR numbering continues from 0016 — verify `ls docs/decisions/` before numbering).
- Content = the audit's verified findings verbatim-reasoned (Python 2/2-commit RELATER; HierGAT PyTorch-1.4 staleness; Utopia decision-ledger design-review pointer; AWS accelerator's Neptune/Bedrock coupling vs. local-only requirement; Ontop as the portable OBDA layer if ever needed). No code.
- `python scripts/check.py docs` → pass (every citation resolves). Commit: `docs(adr): record §13 reference-only + rejection decisions`.

## Phase C.9 — Final gate

1. `python scripts/check.py all` → 10/10 expected.
2. **[LIVE]** one full Boeing + one Airbus run end-to-end (all gates manual where C.4 label collection needs them); independently re-verify destination counts via direct SQL; capture the before/after table (cluster correctness vs audit baselines: Boeing 92.9% run, Airbus 15/16 clusters).
3. Fill every TODO.md `*Evidence:*` slot (or mark `BLOCKED: <reason>` honestly — never leave a checked box without evidence, never check a box whose [LIVE] half couldn't run).
4. Commit: `docs: fix-v3 final evidence pass`. **No push, no merge to main** — that decision stays with the user (standing branch discipline).

---

## Risks, tradeoffs, open questions

1. **Dirty tree carry-over:** C.0 snapshots 17 uncommitted audit fixes onto `fix-v3`. If the user wants them committed to `feat/v3-discovery-er` instead, say so before Step 0 — it changes only the branch topology, nothing else.
2. **Library maturity is exactly what §13 warns about:** Semantica and Graphiti are pilots/studies for a reason. Both phases have explicit fallback paths (rule-engine deviation ADR; pilot doc) — the plan never lets an immature dep block the gate.
3. **[LIVE]-dependent verification:** Docker DBs and the vLLM box (10.10.9.45:8000) may be down on implementation day. Every task keeps a hard unit-test gate; live evidence slots stay explicitly `PENDING-LIVE` rather than being faked or skipped silently.
4. **pagila_staff.csv may be unrecoverable** → C.3 step 1 is a hard stop for Splink promotion; the rest of C.3 (token blocking, train-once wiring) can still land flag-gated with the measurement gap disclosed.
5. **Relational signal can *worsen* the Airbus blob** if the common-parent guard is wrong — that's why C.2's guard ships with the Airbus negative fixture as its first RED test and a ground-truth regression run as its exit gate.
6. **DSPy/dedupe need real human labels** that don't exist yet (column empty by design of testing). C.4 step 1 starts collection; steps 2–6 are built + unit-tested now, *optimized* later — the audit's own ordering.
7. **GX/sqlglot pins:** verify actual installed/importable versions before writing extras into `pyproject.toml`/bootstrap (the audit's sqlglot "30.18.0" claim is from the audit box, not necessarily this one).
8. TODO.md rewrite **preserves Part A verbatim** — deliberate; history/evidence is load-bearing in this repo.
9. Effort realism: C.1–C.2 + C.5 are the high-value core (~days); C.3–C.4 partly gated on data/fixtures; C.6–C.7 are bounded experiments with their own exit docs. The plan implements *all* recommendations, but "implemented" for a pilot means "flag-gated + measured doc", not "adopted".
