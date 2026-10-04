# Part 1 — cloud re-check #2 (before Phase 1)

**Date:** 2026-10-04, 10:15–10:45 UTC · **Branch:** `feat/part1-fixes` from `382affe` · **Lead:** cloud session
`session_01NvPSpVeQWhuwo6M1z1vX2s` (Opus 5.5, effort high, permission mode `auto`)
**Scope:** plan §11 re-check #2: setup script, graphs, Ponytail, MCP server, and a session started by a
routine. No Part 1 code was written. Raw logs are under `.tmp/cloud_recheck2/` (gitignored).

## Verdict

**No. The hourly supervisor cannot run Phases 1–5 unattended in the cloud yet.**

**Item 4 decided it.** A routine created from a session (`create_trigger`, fresh session per fire) starts a
new session **without the repository**. The trigger stores `sources: []`, and the tool has no parameter
to set a repo or branch. In that empty session the environment's setup script,
`bash scripts/cloud_setup.sh`, fails with exit 127 and the session ends before Claude starts. No
HANDOFF.md was read and nothing was pushed. Each hourly tick would die the same way.

Everything else holds up. The three graph tools install and all four lookups work; `gh` reads CI; the
routine fires on time and is not denied. Two gaps remain: Ponytail does not load in the cloud, and the
codebase-memory MCP server was not shown to load in a fresh session (only its CLI). Items 1–4 of
"What is still missing" are on your side (environment, routine and plugin settings, one decision);
item 5 is a small repo fix outside this re-check's scope.

| # | check | result |
|---|---|---|
| 1 | environment setup script ran | **no** for this session (`has_init_script:false`, resumed from cache); **yes** for a new session (§4), where it failed only because no repo was cloned. Tools installed here by running `scripts/cloud_setup.sh` by hand, exit 0 |
| 2 | `refresh_graphs.py`, 3 graphs, queries | **pass** (exit 0, 20 s, 3/3 built); `graphify query`, `graft ask`, `search_graph`, `check_index_coverage` all answer — the last two **only through the CLI**, the MCP server is not loaded |
| 3 | Ponytail | **unavailable**: 0 of 6 commands; `SKIP_PLUGIN_MARKETPLACE=true` in the cloud container |
| 4 | routine-started session | **fail**: a new session *was* started, but with **no repo** ("No sources configured"); the environment setup script then failed with exit 127 and Claude never ran. HANDOFF not read, nothing pushed |
| 5 | hourly cron permitted | **not tested by design**; nothing seen that would deny it (see §5) |
| 6 | read CI from here | **yes**: `gh run list/view --log-failed` and the GitHub MCP `actions_list` both work |
| — | side finding | **Bootstrap workflow is red** on `963c64f` (last run on the branch); `CI` is green on `382affe` |
| — | side finding | rate-limit status reads **`allowed_warning`**, which SUPERVISOR.md step 2 treats as "exit" |

## 1. Did the environment setup script run? — **not for this session; yes for a new one (§4)**

