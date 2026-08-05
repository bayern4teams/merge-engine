"""
Consistency tests between entity_types.CANONICAL_ENTITY_TYPES and
entity_aliases.ALIAS_GROUPS, plus regression coverage for the real destination
table names (Global Warehouse) each concept must resolve to.

Added 2026-07-30 as part of remediation #1 (breadth) in
merge-engine-remediation-1-to-6 (project memory) — these two structures used to
drift apart silently (e.g. "technology" and "skill" were conflated into one alias
group, and finance/industry/technology_category/company_relation had no alias
group at all, which is why the heuristic table-selection floor could never find
CompanyFinancials no matter what the LLM proposed).
"""

from __future__ import annotations

import pytest

from src.orchestrator.utils.entity_aliases import ALIAS_GROUPS, _group_of, concept_matches
from src.orchestrator.utils.entity_types import (
    CANONICAL_ENTITY_TYPES,
    ORIGINAL_SEVEN,
    WIDENED_TYPES,
)

# person/company/product already had groups before the widening; order/account/
# country are generic concepts carried forward for schema-agnostic compatibility
# but have no table (and therefore no alias group) in this destination's schema —
# see the docstring in entity_types.py.
_EXPECTED_GROUPED = set(WIDENED_TYPES) | {"person", "company", "product", "contract"}
_EXPECTED_UNGROUPED = {"country", "account", "order"}


class TestAliasGroupCoverage:
    def test_every_widened_type_has_exactly_one_alias_group(self):
        for concept in _EXPECTED_GROUPED:
            group = _group_of(concept)
            assert group is not None, f"{concept!r} has no alias group at all"

    def test_generic_untabled_types_have_no_group_by_design(self):
        # Not a bug -- documents the deliberate scoping decision so a future
        # change that silently adds/removes one of these is forced to touch
        # this test and explain why.
        for concept in _EXPECTED_UNGROUPED:
            assert _group_of(concept) is None

    def test_canonical_types_partition_correctly(self):
        assert set(CANONICAL_ENTITY_TYPES) == set(ORIGINAL_SEVEN) | set(WIDENED_TYPES)
        assert set(ORIGINAL_SEVEN) & set(WIDENED_TYPES) == set()

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
