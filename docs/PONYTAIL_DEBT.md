# Ponytail debt ledger

Harvested by `/ponytail-debt` on 2026-11-26 (branch `chore/ponytail`, from
`feat/part1-fixes` @ 754f816). Convention: `shortcut:` (upstream) / older
`ponytail:` (this repo) — the code works; the comment names a known limit
(AGENTS.md C4/E5). A row tagged **no-trigger** names no upgrade path or revisit
condition in the comment itself — some point at a numbered report ceiling
("report P16") instead; those rot only if the report stops being readable.

**Total: 45 markers, 15 with no trigger.**

## Markers

### `src/mcp_server/connectors/postgresql_destination.py`

- **L549** **[no-trigger]**: ponytail: ENTITY/CODE/MONEY value kinds need extra reference data (a controlled code taxonomy, currency + exchange-rate lookups) this writer doesn't resolve yet — a value that would need one of those is coerced to TEXT by the caller instead of raising.

### `src/mcp_server/tools/cluster_quality.py`

- **L61** **[no-trigger]**: ponytail: character-level similarity; an alias with no lexical overlap (an acronym and its expansion) reads as unrelated (report P20 Ceiling).

- **L74** **[no-trigger]**: ponytail: signals, not measurements (report P16 Ceiling): a rise says merging behaviour changed, not that it is wrong; the flag marks clusters worth a look and never splits one. A cluster is flagged only when its names fall into two or more groups that share no token and no substring AND the groups' representatives differ by more than the run-derived threshold. Accepted ceilings: siblings that share a family token (Tata Steel / Tata Motors), or fused companies that share a legal-form word, stay unflagged; a lone fused cluster with no other named cluster has no reference and is not flagged; one anchor group is compared with the others

### `src/mcp_server/tools/fellegi_sunter.py`

- **L137** **[no-trigger]**: ponytail: the table is a per-run artifact (Part 1); persisting a global, per-run-snapshotted table (report D-23) is Part 3 storage.

- **L182**: ponytail: report D-14 asks for TF-IDF; plain token cosine was measured to turn every typo into DISAGREE (person recall 0.85 -> 0.26), so the token match is soft. No SoftTFIDF theta: the caller's own character similarity is used as is. Deferred: a continuous sum(-log2(df/N)) text weight. ponytail: D5 ceiling -- a superset with ONE rare extra token still lands in the weak level; if such near-duplicates are common among the candidates, EM learns that level (and the year off by one) as match evidence. Upgrade path: score calibration against labels (D12).

- **L186**: ponytail: D5 ceiling -- a superset with ONE rare extra token still lands in the weak level; if such near-duplicates are common among the candidates, EM learns that level (and the year off by one) as match evidence. Upgrade path: score calibration against labels (D12).

- **L201**: ponytail: ceiling -- above the budget a typo in long text earns no partial credit. Upgrade path: a character n-gram index of candidates.

- **L395**: ponytail: ceiling -- a rare value rarely agrees in a random sample, so the estimate floors (prior pseudo-count, never 0). Deferred: calibrating the score (Platt/isotonic, Belin & Rubin 1997); trigger: the review band's volume justifies it (report P13, section 2.17). Prior consistency (lead decision, deviates from the report's literal wording): sampled ``u`` is the random-pair population's, so ``match_prior`` must be that population's too -- the share of RANDOM pairs that match, EM match mass over the candidates / population_pairs. Pairing sampled ``u`` with the blocked-

### `src/mcp_server/tools/join_graph.py`

- **L329**: ponytail: a real FK into a dense surrogate sequence (1..N) is indistinguishable from coincidence by values alone, so it is NOT inferred; a declared FK still covers it. Sparse or non-numeric keys are unaffected.

- **L351** **[no-trigger]**: ponytail: a FULL profile scan per column that survives the in-memory pre-prune (not per column): the price of exact RHS-uniqueness. A connector without profile_column (MongoDB) leaves it None.

- **L417** **[no-trigger]**: ponytail: unary INDs only (no composite keys). A key-less table whose largest column is not unique, and a nullable unique column in a small table with no profile_column, are misjudged by the size rule. Above the sample cap a copy of the primary key under another name is not recognised as a key (two independent samples cannot be compared); the coincidence test in `_verify_inclusion` still catches integer ones. A genuine FK that is itself its table's key (a shared-key 1:1 extension table) is dropped by the PK-subset filter; a declared FK still finds it.

