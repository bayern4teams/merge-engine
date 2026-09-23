# AGENTS.md

See [CLAUDE.md](CLAUDE.md) — same content, kept as a separate file so the
harness stays tool-agnostic (this repo's map should not disappear if the
agent changes). Do not fork the content between the two files; edit
`CLAUDE.md` and keep this one a pointer.

## Code lookup order (the one rule worth repeating here)

Before reading source, go down this ladder and **stop at the first tier that
answers**: `graphify` -> `graft` -> `codebase-memory-mcp` -> raw source.
Never the LLM/`--deep` paths of any of them.

Full commands and the reasoning are under "Code lookup order" in
[CLAUDE.md](CLAUDE.md) -- this is the only thing restated here, because an
agent that skips it reads the whole repo the slow way.

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
