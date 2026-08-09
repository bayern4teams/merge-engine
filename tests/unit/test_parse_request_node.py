"""Unit tests for parse_request node."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.parse_request import parse_request


class TestParseRequest:
    """Tests for the parse_request node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state."""
        return {
            "user_request": "Import all people and companies from our CRM",
            "target_entities": [],
            "schemas": {},
            "llm_config": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "test_key",
            },
            "errors": [],
        }

    @pytest.fixture
    def mock_llm_client(self) -> MagicMock:
        """Mock LLMClient for testing."""
        client = MagicMock()
        client.call_json = AsyncMock(
            return_value={
                "entity_types": ["person", "company"],
                "entity_tables": {
                    "person": [{"origin_id": "origin_1", "table": "Person"}],
                    "company": [{"origin_id": "origin_1", "table": "Company"}],
                },
            }
        )
        return client

    @staticmethod
    def _prompts(
        system: str = "Extract entity types. Return only JSON.",
        human: str = "Extract entities: {user_request}",
    ) -> dict[str, dict[str, str]]:
        return {"request_parsing": {"system": system, "human": human}}

    @pytest.mark.asyncio
    async def test_parse_request_success(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request with the entity_types contract."""
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == ["person", "company"]
        assert "errors" not in result or not result.get("errors")
        mock_llm_client.call_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_request_empty_dict_response(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request with empty dict response falls back to empty list."""
        mock_llm_client.call_json = AsyncMock(return_value={})

        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == []

    @pytest.mark.asyncio
    async def test_parse_request_missing_prompt_template_uses_fallback(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request falls back to default prompt when template missing."""
        mock_llm_client.call_json = AsyncMock(return_value={"entity_types": ["person"]})

        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                {},  # Empty - no request_parsing template
            ),
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == ["person"]
        mock_llm_client.call_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_parse_request_llm_failure_returns_empty_with_error(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request handles LLM failure gracefully."""
        mock_llm_client.call_json = AsyncMock(side_effect=Exception("LLM service unavailable"))

        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == []
        assert len(result.get("errors", [])) == 1
        assert "Request parsing failed" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_parse_request_unexpected_result_type(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test parse_request handles unexpected LLM result type (not a dict)."""
        mock_llm_client.call_json = AsyncMock(return_value="string_not_a_dict")

        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == []

    @pytest.mark.asyncio
    async def test_parse_request_prompt_formatting(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test that the human prompt is correctly formatted with user_request."""
        mock_llm_client.call_json = AsyncMock(return_value={"entity_types": ["person"]})

        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(human="Parse this: {user_request}"),
            ),
        ):
            await parse_request(base_state)

        call_args = mock_llm_client.call_json.call_args
        assert "Import all people and companies from our CRM" in call_args[1]["user_prompt"]

    @pytest.mark.asyncio
    async def test_parse_request_system_prompt_passed(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Test that the system prompt is correctly passed to LLM."""
        mock_llm_client.call_json = AsyncMock(return_value={"entity_types": ["person"]})

        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(
                    system="You extract entity types from user requests. Return only JSON."
                ),
            ),
        ):
            await parse_request(base_state)

        call_args = mock_llm_client.call_json.call_args
        assert "You extract entity types from user requests" in call_args[1]["system_prompt"]

    @pytest.mark.asyncio
    async def test_company_scope_is_captured(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """A request about one named company yields an active merge_scope."""
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "entity_types": ["company", "person", "product"],
                "subject": "Nova",
                "scope_type": "company",
            }
        )
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["merge_scope"] == {
            "active": True,
            "subject": "Nova",
            "scope_type": "company",
        }

    @pytest.mark.asyncio
    async def test_general_request_has_inactive_scope(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """A general request (no subject) leaves the scope inactive."""
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "entity_types": ["company", "person"],
                "subject": "",
                "scope_type": "none",
            }
        )
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["merge_scope"]["active"] is False
        assert result["merge_scope"]["subject"] == ""

    @pytest.mark.asyncio
    async def test_subject_without_scope_type_is_not_active(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """A subject with scope_type 'none' must not activate scoping.

        Guards against a half-answer from the model quietly filtering a merge
        the user meant to be whole.
        """
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "entity_types": ["company"],
                "subject": "CRM",
                "scope_type": "none",
            }
        )
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["merge_scope"]["active"] is False

    @pytest.mark.asyncio
    async def test_failure_yields_inactive_scope(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """The error path must still return a well-formed inactive scope."""
        mock_llm_client.call_json = AsyncMock(side_effect=Exception("boom"))
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["merge_scope"] == {
            "active": False,
            "subject": "",
            "scope_type": "none",
        }

    # -- explicit vs. inferred entity types (remediation #1, 2026-07-30) --

    @pytest.mark.asyncio
    async def test_explicit_entity_types_is_returned_separately(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """A Company-Wildcard response names some types but only explicitly
        asks for a subset; both lists must be returned, and explicit is the
        proper subset the prompt contract requires."""
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "entity_types": ["company", "person", "finance", "technology"],
                "explicit_entity_types": ["finance", "technology"],
                "subject": "Boeing",
                "scope_type": "company",
            }
        )
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["target_entities"] == ["company", "person", "finance", "technology"]
        assert result["explicit_target_entities"] == ["finance", "technology"]

    @pytest.mark.asyncio
    async def test_missing_explicit_entity_types_defaults_to_all_explicit(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """A model/profile that doesn't emit explicit_entity_types at all must
        default to treating everything as explicit -- the strictest, safest
        fallback (never silently widen what's allowed to fail quietly)."""
        mock_llm_client.call_json = AsyncMock(return_value={"entity_types": ["person", "company"]})
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["explicit_target_entities"] == ["person", "company"]

    @pytest.mark.asyncio
    async def test_explicit_entity_types_is_clamped_to_a_subset(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """If the model violates its own output contract and lists an explicit
        type not present in entity_types, drop it rather than trust it."""
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "entity_types": ["company"],
                "explicit_entity_types": ["company", "person"],  # "person" not in entity_types
            }
        )
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["explicit_target_entities"] == ["company"]

    @pytest.mark.asyncio
    async def test_explicit_entity_types_failure_path_is_empty(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        mock_llm_client.call_json = AsyncMock(side_effect=Exception("boom"))
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["explicit_target_entities"] == []

    @pytest.mark.asyncio
    async def test_llm_failure_sets_failure_for_error_gate(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """An exception must set state["failure"] (Phase 1.5) so
        _fallible_router routes to the error gate instead of the run
        silently continuing to analyze_dependencies with zero entities."""
        mock_llm_client.call_json = AsyncMock(side_effect=Exception("boom"))
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["failure"]["node"] == "parse_request"
        assert "boom" in result["failure"]["error"]
        assert result["failure"]["disposition"] in ("fail", "escalate", "retry")

    # -- schema grounding (Phase 1.8 / Phase 9 Component 2) --

    @pytest.mark.asyncio
    async def test_schema_summary_reaches_the_prompt(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """When schemas is populated, the rendered prompt must contain real
        table and column names and must not leave the literal {schema_info}
        placeholder token unsubstituted."""
        base_state["schemas"] = {
            "origin_1": {
                "origin_type": "clickhouse",
                "tables": [
                    {
                        "name": "CompanyExperience",
                        "columns": [{"name": "companyID"}, {"name": "personID"}],
                    }
                ],
            }
        }
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(human="Schema:\n{schema_info}\n\nRequest: {user_request}"),
            ),
        ):
            await parse_request(base_state)

        user_prompt = mock_llm_client.call_json.call_args[1]["user_prompt"]
        assert "CompanyExperience" in user_prompt
        assert "companyID" in user_prompt
        assert "personID" in user_prompt
        assert "{schema_info}" not in user_prompt

    @pytest.mark.asyncio
    async def test_entity_tables_shape_is_returned(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """entity_tables must come back as {entity: [{origin_id, table}, ...]}."""
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["entity_tables"] == {
            "person": [{"origin_id": "origin_1", "table": "Person"}],
            "company": [{"origin_id": "origin_1", "table": "Company"}],
        }

    @pytest.mark.asyncio
    async def test_malformed_entity_table_entries_are_dropped(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """A malformed entity_tables entry (missing origin_id/table, not a
        dict, or not a list at all) must be cleaned out rather than passed
        through — see the cleaning loop at parse_request.py:102-111."""
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "entity_types": ["company", "person", "ghost"],
                "entity_tables": {
                    "company": [
                        {"origin_id": "origin_1", "table": "Company"},
                        {"origin_id": "origin_1"},  # missing "table" -- dropped
                        {"table": "Company"},  # missing "origin_id" -- dropped
                        "not_a_dict",  # dropped
                    ],
                    "person": "not_a_list",  # whole value dropped for this entity
                },
            }
        )
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                self._prompts(),
            ),
        ):
            result = await parse_request(base_state)

        assert result["entity_tables"]["company"] == [
            {"origin_id": "origin_1", "table": "Company"}
        ]
        assert "person" not in result["entity_tables"]
