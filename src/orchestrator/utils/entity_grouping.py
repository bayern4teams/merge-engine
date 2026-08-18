"""
Deterministic table-to-entity grouping for the merge-engine.

The 4th live run against real ClickHouse origins (Bugs/Logs/
4th_try_05_18_local_enrichprompt.txt) showed `parse_request` picking 6 entity
types covering 13 of 20 discovered tables — the LLM simply never mentioned
CompanyExperience, CompanyIndustry, CompanyRelation, IndustryClassification,
Experience, Industry, or CompanyIndustry (origin_2), and nothing checked that
every discovered table got assigned somewhere. This module removes that
failure mode structurally: `group_tables_into_entities` is a pure function
with no LLM call that assigns EVERY discovered table to exactly one group.
`parse_request` (Phase 3.2) uses these groups as the authoritative
membership; the LLM is only consulted to pick a human-readable name per
group, and cannot add or remove a table from one.

Reuses `entity_aliases.ALIAS_GROUPS`/`concept_matches` as the single
vocabulary source rather than introducing a fourth one — see that module's
own docstring and `src/orchestrator/utils/entity_types.py`'s removal (Phase
3.1) for the three/four vocabularies this collapses.
"""

from __future__ import annotations

import re
from typing import Any

from src.orchestrator.utils.entity_aliases import _group_of

# A tiny set of irregular plurals the trailing-s/-es heuristic below gets
# wrong. Deliberately small — anything not listed here just isn't
# singularised, which only means it keeps its own group (safe: the
# invariant this module exists to guarantee is "never orphaned", not
# "perfectly singularised").
_IRREGULAR_PLURALS = {"people": "person", "categories": "category"}

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _split_words(name: str) -> list[str]:
    """Split a table name into lowercase word tokens, whatever its casing.

    "CompanyExperience" -> ["company", "experience"]
    "IndustryClassification" -> ["industry", "classification"]
    "contact_email" -> ["contact", "email"]
    "technologic" -> ["technologic"]
    """
    if "_" in name:
        return [w.lower() for w in name.split("_") if w]
    if "-" in name:
        return [w.lower() for w in name.split("-") if w]
    parts = _CAMEL_BOUNDARY.split(name)
    return [w.lower() for w in parts if w]


def _singularise(word: str) -> str:
    """Best-effort singular form. Deliberately conservative — see
    _IRREGULAR_PLURALS."""
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("es") and len(word) > 2 and word[-3] in "sxzh":
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 1:
        return word[:-1]
    return word


def _normalise_table_name(table_name: str) -> str:
    """The table's own name reduced to a snake_case, singularised key, with
    no cross-table reasoning — the fallback when no owning-entity prefix
    can be safely stripped (see _strip_owning_prefix)."""
    return "_".join(_singularise(w) for w in _split_words(table_name))


def _strip_owning_prefix(table_name: str) -> str | None:
    """If `table_name` reads as "<OwningEntity><Concept>" and the concept
    part is a recognised business concept in its own right (a member of
    some entity_aliases.ALIAS_GROUPS set), return the concept part alone.
    Otherwise return None — the whole name stays one compound group.

    This is deliberately NOT "strip any prefix that is itself a known
    table" — that rule strips "Industry" from "IndustryClassification"
    exactly as readily as it strips "Company" from "CompanyExperience",
    which would wrongly merge a taxonomy/lookup table into the same group
    as the company-industry link table. Gating the strip on the REMAINDER
    being a recognised concept (not the prefix being a known table) is what
    tells "Experience"/"Industry"/"Contact" (real business concepts, so
    CompanyExperience/CompanyIndustry/PersonContact safely collapse into
    them) apart from "Classification" (not a concept in its own right, so
    IndustryClassification stays its own group).
    """
    words = _split_words(table_name)
    if len(words) < 2:
        return None
    remainder = "_".join(_singularise(w) for w in words[1:])
    remainder_single_token = _singularise(words[-1]) if len(words) == 2 else None
    if _group_of(remainder) is not None:
        return remainder
    if remainder_single_token is not None and _group_of(remainder_single_token) is not None:
        return remainder_single_token
    return None


def _table_key(table_name: str) -> tuple[str, bool]:
    """The grouping key for one table name, and whether it is eligible for
    alias-group merging with other tables' keys.

    Eligible when either (a) an owning-entity prefix was stripped — the
    remainder is then guaranteed to be a recognised concept, since
    _strip_owning_prefix only returns non-None in that case — or (b) the
    table name needed no compounding at all (a single word, e.g. bare
    "Experience" or "Industry"), so the key is exactly that word.

    NOT eligible for a genuine multi-word fallback compound (2+ words that
    could not be split via a recognised-concept remainder, e.g.
    "CompanyEmployeeCategory" -> "company_employee_category"). This
    distinction is load-bearing: entity_aliases.ALIAS_GROUPS' industry group
    already contains the literal (underscored) alias
    "industry_classification" — added there for destination-table matching,
    a different purpose than this module's — so checking group membership
    on THAT fallback key would silently merge "IndustryClassification" into
    "industry", exactly the over-merge _strip_owning_prefix's own gate
    exists to prevent. A genuine multi-word fallback compound is always its
    own standalone group; a single bare word is not a "compound" at all, so
    this exception does not apply to it.
    """
    stripped = _strip_owning_prefix(table_name)
    if stripped is not None:
        return stripped, True
    key = _normalise_table_name(table_name)
    return key, "_" not in key


