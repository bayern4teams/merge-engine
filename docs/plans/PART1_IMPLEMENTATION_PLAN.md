# Part 1 implementation plan — cloud, multi-agent, lead-approved

**Source of truth for *what* to build:** [TECHNICAL_REPORT_2026-09-26.md](../../TECHNICAL_REPORT_2026-09-26.md),
PART 1 (P1–P20) and its "Part 1 ordering and dependencies". This plan says *how* to
build it. Where the two disagree on substance, the report wins; where the code
contradicts either, the code wins and the discrepancy goes in the handoff file.

**Scope:** Part 1 only. No new storage, no new pipeline stage. Part 2 is research,
Part 4 is appendices, **Part 3 (architecture) is out of scope** — it is planned only
after the user has merged Part 1 and verified it locally (§10).

**Branch:** `feat/part1-fixes`, created from `main`. Every agent works there and
nowhere else (§2). The five older `feat/*`/`chore/*` branches (B1–B5) are *not*
being implemented; their file-ownership map does **not** apply here, and the user
has said files they own may be modified.

**Live state lives in two files, not in any session:**
[part1/HANDOFF.md](part1/HANDOFF.md) (status table, next action, escalations) and
[part1/LOCAL_VERIFY.md](part1/LOCAL_VERIFY.md) (what the cloud could not prove).

---

## 1. Before the first launch (the user does this once)

1. Push `feat/part1-fixes` to GitHub; the cloud clones from there.
2. Cloud environment: Python **3.11**, Node 20, network access allowed for package
   installs. Setup script = what `.github/workflows/ci.yml` does:
   `pip install -e ".[dev,perf,splink,dspy,schema_matching,dq,lineage,swarm]"`,
   `npm ci` in `src/ui/frontend`, then `python scripts/bootstrap.py` (hooks + graphs).
   CI already runs `check.py all` green with no database service, so the cloud can too.
3. Enable the Ponytail plugin for the project. The agent also verifies this in Phase 0.
4. Launch one session: **Opus 5.5, high effort**, repo `merge-engine`, branch
   `feat/part1-fixes`, prompt:

   > Read `docs/plans/PART1_IMPLEMENTATION_PLAN.md` and `docs/plans/part1/HANDOFF.md`.
   > Execute the phase named under "Next action", following the plan exactly.

   You do **not** create worker sessions. The lead creates every subagent itself.
5. Phase 0 ends with a report and a notification, then **stops**. The user brings the
   report back so the orchestration choices in §7 can be fixed before Phases 1–5.

## 2. Operating rules — every agent, every phase

These are CLAUDE.md's rules restated as the contract. The lead copies the relevant
ones into every worker brief; a worker that was not told a rule is the lead's failure.

2.1 **One branch.** `feat/part1-fixes` only. Never `git checkout` another branch,
merge, rebase, delete or force-push. Worktree isolation is not used: lanes own
disjoint files (§3.3), so one working tree is safe.

2.2 **Tool order, stop at the first tier that answers.** graphify
(`query` / `path` / `explain` / `affected` / `god-nodes`) → graft (`ask`,
`skeleton`) → codebase-memory-mcp (`search_graph`, `trace_path`, `get_code_snippet`,
`check_index_coverage` on every file relied on) → raw source. Missing graphs:
`python scripts/refresh_graphs.py` (~8 s), never a silent drop to grep. **Never** the
LLM paths: `graphify extract --mode deep`, `graphify label`, `graphify cluster-only`
without `--no-label`, `graft build --deep`. A graph that contradicts the code loses;
rebuild it. `affected "X"` returning nothing means the *graph* sees no caller —
confirm with grep before deleting anything.

2.3 **Ponytail, all six commands, used for real.**
- `/ponytail` full — the ladder: does it need to exist → already in this codebase →
  stdlib → native → installed dependency → one line → minimum code.
- Every non-trivial branch leaves **one** runnable check; deliberate corners carry a
  `ponytail:` comment naming the ceiling and the upgrade path (the report's own
  "Ceiling" / "Deferred alternative" text is the source for those comments).
- `/ponytail-review` on every phase's diff **before** the phase closes.
- `/ponytail-audit` once, in Phase 5, paired with graph tiers (graph finds the
  unreferenced, the audit decides whether it should exist).
- `/ponytail-debt` in Phase 5: the ledger of every deferral and its trigger.
- `/ponytail-gain` in Phase 5: measured effect, for the final report.
- `/ponytail-help` when unsure.

