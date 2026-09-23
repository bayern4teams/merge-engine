# AGENTS.md

See [CLAUDE.md](CLAUDE.md) — same content, kept as a separate file so the
harness stays tool-agnostic (this repo's map should not disappear if the
agent changes). Do not fork the content between the two files; edit
`CLAUDE.md` and keep this one a pointer.

## Do this first, in a fresh clone

```bash
python scripts/bootstrap.py    # venv, deps, frontend, git hooks, graphs
```

`git clone` gives you none of it: `.git/hooks/` is never cloned, and there is
no post-clone hook. This repo was found with an empty `.git/hooks/`, so every
configured hook had never run. Bootstrap installs them and builds the graphs.

Then, every session, before reading code:

```bash
ls graphify-out/graph.json graft/ >/dev/null 2>&1 || python scripts/refresh_graphs.py
```

Missing graphs are the normal state of a fresh clone, not a bug — build them
(~8s, no LLM) instead of falling back to grepping the whole repo.

## The two rules worth repeating here

**1. graphify, graft and codebase-memory-mcp are the primary tools for
understanding this code. Reading source is the fallback, not the default.**
Go down the ladder and **stop at the first tier that answers**: `graphify` ->
`graft` -> `codebase-memory-mcp` -> raw source. Never the LLM/`--deep` paths
of any of them. If a graph is missing or stale, run
`python scripts/refresh_graphs.py` rather than silently dropping to source.

**2. Ponytail is active by default, and all six commands are available:**
`/ponytail` (intensity), `/ponytail-review` (a diff, before it lands),
`/ponytail-audit` (dead code and unused deps), `/ponytail-debt` (what was
deferred, and what triggers paying it), `/ponytail-gain` (what a
simplification actually bought), `/ponytail-help` (which of these applies).

Full commands and reasoning are in [CLAUDE.md](CLAUDE.md) -- these are the
only things restated here, because an agent that skips them reads the whole
repo the slow way and then over-builds what it finds.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has **no** committed `openwiki/` tree -- do not go looking for one. The scheduled workflow regenerates it only when `OPENROUTER_API_KEY` is configured; with no such secret it skips cleanly (it used to fail every night instead). Source code and tests are the authority regardless.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