Evidence (`/tmp/env-manager.log`, the cloud environment manager's own log):

- 09:05:17Z, the cached environment build (`session_mode: setup-only`): `"has_init_script": false`.
- 10:15:22Z, this session's start (`session_mode: resume-cached`): `"has_init_script": false`.

So no setup script was attached to the environment when its cached image was built, and this session
resumed that image without running one. Even if one had been configured as
`bash scripts/cloud_setup.sh`, it would have failed with "No such file": the session's clone was at
`6826c91`, and `scripts/cloud_setup.sh` only arrived in `382affe`, which was pushed after the clone.

Whether the setup script the user has *since* entered runs for a *new* session is answered by §4 (the
routine starts a new session).

**Tools on PATH now** — installed at 10:21:48–10:22:19Z by running `bash scripts/cloud_setup.sh` by
hand in this session (exit 0, ~3 min including `bootstrap.py`; `/opt/graphtools` mtime 10:21:48Z):

| tool | path | version |
|---|---|---|
| graphify | `/usr/local/bin/graphify` → `/opt/graphtools/bin/graphify` | `graphify 0.9.67` (`graphifyy` 0.9.67) |
| graft | `/opt/node22/bin/graft` | `0.19.0` (prints "0.21.1 available"; the pin is deliberate) |
| codebase-memory-mcp | `/usr/local/bin/codebase-memory-mcp` → `/opt/graphtools/bin/…` | `codebase-memory-mcp 0.11.0` |

The install was **not denied** by the permission classifier this time (re-check #1: denied as
"Untrusted Code Integration"). The difference is that the user explicitly asked for the script to run.

## 2. `python scripts/refresh_graphs.py` — **pass**

`.venv/bin/python scripts/refresh_graphs.py` → **exit 0, 20 s wall**. `.tmp/refresh_graphs.log`:

```
=== 2026-10-04 10:26:27 HEAD=382affe9 ===
ok   graphify (8.4s)
ok   graft (1.2s)
ok   codebase-memory-mcp (10.1s)
all graphs refreshed
```

- `graphify-out/graph.json`: 9,368,160 bytes, 8,509 nodes. `graft/`: 283 files (`INDEX.md`, `scripts/`, `src/`, …).
- **`graphify query "where are candidate pairs generated for entity resolution"`**: exit 0, 288 nodes found
  (BFS depth 2, truncated to the 2,000-token budget). Works, but a broad natural-language query lands on
  test docstrings first; a symbol name works better.
- **`graft ask "_generate_candidate_pairs"`**: exit 0, rank 1 is
  `src/mcp_server/tools/resolve_entities.py:L1060-L1273`. Independent check: `grep -n "def _generate_candidate_pairs"`
  gives line 1060. Correct.
- **codebase-memory-mcp `search_graph`** (`codebase-memory-mcp cli search_graph
  '{"project":"home-user-merge-engine","name_pattern":"_generate_candidate_pairs"}'`): exit 0, 2 results,
  the function at `resolve_entities.py 1060-1273`, 27 in / 12 out edges. Matches grep.
- **`check_index_coverage`** (`paths: [resolve_entities.py, fellegi_sunter.py]`): exit 0, both
  `no_recorded_issue`, `metadata_match`, index `generation 2026-10-04T10:24:44Z`, `recording_status: complete`.

**The MCP server is not loaded in this session.** No `search_graph` / `check_index_coverage` tools exist
(`ToolSearch` finds none). The session's MCP config (`/tmp/mcp-config-cse_….json`) lists only `github`,
`Claude_Docs`, `claude-code-remote`. `.mcp.json` arrived with the pull at ~10:21Z, after the session had
started, and the binary did not exist at start either, so this session cannot show whether a new session
would pick `.mcp.json` up. The CLI gives the same tools, so tier 3 is usable either way; §4 records what a
new session saw.

## 3. Ponytail — **unavailable**

- `Skill` for each of `ponytail`, `ponytail-review`, `ponytail-audit`, `ponytail-debt`, `ponytail-gain`,
  `ponytail-help`: all six return `Unknown skill`.
- No Ponytail files anywhere on the container (`find / -ipath '*ponytail*'` outside the repo: none).
  `~/.claude/plugins/` holds only `synced/<account>` (empty). The Claude Code log shows
  `PluginSkills: Processing 3 enabled plugins` (the built-in telemetry, plugin-authoring, agents-md) and
  `plugin skills loaded: 0`; the word "ponytail" appears in it only from my own `ToolSearch`.
- **Likely cause:** the container sets **`SKIP_PLUGIN_MARKETPLACE=true`** (environment-manager key list
  and `echo $SKIP_PLUGIN_MARKETPLACE`). The committed `.claude/settings.json` declares the marketplace via
  `extraKnownMarketplaces` and enables `ponytail@ponytail`, but a client that skips marketplaces never
  fetches it. Caveat: this session also started before `.claude/settings.json` was pulled, so it is not a
  clean test on its own; §4's fresh session is.
- **`/ponytail-review` on this re-check's diff could not be invoked.** Done by hand against its rules
  (smallest diff, delete before adding, nothing speculative): the diff is this report, a
  `HANDOFF.md` update and the routine's one-line `.handoff/supervisor_dryrun.txt`. No code, scripts,
  config or dependencies. Scratch output stays in gitignored `.tmp/`. Nothing to cut. This is **not** a
  plugin run.

## 4. One-shot routine — DRY RUN of SUPERVISOR.md

Created with `mcp__claude-code-remote__create_trigger`: `trig_01KQZUDeSFTaLhoKGKQ4ZZ9w`, one-shot,
`run_once_at 2026-10-04T10:38:00Z`, `create_new_session_on_fire: true`, `initiation: human_request`.
**Not denied.** The prompt was SUPERVISOR.md steps 1–2 only: fetch/checkout/pull `feat/part1-fixes`, read
HANDOFF.md, evaluate the four exit conditions, write one line to `.handoff/supervisor_dryrun.txt`,
commit, push. It explicitly excluded the lock, code and everything else.

**What the create call already showed.** The stored request has `"sources": []`, `"mcp_connections": []`,
`"enabled_plugins": []`, `"extra_marketplaces": []`, plus the warning that fired sessions run "without
connector (mcp__<server>__*) tools". The tool has no `source_url` / branch parameter (only
`create_session` has one).