2.4 **No fitted constants — the report's own criterion.** A general mechanism
computes its constants from the data at runtime; a fitted one contains literals drawn
from a corpus. Every item's acceptance includes a grep for corpus literals
(`France`, `US`, `SAS`, `Berhad`, `Global`, `Group`, `Holdings`, `companyname`,
`main_entity_id`, `company_relation`, `staff`, table names of the two origins) and,
for each remaining numeric constant, the question "would a count over the corpus
produce this?". Sanctioned standing constants (e.g. the 0.95 / 0.70 band, shown to the
operator at the gate per P15) are named in the item's spec.

2.5 **Mechanically enforced rules stay enforced** (`check.py guards`/`arch`):
prompt substitution is `str.replace()` never `.format()`; no module-level mutable state
in `src/mcp_server/tools/` (return state through the result dict); layering
`api → orchestrator → tools → connectors → utils`; no bare `except Exception: pass`
(`contextlib.suppress` if intentional); **no new destination columns, ever** —
unmappable fields go to `unmapped_fields`; `sample_databases/` never committed; never
hand-edit `arch-lint-baseline.txt` or the mypy baseline to hide a new violation.

2.6 **HITL resume trap.** `interrupt_before` means a gate node's own body never runs
on resume — only the router does. Anyone touching gate logic (P9, P10, P15, P4's
confirmation) reads `docs/architecture/orchestration_and_ui.md` first.

2.7 **Completed is not evidence.** A run finishing `completed` proves nothing; the
only proof is an independent score. The scorer/acceptance check is written *before*
the implementation, by the verifier (§4), never fitted afterwards.

2.8 **Python:** the repo `.venv` may be 3.14 and gives false results; use 3.11.
`/tmp` can be small (436 MB locally) — large artifacts go in the scratchpad or under
the repo's `.tmp/`.

## 3. Roles, routing, concurrency

### 3.1 Roles

| role | model / effort | does |
|---|---|---|
| **Lead** (the launched session) | Opus 5.5, high | designs, does the complex and cross-cutting items itself, writes worker briefs, reviews and approves/rejects *every* result, owns commits, `HANDOFF.md`, phase gates, notifications |
| **part1-worker** (`.claude/agents/part1-worker.md`) | Sonnet 5.5, high | implements one item inside named files; reports to `.handoff/<id>.report.md` |
| **part1-verifier** (`.claude/agents/part1-verifier.md`) | Sonnet 5.5, high | *derive*: writes acceptance checks from the report before implementation; *attack*: tries to break the result afterwards; read-only on `src/` |

Phase 0 (S1) showed the frontmatter `model` key is honoured but a worker's `effort` could not
be confirmed as high (one worker reported low). So: the lead **always passes `model: "sonnet"`
at spawn**, treats worker effort as unverified, and keeps judgement-heavy work (§3.2 lead column) itself.

### 3.2 Routing table — who gets what

The lead may deviate **only** with a one-line reason in `HANDOFF.md`.

| goes to **lead (Opus)** | goes to **Sonnet worker** |
|---|---|
| anything inside `resolve_entities.py` (2.7k lines, shared by P2/P5/P6/P7/P8/P15/P19/P20) | work in one small file with a fully specified fix |
| P2, P5, P6, P7, P8, P14, P15, P20, P10 | P1, P11, P12, P13, P16, P18, P9, P17, P19 |
| gate/HITL logic, graph wiring, algorithmic work with subtle correctness | tests, fixtures, docs, report plumbing, applying a known pattern |
| P3 and P4 if a worker fails review twice | P3, P4 first attempt (lead reviews the diff line-by-line) |
| every approval decision | never an approval decision |

**Escalation rule:** a worker whose result is rejected twice is taken over by the lead.

### 3.3 Concurrency and conflict avoidance

- **At most 2 concurrent subagents.** The user is on the Pro plan (Phase 0, S3), where the
  5-hour window is small; parallel agents burn it fast. The cap is raised only by the user.
- **A lane = a set of files.** Two lanes never share a file. Phase 0.5 locks the real
  ownership table with `graphify affected` per item (the report names files only for
  P1, P2, P3, P7, P8, P13, P14, P15, P18; the rest are inferred and must be confirmed)
  and writes it into `HANDOFF.md`. A lane that needs a file it does not own asks the
  lead; the lead serialises it behind the owner.
- Everything touching `resolve_entities.py` is **one serial chain** run by the lead.
  Independent work runs beside it, never inside it.

## 4. The lead approval protocol (nothing advances without it)

For every item, in this order:

1. **Derive.** Lead briefs a `part1-verifier` (*derive*): write acceptance checks from
   the report's Evidence + Fix text only — including a negative case that fails on the
   *old* behaviour and the §2.4 grep. Output: tests under `tests/` +
   `.handoff/<id>.acceptance.md`. The implementer has not influenced these.
2. **Implement.** Lead (or a worker, per §3.2) implements against the brief. The brief
   states: report sections, owned files, acceptance file, rules from §2, what "done"
   means (§5), and "report what you could NOT verify".
3. **Attack.** A `part1-verifier` (*attack*) runs the checks and tries to break the
   result. Verdict file: ACCEPT / REJECT with evidence, plus the unverified list.
4. **Lead decides.** The lead reads the worker report, the verdict *and the diff*.
   - **Reject** if it passes only the written tests, passes only the report's one
     example, contains a corpus literal, widens scope, or leaves any old failure
     mode reproducible. Send *specific* corrections to the same worker with
     `SendMessage` (it keeps its context). Max two rounds, then the lead takes over.
   - **Accept** only when it would survive the lead's own attempt to break it.
5. **Record.** Lead commits (`P<n>: <what> — <evidence>`), updates `HANDOFF.md`
   (`accepted-cloud` or `unverified-live`), and adds a `LOCAL_VERIFY.md` entry for
   anything the cloud could not prove. If a result is wrong or evidence-thin, it goes
   under "Open escalations" in `HANDOFF.md` instead of being waved through.

Workers write full reports to files and return ≤10 lines, so the lead's context holds
verdicts, not essays.

## 5. Definition of done — per item

- [ ] Acceptance checks (derived by the verifier) pass, **and** the negative case fails
  on the pre-change code (shown by running it against `git stash` or the parent commit).
- [ ] `python scripts/check.py` stages that the touched files affect are green; at
  phase end, `python scripts/check.py all` is green (it is the one verification command).
- [ ] §2.4 literal grep clean; §2.5 guards clean; no `arch-lint` / mypy baseline growth.
- [ ] The report's "Ceiling" for the item is recorded as a `ponytail:` comment or an
  item note; the "Deferred alternative" and its trigger are listed for `/ponytail-debt`.
- [ ] Graphs refreshed (`python scripts/refresh_graphs.py`) after the commit.
- [ ] `HANDOFF.md` row updated; `LOCAL_VERIFY.md` entry added where applicable.
- [ ] Lead's explicit verdict recorded. No self-certification by a worker.

## 6. What the cloud can and cannot verify

**Can:** lint, format, arch, docs, portability, guards, mypy ratchet, frontend,
unit + integration tests (what CI runs), and *synthetic* fixtures with planted ground
truth built by the verifier from the report's failure shapes (§8 Phase 0.6).

**Cannot:** the real origin databases, `global_warehouse`, the Qwen/llama.cpp endpoint,
Langfuse, or the report's own corpus (the report records that `ground_truth` was dropped
and `global_warehouse` wiped — the user must rebuild them locally from
[GROUND_TRUTH_FULL_CORPUS_GUIDE.md](GROUND_TRUTH_FULL_CORPUS_GUIDE.md) /
[GROUND_TRUTH_MANUAL_BUILD_GUIDE.md](GROUND_TRUTH_MANUAL_BUILD_GUIDE.md) before the
local verification pass).

**Rules for the split:**
- An item is fully implemented even when its live check is deferred; it is marked
  `unverified-live`, never `accepted-cloud`, and never silently "done".
- Synthetic tests are *evidence of mechanism*, not proof the live numbers moved. The
  report's live figures (e.g. 28/29 zoominfo fields unmapped, 711,670 pairs discarded,
  `dependency_tree = {}`, 8/16 entities unmapped) appear in `LOCAL_VERIFY.md` as the
  "before" values the user must see change.
- LLM-dependent code (P8) is exercised with a stub LLM client that records calls; the
  live endpoint is `unverified-live`.
- Report counts, not rates, with cross-source vs within-source stratified (report §0.4).
  Review rate is reported beside every precision figure.

## 7. Sessions, limits, supervisor, notifications

