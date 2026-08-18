---
type: operations
title: Operational Scripts
description: >
  Operational scripts for merge-engine: check.py (comprehensive quality gate),
  bootstrap.py (project setup), lint scripts, sample database import, and
  diagnostic tools.
tags: [operations, scripts, quality-gates, diagnostics]
---

# Operational Scripts

## Purpose

Python scripts for development, quality assurance, and operations.

## File

`scripts/` directory

## Key Scripts

| Script | Purpose |
|--------|---------|
| `check.py` | **Comprehensive quality gate**: lint, format, types, arch, docs, portability, test, eval, prompts, frontend |
| `bootstrap.py` | One-command setup: venv, pip install, npm build, .env copy |
| `lint_arch.py` | Architecture lint: layering, no mutable module state, no compile() at module scope, ENTER/EXIT logging |
| `lint_docs.py` | Documentation lint: broken links, stale file citations |
| `lint_portability.py` | Portability checks: no hardcoded conda/na180 paths, from-scratch files present |
| `import_sample_databases.py` | Import sample databases into ClickHouse |
| `generate_requirements.py` | Requirements file generation |
| `update_arch_lint_baseline.py` | Regenerate architecture lint baseline |
| `update_mypy_baseline.py` | Regenerate mypy baseline |

## Diagnostics

`scripts/diagnostics/`:

| Script | Purpose |
|--------|---------|
| `compare_to_golden.py` | Compare results against golden test data |
| `drive_app.py` | Run the app (supports `--assert-budgets` for timing checks) |
| `profile_resolve.py` | Profile resolve_entities (uses `MERGE_ENGINE_DISABLE_RESOLVE_CACHE`) |
| `reset_destination.py` | Reset destination database |
| `restore_pristine.py` | Restore pristine state |
| `verify_langfuse_trace.py` | Verify Langfuse tracing completeness |

## Makefile

`Makefile` — common development commands:

| Target | Description |
|--------|-------------|
| `make check` | Run comprehensive quality checks |
| `make test` | Run test suite |
| `make lint` | Run ruff linting |
| `make format` | Run ruff formatting |
| `make mypy` | Run mypy type checking |

## Test Coverage

- `tests/unit/test_api.py` — includes API endpoint testing
- `scripts/diagnostics/compare_to_golden.py` — golden data comparison
