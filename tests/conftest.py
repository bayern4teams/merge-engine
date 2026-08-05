"""
Pytest configuration and shared fixtures for merge-engine tests.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

# Must be set BEFORE src.ui.connections is imported anywhere: its module-level
# CONFIG_PATH reads this env var. Without it, tests that exercise the recent-
# connections history overwrite the user's real
# config/recent_connections.json with fixture data.
#
# Keyed by PYTEST_XDIST_WORKER (Phase 0 Sec 0.7): under `pytest -n auto`,
# pytest-xdist runs one process per worker ("gw0", "gw1", ...), and every
# worker importing this module at the same fixed path would collide on the
# same file. Serial runs (no xdist) have no such env var and all share
# "main", which is exactly today's single-file behaviour.
_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
os.environ.setdefault(
    "MERGE_ENGINE_HISTORY_PATH",
    os.path.join(tempfile.gettempdir(), f"merge_engine_test_history_{_xdist_worker}.json"),
)

# Part 1.5 D.7: resolve_entities' cross-run matching cache lives at a fixed
# path (logs/spool/_resolve_cache.duckdb), not a per-test fixture. Without
# this, two tests using the same synthetic record set would silently share a
# cache hit across test boundaries — real-world-correct (a hit is always
# byte-identical to a fresh compute, by construction), but it breaks test
# isolation and would mask a bug in the CACHE ITSELF behind "still passed".
os.environ.setdefault("MERGE_ENGINE_DISABLE_RESOLVE_CACHE", "1")

from src.utils.config_loader import (  # noqa: E402 - must follow the env var above
    Config,
    load_config,
)


@pytest.fixture(scope="session")
def test_config_path() -> str:
    """Path to the test configuration file."""
    return str(Path(__file__).parent / "fixtures" / "test_config.yaml")


@pytest.fixture(scope="session")
def test_config(test_config_path: str) -> Config:
    """Load the test configuration."""
    return load_config(test_config_path)


@pytest.fixture
def test_config_dict(test_config: Config) -> dict[str, Any]:
    """Flattened test config dict for node tests."""
    return {
        "user_request": "Import all people and companies",
        "target_entities": ["person", "company"],
        "origins": [
            {
                "id": "test_crm",
                "type": "postgres",
                "connection": {
                    "host": "localhost",
                    "port": 5432,
                    "user": "test",
                    "password": "test",
                    "database": "test_crm",
                },
                "priority": 1,
            },
            {
                "id": "test_hr",
                "type": "mongodb",
                "connection": {
                    "host": "localhost",
                    "port": 27017,
                    "user": "test",
                    "password": "test",
                    "database": "test_hr",
                },
                "priority": 0,
            },
        ],
        "destination": {
            "type": "postgresql",
            "host": test_config.destination.host or "localhost",
            "port": test_config.destination.port or 5432,
            "user": test_config.destination.user or "test",
            "password": test_config.destination.password or "test",
            "database": test_config.destination.database or "test_db",
            "schema": test_config.destination.schema_name or "public",
        },
        "llm_config": {
            "provider": test_config.llm.provider,
            "model": test_config.llm.model,
            "api_key": test_config.llm.api_key,
            "base_url": test_config.llm.base_url,
            "temperature": test_config.llm.temperature,
        },
        "extraction_config": {
            "default_batch_size": test_config.extraction.default_batch_size,
        },
        "schemas": {},
        "dependency_tree": {},
        "extraction_order": [],
        "extracted_data": {},
        "entity_clusters": {},
        "unified_entities": {},
        "field_mappings": {},
        "pending_schema_changes": [],
        "human_feedback": None,
        "load_approval": None,
        "errors": [],
        "retry_counts": {},
        "interrupt_data": None,
    }


@pytest.fixture
def sample_person_records() -> list[dict[str, Any]]:
    """Sample person records from multiple origins."""
    return [
        {
            "id": 1,
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "phone": "+1-555-1234",
            "birth_date": "1990-01-15",
            "_origin_id": "test_crm",
            "_origin_table": "customers",
            "_origin_id_row": "1",
        },
        {
            "id": 101,
            "first_name": "John",
            "last_name": "D.",
            "email": "john.doe@example.com",
            "phone": "5551234",
            "birth_date": "1990/01/15",
            "_origin_id": "test_hr",
            "_origin_table": "employees",
            "_origin_id_row": "101",
        },
        {
            "id": 2,
            "first_name": "Jane",
            "last_name": "Smith",
            "email": "jane.smith@example.com",
            "phone": "+1-555-5678",
            "birth_date": "1985-06-20",
            "_origin_id": "test_crm",
            "_origin_table": "customers",
            "_origin_id_row": "2",
        },
    ]


@pytest.fixture
def sample_company_records() -> list[dict[str, Any]]:
    """Sample company records from multiple origins."""
    return [
        {
            "id": 1,
            "name": "Acme Corp",
            "registration_number": "REG-001",
            "domain": "acme.com",
            "_origin_id": "test_crm",
            "_origin_table": "companies",
            "_origin_id_row": "1",
        },
        {
            "id": 201,
            "name": "Acme Corporation",
            "registration_number": "REG-001",
            "domain": "www.acme.com",
            "_origin_id": "test_hr",
            "_origin_table": "employers",
            "_origin_id_row": "201",
        },
    ]


@pytest.fixture
def sample_schemas() -> dict[str, Any]:
    """Sample schema discovery results."""
    return {
        "test_crm": {
            "origin_id": "test_crm",
            "origin_type": "postgres",
            "tables": [
                {
                    "name": "customers",
                    "full_name": "public.customers",
                    "columns": [
                        {"name": "id", "type": "INTEGER", "nullable": False},
                        {"name": "first_name", "type": "VARCHAR", "nullable": False},
                        {"name": "last_name", "type": "VARCHAR", "nullable": False},
                        {"name": "email", "type": "VARCHAR", "nullable": False},
                        {"name": "phone", "type": "VARCHAR", "nullable": True},
                        {"name": "birth_date", "type": "DATE", "nullable": True},
                    ],
                    "foreign_keys": [
                        {
                            "column": "company_id",
                            "ref_table": "companies",
                            "ref_column": "id",
                        }
                    ],
                    "sample_row": {
                        "id": 1,
                        "first_name": "John",
                        "last_name": "Doe",
                        "email": "john.doe@example.com",
                    },
                },
                {
                    "name": "companies",
                    "full_name": "public.companies",
                    "columns": [
                        {"name": "id", "type": "INTEGER", "nullable": False},
                        {"name": "name", "type": "VARCHAR", "nullable": False},
                        {"name": "registration_number", "type": "VARCHAR", "nullable": True},
                        {"name": "domain", "type": "VARCHAR", "nullable": True},
                    ],
                    "foreign_keys": [],
                    "sample_row": {
                        "id": 1,
                        "name": "Acme Corp",
                        "domain": "acme.com",
                    },
                },
            ],
        },
        "test_hr": {
            "origin_id": "test_hr",
            "origin_type": "mongodb",
            "tables": [
                {
                    "name": "employees",
                    "columns": [
                        {"name": "id", "type": "INTEGER", "nullable": False},
                        {"name": "first_name", "type": "STRING", "nullable": False},
                        {"name": "last_name", "type": "STRING", "nullable": False},
                        {"name": "email", "type": "STRING", "nullable": False},
                        {"name": "phone", "type": "STRING", "nullable": True},
                    ],
                    "foreign_keys": [],
                    "sample_row": {
                        "id": 101,
                        "first_name": "John",
                        "email": "john.doe@example.com",
                    },
                },
            ],
        },
    }


@pytest.fixture
def temp_config_file() -> str:
    """Create a temporary config file for testing (minimal format, no origins)."""
    config_data = {
        "destination": {
            "type": "postgresql",
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "password": "test",
            "database": "test_db",
            "schema": "public",
        },
        "llm": {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test_key",
            "base_url": None,
            "temperature": 0.0,
        },
        "retry": {
            "max_attempts": 3,
            "backoff": "exponential",
            "min_backoff_seconds": 1,
            "max_backoff_seconds": 30,
        },
        "extraction": {
            "default_batch_size": 1000,
            "max_batch_size": 10000,
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(config_data, f)
        return f.name


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: Any) -> None:
    """Clean environment for each test."""
    # Clear any test-related env vars
    for key in list(os.environ.keys()):
        if key.startswith("DB_") or key.startswith("DATABRICKS_") or key.startswith("OPENAI_"):
            monkeypatch.delenv(key, raising=False)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-apply the `unit`/`integration` markers by directory (Phase 0 Sec
    0.7), so no individual test file needs a decorator just to be
    selectable by `-m unit` / `-m integration` — the directory a test lives
    in already IS that classification."""
    for item in items:
        path = str(item.path)
        if f"{os.sep}tests{os.sep}unit{os.sep}" in path:
            item.add_marker(pytest.mark.unit)
        elif f"{os.sep}tests{os.sep}integration{os.sep}" in path:
            item.add_marker(pytest.mark.integration)