**Target: unattended cloud runs, resumed by an hourly supervisor routine** (cloud re-check
#2 must first prove it; fallback is a local session, same protocol).

- **State is in git.** After every accepted item (and every `WIP P<n>` step): commit, update
  `HANDOFF.md`, push. A new session needs only the plan + `HANDOFF.md`. Sessions are short-lived
  *slices*; nothing depends on a session's memory, so no compaction is needed.
- **Usage limit.** The cloud shows no percentage (S3), so there is no 90 % rule there: work
  until the limit stops the session. Nothing is lost because everything is committed per
  step. If a usage percentage *is* readable (local `get_usage`), stop at ≥ 90 %: finish the
  commit, write `HANDOFF.md`, push.
- **Supervisor.** A scheduled cloud routine (cron `0 * * * *`, branch `feat/part1-fixes`)
  (created in the claude.ai Routines UI with the repo and GitHub connector — a routine made from
  inside a session has no repo) starts a fresh session whose prompt is
  [part1/SUPERVISOR.md](part1/SUPERVISOR.md). It exits
  at once when: the rate limit is a hard stop (`allowed_warning` is not one); `HANDOFF.md` says `Autonomy: off`; the next
  action is "done" or "waiting for the user"; or `Lock` is younger than 75 minutes (another
  session is alive). Otherwise it takes the lock and acts as **lead** for as long as it can.
  An idle tick costs almost nothing, so hourly resumes soon after each window reset.
- **Phases chain automatically** once the user sets `Autonomy: unattended-through-phase-5`:
  the phase gate (§8) must pass, including a green CI run on the pushed head, before
  `HANDOFF.md` advances to the next phase. After Phase 5 the supervisor stops and never merges.
- **Never lose an item.** Commit `WIP P<n>` before any long operation. A session that finds
  uncommitted work or a stale lock inspects `git status`, finishes or discards it deliberately,
  and records which in `HANDOFF.md`.
- **Notifications.** Cloud push does not reach the user (confirmed). The record is
  `HANDOFF.md` + commits: every phase end, blocker and the final completion are written to its
  "Notifications" section, and a `[PHASE n DONE]` / `[BLOCKED]` / `[PART 1 COMPLETE]` commit
  message prefix makes them visible in GitHub. Try the push tool anyway; do not rely on it.
- **Cost discipline:** Sonnet for the mechanical work in §3.2; batch independent
  lookups; keep worker reports in files; do not re-read what the graphs already answered.

## 8. Phases

Ordering respects the report's dependency graph, not its linear "recommended
sequence" (the two differ only where the DAG allows work earlier: P1/P13/P14/P18 have
no dependency on P2/P5). Hard constraints from the report: **P2 before P5**; P1 → P3;
P3 → P4, P7; P13 → P14 → P15, P6; P15 → P6; P15 → P8; P5 → P20 → P16 → P10;
P17, P18 → P10; P19 measures P5.

### Phase 0 — Smoke test and baseline (lead only; no Part 1 code)

Output: `docs/plans/part1/PHASE0_REPORT.md` (written in 0.8), a commit, a notification, then **stop**.

- **0.1** Read this plan and `HANDOFF.md`; confirm branch `feat/part1-fixes`.
- **0.2** `python scripts/bootstrap.py`; confirm `graphify-out/graph.json` and `graft/`
  exist; run `check_index_coverage` on the key files; record tool versions and anything
  that failed to install (and the network allowlist it needs).
- **0.3** `python scripts/check.py all` — record per-stage result and wall time. It must
  match CI (green). Anything not green is a Phase 0 blocker reported, not worked around.
- **0.4** Smoke tests (each: pass/fail + evidence in the report):
  - **S1** spawn `part1-worker`; confirm its model and effort are Sonnet 5.5 / high.
  - **S2** spawn 3 trivial workers in parallel on 3 disjoint scratch files; send one a
    correction with `SendMessage` and confirm it keeps context.
  - **S3** is session usage readable from inside the cloud session? Record how.
  - **S4** schedule a cheap task for a few minutes later that appends to a scratch file
    and reads `HANDOFF.md` from git; confirm it fires. This decides "chain phases
    automatically" vs "user launches each phase".
  - **S5** push notification arrives.
  - **S6** Ponytail's six commands and the three graph tools are usable.
  - **S7** `/compact` availability and cost, for the §7 decision.
- **0.5** For each of P1–P20 run `graphify affected` on the functions the report
  names; write the **locked file-ownership table** and the lane plan into
  `HANDOFF.md`; flag any item pair that unexpectedly shares a file.
- **0.6** Scorer-first baseline. Locate the report's surviving artifacts
  (`state.json`, `gates.jsonl`, `score.json`, `gt_score.py`, `gt_rows.py`,
  `run_merge.py`) inside the repo; if absent in the cloud, say so. A `part1-verifier`
  builds **synthetic fixtures** under `tests/part1/` reproducing the failure *shapes*
  with planted ground truth (concatenated-lowercase columns; a block larger than the old
  cap; a child column whose name does not match the `<parent>id` pattern; a
  single-`id` destination table; a three-way company set: same-email / same-address
  different tax-ID / same-address-phone; a giant-component bridge) and records
  **before** numbers on the current code. Prove the scorer both ways (identity passes;
  injected FP and FN are caught and named), as the report did.