**What happened** (`get_trigger`, then `get_session` / `list_events` on the started session):

| question | answer | evidence |
|---|---|---|
| was a new session started? | **yes** | `session_01SbErWYmeEKdWQNDp1DrKUh`, `origin: scheduled_trigger`, `session_mode: new`, created 10:38:52.6Z (fired 52 s after the scheduled minute) |
| was the repo cloned? | **no** | env-manager step `clone`: **"No sources configured"**, `step_status: skipped` |
| did the setup script run? | **yes, and failed** | step `setup_script`: "Running setup script" 10:38:55.43Z → "Setup script failed"; synthetic message: `Setup script failed with exit code 127. bash: scripts/cloud_setup.sh: No such file or directory` |
| did Claude start? | **no** | `result`: `error_during_execution`, `num_turns: 0`, cost 0; `last_init_error.error_kind: init_script`, `recoverable: false` |
| did it see the right HANDOFF.md? | **no** | it never ran; there was no checkout to read |
| did its push land? | **no** | `git ls-remote origin refs/heads/feat/part1-fixes` stayed `382affe9…` from 10:28Z to 10:45Z (polled every 15 s until 10:40Z) |
| routine status | **FAILED** | `last_run.status: ROUTINE_RUN_STATUS_FAILED`, `finished_at 10:38:55.6Z`, `ended_reason: run_once_fired` |

No permission denial occurred anywhere in item 4; the failure is configuration, not permission. Nothing
was retried or worked around.

**Two facts this does establish:**

1. **The environment setup script is now configured** and runs for new sessions, in the documented
   order `provision → clone → setup_script → start_cc`. So item 1's question ("did it run?") is: not for
   this session (resumed from a cache built without it), **yes** for a new one.
2. **The setup script is a hard gate.** Exit ≠ 0 kills the session with `recoverable: false`. Any future
   failure of `cloud_setup.sh` (a PyPI/npm outage, a moved tool) stops every supervisor tick before it
   can write `[BLOCKED]` to HANDOFF.md. The failure is visible only in the routine's run status.

Still unproven, because the session never got that far: whether a fresh session loads Ponytail from
`.claude/settings.json` and the MCP server from `.mcp.json`, and whether `gh` is authenticated in a
routine-started session.

## 5. Could the routine run hourly (`0 * * * *`) without a denial? — **not tested, by instruction**

The hourly routine was **not** created. What can be said without creating it:

- The one-shot `create_trigger` call in §4 (`initiation: human_request`, `create_new_session_on_fire: true`)
  was **not denied**. An hourly one is the same call with `cron_expression` in place of `run_once_at`.
- The tool's own rules allow it: the minimum interval is "normally hourly", so `0 * * * *` is at the limit,
  not below it. **Note:** for hourly schedules at minute 0 the server "anchors it to the creation minute",
  so the routine fires at *creation minute* past each hour, not at :00. Harmless for the supervisor.
- Not provable here: whether the auto-mode classifier would treat a standing hourly routine differently
  from a one-shot. The classifier judges each call. SUPERVISOR.md being the user's written authorisation,
  plus the user asking in the session, is the strongest case available. If it is denied, the user can
  create it from the claude.ai Routines UI with the SUPERVISOR.md prompt.
- **Routines get no connectors** when created from this session. The create result warned: "this trigger
  stores no MCP connectors, so the sessions it fires will run without connector (mcp__<server>__*)
  tools". So a supervisor session has **no GitHub MCP**. It must read CI with `gh` (see §6), and §4 shows
  whether `gh` is authenticated there.

## 6. Can this session read CI? — **yes**

