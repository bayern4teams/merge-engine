---
name: restore-pristine
description: Restore a destination PostgreSQL schema to pristine schema-only state, dropping extra added columns and helper tables.
---

# Restore Pristine Skill

Restores a destination PostgreSQL schema to pristine state by dropping any non-original schema columns (e.g. from failed `ADD COLUMN` migrations), dropping `merge_engine_*` helper tables, truncating all tables, and verifying table counts.

## Invocation Command
```bash
python scripts/diagnostics/restore_pristine.py [--db DB] [--schema SCHEMA]
```

## Description
Use this skill after a failed or partial workflow run that mutated schema structures or left helper tables behind.
