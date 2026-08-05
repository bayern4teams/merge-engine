"""
Concept ↔ table-name matching helpers shared by pipeline nodes.

The LLM parses user requests into abstract entity concepts ("company",
"person"), while schema discovery and extraction operate on concrete
table names that differ per origin ("companies", "organizations").
These helpers bridge the two vocabularies with plural handling and
business-domain alias groups.
"""

from __future__ import annotations

# Names within a group refer to the same business concept.
#
# Widened 2026-07-30 (remediation #1 in merge-engine-remediation-1-to-6, project
# memory): added groups for finance/industry/technology_category/company_relation
# (previously absent entirely, so the heuristic floor could never find
# CompanyFinancials/CompanyIndustry/etc. no matter what the LLM proposed), and
# split "technology" and "skill" out of a single conflated group (they used to
# share one set, meaning `concept_matches("technology", "skill")` was True —
# wrong, those are two different destination tables). Also added the
# destination's actual (non-standard) "technologic"/"technologic_category"
# spellings as aliases, since neither matched the natural-English "technology"
# spelling before. See src/orchestrator/utils/entity_types.py for the canonical
# entity-type list every group here should correspond to.
ALIAS_GROUPS: list[set[str]] = [
    {
        "company",
        "companies",
        "organization",
        "organizations",
        "org",
        "orgs",
        "firm",
        "firms",
        "business",
        "businesses",
    },
    {"person", "persons", "people", "staff", "individual", "individuals", "employee", "employees"},
    {"product", "products", "product_catalog", "catalog", "item", "items"},
    {"contract", "contracts", "deal", "deals", "agreement", "agreements"},
    {"founder", "founders", "org_founder", "org_founders", "cofounder", "cofounders"},
    {
        "investment_round",
        "investment_rounds",
        "funding_round",
        "funding_rounds",
        "investment",
        "investments",
        "funding",
        "round",
        "rounds",
    },
    {"location", "locations", "site", "sites", "address", "addresses", "place", "places"},
    {
        "contact",
        "contacts",
        "contact_point",
        "contact_points",
        "contact_info",
        "contact_information",
    },
    {
        "employment",
        "employments",
        "employment_history",
        "job",
        "jobs",
        "position",
        "positions",
        "experience",
        "experiences",
        "work_history",
    },
    {
        "education",
        "educations",
        "education_record",
        "education_records",
        "school",
        "schools",
        "degree",
        "degrees",
    },
    {"technology", "technologies", "tech", "technologic", "technologics"},
    {"skill", "skills", "competency", "competencies", "person_skill", "person_skills"},
    {
        "technology_category",
        "technology_categories",
        "tech_category",
        "tech_categories",
        "technologic_category",
        "technologic_categories",
    },
    {
        "finance",
        "finances",
        "financial",
        "financials",
        "companyfinancials",
        "revenue",
        "earnings",
        "budget",
        "budgets",
        "finance_budget",
        "currency_rate",
        "currency_rates",
        "finance_currency_rate",
    },
    {
        "industry",
        "industries",
        "companyindustry",
        "sector",
        "sectors",
        "industry_classification",
        "industryclassification",
    },
    {
        "company_relation",
        "company_relations",
        "companyrelation",
        "relation",
        "relations",
        "affiliation",
        "affiliations",
    },
]


def _norm(name: str) -> str:
    """Normalize a table/concept name: lowercase, strip schema prefix."""
    return name.lower().split(".")[-1].strip()


def _group_of(name: str) -> set[str] | None:
    n = _norm(name)
    for group in ALIAS_GROUPS:
        if n in group:
            return group
    return None


def concept_matches(concept: str, name: str) -> bool:
    """True when a table name plausibly holds records of the concept.

    Also matches compound/prefixed table names like "contact_email" or
    "contract_amendment" against their base concept ("contact", "contract") by
    splitting on "_" and checking each token — added 2026-07-30 (remediation #1,
    merge-engine-remediation-1-to-6 in project memory) after finding this
    fallback only existed in two of the ~5 call sites (extract_data.py's
    `_table_matches_entity`, select_entity_tables.py's `heuristic_candidates`),
    so `resolve_dest_table` (used by workflow_manager.py, resolve_entities.py,
    load_data.py, map_fields_node.py) silently could not find e.g.
    "contact_email" for concept "contact" at all. Centralizing it here fixes
    every call site at once instead of duplicating the fallback a third time.
    """
    c, n = _norm(concept), _norm(name)
    if c == n:
        return True
    # Simple singular/plural variants
    if n in (c + "s", c + "es") or c in (n + "s", n + "es"):
        return True
    gc, gn = _group_of(c), _group_of(n)
    if gc is not None and gc is gn:
        return True
    # Compound name fallback: "contact_email" -> tokens ["contact", "email"];
    # match if the concept equals/aliases any token (but not vice versa -- a
    # concept named "contact_info" shouldn't be split, only the table name is).
    # Gated on the WHOLE name not already being a recognized concept in its own
    # right: "org_founders" is itself a member of the founder group, and must
    # not ALSO token-match "company" just because "org" (one of its tokens)
    # happens to be a company alias -- a table that already has a precise
    # concept of its own never leaks into a different one via token-splitting.
    if "_" in n and gn is None:
        return any(concept_matches(concept, token) for token in n.split("_") if token)
    return False


def resolve_dest_table(concept: str, tables: list[str]) -> str | None:
    """Pick the destination table for a concept from available tables."""
    for t in tables:
        if _norm(t) == _norm(concept):
            return t
    for t in tables:
        if concept_matches(concept, t):
            return t
    return None