- `gh` 2.89.0 at `/usr/local/bin/gh`, authenticated through the agent proxy.
  `gh run list -R bayern4teams/merge-engine -b feat/part1-fixes -L 8` lists runs;
  `gh run view 37193690059 --log-failed` returns the failing step's log.
- GitHub MCP `actions_list` (`list_workflow_runs`, branch `feat/part1-fixes`) returns the same runs
  (total 12).
- Read-only: no PR, comment or re-run was created.

**Side finding — the Bootstrap workflow is red on this branch.** The latest `Bootstrap` run is
[37193690059](https://github.com/bayern4teams/merge-engine/actions/runs/37193690059), `failure` on `963c64f`.
The step "Graph refresh degrades cleanly without the tools" (`.github/workflows/bootstrap.yml`) still
expects `refresh_graphs.py` to exit 0 with no tools installed. `963c64f` deliberately changed it to exit 1
("its graph was NOT built"). The workflow's expectation and the script now disagree. `382affe` did not
touch the workflow's `paths`, so no Bootstrap run exists for it; `CI`
([37193947992](https://github.com/bayern4teams/merge-engine/actions/runs/37193947992)) is green on `382affe`.
The §8 phase gate says "green CI on the pushed head". If the supervisor reads that as every workflow, a
Phase-1 commit that touches `scripts/` would see Bootstrap red and block. The fix (assert the non-zero
exit in the workflow, or install the tools there) is in `.github/` and `scripts/`, outside this
re-check's "no code" scope. Not fixed here.

**Side finding — rate limit `allowed_warning`.** `get_session` at ~10:28Z: `rate_limit_info.status:
"allowed_warning"`, `rateLimitType: five_hour`, `resetsAt: 1791115200` (12:00Z). Re-check #1 saw
`allowed`. So the status *is* a coarse usage signal (it changes before the limit is hit), but
SUPERVISOR.md step 2 says to exit when it "is not `allowed`". As written, every tick in the warning band
exits without working. That may be what you want (stop early, like the old 90 % rule), or not. It needs
a decision.

## What is still missing

In order; 1 is the blocker.

1. **Routine sessions need the repo.** Pick one:
   - **(a, recommended)** Create the hourly routine yourself in the claude.ai Routines UI, with repo
     `bayern4teams/merge-engine`, branch `feat/part1-fixes`, the SUPERVISOR.md prompt, and the
     GitHub connector. A routine made by the in-session tool cannot carry a repo.
   - **(b)** Bind the routine to one long-lived session that already has the repo
     (`persistent_session_id`, or self-bound like re-check #1's S4, which did execute). It reuses one
     context, so it relies on compaction; HANDOFF.md-in-git makes that safe, but it is not the
     fresh-session design of §7.
   Then run this item-4 dry run once more before Phase 1. It is the only end-to-end proof, and it
   would also settle the three unproven points above.
2. **Harden the setup script line** in the environment settings so a missing file or a failed install
   does not kill the session silently, for example
   `[ -f scripts/cloud_setup.sh ] && bash scripts/cloud_setup.sh || echo "cloud_setup failed: $?" >&2`.
   The supervisor's step 4 (run bootstrap if graphs are missing) then becomes the place where a broken
   tool is noticed and written up as `[BLOCKED]`.
3. **Ponytail.** `SKIP_PLUGIN_MARKETPLACE=true` is set by the cloud container, so the committed
   `extraKnownMarketplaces` probably never loads. Unless the dry run in (1) shows it does, either add
   Ponytail as an account-level plugin/skill that cloud sessions sync, or accept a by-hand review
   against its rules (as both re-checks did) and say so in plan §5.
4. **Decide `allowed_warning`.** SUPERVISOR.md step 2 exits on anything but `allowed`. Keep it
   (a warning-band stop, close to the old 90 % rule) or change it to exit only on a real limit.
5. **Bootstrap workflow is red** (§6). Make `.github/workflows/bootstrap.yml` assert the new non-zero
   exit, so the phase gate's "green CI" is not blocked by a stale expectation. That is a `scripts/`/`.github/`
   change, a request back to whoever owns those files, not something this re-check edits.

Until 1 is done and the repeat dry run passes, plan §11's fallback applies: **Phases 1–5 run in an
attended session (local, or a cloud session the user launches), same HANDOFF protocol.** Phase 1 was
not started.
