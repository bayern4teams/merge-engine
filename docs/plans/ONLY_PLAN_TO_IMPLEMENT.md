# merge-engine v2 — Harness foundation on `main`, then six parallel feature branches

> **Status (2026-08-05): Phase 0, Part 1.5, and Part 1.6 are ✅ COMPLETE and
> live-verified on `main`.** This is the synced, in-repo copy of the plan
> that drove all three (the plan-mode file this was copied from is
> `hidden-growing-wilkes.md` — the note at the bottom of this document,
> under "Note for the implementer", is what required this sync). The
> **BRANCH CREATION GATE** below has now been passed: the user gave explicit
> go-ahead, everything above the gate was committed to `main` as a single
> checkpoint commit, and the five branches (B1-B5) were created immediately
> after, each with its own `Branch_Plan.md` + `TODO.md` at its root per the
> READ-THIS-FIRST instructions. For live-verified evidence of every item in
> Phase 0 / Part 1.5 / Part 1.6, see the root [`TODO.md`](../../TODO.md) —
> this document is the *design* record (why things are shaped the way they
> are); `TODO.md` is the *execution* record (what actually ran, against what
> data, with what result). Do not re-derive Phase 0/1.5/1.6 scope from this
> file if it conflicts with `TODO.md` — `TODO.md` reflects what was actually
> built when the two diverge (e.g. a task deferred with a documented reason).

---

## ⚠️ READ THIS FIRST — instruction to the implementing agent

**This plan has two parts and a hard stop between them.**

**Part 1 (`PHASE 0`) lands directly on `main`.** No branches exist yet. Every task in
Phase 0 is either (a) something all five branches depend on, or (b) a deliberate
refactor of a file that all five branches would otherwise edit simultaneously.
Doing Phase 0 after branching guarantees six-way merge conflicts in `graph.py`,
`load_data.py`, `pyproject.toml`, and every node file. Do not skip ahead.

**Part 1.5 also lands directly on `main`, after Phase 0 and before the gate.** It is
speed work plus a rebuilt Langfuse observability layer. Same rationale as Phase 0:
it touches `resolve_entities.py`, `map_fields_node.py`, `client.py` and
`workflow_manager.py` — files three-to-five branches would otherwise edit at once —
and every branch's verification cycle pays the current 40.7-minute run time. Its
observability half is additionally a hard requirement: a management demonstration
of the full graph flow. Written from three live measured runs, not intuition; see
its own Context section for two diagnoses it corrects.

**Part 1.6 also lands directly on `main`, after Part 1.5 and before the gate.** It
adds run-by-run + platform configuration, four investigation tools, and
cross-platform portability (venv + Docker, no conda anywhere required). Same
rationale again: it edits `src/api/main.py`, `src/ui/frontend/**`,
`config/config.yaml`, `Dockerfile`, `.pre-commit-config.yaml`, `scripts/check.py`,
`CLAUDE.md` and docs — files 3-5 branches would collide on.

**Then STOP at the `BRANCH CREATION GATE`.** Report Phase 0, Part 1.5, and Part 1.6
results to the user and wait for explicit go-ahead. Do not create branches unprompted.
**(This gate has now been passed — see the status banner above.)**

**Part 2 — on go-ahead, for each of the five branches B1…B5:**

1. `git checkout main && git pull` (main must be green), then
   `git checkout -b <branch-name>` from the same Phase-0/1.5/1.6 completion commit.
2. Write `Branch_Plan.md` at the branch root containing **that branch's full
   section from this plan, verbatim** — goal, owned files, forbidden files,
   task breakdown, acceptance gates, and the cross-branch rules section.
3. Write `TODO.md` at the branch root: every task from that section as a
   checkbox item, in dependency order, in the same style as the existing
   root `TODO.md` (phase headings, `- [ ]` items, live-verification note per item).
4. Commit both files as the branch's first commit
   (`chore(<branch>): branch plan and TODO`).
5. Return to `main` before creating the next branch.

**Then notify the user again** with the branch list, what landed in each, and
which branch to start on. Do not begin implementing any branch's work until the
user says which one.

---

## Context — why this is being built

`main` now carries a working v1 (commit `2a48845`): a LangGraph merge pipeline that,
as of 2026-08-01, completes a real Boeing merge end-to-end against 35M-row ClickHouse
origins and a PostgreSQL destination. That is the first honest completion in the
project's history — but it lands at **12.0% row parity** against the hand-merge
golden, and entity clustering takes **10–30 minutes per entity type** even on 1,503
records. Correctness is no longer the blocker; **breadth, quality, and speed** are.

A manual inspection of the live destination during planning then found **three
concrete correctness bugs** that were reproduced and root-caused on the spot
(§0.0): the loader has silently grown `company` from 14 to **43 columns**, 15 of 31
company rows have a NULL `name` (and all 31 a NULL `legal_name`) because origin1's
name fields were diverted into invented columns, and — the most serious — at the
real corpus size the matcher generates **zero candidate pairs** while reporting zero
uncompared. Those come first, because the 12% parity figure is partly an artifact of
them.

Beyond that, three independent bodies of evidence say what to do next:

1. `docs/diagnostics/PERFORMANCE_BOTTLENECKS.md` (measured, unfixed) — five bottlenecks, with
   `_partition_component`/`_average_linkage`/`_split_weakest_link` named as the
   dominant cost by a wide margin.
2. `docs/plans/PHASE_Q_PLAN.md` §Q.1–Q.3 (researched, unstarted) — Splink as an optional
   matching engine, Great Expectations as a data-quality gate, column-level lineage
   in the reconciliation report.
3. The remediation-#7 decision deferred in the previous plan: the fixed 18-value
   entity enum in `request_parsing.system.txt` caps "everything about Boeing" at
   3 of 11 data families. That single cap is the largest identified contributor to
   the 12% parity number.

Against that, three external sources define *how* to build the next round:
the **agent-harness literature** (LangChain "Anatomy of an Agent Harness", Addy
Osmani "Agent Harness Engineering", OpenAI "Harness Engineering"), **DSPy**
(programmatic, optimizable prompts instead of hand-tuned `.txt` files), and
**langgraph-swarm** (dynamic agent handoff instead of a one-shot fixed DAG for
open-ended planning).

The harness literature converges on one finding that this repo fails hard today:
*if a rule isn't mechanically enforced, it isn't a rule.* This repo has **no CI, no
pre-commit config, no task runner, no `CLAUDE.md`**, `mypy strict = true` that has
almost certainly never been run, `ruff` with no rule selection, and a
`pyproject.toml` that is missing three runtime dependencies so `pip install -e .`
produces a broken environment. Six agents/developers working in parallel on that
substrate will produce six divergent codebases. Phase 0 fixes the substrate first.

---

## Locked decisions (user-confirmed 2026-08-01 — do not revisit)

1. **CI: both local and server-side.** `pre-commit` + a cross-platform task runner
   for fast local feedback, **plus** a real server-side CI gate on `git.n4t.com`.
2. **DSPy: pilot two stages first** — `request_parsing` and `field_mapping` (the two
   with the most golden cases: 14 and 8). Prove the `LLMClient`→`dspy.LM` adapter
   preserves the quota-fallback chain, semantic cache, cost ledger and Langfuse
   tracing before converting the other four.
3. **langgraph-swarm: planning subgraph only.** Handoff across
   SchemaExplorer / EntityPlanner / TableSelector / ScopePlanner, entirely *before*
   `extract_data` and *before* the first HITL gate. The execution DAG, the six gates,
   SQLite checkpointing and `as_node=` resume semantics are untouched.
4. **Branching: all five branches in parallel, serialized merge-back.** All five start
   from the same Phase-0 completion commit. A strict file-ownership map prevents
   conflicts; branches merge one at a time and every other branch rebases
   immediately after each merge.

## Standing constraints (carried from all prior phases)

- Work **only** inside `D:\transfer\merging`. Never touch parent folders.
- Python: any 3.11 environment satisfying `pyproject.toml` — venv or conda, the
  hard constraint is the *version*, not the tool (`python scripts/bootstrap.py`
  sets one up from scratch on any platform — Part 1.6.C removed every hardcoded
  conda/`na180` path from code, docs, and `.claude/launch.json`; do not
  reintroduce one).
- App runs as `python -m src.main --mode gradio --port 7860` (the flag name is
  legacy; it serves FastAPI + the React SPA). `--mode both` is broken.
- Origins: ClickHouse `origin1`+`origin2` on `:8190` (`default`/`pass`).
  Destination: PostgreSQL `global_warehouse` on `:55432` (`postgres`/`pass`).
  Goldens: `global_warehouse_golden`, schemas `boeing`/`airbus`/`lockheed`.
- **Exactly one destination configured at a time.**
- **Prompt substitution is literal `str.replace()`, never `str.format()`**
  (`src/llm/__init__.py`; rationale at `analyze_dependencies.py:124-125`). A prompt
  that loses a placeholder token fails *silently*. Placeholder tokens are sacred.
- **Live verification is mandatory per task.** Check a box only after running the
  thing against the real app/DBs and confirming with an independent check (direct
  SQL, log inspection, a regenerated report) — never the app's own success message.
- LLM quota errors self-heal via `LLMClient._advance_model` walking
  `config.llm.fallback_models`. Only escalate if the whole 9-model chain is exhausted.

---

# PHASE 0 — Foundation on `main` (before any branch exists)

> ✅ **COMPLETE.** See `TODO.md`'s "Phase 0" section for the full live-verified
> record of every item below, task by task, with real evidence (direct SQL,
> regenerated reports, real ClickHouse/PostgreSQL runs). This section is kept
> as the *design* rationale — why each fix is shaped the way it is — not
> re-summarized as a status log.

Every item here is a prerequisite for parallel work. Phase 0 ends with `main` green
and a recorded baseline that all five branches measure against.

**Do §0.0 first.** It is three real correctness bugs found by manual inspection of the
live destination, all three reproduced and root-caused with direct evidence during
planning. They must be fixed on `main` because they invalidate the baseline every
branch will be measured against — there is no point recording a 12.0% parity baseline
that is 12% partly because the matcher silently compared **zero** pairs.

---

## 0.0 — Destination-correctness bugs (do these FIRST, on `main`)

### Evidence gathered during planning (all reproduced live, not inferred)

```
global_warehouse.company (app output)     global_warehouse_golden.boeing.company (hand-merge)
  rows                31                    rows                31
  name     NOT NULL   16                    name     NOT NULL   31
  legal_name NOT NULL  0                    legal_name NOT NULL 18
  website  NOT NULL   29                    website  NOT NULL   26
  COLUMNS             43                    COLUMNS             14
```

The 29 extra columns are **verbatim origin field names**: `companyID`, `entityName`,
`fullName`, `sourceCategory`, `summary`, `allDescriptions`, `types`, `motto`, `logo`,
`descriptors`, `foundingDate`, `totalInvestment`, `isPublic`, `ceoName`, `founderName`,
`metadata_confidence`, `explicit_origins`, `domain`, `phone`, `rank`, `alexaRank`,
`revenueRange`, `ticker`, `type`, `employeecount_raw`, …

And the origin schemas explain the NULLs exactly:

```
origin1.Company:  companyID, entityName, fullName, sourceCategory, description, ...   ← NO `name` column
origin2.Company:  companyID, name, description, website, domain, ...                  ← HAS `name`
```

---

### BUG A — the destination schema is being mutated: 43 columns where the contract says 14

**This is the red line the user has drawn: no node may add a column or alter the
destination in any way. Every field maps to an existing column or is dropped.**

**Root cause is a four-link chain, and the first link is the prompt itself.**
`src/llm/prompts/field_mapping.system.txt` currently instructs the model, verbatim:

> *"When genuinely unsure whether a field is the same concept as an existing column,
> prefer a new column over a bad merge — a redundant column costs a little schema
> sprawl; a bad merge costs data integrity."*

Rule 2 then explicitly authorises `{"action": "new_column", "name": …, "type": …}`.
The model is doing precisely what it was told. The chain:

1. **Prompt** (`field_mapping.system.txt`, cost model + rule 2 + output contract)
   authorises and *encourages* `new_column`.
2. **`map_fields_impl`** (`src/mcp_server/tools/map_fields.py`) runs three tiers —
   exact-name (`:93-110`), confident heuristic ≥ `_HEURISTIC_ACCEPT` (`:113-140`),
   then the LLM. An LLM `new_column` decision is appended to `schema_changes`.
   Separately, `_propose_incompatible_fallback` (`:229+`) *also* creates a new column
   whenever `_incompatible_with_column` rejects a type match — a second, non-LLM
   source of sprawl.
3. **State**: `schema_changes` → `pending_schema_changes` → the
   `human_review_mappings` gate → `load_data`.
4. **Load**: `_apply_schema_changes` (`src/orchestrator/nodes/load_data.py:727`) calls
   `connector.extend_schema_batch(changes)` → real
   `ALTER TABLE company ADD COLUMN …` (`postgresql_destination.py:650`).

**Fix — four layers of defence.** A prompt instruction is a *suggestion*; only code is
a *rule* (this is exactly OpenAI's structural-enforcement principle, and this bug is
the proof). Implement all four:

- **L1 — Prompt (senior-prompt-engineer rewrite).** Remove `new_column` from the
  action enum entirely. Actions become **`map` | `skip`** only. Invert the cost model:
  *the destination schema is a fixed contract you may not alter; a field with no
  destination equivalent is reported as unmapped, never invented into a column.*
  Add the real failure as a worked example (`entityName` → `name`,
  `fullName` → `legal_name`). **Keep the four placeholder tokens
  `{entity_type}`, `{dest_table}`, `{destination_schema}`, `{source_fields_block}`
  byte-identical** — substitution is literal `str.replace()` and a lost token fails
  silently. Update the 8 cases in `tests/prompts/golden/field_mapping.json` and the
  `field_mapping` scorer in `tests/prompts/harness.py:263-280` to reject `new_column`.
- **L2 — Code, the actual gate.** `map_fields_impl` gains
  `allow_schema_changes: bool = False`. When `False` (the default and the only value
  the pipeline passes) it **must return `schema_changes == []`, unconditionally**:
  an LLM `new_column` reply is coerced to `skip` and recorded in `unmapped_fields`
  with the reason; `_propose_incompatible_fallback` records an unmapped field instead
  of proposing a column. This holds even if the prompt regresses or a model
  hallucinates — which is the point.
- **L3 — Load-time backstop.** `_apply_schema_changes` becomes a no-op unless
  schema changes are explicitly enabled. If `pending_schema_changes` is non-empty
  while disabled, do **not** silently ALTER: record it as an error and surface it at
  the final gate. Add `destination.allow_schema_changes: false` to
  `config/config.yaml` as an explicit, documented opt-in.
