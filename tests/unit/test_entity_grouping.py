"""Unit tests for the deterministic entity-grouping module (Phase 3.1).

Uses the exact table lists discovered against real ClickHouse origins in
the 4th live run (Bugs/Logs/4th_try_05_18_local_enrichprompt.txt, L165-170)
— 13 tables in origin_1, 7 in origin_2 — since that run is the concrete
proof issue 1 (experience/industry/company_relation silently dropped) was
never a prompt problem but a "nothing checked every table got assigned"
problem, and this module is what makes that structurally impossible.
"""

from __future__ import annotations

import pytest

from src.orchestrator.utils.entity_grouping import group_tables_into_entities


def _schemas_from(origin_1_tables: list[str], origin_2_tables: list[str]) -> dict:
    def _mk(name: str) -> dict:
        return {"name": name, "columns": [{"name": "id"}]}

    return {
        "origin_1": {"origin_type": "clickhouse", "tables": [_mk(t) for t in origin_1_tables]},
        "origin_2": {"origin_type": "clickhouse", "tables": [_mk(t) for t in origin_2_tables]},
    }


ORIGIN_1_TABLES = [
    "Company",
    "CompanyContact",
    "CompanyEmployeeCategory",
    "CompanyExperience",
    "CompanyFinancials",
    "CompanyIndustry",
    "CompanyInvestmentRound",
    "CompanyInvestor",
    "CompanyRelation",
    "CompanyTechnographics",
    "IndustryClassification",
    "Location",
    "Person",
]

ORIGIN_2_TABLES = [
    "Company",
    "CompanyIndustry",
    "Experience",
    "Industry",
    "Location",
    "Person",
    "PersonContact",
]


class TestFullCoverage:
    """The invariant this module exists to guarantee: no discovered table
    is ever silently dropped, unlike parse_request's old free-form LLM
    selection (6 of 20 tables assigned in the 4th live run)."""

    def test_every_discovered_table_is_assigned_exactly_once(self):
        schemas = _schemas_from(ORIGIN_1_TABLES, ORIGIN_2_TABLES)
        entity_tables, unassigned = group_tables_into_entities(schemas)

        assert unassigned == []

        assigned_pairs = [
            (e["origin_id"], e["table"]) for tables in entity_tables.values() for e in tables
        ]
        expected_pairs = {("origin_1", t) for t in ORIGIN_1_TABLES} | {
            ("origin_2", t) for t in ORIGIN_2_TABLES
        }
        assert set(assigned_pairs) == expected_pairs
        assert len(assigned_pairs) == len(expected_pairs), "a table was assigned more than once"

    def test_empty_schemas_yields_no_groups_and_no_orphans(self):
        entity_tables, unassigned = group_tables_into_entities({})
        assert entity_tables == {}
        assert unassigned == []


class TestFullNameIdentity:
    """Regression test for the bug that made parse_request crash on any
    PostgreSQL origin: a prior version of this function stored `full_name`
    (e.g. "public.Company") in the `table` key instead of the bare name,
    which matched nothing any downstream consumer (or parse_request's own
    orphan post-condition) was keyed on. `table` must stay the bare name;
    `full_name` is carried alongside for consumers that need it."""

    def test_table_key_is_bare_name_full_name_carried_separately(self):
        schemas = {
            "origin_1": {
                "origin_type": "postgres",
                "tables": [
                    {
                        "name": "Company",
                        "full_name": "public.Company",
                        "columns": [{"name": "id"}],
                    },
                    {
                        "name": "Person",
                        "full_name": "public.Person",
                        "columns": [{"name": "id"}],
                    },
                ],
            }
        }
        entity_tables, unassigned = group_tables_into_entities(schemas)
        assert unassigned == []

        all_entries = [e for tables in entity_tables.values() for e in tables]
        assigned_pairs = {(e["origin_id"], e["table"]) for e in all_entries}
        assert assigned_pairs == {("origin_1", "Company"), ("origin_1", "Person")}

        full_names_by_table = {e["table"]: e["full_name"] for e in all_entries}
        assert full_names_by_table == {
            "Company": "public.Company",
            "Person": "public.Person",
        }

    def test_unnamed_table_is_recorded_as_unassigned_and_raises(self):
        schemas = {
            "origin_1": {
                "origin_type": "postgres",
                "tables": [
                    {"name": "Company", "full_name": "public.Company", "columns": []},
                    {"name": "", "full_name": "public.<unnamed>", "columns": []},
                ],
            }
        }
        with pytest.raises(ValueError, match="Orphaned tables"):
            group_tables_into_entities(schemas)


