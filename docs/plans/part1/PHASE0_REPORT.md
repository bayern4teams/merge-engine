# Part 1 — Phase 0 report (smoke test and baseline)

**Date:** 2026-10-04 · **Branch:** `feat/part1-fixes` (base commit `ab12e0d`) · **Lead:** Opus 5.5
**Scope:** plan §8 Phase 0, steps 0.1–0.8. No Part 1 code was written.

## Headline

| | result |
|---|---|
| Environment | **Local desktop session, not the cloud** the plan was written for. Every finding below is about this machine; S1–S7 must be re-run if Phases 1–5 move to the cloud. |
| `check.py all` locally | **green**, 11/11 stages, 85 s |
| CI (GitHub Actions) | **red** — on this branch's head and on every `main` push since 2026-09-27. Blocker B1. |
| `bootstrap.py` | **fails** — hard-codes `.venv`, which here is Python 3.14 with no pip. Blocker B2. |
| Smoke tests | S1 partial · S2 pass · S3 pass · S4 **blocked by permission** · S5 partial · S6 pass · S7 not testable from inside |
| Ownership | locked in [HANDOFF.md](HANDOFF.md); **5 flags**, 3 of them change the Phase 1/3 lane plan |
| Baseline | scorer proven both ways; 6 failure shapes fixtured with before-numbers. **3 surprises** change acceptance: P1 is mostly handled already, P20's own shape already passes, and P2's `uncompared_pairs` over-counts 5.6× |

## Blockers — need a decision from the user before Phase 1

### B1 — CI is red, local is green (dependency drift)

The plan (§1, §8 0.3) assumes CI is green. It has not been since **2026-09-27**: the last
green run is `35857069147` (2026-09-23); every CI run after it failed, including
`37189323268` on this branch's head commit `ab12e0d`.

Evidence from `gh run view 37189323268 --log-failed`:

| stage | CI | local `.venv311` |
|---|---|---|
| `types` | **FAIL** — 9 new mypy errors: `splink.internals.similarity_analysis` import-not-found; `Argument 3 to "Linker" has incompatible type "DuckDBAPI"` ×7 (`splink_common.py`, `splink_model.py`, `splink_workbench.py`, `splink_matcher.py`); one unused `type: ignore` | ok (2.6 s) |
| `test` | **FAIL** — collection error: `ModuleNotFoundError: No module named 'greenlet'` (SQLAlchemy asyncio) from `tests/unit/test_analyze_dependencies_join_graph.py` | ok (74.7 s) |
| `eval` | **FAIL** — same `greenlet` error | ok (0.8 s) |
| all other stages | ok | ok |

Root cause: `pyproject.toml` floors but does not cap these dependencies, so CI installs the
newest releases while the local venv holds older ones:

| package | `pyproject.toml` | CI resolved | local `.venv311` |
|---|---|---|---|
| splink | `>=4.0.0` | **5.0.0** (API break) | 4.0.17 |
| sqlalchemy | `>=2.0.0` | **2.1.3** (no longer pulls in `greenlet`) | 2.0.54 (+ greenlet 3.5.6) |
| ruff | `>=0.4.0` | 0.16.10 | 0.16.8 |
| duckdb | — | 1.5.6 | 1.5.5 |

The first failing run (`36321375541`, 2026-09-27) already shows the `greenlet` failure, so
this is drift over time, not something a Part 1 commit introduced. Per the plan it is
reported, **not worked around**: fixing it means editing `pyproject.toml` (and probably
`requirements*.txt` via `scripts/generate_requirements.py`), which is outside Phase 0.
Likely fix, for the user to approve: `sqlalchemy[asyncio]>=2.0,<2.2` (or an explicit
`greenlet` dependency) and `splink>=4.0.0,<5`, then regenerate requirements. Until it lands,
"`check.py all` green" at a phase gate can only mean *locally green*, and CI cannot act as
the independent check the plan relies on.

### B2 — `scripts/bootstrap.py` cannot bootstrap this checkout

