---
type: ui component
title: Recent Connections
description: >
  Recent-connection persistence for the merge-engine UI: saves connection
  history to config/recent_connections.json with de-duplication on
  (host, database, type) and max_size=5 trimming.
tags: [ui, connections, persistence, history]
---

# Recent Connections

## Purpose

Persists the user's recent database connections for quick reconnection.
Used by the UI's connection form to populate recent connection history.

## File

`src/ui/connections.py`

## Key Functions

| Function | Purpose |
|----------|---------|
| `load_persistent_history()` | Read `config/recent_connections.json`, return `{"recent_origins": [], "recent_destinations": []}` |
| `save_persistent_history(history)` | Persist history to JSON file (best-effort) |
| `add_to_recent(new_item, max_size=5, kind="origin")` | Append item, dedup on (host, database, type), trim to max_size, persist |
| `conn_label(item)` | Human-readable label with type icon (🐘 for postgresql, ⚡ for clickhouse, 🍃 for mongodb) |

### add_to_recent Behavior

```python
def add_to_recent(new_item, max_size=5, kind="origin"):
    """Append an item to the recent list for `kind`, de-duplicating on
    (host, database, type), and persist. Returns the trimmed list."""
    history = load_persistent_history()
    existing = history.get(key, []) or []

    # Remove existing items with same (host, database, type)
    cleaned = [
        item
        for item in existing
        if not (
            item["connection"]["host"] == new_host
            and item["connection"]["database"] == new_db
            and item.get("type") == new_type
        )
    ]

    cleaned.append(new_item)
    history[key] = cleaned[-max_size:]  # Trim to max_size
    save_persistent_history(history)
    return trimmed
```

## Storage

- **Path**: `config/recent_connections.json`
- **Overridable**: `MERGE_ENGINE_HISTORY_PATH` environment variable for test isolation
- **Format**: JSON array with de-duplication on (host, database, type)

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/connections/recent` | GET | Returns recent origins/destinations with labels |
| `/api/connections/recent` | POST | Adds an item to recent list |

## Test Coverage

- `tests/unit/test_api.py` — tests the connection endpoints