class TestCrossOriginMerging:
    """The specific bug: origin_1's CompanyExperience/CompanyIndustry never
    joined origin_2's bare Experience/Industry, so the LLM had no signal
    they were the same concept and both got dropped from most runs."""

    def _groups(self):
        schemas = _schemas_from(ORIGIN_1_TABLES, ORIGIN_2_TABLES)
        entity_tables, _ = group_tables_into_entities(schemas)
        return entity_tables

    def test_company_experience_merges_with_bare_experience(self):
        groups = self._groups()
        matches = [
            g
            for g, tables in groups.items()
            if any(t["table"] == "CompanyExperience" for t in tables)
        ]
        assert len(matches) == 1
        group = matches[0]
        tables_in_group = {(t["origin_id"], t["table"]) for t in groups[group]}
        assert ("origin_1", "CompanyExperience") in tables_in_group
        assert ("origin_2", "Experience") in tables_in_group

    def test_company_industry_merges_with_bare_industry(self):
        groups = self._groups()
        matches = [
            g
            for g, tables in groups.items()
            if any(t["table"] == "Industry" and t["origin_id"] == "origin_2" for t in tables)
        ]
        assert len(matches) == 1
        group = matches[0]
        tables_in_group = {(t["origin_id"], t["table"]) for t in groups[group]}
        assert ("origin_1", "CompanyIndustry") in tables_in_group
        assert ("origin_2", "CompanyIndustry") in tables_in_group
        assert ("origin_2", "Industry") in tables_in_group

    def test_company_contact_and_person_contact_merge(self):
        """Both are "contact info" tables and the destination schema treats
        contact as one unified concept (contact_email/phone/social with no
        company-vs-person split) — merging them is correct, not incidental."""
        groups = self._groups()
        matches = [
            g for g, tables in groups.items() if any(t["table"] == "CompanyContact" for t in tables)
        ]
        assert len(matches) == 1
        group = matches[0]
        tables_in_group = {(t["origin_id"], t["table"]) for t in groups[group]}
        assert ("origin_1", "CompanyContact") in tables_in_group
        assert ("origin_2", "PersonContact") in tables_in_group

    def test_bare_company_tables_merge_across_origins(self):
        groups = self._groups()
        matches = [
            g
            for g, tables in groups.items()
            if any(t["table"] == "Company" and t["origin_id"] == "origin_1" for t in tables)
        ]
        assert len(matches) == 1
        tables_in_group = {(t["origin_id"], t["table"]) for t in groups[matches[0]]}
        assert ("origin_1", "Company") in tables_in_group
        assert ("origin_2", "Company") in tables_in_group

    def test_bare_location_and_person_merge_across_origins(self):
        groups = self._groups()
        for table_name in ("Location", "Person"):
            matches = [
                g
                for g, tables in groups.items()
                if any(t["table"] == table_name and t["origin_id"] == "origin_1" for t in tables)
            ]
            assert len(matches) == 1, table_name
            tables_in_group = {(t["origin_id"], t["table"]) for t in groups[matches[0]]}
            assert (
                "origin_2",
                table_name,
            ) in tables_in_group, f"{table_name} did not merge across origins"


class TestDoesNotOverMerge:
    """The other half of correctness: a fallback compound name must never
    be swept into an unrelated alias group just because ALIAS_GROUPS
    happens to contain that literal compound string as an alias added for
    a different purpose (destination-table matching)."""

    def _groups(self):
        schemas = _schemas_from(ORIGIN_1_TABLES, ORIGIN_2_TABLES)
        entity_tables, _ = group_tables_into_entities(schemas)
        return entity_tables

    def test_industry_classification_stays_separate_from_industry(self):
        """entity_aliases.ALIAS_GROUPS' industry group literally contains
        the alias "industry_classification" (added for a different reason
        entirely) — a naive group_of() check on the fallback compound name
        would wrongly merge a taxonomy/lookup table into the same group as
        the company-industry junction table. It must not.
        """
        groups = self._groups()
        industry_group = next(
            g
            for g, tables in groups.items()
            if any(t["table"] == "CompanyIndustry" for t in tables)
        )
        classification_group = next(
            g
            for g, tables in groups.items()
            if any(t["table"] == "IndustryClassification" for t in tables)
        )
        assert industry_group != classification_group
        industry_tables = {t["table"] for t in groups[industry_group]}
        assert "IndustryClassification" not in industry_tables

    def test_company_technographics_does_not_merge_into_technology(self):
        """ "Technographics" (firmographic/segmentation data) is not the
        same concept as "technology" (tools a company uses), even though
        both start with "techno" — must not collide via substring luck."""
        groups = self._groups()
        matches = [
            g
            for g, tables in groups.items()
            if any(t["table"] == "CompanyTechnographics" for t in tables)
        ]
        assert len(matches) == 1
        assert matches[0] not in ("technology", "technologic")

    def test_company_investor_does_not_merge_with_investment_round(self):
        """Both origin from "Company" + investment-adjacent nouns, but an
        investor (a person/entity) and an investment round (an event) are
        different concepts — the grouper is conservative rather than
        guessing they're the same."""
        groups = self._groups()
        investor_group = next(
            g
            for g, tables in groups.items()
            if any(t["table"] == "CompanyInvestor" for t in tables)
        )
        round_group = next(
            g
            for g, tables in groups.items()
            if any(t["table"] == "CompanyInvestmentRound" for t in tables)
        )
        assert investor_group != round_group