- **0.7** Add the DB-restore note and the report's "before" values to `LOCAL_VERIFY.md`.
- **0.8** Write `PHASE0_REPORT.md`, update `HANDOFF.md` (Next action = Phase 1), commit, push, notify.

### Phase 1 — Independent foundations (3 lanes + lead)

| lane | owner | items | files (confirm in 0.5) |
|---|---|---|---|
| Lead | Opus | **P2** then **P14** | `resolve_entities.py` (P2); `fellegi_sunter.py` (P14) |
| B | worker | **P1 → P12 → P11** serial | `map_fields.py` and the profiling/mapping path P12 reports |
| C | worker | **P18** (five sub-fixes) | `unify_entity_group.py`, `survivorship.py` |
| D | worker | **P13** (then lead takes P14) | `fellegi_sunter.py` |

At most 2 lanes run at once (cap 2): start B and C, then D when one finishes; the lead does P2 meanwhile, then the P13 call site and P14. File ownership and the P13 split are fixed in `part1/HANDOFF.md` (Phase 0.5) and override the table above where they differ.

### Phase 2 — Structure and blocking

| lane | owner | items |
|---|---|---|
| Lead | Opus | **P5** (needs P2 accepted) |
| E | worker | **P3 → P4** (`join_graph.py` and the dimension-materialisation path); lead reviews line-by-line |

### Phase 3 — Matching chain (serial in `resolve_entities.py`) + side lanes

Lead, in order: **P15 → P6 → P7 → P8 → P20**. Side lanes beside it, disjoint files:
**P9** (decision-memory file + gate hook), **P17** (destination defect report),
**P19** (zero-label tuning/report; read-only on `resolve_entities.py` — if it needs an
edit the lead serialises it).

### Phase 4 — Quality signals and the gate

**P16** (worker; needs P20) → **P10** (lead; needs P16, P17, P18; touches graph/gate
wiring — read §2.6 first).

### Phase 5 — Integration and hand-over

`python scripts/check.py all` green; `/ponytail-review` over the whole branch diff,
`/ponytail-audit`, `/ponytail-debt`, `/ponytail-gain`; verify every `HANDOFF.md` row has
a lead verdict; finalise `LOCAL_VERIFY.md`; update `docs/` for changed behaviour
(`python scripts/check.py docs` green); purge `.handoff/` after folding its verdicts
into a short `part1/PHASE5_REPORT.md`; final notification. **Stop. Do not merge.**
The user verifies locally and merges.

### Phase gate (end of every phase)

All items accepted or explicitly `unverified-live`/escalated → `check.py all` green →
`/ponytail-review` of the phase diff addressed → graphs refreshed → `HANDOFF.md`
updated → push → notification → apply §7 session policy.

## 9. Per-item specification

Each item: **report section → files → what to build → acceptance beyond "tests pass"
→ live check deferred to the user.** Constants and the "Ceiling / Deferred" text come
from the report; read the full section before starting.

**P1 — tokeniser** (`map_fields.py`). Phase 0 baseline: 7 of 11 concatenated-lowercase fields already map via the underscore-collapse rule; the tokeniser only matters for the other 4 (`foundingdate`, `taxregistration`, `contactemail`, `postalcode`). Dictionary-driven split of no-delimiter names
against the vocabulary *observed in the destination schema* (runtime, finite);
ambiguous splits are reported, never guessed. *Accept:* those 4 fields now map and the 3 planted negatives still do not (a no-op fix must fail); `companyname` → `company`,`name`;
`businessaddress` → `business`,`address`; camelCase/delimited behaviour unchanged
(regression snapshot); an ambiguous name yields a report entry; no hard-coded prefix
list (grep); the failure reason string for an unsplittable name remains. *Live:*
zoominfo `company` mapped fields rise from 1 of 29 (28 unmapped before).

**P2 — comparison budget** (`resolve_entities.py`). Replace the 250k cap with block
purging (parameter-free) → block filtering → meta-blocking (WNP local mean). Keep a
budget only as an out-of-memory guard that **fails the run loudly** when it binds.
*Accept:* also fix the reported `uncompared_pairs`, which over-counts 5.6× (389,200 reported vs 69,600 truly discarded, because one block arrives under two keys and the overflow estimate is not deduped) — it must equal the true count (or be gone because nothing is discarded). A fixture with one block far beyond the old cap (the Phase 0 800-record block; 9 of 40 planted pairs lost today) loses no pair to a counter —
every excluded pair is excluded by a *computed property*; the guard raises instead of
truncating; pair completeness is measured and printed; the old truncation reproduced on
the parent commit. *Live:* `staff` 962k candidates / 711,670 previously discarded; a run
must never end `completed` with silent discards. **Ships before P5.**