- **L496** **[no-trigger]**: ponytail: one filtered query per surviving pair against a truncated parent; `_find_candidate_edges` bounds how many pairs that is.

- **L534** **[no-trigger]**: ponytail: containment is the only score, so a coincidental IND with overlap 1.0 (a low-cardinality column inside a surrogate sequence) ties

- **L623** **[no-trigger]**: ponytail: a name guess is the one thing this tier must not do, so with nothing to sample there is nothing to propose. plan_scope's LLM-proposed hops still reach the graph through its own path.

- **L750**: ponytail: the pointer and junction tables are read in full (paged by ``sample_size``, held in memory), while P3/P4 decide the structure on samples; one reference per FK column (the first P3 kept). Deferred alternative: relationship-based duplicate detection over these edges, trigger: a non-empty hierarchy on the real origins.

### `src/mcp_server/tools/load_data.py`

- **L483**: ponytail: find-or-create among rows THIS function wrote (deterministic id, no lookup); a pre-existing row with the same text under another id is not matched, and a dimension that is also its own entity this run is left to that entity (report P4 Ceiling / Deferred: human-confirmed structure).

### `src/mcp_server/tools/map_fields.py`

- **L239** **[no-trigger]**: ponytail: a field whose name differs from the table's, or a dimension whose label is not its first usable column, still reaches the tiers below and the mapping gate; without FK information (direct callers) the reference is read off column names (report P4 Ceiling).

- **L943**: ponytail: ceiling (report P1) -- a dictionary split cannot resolve real ambiguity (``presspoint`` is not ``press|point`` in every schema), so ties are returned for the caller to REPORT, never to pick from. ponytail: debt -- a 1-letter vocabulary word (from e.g. ``x_coord``) can yield absurd unique splits (``idea`` -> id|e|a). A minimum segment length would be a fitted constant; not added. Pay it if such a vocabulary appears.

- **L946**: ponytail: debt -- a 1-letter vocabulary word (from e.g. ``x_coord``) can yield absurd unique splits (``idea`` -> id|e|a). A minimum segment length would be a fitted constant; not added. Pay it if such a vocabulary appears.

### `src/mcp_server/tools/metadata_classification.py`

- **L20**: ponytail: Ceiling (report P12) - detection is by NAME (columns) and by DBMS convention (tables), so a business field that looks like metadata (``signup_date``-style, a ``created_at`` that is really a founding date) is misclassified; the override layers and the recorded risk are the mitigation. Value patterns need more than the one ``sample_row`` discovery holds - deferred. Deferred alternative: classify fields by the section 3.5 taxonomy (measured features), so metadata falls out as a class instead of a list. Trigger: Part 3, with section 3.5.

### `src/mcp_server/tools/plan_scope.py`

- **L528** **[no-trigger]**: ponytail: ONE minimal combination, in schema order; where several exist inside ``cols`` the others are not enumerated (report P4 Ceiling).

- **L562**: ponytail: a sample of a larger table proves non-uniqueness but only suggests uniqueness (``complete`` False); composite FKs are not looked for (P3 finds unary INDs); one local discriminator column is tried, not a combination. Junction-vs-weak errors surface at the gate (report P4 Ceiling). Deferred alternative: human-confirmed structure remembered across runs, trigger Part 3 section 3.9.

### `src/mcp_server/tools/resolve_entities.py`

- **L928**: ponytail: pairs below the exclude bar are not retained, so a committed exclude bar LOWER than the run's needs a retry of the stage; the band's width is policy until the score is calibrated (report P15 Ceiling). Deferred: Possible Match as a persisted, revocable state with its own queue (report section 3.7), trigger Part 3.

- **L993** **[no-trigger]**: ponytail: a changed cluster's id is derived from its new membership, not

- **L1131** **[no-trigger]**: ponytail: a typo inside every shared token (Smith/Smyth with nothing else in common) no longer co-blocks -- the old phonetic key did; token blocking trades that for schema independence (report P5).

