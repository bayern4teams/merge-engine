---
type: ui architecture
title: User Interface
description: >
  UI architecture for merge-engine: React SPA built with Vite and TypeScript,
  served by FastAPI static files, with an API client, state management, and
  connection testing helpers.
tags: [ui, react, vite, fastapi, frontend]
---

# User Interface

## Purpose

The UI provides a web interface for managing merge workflows. It is a React
single-page application built with Vite, served by the FastAPI backend, and
communicates with the backend via REST API calls.

## Architecture

```
User Browser
    │
    ▼
┌─────────────────────┐
│  React SPA (Vite)   │
│  src/api.ts         │  API client layer
│  src/store.ts       │  State management
│  src/styles.css     │  Styling
└─────────────────────┘
    │  HTTP requests
    ▼
┌─────────────────────┐
│  FastAPI Backend    │  Serves SPA + API endpoints
│  Static files       │  From src/ui/frontend/dist/
│  /api/*             │  JSON API
└─────────────────────┘
```

## File

`src/ui/` — UI support code
`src/ui/frontend/` — React SPA source and build

## React SPA

### Source Files

| File | Purpose |
|------|---------|
| `src/ui/frontend/index.html` | App shell |
| `src/ui/frontend/src/api.ts` | API client — wraps all FastAPI endpoints |
| `src/ui/frontend/src/store.ts` | State management |
| `src/ui/frontend/src/styles.css` | Styles |
| `src/ui/frontend/vite.config.ts` | Vite build configuration |
| `src/ui/frontend/tsconfig.json` | TypeScript configuration |
| `src/ui/frontend/package.json` | Dependencies and build scripts |

### Build Output

The built SPA lives at `src/ui/frontend/dist/`. FastAPI serves it via
`StaticFiles`:

```python
_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "ui" / "frontend" / "dist"
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST)), name="static")
```

## UI Support Code

### connections.py

Recent-connection history persistence (see `ui/recent_connections.md`).

### components/db_discovery.py

Connection testing and schema discovery helpers for the UI:

- `test_and_discover_schemas()` — test DB connection and discover schema
- `sample_table_rows()` — get sample rows from a table

## API Endpoints Used by the UI

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/connections/recent` | GET | Load recent connections |
| `/api/connections/recent` | POST | Save a new connection |
| `/api/workflow/*` | Various | Workflow management |
| `/api/config/*` | Various | Configuration management |
| `/api/profiles/*` | Various | Profile management |

## Test Coverage

- `tests/unit/test_api.py` — API endpoint tests (includes UI-facing endpoints)