def group_tables_into_entities(
    schemas: dict[str, Any],
) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    """Assign EVERY discovered table across all origins to exactly one
    entity group, deterministically, with no LLM involved.

    Parameters
    ----------
    schemas : dict
        The ``schemas`` state key: ``{origin_id: {"tables": [{"name",
        "columns": [...], ...}]}}`` as produced by ``discover_schemas``.

    Returns
    -------
    (entity_tables, unassigned)
        ``entity_tables`` is ``{group_key: [{"origin_id", "table", "full_name"},
        ...]}`` — ``table`` is the bare name (matching ``discover_schemas``'
        own ``"name"`` field and every downstream consumer keyed on it);
        ``full_name`` is the schema-qualified name, carried for consumers
        that need to disambiguate (e.g. two PostgreSQL schemas each with
        their own ``Company`` table). ``unassigned`` is always ``[]`` on a
        successful return — this function raises ``ValueError`` rather than
        returning a non-empty list, since an unassigned table is a genuine
        data problem (an unnamed table, or a name that reduces to an empty
        key), not something a caller can silently work around.

    Raises
    ------
    ValueError
        If any discovered table could not be assigned a group key.
    """
    # Two tables merge into the same group if their computed keys are
    # textually equal, OR both keys came from a successful owning-prefix
    # strip AND entity_aliases recognises them as members of the same alias
    # group (so a table normalising to "job" and one normalising to
    # "experience" still land together, since both are in ALIAS_GROUPS'
    # employment/job/position set). A fallback (unstripped) key is never
    # subject to alias-group merging — see _table_key's docstring for why.
    group_key_by_canonical: dict[Any, str] = {}
    entity_tables: dict[str, list[dict[str, str]]] = {}
    unassigned: list[str] = []

    for origin_id, schema_data in sorted((schemas or {}).items()):
        for i, table in enumerate((schema_data or {}).get("tables", []) or []):
            table_name = (table or {}).get("name", "")
            if not table_name:
                # The one case the invariant below exists to catch: record it
                # rather than silently `continue`-ing past it. There is no
                # table identity to key a fallback group on, so this table is
                # genuinely unrecoverable — callers must treat it as a bug in
                # discover_schemas, not something this function can group.
                unassigned.append(f"{origin_id}:<unnamed table #{i}>")
                continue
            full_name = (table or {}).get("full_name") or table_name

            key, eligible_for_alias_merge = _table_key(table_name)
            if not key:
                unassigned.append(f"{origin_id}:{table_name}")
                continue
            group = _group_of(key) if eligible_for_alias_merge else None
            # ALIAS_GROUPS' sets aren't hashable themselves, but `id()` of
            # the same module-level set object is stable and unique across
            # calls — exactly the merge-key we want: two different literal
            # strings that both resolve to the SAME ALIAS_GROUPS set must
            # produce the same dict entry.
            canonical: Any = id(group) if group is not None else key

            if canonical not in group_key_by_canonical:
                group_key_by_canonical[canonical] = key
            resolved_key = group_key_by_canonical[canonical]

            # `table` is deliberately the BARE name, matching discover_schemas'
            # own "name" field and every downstream consumer that keys off it
            # (extract_data._candidates_from_selection, analyze_dependencies.
            # _build_coverage_matrix, map_fields_node._build_source_schema_
            # from_discovered, human_nodes._build_field_provenance). A prior
            # version of this function stored `full_name` here instead (e.g.
            # "public.Company"), which matched nothing downstream and made
            # parse_request's own orphan post-condition fire on every
            # PostgreSQL-origin run. `full_name` is carried alongside for any
            # consumer that specifically needs a schema-qualified name.
            entity_tables.setdefault(resolved_key, []).append(
                {"origin_id": origin_id, "table": table_name, "full_name": full_name}
            )

    # The invariant this whole module exists to guarantee. If this ever
    # fires, it is a genuine data problem (an unnamed table, or a table name
    # that reduces to an empty key) — not a caller-fixable code path, so
    # callers should raise rather than silently drop rows. Not an `assert`:
    # this must still fire under `python -O`.
    if unassigned:
        raise ValueError(f"Orphaned tables in group_tables_into_entities: {unassigned}")

    return entity_tables, unassigned