- **L1233**: ponytail: meta-blocking is lossy by design — a recall/cost trade, measured here as counts (``block_memberships_filtered`` — memberships, not pairs; ``pairs_lost_by_block_filtering_ub`` and ``pairs_pruned_by_purging_ub`` — upper bounds, a pair counted once per shared block; ``pairs_pruned_by_meta_blocking``); pair completeness needs ground truth and is computed by the eval harness, never guessed here (P19). A name in a script without spaces (CJK) is one token, so a one-character variant does not co-block (upgrade: character n-gram keys). Deferred alternative: progressive (pay-as-you-go) resolution, if a run is ever time-boxed rather than quality-boxed.

- **L1984**: ponytail: four levels cannot tell "two rare tokens agree" from "five common ones agree" (report D-14 ceiling). Deferred: a continuous ``sum(-log2(df/N))`` text weight, once this version is measured.

- **L2675**: ponytail: Ceiling -- with few evident duplicates (a small or a single-origin corpus) (e) has little to go on, so an identifier may be missed (no conflict raised: the pre-P6 behaviour) and a class-2 field that happens to agree on every observed duplicate is taken for class 4. The report's full classifier (D-26: entropy, length distribution, FD membership, child-table cardinality) is the upgrade; deferred to the corpus (Part 3), where values are classified once per origin.

- **L2909**: ponytail: ceiling -- an addendum carrying the SAME year as its original is not caught (no number disagrees), and containment with no numeric field at all keeps its score. Upgrade path: D12 calibration against labels. The rarity test reuses the corpus's own per-entity ceiling.

- **L2986**: ponytail: ceiling -- a variant whose code sits only in another field, or a code written differently on each side, is not caught / is sent to review. Upgrade path: an identifier-token comparison level in the model.

- **L3089**: ponytail: ``p`` is the model's stated probability, not a calibrated one -- calibration is the open item of report P13 / section 2.17 (a labelled sample scored against the stated p); until then the band reads it as is.

- **L3400**: ponytail: two strong wrong edges into one node make a real triangle, and names are not consulted (P20 narrowed by user decision c: name coherence is a P16 flag, never a cut). Upgrade: correlation clustering (report P20 Deferred) once flagged clusters prove to be real wrong merges.""" parts: list[list[str]] = [] repaired = False

- **L3420** **[no-trigger]**: hand-set confidence. ponytail: lossy by design -- a true edge under the mean at BOTH ends is dropped (report P20 Ceiling: recall for precision).""" total: dict[str, float] = {} degree: dict[str, int] = {}

- **L3468**: ponytail: a true member attached to a clique by one scored edge (a pendant) is split off -- recoverable, and the average-linkage cut it replaces split it too. Upgrade: support counted over 2-hop paths.""" inside = set(members) internal = [k for k in edges if k[0] in inside and k[1] in inside] neighbours: dict[str, set[str]] = {m: set() for m in members}

- **L3494**: ponytail: more than two sources is not reduced (logged); scipy comes with the optional extras -- without it the part is left unreduced (logged).""" source = {m: str(records_by_id[m].get("_origin_id")) for m in members} sides = sorted(set(source.values()))

### `src/mcp_server/tools/splink_matcher.py`

- **L267**: ponytail: every OTHER exact-match-shaped column still gets a comparison, but only the first one contributes a blocking rule of its own (same fallback shape as before this fix) when name/domain/ email left none — unioning a rule per arbitrary remaining column risks the same low-selectivity blow-up the comparison-budget bug hit on `jobTitle` values like "senior" (§18.4/§00b); a real fix would cost each candidate with Splink's own `count_comparisons_from_blocking_rule` (already used by the B.4

### `src/mcp_server/tools/unify_entity_group.py`

- **L67**: ponytail: these five repair the rules, they do not make survivorship non-destructive -- the losing values are still gone at the end of the run. Deferred: read-time survivorship over a lossless corpus (report section 3.4; trigger: Part 3, where "the loser is still retrievable" becomes true).

### `src/orchestrator/decision_memory.py`