- **L4 — Regression test + ratchet.** A test that runs a full Boeing load and asserts
  `SELECT count(*) FROM information_schema.columns WHERE table_name='company'` is
  **exactly 14**, plus a unit test asserting `map_fields_impl` returns no
  `schema_changes` under default config. Add the corresponding `B5` ratchet hook.

**Honest consequence to accept and make visible:** with new columns forbidden, a
genuinely unmappable source field is **dropped**. That is the user's explicit
decision — the destination schema is a contract. It must never be silent: every
dropped field lands in `unmapped_fields`, is shown at the `human_review_mappings`
gate, and is counted in the reconciliation report as `fields_dropped_from_mapping`.

---

### BUG B — `company.name` NULL in 15 of 31 rows, `legal_name` NULL in all 31

**This is the same bug as A, seen from the data side.** Fixing A is necessary; a
little more is needed to make it fix B *correctly* rather than by dropping the data.

**Mechanism, exactly:**

- `origin2.Company` has a `name` column → exact-name match to destination
  `company.name` → those **16** rows are populated.
- `origin1.Company` has **no `name` column at all** — it carries `entityName` and
  `fullName`. Neither exact-matched, neither cleared the heuristic bar, so the LLM
  proposed new columns. Both were created. The destination-native `name` was left
  **NULL for all 15 origin1-derived rows**.
- `legal_name` is NULL in **31 of 31** because nothing ever mapped to it: origin1's
  `fullName` — which literally holds the legal name (`"Boeing (China) Investment
  Co., Ltd."`, `"Boeing United Kingdom Limited"`) — went into its own column, and
  origin2 has no legal-name field at all.

You can read the damage directly in the destination: rows with `name` NULL have
`entityName = "Boeing Defense & Space Group"` and
`fullName = "Boeing (China) Investment Co., Ltd."` sitting in columns that should
never have existed.

**Fix (in addition to A):**

- **Make the heuristic tier handle camelCase.** `_heuristic_match` must tokenize
  `entityName` → `entity name` and `fullName` → `full name` before comparing to
  destination `name` / `legal_name`. Today those comparisons happen on raw strings,
  which is why a trivially-equivalent field fell through to the LLM at all. This
  fixes an entire class of origin1 fields at once (`foundingDate` → `founded_date`,
  `ceoName`, `founderName`, …), not just these two.
- **Add the mappings explicitly to the rewritten prompt** as worked examples.
- **Tests:** golden mapping cases asserting `entityName` → `name` and
  `fullName` → `legal_name`; a destination assertion after a Boeing run that
  `count(name) = 31` and `count(legal_name) > 0`.

---

### BUG C — companies sharing a website are not unified

The user's question — *"based on what really does the app unify and merge entities?
Does it use any fact table? Does it use any references?"* — deserves a direct answer
first, because the answer is the bug.

**How matching actually works today.** There is **no fact table, no foreign-key
reference, and no external identifier authority.** Matching is pure pairwise record
comparison inside a single entity type, in three stages:

1. **Blocking** (`_generate_candidate_pairs`, `resolve_entities.py:606-738`) — group
   records by a cheap key so only plausible pairs are compared. Everything not
   blocked together is **never compared at all**.
2. **Scoring** (`_score_pair`, `:875-939`) — Fellegi–Sunter probability plus
   term-frequency weighting, a deterministic-identity fast path, and guards
   (`_has_conflicting_identity`, `_has_discriminating_agreement`). Output: 0.0–1.0.
   `≥ 0.95` auto-merge, `≤ 0.70` auto-exclude, in between → LLM adjudication → the
   `human_review_matches` gate.
3. **Clustering** (`_build_clusters`, `:1442`) — union-find over confident edges,
   then conflict-repair and cohesion-cut passes.

The only cross-run identity anchor is `merge_engine_entity_map`
(`postgresql_destination.py:766`, **PostgreSQL destinations only**), which maps an
origin row to the destination cluster id it previously landed in, and is fed back in
as `known_cluster_ids` so re-runs reuse ids instead of duplicating. ClickHouse
destinations have no equivalent.

Critically: **`resolve_entities` runs *before* `map_fields`.** The matcher sees raw,
unaligned origin field names — `entityName` on one side, `name` on the other.

**Four distinct verified mechanisms are suppressing the website match. All four were
reproduced during planning. C0 is the deepest and was found last — it is the reason
precision cannot improve no matter how the thresholds are tuned.**

#### C0 — the scorer compares only the *intersection of raw origin field names*, so it never sees the name

`_build_comparison_vector` (`resolve_entities.py:1040-1049`) builds the evidence
vector that Fellegi–Sunter scores as:

```python
for field in set(fields1) & set(fields2)      # intersection of RAW field names
```

Because resolution runs **before** field mapping, those are raw origin names. For
`company`:

```
origin1.Company : companyID, entityName, fullName, sourceCategory, description,
                  summary, allDescriptions, types, motto, website, logo, descriptors,
                  foundingDate, totalInvestment, isPublic, ceoName, founderName, ...
origin2.Company : companyID, name, description, website, domain, employeeCount,
                  phone, revenue, rank, alexaRank, revenueRange, ticker, type, ...

INTERSECTION    : companyID, description, website, created_at        ← 4 fields
```

Measured on a real cross-origin Boeing pair:

| field compared | level assigned | what it actually is |
|---|---|---|
| `companyID` | **disagree** | two unrelated surrogate keys — *guaranteed* to disagree, counted as evidence **against** the match |
| `description` | **disagree** | `"aerospace"` vs `"aerospace manufacturer"` |
| `website` | **weak** | `boeing.com` vs `www.boeing.com` — `_comparison_level` uses `_normalize`, **not** `_domain_of`, so the one field that proves identity is downgraded to "weak" |
| `created_at` | **weak** | load timestamps, meaningless |
| `name` / `entityName` | **never compared** | not in the intersection |

So Fellegi–Sunter receives **two disagreements and two weak agreements** and correctly
concludes "not a match" — from evidence that is entirely artifactual. The name, the
legal name, the CEO, the founding date, the ticker: none of it reaches the model.

**The model is not wrong. It is blind.** No threshold change, no EM tuning, and no
amount of LLM adjudication can fix a scorer that is fed 4 of 34 fields and two of
those four are noise.

**Fix — three parts, all required:**

- **C0a — canonical comparison projection.** Before blocking and scoring, project
  every record onto a uniform view — `{name, legal_name, domain, ticker, phone,
  email, description, founded, ...}` — resolved per origin using the existing hint
  logic (`_best_name_field` `:535`, `_blockable_fields` `:405`,
  `_DETERMINISTIC_ID_HINTS` `:105`). Compare the projection, not raw keys. This is
  the same cross-origin divergence `docs/plans/PHASE_Q_PLAN.md` §Q.1 calls invariant 6 and
  defers to Splink — it has to be solved for the built-in matcher first, and then
  Splink inherits it.
- **C0b — exclude origin-local surrogate keys from the evidence vector.** A field
  that is ~100% distinct *within* each origin and has ~0% value overlap *across*
  origins is a surrogate key. It must be scored `not_comparable`, never `disagree`.
  Today `companyID` actively pushes every true cross-origin match toward rejection.
- **C0c — type-aware comparison levels.** `_comparison_level` applies one generic
  `_normalize` to everything. Route by field type: URL/domain fields through the
  existing, already-correct `_domain_of`; phone through E.164 digits; dates through
  a parsed date; names through token/phonetic comparison. `website` should be
  `exact`, not `weak`.

#### C1 — Block purging annihilates the *entire* candidate set at real corpus size (dominant cause)

`_MAX_REF_BLOCK = 25` (`resolve_entities.py:117`): any approximate block with more
than 25 members is **dropped whole** (`:643-653`). In a scoped single-subject merge,
every record contains the subject token, so the one block that holds everything
exceeds 25 and is purged. Measured, on Boeing-shaped records:

| corpus size | candidate pairs | blocks purged | `uncompared_pairs` reported |
|---|---|---|---|
| 20 | 171 | 0 | 0 |
| 26 | 300 | 0 | 0 |
| **31** (the real size) | **0** | **2** | **0** |
| 36 | **0** | 2 | 0 |

**At 31 records the matcher compares zero pairs.** Not "few" — zero. 31 companies
therefore become 31 clusters, nothing merges, and this exactly reproduces the
historically observed `auto_matched=0, auto_excluded=141, llm_scored=0`.

**And it reports `uncompared_pairs = 0`**, when 465 pairs existed and none were
compared. The one signal the whole design depends on to say *"duplicates may still
exist, I didn't look"* is silently zeroed. That is worse than the missed merges,
because it makes the failure invisible.

**Fix:**

- A purge must **degrade, never annihilate.** If purging leaves a record with no
  block, fall back — sub-block on a secondary key, or admit the block and let the
  existing `_MAX_COMPARISONS = 250_000` budget do the limiting (that budget already
  degrades honestly: it *counts* what it skipped).
- **Count every purged pair into `uncompared_pairs`.** Never report 0 uncompared
  when pairs were dropped. Add a test that asserts this invariant directly.
- Make `_MAX_REF_BLOCK` scope-aware: in a scoped merge the giant subject block is
  *the point of the run*, not accidental noise, and a fixed 25 is the wrong rule.

#### C2 — website/domain are never used as blocking keys

Verified: `_blockable_fields` on Boeing-shaped records returns
`['entityName', 'fullName', 'name']` — **name-shaped fields only.**

Yet `_DETERMINISTIC_ID_HINTS` (`:105`) already trusts `website`, `domain`, `url`,
`homepage`, `ticker`, `tax_id` to settle a pair outright, and that machinery **works
correctly** — verified directly:

```
'www.boeing.com'                  -> 'boeing.com'
'boeing.com'                      -> 'boeing.com'
'https://www.boeing.com/careers'  -> 'boeing.com'
_deterministic_match(origin1_rec, origin2_rec)  -> True
_deterministic_conflict(...)                    -> False
```

So the strongest identity key in the data can **prove** a pair, but can never
**surface** one. Two records sharing `boeing.com` whose names don't co-block are
never compared. This is precisely the case the user spotted.

**Fix:** add a deterministic-key blocking pass — block on `_domain_of(website)`,
`_domain_of(domain)`, `ticker`, `tax_id` — and **exempt those blocks from purging**.
These are high-precision keys; a large same-domain block is signal, not noise. This is
a small, targeted change and it directly addresses the reported symptom.

#### C3 — cross-origin field-name divergence leaves records with no blocking key

Blocking fields are chosen from the corpus. `origin1` names live in
`entityName`/`fullName`; `origin2` names live in `name`. When one origin's field is
too sparse to qualify as blockable, **those records get no blocking key at all** and
are never paired — verified in the 20-record run, where the single origin1 record was
excluded entirely (171 pairs = C(19,2), not C(20,2)).

**Fix:** build a **canonical comparison projection** before blocking — map every
record to a uniform `{name, ref, email, domain}` view using the existing
`_best_name_field` / `_blockable_fields` hint logic — so both origins block on the
same canonical key regardless of source naming. (This is the same cross-origin schema
divergence that `docs/plans/PHASE_Q_PLAN.md` §Q.1 flags as invariant 6 for Splink. It is biting
the built-in matcher **today**, and fixing it here means B4 inherits the solution.)

**Ruled out, so nobody chases it:** `_has_conflicting_identity` /
`_deterministic_conflict` vetoing the pair. Tested on the real record shapes —
`conflict = False`. Not the cause.

---

---

### BUG D — pipeline throughput (the former branch B1, pulled into Phase 0)

**Why this moved here, and why it is not optional.** Two independent reasons, the
second of which was only visible after C1 was found:

1. **Iteration speed is the meta-constraint.** Every item in §0.0 is verified by a
   full Boeing re-run. At v1 speeds that is 60–90 minutes per cycle, and §0.0 alone
   has seven verification items — before the five branches each need many runs of
   their own. Everything downstream is multiplied by this number.
2. **C1 and C2 will make clustering dramatically *worse*.** Today the matcher
   generates **zero** candidate pairs at n=31, so `_build_clusters` receives nothing
   and is trivially fast — the measured 10–30 min figures come from entities where
   blocking *did* survive. Restoring candidate generation (C1) and adding
   domain-key blocking (C2) will produce large, dense connected components feeding
   straight into the unguarded O(n²)/O(n³) partitioning. **§0.0's correctness fixes
   are likely unverifiable at acceptable runtime until this is done.** The two are
   one unit of work, not two.

They also touch the same module: C0/C1/C2 modify `_generate_candidate_pairs`,
`_build_comparison_vector`, `_comparison_level`, `_blockable_fields`; the throughput
work modifies `_build_clusters`, `_partition_component`, `_average_linkage`,
`_split_weakest_link`. Splitting them across a branch boundary guarantees a rebase
conflict in `resolve_entities.py`. Source of truth:
`docs/diagnostics/PERFORMANCE_BOTTLENECKS.md`.

**D.1 — Profile before optimizing (the doc says so explicitly; do not skip).**
Instrument `_build_clusters` (`:1442`) to log, per connected component: member
count, edge count, density, wall time through `_partition_component`. Run Boeing
**after** C0–C2 land, so the profile reflects the real post-fix component sizes, not
today's degenerate empty ones. The v1 tell — `technology` at **1,503 records taking
9–11 minutes, nearly as long as `person`'s ~7,000** — says cost comes from a few
pathologically dense components, not per-record volume. Write the histogram to
`docs/verification/CLUSTERING_PROFILE.md`. **Choose no fix before you have it.**

**D.2 — Fix the O(n²)/O(n³) partitioning.** Three offenders:

- `_partition_component` (`:1577-1610`) — nested `for i,a … for b in members[i+1:]`
  conflict check that **rebuilds two filtered dict comprehensions inside the inner
  loop** (`:1592-1593`). Hoisting those is a large constant-factor win and is
  risk-free. **Do that first, re-measure, then decide whether more is needed.**
- `_average_linkage` (`:1613-1629`) — full O(n²) pairwise pass.
- `_split_weakest_link` (`:1632-1681`) — O(n²) sort plus **recursion** (`:1678`),
  each level re-running `_average_linkage`; worst case O(n³)+.

Given the profile, choose between a **size cap** and an **algorithmic change**. The
existing `_MAX_COMPARISONS = 250_000` budget guard (`:49`, `:696-698`) is the house
precedent for degrading *honestly* — it counts what it skipped. Do the same, and
apply C1's lesson: never report zero shortfall when work was skipped.

**D.3 — Add a yield point in clustering.** The scoring loop yields every 1,000 pairs
(`:335-336`) because API status polls were timing out. `_build_clusters` (`:377`) has
**no yield at all** — which is why the UI freezes for 10–30 minutes.

**D.4 — Parallelize the safe sequential loops, in this order:**

1. `discover_schemas.py:41-63` — **the clean one.** Origins are independent,
   `discover_schema_impl` opens and closes its own connector per call, no shared
   state. Only fix needed: sort `failed_origins` before rendering it at `:77` so
   error text stays deterministic.
