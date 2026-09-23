"""Unit tests for V3 semantic type inference (Part B, P-4).

The heuristic (no LLM) is the primary thing under test: it must use BOTH
the column name and the measured P-3 profile, and a name that disagrees
with the measured values must not win blindly. The LLM fallback is tested
against a mocked LLMClient -- only reached when the heuristic itself is not
confident, per the module's own threshold.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.mcp_server.tools.semantic_type_inference import (
    SEMANTIC_TYPES,
    _normalize_column_name,
    infer_semantic_type,
    infer_semantic_type_heuristic,
)


class TestNormalizeColumnName:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("companyID", "company_id"),
            ("CompanyId", "company_id"),
            ("company_id", "company_id"),
            ("createdAt", "created_at"),
            ("fullName", "full_name"),
            ("email", "email"),
        ],
    )
    def test_every_naming_convention_normalizes_the_same_way(self, raw: str, expected: str) -> None:
        assert _normalize_column_name(raw) == expected


class TestHeuristicNameOnly:
    def test_email_shaped_name_with_no_profile_gets_a_confident_but_uncorroborated_score(
        self,
    ) -> None:
        result = infer_semantic_type_heuristic("email", "text", None)
        assert result["semantic_type"] == "EMAIL"
        assert result["confidence"] == pytest.approx(0.75)

    def test_camelcase_company_id_is_recognized_as_foreign_key_not_organization(self) -> None:
        # "company" alone would suggest ORGANIZATION_NAME, but "_id" is the
        # more specific, higher-priority rule for an FK-shaped column name.
        result = infer_semantic_type_heuristic("companyID", "text", None)
        assert result["semantic_type"] == "FOREIGN_KEY"

    def test_bare_id_is_identifier_not_foreign_key(self) -> None:
        result = infer_semantic_type_heuristic("id", "text", None)
        assert result["semantic_type"] == "IDENTIFIER"

    def test_no_name_match_and_no_profile_is_unknown(self) -> None:
        result = infer_semantic_type_heuristic("xyz123abc", "text", None)
        assert result["semantic_type"] == "UNKNOWN"
        assert result["confidence"] == 0.0


class TestHeuristicWithProfileCorroboration:
    def test_email_name_corroborated_by_email_shaped_values_is_high_confidence(self) -> None:
        profile = {"pattern_distribution": {"email": 95, "other": 5}}
        result = infer_semantic_type_heuristic("contact_email", "text", profile)
        assert result["semantic_type"] == "EMAIL"
        assert result["confidence"] > 0.85

    def test_email_name_contradicted_by_uuid_shaped_values_is_not_trusted(self) -> None:
        """The whole point of using measured evidence: a misleading column
        name must not win over what the data actually looks like."""
        profile = {"pattern_distribution": {"uuid": 100}}
        result = infer_semantic_type_heuristic("email", "text", profile)
        assert result["semantic_type"] == "UNKNOWN"
        assert result["confidence"] == 0.0

    def test_no_name_match_but_dominant_uuid_pattern_infers_identifier(self) -> None:
        profile = {"pattern_distribution": {"uuid": 98, "other": 2}}
        result = infer_semantic_type_heuristic("ref_code", "text", profile)
        assert result["semantic_type"] == "IDENTIFIER"
        assert result["signals"]["dominant_pattern"] == "uuid"

    def test_no_name_match_and_no_dominant_pattern_is_unknown(self) -> None:
        profile = {"pattern_distribution": {"alpha": 30, "numeric": 30, "other": 40}}
        result = infer_semantic_type_heuristic("misc_field", "text", profile)
        assert result["semantic_type"] == "UNKNOWN"

    def test_a_bare_name_column_is_not_overconfident_about_person_vs_organization(self) -> None:
        """Live-caught (BP-B, a real ClickHouse Company table): a bare
        'name' column measured as ~100% alpha-shaped text was boosted to
        0.93 confidence for PERSON_NAME. But PERSON_NAME and
        ORGANIZATION_NAME both corroborate on the exact same 'alpha'
        pattern bucket -- alpha-shaped text is equally consistent with
        either, so the measurement cannot actually discriminate between
        them, and confidence must not be boosted as if it could."""
        profile = {"pattern_distribution": {"alpha": 98, "other": 2}}
        result = infer_semantic_type_heuristic("name", "String", profile)
        assert result["semantic_type"] == "PERSON_NAME"
        assert result["confidence"] <= 0.75

    def test_an_ambiguous_pattern_can_still_rule_a_name_match_out(self) -> None:
        """Even though 'alpha' can't confirm PERSON_NAME over
        ORGANIZATION_NAME, a near-total MISMATCH (values that are not
        alpha-shaped at all) still means the name is misleading."""
        profile = {"pattern_distribution": {"numeric": 100}}
        result = infer_semantic_type_heuristic("name", "String", profile)
        assert result["semantic_type"] == "UNKNOWN"

    def test_signals_always_explain_the_decision(self) -> None:
        """A human reviewing a NEEDS_REVIEW row must be able to see the
        reasoning without re-running anything."""
        profile = {"pattern_distribution": {"email": 90, "other": 10}}
        result = infer_semantic_type_heuristic("contact_email", "text", profile)
        assert result["signals"]["name_match"] == "EMAIL"
        assert result["signals"]["expected_pattern"] == "email"
        assert result["signals"]["corroboration_ratio"] == pytest.approx(0.9)


class TestSemanticTypesEnum:
    def test_every_heuristic_output_is_a_valid_semantic_type(self) -> None:
        for column_name in ("email", "companyID", "id", "xyz_nonsense_123"):
            result = infer_semantic_type_heuristic(column_name, "text", None)
            assert result["semantic_type"] in SEMANTIC_TYPES


class TestInferSemanticTypeOrchestration:
    @pytest.mark.asyncio
    async def test_a_confident_heuristic_result_never_calls_the_llm(self) -> None:
        profile = {"pattern_distribution": {"email": 100}}
        with patch(
            "src.mcp_server.tools.semantic_type_inference._infer_with_llm",
            new=AsyncMock(side_effect=AssertionError("LLM must not be called")),
        ):
            result = await infer_semantic_type(
                "contact_email", "text", profile, llm_config={"provider": "openai"}
            )
        assert result["semantic_type"] == "EMAIL"
        assert result["origin"] == "ALGORITHMIC"

    @pytest.mark.asyncio
    async def test_an_unconfident_heuristic_result_falls_through_to_the_llm(self) -> None:
        with patch(
            "src.mcp_server.tools.semantic_type_inference._infer_with_llm",
            new=AsyncMock(
                return_value={
                    "semantic_type": "DESCRIPTION_TEXT",
                    "confidence": 0.7,
                    "signals": {"reason": "long free-text values"},
                }
            ),
        ) as mock_llm:
            result = await infer_semantic_type(
                "xyz_nonsense_123", "text", None, llm_config={"provider": "openai"}
            )
        mock_llm.assert_called_once()
        assert result["semantic_type"] == "DESCRIPTION_TEXT"
        assert result["origin"] == "LLM_ASSISTED"

    @pytest.mark.asyncio
    async def test_no_llm_config_never_calls_the_llm_even_when_unconfident(self) -> None:
        with patch(
            "src.mcp_server.tools.semantic_type_inference._infer_with_llm",
            new=AsyncMock(side_effect=AssertionError("LLM must not be called")),
        ):
            result = await infer_semantic_type("xyz_nonsense_123", "text", None, llm_config=None)
        assert result["semantic_type"] == "UNKNOWN"
        assert result["origin"] == "ALGORITHMIC"

    @pytest.mark.asyncio
    async def test_llm_failure_keeps_the_heuristic_result_not_a_crash(self) -> None:
        with patch(
            "src.mcp_server.tools.semantic_type_inference._infer_with_llm",
            new=AsyncMock(return_value=None),
        ):
            result = await infer_semantic_type(
                "xyz_nonsense_123", "text", None, llm_config={"provider": "openai"}
            )
        assert result["semantic_type"] == "UNKNOWN"
        assert result["origin"] == "ALGORITHMIC"
