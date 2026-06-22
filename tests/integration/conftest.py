"""Shared fixtures for integration tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


# ──────────────────────────── Test Data Fixtures ─────────────────────────────


@pytest.fixture
def test_config_path(tmp_path: Path) -> Path:
    """Create a minimal valid config file for integration tests."""
    config_content = """
origins:
  - id: postgres_main
    type: postgres
    connection:
      host: localhost
      port: 5432
      user: test_user
      password: test_pass
      database: test_db
    priority: 1
  - id: mongo_analytics
    type: mongodb
    connection:
      uri: mongodb://localhost:27017
      database: analytics
    priority: 2

destination:
  type: databricks
  host: databricks.cloud
  http_path: /sql/1.0/warehouses/abc123
  token: test_token
  catalog: main
  schema: merge_engine
  tables:
    company: companies
    person: people
    country: countries

llm:
  provider: openai
  model: gpt-4
  api_key: test_api_key
  temperature: 0.0

blocking_keys:
  company:
    - name
    - email
  person:
    - name
    - email

retry:
  max_attempts: 3
  backoff: exponential
  min_backoff_seconds: 0.1
  max_backoff_seconds: 0.5

extraction:
  default_batch_size: 100
  max_batch_size: 1000
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_content)
    return config_file


@pytest.fixture
def mock_config(test_config_path: Path) -> Any:
    """Load a test config object."""
    from src.utils.config_loader import load_config
    return load_config(str(test_config_path))


@pytest.fixture
def sample_company_records() -> list[dict[str, Any]]:
    """Sample company records from multiple origins."""
    return [
        {
            "name": "Acme Corp",
            "email": "info@acmecorp.com",
            "industry": "Technology",
            "_origin_id": "postgres_main",
            "_origin_table": "companies",
            "_origin_id_row": "101",
        },
        {
            "name": "Acme Corporation",
            "email": "contact@acmecorp.com",
            "industry": "Tech",
            "_origin_id": "mongo_analytics",
            "_origin_table": "companies",
            "_origin_id_row": "abc123",
        },
        {
            "name": "Beta Industries",
            "email": "info@betaind.com",
            "industry": "Manufacturing",
            "_origin_id": "postgres_main",
            "_origin_table": "companies",
            "_origin_id_row": "102",
        },
        {
            "name": "Gamma LLC",
            "email": "hello@gammallc.com",
            "industry": "Retail",
            "_origin_id": "mongo_analytics",
            "_origin_table": "companies",
            "_origin_id_row": "def456",
        },
    ]


@pytest.fixture
def sample_person_records() -> list[dict[str, Any]]:
    """Sample person records from multiple origins."""
    return [
        {
            "name": "John Smith",
            "email": "john@example.com",
            "phone": "555-0101",
            "_origin_id": "postgres_main",
            "_origin_table": "people",
            "_origin_id_row": "201",
        },
        {
            "name": "John A. Smith",
            "email": "j.smith@example.com",
            "phone": "555-0101",
            "_origin_id": "mongo_analytics",
            "_origin_table": "people",
            "_origin_id_row": "xyz789",
        },
        {
            "name": "Jane Doe",
            "email": "jane@doe.com",
            "phone": "555-0202",
            "_origin_id": "postgres_main",
            "_origin_table": "people",
            "_origin_id_row": "202",
        },
    ]


@pytest.fixture
def sample_extracted_data() -> dict[str, list[dict[str, Any]]]:
    """Sample extracted data organized by entity type."""
    return {
        "company": [
            {
                "id": 1,
                "name": "Acme Corp",
                "email": "info@acmecorp.com",
                "country_id": 1,
            },
            {
                "id": 2,
                "name": "Beta Industries",
                "email": "info@betaind.com",
                "country_id": 2,
            },
        ],
        "person": [
            {
                "id": 1,
                "name": "John Smith",
                "email": "john@example.com",
                "company_id": 1,
            },
            {
                "id": 2,
                "name": "Jane Doe",
                "email": "jane@doe.com",
                "company_id": 2,
            },
        ],
        "country": [
            {"id": 1, "name": "United States", "code": "US"},
            {"id": 2, "name": "Canada", "code": "CA"},
        ],
    }


# ──────────────────────────── Mock Fixtures ──────────────────────────────────


