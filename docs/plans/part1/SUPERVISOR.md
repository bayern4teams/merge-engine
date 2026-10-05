# Supervisor prompt — the hourly routine runs exactly this

**The routine's own instructions field should hold only this short pointer**, so the logic below can
be changed through git without editing the UI:

```
You are the Part 1 supervisor tick. Run: git fetch origin feat/part1-fixes, then read
docs/plans/part1/SUPERVISOR.md with: git show origin/feat/part1-fixes:docs/plans/part1/SUPERVISOR.md
Follow the LAST fenced block in that file exactly. Do nothing else.
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
   starts on a throwaway claude/... branch; all work goes to feat/part1-fixes). Read ONLY docs/plans/part1/HANDOFF.md now: an idle tick must
   stay cheap, so CLAUDE.md and the plan are read in step 3b, after the exit checks.
1b. If HANDOFF.md says "Autonomy: dryrun": write ONE line to .handoff/supervisor_dryrun.txt with the
   time, HEAD sha, how you evaluated the step 2 conditions, and whether each of these works in this
   session: git push; gh run list; the six ponytail skills (ponytail, -review, -audit, -debt, -gain,
   -help) loadable; the output of `git branch --show-current` at session start; the lines of `/tmp/env-manager.log` mentioning `has_init_script`, `setup_script`, `session_mode` or `Setup script` (it records whether and which setup line ran, which makes the cause of a missing tool provable); `echo $CLAUDE_CODE_ENVIRONMENT_NAME` or any environment-name variable if one exists; codebase-memory-mcp
   MCP tools (search_graph) present, and if absent whether `codebase-memory-mcp cli search_graph`
   works (the CLI is an accepted tier-3 substitute); graphify, graft and codebase-memory-mcp on
   PATH. Also record: `cat /tmp/cloud_setup.log` (the environment's setup line writes it; if the file is
   absent the setup line did NOT run at session start), `grep -in 'init\|script\|setup'
   /tmp/env-manager.log | tail -20`, and `ls -la /opt/graphtools /usr/local/bin | head -20`.
   If graphify, graft or codebase-memory-mcp are missing, run `bash scripts/cloud_setup.sh 2>&1 |
   tail -40` ONCE by hand, record its exit code, wall time and the last lines, then re-check PATH
   and `python scripts/refresh_graphs.py` (record exit code and `.tmp/refresh_graphs.log`). That
   install is the only action beyond the one-line file allowed in dryrun. Commit, push, stop. Do
   nothing else (no lock, no code).
2. EXIT IMMEDIATELY, doing nothing else and writing nothing, if any of these hold:
   - your session's rate limit is a hard stop (any status other than "allowed" or
     "allowed_warning"; the warning band is NOT a reason to stop: work, commit per step, and let
     the limit end the session);
   - HANDOFF.md says "Autonomy: off";
   - the Next action says Part 1 is complete or "waiting for the user";
   - HANDOFF.md "Lock" is younger than 75 minutes (another session is alive).
3. Otherwise take the lock: set "Lock: <current ISO time>" in HANDOFF.md, commit, push. If that push
   is rejected, another session got there first: git pull --rebase, re-read "Lock"; if it is now
   held, EXIT as in step 2. Never force-push.
3b. Now read CLAUDE.md and docs/plans/PART1_IMPLEMENTATION_PLAN.md (sections 2-5, 7-9).
4. If graphify or codebase-memory-mcp are not on PATH, run bash scripts/cloud_setup.sh; if
   graphify-out/graph.json is missing, run python scripts/bootstrap.py. If one of those two is still
   missing, that is a [BLOCKED] (step 8), not something to skip. graft is OPTIONAL (tier 2): its
   install can hang, so if it is missing continue without it, note it once in HANDOFF.md, and use
   graphify and codebase-memory-mcp instead. Run any install under `timeout -k 5 150`.
5. Act as the lead exactly as the plan says, from the Next action in HANDOFF.md. Refresh the lock
   timestamp in every commit and push each commit. Commit "WIP P<n>" before any long step.
   Pass model "sonnet" for workers, at most 2 concurrent, lead approval for every item. If any push
   is rejected, git pull --rebase and retry (never force-push). If your context is compacted or you
   lose track, re-read HANDOFF.md and the plan sections before continuing.
6. At a phase end: check.py all green, push, wait for CI on the pushed head to be green
   (gh run list/watch), run /ponytail-review on the phase diff, then advance HANDOFF.md and
   record "[PHASE n DONE]" under Notifications. Continue into the next phase.
7. After Phase 5: write the final report, set Next action to "Part 1 complete - waiting for the
   user", clear the Lock, commit "[PART 1 COMPLETE]", push, and stop. Never merge.
8. If you are blocked (CI red you cannot fix, an item rejected twice by yourself, a missing
   tool), write it under Notifications as "[BLOCKED]", clear the Lock, push, and stop.
   Do not work around a denied permission.
```