**P13 — `u` estimation** (`fellegi_sunter.py`). `u` from a random sample of the
*unblocked* cross product (a few thousand pairs, seeded); `m` from EM on blocked
candidates unchanged; token-level exact agreement uses `df/N` directly. The two `u`
paths coexist on purpose — a comment forbids "unifying" them. *Accept:* on a fixture
where blocking makes look-alikes, sampled `u` < blocked-EM `u` and `log2(m/u)` is larger
for the discriminating field; sampling is deterministic under a seed; a field value
with no sampled agreement floors rather than dividing by zero.

**P14 — token-grain IDF** (`fellegi_sunter.py`). Add `build_token_document_frequencies`
beside the value-level function; keep the value-level path for atomic fields; text
fields pick their level (EXACT/STRONG/WEAK/DISAGREE kept — D-14) from an
IDF-weighted similarity; **`log2` everywhere**; start with TF-IDF, not SoftTFIDF.
Library traps: `py_stringmatching` `SoftTfIdf` defaults differ from the paper and it
raises on NULL — guard both. DF table is a per-run artifact in Part 1 (storage is
Part 3). *Accept:* on a fixture where "Group" is high-df, *Acme Group Ltd* vs *Beta
Group Ltd* now scores **below** *Acme Group Ltd* vs *Acme Holdings Ltd* (the inversion
the report names); atomic-field scores unchanged; NULL input does not crash; no suffix
list literal.

**P18 — survivorship** (`unify_entity_group.py`, `survivorship.py`). ① document in
place that **higher priority number wins — do not reverse it**; ② key priority by
`(origin, field)` falling back to the origin value, and resolve the dead
`measured_reliability` path; ③ rename `completeness`→`longest` for text fields (keep a
config alias) *or* implement the record-level fewest-NULLs meaning — pick one, record why;
④ `first_seen` gets a total order (…, `origin ASC, source id ASC`); ⑤ `numeric_max` still
resolves but records the disagreement **and its magnitude** and surfaces it in the run
report. *Accept:* each of the five has its own failing-before test; the existing config
files still load and mean the same thing.