2. `map_fields_node.py:85-228` — needs prep: `dest_connector` is created once
   (`:77`) and called synchronously inside the loop (`:105`) on one shared
   connection. Give each coroutine its own connector or wrap the read in
   `asyncio.to_thread`; merge results in `target_entities` order so
   `pending_schema_changes` ordering stays deterministic.
3. Entity-type-level resolution — only after §0.8's `_LAST_BLOCKING_STATS`
   de-globalization lands.

**D.5 — Vectorize scoring and model fitting.** `_score_pair` (`:875-939`) and
`_fit_match_model` (`:819-872`) are pure Python over up to 250k pairs; the latter
materializes every comparison vector in one list comprehension (`:853-856`). The hot
spot is `_string_similarity_cached` (`:799-816`), whose inner
`for t in tokens1: for u in unused:` loop with `list.remove` is O(t₁·t₂) per uncached
pair. **Field-comparison semantics — phonetic keys, term-frequency weighting — must
stay byte-identical.** Use the `[perf]` extras (numpy/pandas).

**D.6 — ClickHouse extraction round-trips.** `_chunked_filters` in `extract_data.py`
sends 500 ids per `IN (...)`; Boeing's 34,782-id `_resolve_link_via` case is ~70
round-trips. Replace with a temp-table + JOIN, with scratch-table lifecycle and
cleanup on failure.

**D.7 — Mid-run resume.** A `load_data` failure currently forces every entity's
blocking, scoring and clustering to redo from zero. Persist clustering results into
the existing DuckDB spool keyed by a content hash of the input records, invalidated
when the hash changes. This is what made v1's fix-and-retry loop cost ~1–1.5 h per
attempt.

---

### 0.0 verification (all live, against the real destination)

Re-run Boeing end-to-end, then assert each independently by direct SQL — never from
the app's own success message:

- [x] `company` has **exactly 14 columns**; no `merge_engine_*` helper table beyond
      the sanctioned entity map; `person` has exactly 9.
- [x] `count(name) = 31` and `count(legal_name) > 0` in `company`.
- [x] Companies sharing a registrable domain are unified — the `boeing.com` group
      collapses instead of splitting 1 + 3.
- [x] `resolution_stats.candidate_pairs > 0` for `company`, and `uncompared_pairs`
      is non-zero whenever blocks were purged (the honesty invariant).
- [x] `unmapped_fields` lists every dropped source field with a reason, and the count
      appears in the reconciliation report.
- [x] `python -m tests.eval.score` shows **no precision regression** — C1/C2/C3 make
      the matcher compare *more* pairs, so over-merge risk goes up and this gate is
      mandatory, not optional.
- [x] Boeing parity is re-measured and reported honestly. It should rise well above
      12.0%; whatever it is, that becomes the real `BASELINE_V1.md` number.
- [x] `docs/verification/CLUSTERING_PROFILE.md` exists, taken **after** C0–C2, with
      the per-component histogram.
- [x] `drive_app.py --assert-budgets` passes, and the before/after seconds per entity
      type are reported. A full Boeing run finishes inside `full_run_minutes_max`.
- [x] No shortfall counter reports zero while work was skipped — asserted by test for
      both the block-purge path (C1) and the clustering cap (D.2).

*(All nine checked live — see `TODO.md`'s "Phase 0" §0.0 entries for the actual
numbers and independent-check evidence behind each box.)*

---

## 0.1 — 0.11

> ✅ **COMPLETE**, all eleven items (dependency truth, the verification contract,
> server CI + pre-commit, repository-first knowledge architecture, architectural
> enforcement, observable budgets, test infrastructure, conflict-proofed shared
> seams, the frontend build story, the flagged secret, and the recorded baseline).
> The full original task text for each (0.1 dependency truth, 0.2 `scripts/check.py`,
> 0.3 server CI, 0.4 `CLAUDE.md`/`AGENTS.md`/`docs/INDEX.md`, 0.5 `lint_arch.py`,
> 0.6 `BUDGETS.yaml`, 0.7 test infra, 0.8 the six conflict seams, 0.9 frontend build,
> 0.10 secret hygiene, 0.11 `BASELINE_V1.md`) is preserved for reference in this
> plan's git history and in `TODO.md`'s "Phase 0" section, which carries the
> live-verified outcome of each — including the one honestly-open item (§0.10, the
> `dashscope` token rotation, still flagged and awaiting the user's own decision,
> never acted on autonomously per the standing rule).

## Phase 0 exit criteria

- [x] **§0.0's seven verification items all pass** — 14 columns, `name` fully
      populated, `legal_name` populated, same-domain companies unified,
      `candidate_pairs > 0`, honest `uncompared_pairs`, no matcher precision
      regression.
- [x] `python scripts/check.py all` passes on a clean checkout of `main`.
- [x] `pip install -e ".[dev]"` succeeds in a fresh env; every extras group installs.
- [x] Server CI runs the identical command and is green (or the fallback is
      documented honestly with the reason) — documented honestly:
      `git.n4t.com` is unreachable at the git protocol level; `.gitea/workflows/ci.yml`
      is prepared but not live-verified; `pre-commit` is the real enforcement today.
- [x] `pytest -n auto` passes with no worker collisions.
- [x] `docs/verification/BASELINE_V1.md` exists with all four measurements taken
      against the current model and current code.
- [x] `CLAUDE.md` reports zero broken links or stale `file:line` citations via
      `scripts/lint_docs.py` (line count grew past the original 100-line aspiration
      once Part 1.6's branch-discipline section landed — every line remains
      traceable to a real failure or hard constraint, which was always the actual
      rule; the count was a proxy for it).
- [x] No behaviour change: one Boeing run still completes with parity ≥ 12.0%.

---

# PART 1.5 — Speed and observability on `main` (before the branch gate)

> ✅ **COMPLETE.** See `TODO.md`'s "Part 1.5" section for the full live-verified
> record — every A/B/C sub-item, the before/after Boeing timings, and the
> `verify_langfuse_trace.py` assertions that passed. This section is the design
> rationale kept for reference; do not re-derive scope from it if `TODO.md`
> disagrees (e.g. B.2 was deliberately deferred with a documented reason — see
> its own entry below).

## Context — why this phase exists, and why it lands before branching

Phase 0 made the pipeline *correct* and *measurable*. Three live Boeing runs
(`docs/diagnostics/BOEING_SPEED_REPORT_2026-08-04.md`) then established the
real numbers: a full run is **40.7 minutes**, of which **`resolve_entities` is
26.8 min (66%)** and `map_fields` is 6.3 min. Every verification cycle in
B1–B5 pays that 40 minutes. Halving it pays back across every branch, and
like Phase 0 it touches files (`resolve_entities.py`, `map_fields_node.py`,
`client.py`, `workflow_manager.py`) that three-to-five branches would
otherwise edit at once.

Two things forced this phase to be written from measurement rather than
intuition, and both **corrected an earlier wrong diagnosis** — record them so
nobody re-derives the wrong answer:

1. **The speed report's §4a hypothesis was wrong.** It blamed the C0a
   canonical projection for the candidate-pair pool growing 2–3× (technology
   323,169 → 1,396,201 `uncompared_pairs`). Tracing the actual call graph
   disproves this: `_project_to_canonical` is referenced *only* from
   `_build_comparison_vector` (`resolve_entities.py:1254`) and
   `_compute_heuristic_confidence` (`:1403`) — both **scoring**-side. The
   blocking path uses a completely separate hint set
   (`_blockable_fields:690`). C0a cannot change the pair count.
   The pool growth came from two *deliberate* BUG-C fixes instead: the
   `_MAX_REF_BLOCK` purge was turned into logging-only (`:758-766` —
   `usable[key] = members` now runs unconditionally), and a new
   purge-exempt `full:dom:` blocking key was added (`:649-653`).
   **Neither should be reverted** — they are correctness fixes.
2. **The pool growth is not where the time goes.** Overflow blocks are only
   *counted* with cheap arithmetic (`n*(n-1)//2`, `:810`), never expanded.
   Both `technology` and `experience` score exactly 250,000 pairs
   (`_MAX_COMPARISONS`). `technology` = 1,503 records / 5.1 min = **~1.2 ms
   per pair**; `experience` = **~3.8 ms per pair**. The dominant cost is
   **per-pair scoring work**, and C0a *did* raise that (it added 4–8
   uncached projections per pair) — which is the real reason `technology`
   went 4.2 → 5.1–6.7 min. That cost is fully recoverable by caching, with
   provably identical output.

Separately, **Langfuse tracing is being rebuilt for a management
demonstration**. It exists today (`src/llm/tracing.py`, `docker/langfuse-compose.yml`)
but does not show the graph correctly. §1.5.C proves exactly why, with a
reproducible experiment, and fixes it.

**Ordering:** 1.5.C (observability) may be done first and is recommended —
the custom phase spans from C.4 turn the A/B optimization work from
"measure with a stopwatch" into "read the waterfall". A and B are
independent of each other.

---

## 1.5.A — Scoring-path waste removal (provably zero precision risk)

Every change in §A is a **pure-waste elimination**: the same values are
computed fewer times. Matcher output must be **byte-identical**. That is the
acceptance test, and it makes this section safe to land without re-arguing
matcher precision.

### A.0 — Build the falsifiable measurement first (do this before any edit)

Without this, "it got faster" is an adjective (rule R10). Add
`scripts/diagnostics/profile_resolve.py`:

- Loads a fixed record set from the live ClickHouse origins (the same method
  `docs/verification/CLUSTERING_PROFILE.md` already used — real data, not
  synthetic), for `technology` (1,503) and a 5,000-row `experience` sample.
- Runs `resolve_entities_impl` with `llm_config=None` (heuristic only, so the
  number is pure CPU, no network variance).
- Records wall time, and `cProfile` cumulative time for the top 25 functions.
- **Writes the full result dict — every cluster id and member list — to a
  JSON file.** This is the byte-identical baseline for A.1–A.6.
- Writes the profile to `docs/verification/SCORING_PROFILE.md`.

Acceptance for every subsequent A-task: re-run, `json.load` both files,
assert the cluster structure is **exactly equal**. Not "similar" — equal.

### A.1 — Precompute canonical projections per record (the single biggest win)

**Problem.** `_project_to_canonical(rec)` (`:1125-1175`) is a pure function of
one record, running ~35 substring tests per field. It is called 2× in
`_build_comparison_vector` (`:1254-1255`), 2× in `_compute_heuristic_confidence`
(`:1403-1404`), and `_score_pair` may invoke those paths 2–4× per pair
(`:1035`, `:1090`, `:1116`) — plus 2 more per pair inside `_fit_match_model`
(`:970-973`). At 250,000 pairs that is **on the order of 350 million
substring comparisons per entity type** to recompute ~1,500 distinct values.

**Fix.** Compute once per *record*, look up per pair.

```python
# In resolve_entities_impl, immediately after candidate generation (~:380):
projections: dict[str, tuple[dict[str, Any], set[str]]] = {
    _record_id(rec): _project_to_canonical(rec) for rec in records
}
```

Thread it through as an **optional** parameter so every existing unit test
that calls these helpers directly keeps working unchanged:

```python
def _build_comparison_vector(
    rec1,
    rec2,
    normalise=_normalize,
    projections: dict[str, tuple[dict, set]] | None = None,
) -> dict[str, tuple[str, str | None]]:
    if projections is not None:
        proj1, _ = projections[_record_id(rec1)]
        proj2, _ = projections[_record_id(rec2)]
    else:
        proj1, _ = _project_to_canonical(rec1)  # unchanged fallback
        proj2, _ = _project_to_canonical(rec2)
    ...
```

Key on `_record_id(rec)` (a stable string) rather than `id(rec)` — object
identity would silently break if a record is ever copied.

Same treatment for `_compute_heuristic_confidence`. Expected: **1,500
projections instead of ~1,000,000+** for `technology`.

### A.2 — Reuse `_fit_match_model`'s comparison vectors instead of discarding them

**Problem.** `_fit_match_model` (`:970-978`) materializes one comparison
vector per candidate pair — up to 250,000 dicts — passes them to
`estimate_model`, then **throws them away**. The scoring loop at `:1035`
immediately rebuilds the identical vectors.