@pytest.fixture
def mock_connector_factory() -> Any:
    """Factory that creates mock connectors returning controlled data."""

    def _create_mock_connector(
        tables: dict[str, Any] | None = None,
        extract_rows: list[dict] | None = None,
        extract_total: int = 0,
    ) -> MagicMock:
        """Create a single mock connector instance.

        Parameters
        ----------
        tables : dict, optional
            Mapping of table_name -> schema dict for discover_schema().
        extract_rows : list, optional
            Rows to return on first extract() call.
        extract_total : int
            Total count reported by extract().
        """
        connector = MagicMock()

        # Schema discovery
        if tables is not None:
            connector.discover_schema = AsyncMock(return_value={"tables": tables})

        # Data extraction
        if extract_rows is not None:
            call_count = [0]

            async def mock_extract(*args: Any, **kwargs: Any) -> dict[str, Any]:
                current = call_count[0]
                call_count[0] += 1
                batch_size = kwargs.get("batch_size", 1000)
                cursor = kwargs.get("cursor")

                # Parse offset from cursor
                offset = 0
                if cursor:
                    try:
                        offset = int(cursor)
                    except ValueError:
                        offset = 0

                start_idx = offset
                end_idx = min(offset + batch_size, len(extract_rows))

                batch = extract_rows[start_idx:end_idx]
                has_next = end_idx < len(extract_rows)

                result = {
                    "rows": batch,
                    "column_names": list(batch[0].keys()) if batch else [],
                    "total_count": extract_total or len(extract_rows),
                    "next_cursor": str(end_idx) if has_next else None,
                    "origin_ids": [str(r.get("id", i)) for i, r in enumerate(batch)],
                }
                return result

            connector.extract = AsyncMock(side_effect=mock_extract)

        # Mark imported
        connector.mark_imported = AsyncMock(return_value=True)

        # Close
        connector.close = AsyncMock()

        return connector

    return _create_mock_connector


@pytest.fixture
def mock_llm_responses() -> dict[str, Any]:
    """Deterministic LLM responses for testing."""
    return {
        "request_parsing": {
            "entity_types": ["company", "person"],
            "target_tables": ["companies", "people"],
        },
        "dependency_analysis": {
            "dependencies": {
                "person": ["company"],
            },
        },
        "field_mapping": {
            "mappings": {
                "company": {
                    "name": ("name", 0.98),
                    "email": ("email", 0.95),
                    "industry": ("industry", 0.90),
                },
                "person": {
                    "name": ("name", 0.97),
                    "email": ("email", 0.96),
                    "phone": ("phone_number", 0.88),
                },
            },
        },
        "entity_resolution_score": {
            "confidence": 0.85,
            "reason": "Similar names and matching phone numbers",
        },
        "entity_unification": {
            "unified_record": {
                "name": "John Smith",
                "email": "john@example.com",
                "phone_number": "555-0101",
                "sources": ["postgres_main", "mongo_analytics"],
            },
        },
    }


@pytest_asyncio.fixture
async def mock_llm_client(mock_llm_responses: dict[str, Any]) -> AsyncMock:
    """Mock LLM client with deterministic responses."""
    from src.llm.client import LLMClient

    client = AsyncMock(spec=LLMClient)

    async def _structured_parse(prompt: str, system_prompt: str) -> dict[str, Any]:
        """Map prompts to responses."""
        if "entity" in prompt.lower() or "request" in prompt.lower():
            return mock_llm_responses["request_parsing"]
        if "depend" in prompt.lower():
            return mock_llm_responses["dependency_analysis"]
        if "field" in prompt.lower() or "mapping" in prompt.lower():
            return mock_llm_responses["field_mapping"]
        if "resolve" in prompt.lower() or "score" in prompt.lower():
            return mock_llm_responses["entity_resolution_score"]
        if "unif" in prompt.lower():
            return mock_llm_responses["entity_unification"]
        return {}

    async def _score_pair(record1: dict, record2: dict, entity_type: str) -> dict[str, Any]:
        return mock_llm_responses["entity_resolution_score"]

    async def _unify_records(records: list[dict], entity_type: str) -> dict[str, Any]:
        return mock_llm_responses["entity_unification"]

    client.structured_parse = _structured_parse
    client.score_pair = _score_pair
    client.unify_records = _unify_records
    client.batch_score = AsyncMock(side_effect=lambda pairs, et: [
        mock_llm_responses["entity_resolution_score"] for _ in pairs
    ])
    client.batch_unify = AsyncMock(side_effect=lambda groups, et: [
        mock_llm_responses["entity_unification"] for _ in groups
    ])
    client.is_available = True

    return client


