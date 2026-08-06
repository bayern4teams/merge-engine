---
name: drive-app
description: Drive a subject end-to-end through all HITL gates via the REST API and capture diagnostic evidence.
---

# Drive App Skill

Drives the merge-engine application end-to-end for a given company subject via REST API endpoints, automatically responding to HITL approval/skip gates and evaluating budgets.

## Invocation Command
```bash
python scripts/diagnostics/drive_app.py "<Company>" [--assert-budgets]
```

## Description
Runs an end-to-end integration workflow for subjects like "Boeing", "Airbus", or "Lockheed", approving valid gates and capturing full state logs and timing evidence.
