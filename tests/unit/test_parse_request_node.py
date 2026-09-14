"""Unit tests for parse_request node (Phase 3.2 contract).

Entity grouping is deterministic (entity_grouping.group_tables_into_entities,
Phase 3.1) — the LLM's only job here is naming groups, classifying which
were explicitly named, and extracting merge scope. It cannot add, remove, or
rename membership away from what was discovered.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.nodes.parse_request import parse_request


def _schema(*tables: str) -> dict[str, Any]:
    return {
        "origin_1": {
            "origin_type": "clickhouse",
            "tables": [{"name": t, "columns": [{"name": "id"}, {"name": "name"}]} for t in tables],
        }
    }


class TestParseRequest:
    """Tests for the parse_request node."""

    @pytest.fixture
    def base_state(self) -> dict[str, Any]:
        """Base workflow state with two discoverable tables."""
        return {
            "user_request": "Import all people and companies from our CRM",
            "target_entities": [],
            "schemas": _schema("Person", "Company"),
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
                "group_names": {"person": "person", "company": "company"},
                "explicit_groups": ["person", "company"],
                "subject": "",
                "scope_type": "none",
                "notes": "",
            }
        )
        return client

    @staticmethod
    def _prompts(
        system: str = "Name entity groups. Return only JSON.",
        human: str = "Groups: {group_catalog}\nRequest: {user_request}",
    ) -> dict[str, dict[str, str]]:
        return {"request_parsing": {"system": system, "human": human}}

    @pytest.mark.asyncio
    async def test_parse_request_success(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Both discovered groups are named and returned."""
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

        assert set(result["target_entities"]) == {"person", "company"}
        assert "errors" not in result or not result.get("errors")
        mock_llm_client.call_json.assert_called_once()

    # -- full-coverage invariant (the bug this rewrite exists to fix) --

    @pytest.mark.asyncio
    async def test_every_discovered_table_reaches_entity_tables(
        self, mock_llm_client: MagicMock
    ) -> None:
        """The 4th live run's bug: 7 of 20 discovered tables never appeared
        in any entity type, because the LLM was free to just not mention
        them. Now every discovered table must appear in entity_tables
        regardless of what the LLM names or classifies as explicit."""
        state: dict[str, Any] = {
            "user_request": "Sync everything",
            "schemas": _schema("Company", "CompanyExperience", "IndustryClassification"),
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
        }
        mock_llm_client.call_json = AsyncMock(
            # LLM only bothers to name ONE of the three groups and lists no
            # explicit groups -- membership must still be complete.
            return_value={
                "group_names": {"company": "company"},
                "explicit_groups": [],
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
            result = await parse_request(state)

        all_tables = {
            (e["origin_id"], e["table"])
            for entries in result["entity_tables"].values()
            for e in entries
        }
        assert ("origin_1", "Company") in all_tables
        assert ("origin_1", "CompanyExperience") in all_tables
        assert ("origin_1", "IndustryClassification") in all_tables

    # -- naming --

    @pytest.mark.asyncio
    async def test_llm_rename_is_applied(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "group_names": {"person": "employee", "company": "company"},
                "explicit_groups": [],
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

        assert "employee" in result["target_entities"]
        assert "employee" in result["entity_tables"]

    @pytest.mark.asyncio
    async def test_missing_group_name_falls_back_to_deterministic_key(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """The LLM omitting a group from group_names must not drop it --
        the deterministic key is used as the name instead."""
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "group_names": {"person": "employee"},  # "company" omitted
                "explicit_groups": [],
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

        assert "company" in result["target_entities"]
        assert "company" in result["entity_tables"]

    @pytest.mark.asyncio
    async def test_invented_group_name_for_unknown_key_is_ignored(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """A rename entry for a group key that was never in the
        deterministic catalog must be ignored -- membership cannot be
        expanded by the LLM inventing a new key."""
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "group_names": {
                    "person": "person",
                    "company": "company",
                    "ghost_group": "invented",
                },
                "explicit_groups": [],
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

        assert "invented" not in result["target_entities"]
        assert set(result["target_entities"]) == {"person", "company"}

    # -- explicit vs inferred --

    @pytest.mark.asyncio
    async def test_explicit_groups_maps_to_renamed_entities(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "group_names": {"person": "employee", "company": "company"},
                "explicit_groups": ["person"],
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

        assert result["explicit_target_entities"] == ["employee"]

    @pytest.mark.asyncio
    async def test_explicit_groups_with_unknown_key_is_dropped(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "group_names": {"person": "person", "company": "company"},
                "explicit_groups": ["person", "ghost_group"],
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

        assert result["explicit_target_entities"] == ["person"]

    # -- scope --

    @pytest.mark.asyncio
    async def test_company_scope_is_captured(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "group_names": {"person": "person", "company": "company"},
                "explicit_groups": [],
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
                "group_names": {"person": "person", "company": "company"},
                "explicit_groups": [],
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

    # -- schema-grounding / prompt construction --

    @pytest.mark.asyncio
    async def test_group_catalog_reaches_the_prompt(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """The rendered prompt must contain the deterministic groups' member
        tables and must not leave the literal {group_catalog} placeholder
        unsubstituted."""
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
            await parse_request(base_state)

        user_prompt = mock_llm_client.call_json.call_args[1]["user_prompt"]
        assert "Person" in user_prompt
        assert "Company" in user_prompt
        assert "{group_catalog}" not in user_prompt

    @pytest.mark.asyncio
    async def test_no_schemas_returns_empty_without_calling_llm(
        self, mock_llm_client: MagicMock
    ) -> None:
        state: dict[str, Any] = {
            "user_request": "Import all companies",
            "schemas": {},
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
        }
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
            result = await parse_request(state)

        assert result["target_entities"] == []
        assert result["entity_tables"] == {}
        mock_llm_client.call_json.assert_not_called()

    # -- graceful degradation on LLM failure --

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_deterministic_keys_not_empty(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """Grouping never depended on the LLM (Phase 3.1) -- a naming
        failure must not regress to the empty-entities silent-continue this
        project has already been burned by twice (Phase 1.4/1.5). The run
        degrades to usable, deterministically-keyed entities instead."""
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

        assert set(result["target_entities"]) == {"person", "company"}
        assert "failure" not in result or not result.get("failure")
        assert len(result.get("errors", [])) == 1
        assert "degraded" in result["errors"][0].lower()
        assert (
            "LLM" in result["table_selection_notes"] or "failed" in result["table_selection_notes"]
        )

    @pytest.mark.asyncio
    async def test_llm_failure_yields_inactive_scope(
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

        assert result["merge_scope"] == {
            "active": False,
            "subject": "",
            "scope_type": "none",
        }

    @pytest.mark.asyncio
    async def test_unexpected_result_type_falls_back_to_deterministic_keys(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """A non-dict LLM response is treated as an empty naming/scope
        answer -- entities still come from the deterministic groups."""
        mock_llm_client.call_json = AsyncMock(return_value="not a dict")
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

        assert set(result["target_entities"]) == {"person", "company"}

    @pytest.mark.asyncio
    async def test_missing_prompt_template_uses_fallback(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
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

        assert set(result["target_entities"]) == {"person", "company"}
        mock_llm_client.call_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_postcondition_failure_is_not_swallowed_as_llm_failure(
        self, base_state: dict[str, Any], mock_llm_client: MagicMock
    ) -> None:
        """A ValueError from _finish's orphan post-condition must propagate
        as a real exception -- never be caught by the `except Exception`
        around the LLM call and misreported as "LLM call failed". That was
        the original bug: `_finish` used to be called INSIDE that try, so
        its AssertionError was swallowed, logged with a false diagnosis,
        and then re-raised from a second `_finish` call inside the handler.
        The LLM call here succeeds (the default mock), so the raised error
        can only have come from _finish's post-condition, called after the
        try/except -- proving it is no longer misdiagnosed."""
        bad_groups = (
            {
                "company": [
                    {"origin_id": "origin_1", "table": "DoesNotExist", "full_name": "DoesNotExist"}
                ]
            },
            [],
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
            patch(
                "src.orchestrator.nodes.parse_request.group_tables_into_entities",
                return_value=bad_groups,
            ),
        ):
            with pytest.raises(ValueError, match="Orphaned source tables"):
                await parse_request(base_state)

        mock_llm_client.call_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_naming_collision_does_not_drop_either_groups_tables(
        self, mock_llm_client: MagicMock
    ) -> None:
        """Live regression (Airbus, this session, reproduced deterministically
        twice): the LLM assigned the SAME display name to two different
        deterministic groups. entity_tables used to be built as a plain
        {name_map[g]: ... for g in deterministic_groups} dict comprehension,
        so the second group's tables silently overwrote the first's entry --
        raising "Orphaned source tables" downstream with no clue why. Both
        groups' tables must now survive, one under the LLM's chosen name,
        the other under a disambiguated fallback -- and this must NOT raise."""
        state: dict[str, Any] = {
            "user_request": "Merge everything about Airbus",
            "schemas": _schema("Person", "Company"),
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
        }
        mock_llm_client.call_json = AsyncMock(
            return_value={
                # Both "person" and "company" groups named "contact" --
                # a genuine LLM naming collision.
                "group_names": {"person": "contact", "company": "contact"},
                "explicit_groups": ["person", "company"],
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
            result = await parse_request(state)  # must not raise

        all_tables = {
            (e["origin_id"], e["table"])
            for entries in result["entity_tables"].values()
            for e in entries
        }
        assert ("origin_1", "Person") in all_tables
        assert ("origin_1", "Company") in all_tables
        # Exactly one entity kept the collided display name; the other
        # fell back to its own deterministic group key instead of vanishing.
        assert result["target_entities"].count("contact") == 1
        assert len(result["target_entities"]) == 2


class TestProfileCaching:
    """Phase A4: a profile-pinned entity_tables decision must actually be
    consulted before parse_request does any work, not just seeded into
    state and immediately overwritten (the "O.1 is inert" bug the audit
    found: entity_tables was pinned by apply_profile, then parse_request
    unconditionally rebuilt it from group_tables_into_entities + a fresh
    LLM call every time, regardless of whether a pin existed)."""

    @staticmethod
    def _prompts() -> dict[str, dict[str, str]]:
        return {
            "request_parsing": {
                "system": "Name entity groups. Return only JSON.",
                "human": "Groups: {group_catalog}\nRequest: {user_request}",
            }
        }

    @pytest.mark.asyncio
    async def test_first_run_records_a_fingerprint_for_capture(self) -> None:
        state: dict[str, Any] = {
            "user_request": "Import all people and companies",
            "schemas": _schema("Person", "Company"),
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
        }
        client = MagicMock()
        client.call_json = AsyncMock(
            return_value={
                "group_names": {"person": "person", "company": "company"},
                "explicit_groups": [],
                "subject": "",
                "scope_type": "none",
                "notes": "",
            }
        )
        with (
            patch("src.orchestrator.nodes.parse_request.LLMClient", return_value=client),
            patch("src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES", self._prompts()),
        ):
            result = await parse_request(state)

        client.call_json.assert_called_once()
        assert result.get("_entity_tables_fingerprint")

    @pytest.mark.asyncio
    async def test_a_matching_cached_fingerprint_skips_the_llm_call_entirely(self) -> None:
        """The actual point of the feature: zero LLM calls on a repeat run
        against the same schema and request text."""
        schemas = _schema("Person", "Company")
        user_request = "Import all people and companies"
        state: dict[str, Any] = {
            "user_request": user_request,
            "schemas": schemas,
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
        }
        client = MagicMock()
        client.call_json = AsyncMock(
            return_value={
                "group_names": {"person": "person", "company": "company"},
                "explicit_groups": ["person"],
                "subject": "",
                "scope_type": "none",
                "notes": "",
            }
        )
        with (
            patch("src.orchestrator.nodes.parse_request.LLMClient", return_value=client),
            patch("src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES", self._prompts()),
        ):
            first = await parse_request(state)

        # Simulate what apply_profile does with a captured profile: seed
        # _profile_cache from the first run's own fingerprint + bundle.
        second_state: dict[str, Any] = {
            "user_request": user_request,
            "schemas": schemas,
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
            "_profile_cache": {
                "fingerprints": {"entity_tables": first["_entity_tables_fingerprint"]},
                "artefacts": {
                    "entity_tables": {
                        "entity_tables": first["entity_tables"],
                        "target_entities": first["target_entities"],
                        "explicit_target_entities": first["explicit_target_entities"],
                        "merge_scope": first["merge_scope"],
                        "table_selection_notes": first["table_selection_notes"],
                    }
                },
            },
        }
        client.call_json.reset_mock()
        with (
            patch("src.orchestrator.nodes.parse_request.LLMClient", return_value=client),
            patch("src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES", self._prompts()),
        ):
            second = await parse_request(second_state)

        client.call_json.assert_not_called()
        assert second["entity_tables"] == first["entity_tables"]
        assert set(second["target_entities"]) == set(first["target_entities"])
        assert second["reused_stages"] == ["entity_tables"]

    @pytest.mark.asyncio
    async def test_a_different_request_text_misses_the_cache(self) -> None:
        """explicit_target_entities/merge_scope depend on request text, not
        just schema -- reusing a cached decision for a DIFFERENT request
        would silently answer the wrong question, so a text change must
        fall through to a real (LLM) computation."""
        schemas = _schema("Person", "Company")
        client = MagicMock()
        client.call_json = AsyncMock(
            return_value={
                "group_names": {"person": "person", "company": "company"},
                "explicit_groups": [],
                "subject": "",
                "scope_type": "none",
                "notes": "",
            }
        )
        state: dict[str, Any] = {
            "user_request": "just companies please",
            "schemas": schemas,
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
            "_profile_cache": {
                "fingerprints": {"entity_tables": "fp-from-a-different-request"},
                "artefacts": {
                    "entity_tables": {
                        "entity_tables": {"person": [], "company": []},
                        "target_entities": ["person", "company"],
                        "explicit_target_entities": [],
                        "merge_scope": {"active": False, "subject": "", "scope_type": "none"},
                        "table_selection_notes": "",
                    }
                },
            },
        }
        with (
            patch("src.orchestrator.nodes.parse_request.LLMClient", return_value=client),
            patch("src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES", self._prompts()),
        ):
            result = await parse_request(state)

        client.call_json.assert_called_once()
        assert "reused_stages" not in result


class TestPostgresqlOriginIdentity:
    """Regression test for the bug that crashed parse_request on any
    PostgreSQL origin: entity_grouping.py used to store `full_name` (e.g.
    "public.Company") in the `table` key, which matched nothing downstream
    -- including parse_request's own orphan post-condition, which compares
    against discover_schemas' bare "name" field."""

    @pytest.mark.asyncio
    async def test_postgresql_origin_with_full_name_does_not_orphan(self) -> None:
        schema = {
            "origin_1": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "Company",
                        "full_name": "public.Company",
                        "columns": [{"name": "id"}, {"name": "name"}],
                    },
                    {
                        "name": "Person",
                        "full_name": "public.Person",
                        "columns": [{"name": "id"}, {"name": "name"}],
                    },
                ],
            }
        }
        state: dict[str, Any] = {
            "user_request": "Bring over everything",
            "target_entities": [],
            "schemas": schema,
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "test_key"},
            "errors": [],
        }
        mock_llm_client = MagicMock()
        mock_llm_client.call_json = AsyncMock(
            return_value={
                "group_names": {"company": "company", "person": "person"},
                "explicit_groups": [],
                "subject": "",
                "scope_type": "none",
                "notes": "",
            }
        )
        with (
            patch(
                "src.orchestrator.nodes.parse_request.LLMClient",
                return_value=mock_llm_client,
            ),
            patch(
                "src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES",
                TestParseRequest._prompts(),
            ),
        ):
            result = await parse_request(state)

        assert set(result["target_entities"]) == {"company", "person"}
        all_entries = [e for lst in result["entity_tables"].values() for e in lst]
        assert {(e["origin_id"], e["table"]) for e in all_entries} == {
            ("origin_1", "Company"),
            ("origin_1", "Person"),
        }
        assert {e["full_name"] for e in all_entries} == {"public.Company", "public.Person"}


class TestSubjectFirstMode:
    """Phase 3.3: get_initial_state sets an active merge_scope directly
    from the UI's company-name box before parse_request ever runs. That
    pre-set scope must win over whatever the LLM's own subject/scope_type
    guess says, and must survive a naming-call failure intact."""

    @pytest.fixture
    def subject_state(self) -> dict[str, Any]:
        return {
            "user_request": "Merge all information about Airbus.",
            "schemas": _schema("Company", "Person"),
            "merge_scope": {"active": True, "subject": "Airbus", "scope_type": "company"},
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
        }

    @staticmethod
    def _prompts() -> dict[str, dict[str, str]]:
        return {
            "request_parsing": {
                "system": "Name entity groups. Return only JSON.",
                "human": "Groups: {group_catalog}\nRequest: {user_request}",
            }
        }

    @pytest.mark.asyncio
    async def test_pre_set_scope_wins_over_llm_guess(self, subject_state: dict[str, Any]) -> None:
        client = MagicMock()
        client.call_json = AsyncMock(
            return_value={
                "group_names": {"company": "company", "person": "person"},
                "explicit_groups": [],
                # The LLM guesses a DIFFERENT subject/scope -- must be ignored.
                "subject": "Some Other Company",
                "scope_type": "none",
            }
        )
        with (
            patch("src.orchestrator.nodes.parse_request.LLMClient", return_value=client),
            patch("src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES", self._prompts()),
        ):
            result = await parse_request(subject_state)

        assert result["merge_scope"] == {
            "active": True,
            "subject": "Airbus",
            "scope_type": "company",
        }

    @pytest.mark.asyncio
    async def test_pre_set_scope_survives_llm_failure(self, subject_state: dict[str, Any]) -> None:
        client = MagicMock()
        client.call_json = AsyncMock(side_effect=Exception("boom"))
        with (
            patch("src.orchestrator.nodes.parse_request.LLMClient", return_value=client),
            patch("src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES", self._prompts()),
        ):
            result = await parse_request(subject_state)

        assert result["merge_scope"] == {
            "active": True,
            "subject": "Airbus",
            "scope_type": "company",
        }
        # Grouping still succeeded despite the LLM failure (Phase 3.1/3.2).
        assert set(result["target_entities"]) == {"company", "person"}

    @pytest.mark.asyncio
    async def test_pre_set_scope_survives_no_discovered_schemas(self) -> None:
        state: dict[str, Any] = {
            "user_request": "Merge all information about Airbus.",
            "schemas": {},
            "merge_scope": {"active": True, "subject": "Airbus", "scope_type": "company"},
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
        }
        client = MagicMock()
        client.call_json = AsyncMock(return_value={})
        with (
            patch("src.orchestrator.nodes.parse_request.LLMClient", return_value=client),
            patch("src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES", self._prompts()),
        ):
            result = await parse_request(state)

        assert result["merge_scope"]["active"] is True
        assert result["merge_scope"]["subject"] == "Airbus"
        client.call_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_inactive_pre_set_scope_does_not_suppress_llm_extraction(self) -> None:
        """An explicitly inactive merge_scope in state (the normal
        free-text/Advanced-mode case) must not be treated as "pre-set" --
        the LLM's own subject/scope_type extraction still applies."""
        state: dict[str, Any] = {
            "user_request": "Bring over everything about Nova",
            "schemas": _schema("Company", "Person"),
            "merge_scope": {"active": False, "subject": "", "scope_type": "none"},
            "llm_config": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
            "errors": [],
        }
        client = MagicMock()
        client.call_json = AsyncMock(
            return_value={
                "group_names": {"company": "company", "person": "person"},
                "explicit_groups": [],
                "subject": "Nova",
                "scope_type": "company",
            }
        )
        with (
            patch("src.orchestrator.nodes.parse_request.LLMClient", return_value=client),
            patch("src.orchestrator.nodes.parse_request.PROMPT_TEMPLATES", self._prompts()),
        ):
            result = await parse_request(state)

        assert result["merge_scope"] == {
            "active": True,
            "subject": "Nova",
            "scope_type": "company",
        }
