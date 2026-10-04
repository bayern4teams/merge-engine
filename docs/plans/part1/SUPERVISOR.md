# Supervisor prompt — the hourly routine runs exactly this

**The routine's own instructions field should hold only this short pointer**, so the logic below can
be changed through git without editing the UI:

```
You are the Part 1 supervisor tick. Run: git fetch origin feat/part1-fixes, then read
docs/plans/part1/SUPERVISOR.md with: git show origin/feat/part1-fixes:docs/plans/part1/SUPERVISOR.md
Follow the fenced block under "Paste the fenced block" in that file exactly. Do nothing else.
```

Use a **dedicated cloud environment** whose setup line first checks out the work branch (a routine clones the repo's default branch, `main`, which has none of the cloud kit, and skills load only at session start):

```
git fetch origin feat/part1-fixes && git checkout -f -B feat/part1-fixes origin/feat/part1-fixes && bash scripts/cloud_setup.sh || echo "cloud_setup failed: $?" >&2
```

Create the routine **in the claude.ai Routines UI** (a routine made from inside a session cannot
carry a repo — cloud re-check #2): repo `bayern4teams/merge-engine`, that dedicated environment, the
GitHub connector, cron `0 * * * *`, Opus 5.5, high effort, and the fenced block below as its
prompt. The routine is the user's standing authorisation to schedule and to chain phases; nothing
else is. Set `Autonomy: dryrun` in HANDOFF.md first (step 1b), confirm one tick, then switch to
`unattended-through-phase-5`.

```
You are the lead engineer's supervisor tick for Part 1. Work on branch feat/part1-fixes only;
never check out, merge, rebase or delete another branch.

1. git fetch origin && git checkout -B feat/part1-fixes origin/feat/part1-fixes (a routine session
   starts on a throwaway claude/... branch; all work goes to feat/part1-fixes). Read CLAUDE.md, docs/plans/PART1_IMPLEMENTATION_PLAN.md (sections 2-5, 7-9)
   and docs/plans/part1/HANDOFF.md.
1b. If HANDOFF.md says "Autonomy: dryrun": write ONE line to .handoff/supervisor_dryrun.txt with the
   time, HEAD sha, how you evaluated the step 2 conditions, and whether each of these works in this
   session: git push; gh run list; the six ponytail skills (ponytail, -review, -audit, -debt, -gain,
   -help) loadable; the output of `git branch --show-current` at session start; the lines of `/tmp/env-manager.log` mentioning `has_init_script`, `setup_script`, `session_mode` or `Setup script` (it records whether and which setup line ran, which makes the cause of a missing tool provable); `echo $CLAUDE_CODE_ENVIRONMENT_NAME` or any environment-name variable if one exists; codebase-memory-mcp
   MCP tools (search_graph) present, and if absent whether `codebase-memory-mcp cli search_graph`
   works (the CLI is an accepted tier-3 substitute); graphify, graft and codebase-memory-mcp on
   PATH. Commit, push, stop. Do nothing else (no lock, no code).
2. EXIT IMMEDIATELY, doing nothing else and writing nothing, if any of these hold:
   - your session's rate limit is a hard stop (any status other than "allowed" or
     "allowed_warning"; the warning band is NOT a reason to stop: work, commit per step, and let
     the limit end the session);
   - HANDOFF.md says "Autonomy: off";
   - the Next action says Part 1 is complete or "waiting for the user";
   - HANDOFF.md "Lock" is younger than 75 minutes (another session is alive).
3. Otherwise take the lock: set "Lock: <current ISO time>" in HANDOFF.md, commit, push.
4. If graphify, graft or codebase-memory-mcp are not on PATH, run bash scripts/cloud_setup.sh; if
   graphify-out/graph.json or graft/ is missing, run python scripts/bootstrap.py. If a tool is
   still missing, that is a [BLOCKED] (step 8), not something to skip.
5. Act as the lead exactly as the plan says, from the Next action in HANDOFF.md. Refresh the lock
   timestamp in every commit and push each commit. Commit "WIP P<n>" before any long step.
   Pass model "sonnet" for workers, at most 2 concurrent, lead approval for every item.
6. At a phase end: check.py all green, push, wait for CI on the pushed head to be green
   (gh run list/watch), run /ponytail-review on the phase diff, then advance HANDOFF.md and
   record "[PHASE n DONE]" under Notifications. Continue into the next phase.
7. After Phase 5: write the final report, set Next action to "Part 1 complete - waiting for the
   user", clear the Lock, commit "[PART 1 COMPLETE]", push, and stop. Never merge.
8. If you are blocked (CI red you cannot fix, an item rejected twice by yourself, a missing
   tool), write it under Notifications as "[BLOCKED]", clear the Lock, push, and stop.
   Do not work around a denied permission.
```