```
Python 3.11.15 — OK
/home/bayern/Projects/merge-engine/.venv already exists — reusing it.
Installing merge-engine[dev,perf,schema_matching,dq] ...
/home/bayern/Projects/merge-engine/.venv/bin/python: No module named pip
FAILED: pip install -e '.[dev,perf,schema_matching,dq]' failed — see output above.
```

`bootstrap.py` hard-codes `VENV_DIR = ROOT / ".venv"` and reuses whatever is there,
whichever interpreter launched it. Here `.venv` is Python **3.14.7** with no pip (a known
local hazard). The 3.11 environment that works is `.venv311`. Consequences:

- Steps 3–7 of bootstrap never ran in this session. The hooks were already installed
  (pointing at `.venv311`) and the graphs already existed, so nothing downstream was lost.
  Graphs were refreshed with `.venv311/bin/python scripts/refresh_graphs.py` (CLAUDE.md's
  own fallback): graphify 5.1 s, graft 0.8 s, codebase-memory-mcp 2.5 s, all ok.
- In a **cloud** clone there is no `.venv`, so bootstrap would create one from the 3.11
  interpreter and should work. It also installs only `[dev,perf,schema_matching,dq]`,
  whereas CI and the plan §1 install `[dev,perf,splink,dspy,schema_matching,dq,lineage,swarm]`.
  A bootstrapped env would therefore fail `check.py types` the same way CI's root cause #3
  did (missing extras). Worth fixing alongside B1; not touched here.

## 0.1 — Plan read, branch confirmed

Read CLAUDE.md, the plan, HANDOFF.md, LOCAL_VERIFY.md, both agent definitions, and the
report's §0 and PART 1 (P1–P20 plus ordering). Branch `feat/part1-fixes`, clean, up to date
with origin at `ab12e0d`.

## 0.2 — Bootstrap, graphs, coverage, tool versions

| tool | version / state |
|---|---|
| Python | `.venv311`: 3.11.15 (`.venv`: 3.14.7, unusable — see B2) |
| ruff / mypy | 0.16.8 / 2.3.1 |
| Node / npm | v22.23.2 / 10.9.8 (plan §1 says Node 20; `check.py frontend` passes on 22) |
| graphify | 0.9.67 (warns that a Hermes skill copy is 0.9.64; harmless) |
| graft | 0.19.0 (0.21.1 available; not upgraded) |
| codebase-memory-mcp | indexed, `index_mode: full`, generation 2026-10-04T08:37Z |
| git hooks | `pre-commit` + `post-commit` installed, interpreter `.venv311/bin/python` |
| Ponytail | plugin 4.9.0, active in `full` mode via the SessionStart hook |

`check_index_coverage` on the eight files Part 1 leans on (`T/resolve_entities.py`,
`T/map_fields.py`, `T/fellegi_sunter.py`, `T/join_graph.py`, `T/unify_entity_group.py`,
`T/survivorship.py`, `orchestrator/graph.py`, `N/resolve_entities.py`): all
`no_recorded_issue`, `metadata_match`. Best-effort only, as the tool itself says.

Nothing failed to install, because nothing was installed: the existing `.venv311` was used.
Network allowlist is therefore not measured here. For the cloud it is whatever
`pip install -e ".[…all extras…]"` and `npm ci` need (PyPI, files.pythonhosted.org,
registry.npmjs.org), plus `github.com` for push.

## 0.3 — `check.py all` (local, `.venv311`, `TMPDIR=$PWD/.tmp`)

| stage | result | wall |
|---|---|---|
| lint | ok | 0.1 s |
| format | ok | 0.0 s |
| arch | ok | 0.4 s |
| docs | ok | 0.2 s |
| portability | ok | 0.0 s |
| guards | ok | 0.7 s |
| types | ok | 2.6 s |
| frontend | ok | 4.0 s |
| test | ok | 74.7 s |
| eval | ok | 0.8 s |
| prompts | ok | 1.4 s |
| **total** | **11/11 passed** | **85 s** |

It does **not** match CI (B1). Per the plan that is a Phase 0 blocker, reported above.

