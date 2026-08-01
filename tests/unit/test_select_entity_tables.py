"""Phase K.5: entity -> source table selection.

The old selector was a hardcoded synonym list, so Pagila's ``customer`` and
MongoDB's ``users`` were never considered for the entity ``person`` — a
two-origin merge silently ran against one table. These tests cover the
heuristic floor, the LLM's ability to add tables beyond it, and the guard
against the LLM inventing tables that don't exist.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_server.tools.select_entity_tables import (
    build_table_catalog,
    heuristic_candidates,
    select_entity_tables_impl,
)


@pytest.fixture
def schemas() -> dict[str, Any]:
    return {
        "pg_main": {
            "origin_type": "postgresql",
            "tables": [
                {"name": "staff", "full_name": "public.staff", "row_count": 1500,
                 "columns": [{"name": "staff_id"}, {"name": "first_name"},
                             {"name": "last_name"}, {"name": "email"}]},
                {"name": "customer", "full_name": "public.customer", "row_count": 599,
                 "columns": [{"name": "customer_id"}, {"name": "first_name"},
                             {"name": "last_name"}, {"name": "email"}]},
                {"name": "film", "full_name": "public.film", "row_count": 1000,
                 "columns": [{"name": "film_id"}, {"name": "title"}]},
            ],
        },
        "mongo_app": {
            "origin_type": "mongodb",
            "tables": [
                {"name": "users", "full_name": "sample_mflix.users", "row_count": 185,
                 "columns": [{"name": "_id"}, {"name": "name"}, {"name": "email"}]},
            ],
        },
    }


def test_catalog_includes_every_table_unfiltered(schemas) -> None:
    """The whole point: tables whose names don't look like the entity."""
    catalog = build_table_catalog(schemas)
    for expected in ("public.staff", "public.customer", "public.film",
                     "sample_mflix.users"):
        assert expected in catalog
    assert "1500 rows" in catalog
    assert "first_name" in catalog


def test_heuristic_alone_misses_customer_and_users(schemas) -> None:
    """Documents the gap the LLM stage exists to close."""
    tables = {c["table"] for c in heuristic_candidates("person", schemas)}
    assert tables == {"public.staff"}


@pytest.mark.asyncio
async def test_llm_adds_tables_the_heuristic_missed(schemas) -> None:
    response = {
        "entity_tables": {
            "person": [
                {"origin_id": "pg_main", "table": "public.staff",
                 "confidence": 0.95, "reason": "staff are people"},
                {"origin_id": "pg_main", "table": "public.customer",
                 "confidence": 0.9, "reason": "customers are people"},
                {"origin_id": "mongo_app", "table": "sample_mflix.users",
                 "confidence": 0.88, "reason": "user accounts are people"},
            ]
        },
        "notes": "",
    }
    with patch("src.llm.LLMClient") as mock_client:
        mock_client.return_value.call_json = AsyncMock(return_value=response)
        result = await select_entity_tables_impl(
            ["person"], schemas, {"provider": "openai"}
        )

    tables = {(c["origin_id"], c["table"]) for c in result["entity_tables"]["person"]}
    assert tables == {
        ("pg_main", "public.staff"),
        ("pg_main", "public.customer"),
        ("mongo_app", "sample_mflix.users"),
    }
    assert result["llm_used"] is True


@pytest.mark.asyncio
async def test_invented_tables_are_dropped(schemas) -> None:
    """A hallucinated table must never reach extraction."""
    response = {
        "entity_tables": {
            "person": [
                {"origin_id": "pg_main", "table": "public.staff", "confidence": 0.9},
                {"origin_id": "pg_main", "table": "public.employees_v2",
                 "confidence": 0.9},
            ]
        },
        "notes": "",
    }
    with patch("src.llm.LLMClient") as mock_client:
        mock_client.return_value.call_json = AsyncMock(return_value=response)
        result = await select_entity_tables_impl(
            ["person"], schemas, {"provider": "openai"}
        )

    tables = {c["table"] for c in result["entity_tables"]["person"]}
    assert tables == {"public.staff"}


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_the_heuristic(schemas) -> None:
    """A dead LLM degrades to the old behaviour instead of extracting nothing."""
    with patch("src.llm.LLMClient") as mock_client:
        mock_client.return_value.call_json = AsyncMock(
            side_effect=RuntimeError("model unreachable")
        )
        result = await select_entity_tables_impl(
            ["person"], schemas, {"provider": "openai"}
        )

    assert [c["table"] for c in result["entity_tables"]["person"]] == ["public.staff"]
    assert result["llm_used"] is False
    assert "unavailable" in result["notes"]


@pytest.mark.asyncio
async def test_bare_table_name_resolves_to_the_qualified_one(schemas) -> None:
    """Models often drop the schema prefix; that shouldn't lose the table."""
    response = {
        "entity_tables": {
            "person": [{"origin_id": "pg_main", "table": "customer",
                        "confidence": 0.9}]
        },
        "notes": "",
    }
    with patch("src.llm.LLMClient") as mock_client:
        mock_client.return_value.call_json = AsyncMock(return_value=response)
        result = await select_entity_tables_impl(
            ["person"], schemas, {"provider": "openai"}
        )

    tables = {c["table"] for c in result["entity_tables"]["person"]}
    assert "public.customer" in tables


@pytest.mark.asyncio
async def test_selection_can_be_disabled(schemas) -> None:
    with patch("src.llm.LLMClient") as mock_client:
        result = await select_entity_tables_impl(
            ["person"], schemas,
            {"provider": "openai", "table_selection_enabled": False},
        )
    mock_client.assert_not_called()
    assert result["llm_used"] is False
    assert [c["table"] for c in result["entity_tables"]["person"]] == ["public.staff"]
