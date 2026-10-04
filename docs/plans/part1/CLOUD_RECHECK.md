# Part 1 — cloud re-check (before Phase 1)

**Date:** 2026-10-04, 09:05–09:20 UTC · **Branch:** `feat/part1-fixes` at `6826c91` · **Lead:** Opus 5.5, high effort
**Scope:** plan §11 re-check: S1–S5, bootstrap, graphs, `check.py all`, Ponytail. No Part 1 code was
written. Raw logs are under `.tmp/cloud_recheck/` (gitignored, so they die with the container).

## Verdict

**Run Phases 1–5 in a local session. Do not run them unattended in the cloud yet.**

**S3 decided it.** The cloud exposes the 5-hour window's *state* but not its *percentage*,
so the §7 rule ("at ≥ 90 % … stop, commit, schedule a resume") cannot be applied. By §11's
own rule ("if the cloud cannot read usage … Phases 1–5 run in a local session"), that is
disqualifying. S4 did **not** decide it: scheduled wake-ups work here, which is better than the
local result. S5 is unconfirmed (the tool says the push was requested; only you can say it
arrived), so it adds doubt but did not decide anything.

Two non-S findings would block unattended cloud work on their own, even if S3 were fixed:

- **The graphs do not exist in the cloud, and the tools to build them are not installed.**
  `bootstrap.py` reports "Code graphs ready" while building nothing (§3). CLAUDE.md makes
  graphify/graft/codebase-memory-mcp the primary lookup tiers. Installing them was denied by
  the permission classifier.
- **Ponytail is not available in the cloud** (§10). The plan (§5, worker brief) depends on it.

Both are environment fixes you can make, listed at the end. The S3 gap is not: it needs a
usage API in the cloud session.

| check | result |
|---|---|
| 1 Environment | cloud, Python 3.11.15, Node v22.22.0 |
| 2 `bootstrap.py` from fresh | **succeeds**, exit 0, 148 s; no allowlist change needed, **but the graph step silently does nothing** |
| 3 Graphs + 3 tools | **fail**: no `graph.json`, no `graft/`, tools not installed; install **denied** |
| 4 `check.py all` | **green, 11/11, 156 s**; matches CI run 37190719097 on the same commit |
| S1 worker model/effort | model **honoured** (Sonnet 5.5); effort **not high** (worker sees `reasoning_effort` 10) |
| S2 parallel + `SendMessage` | **pass** |
| S3 usage readable | **partial**: state, window and reset readable; no percentage |
| S4 scheduled task | **pass**: delivered at 09:12:49Z, executed at 09:16:09Z when the session went idle |
| S5 push notification | tool replied `Mobile push requested.`; delivery **unconfirmed** |
| Ponytail | **unavailable**: 0 of 6 commands; review done by hand |

## 1. Environment

| | value | evidence |
|---|---|---|
| cloud? | **yes** | `CLAUDE_CODE_REMOTE=true`, `CLAUDE_CODE_REMOTE_ENVIRONMENT_TYPE=cloud_default`, a `CLAUDE_CODE_CONTAINER_ID`, hostname `vm`, egress through the agent proxy in `/root/.ccr/`; `get_session` returns `environment_kind: anthropic_cloud`, `origin: desktop_app` (launched from the desktop app, running in the cloud) |
| Python | 3.11.15 | `python --version`; `/usr/bin/python3.11` |
| Node / npm | v22.22.0 / 10.9.4 | plan §1 says Node 20; `check.py frontend` passes on 22, as it did locally |
| lead model | `claude-opus-5-5`, effort `high` | `get_session`: `session_context.model`, `effort_level`, `last_served_model` |
| permission mode | `auto` | `get_session` |
| fresh state | yes | no `.venv*`, no `graphify-out/`, no `graft/`, only `*.sample` in `.git/hooks/` before bootstrap |

## 2. `python scripts/bootstrap.py` from fresh

**Succeeds**: exit 0, **148 s** wall (09:06:08 → 09:08:36Z). B2 from Phase 0 does not occur here:
with no `.venv`, the script creates one from the 3.11 interpreter that launched it.

- Installs the CI extras (`dev,perf,splink,dspy,schema_matching,dq,lineage,swarm`). The B1 pins
  hold: **splink 4.0.17**, **sqlalchemy 2.1.3 + greenlet 3.5.6** (from the `[asyncio]` extra),
  ruff 0.16.10, mypy 2.3.1, duckdb 1.5.6.
- Frontend: `npm install` (68 packages, 3 s; npm reports 6 audit warnings, not acted on), then
  `vite build` → `src/ui/frontend/dist/` (1.2 s).
- `.env` created from `.env.example`. `pre-commit` and `post-commit` hooks installed.
- **Graph step: reports success, builds nothing.** It prints "Code graphs ready";
  `.tmp/refresh_graphs.log` says `skip graphify: not on PATH`, `skip graft: not on PATH`,
  `skip codebase-memory-mcp: not on PATH`, then `all graphs refreshed`. This is the "app's own
  success message" trap CLAUDE.md warns about, in the bootstrap itself. Neither `bootstrap.py`
  nor the repo says how to install the three tools.
