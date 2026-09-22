# ADR 0003: Server-side CI could not be configured — `git.n4t.com` unreachable

**Status:** documented gap, not resolved. Revisit when the *git protocol*
(not just port 443) is reachable — see the 2026-08-05 re-check below, which
found a real change but not the one that matters here.

## Context

Phase 0 Sec 0.3 calls for both local (pre-commit) and server-side CI. The
local half is done: `.pre-commit-config.yaml` runs `ruff`, `ruff-format`,
the mypy ratchet, `lint_arch.py`, `lint_docs.py`, and five local hooks
(`no-blanket-data-ignore`, `no-sample-databases-commit`,
`no-str-format-on-prompts`, `no-new-bare-except-pass` — all four verified
live via `pre-commit run --all-files`, which also caught and fixed a real
crash in the except-pass hook's git-diff comparison under Windows' default
console codepage).

## Finding

`git.n4t.com` is unreachable over both protocols, checked live:

```
curl -sI --max-time 8 https://git.n4t.com          -> curl exit 28 (timeout)
timeout 8 bash -c "cat < /dev/tcp/git.n4t.com/22"   -> exit 124 (timeout)
```

This matches the same finding recorded during planning. `github.com` is
reachable from this environment (verified: `curl -sI https://github.com` ->
`200 OK`), which is how `.pre-commit-config.yaml`'s two hosted hook repos
(`astral-sh/ruff-pre-commit`, `pre-commit/pre-commit-hooks`) could be
installed and verified live — the reachability gap is specific to
`git.n4t.com`, not outbound network access in general.

Without reachability there is no way to determine whether it runs Gitea,
Forgejo, or GitLab (the header/login check the plan specifies both need the
server to answer), and no way to register a runner or install a
`pre-receive` hook.

## Decision

Ship the local half (`.pre-commit-config.yaml`, `scripts/check.py all`) now
— it does not depend on the server at all and is fully live-verified.
Leave server-side CI undone rather than guessing at a Gitea/GitLab config
that can't be tested against the real server. `python scripts/check.py all`
is the single command a human or CI would run either way; whichever CI
config eventually gets added should invoke exactly that command and nothing
else, per R1 in the harness-foundation plan.

## Revisit trigger

When `git.n4t.com` becomes reachable: run `curl -sI https://git.n4t.com`,
inspect for a `Gitea`/`Forgejo` header or a GitLab `X-Gitlab-*` header (or
log in and look), then add `.gitea/workflows/ci.yml` or `.gitlab-ci.yml`
running `python scripts/check.py all`. If no runner can be registered even
once the server is reachable, fall back to a server-side `pre-receive` hook
that rejects a push whose HEAD commit lacks a passing-check trailer.

## 2026-08-05 re-check (Part 1.6.D) — real change, wrong protocol

Re-ran the exact revisit-trigger commands. Port 443 now answers, which it
did not before:

```
curl -sI --max-time 8 https://git.n4t.com
  -> HTTP/1.1 404 Not Found, Server: Kestrel
```

`Kestrel` is ASP.NET Core's web server — not a Gitea/Forgejo header (which
would say `Server: nginx`/no header plus a recognisable HTML shell) and not
a GitLab `X-Gitlab-*` header. `/api/v1/version` (Gitea/Forgejo) and
`/api/v4/version` (GitLab) both returned empty bodies, and the repo's own
page (`/bayern/mergeengine`) returned nothing — none of the three signals
ADR 0003 originally asked for are present. Whatever now answers on 443 does
not look like this project's git host; it may be an unrelated service that
started sharing the hostname, a placeholder, or a proxy default backend.

**The protocol that actually matters is still unreachable.** `origin` is an
SSH remote (`git@git.n4t.com:bayern/mergeengine.git`), not HTTPS:

```
timeout 8 bash -c "cat < /dev/tcp/git.n4t.com/22"   -> exit 124 (timeout)
git ls-remote origin                                -> fatal: Could not read
                                                        from remote repository
```

So push/pull/clone against this remote is exactly as broken as ADR 0003
originally found — the 443 response is a red herring for this decision, not
evidence the server-CI gap has closed. Server-side CI stays undone. Re-check
`git ls-remote origin` specifically (not just `curl`) on the next revisit —
it is the one command that can't be fooled by an unrelated service on 443.
