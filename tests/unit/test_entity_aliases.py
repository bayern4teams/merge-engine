"""
Regression coverage for entity_aliases.ALIAS_GROUPS: the invariant that no
alias appears in two groups, plus real destination table names (Global
Warehouse) each concept must resolve to.

Added 2026-07-30 as part of remediation #1 (breadth) in
merge-engine-remediation-1-to-6 (project memory) — "technology" and "skill"
were once conflated into one alias group, and finance/industry/
technology_category/company_relation had no alias group at all, which is
why the heuristic table-selection floor could never find CompanyFinancials
no matter what the LLM proposed.

Originally also checked consistency against entity_types.CANONICAL_ENTITY_TYPES,
a fixed enum with zero production importers (only this test ever imported it —
the enum it mirrored in request_parsing.system.txt was itself removed by the
schema-grounded parse_request rewrite). Phase 3.1 of the fix-bugs follow-up
plan deleted entity_types.py: ALIAS_GROUPS is now consulted directly by
entity_grouping.py (see tests/unit/test_entity_grouping.py), which derives
its vocabulary from what's actually discovered rather than a static list, so
there is no longer a second structure for this file to check ALIAS_GROUPS
against.
"""

from __future__ import annotations

import pytest

from src.orchestrator.utils.entity_aliases import ALIAS_GROUPS, concept_matches


class TestAliasGroupCoverage:
    def test_no_alias_string_appears_in_two_groups(self):
        seen: dict[str, set[str]] = {}
        for group in ALIAS_GROUPS:
            for alias in group:
                if alias in seen and seen[alias] is not group:
                    pytest.fail(
                        f"alias {alias!r} appears in more than one ALIAS_GROUPS "
                        f"set -- concept_matches() would treat them as the same "
                        f"concept, which is exactly the technology/skill bug "
                        f"this test guards against."
                    )
                seen[alias] = group

    def test_technology_and_skill_are_distinct_concepts(self):
        # The original bug: {"technology", ..., "skill", ...} was one set, so
        # these returned True. They must not be the same concept.
        assert not concept_matches("technology", "skill")
        assert not concept_matches("skill", "technology")


class TestRealTableNameResolution:
    """Every widened concept must resolve against the ACTUAL Global Warehouse
    table names (see sample_databases/destination/sql/*.sql), not just an
    idealized English word."""

    @pytest.mark.parametrize(
        "concept,table_name",
        [
            ("finance", "finance"),
            ("finance", "finance_budget"),
            ("finance", "finance_currency_rate"),
            ("industry", "industry"),
            ("location", "location"),
            ("experience", "experience"),
            ("education", "education"),
            ("skill", "skill"),
            ("skill", "person_skill"),
            ("technology", "technologic"),
            ("technology_category", "technologic_category"),
            ("contact", "contact_email"),
            ("contact", "contact_phone"),
            ("contact", "contact_social"),
            ("company_relation", "company_relation"),
            ("contract", "contract"),
            ("contract", "contract_amendment"),
            ("contract", "contract_milestone"),
            ("contract", "contract_period"),
            ("contract", "contract_product"),
            ("company", "company"),
            ("person", "person"),
            ("product", "product"),
            ("product", "product_category"),
        ],
    )
    def test_concept_matches_real_table(self, concept, table_name):
        assert concept_matches(concept, table_name), (
            f"concept {concept!r} does not match real destination table "
            f"{table_name!r} -- this table would silently never be selected"
        )

    def test_technologic_is_not_matched_by_skill(self):
        # Regression guard for the specific conflation bug: the real table is
        # named "technologic" (not "technology"), and before this fix it wasn't
        # matched by "technology" at all, nor should it ever be matched by "skill".
        assert not concept_matches("skill", "technologic")
