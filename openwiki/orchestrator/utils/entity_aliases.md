---
type: utility
title: Entity Aliases
description: Business concept to table-name matching. Maps canonical entity types (company, person, product, etc.) to their possible table names via alias groups. Used by dependency analysis and load_data table resolution.
tags: [entity-aliases, matching, taxonomy]
---

# Entity Aliases

## Purpose

Maps canonical entity types to their possible table names via alias groups. Used by `analyze_dependencies` to determine which source tables hold each entity, and by `load_data` to resolve destination table names.

## File

`src/orchestrator/utils/entity_aliases.py`

## Alias Groups

Maps each canonical entity type to a set of possible table name variants:

```python
ENTITY_ALIASES = {
    "company": {
        "company",
        "companies",
        "corporation",
        "corporations",
        "firm",
        "firms",
        "enterprise",
        "enterprises",
    },
    "person": {
        "person",
        "people",
        "user",
        "users",
        "customer",
        "customers",
        "contact",
        "contacts",
        "employee",
        "employees",
    },
    "product": {"product", "products", "item", "items", "sku", "skus"},
    "location": {"location", "locations", "place", "places", "address", "addresses"},
    # ... etc.
}
```

## Key Functions

| Function | Purpose |
|----------|---------|
| `concept_matches(entity_type, table_name)` | Check if a table name matches a canonical entity type |
| `resolve_dest_table(entity_type, dest_schema)` | Resolve a destination table name for an entity type |

## concept_matches(entity_type, table_name)

Checks if `table_name` (lowercased) is in the alias group for `entity_type`. Case-insensitive matching.

## resolve_dest_table(entity_type, dest_schema)

Resolves a destination table name by:
1. Checking if the entity type name itself is a table in the schema
2. Checking if any table in the schema matches the entity's alias group
3. Returning the first match or `None`

## Test Coverage

- `tests/unit/test_entity_aliases.py` — alias matching correctness

## Cross-References

| Concern | Page |
|---------|------|
| Dependency analysis | [Analyze Dependencies](/openwiki/orchestrator/nodes/analyze_dependencies.md) |
| Load data | [Load Data](/openwiki/orchestrator/nodes/load_data.md) |