- **L10**: ponytail: a file has no concurrency control, no schema and no query surface (report P9 Ceiling): two live instances on one path are last-writer-wins and "every decision about entity X" is a full scan. Deferred alternative: the indexed, revocable decision store in ``merge_engine`` (report sections 3.8 and 3.9). Trigger: Part 3, which backfills from this file.

### `src/orchestrator/nodes/analyze_dependencies.py`

- **L157**: ponytail: not recomputed when the whole tree is reused from a pinned profile above (that path returns before any graph exists).

### `src/orchestrator/nodes/load_data.py`

- **L436** **[no-trigger]**: ponytail: a document that omits its name but carries another name column no named sibling has (``email``) is indistinguishable from a second naming style and is judged on that column.

- **L488**: ponytail: refusing is all it can do -- it cannot roll back a partial write (report P10 Ceiling); the proposals are rules of thumb relative to the run's own entities, so a run whose entities are ALL bad alike is caught only by the floors above or by the operator. The 1/2 cap ("never a majority") and the 2x multipliers are sanctioned standing policy, shown and editable at the gate like the P15 band (plan 2.4), not counts of the corpus. Deferred: thresholds learnt from past runs (report P10 Deferred; trigger: enough confirmed runs in P16's history).""" per = { e: _entity_measures(state, e)

- **L648**: ponytail: Ceiling - a mapped column the table lacks empties the whole entity rather than landing it partially (no repair, by spec); with no destination table at all (``schemas`` empty, indistinguishable from a failed read) nothing is reported; a composite key is judged by its first column. Deferred: hold the links in the corpus and re-project once the schema's owner fixes the DDL (report §3.4); trigger = the corpus lands. ``held_links`` grows with the row count until then.

### `src/orchestrator/nodes/validate_graph_consistency.py`

- **L22**: ponytail: only one comparison op (``after_when_valid_from``) and same- entity facts are implemented. Upgrade path: add more ``compare_op`` values, or a ``related_via`` cross-entity link predicate, when a rule genuinely needs one — the pure evaluator (``check_consistency_rules``) and the rule file are both additive, no rule-engine dependency to swap.

### `tests/eval/score.py`

- **L30**: ponytail: flat 10% heuristic; swap for a real block-size estimate if a legitimate dataset ever trips it.

- **L458**: ponytail: without labels, blocking recall is unmeasured -- this reports cost (reduction ratio, K, k) and, where ground truth exists, PC/PQ. Deferred: PC/PQ against a labelled sample accumulated from HITL decisions (inverse-cluster-size weights if that sample is not random); trigger: the first run that has such decisions. Also deferred (Part 4): nothing in the eval harness calls this yet; wiring it into main() needs a labelled run.

---

## Decisions log

Append-only; every owner choice recorded here so a later audit does not ask
again (AGENTS.md F4).

| date | finding / marker | owner decision (own words) |
|---|---|---|
| 2026-11-26 | ledger location | "docs/PONYTAIL_DEBT.md" |
| 2026-11-26 | audit finding 1 (dead `spool_collection`) | applied — deleted |
| 2026-11-26 | audit finding 2 (`dspy` extra, never imported) | applied — extra removed |
| 2026-11-26 | audit finding 3 (`swarm` extra unused) | "Skip — swarm phase is still planned" — kept on purpose; the deptry whitelist names this row as its reason (C5) |
| 2026-11-26 | audit finding 4 (pytest-cov/pytest-xdist unused) | applied — removed from dev extra |
| 2026-11-26 | audit finding 5 (ground-truth scripts silently drop `evidence_ids`/`supersedes`/`numeric2`) | "Leave as-is, record in ledger" — scripts are one-off GT builds on the bench corpus; revisit only if GT builds resume |
| 2026-11-26 | audit finding 6 (bare type-ignore comments) | applied — 5 unused ignores deleted, 32 carry a reason clause now |
| 2026-11-26 | audit finding 7 (no-trigger markers) | reviewed 2026-11-26: classification rebuilt with comment-aware continuation (36 -> 15 true no-trigger); no fake triggers invented — each of the 15 needs its author (or the report it cites) to name the upgrade path |
| 2026-11-26 | audit finding 8 (recurring unused-dep class) | applied — deptry added to audit.yml with a reason-coded whitelist in `[tool.deptry]` |
