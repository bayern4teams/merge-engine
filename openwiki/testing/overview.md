---
type: testing infrastructure
title: Testing Infrastructure
description: >
  Testing infrastructure for merge-engine: pytest configuration, shared fixtures
  in conftest.py, test database setup, mock data generation, and the overall
  test organization (unit, integration, evaluation).
tags: [testing, pytest, fixtures, conftest]
---

# Testing Infrastructure

## Purpose

Comprehensive test suite with pytest, covering unit tests (58+ files), integration tests (11 files), and evaluation harnesses.

## File

`tests/conftest.py` — 11,620 bytes of shared fixture configuration

## Test Organization

```
tests/
├── conftest.py                    # Shared fixtures
├── fixtures/
│   ├── mock_data_generator.py     # Test data generation (51KB)
│   ├── setup_test_databases.py    # Test DB initialization (32KB)
│   ├── test_config.yaml           # Test-specific config
│   └── generated/                 # Generated test data
├── integration/                   # 11 integration tests
│   ├── test_boeing_full_pipeline.py
│   ├── test_config_resolution.py
│   ├── test_edge_cases.py
│   ├── test_extract_data.py
│   ├── test_field_mapping.py
│   ├── test_full_pipeline_manual.py
│   ├── test_graph_workflow.py
│   ├── test_human_in_the_loop.py
│   ├── test_load_data.py
│   ├── test_resolve_entities.py
│   └── test_workflow_manager.py
├── eval/                          # Evaluation harness
│   ├── golden.py                  # Golden dataset management
│   └── score.py                   # Pipeline evaluation scoring
├── prompts/
│   ├── harness.py                 # Prompt evaluation harness (15KB)
│   └── golden/                    # Golden prompt test data
└── unit/                          # 58+ unit test files
    ├── test_analyze_dependencies_node.py
    ├── test_postgres_connector.py
    ├── test_llm_client.py
    └── ...
```

## Shared Fixtures (conftest.py)

Key fixtures available to all tests:

| Fixture | Purpose |
|---------|---------|
| `anyio_backend` | Async test backend |
| `test_config` | Test-specific configuration |
| `setup_test_databases()` | Initialize test databases before tests |
| `mock_data_generator()` | Generate test data |

## Test Configuration

Test-specific config in `tests/fixtures/test_config.yaml`:

```yaml
destination:
  type: postgresql
  host: localhost
  port: 5432
  database: test_merge_engine
llm:
  provider: openai
  model: gpt-4o
  api_key: test_key
```

## Running Tests

```bash
# All tests
pytest

# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# With coverage
pytest --cov=src --cov-report=term-missing

# Parallel execution
pytest -n auto
```

## Test Dependencies

From `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
    "pytest-xdist>=3.5.0",
]
```

## Test Coverage

- 58+ unit test files
- 11 integration test files
- 2 evaluation harness files
- 1 prompt harness file
- Shared fixtures in `conftest.py` and `tests/fixtures/`