# ──────────────────────────── Config Patching ─────────────────────────────────


@pytest.fixture
def patch_config_loader(test_config_path: Path) -> Any:
    """Patch config loader to use the test config file."""
    with patch("src.utils.config_loader.load_config") as mock_load:
        from src.utils.config_loader import load_config
        mock_load.return_value = load_config(str(test_config_path))
        mock_load.side_effect = None
        yield mock_load


@pytest.fixture
def patch_server_config(test_config_path: Path, mock_config: Any) -> Any:
    """Patch the MCP server's internal config reference.

    Uses a global variable approach to avoid importing server.py at test
    collection time, which triggers module-level code that may fail.
    """
    # Set the global config directly to avoid importing server.py
    import src.mcp_server.server as server_mod
    original = server_mod._config
    server_mod._config = mock_config
    yield mock_config
    server_mod._config = original


# ──────────────────────────── Workflow State Fixtures ─────────────────────────


@pytest.fixture
def initial_workflow_state(
    mock_config: Any,
) -> dict[str, Any]:
    """Create initial workflow state for testing."""
    from src.orchestrator.graph import get_initial_state

    user_request = "Import all companies and their contacts"
    return get_initial_state(user_request, mock_config)


@pytest.fixture
def parsed_state(initial_workflow_state: dict[str, Any]) -> dict[str, Any]:
    """State after parse_request node."""
    state = initial_workflow_state.copy()
    state["target_entities"] = ["company", "person"]
    return state


@pytest.fixture
def discovered_state(parsed_state: dict[str, Any]) -> dict[str, Any]:
    """State after discover_schemas node."""
    state = parsed_state.copy()
    state["schemas"] = {
        "postgres_main": {
            "tables": [
                {
                    "name": "companies",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "name", "type": "VARCHAR"},
                        {"name": "email", "type": "VARCHAR"},
                        {"name": "country_id", "type": "INTEGER"},
                    ],
                    "foreign_keys": [
                        {
                            "column": "country_id",
                            "ref_table": "countries",
                            "ref_column": "id",
                        }
                    ],
                },
                {
                    "name": "people",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "name", "type": "VARCHAR"},
                        {"name": "email", "type": "VARCHAR"},
                        {"name": "phone", "type": "VARCHAR"},
                        {"name": "company_id", "type": "INTEGER"},
                    ],
                    "foreign_keys": [
                        {
                            "column": "company_id",
                            "ref_table": "companies",
                            "ref_column": "id",
                        }
                    ],
                },
                {
                    "name": "countries",
                    "columns": [
                        {"name": "id", "type": "INTEGER"},
                        {"name": "name", "type": "VARCHAR"},
                        {"name": "code", "type": "VARCHAR"},
                    ],
                    "foreign_keys": [],
                },
            ]
        },
        "mongo_analytics": {
            "tables": [
                {
                    "name": "companies",
                    "columns": [
                        {"name": "_id", "type": "STRING"},
                        {"name": "name", "type": "STRING"},
                        {"name": "email", "type": "STRING"},
                        {"name": "industry", "type": "STRING"},
                    ],
                    "foreign_keys": [],
                },
                {
                    "name": "contacts",
                    "columns": [
                        {"name": "_id", "type": "STRING"},
                        {"name": "name", "type": "STRING"},
                        {"name": "email", "type": "STRING"},
                        {"name": "phone", "type": "STRING"},
                        {"name": "company", "type": "STRING"},
                    ],
                    "foreign_keys": [],
                },
            ]
        },
    }
    return state


@pytest.fixture
def dependency_state(discovered_state: dict[str, Any]) -> dict[str, Any]:
    """State after analyze_dependencies node."""
    state = discovered_state.copy()
    state["dependency_tree"] = {
        "person": ["company"],
    }
    state["extraction_order"] = ["company", "country", "person"]
    return state