**P1/P11/P12 batch** — **P12:** catalog/system tables excluded from analysis and mapping
and projection; ETL metadata columns excluded from matching and projection but kept in
the run output; three override layers (detected default → per-origin → per-run) with
that precedence. *Accept:* override precedence tested; a business field that looks like
metadata (the report's `signup_date` ceiling) is protected by the override and the
misclassification risk is recorded, not hidden. **P11:** bounded-pool per-entity
mapping modelled on the existing `_LLM_CONCURRENCY` pattern. *Accept:* output identical
to serial and in deterministic order; exceptions propagate; concurrency never exceeds the
bound; faster than serial against a stub with latency.

**P5 — schema-agnostic blocking** (`resolve_entities.py`; **after P2**). Token blocking
(every token a key, attribute names never read) → purge → filter → meta-blocking WNP /
CEP (`K = Σ|b|/2`) / CNP (`k = Σ|b|/(|E1|+|E2|)`); all constants computed from the block
collection. *Accept:* **property test** — renaming/reordering columns leaves the
candidate set unchanged; planted duplicates are retained and PC/RR are reported (the
report's 0.96–0.99 / 0.76–0.90 are reference expectations, **never tuned to**); on the
fixture, comparisons performed with P2+P5 do not exceed the pre-P5 uncapped baseline in
a way that would show P5 landed without P2. The memory peak during block building is
measured and noted (the report's stated practical ceiling).

**P3 — IND discovery** (`join_graph.py`). Value-based inclusion-dependency discovery,
RHS-uniqueness prefilter, `PK ⊆ PK` filter, **the cliff** (cut the ranked list at its
largest score gap — no parameter), name-agnostic. **Do not** adopt the acyclicity rule
(it would reject junction tables and self-referencing hierarchies — exactly what P4/P7
need) and do not use the fitted name features. *Accept:* a fixture shaped like
`child.main_entity_id → parent.business_key` (name does not match `<parent>id`) yields an
that edge, and the whole `dependency_tree` **equals the planted truth** (non-empty is not enough: today it is non-empty but wrong, and the coincidental-overlap decoy `ticket.orgid → org._id` is wrongly accepted — it must be rejected); the surrogate-`_id` false edge is still
rejected; renaming columns leaves the INDs unchanged; a junction table and a
self-referencing column both survive; the cliff has no tunable constant (grep).
*Live:* 26 tables → `dependency_tree` non-empty; `staff.main_entity_id =
company.companyid` joins all 13,518 rows.

**P4 — junction rule + dimension materialisation.** Junction = a table whose **minimal
unique column combination equals the union of ≥2 FK column sets**; payload columns
allowed. Weak-entity (FK + local discriminator) is **proposed to the gate, never silently
acted on**. Repeated text on a row becomes a dimension row plus a link, with **zero new
destination columns** (`allow_schema_changes=False` stays the gate). *Accept:* a
payload-carrying junction is detected; an all-FK table whose minimal key is *not* the FK
union is not; the weak-entity case is routed to the gate; dimension rows are produced
from repeated text with a link; a test asserts no DDL/column was added. *Live:* entities
with zero mappings drops from 8 of 16; `industry` and `product_category` non-empty.

**P15 — consume the review band** (`resolve_entities.py`). `<0.70` Exclude;
`0.70 ≤ s < 0.95` **Possible Match → existing HITL gate**; `≥0.95` Match. Show the
`log m/u` score distribution and predicted match counts at the gate and let the operator
commit — the two constants stay the defaults, no longer unexplained. *Accept:* a pair at
~0.8 produces a gate item and is resolved within the run; Match/Exclude behaviour
unchanged; resume behaviour respects §2.6.

**P6 — three-way discrimination** (`resolve_entities.py`; after P14, P15). Token IDF in
the comparator (P14); **conflicting exclusive identifiers suppress the matching rule,
never the pair**, with exclusivity decided from value-sharing frequency in the data, not
a list; three-way outcome Match / Possible Match (HITL) / Possibly Related (typed edge,
no human). Honest limit: no hierarchy *direction* without a declared edge. *Accept:* the
report's own three examples as fixtures, checked as outcome *classes* (not exact scores) on **both** the heuristic and the fitted-model paths, since today they diverge (fitted: same-email lands in review, different-tax-ID is silently excluded) — same email → Match; same address + different
tax IDs → Possible Match (not rejected, not auto-merged); same address + phone,
different company → Possibly Related edge; a value shared by many entities is labelled
generic and prior records re-evaluated; **no** `France`/`US`/`SAS`/`Berhad`/`Global`/
`Group`/`Holdings` literal anywhere (grep).

**P7 — typed relationship edges** (after P3, P4). IND → cliff → junction test → self-INDs
as parent pointers → emit typed edges with the report's vocabulary (`DIRECT_PARENT`,
`ULTIMATE_PARENT`, `BRANCH_OF`). No table is named in the procedure. A cyclic hierarchy is
reported, not silently broken. *Accept:* a fixture with a junction and a self-reference
yields the right typed edges; a cycle is reported; grep finds no origin table name in the logic.

**P8 — LLM targeting** (`resolve_entities.py`; after P15). One pair per call returning a
calibrated probability field; target the uncertain band, not nearest-to-auto-match;
concurrency kept. *Accept (stub LLM):* the pairs sent are exactly those inside the
band; no call carries more than one pair; one malformed response damages one pair;
the number sent falls versus the old selection on the same fixture. Calibration remains
an open item (report P13/§2.17) — noted, not invented. *Live:* endpoint behaviour.

**P20 — clustering** (`resolve_entities.py`). Prune weak edges with meta-blocking's own
threshold (no hand-set confidence cut) → `scipy.sparse.csgraph.connected_components` →
coherence split using P16's name-variation rate → LP 1:1 reduction where strictly
one-to-one is required. *Accept:* the report's weak-bridge shape already passes today, so it is a regression guard only. The real failing shapes (Phase 0): (i) the bridge is the strongest edge (0.99 over internal 0.96) → both clusters currently shatter (FN 20); (ii) a true cluster seen only as a chain → 5 singletons (FN 10). Both must be fixed; an incoherent cluster is split; no
literal confidence threshold (grep).

**P19 — non-circular blocking tuning** (after P5). Tune only on zero-label quantities
computed from the block collection; report PC/PQ **only where ground truth exists**.
*Accept:* the tuning code path takes no match labels (static check on its inputs/imports);
with no ground truth PC/PQ are absent, not guessed.

**P9 — decision memory (file)**. Persist gate decisions to a file keyed by a stable hash
of the question; apply matching decisions without asking and report the count.
*Accept:* run 2 with identical questions asks **zero**; the hash is stable across
processes (different `PYTHONHASHSEED`); writes are atomic and a corrupt file degrades to
"ask again", not a crash. Gate-hook work obeys §2.6.

**P17 — destination defect report** (report, never repair). Name the table, the missing
linkage columns and the unprojectable row count at the pre-load gate; leave the
destination table **empty**; issue no DDL. *Accept:* a one-`id`-column destination table
(the `company_industry` shape) → defect reported, 0 rows written, and a test asserts no
`ALTER`/`CREATE` was issued. *Live:* `company_industry` previously loaded 539 key-only rows.

**P16 — label-free quality report** (after P20). Print homonymy rate, name-variation
rate, cluster-size distribution (Hill numbers), and B-cubed vs the previous run's
clustering (persisted to a file; **run one prints "not available"**, not a number).
*Accept:* the giant-component fixture is visible in the distribution and name-variation
rate; B-cubed of a clustering against itself is 1.0 and a perturbed one is < 1; first run
reports unavailability.

**P10 — pre-load gate** (after P16, P17, P18). Before any destination write, compute
mapping coverage per entity, proportion of NULL identity fields, largest-cluster share and
P16's signals; **fail the run**; thresholds are *proposed by the run and confirmed at the
gate*, not hard-coded. It can only refuse to write — it cannot roll back a partial
write (the report's stated ceiling). *Accept:* a replay of the old failure shape (many
entities with zero mappings, NULL identity fields, a giant cluster) **fails** pre-load
instead of finishing `completed`; a healthy fixture passes; no hard-coded threshold literal.

## 10. After Phase 5 — and Part 3

The branch is **not merged by the agents.** The user runs `LOCAL_VERIFY.md` against
restored databases, then merges to `main` locally. Part 3 (§3.x of the report) is
planned afterwards, informed by Part 1's measured results; nothing here prepares Part 3
beyond what Part 1 itself specifies (e.g. P9's file is Part 3's backfill source).

## 11. Phase 0 outcome and standing corrections

Phase 0 ran in a **local** desktop session ([part1/PHASE0_REPORT.md](part1/PHASE0_REPORT.md)). CI had been red since 2026-09-27 from dependency drift (splink 5, SQLAlchemy 2.1); fixed on this branch by `splink<5`, `sqlalchemy[asyncio]<2.2`, regenerated requirements, and `bootstrap.py` installing CI's extras. A **cloud re-check** (S1, S3, S4, S5, `check.py all`) precedes Phase 1, because S3/S4/S5 did not transfer from the local run. If the cloud cannot read usage or resume itself, Phases 1–5 run in a local session instead, and §7 stays "user launches each phase".

**Cloud re-check outcome** ([part1/CLOUD_RECHECK.md](part1/CLOUD_RECHECK.md)): `check.py all` is green in the cloud (156 s) and matches CI; scheduled wake-ups work; the cloud shows no usage percentage, had no graph tools, no Ponytail, and push notifications do not reach the user. None is a real blocker: the supervisor design (§7) removes the need for a percentage; `scripts/cloud_setup.sh` supplies the graph tools (proven in re-check #2: 3/3 graphs built, all lookups answer); notifications move into `HANDOFF.md` and commit prefixes.

**Cloud re-check #2** ([part1/CLOUD_RECHECK2.md](part1/CLOUD_RECHECK2.md)) found the cloud sets `SKIP_PLUGIN_MARKETPLACE=true`, so the Ponytail plugin never loads there; its six skills (MIT) are therefore **vendored in `.claude/skills/`**, which loads without a marketplace. It also found a routine created from inside a session starts with no repo, so the supervisor routine is created in the Routines UI (§7). A `dryrun` tick (SUPERVISOR.md) proves the end-to-end path before `Autonomy` is set to unattended. If that fails, Phases 1–5 run in an attended session with the same protocol.

## 12. Known limits of this plan (stated, not hidden)

- Orchestration (§7) is the supervisor design; it is proven only after cloud re-check #2. A session stopped by the limit being woken by the routine (rather than a fresh session) is not relied on.
- Cloud-verifiable evidence is mechanism-level (synthetic fixtures). The report's live
  numbers can only be confirmed locally — which is why `LOCAL_VERIFY.md` exists.
- Parallelism is modest: roughly half of Part 1 is a serial chain through one file. The
  larger saving is cost, from routing mechanical items to Sonnet.
- File ownership for P4–P6, P12, P16, P19, P20 is inferred until Phase 0.5.
