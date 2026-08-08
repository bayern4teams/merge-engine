---
type: docker config
title: Docker Configuration
description: >
  Docker and Docker Compose configuration for merge-engine: multi-stage build,
  app compose stack, and self-hosted Langfuse observability stack.
tags: [docker, compose, deployment, langfuse, observability]
---

# Docker Configuration

## Purpose

Docker Compose files for running merge-engine and its observability stack.

## File

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage container build |
| `docker/app-compose.yml` | Application compose stack |
| `docker/langfuse-compose.yml` | Langfuse observability stack |
| `docker/langfuse.env.example` | Langfuse env var template |

## Dockerfile

Multi-stage build:

### Stage 1: Frontend Build

1. Node.js base image
2. Install npm dependencies from `src/ui/frontend/package.json`
3. Run `npm run build` to produce `dist/`

### Stage 2: Backend Runtime

1. Python base image
2. Copy `requirements.txt` and install dependencies
3. Copy application source code
4. Copy built frontend from Stage 1
5. Set entrypoint to `python -m src.main`

## Compose: Application

`docker/app-compose.yml` — runs merge-engine with dependencies:
- merge-engine service
- Environment from `.env`
- Ports: 7860 (UI), 8000 (MCP)

## Compose: Langfuse

`docker/langfuse-compose.yml` — runs self-hosted Langfuse:
- `langfuse/langfuse:3` — web server
- `langfuse/langfuse-worker:3` — background worker

Environment variables from `docker/langfuse.env.example`:
- `LANGFUSE_HOST` — server URL
- `LANGFUSE_PUBLIC_KEY` — API key
- `LANGFUSE_SECRET_KEY` — API secret

## Testing

- `tests/integration/test_workflow_manager.py` — workflow lifecycle testing
- `tests/unit/test_api.py` — API endpoint testing