## 0.4 — Smoke tests

| test | verdict | evidence |
|---|---|---|
| **S1** worker model/effort | **partial** | Three `part1-worker` spawns each wrote their self-reported identity to a scratch file: all three `Sonnet 5.5 (claude-sonnet-5-5)`, so the frontmatter `model: sonnet` **is honoured**. Effort is **not confirmed**: two wrote `effort=not-stated`; the third wrote `effort=low`, saying it saw a reasoning-effort setting of low. The frontmatter says `effort: high`. A subagent's effort is not reliably observable from inside, and one sighting says it is *not* high. Treat worker effort as unverified, and do not route judgement-heavy work to workers on the assumption it is high. |
| **S2** 3 parallel workers + `SendMessage` | **pass** | Workers B and C wrote their files 12 ms apart (04:38:10.376 / 04:38:10.388), so they ran concurrently. Worker A was told a word to remember. After it finished, a `SendMessage` correction asked it to append that word; it appended `secret=PELICAN-7`, which was never written to disk before, so the resumed agent kept its context. Side note: auto mode flagged A's reply as "Instruction Poisoning" because of the remember-a-secret design. Future context checks should use a neutral fact. |
| **S3** usage readable | **pass** | `mcp__ccd_session_mgmt__get_usage` returns plan limits: Pro plan, 5-hour window **9 %** (resets 12:00 UTC), weekly 2 %; context 10 % of 1 M, auto-compact at 97 %. The lead can check usage at each item boundary as §7 wants. Note: **Pro**, not Max — plan §3.3's "3 concurrent subagents" will hit the 5-hour cap much faster on Pro. |
| **S4** scheduled resume | **blocked** | Creating a one-shot scheduled task (fire 04:45 EDT: append to `.tmp/phase0/s4/fired.txt`, read `HANDOFF.md` from git) was **denied by the auto-mode permission classifier**, which gave no reason. Not retried through another tool (e.g. `CronCreate`), since that would route around the denial. **Decision for the user:** allow scheduled tasks for this project, or accept "user launches each phase". Until then §7 defaults to the latter. |
| **S5** push notification | **partial** | `PushNotification` ran; the tool replied *"Mobile push not sent (Remote Control inactive)"*. A desktop notification may have arrived; only the user can confirm. With Remote Control off, phase-end notifications reach this desktop only. |
| **S6** Ponytail ×6 + graph tools ×3 | **pass** | All six Ponytail skills are registered (`ponytail`, `-review`, `-audit`, `-debt`, `-gain`, `-help`); `-help` was invoked and loaded; `-review` was run on this phase's diff (below). graphify (`affected`, `query`), graft (`skeleton`) and codebase-memory-mcp (`check_index_coverage`, `index_status`) were all used for real in 0.2/0.5. |
| **S7** `/compact` | **not testable from inside** | `/compact` is a user-typed CLI command, not a tool or skill the session can call. What is measurable: context 10 % used after the ownership survey; auto-compact triggers at 97 % of a 1 M window. For §7: a fresh session per phase stays the default; the lead cannot compact itself on demand. |

## 0.5 — Locked file-ownership table

Written into [HANDOFF.md](HANDOFF.md) with the lane plan. Five flags, three of which change
the plan's lanes:

1. P13 cannot stay inside `fellegi_sunter.py`: `estimate_model` only ever sees candidate-pair
   vectors, and building vectors for random pairs lives in `resolve_entities.py`. The worker
   keeps `fellegi_sunter.py`; the lead makes the call-site change in the serial chain.
2. `orchestrator/nodes/load_data.py` (where the run report is built) is touched by five
   items: P18 ⑤, P4, P17, P16 and P10. One owner per phase.
3. `orchestrator/nodes/human_nodes.py` / `orchestrator/graph.py` are shared in Phase 3 by
   P15, P9 and P17. Side lanes own only their new modules; the lead applies the gate hooks
   after P15.
4. P20 needs P16's name-variation rate, but the DAG orders P16 after P20. P20 creates the
   helper and P16 reuses it.
