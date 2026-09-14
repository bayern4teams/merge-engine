"""Unit tests for post-merge consistency rule validation (audit §13 #9,
fix-v3 B.4.2). ADR 0021: no rule-engine dependency -- plain-Python clause
evaluation over rules-as-data.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.orchestrator.nodes.validate_graph_consistency import (
    check_consistency_rules,
    validate_graph_consistency,
)

_RULE = {
    "id": "acquired_no_late_investment",
    "when_predicate": "company.status",
    "when_value": "ACQUIRED",
    "compare_predicate": "company.last_investment_date",
    "compare_op": "after_when_valid_from",
    "severity": "warn",
}


class TestCheckConsistencyRules:
    def test_a_violating_fact_produces_one_finding(self) -> None:
        facts = {
            "e1": {
                "company.status": {"value": "ACQUIRED", "valid_from": "2020-01-01"},
                "company.last_investment_date": {"value": "2021-06-01", "valid_from": None},
            }
        }
        violations = check_consistency_rules(facts, [_RULE])
        assert len(violations) == 1
        assert violations[0]["rule_id"] == "acquired_no_late_investment"
        assert violations[0]["entity_id"] == "e1"
        assert violations[0]["severity"] == "warn"

    def test_a_clean_entity_produces_no_findings(self) -> None:
        facts = {
            "e1": {
                "company.status": {"value": "ACQUIRED", "valid_from": "2020-01-01"},
                "company.last_investment_date": {"value": "2019-06-01", "valid_from": None},
            }
        }
        assert check_consistency_rules(facts, [_RULE]) == []

    def test_entity_not_matching_when_value_is_skipped(self) -> None:
        facts = {
            "e1": {
                "company.status": {"value": "ACTIVE", "valid_from": "2020-01-01"},
                "company.last_investment_date": {"value": "2021-06-01", "valid_from": None},
            }
        }
        assert check_consistency_rules(facts, [_RULE]) == []

    def test_missing_compare_predicate_is_skipped_not_a_crash(self) -> None:
        facts = {"e1": {"company.status": {"value": "ACQUIRED", "valid_from": "2020-01-01"}}}
        assert check_consistency_rules(facts, [_RULE]) == []

    def test_no_rules_no_facts_is_a_noop(self) -> None:
        assert check_consistency_rules({}, []) == []


class TestValidateGraphConsistencyNode:
    @pytest.mark.asyncio
    async def test_missing_rule_file_logs_and_passes(self) -> None:
        with patch("src.orchestrator.nodes.validate_graph_consistency._RULES_PATH") as mock_path:
            mock_path.exists.return_value = False
            result = await validate_graph_consistency({"destination": {"type": "postgres"}})
        assert result == {}

    @pytest.mark.asyncio
    async def test_a_violating_fact_in_the_fake_store_becomes_one_red_flag(self) -> None:
        class _FakeConnector:
            def execute_sql(self, sql: str, params: Any = None) -> list[tuple]:
                return [
                    (
                        "e1",
                        "company.status",
                        None,
                        "ACQUIRED",
                        "2020-01-01",
                        None,
                        None,
                        "2020-01-01",
                    ),
                    (
                        "e1",
                        "company.last_investment_date",
                        None,
                        None,
                        "2021-06-01",
                        None,
                        None,
                        None,
                    ),
                ]

        state = {"destination": {"type": "postgres"}, "reconciliation_report": {}}
        with (
            patch(
                "src.orchestrator.nodes.validate_graph_consistency._load_consistency_rules",
                return_value=[_RULE],
            ),
            patch(
                "src.mcp_server.tools.load_data._get_destination_connector",
                return_value=_FakeConnector(),
            ),
            patch("src.orchestrator.nodes.load_data._write_run_report"),
        ):
            result = await validate_graph_consistency(state)

        red_flags = result["reconciliation_report"]["data_quality"]["red_flags"]
        assert any("acquired_no_late_investment" in f for f in red_flags)

    @pytest.mark.asyncio
    async def test_a_clean_load_in_the_fake_store_adds_no_red_flags(self) -> None:
        class _FakeConnector:
            def execute_sql(self, sql: str, params: Any = None) -> list[tuple]:
                return [
                    (
                        "e1",
                        "company.status",
                        None,
                        "ACTIVE",
                        None,
                        None,
                        None,
                        "2020-01-01",
                    ),
                ]

        state = {"destination": {"type": "postgres"}, "reconciliation_report": {}}
        with (
            patch(
                "src.orchestrator.nodes.validate_graph_consistency._load_consistency_rules",
                return_value=[_RULE],
            ),
            patch(
                "src.mcp_server.tools.load_data._get_destination_connector",
                return_value=_FakeConnector(),
            ),
        ):
            result = await validate_graph_consistency(state)

        assert result == {}