**Fix.** Have `_fit_match_model` return `(model, vectors)`, and have the
scoring loop index into `vectors` by pair position rather than recomputing.
The pair ordering is already deterministic and fixed (`:807`'s size+key sort),
so positional indexing is safe. Note the guard at `:951`
(`len(candidate_pairs) < _MIN_PAIRS_FOR_MODEL`, = 30) — when the model isn't
fitted there are no vectors, so the scoring path must retain its
compute-on-demand branch.

Combined with A.1 this removes roughly **half** the remaining projection and
comparison-level work.

### A.3 — Hoist function-local imports out of per-pair code paths

`_comparison_level` (`:1206-1212`) executes

```python
from src.mcp_server.tools.fellegi_sunter import (
    LEVEL_DISAGREE,
    LEVEL_EXACT,
    LEVEL_NULL,
    LEVEL_STRONG,
    LEVEL_WEAK,
)
```

**inside the function**, i.e. once per field per pair — order 5–10 million
executions per entity type. Same pattern at `:1078`
(`_has_discriminating_agreement`) and `:1117` (`_only_partial_name_evidence`).

`fellegi_sunter.py` does **not** import `resolve_entities`, so there is no
circular-import reason for this. Move all three to module scope. Trivial,
zero risk, measurable.

### A.4 — Hoist `_record_id` out of the O(n²) blocking loop

`_generate_candidate_pairs:814-815` calls `_record_id` twice per pair inside
the double loop:

```python
for i in range(n):
    for j in range(i + 1, n):
        id1 = _record_id(block_records[i])  # recomputed n-i-1 times
        id2 = _record_id(block_records[j])
```

`_record_id` (`:1975-1980`) does three `dict.get` calls and builds an
f-string. This is **the identical defect BUG D.2 already fixed in
`_partition_component`** — it was simply never applied here. Hoist:

```python
        ids = [_record_id(r) for r in block_records]
```

### A.5 — Cache `_best_name_field` per record

Called from `_cross_name_similarity:1496` per pair, uncached, scanning every
field against 9 hints. Fold it into the A.1 projection table (compute once
per record, store alongside the projection).

### A.6 — Fix the latent unbounded-expansion bug in the budget check

**Not currently biting, but a real time-bomb — include it.** At
`_generate_candidate_pairs:807-824` the budget check sits **outside** the
inner double loop:

```python
for _key, block_records in sorted(usable.items(), key=...):
    n = len(block_records)
    if budget_reached:
        overflow_pairs_estimate += n * (n - 1) // 2
        continue
    for i in range(n):  # <- fully expands this block
        for j in range(i + 1, n):  #    regardless of budget
            ...
    if len(pairs) >= _MAX_COMPARISONS:  # <- only checked afterwards
        budget_reached = True
```

Blocks are sorted smallest-first, so today the budget is normally reached
before the giant blocks — which is why this has not exploded. But if a single
block is encountered while `len(pairs)` is still under budget, it expands to
`n(n-1)/2` four-tuples with no ceiling. On a `full:dom:` block covering a
whole scoped corpus (34,782 records → 604M tuples) that is an OOM, not a
slowdown.

**Fix:** move the check inside the `j` loop (`if len(pairs) >= _MAX_COMPARISONS: budget_reached = True; break`),
breaking out of both loops and folding the block's *remaining* pairs into
`overflow_pairs_estimate` so the honesty invariant (C1) still holds.
**This one can change which pairs are scored** in the edge case where it
triggers — so it needs the eval gate, unlike A.1–A.5. Land it last and
verify separately.

### §A verification

1. `python scripts/diagnostics/profile_resolve.py` → cluster JSON is
   **exactly equal** to the A.0 baseline (A.1–A.5). A.6 may differ only in
   the overflow edge case; if it differs at all, prove why.
2. `python -m tests.eval.score` → precision **1.0000**, recall **0.9950**,
   unchanged.
3. `python scripts/check.py all` → 9/9.
4. Report before/after seconds per entity type from the profile.

---

## 1.5.B — Parallelism (scheduling only; results unchanged)

Ordered by payoff-to-risk. **B.1 is the free win — do it first.**

The controlling fact, which decides the tool for each stage:

| Stage | Bound by | `asyncio` | threads | processes |
|---|---|---|---|---|
| `resolve_entities` (26.8 min) | CPU (pure Python) | ✗ | ✗ **GIL** | ✓ |
| `map_fields` (6.3 min) | network (LLM) | ✓ **big** | — | unnecessary |
| `extract_data` (24 s) | network (DB) | ✓ small | — | unnecessary |
| `analyze_dependencies`→`plan_scope` (3.8 min) | network, but strictly sequential (plan_scope consumes deps) | ✗ | — | — |

### B.1 — `map_fields`: `asyncio.gather` across entity types

`map_fields_node.py:91` is a bare `for entity_type in target_entities:` with
one `await map_fields_impl(...)` per iteration (`:207`). These are independent
LLM round-trips: wall time is `sum(latencies)` where it should be
`max(latency)`. ~10 entity types → **6.3 min → ~1 min**.

**The one hard blocker** is the single shared `dest_connector` created at
`:79-81` and called *inside* the loop at `:115` (`get_table_schema`).
A `psycopg2`/`databricks` connection is not safe for concurrent use.

**Fix — pre-fetch, don't share.** `available_dest_tables` is already known at
`:82`, before the loop. Resolve every entity's destination table and fetch all
schemas up front into a plain dict, then the per-entity coroutines touch no
connection at all:

```python
    dest_schemas = {t: dest_connector.get_table_schema(t) for t in needed_tables}
```

**Determinism.** `pending_schema_changes.extend(...)` (`:226`) and
`errors.append(...)` (`:244`) are order-dependent accumulators; concurrent
appends make run-to-run ordering nondeterministic. `asyncio.gather` preserves
input order, so collect per-entity results into an ordered list and merge
afterwards in `target_entities` order — the pattern `discover_schemas.py:47`
(D.4.1) already established. `field_mappings[entity_type] = ...` is keyed by
entity and needs no change.

Use `return_exceptions=True` so one entity's LLM failure degrades to that
entity's error entry rather than cancelling the siblings — matching the
existing per-entity `try/except` semantics at `:242-245`.

### B.2 — `extract_data`: parallelize the independent filter chunks

**Deprioritized, with reason — see the live-verified status in `TODO.md`'s
"Part 1.5" section.** `_chunked_filters` (`extract_data.py:32-51`) splits a
large `IN (...)` into 500-id chunks. Both callers (`:349`, `:598`) then run
`for chunk: while True: await ...` — **fully serialized**. Each chunk has an
independent cursor **by construction**, so chunks are trivially gatherable in
principle. But `table_failed` triggers a POSITION-based spool rollback
(`spool.truncate_after`) that assumes every row written since a checkpoint is
this table's and nothing else's — fetching chunks concurrently while
appending straight to the spool (the current constant-memory design) creates
a real race between a still-in-flight chunk and a rollback. The only safe fix
trades away the constant-memory guarantee for a measured ~1% payoff (24s of a
2442s run). Deprioritized rather than implemented under time pressure; revisit
only if a future `BASELINE_V1.md` measurement shows `extract_data` as a real
bottleneck.

### B.3 — `resolve_entities`: true multiprocessing (largest win, largest lift)

`nodes/resolve_entities.py:74` loops entity types sequentially with one
`await resolve_entities_impl(...)` each. `asyncio.gather` here would achieve
**nothing** — the work is CPU-bound Python and the GIL serializes it. Only
separate processes give real parallelism: 15.8 + 5.1 + 3.8 + small ≈ 26.8 min
sequential becomes **bounded by the longest entity (`experience`, ~15.8 min)**.

**Why this is a real refactor, not a one-liner:** `resolve_entities_impl` is
`async` and contains an I/O phase (`_judge_pairs_with_llm`, `:1630`,
`asyncio.gather` over LLM chunks). A coroutine cannot be shipped to a
`ProcessPoolExecutor`.

**Approach — split CPU from I/O:**

1. Extract the pure-CPU core into a **synchronous, module-level, picklable**
   function: blocking (`_generate_candidate_pairs`) → model fit
   (`_fit_match_model`) → scoring loop, returning
   `(scored_pairs, borderline, model_params, stats)`.
2. `await loop.run_in_executor(process_pool, _resolve_core, ...)` per entity.
3. Keep **LLM adjudication and clustering in the parent process** — the LLM
   part is I/O and already concurrent; clustering needs the adjudicated
   scores anyway.

**Constraints to design for, all verified:**

- **Pickling cost.** `experience` is 34,782 records; the payload is tens of
  MB per task. Measure it — if serialization eats the win for small entity
  types, apply the pool only above a record-count threshold and run small
  entities inline.
- **Cold caches.** The four module-level `lru_cache`s (`:877`, `:883`,
  `:889`, `:914`, each 500k entries) start empty in every worker. They are
  pure-function caches so this is a warm-up cost, not a correctness issue.
- **Windows `spawn`.** Workers re-import the module; `resolve_entities.py`
  must have no import-time side effects (it currently has none — all
  module-level names are read-only constants, confirmed).
- **Tracing.** OTel context does **not** cross a process boundary. Emit the
  per-entity span in the **parent** around `run_in_executor` and record the
  worker's returned stats as span attributes (see C.4). Do not attempt to
  create spans inside workers.
- **Determinism.** Results are keyed per entity type and merged in
  `target_entities` order — same rule as B.1.

**Landed. Measured payoff recorded in `docs/verification/PROCESS_POOL_PROFILE.md`
and `TODO.md`'s "Part 1.5" section.**

### §B verification

- Cluster JSON from A.0's harness still **exactly equal** (parallelism must
  not change results).
- `pending_schema_changes`, `unmapped_fields`, and `errors` ordering
  identical across two consecutive full runs — assert in a test, not by eye.
- One full Boeing run: `drive_app.py "Boeing" --assert-budgets`, report
  per-node timings from `logs/run_reports/<run_id>.json` against the
  40.7-min / 26.8-min baseline.

---

## 1.5.C — Langfuse observability, rebuilt to show the whole graph

**Goal (agreed):** one Boeing run = **one** Langfuse trace; every graph node a
`CHAIN` span in execution order with real durations; every LLM call a
`GENERATION` span **nested under its node** with model/tokens/cost; HITL
resumes joined into the same session; **plus custom spans for DB extraction,
destination loads, and the internal phases of `resolve_entities`** so the
trace is a complete latency waterfall. Verified by an automated script that
queries the Langfuse API and asserts the span tree — not by eyeballing the UI.

### C.0 — Root cause of "it doesn't show nodes and the graph properly" (proven, not theorized)

Two independent defects. The first is the one that breaks the picture.

**Defect 1 — LLM calls destroy their parent context.** `client.py:232` builds
a config and `:255` passes it into `ainvoke`:

```python
cfg = trace_config(tags=[self.session_name])
...
response = await model.ainvoke(messages, config=cfg)
```

`trace_config` (`tracing.py:97-107`) returns `{"callbacks": [handler], "metadata": {...}}`.
LangChain's `ensure_config()` **replaces**, not merges, these keys. Reproduced
directly against the installed `langchain-core 1.4.9`:

```
AMBIENT callbacks : [H(parent)]                              # the node's callback manager
AMBIENT metadata  : {'langgraph_node': 'resolve_entities'}

MERGED  callbacks : [H(langfuse)]                            # parent DISCARDED
MERGED  metadata  : {'langfuse_tags': ['merge-engine']}       # langgraph_node LOST
```

The parent `CallbackManager` carries the parent run id. Replacing it makes
every LLM generation a **detached root span** instead of a child of its node,
and simultaneously drops the `langgraph_node` / `langgraph_step` metadata that
would associate it with a node at all. **This is the bug.**

The fix is confirmed by the same experiment — inherit callbacks, merge only
metadata:

```
FIX (merged metadata) callbacks = [H(parent-langfuse)]
                      metadata  = {'langgraph_node': 'resolve_entities',
                                   'langgraph_step': 7,
                                   'langfuse_tags': ['merge-engine']}
```

**Defect 2 — the trace is fragmented across HITL resumes and lost after a
restart.**

- `workflow_manager.py:199-200` is the *only* place the handler is bound.
  `rehydrate_workflow:804` rebuilds a bare `{"configurable": {"thread_id": ...}}`
  with **no callbacks**, so after an app restart every resume runs untraced.
- `update_state` (`:1228`) is given the handler. LangGraph treats that as a
  pseudo-node run, so each HITL resume emits **its own root trace** — matching
  the "3 root LangGraph traces" already recorded in `TODO.md:283`.

### C.1 — Stack + SDK compatibility smoke test (do this first; it gates everything)

Installed: **`langfuse==4.13.2`** (OpenTelemetry-based, exports to
`/api/public/otel/v1/traces`, confirmed at
`langfuse/_client/span_processor.py:118`), against server images pinned
**`langfuse/langfuse:3`** and `langfuse-worker:3`
(`docker/langfuse-compose.yml:19`, `:69`). `pyproject.toml:37` pins only
`langfuse>=3.0.0`, so the 4.x SDK is what actually loads.

Per the agreed decision: **verify v3 server accepts v4 SDK OTLP ingestion
before changing anything.**

```bash
docker compose -f docker/langfuse-compose.yml --env-file docker/langfuse.env up -d
```

Then a minimal end-to-end probe: `get_client().auth_check()`, emit one
trivial span, `flush()`, and read it back through
`client.api.trace.list(...)`. If the span round-trips, keep `:3` and pin
`langfuse>=4.0,<5` in `pyproject.toml` to make the working combination
explicit (`requirements.txt` is generated from it — regenerate, don't
hand-edit). If it does **not** round-trip, upgrade the two image tags to v4,
re-bootstrap via the `LANGFUSE_INIT_*` vars in `docker/langfuse.env`, and
record the decision in `docs/decisions/`.

Also note for the runbook: the Langfuse UI is on **`http://localhost:3000`**
(`langfuse-compose.yml:72-73`), and it was simply **not running** during the
Phase 0 baseline (`BASELINE_V1.md:239-245`, `WinError 10061`) — that is a
stack-not-up symptom, not a code defect, and must not be confused with C.0.

### C.2 — Fix the nesting (Defect 1)

Change `trace_config`/`client.py` so the handler is attached **only when there
is no ambient callback manager**, and metadata is **merged into** the
inherited metadata rather than replacing it:

- Inside a graph run → inherit the node's callbacks (generation nests
  correctly under the node span), add only `langfuse_*` metadata keys.
- Standalone (e.g. `tests/prompts/harness.py`) → no ambient callbacks, so
  attach the handler and behave exactly as today.

Use `langchain_core.runnables.config.ensure_config()` to read the ambient
config. The metadata keys the installed handler honours are exactly
`langfuse_session_id`, `langfuse_user_id`, `langfuse_tags`, `langfuse_prompt`
(verified at `langfuse/langchain/CallbackHandler.py:496-520`, `:1769-1775`) —
so the existing key names are already correct and must be preserved.

**Do not** attach a second handler at the LLM level while one is active at the
graph level; the v4 SDK would emit duplicate spans.

### C.3 — One trace per run, across HITL resumes (Defect 2)

- **`rehydrate_workflow:804`** must rebuild the traced config exactly as
  `start_workflow:199-200` does, so a post-restart resume is still traced.
  Factor the config construction into one helper used by both — this
  divergence is precisely the kind of duplication that caused the bug.
- **Stop passing callbacks to non-run calls**: `update_state` (`:1228`) and
  the four `get_state` calls (`:319`, `:858`, `:879`, `:1244`) should receive
  a plain `{"configurable": {"thread_id": ...}}`. Only `astream` (`:256`,
  `:1269`) should carry the handler. This is what collapses the 3 root traces
  into one.
- **Session grouping**: keep `langfuse_session_id = thread_id`. The v4 SDK
  also offers `langfuse.propagate_attributes(session_id=..., tags=...,
  trace_name=...)` as a context manager (signature verified) — entering it
  inside `_run_workflow`/`_continue_workflow` around the `astream` call is the
  cleaner v4-native way to set trace-level attributes, and it gives the trace
  a stable human-readable name (e.g. `merge:Boeing`) for the demo.
  Note both methods must be applied **inside the worker thread**
  (`_run_workflow:250`, `_continue_workflow:1263` each create a new event loop
  in a new daemon thread), because OTel context is contextvar-based and does
  not cross a `threading.Thread` boundary.

### C.4 — Custom spans for DB / connector / phase work (the waterfall)

**This is what makes the trace explain the 27 minutes** rather than just
showing one long `resolve_entities` bar.

**It works cleanly because of a verified SDK behaviour:** the Langfuse
LangChain handler calls
`context.attach(trace.set_span_in_context(observation._otel_span))` in
`_attach_observation` (`CallbackHandler.py:709`). The node's span therefore
**is** the current OTel span while the node body runs, so any span started
inside a node body nests under it automatically — no manual parent plumbing.