5. `T/resolve_entities.py` is shared by nine items (expected: the lead's serial chain).

## 0.6 — Scorer-first baseline

**Surviving artifacts.** None of `state.json`, `gates.jsonl`, `score.json`, `gt_score.py`,
`gt_rows.py`, `run_merge.py` is in the repository, tracked or untracked (searched by name
across the tree, excluding venvs and `node_modules`). The report's live figures can
therefore only be quoted, not re-derived. They are carried into LOCAL_VERIFY.md as "before"
values.

**Synthetic fixtures** under `tests/part1/`, built by two `part1-verifier` agents in
parallel on disjoint files. The lead then re-ran both baselines independently: each was
byte-identical to the agent's output (matching also under `PYTHONHASHSEED=7`). `src/` is
untouched; the §2.4 literal grep over `tests/part1/*.py` is empty; `check.py lint format` is
green. None of it runs under `check.py test` (which collects `tests/unit` and
`tests/integration` only). These are baselines, not gates; the Phase 1+ acceptance tests
derived from them will be.

| file | what |
|---|---|
| `scorer.py` | pair-level TP/FP/FN with the named FP/FN pairs, split cross- vs within-source |
| `fixtures_matching.py`, `baseline_matching.py` → `before_matching.json` | P2, P6, P20 shapes |
| `fixtures_schema.py`, `baseline_schema.py` → `before_schema.json` | P1, P3, P17 shapes |

Reproduce: `TMPDIR=$PWD/.tmp .venv311/bin/python -m tests.part1.scorer`,
`… -m tests.part1.baseline_matching`, `… -m tests.part1.baseline_schema`.

**Scorer proven both ways** (`python -m tests.part1.scorer`): identity → TP 2 / FP 0 / FN 0;
one injected FP → FP 1, pair `(c1, d1)` named, in the cross-source stratum; one injected FN →
FN 1, pair `(b1, b2)` named, in the within-source stratum.

**Before numbers on the current code** (counts, not rates):

| shape | planted truth | before (current code) | finding |
|---|---|---|---|
| **P1** concatenated-lowercase source fields vs a destination vocabulary (no LLM, heuristic tier) | 11 fields map; 3 negatives must not | concat **7/11** mapped, **4 missed** (`foundingdate`, `taxregistration`, `contactemail`, `postalcode`); camelCase 11/11 and snake_case 11/11 controls; 0 false maps | **The report is only partly right.** 7 of 11 already map via the underscore-collapse rule (`map_fields.py:944`, score 0.85), which bypasses the tokeniser. The tokeniser only matters when the destination has no delimiter-stripped twin (inflection differs, or the source holds a subset of the destination's parts). P1 acceptance must target those 4 plus the 3 negatives, or a no-op fix passes. |
| **P2** one 800-record block = 319 600 pairs > `_MAX_COMPARISONS` (250 000), 40 planted pairs | 40 true pairs | 250 000 returned, **69 600 truly discarded**, **9 of 40 planted pairs lost** (pair completeness 31/40); no exception, one WARNING | **New defect:** the reported `uncompared_pairs` is **389 200**, 5.6× the true discard. The same 800-member block arrives under two keys, and the overflow estimate is not deduped against pairs already seen (`resolve_entities.py:1240-1253`), so it **over**-counts, though the code comment calls it a lower bound. The report's live 711 670 / 9 556 382 discard figures may be inflated by the same double-count. Added to LOCAL_VERIFY. |
| **P6** three-way company set | (a) same email → Match; (b) same address + different tax IDs → Possible Match; (c) same address + phone, different company → Possibly Related | heuristic path: (a) 1.0, auto-merged ✔; (b) 0.775, review band; (c) **never compared** (not co-blocked); co-blocked variant 0.667, excluded. With a fitted model (14 fillers): (a) **0.76 → review** ✘; (b) **0.015 → silently excluded** ✘ (the exact outcome the report forbids); no typed "Possibly Related" edge exists | Outcome depends on whether the model is fitted, so acceptance must compare outcome *classes* on both paths, not exact scores. |
| **P20** two clean 5-clusters + one 0.96 bridge, plus a chain variant | clusters {5, 5} | **already correct**: {5, 5}, FP 0, FN 0, thanks to the existing `_MIN_COHESION` cut | **The report's own shape does not fail today**, so a no-op P20 would pass it. What does fail: (i) wrong edge strongest (bridge 0.99 over internal 0.96) → both clusters shatter, FP 1 (`src_a:t:a0`–`src_a:t:b0`), **FN 20**; (ii) a true cluster seen only as a chain → 5 singletons, **FN 10**. P20 acceptance must include both. |
| **P3** parent with surrogate `_id` + unique business key; child FK `member.owner_ref` (name unrelated to the parent) fully contained in the business key; a decoy `ticket.orgid` whose values coincidentally overlap `org._id` | 2 true edges (`member`, `note` → `org`); decoy rejected | **0 of 2** true edges found. `member.owner_ref` is never proposed; `note.org_id → org._id` is proposed and rejected (the report's shape). The **decoy is accepted** as verified at 1.0. `dependency_tree = {member: [], note: [], org: [], ticket: [org]}`, `origin: ALGORITHMIC`, `fully_covered: True`, so the LLM is never asked | The report says `dependency_tree = {}`; on this shape it is **non-empty but wrong**, and the node is confident about it. "Non-empty" alone is therefore a bad P3 acceptance criterion; the tree must match the planted truth. Containment and uniqueness alone cannot reject the decoy, so it tests the P3 filters properly. |
| **P17** destination link table with only an `id` column; 40 source link rows each needing 2 linkage columns | defect reported, 0 rows written, no DDL | **40 id-only rows written**, 80 linkage values lost, `errors = []`, `failed = []`, **no defect reported**; SQL issued: one no-op `CREATE TABLE IF NOT EXISTS`, one `INSERT`; 0 `ALTER` | Reproduces the report's 539-row shape at mechanism level. Limit: driven through `_load_all_entities` with a recording stub destination, not through `load_data_node` or a real Postgres, so the pre-load gate path is unexercised. |

Full per-fixture reports: `.handoff/P0-6-matching.report.md`, `.handoff/P0-6-schema.report.md`
(working files, purged in Phase 5 per the plan).

## 0.7 — LOCAL_VERIFY.md

[LOCAL_VERIFY.md](LOCAL_VERIFY.md) now opens with the database-restore procedure (report §0.2:
`ground_truth` dropped, `global_warehouse` wiped, `enrichment` credentials changed, evidence
artifacts absent) and a table of the report's "before" values per item, which the user must
see change. Per-item entries are added as items are accepted.

## `/ponytail-review` of the Phase 0 diff

Run on the staged Phase 0 diff (13 files). Findings, all applied:

- `baseline_schema.py`: yagni, a `--stdout` print-only mode with no user. Deleted.
- `baseline_schema.py`: shrink, a `sys.path.insert` hack plus three `# noqa: E402`. Now run as
  `-m tests.part1.baseline_schema`, like the matching baseline.
- `baseline_schema.py`: shrink, an inline tuple duplicating `_edge_key`. Reused.

After the cuts, `before_schema.json` regenerates byte-identical; `check.py lint format docs`
is green. The scorer (79 lines including its self-proof) and the matching baseline were
judged lean. The JSON baselines and docs are requested deliverables, not cut candidates.
Net: −6 lines.

## Recommendations for §7 (the user decides)

- **Sessions:** fresh session per phase, user-launched (S4 blocked; S7 not self-invocable).
- **Usage:** check `get_usage` at every item boundary. On Pro, budget **≤ 2 concurrent
  subagents** rather than 3, and expect one phase per 5-hour window at most.
- **Routing:** keep Sonnet workers for mechanical items, but treat their effort as
  unverified (S1). Pass `model: "sonnet"` explicitly at spawn as a belt-and-braces measure.
- **Before Phase 1:** land B1 (pin `splink<5`, `sqlalchemy[asyncio]`), so CI is an
  independent check again; optionally B2.
