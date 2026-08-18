---
type: utility
title: Logging Configuration
description: >
  Structured logging setup for merge-engine using structlog with JSON
  formatting, Windows UTF-8 handling, and per-module logger names.
tags: [utils, logging, structlog, structured-logging]
---

# Logging Configuration

## Purpose

Provides structured JSON logging for merge-engine using `structlog`.

## File

`src/utils/logging_config.py`

## Key Functions

| Function | Purpose |
|----------|---------|
| `setup_logging(level, log_file)` | Configure structlog with JSON formatter |

## Logger Names

Loggers are named per module, following the pattern:
`merge-engine.{module}.{submodule}`

Examples:
- `merge-engine.orchestrator.graph`
- `merge-engine.connectors.postgres`
- `merge-engine.mcp_server`

## Windows UTF-8

Handles Windows console encoding for proper UTF-8 output in terminal logs.

## Test Coverage

- `tests/unit/test_logging_config.py` — logging setup and JSON formatting
