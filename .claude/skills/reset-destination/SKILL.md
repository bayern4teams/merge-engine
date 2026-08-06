---
name: reset-destination
description: Reset a destination PostgreSQL schema to a true blank slate by truncating all tables and helper tables.
---

# Reset Destination Skill

Resets a destination PostgreSQL schema (`global_warehouse.public` by default) to a blank slate, truncating all data tables and helper tables, verifying 0 rows remaining.

## Invocation Command
```bash
python scripts/diagnostics/reset_destination.py [--db DB] [--schema SCHEMA]
```

## Description
Use this skill before starting a fresh diagnostic or test run to ensure no stale rows or watermarks remain from prior executions.
