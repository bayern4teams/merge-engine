"""
Canonical entity-type vocabulary for the merge-engine.

This is the single source of truth for "what kinds of things can a merge request
be about." It is consumed by:
  - `src/llm/prompts/request_parsing.system.txt` (the enum the LLM must choose
    `entity_types`/`explicit_entity_types` from — kept in sync by hand since the
    prompt is plain text, not templated from this file; `test_entity_aliases.py`
    is the guard against the two drifting apart)
  - `src/orchestrator/utils/entity_aliases.py` (`ALIAS_GROUPS`, the heuristic
    table-name matcher — every type below has exactly one alias group)

Widened 2026-07-30 (remediation #1 in `merge-engine-remediation-1-to-6`, project
memory) from an original fixed 7-type enum that capped "all information about X"
to company/person/product and left 8 of an 11-table-family destination schema
permanently unreachable — see `docs/diagnostics/BOEING_APP_VS_MANUAL_DIAGNOSIS.md` and its
Airbus/Lockheed siblings for the live evidence (identical root cause on all 3
real-world subjects).

Deliberately NOT included: founders and investors/investment-rounds. Boeing's and
Airbus's origin1 data has rich `CompanyInvestor`/`CompanyInvestmentRound` tables,
but the target `global_warehouse` destination schema has no equivalent table at
all (confirmed against its 25-table DDL) — representing them would mean inventing
new destination tables, not widening which existing tables the enum can reach.
That is a bigger architectural step than this fix and is left as a known,
documented gap rather than silently attempted.
"""

from __future__ import annotations

# canonical identifier -> short description. The description text mirrors (and
# must stay consistent with) the enum documentation in
# request_parsing.system.txt's "### ALLOWED ENTITY TYPES" block.
CANONICAL_ENTITY_TYPES: dict[str, str] = {
    # Original 7 — generic concepts that don't necessarily map to a table in
    # every destination (kept for backward compatibility with other schemas).
    "person": "individuals, employees, contacts, users, customers, staff",
    "company": "organizations, businesses, corporations, firms, enterprises",
    "product": "items, goods, services, SKUs, inventory",
    "country": "nations, territories, regions",
    "address": "locations, places, physical addresses",
    "account": "financial accounts, customer accounts, user accounts",
    "order": "orders, transactions, purchases, invoices",
    # Widened 2026-07-30 — map to real Global Warehouse table families that were
    # previously unreachable because no entity type could select them.
    "finance": "revenue, budgets, currency rates, financial history",
    "industry": "industry/sector classifications",
    "location": "addresses, offices, facilities tied to a company or person",
    "experience": "employment history, roles, tenures",
    "education": "schools, degrees, certifications",
    "skill": "skills and competencies (including person-skill links)",
    "technology": "technologies/tools a company uses",
    "technology_category": "groupings of technologies",
    "contact": "emails, phone numbers, social handles",
    "company_relation": "relationships between companies (parent/subsidiary, partner, competitor, etc.)",
    "contract": "contracts and their amendments, milestones, periods, and line-item products",
}

# The types that existed before the 2026-07-30 widening — kept as a named constant
# so tests and prompt-golden updates can distinguish "the original cap" from "what
# the widening added" without re-deriving the split from prose.
ORIGINAL_SEVEN: tuple[str, ...] = (
    "person",
    "company",
    "product",
    "country",
    "address",
    "account",
    "order",
)

WIDENED_TYPES: tuple[str, ...] = tuple(t for t in CANONICAL_ENTITY_TYPES if t not in ORIGINAL_SEVEN)

# "All information about <Company>" should expand to every concept that can
# plausibly describe a company and the people/data attached to it — i.e.
# everything except the domain-generic, clearly-unrelated-to-a-company concepts
# (country/account/order describe other subjects entirely). This is what
# request_parsing.system.txt's "Company Wildcards" rule expands to; kept here so
# the prompt text and the enum can be cross-checked by test.
COMPANY_WILDCARD_TYPES: tuple[str, ...] = tuple(
    t for t in CANONICAL_ENTITY_TYPES if t not in ("country", "account", "order")
)
