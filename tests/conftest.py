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

from src.utils.config_loader import load_config, Config
from src.orchestrator.workflow_manager import WorkflowManager


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
                "id": o.id,
                "type": o.type,
                "connection": o.connection.model_dump(),
                "priority": o.priority,
            }
            for o in test_config.origins
        ],
        "destination": {
            "type": test_config.destination.type,
            "host": test_config.destination.host,
            "http_path": test_config.destination.http_path,
            "token": test_config.destination.token,
            "catalog": test_config.destination.catalog,
            "schema": test_config.destination.schema_name,
            "tables": test_config.destination.tables,
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
    """Create a temporary config file for testing."""
    config_data = {
        "origins": [
            {
                "id": "test_origin",
                "type": "postgres",
                "connection": {
                    "host": "localhost",
                    "port": 5432,
                    "user": "test",
                    "password": "test",
                    "database": "test",
                },
                "priority": 1,
            }
        ],
        "destination": {
            "type": "databricks",
            "host": "test.databricks.com",
            "http_path": "/sql/1.0/warehouses/test123",
            "token": "test_token",
            "catalog": "main",
            "schema": "test",
            "tables": {"person": "dim_person"},
        },
        "llm": {
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "test_key",
            "base_url": None,
            "temperature": 0.0,
        },
        "blocking_keys": {"person": ["email"]},
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

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        yaml.dump(config_data, f)
        return f.name


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: Any) -> None:
    """Clean environment for each test."""
    # Clear any test-related env vars
    for key in list(os.environ.keys()):
        if key.startswith("CRM_") or key.startswith("HR_") or key.startswith("DATABRICKS_") or key.startswith("OPENAI_"):
            monkeypatch.delenv(key, raising=False)