- A first attempt failed with exit 127 before running anything, because the image has no
  `/usr/bin/time`. It was re-run with shell timing. That was a harness mistake, not a bootstrap
  fault.

**Network allowlist.** The environment's default policy was enough, with no change:
`pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org` are on the proxy's direct
(`noProxy`) list; `github.com` (clone, push) goes through the agent proxy. Not measured: whatever
the graph tools need. `codebase-memory-mcp` says it "installs a verified native runtime", which
probably means a download from GitHub releases. graft's source is unknown (§3).

## 3. Graphs and the three graph tools — **fail**

| | state |
|---|---|
| `graphify-out/graph.json` | **absent** |
| `graft/` | **absent** |
| `graphify` / `graft` / `codebase-memory-mcp` on PATH | **none** (not in the image, not in `.venv`) |
| codebase-memory-mcp as an MCP server in this session | **absent**: no `search_graph` / `check_index_coverage` tools; no `.mcp.json` in the repo |

So `graphify query`, `graft ask` and `check_index_coverage` could not be run.

**What I tried.** PyPI has `graphifyy` 0.9.67 (the exact version Phase 0 used locally; homepage
`github.com/Graphify-Labs/graphify`) and `codebase-memory-mcp` 0.11.0
(`github.com/DeusData/codebase-memory-mcp`). I checked their metadata and tried to install them
into the gitignored `.venv`. **The auto-mode permission classifier denied it ("Untrusted Code
Integration").** Recorded, not routed around. The downloaded wheels were deleted.

**graft** was not attempted. `graft` on PyPI (0.0.1) and on npm (GraftJS, "Full-Stack JavaScript
Through Microservices") are unrelated projects, not the 0.19.0 CLI with `ask`/`skeleton` used
locally. The repo never names graft's source.

## 4. `python scripts/check.py all` — **green**

Run as `TMPDIR=$PWD/.tmp .venv/bin/python scripts/check.py all`, exit 0, 156 s wall.

| stage | result | wall (cloud) | wall (local, Phase 0) |
|---|---|---|---|
| lint | ok | 0.1 s | 0.1 s |
| format | ok | 0.1 s | 0.0 s |
| arch | ok | 0.8 s | 0.4 s |
| docs | ok | 0.3 s | 0.2 s |
| portability | ok | 0.0 s | 0.0 s |
| guards | ok | 2.7 s | 0.7 s |
| types | ok | 64.0 s | 2.6 s (warm mypy cache; this was cold) |
| frontend | ok | 7.9 s | 4.0 s |
| test | ok | 75.0 s | 74.7 s |
| eval | ok | 1.5 s | 0.8 s |
| prompts | ok | 2.8 s | 1.4 s |
| **total** | **11/11** | **156 s** | 85 s |

**Independent check:** GitHub Actions run
[37190719097](https://github.com/bayern4teams/merge-engine/actions/runs/37190719097) is
`conclusion: success` on head `6826c91e`, the same commit checked out here. Cloud and CI agree,
so B1 is closed: CI can act as the independent check again.

## 5. S1 — worker model and effort

One `part1-worker`, spawned with `model: "sonnet"` passed explicitly, wrote
`.tmp/cloud_recheck/s1/worker.txt`:

```
model=Sonnet 5.5 (claude-sonnet-5-5)
effort=10
effort_source=reasoning_effort tag in my context (value 10); $CLAUDE_EFFORT env var separately reads "high"
env_CLAUDE_EFFORT=high
written_at=2026-10-04T09:06:37.783Z
```

- **Model: honoured.**
- **Effort: not high.** The frontmatter says `effort: high`, but the only effort signal inside the
  worker's own context is a `reasoning_effort` of 10. `$CLAUDE_EFFORT=high` is not evidence: it is
  the lead's environment, inherited by the worker's shell. This matches Phase 0 (one worker
  reported "low"). Keep §3.1's stance: worker effort is unverified, and judgement-heavy work stays
  with the lead.

## 6. S2 — two parallel workers, then a `SendMessage` correction — **pass**

- Workers A and B were spawned in one message, on disjoint files `s2/a.txt` and `s2/b.txt`.
  B wrote at 09:06:55.374Z, inside A's 18 s run, and A wrote at 09:07:08.336Z. The runs overlapped.
- A's brief contained a neutral fact it was told not to write anywhere: the reference river is
  the Danube. After A finished, a `SendMessage` told it to correct line 1 and append the river
  *from its brief*, without searching. Result:

  ```
  worker=A-corrected
  written_at=2026-10-04T09:07:08.336Z
  river=Danube
  corrected_at=2026-10-04T09:09:56.573Z
  ```

  `grep -ri danube` over the repo and scratchpad finds only `a.txt`, so the resumed agent kept its
  context. No secret-style fact was used, so nothing was flagged this time.

## 7. S3 — session usage — **partial**

There is no usage tool in this cloud session. The local `get_usage` (`mcp__ccd_session_mgmt__*`)
is a desktop-app tool and does not exist here. What *is* readable, through
`mcp__claude-code-remote__get_session` (no arguments):

| field | value at 09:06Z |
|---|---|
| `rate_limit_info.rateLimitType` | `five_hour` |
| `rate_limit_info.status` | `allowed` |
| `rate_limit_info.resetsAt` | `1791115200` = **2026-10-04 12:00Z**, the same reset Phase 0 saw locally (same account window) |
| `rate_limit_info.isUsingOverage` | `false` |
| `context_usage` | `max_tokens 1000000`, `used_tokens 0` (not populated mid-turn) |

There is **no percentage and no weekly figure**. The lead can tell "allowed" from "limited" and
when the window resets. It cannot tell 50 % from 89 %, so the §7 90 % pre-emptive stop cannot be
implemented. The lead would only learn of the limit by hitting it.

## 8. S4 — scheduled task — **fires**

- Scheduled at 09:06:09Z with `mcp__claude-code-remote__send_later` (a self-bound, one-shot
  Routine, `trig_01BsEpXBrNnCCK11SV3C2ut1`, fire at 09:12Z). **Not denied.** This is the opposite
  of the local Phase 0 result.
- The tool was chosen because it is durable ("delivery survives container restarts"), so it is the
  only one that could resume a stopped session. `CronCreate` was not used: it is session-only by
  its own description, so it cannot test resuming.
- `get_trigger` afterwards: `ended_reason: run_once_fired`, `last_fired_at: 09:12:49Z`,
  `last_run.status: ROUTINE_RUN_STATUS_SUCCEEDED` into this session. That proves delivery, not
  execution. The session was mid-turn, so the message was queued until the turn ended.
- **Execution: done.** The queued turn ran as soon as the lead's turn ended. It appended
  `S4 fired at 2026-10-04T09:16:09Z` to `.tmp/cloud_recheck/s4/fired.txt`, then the first 5 lines of
  `HANDOFF.md` read with `git show origin/feat/part1-fixes:…` (title line
  `# Part 1 handoff — the only memory that survives a session`). That is 3 min 20 s after delivery,
  because the turn waited for the lead to go idle. A Routine prompt can wake an idle cloud session
  and do repo work.

**Not tested:** whether a session that has been *stopped* (limit hit, container reclaimed) is
woken by such a trigger. Here the session was alive and attended.

## 9. S5 — push notification — **unconfirmed**

`PushNotification` at about 09:06Z, message "Part 1 cloud re-check S5: test push notification…".
Tool reply: **`Mobile push requested.`** Locally it was "Mobile push not sent (Remote Control
inactive)". So the cloud at least hands the push off. Whether it reached your phone is something
only you can confirm. The verdict notification at the end of this re-check is a second sample.

## 10. Ponytail — **unavailable**

- **0 of 6 commands are usable.** None of `ponytail`, `-review`, `-audit`, `-debt`, `-gain`,
  `-help` is in this session's skill list. No Ponytail files exist anywhere on the container
  (`find / -ipath '*ponytail*'`). The account's synced plugin directory holds only `agents/` and
  `launch.json`.
- The repo does not enable it either. There is no `.claude/settings.json`; `.claude/` holds only
  the two agent files and `launch.json`. Phase 0's "plugin 4.9.0, active via the SessionStart hook"
  came from the *local* user-level install, which a cloud clone does not get.
- **`/ponytail-review` could not be invoked.** By hand, against its rules (smallest diff,
  nothing speculative, no new code): this re-check's diff is this report plus a 5-line
  `HANDOFF.md` update, both requested deliverables. It adds no code, scripts, config or
  dependencies. Scratch output stays in gitignored `.tmp/`. Nothing to cut. This is **not** a
  plugin run.

## What would make the cloud viable (for you to decide; none of it done here)

1. **Usage (S3):** needs a usage-percentage API inside cloud sessions. Not fixable from the repo.
   Without it, a cloud run can only be "one phase per launch, sized to fit a window", which is
   §7's fallback, and §11 sends that case to a local session.
2. **Graph tools:** add their install to the cloud environment's setup script, or add it to
   `bootstrap.py` with pinned versions (`graphifyy==0.9.67`, `codebase-memory-mcp==0.11.0`, graft
   from wherever the local copy came from), and allow that install in the permission settings.
   Make `refresh_graphs.py` fail, or at least say "0 of 3 built", when every tool is skipped
   instead of printing "all graphs refreshed". That last point is a `scripts/` change, so it is a
   request back to `main`, not something this branch should edit unasked.
3. **Ponytail:** enable it for the project in a committed `.claude/settings.json`
   (marketplace + `enabledPlugins`), so cloud clones get it.
4. **Notifications (S5):** confirm whether the 09:06Z test push reached your phone.