**Not raw OpenTelemetry — tried first, verified NOT to work.** A span opened
via the plain `opentelemetry` API (`trace.get_tracer(...)
.start_as_current_span(...)`) does correctly nest under the ambient context
and produces a real SDK span with a valid trace/span id — but Langfuse's own
`LangfuseSpanProcessor` silently drops it at export time. It filters every
span by `instrumentation_scope.name` against an allow-list of Langfuse's own
tracer name plus known LLM-library instrumentations
(`langfuse/_client/span_filter.py`'s `KNOWN_LLM_INSTRUMENTATION_SCOPE_PREFIXES`),
and an arbitrary third-party scope name is not on that list. **Verified live:
0 of 2 raw-OTel spans reached the Langfuse UI.** The fix is to go through
Langfuse's own `client.start_as_current_observation(as_type="span", ...)`
API instead — verified live afterward: 3/3 custom spans exported with
correct `parent_observation_id`, and correctly marked `level=ERROR` when the
wrapped code raised. `get_client()` is safe to call from a module that
didn't itself initialize the SDK: it is keyed internally by the project's
public key, not caller identity, so a second call from `src/utils/tracing.py`
reaches the same trace already in progress from `src/llm/tracing.py`'s
earlier `get_client()` call.

**Where the helper lives (architecture-lint constraint, verified).**
`scripts/lint_arch.py:96` explicitly allows *anything* below `src/api` to
import `src/llm`; and `src/llm` may itself import only `src/llm.*` and
`src/utils` (`:88-95`). Connectors sit at the second-lowest layer, so:

- `src/utils/tracing.py` — a `@contextmanager span(name, **attrs)` that
  checks `LANGFUSE_PUBLIC_KEY` itself (not imported from `src.llm.tracing`,
  specifically so this module never reaches upward) and yields either the
  real Langfuse observation (with `.update(output=...)` for attaching
  results computed only after the wrapped call finishes) or a no-op
  stand-in — so every call site can call `.update(...)` unconditionally,
  with no `if obs is not None` guard needed. Everything, including
  connectors, may import `src/utils`.
- Leave the Langfuse/LangChain handler wiring in `src/llm/tracing.py`, which
  may import `src/utils`.

This ordering matters: putting the generic helper in `src/llm` and importing
it from a connector would pass the lint (rule at `:96`) but inverts the
dependency story; `src/utils` is the honest home.

**Spans added, all done and live-verified:**

| Span | Where | Attributes recorded |
|---|---|---|
| `resolve.<entity>.blocking` | around `_generate_candidate_pairs` (`resolve_entities.py`) | `candidate_pairs`, `uncompared_pairs`, `blocks_used`, `blocks_purged` |
| `resolve.<entity>.fit_model` | around `_fit_match_model` | pair count, whether a model was fitted |
| `resolve.<entity>.scoring` | around the scoring loop | pairs scored, borderline count |
| `resolve.<entity>.clustering` | around `_build_clusters` | `clusters`, `auto_matched`, `auto_excluded`, plus `clustering_stats` |

`extract.<origin>.<table>` and `load.<table>` were deliberately **not built**,
same judgment call and reason as B.2: `extract_data` is ~1% of a full run's
wall time and already shows up as its own node-level span once C.2's nesting
fix landed; only the inside-a-node breakdown for `resolve_entities` (66% of
wall time) was worth hand-building. Revisit if a future measurement shows
extraction becoming a real cost center.

For **B.3's process pool**, `resolve.<entity>.*` spans are emitted in the
*parent* around `run_in_executor`, attaching the worker's returned stats —
OTel context does not cross process boundaries.

### C.5 — Automated live verification (the demo gate)

`scripts/diagnostics/verify_langfuse_trace.py <thread_id>` queries the
Langfuse API (`client.api.trace`, `client.api.observations`) and asserts:

1. Exactly **one** trace carries `session_id == thread_id` (proves C.3).
2. Every executed node appears as a `CHAIN` span, matching the `timings` keys
   in `logs/run_reports/<thread_id>.json` — cross-checked against the app's
   own independent record.
3. Every `GENERATION` span has a **non-null parent** that is a node span
   (proves C.0/C.2).
4. Generations carry model name and non-zero token counts.
5. The custom spans from C.4 are present with their attributes.
6. Span durations are within a tolerance of the run report's `timings`.

Wired into `drive_app.py --assert-budgets`.

### C.6 — Flush lifecycle

`flush()` is called on FastAPI shutdown and at the end of `_run_workflow` /
`_continue_workflow`. The one-shot init latch retries on a bounded schedule
rather than staying permanently dead if the Langfuse stack starts after the
app.

### §C verification (all live — this is the manager-facing gate)

All six assertions in `verify_langfuse_trace.py` passed on a real Boeing run;
one trace, node spans in execution order, generations nested beneath their
nodes, custom resolve-phase spans present, one session spanning all HITL
resumes, zero Langfuse warnings/exceptions in the app log for the whole run.

---

## Part 1.5 exit criteria

- [x] `scripts/diagnostics/profile_resolve.py` exists; A.1–A.5 produce
      **byte-identical** cluster JSON vs the A.0 baseline.
- [x] `python -m tests.eval.score` → precision **1.0000**, recall **0.9950**
      (unchanged). A.6 and any B change re-verified against this.
- [x] `python scripts/check.py all` → 9/9.
- [x] One full Boeing run: total wall time and per-node `timings` reported
      against the **40.7 min / `resolve_entities` 26.8 min / `map_fields`
      6.3 min** baseline.
- [x] Two consecutive runs produce identical `pending_schema_changes`,
      `unmapped_fields`, and `errors` ordering (parallelism determinism).
- [x] Boeing parity is **not worse** than the 0.9% recorded in
      `BASELINE_V1.md` §4c.
- [x] `verify_langfuse_trace.py` passes all six assertions on that run.
- [x] `BASELINE_V1.md` and `BUDGETS.yaml` updated with the new timings, and
      `docs/diagnostics/BOEING_SPEED_REPORT_2026-08-04.md` §4a corrected —
      its C0a attribution is wrong (see this section's Context).

---

# PART 1.6 — Configurability, investigation UI, and cross-platform portability on `main`

> ✅ **COMPLETE.** See `TODO.md`'s "Part 1.6" section for the full
> live-verified record of §A/§B/§C/§D, including the three real bugs found
> and fixed during verification (the FastAPI module-scope Pydantic-model bug,
> the Settings-page save-pins-everything bug, and the Docker
> `127.0.0.1`-binding bug).

## Context — why this exists and why it lands on `main` before the gate

Same rationale as Phase 0 and Part 1.5, plus three new drivers found by
inspecting the live app during this planning pass:

1. **Configuration is trapped in files and needs a restart.** Run-by-run knobs
   (LLM `model` + the 8-entry `fallback_models` chain, `temperature`,
   `entity_resolution_enabled`, `field_mapping_enabled`, `retry.*`,
   `extraction.default_batch_size`/`max_batch_size`) all live in
   `config/config.yaml` and are read at process start. The UI
   (`PipelineScreen.tsx`) exposes only: saved profile, save-as, full-remerge,
   company preset. There is no way to change matching strictness, conflict
   policy, model, or batch size for a single run without editing YAML and
   restarting. Worse, `matching_rules` and `conflict_policy` are already
   `PINNED_KEYS` in `profiles.py:45-50` and already threaded through
   `MergeState` — **but nothing in the API or UI ever writes them** (the plan's
   own B4.4e confirms this). The config surface is half-built and dead.
2. **The pipeline is a black box.** An operator watching a run sees a stepper, a
   status line, an event log, and a data-quality grade (`RunScorecard`,
   `PipelineScreen.tsx:403`). When Boeing lands at 0.9% parity there is no
   in-app way to ask *why* — no view of the candidate pairs, the match
   evidence, the clusters, the per-node timings, the token spend, or the
   source/destination data. Every such investigation this project has done was
   a hand-written SQL query or a `compare_to_golden.py` run.
3. **"Clone and run on another platform" is unproven and partly broken.** The
   conda env `na180` / `C:/Users/rezas/miniconda3/...` is hardcoded in
   `.claude/launch.json:6`, `CLAUDE.md`, and ~6 docs; source `.py` is clean
   (verified) but the *instructions* point at one machine. `scripts/check.py`
   already uses `sys.executable` so it is env-agnostic once invoked. The
   `Dockerfile` is **stale and non-functional**: it references the removed
   Gradio UI, runs the broken `--mode both` (CLAUDE.md: "`--mode both` is
   broken"), exposes a port for a transport that no longer exists, and **never
   runs `npm run build`** — so the image would boot to the api-only stub with
   no UI. There is no bootstrap script and no `environment.yml`.

Decisions locked with the user for this part (2026-08-05): **venv + Docker** as
the canonical from-scratch path; **all four** investigation tools; config split
into an **Advanced panel (run-by-run) + a Settings page (platform)**; and a
**Docker/venv smoke test in CI** as the portability enforcement gate. Like 1.5,
this lands on `main` before the branch gate because it edits `src/api/main.py`,
`src/ui/frontend/**`, `config/config.yaml`, `Dockerfile`, `.pre-commit-config.yaml`,
`scripts/check.py`, `CLAUDE.md` and docs — all files 3–5 branches would collide on.

**Ordering:** C (portability) and D (CI re-audit) are the substrate and may be
done first; A (config) before B (investigation UI) since two of the four tools
read config the A work introduces. Each task is live-verified per R9 — a real
run, an independent check, never the app's own success message.

---

## 1.6.A — Run-by-run + platform configuration surface

### A.1 — A typed config model, layered file → env → per-run override

Introduce one resolved-config path so a value can come from `config.yaml`,
be overridden by an env var (platform), then overridden per-run by the UI
(run-by-run) — without a restart. Reuse the existing loader in
`src/utils/config_loader.py` (do not add a parallel one). The per-run overrides
ride on the **existing** `MergeState` fields and `profiles.py` `PINNED_KEYS`
(`matching_rules`, `conflict_policy`, `field_mappings`, `entity_tables`) plus a
new small `run_config` fragment (model, temperature, batch size, which gates to
auto-approve, `entity_resolution_enabled`/`field_mapping_enabled`). Per R3, add
it as its own `state/` fragment, not by widening the core `TypedDict` body.

### A.2 — Backend endpoints (extend `src/api/main.py`, don't restructure it)

- `GET /api/config` — the effective resolved config (secrets stripped via
  `profiles.strip_secrets`, `profiles.py:65`), tagged per key with its source
  (`file` | `env` | `default`) so the Settings page can show provenance.
- `PUT /api/config/platform` — persist platform settings (ports, `base_url`,
  Langfuse on/off + host, secrets *location*, default destination type) to a
  gitignored local override file, never into `config.yaml` (which is committed).
- `run_config` and `matching_rules`/`conflict_policy` travel in the existing
  `POST /api/workflow/start` body (they are already `MergeState`/profile keys) —
  this is the fix for the dead config surface, not a new subsystem.

### A.3 — UI: Advanced panel (run-by-run) + Settings page (platform)

- **Advanced panel**: a collapsible section on the Pipeline start card
  (`PipelineScreen.tsx`, beside the existing profile/save-as/full-remerge
  controls) — model + fallback chain, temperature, batch size, per-entity
  matching strictness, conflict policy, and per-gate auto-approve toggles.
  Reuses the Phase O profile round-trip so an Advanced config can be saved as a
  profile and replayed.
- **Settings page**: a new top-level screen (sibling of Setup/Pipeline in
  `App.tsx`) for platform/system settings — ports, `base_url`, Langfuse
  enable + host, destination default type, and a **read-only** view of where
  secrets are expected (`.env` keys present/absent, never their values).
- Both are strings-and-selects over `/api/config`; no new heavy components.

### A.4 — Make the dead keys live (the concrete correctness win)

Wire `matching_rules` and `conflict_policy` from the request body →
`MergeState` → the nodes that already read them (`resolve_entities_impl` reads
`matching_rules`; `unify_entity_group` reads `conflict_policy`). Fix the false
comment at `resolve_entities.py` claiming `extraction_config["max_comparisons"]`
is read (flagged in B4.4e). This unblocks per-run matching control that the
schema was already built for.

---

## 1.6.B — Investigation UI (all four tools; read-only, reuse existing data)

Every tool reads data the pipeline **already produces** — none require new
measurement. All are read-only; none can mutate a run or a database.

### B.1 — Match & cluster inspector

*Why did/didn't these two records merge, and what clusters formed?* Surfaces the
Fellegi–Sunter evidence the matcher already computes: per candidate pair the
comparison vector, the agree/disagree/weak levels, the score, and the
auto-match/review/exclude verdict; per cluster the members, confidence, and
`required_human_review`. Source: the `resolution_stats` / cluster result dict
`resolve_entities_impl` already returns and the `human_review_matches` interrupt
payload already carries (`_build_interrupt_data`). New endpoint
`GET /api/workflow/{id}/matches` reading the run's spool + result; a
`MatchInspector.tsx` card. This is the highest-leverage tool for the parity
question.

### B.2 — Run trace & cost timeline

Per-node waterfall with durations from `logs/run_reports/<id>.json`'s `timings`
key (added in §0.6) rendered as a horizontal bar timeline, plus the per-run
token/cost ledger. The cost half already has an endpoint —
`GET /api/workflow/{id}/cost` (`src/api/main.py:295`) — currently unused by the
UI. Add a small `RunTimeline.tsx`; link out to the Langfuse trace URL that
`verify_langfuse_trace.py` already knows how to build.

### B.3 — Schema & data explorer

Read-only browse of any configured origin/destination: tables, columns, types,
row counts, a small sample of rows, and the destination's watermark /
`merge_engine_extraction_state`. Reuses the discovery path behind
`POST /api/connections/test` (which already returns tables/columns/counts) plus
a new bounded `POST /api/explore/sample` (hard `LIMIT`, read-only, parameterised
— never string-interpolated SQL). A `DataExplorer.tsx` screen.

### B.4 — Run comparison / reconciliation

Compare a run against the golden (or a previous run): per-table parity, matched
vs missed rows, and `fields_dropped_from_mapping` / `unmapped_fields`. Wraps the
existing `scripts/diagnostics/compare_to_golden.py` (already the project's
independent evaluator) behind `GET /api/runs/{id}/diff`, rendering its JSON in a
`RunDiff.tsx` table. Makes the number in `BASELINE_V1.md` §4c inspectable in-app.

---

## 1.6.C — Cross-platform portability (venv + Docker)

### C.1 — De-hardcode the environment

Remove every `na180` / conda-absolute-path reference from `.claude/launch.json`
(point `runtimeExecutable` at `python` on PATH), `CLAUDE.md` (state "any Python
3.11 env — venv or conda; the hard constraint is the *version*, not the tool"),
and the docs that carry it (`TODO.md`, `docs/plans/*`, `docs/verification/*`).
Add a `no-hardcoded-conda-path` check to `scripts/lint_docs.py` (or a pre-commit
hook) so it can't silently return — per R11, every failure becomes a rule.

### C.2 — Bootstrap + environment truth

- `scripts/bootstrap.py` (cross-platform, Windows-first per the standing
  shell): create a `.venv`, `pip install -e ".[dev,perf]"`, `npm install &&
  npm run build` in `src/ui/frontend`, copy `.env.example`→`.env` if absent,
  and print the exact run command. One command from a fresh clone to a running
  app.
- Pin the Python version explicitly (`.python-version` + `requires-python` in
  `pyproject.toml`) so "another platform" can't silently use an incompatible
  interpreter.
- `.env.example` must list **every** variable the app reads (`LLM_API_KEY`,
  `LLM_BASE_URL` if externalised from `config.yaml`, `LANGFUSE_*`) — a fresh
  clone currently can't know what to fill in.

### C.3 — Rewrite the Dockerfile + a runnable compose

The current `Dockerfile` cannot run the app (stale Gradio/`--mode both`, no
frontend build). Rewrite as a three-stage build: (1) `node` stage runs
`npm ci && npm run build` → `dist/`; (2) `python` builder installs deps; (3)
slim runtime copies `src/`, `config/`, and the built `dist/`, runs
`python -m src.main --mode gradio --port 7860` (the real command), exposes only
7860, healthchecks `/`. Add a `docker/app-compose.yml` wiring the app to the
existing origin/destination/Langfuse services so `docker compose up` gives a
working stack. Keep `dist/` build-time only — never committed (CLAUDE.md rule).

### C.4 — The portability test

`scripts/check.py portability` (new subcommand) + `tests/` coverage that proves
a from-scratch install works without the developer's machine:

- **venv path**: in a temp dir, create a clean venv, `pip install -e .` from the
  repo, `python -c "import src.main"`, and assert the app boots to a real UI
  (not the `{"status":"api-only"}` stub — hit `/` and check for the SPA), all
  with **no conda and no `na180` on PATH**.
- **Docker path**: `docker build` the rewritten image and run a boot smoke test
  (container comes up, `/` serves the SPA, `/api/workflow/active` responds).
- **Hardcoded-path guard**: a test that greps the whole tree for machine-
  specific absolute paths and fails on any new one (the mechanical form of C.1).

---

## 1.6.D — CI/CD + branch-conflict re-audit (recheck everything, per the request)

### D.1 — Portability smoke in CI

Wire `check.py portability` (C.4) into the CI config so every push proves a
clean checkout builds and boots. CI runs exactly `python scripts/check.py all`
plus the portability job — no logic CI can run that a developer can't (R1).

### D.2 — Re-audit the six conflict seams (§0.8) against the current tree

Confirm each still does its job after 1.5/1.6's edits, with an assertion, not by
eye: `MergeState` is still assembled from `state/` fragments (no feature keys
inlined into the core `TypedDict` body); node/edge wiring is still the
declarative `NODE_SPECS` table; `build_reconciliation_report` still composes via
`REPORT_CONTRIBUTORS` (the new `run_config`/investigation data must register a
contributor, not edit the body); `pyproject.toml` extras still cover every
branch's deps (add none for 1.6 beyond what exists); `errors`/passthrough
hygiene intact; no module-level mutable state re-introduced in
`mcp_server/tools/` (arch-lint stays at 15). Record any drift as a fresh
arch-lint/pre-commit rule (R11).

**Found during the live re-audit (recorded honestly, not silently fixed):**
`NODE_SPECS` and `REPORT_CONTRIBUTORS` were part of the *aspirational* §0.8
design table but were **never actually built** — `graph.py` still wires all 17
nodes with inline `add_node`/`add_conditional_edges` calls, and
`build_reconciliation_report` is still one function body. R3 is therefore
currently unenforceable as written. **Whichever of B2 (new nodes) / B3 (new
report sections) starts first should build the specific registry it needs.**

### D.3 — Pre-commit + secret hygiene recheck

Re-run every pre-commit hook against the full tree (not just staged files) to
confirm none silently stopped matching after the reorg; confirm
`detect-private-key` + the `dashscope`/`sk-` pattern hook still fire; re-verify
the `no-str-format-on-prompts`, `no-sample-databases-commit`,
`no-blanket-data-ignore`, `no-new-bare-except-pass`,
`no-import-time-graph-compile` hooks each still block a deliberately-bad commit.
Re-confirm the `git.n4t.com` server-CI status against ADR 0003 — if still
unreachable, keep the documented fallback and make the reachable CI (Docker/venv
smoke) the real gate; do not pretend server CI exists.

---

## Part 1.6 exit criteria

- [x] Run-by-run config (model, matching strictness, conflict policy, batch
      size, per-gate auto-approve) is changeable in the UI and takes effect on
      the next run **without a restart**.
- [x] `matching_rules` / `conflict_policy` set in the UI reach the matcher and
      unifier live (the previously-dead config surface).
- [x] All four investigation tools render real data from a real Boeing run.
- [x] A fresh clone runs on a machine with **no conda and no `na180`** via
      `scripts/bootstrap.py` (venv) and via `docker build` + `docker compose up`
      (image), both booting to the real React UI, not the api-only stub.
- [x] `scripts/check.py portability` passes; the hardcoded-path guard fails on a
      seeded bad path and passes on the cleaned tree; zero `na180`/conda-absolute
      references remain anywhere (code, docs, `.claude/`).
- [x] `python scripts/check.py all` → green; arch-lint still 15; every pre-commit
      hook re-verified to block its target; four of six §0.8 seams re-asserted
      intact (`NODE_SPECS`/`REPORT_CONTRIBUTORS` flagged as never-built, above).
- [x] No correctness change: a Boeing run still completes at the documented
      parity.

---

# 🛑 BRANCH CREATION GATE — passed 2026-08-05

**Report delivered to the user:** Phase 0, Part 1.5, and Part 1.6 results
against every exit criterion, with the honest open items called out
(§0.10's flagged secret rotation, the `NODE_SPECS`/`REPORT_CONTRIBUTORS` gap,
`git.n4t.com` still unreachable at the git-protocol level).

**Go-ahead received 2026-08-05.** Everything above this line was committed to
`main` as one checkpoint commit, then the five branches below were created
exactly as described in the READ-THIS-FIRST preamble — branch, write
`Branch_Plan.md` and `TODO.md`, commit, return to `main`. The user was
notified with the branch list before any branch's implementation work began.

---

# The five branches

All five branch from the same Phase-0/1.5/1.6 completion commit. **Ownership is
exclusive**: a branch may *read* any file, but may only *modify* files in its
Owns column. Anything needed outside that set is a request to `main`, not an
edit.

| Branch | Owns (exclusive write) | Never touches |
|---|---|---|
| `feat/dspy-prompt-layer` (B1) | `src/llm/**`, `src/llm/prompts/**`, `tests/prompts/**` | `src/orchestrator/**`, `src/mcp_server/**` (call sites change only via the adapter's stable API) |
| `feat/planner-swarm` (B2) | `nodes/parse_request.py`, `nodes/analyze_dependencies.py`, `nodes/plan_scope.py`, new `src/orchestrator/planning/**`, `state/planner.py` | `src/llm/prompts/**` (B1 owns those), anything after `extract_data` |
| `feat/quality-lineage` (B3) | new `nodes/validate_data.py`, `tools/data_quality.py`, `orchestrator/utils/lineage.py`, `state/quality.py`, `state/lineage.py`, `Gates.tsx` quality/lineage panels | `tools/resolve_entities.py`, `src/llm/**` |
| `feat/matching-precision` (B4) | new `tools/identity_profile.py`, `tools/relational_evidence.py`, `tools/splink_matcher.py`; the scoring/evidence half of `resolve_entities.py` (`_build_comparison_vector`, `_comparison_level`, `_judge_pairs_with_llm`, engine dispatch); `state/matching.py`; `api/main.py` matching-rules fields; `PipelineScreen.tsx` advanced-matching disclosure | `_build_clusters`, `_partition_component`, `_average_linkage`, `_split_weakest_link`, `_assign_cluster_id` — **may call, must not modify** (settled on `main` by §0.0 D.2) |
| `chore/harness-ratchet` (B5) | `CLAUDE.md`, `AGENTS.md`, `docs/**`, `.claude/**`, `scripts/lint_*.py`, `scripts/check.py`, `.pre-commit-config.yaml`, CI config | all of `src/` |

> **Note:** the sixth branch in earlier drafts, `perf/pipeline-throughput`, no longer
> exists — it was pulled onto `main` as §0.0's BUG D, because §0.0's correctness
> fixes and the throughput work modify adjacent code in `resolve_entities.py` and
> because C1/C2 make clustering cost explode. `resolve_entities.py`'s clustering
> functions are therefore **`main`-owned and frozen** for all five branches.

---

## B1 — `feat/dspy-prompt-layer`

**Goal:** replace two hand-tuned prompt stages with DSPy programs that can be
*optimized against a metric* instead of hand-edited, without losing any of the
production plumbing wrapped around `LLMClient`.

**Resources for this branch:**

- DSPy docs: https://dspy.ai/
- DSPy `BaseLM` reference (for the adapter): https://dspy.ai/api/models/LM/
- `BootstrapFewShot` / `MIPROv2` optimizer docs: https://dspy.ai/learn/optimization/optimizers/
- DSPy GitHub (issues/examples are often more current than the docs site for a
  fast-moving library): https://github.com/stanfordnlp/dspy

**B1.1 — The `dspy.LM` adapter (do this before touching any stage).** DSPy's default
LM knows nothing about this project's four load-bearing behaviours. Subclass
`dspy.BaseLM` (verify the exact base class against the installed DSPy version) so
`forward()` delegates to the existing `LLMClient` and preserves **all four**:

- the quota-fallback chain (`_advance_model` / `_is_quota_error`, `client.py:102-118`,
  walking `config.llm.fallback_models`' nine models);
- `SEMANTIC_CACHE` (`cost.py:219`) — and use B2's chance to make caching happen
  *after* a successful parse, not before (the 0.8 fix, if it hasn't landed);
- `COST_LEDGER` (`cost.py:220`), partitioned by `run_id`;
- `trace_config()` Langfuse callback injection (`client.py:206`).

Unit-test the adapter against the existing `TestLLMClientModelFallback` suite shape.
Note: those tests need `"cache_enabled": False` because `SEMANTIC_CACHE` is a
process-wide global that cross-pollutes tests.

**B1.2 — Signatures for the two pilot stages.**

- `request_parsing` — outputs `entity_types`, `explicit_entity_types`, `subject`,
  `scope_type`. Golden: 14 cases.
- `field_mapping` — outputs a **bare, dynamically-keyed object** (`{source_field:
  {action, ...}}`), which does not fit a fixed DSPy signature. Reshape to
  `list[FieldDecision]` inside the module and convert back at the boundary, so
  `map_fields.py:492-495`'s filtering contract is unchanged. Golden: 8 cases.

**Both output contracts must stay byte-identical in key names.** The 18-value entity
vocabulary in `request_parsing.system.txt:3-22` is mirrored by hand in
`src/orchestrator/utils/entity_types.py` and guarded by
`tests/unit/test_entity_aliases.py` — a DSPy rewrite must keep them in sync or that
test fails, which is exactly what it's there for.

**B1.3 — Metrics from the existing scorer.** `tests/prompts/harness.py:172-282`
already implements per-stage scoring (set equality on `entity_types`; per-field
`action` + `dest_column` equality for mappings). Lift those into DSPy metric
functions. **Do not invent new metrics** — the whole point is comparability with
`scorecard_latest.txt`.

**B1.4 — Optimize, honestly.** 14 and 8 cases support `BootstrapFewShot`
(documented as viable from ~10 examples). They do **not** support `MIPROv2`, which
wants 200+ examples and 40+ trials — say so rather than running it on thin data.
Path: `LabeledFewShot` baseline → `BootstrapFewShot` → grow the golden sets → then
reconsider `MIPROv2`/`GEPA`. Save compiled programs with `.save()` (plain-text JSON)
and **check the artifacts into the repo** — a compiled program is a build output the
run depends on, and an unreproducible optimizer run is worse than a hand-written
prompt.

**B1.5 — Fix the harness drift found during planning.**
`tests/prompts/harness.py:100-166` (`_build_prompt`) hand-mirrors production's
`.replace()` chains and has **already drifted**: it omits the appended user-request
suffix that `select_entity_tables.py:203-204` really sends, and it applies no retry
hints. DSPy removes the need for that mirror entirely for converted stages — for the
four not yet converted, make the harness import the production prompt builder rather
than reimplementing it.

**B1.6 — Keep every heuristic floor.** All six stages degrade to a non-LLM fallback
(heuristic table selection, heuristic scope plan, `_compute_heuristic_confidence`,
`_heuristic_match`). Those floors are what keep a run alive when the LLM is down and
they are the natural DSPy baseline. Do not remove them.

**Acceptance gates:**

- `python -m tests.prompts.harness --live` scores **≥ the regenerated
  `BASELINE_V1.md` scorecard** on both converted stages, with the other four
  unchanged.
- The adapter test suite proves quota fallback, cache, ledger and tracing all still
  fire — each asserted independently.
- A full Boeing run completes with parity ≥ 12.0%.
- The four unconverted stages are provably untouched (byte-identical `.txt` files).

---

## B2 — `feat/planner-swarm`

**Goal:** remove the breadth ceiling. Today `parse_request` picks entity types from a
fixed 18-value enum *before anything has looked at the origin catalog*, so "merge all
information about Boeing" yields `["company","person","product"]` — 3 of 11 families —
and `product` (which exists in no origin) hard-errors in `extract_data`. This is the
largest single identified contributor to the 12% parity gap.

**Resources for this branch:**

- `langgraph-swarm` docs: https://langchain-ai.github.io/langgraph/reference/swarm/
- `langgraph-swarm` GitHub (`create_swarm`, `create_handoff_tool` source + examples): https://github.com/langchain-ai/langgraph-swarm-py
- LangGraph multi-agent concepts overview: https://langchain-ai.github.io/langgraph/concepts/multi_agent/
- LangGraph subgraphs (for nesting the swarm inside the main DAG): https://langchain-ai.github.io/langgraph/how-tos/subgraph/

**B2.1 — The planning subgraph.** Build `src/orchestrator/planning/` as a
`langgraph-swarm` graph with `create_swarm([...], default_active_agent=...)` and
`create_handoff_tool(agent_name=..., description=...)`. Agents:

| Agent | Job | Hands off to |
|---|---|---|
| `SchemaExplorer` | queries the real origin catalog + FK graph; answers "does a table for X exist, and what's actually in it?" | EntityPlanner, TableSelector |
| `EntityPlanner` | derives the entity closure for "everything about `<subject>`" from the **actual FK graph**, not an enum | SchemaExplorer (to verify), TableSelector |
| `TableSelector` | resolves each entity to concrete `(origin_id, table)` pairs | ScopePlanner, EntityPlanner (to revise) |
| `ScopePlanner` | produces the `match`/`link`/`link_via` scope plan for the subject | TableSelector (to revise) |

The Boeing loop this makes possible, and which the current fixed DAG structurally
cannot do: EntityPlanner proposes `product` → SchemaExplorer reports no product table
anywhere *and* surfaces the unrequested `CompanyFinancials` (199 rows),
`CompanyTechnographics` (20,093), `CompanyRelation` (6,175) → EntityPlanner revises.

**B2.2 — The contract boundary is absolute.** The subgraph must emit exactly the
state keys the existing DAG expects — `target_entities`, `explicit_target_entities`,
`merge_scope`, `dependency_tree`, `extraction_order`, `entity_tables`,
`table_selection_notes`, `scope_plan` — and the **same `human_review_deps` and
`human_review_scope` interrupt payloads** (built by
`human_nodes.py::_build_scope_interrupt` and mirrored in
`WorkflowManager._build_interrupt_data`, because `interrupt_before` means HITL node
bodies never run). Nothing after `extract_data` may change. `Gates.tsx` should need
no edits; if it does, the contract was broken.

**B2.3 — `SwarmState.active_agent` must not leak into `MergeState`.** Run the swarm
as a nested compiled graph whose input and output are plain dicts; keep
`active_agent` and the swarm's own `messages` inside it. Otherwise it lands in the
SQLite checkpoint and every resume path has to reason about it.

**B2.4 — Bound the loop.** Handoff graphs can ping-pong. Set a hard handoff cap and a
wall-clock budget; on exhaustion, fall back to the current deterministic nodes and
record *why* in `table_selection_notes` so it surfaces at the deps gate. A planner
that spins for 20 minutes is worse than one that gives a mediocre answer in 30
seconds.

**B2.5 — Default off.** Ship behind `planning.engine: "swarm" | "sequential"`
(default `sequential`), the same posture Q.1 uses for Splink. This branch cannot
regress an existing run if it is not on the path.

**B2.6 — Widen `entity_types.py` and the alias groups** to whatever the FK-derived
closure actually produces, keeping `tests/unit/test_entity_aliases.py`'s invariant
(every canonical type has exactly one alias group; no alias appears in two).

**Note on `NODE_SPECS`:** Part 1.6.D's re-audit found this declarative node-wiring
table was never actually built (§0.8 was aspirational there). If this branch adds
new nodes to the planning subgraph, either build the minimal `NODE_SPECS` registry
first (small, self-contained — it only needs to cover this branch's owned files) or
wire the subgraph's entry/exit points by hand and say so plainly in `Branch_Plan.md`
rather than pretending the registry exists.

**Acceptance gates:**

- Boeing under the swarm planner reaches **≥ 9 entity families** (v1: 3) at the
  `human_review_deps` gate, verified by reading the gate payload directly.
- No `product`-style hard error: an inferred-but-absent entity warns, never
  escalates.
- Parity improves measurably vs 12.0% — report the number honestly whatever it is.
- With `planning.engine: sequential`, behaviour is bit-identical to `main`.
- Airbus and Lockheed also run (neither has been re-verified since the eight v1
  bug fixes — this branch is the natural place to close that gap).

---

## B3 — `feat/quality-lineage`

**Goal:** Phase Q's Q.2 and Q.3, together in one branch because both extend the
reconciliation report and would otherwise conflict with each other.

**Resources for this branch:**

- Great Expectations docs: https://docs.greatexpectations.io/docs/
- GX Core Python API (expectation suites, validation): https://docs.greatexpectations.io/docs/core/introduction/
- `sqlglot` (optional SQL-lineage enrichment): https://sqlglot.com/sqlglot.html
- `sqlglot` lineage module specifically: https://sqlglot.com/sqlglot/lineage.html

**B3.1 — Q.2, Great Expectations gate.** New node `validate_data` between
`extract_data` and `resolve_entities` (register via 0.8's `NODE_SPECS` table, not by
editing `graph.py` by hand — **see the same `NODE_SPECS`-does-not-exist note as
B2.6**: build the minimal registry this node needs, or wire it by hand and document
that choice). Logic in `tools/data_quality.py` so the node stays thin.
Per-entity expectations: uniqueness on declared `unique_id_fields`; not-null on the
detected name column (warn); sane bounds on `*_year`/`*_date` (warn); and
`row_count >= 1` as a **hard fail** — that one would have caught the P.0 watermark
bug at exactly the right place. Emit `quality_report` with
`severity ∈ {"warn","fail"}`.

**Behaviour on failure: report and flag, never auto-abort.** A `fail` sets a flag
shown prominently at the existing `human_final_approval` gate; the human decides.
This matches the project's "fail loudly, human decides" posture and avoids adding a
seventh gate. Wrap GX in try/except — a GX failure degrades to "validation
unavailable", never crashes a run.

Note this is *row-content* validation after extraction, distinct from
`nodes/preflight.py`'s pre-run structural checks (reachability, writability, LLM
liveness). Do not duplicate preflight.

**B3.2 — Q.3, column-level lineage.** `orchestrator/utils/lineage.py::build_lineage`
joins `field_mappings[entity]` (source_field → dest column) with, per source field,
the set of `(origin_id, origin_table)` that actually carried it — derivable from the
spool rows, which already carry `_origin_id`/`_origin_table`/`_origin_id_row`.

**The highest-value piece is already computed and thrown away:** `_conflicts` from
`unify_entity_group.py:236` records, per conflicted field,
`{field, values:[{origin, value}], selected_origin, selected_value, winning_rule}` —
that *is* column-level lineage — and `_transform_records`
(`tools/load_data.py:208`) discards it. Preserve it into the lineage output.

`sqlglot`-based SQL-expression lineage is an **optional enrichment** used to
*validate* the mapping-derived lineage, not replace it. If it proves awkward over
parameterised SQL, ship without it and say so — the mapping-derived piece is the
user-visible win.

**Record the known limitation** rather than papering over it: the destination row's
`_source_ids` currently only carries `[{"cluster_id": …}]`
(`tools/load_data.py:242`), not real origin row ids, and the durable
`merge_engine_entity_map` exists **only on a PostgreSQL destination**
(`postgresql_destination.py:766`; ClickHouse has no `save_entity_map`).

**B3.3 — UI.** A quality summary panel and a collapsible lineage section in
`Gates.tsx`'s `SummaryBody` (`:314`). Read-only; no editor.

**Note on `REPORT_CONTRIBUTORS`:** same finding as `NODE_SPECS` above — this
registry was never built. `quality_report` and `lineage` need to land in the
reconciliation report; either build the minimal `REPORT_CONTRIBUTORS` list
(`build_reconciliation_report` calling each registered contributor and merging
its dict) as a small, self-contained refactor first, or add the two keys directly
to `build_reconciliation_report`'s body and say so explicitly in `Branch_Plan.md`
— do not silently assume the registry exists.

**Acceptance gates:**

- A Boeing run report carries non-empty `data_quality` and `lineage` keys.
- Deliberately seed one duplicate unique-id and one 0-row entity; prove `fail`
  severity surfaces at the final gate and that the run still reaches the gate.
- Lineage traces at least one multi-origin destination column back to both
  `origin1` and `origin2` source fields, and flags a created column.
- Graph-wiring tests confirm `validate_data` sits between extract and resolve and
  that `_derive_skipped_gates` and every router are unaffected — this node is **not**
  a HITL gate and must not interrupt.

---

## B4 — `feat/matching-precision`

**Goal:** raise matching precision *and* recall structurally, not by tuning
thresholds. §0.0's C0–C3 fixes make the matcher **see** the data; this branch makes it
**reason** over the data. Splink (Phase Q's Q.1) becomes one candidate engine inside
this branch rather than a branch of its own, because it depends on exactly the same
canonical-projection work.

**Resources for this branch:**

- Splink docs: https://moj-analytical-services.github.io/splink/
- Splink API reference (`Linker`, `cluster_pairwise_predictions_at_threshold`): https://moj-analytical-services.github.io/splink/linker.html
- `desbordante` (UCC/FD discovery for B4.1's Step 1): https://desbordante.readthedocs.io/ and https://github.com/Mstrutov/Desbordante
- Fellegi-Sunter model background (already implemented in this repo — reference for
  understanding, not for reimplementation): https://en.wikipedia.org/wiki/Record_linkage#Fellegi%E2%80%93Sunter_model
- Ditto entity-matching serialization paper (`COL/VAL` format referenced in B4.3),
  VLDB 2020 — "Deep Entity Matching with Pre-Trained Language Models":
  https://arxiv.org/abs/2004.00584
- Collective entity resolution background (B4.2), Bhattacharya & Getoor, TKDD 2007 —
  "Collective Entity Resolution in Relational Data": https://dl.acm.org/doi/10.1145/1217299.1217304
- Reference implementations to *read, not adopt* (B4.2): `nishadi/RELATER`
  (https://github.com/nishadi/RELATER), `CGCL-codes/HierGAT`
  (https://github.com/CGCL-codes/HierGAT)
- AnyMatch (evaluated, deliberately not adopted — see B4.3's ADR requirement):
  https://arxiv.org/abs/2405.04870

### B4.0 — Build the measurement that makes all of the above falsifiable (do this FIRST)

**The current eval cannot see any of this.** `tests/eval/golden.py`'s 560 pairs are
all Pagila *people* from **one** source with synthetic corruptions (typos, nickname
swaps, email reformatting). There is **no cross-origin company pair, no divergent
field names, no relational evidence** anywhere in it. Every scenario in B4 is
therefore unmeasurable against it — it would report "no regression" while the real
cross-origin behaviour changed completely.

Build a second golden set from the **real** origin1/origin2 data: sample candidate
company and person pairs across the two origins for Boeing, Airbus and Lockheed,
label them by hand (the three hand-merge baselines — `docs/diagnostics/*_APP_VS_MANUAL_DIAGNOSIS.md`
plus the memory notes on each subject's manual merge — already establish ground
truth). Keep the existing 560 pairs unchanged as the general-dedup regression guard.

**Without B4.0, none of B4.1–B4.4 can be honestly claimed.** Do it first.

### B4.1 — Identity-key discovery from measured data, then LLM classification

The user's instinct — *"find some fact column with help of LLM"* — is right, but the
LLM must be given **measurements**, not asked to guess from column names. Two steps:

**Step 1 (data, no LLM): profile each origin table.** This is the well-studied
*unique column combination* (UCC) and *approximate functional dependency* problem.
`desbordante` (`pip install desbordante`, C++ core with Python bindings) does exact
and approximate UCC discovery with the g1 metric, plus exact/approximate FDs. Per
column, compute:

- uniqueness ratio **within** each origin,
- **value overlap across origins** (after normalisation),
- null rate, cardinality, and a sample.

That single cross-tabulation separates the three cases the matcher currently
conflates:

| within-origin unique | cross-origin overlap | role | how it must be used |
|---|---|---|---|
| ~1.0 | high | **identity key** (website, ticker, tax id, registration no.) | settles a pair; also a **blocking key** (fixes C2) |
| ~1.0 | ~0.0 | **surrogate key** (`companyID`) | `not_comparable` — never evidence (fixes C0b) |
| < 1.0 | any | ordinary signal | normal Fellegi–Sunter comparison |

**Step 2 (LLM): classify and align, grounded in that profile.** Feed the model the
*measured profile* — name, type, cardinality, uniqueness, cross-origin overlap,
samples — and ask for two outputs: a role per column
(`identity_key | strong_signal | weak_signal | surrogate_key | noise`) and a
cross-origin alignment (`origin1.entityName ≡ origin2.name`,
`origin1.fullName ≡ legal_name`). Grounded in statistics this is a task LLMs do
well; asked cold from column names it is the guesswork that produced the 43-column
mess.

Cache per `(origin, table)` and persist into `matching_rules` — which already exists
in `MergeState` and in `profiles.PINNED_KEYS`, and which Part 1.6.A.4 made reachable
from the UI. This is also what makes a re-run reproducible.

### B4.2 — Relational / collective entity resolution (the multi-signal idea)

The user's people example — *"check name, then contact information, then education
and experience and location; do all of them to increase confidence"* — is exactly
**collective entity resolution in relational data** and **relational match
propagation** (see the resources above). It splits into two pieces, and the first is
nearly free:

**(a) Multi-attribute accumulation — already implemented, currently starved.**
Fellegi–Sunter *is* the "check name, then contact, then education" accumulator: match
weight is additive in log₂ space,
`M = log₂(λ/(1−λ)) + Σᵢ log₂(mᵢ/uᵢ)`, one term per attribute, with term-frequency
adjustment so a rare value counts more than a common one. `fellegi_sunter.py`'s
`MatchModel.score`/`.probability`/`.explain` already do this. **Fixing C0 turns the
existing model into the accumulator the user described** — no new algorithm needed.
Verify this first and measure it before building anything more.

**(b) Neighbourhood evidence — the genuinely new capability.** Use *related*
entities as comparison features:

- two `person` records whose resolved **employer cluster-ids** overlap are more
  likely the same person (from `Experience`);
- two `company` records sharing a resolved **CEO/founder** person cluster, or an
  overlapping **contact** or **location** cluster, are more likely the same company.

Implement as a **second pass**, not a rewrite: resolve entities in the existing
`extraction_order` (already topologically sorted), then re-resolve injecting resolved
neighbour cluster-ids as extra comparison fields. Iterate to a fixed point or a small
cap (2–3 passes). The infrastructure is already there — the spool holds per-entity
rows, `merge_engine_entity_map` holds resolved cluster ids, and `extraction_order`
gives the dependency order.

**Risk to state plainly:** relational evidence is how collective ER produces
*cascading* over-merge — one wrong company merge propagates into its people. Every
pass must re-run the precision gate, and the neighbour feature must be capped in
weight so it can never alone push a pair over `auto_match`.

### B4.3 — Better LLM adjudication of the borderline band

`_judge_pairs_with_llm` (`:1296-1408`) already has the right *shape* — bounded to
`_MAX_LLM_PAIRS = 200`, chunked at 40, concurrency 4, degrading to heuristic scores
on failure. The weakness is its **inputs**: it receives stripped raw field dicts,
which for a cross-origin pair means the same 4-field intersection C0 exposed.

- Feed it the **canonical projection** from C0a plus the neighbour evidence from
  B4.2(b), serialized in the `COL/VAL` form the entity-matching literature converged
  on (Ditto, VLDB 2020 — see resources above).
- Ask for **structured evidence, not a verdict alone** — which attributes agreed,
  which conflicted — so it lands in the reconciliation report and at the
  `human_review_matches` gate instead of being an unexplained number.

**AnyMatch — evaluated and deliberately NOT adopted (record as an ADR in
`docs/decisions/`).** A small fine-tuned entity-matching model reporting F1 87.5 vs
GPT-4-based MatchGPT's 87.4 with ~3 orders of magnitude fewer parameters. Genuinely
impressive, and wrong for this project *right now*, for four reasons:

1. **It optimises a component that is not the constraint.** LLM adjudication is
   capped at `_MAX_LLM_PAIRS = 200` per entity type and only sees the borderline
   band. It is not the cost, latency, or accuracy bottleneck — C0 is. Swapping the
   adjudicator model while it is fed 4-field intersections changes nothing.
2. **Its benchmark does not measure this problem.** Those F1 numbers come from
   standard EM benchmarks (Walmart–Amazon, Abt–Buy, DBLP–Scholar) — pairs of
   **already schema-aligned** records. The failure here is *unaligned* cross-origin
   schemas. The benchmark is silent on the thing that is broken.
3. **Real infrastructure cost.** There is no local model serving today; everything
   goes through one OpenAI-compatible endpoint. Adopting it means a torch/
   transformers dependency, a GPU or CPU inference path, a versioned model artifact,
   and it breaks the single `LLMClient` choke point that B1's DSPy adapter is about
   to be built on.
4. **The genuinely useful parts are free.** Take the Ditto-style `COL/VAL`
   serialization (above) without the model, and record the strategic finding — *a
   small local model can match GPT-4 on entity matching* — for when it matters.

**Revisit trigger (write this into the ADR):** if, after B4.0–B4.2, the borderline
band exceeds ~5,000 pairs per run **and** LLM cost or latency is a *measured*
constraint, re-evaluate AnyMatch or a comparable small local adjudicator. Given a
paid API with a nine-model quota-fallback chain already in place, that day may well
come — but the data problems come first, and adopting it now would optimise the one
part of the matcher that is currently working as designed.

### B4.4 — Splink as an optional engine (the original Q.1)

Unchanged in intent, but it now lands **after** C0a/B4.1, because Splink's single
hardest integration problem — invariant 6, the cross-origin schema divergence — is
precisely what the canonical projection solves. Splink then consumes the projection
directly.

**Goal:** Splink as a per-entity **optional** matching engine, default off, at the
`resolve_entities_impl` seam.

**B4.4a — The seam, not a rewrite.** Add
`engine = (matching_rules or {}).get("engine", "builtin")` near the top of
`resolve_entities_impl` (`resolve_entities.py:186`); if `"splink"` and Splink
imports, delegate to `tools/splink_matcher.py::resolve_with_splink(...)` and return
early. Otherwise fall through unchanged. **Do not rewrite `resolve_entities_impl`.**

**B4.4b — Reproduce the result-dict contract exactly** (`entity_type`, `clusters[]`
with `cluster_id`/`record_ids`/`match_confidence`/`required_human_review`,
`total_records`, `auto_matched`, `auto_excluded`, `llm_scored`, `uncompared_pairs`,
`candidate_pairs`, `blocks_purged`, `pending_merge_candidates`).

**B4.4c — Layer on the eight invariants Splink does not provide.** These are what the
hand-rolled matcher earns its keep with; a naïve swap breaks all of them. The full
list is in `docs/plans/PHASE_Q_PLAN.md` §Q.1 and must be copied verbatim into
`Branch_Plan.md`. The three that decide the design:

- **Splink replaces *scoring*, not *clustering*.** Splink's
  `cluster_pairwise_predictions_at_threshold` is raw transitive closure — exactly
  what `_partition_component`/`_split_weakest_link` deliberately reject. Run Splink
  for pairwise probabilities only, then feed them into the **existing**
  `_build_clusters`. **`main` owns those function bodies; this branch may call, never
  modify.**
- **Deterministic cluster IDs.** Splink's own `cluster_id` must be translated
  through the existing `_assign_cluster_id`, which reuses a destination id when a
  member already maps to one, else `deterministic_uuid(entity_type,
  *sorted_member_ids)`. Skip this and every re-run duplicates every destination row.
- **Cross-origin schema divergence.** Splink assumes one unified column schema; our
  records have divergent field names (`company_name` vs `org_title`) because
  `map_fields` runs *after* resolution. Build a **projected comparison view** — a
  small uniform 4–6 column frame per record (`name`, `email`, `ref`, name parts)
  using the same hint logic as `_blockable_fields`/`_best_name_field`.

**B4.4d — Any Splink failure logs a warning and falls back to builtin.** Never crash a
run over an optional engine.

**B4.4e — `matching_rules` is now reachable (Part 1.6.A.4 fixed this).** Add a
per-entity `engine` field to the Advanced panel's matching-rules UI so
`engine: "splink"` is actually selectable, default `Built-in`.

**Acceptance gates:**

- `python -m tests.eval.score` shows **no precision regression** on the original 560
  pairs at *every* step (B4.1, B4.2, B4.3, B4.4 each gated separately). C0–C3 and
  B4.2 all make the matcher compare far more pairs and accumulate more evidence, so
  over-merge risk rises monotonically — this gate is the whole safety mechanism.
- On the **new cross-origin golden set** (B4.0), report precision/recall/F1 before
  and after each step. The cross-origin Boeing↔Boeing pair must resolve as a match.
- B4.2's neighbour evidence never alone pushes a pair over `auto_match` — asserted
  by unit test, not by inspection.
- Extend `tests/eval/score.py` with a scenario running `evaluate(splink_scorer,
  pairs)` over the **same 560 golden pairs**, printed beside the builtin.
  **Bar: Splink precision ≥ builtin's and recall ≥ 0.99.**
- Same Boeing merge run twice — `engine: builtin` and `engine: splink` — cluster
  counts and inserted/updated rows match within a stated tolerance.
- Every existing Phase-L matcher test stays green (the builtin is unchanged and
  still default).
- **If Splink does not beat the builtin, it ships as a documented, default-off
  option. That is an acceptable outcome, not a failure.**

---

## B5 — `chore/harness-ratchet`

**Goal:** the second tier of harness engineering — the parts of the three articles
that go beyond Phase 0's baseline. **Short-lived and rolling**: merge to `main` early
and often (target: weekly, or whenever a discrete piece is done), because everything
here makes the other five branches faster and delaying it wastes that.

**Resources for this branch:**

- Claude Code subagents docs: https://docs.claude.com/en/docs/claude-code/sub-agents
- Claude Code skills/slash-commands docs: https://docs.claude.com/en/docs/claude-code/slash-commands
- Addy Osmani, "Agent Harness Engineering" (referenced throughout this plan for the
  structural-enforcement principle): search for the original post if a direct link
  isn't already saved in `docs/decisions/`
- OpenAI "Harness Engineering" writeup (same — the "success is silent, failure is
  verbose" and "small continuous payments" principles both come from this source)

**B5.1 — Subagent definitions in `.claude/agents/`** (committed, not local-only, so
every branch and worktree gets them). Per the literature, isolated context per
subagent with the filesystem as the coordination surface:

- `merge-verifier` — drives a full run via `drive_app.py`, runs
  `compare_to_golden.py`, asserts `BUDGETS.yaml`, reports pass/fail with numbers.
- `perf-profiler` — runs the B1.1 profiling harness and produces the component
  histogram.
- `schema-explorer` — read-only origin/destination catalog queries; the same
  capability B2's swarm agent needs, usable interactively.

**B5.2 — Skills for the repeated workflows.** Each of these was done manually many
times during v1: reset destination to pristine; drive a subject end-to-end;
regenerate a golden; compare to golden; restore after a failed run. `scripts/` has
the code (`reset_destination.py`, `restore_pristine.py`, `drive_app.py`,
`compare_to_golden.py`); wrap them as skills so they're discoverable.

**B5.3 — Planner / generator / evaluator split.** The literature is explicit that
self-evaluation is biased — agents overestimate their own work. `compare_to_golden.py`
is already an independent evaluator; formalize the split so the agent that
*implements* a change is never the one that *grades* it, and both write to the
filesystem rather than to each other's context.

**B5.4 — Ralph-loop driver for long runs.** A full Boeing verification is 30–90
minutes across multiple HITL gates and repeatedly exceeded `drive_app.py`'s fixed
iteration budget during v1, forcing manual restarts. Build a driver that re-injects
the original task into a fresh context while the filesystem carries the state.

**B5.5 — Continuous debt mitigation (OpenAI principle #4).** Rather than periodic
refactoring sprints, a scheduled background task that scans for architecture-lint
deviations, stale `file:line` citations in `docs/`, and `mypy-baseline.txt` entries
that could now be removed — and opens small PRs. *"Small continuous payments rather
than periodic reckoning."*

**B5.6 — Build the `NODE_SPECS` / `REPORT_CONTRIBUTORS` registries.** Part 1.6.D's
re-audit found these were part of the original §0.8 design but never actually built
— `graph.py` still wires all 17 nodes by hand and `build_reconciliation_report` is
still one function body. If B2 or B3 hasn't already built the minimal version each
needs, this is the right branch to build the general form both can share, since B5
owns no `src/` files that would conflict — coordinate directly with whichever
branch gets there first rather than duplicating the registry.

**B5.7 — The ratchet, maintained.** Every time any branch hits a real failure, a
corresponding hook, lint rule, or `CLAUDE.md` line is added here — and **every
existing line stays traceable to the failure that caused it.**

**Acceptance gates:**

- `CLAUDE.md` has every line traceable to a documented failure or hard constraint.
- `scripts/lint_docs.py` reports zero broken links / stale citations across `docs/`.
- The `merge-verifier` subagent produces a verdict on a real run without a human
  reading logs.
- Every new hook is proven to actually block its target (write a deliberately bad
  commit, watch it fail).

---

# Cross-branch rules (copy verbatim into every `Branch_Plan.md`)

These are what keep six parallel branches from producing six codebases.

**R1 — One verification command.** `python scripts/check.py all` must pass before
every commit that leaves your machine. Server CI runs the identical command. If it
passes locally and fails in CI, that's a bug in `check.py` — fix it, don't work
around it.

**R2 — Ownership is exclusive.** You may read any file; you may modify only files in
your branch's Owns column. Needing a change outside it is a request to `main`, not
an edit. Ownership violations are the #1 source of six-way conflicts. **This is also
the CLAUDE.md "Branch discipline" rule — `git checkout` your branch and stay there.**

**R3 — Extend through registries, never through shared function bodies.** New state
key → your own `state/<feature>.py` fragment plus one line in the `MergeState` base
list. New node → one row in `NODE_SPECS` (build it if it doesn't exist yet for your
owned files — see B2/B3/B5's notes above). New report section → one
`REPORT_CONTRIBUTORS` registration (same caveat). If you find yourself editing
`build_reconciliation_report`'s body with no registry available, build the minimal
registry first rather than reaching into the shared body.

**R4 — Never edit `pyproject.toml` or `requirements.txt`.** Every dependency you need
was pre-declared as an extras group in Phase 0.1 (`dspy`, `splink`, `dq`, `lineage`,
`swarm`, `perf`). If you need one that wasn't, that's a request to `main`.

**R5 — Rebase on `main` immediately after every merge-back**, before writing another
line. Serialized integration only works if everyone re-baselines at once.

**R6 — New features ship default-off.** Splink, the swarm planner, and any
performance strategy change land behind a config flag whose default reproduces
current behaviour. A branch that cannot be turned off cannot be merged safely.

**R7 — Output contracts are frozen.** Prompt output keys, `resolve_entities_impl`'s
result dict, the six HITL interrupt payloads, and `MergeState`'s existing keys do not
change shape without a `main`-level decision recorded in `docs/decisions/`. The
frontend and `WorkflowManager` both depend on these and neither is your file.

**R8 — House style is mechanical.** One file per node; typed `MergeState`;
`logger.debug("ENTER …")` / `"EXIT …"` in every node; tests co-located in
`tests/unit/test_*.py`; golden evals in `tests/eval/`. `scripts/lint_arch.py`
enforces the parts that can be enforced — if it doesn't complain, you're consistent.

**R9 — Live verification per task, not per phase.** A checkbox goes green only after
the thing ran against the real app and DBs and an *independent* check confirmed it —
direct SQL, log inspection, a regenerated report. Never the app's own success
message. If it can't be verified live, leave it unchecked and say why.

**R10 — Report numbers, not adjectives.** "Faster", "better", "more accurate" are not
results. Every claim carries a before/after number measured against
`docs/verification/BASELINE_V1.md`.

**R11 — Every failure becomes a rule.** When you hit a real bug, open a `B5` item for
the hook/lint/doc line that would have prevented it. This is the ratchet; it is the
single highest-leverage habit in the whole plan.

---

# Integration order (merge-back sequence)

Parallel development, serialized integration. Merge in this order, rebasing all
remaining branches after each:

1. **B5 harness-ratchet** (rolling, continuously) — pure tooling, zero `src/`
   conflict surface.
2. **B3 quality-lineage** — new files plus registry entries; near-zero conflict.
3. **B1 dspy** — self-contained in `src/llm/`.
4. **B4 matching-precision** — consumes §0.0's canonical projection and its frozen
   clustering functions. Largest expected quality win of the five.
5. **B2 planner-swarm** — largest behavioural blast radius; lands last, on the most
   stable base, with the most complete verification tooling available.

---

# Verification (whole programme)

| Level | Gate |
|---|---|
| Every commit | `python scripts/check.py all` — ruff, ruff-format, mypy-ratchet, arch lint, doc lint, unit tests, frontend typecheck+build |
| Every branch merge | full `pytest` (incl. integration) green; `python -m tests.eval.score` no precision regression; `python -m tests.prompts.harness` contracts pass |
| Every performance/quality claim | `drive_app.py --assert-budgets` on a real Boeing run + `compare_to_golden.py` parity number, both diffed against `BASELINE_V1.md` |
| Programme complete | Boeing, **Airbus and Lockheed** all run end-to-end on the merged `main` with parity, family coverage, and runtime all measurably better than v1 — reported as a table, honestly, including anything still short of the hand-merge |

---

# Honest scope note

This is six substantial workstreams plus a foundation phase, and it is a lot. Phase 0
alone is real work that produces **no new user-visible capability** — it is pure
substrate. That is deliberate and it is the highest-value part of the plan: without
it, six parallel branches on a repo with no CI, no lint enforcement, a broken
`pip install -e .`, and a 117-line shared `TypedDict` will spend more time resolving
conflicts than building features.

One thing changed during planning and is worth stating plainly: **§0.0 may turn out
to be the single largest quality win in the whole programme.** A matcher that
compares zero pairs and a loader that scatters names into invented columns account
for an unknown but probably large share of the 12% parity gap — quite possibly more
than the entity-breadth cap that B3 targets. Re-measure parity after §0.0 before
assuming which branch matters most; the priority order below may deserve revisiting
in light of that number.

Of the five branches, **B2 (planner-swarm)** has both a large expected payoff (it
targets the entity-breadth cap) and the largest risk, which is why it is default-off
and merges last. **B4 (matching-precision)** absorbed the old
splink-only branch, because C0 showed the bottleneck is not the *algorithm* but what
the algorithm is allowed to see — and Splink would have inherited exactly the same
blindness. Within B5, Splink specifically may still not beat the hand-rolled matcher;
if it doesn't, it ships default-off as a documented option, which is a legitimate
outcome. B4.1 and B4.2 are the parts most likely to move the parity number. **B1 (perf)** is the most certain win — the measured numbers are
already in hand and the first fix (hoisting two dict comprehensions out of a nested
loop) is nearly free.
