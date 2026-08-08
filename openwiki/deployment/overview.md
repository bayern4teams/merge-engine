---
type: operations
title: Deployment & Operations
description: >
  Deployment overview for merge-engine: Docker Compose setup, entrypoints
  (src/main.py), CLI modes, process management, and self-hosted Langfuse
  observability stack.
tags: [deployment, docker, entrypoints, operations]
---

# Deployment & Operations

## Purpose

Merge-engine runs as a single process that can serve an MCP server, a web
UI (FastAPI + React SPA), or both simultaneously. It is deployed via
Docker Compose with a self-hosted Langfuse observability stack.

## Entrypoint

`src/main.py` is the single entry point, registered in `pyproject.toml`:

```toml
[project.scripts]
merge-engine = "src.main:main"
```

## CLI Arguments

| Argument | Choices | Default | Description |
|----------|---------|---------|-------------|
| `--mode` | `mcp`, `gradio`, `both` | `both` | Which service to start |
| `--config` | path | `config/config.yaml` | Config file path |
| `--port` | int | `7860` | Web UI port |
| `--mcp-port` | int | `8000` | MCP server port |
| `--log-level` | DEBUG, INFO, WARNING, ERROR | DEBUG | Logging level |
| `--log-file` | path | `logs/debug.log` | Log file path |

## Usage

```bash
python -m src.main --mode gradio      # Start web UI only
python -m src.main --mode mcp         # Start MCP server only
python -m src.main --mode both        # Start MCP + web UI (default)
python -m src.main --port 7860        # Custom web UI port
python -m src.main --mcp-port 8000    # Custom MCP port
python -m src.main --config config/custom.yaml
```

## Docker

### Dockerfile

`Dockerfile` — builds the merge-engine image:
1. Python base image with dependencies from `requirements.txt`
2. Node.js for React SPA build
3. Multi-stage build: build frontend, then run backend

### Compose Files

| File | Purpose |
|------|---------|
| `docker/app-compose.yml` | Main application stack (merge-engine + dependencies) |
| `docker/langfuse-compose.yml` | Self-hosted Langfuse observability stack |

### Langfuse Observability

Langfuse runs as a separate Docker Compose stack:

```yaml
# docker/langfuse-compose.yml
services:
  langfuse:
    image: langfuse/langfuse:3
  langfuse-worker:
    image: langfuse/langfuse-worker:3
```

Environment variables in `docker/langfuse.env.example`:
- `LANGFUSE_HOST`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`

## Process Management

When `--mode both` (default):
1. MCP server starts on port 8000
2. FastAPI serves React SPA on port 7860
3. Both run in the same process

## .env File

`src/main.py` auto-loads `.env` relative to the project root:

```python
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_FILE, override=True)
```

## Logs

| Path | Purpose |
|------|---------|
| `logs/debug.log` | Debug log file |
| `logs/spool/<run_id>.duckdb` | Per-run DuckDB spool |
| `logs/run_reports/<thread_id>.json` | Per-run reconciliation reports |
| `logs/spool/_resolve_cache.duckdb` | Cross-run resolve cache |
| `data/checkpoints.db` | LangGraph SQLite checkpoints |

## Test Coverage

- `tests/integration/test_workflow_manager.py` — workflow lifecycle testing
- `tests/unit/test_api.py` — API endpoint testing
