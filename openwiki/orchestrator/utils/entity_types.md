---
type: data model
title: Entity Types
description: Canonical entity type definitions used throughout merge-engine. Defines the universe of supported business entity types and their metadata.
tags: [entity-types, taxonomy, data-model]
---

# Entity Types

## Purpose

Defines the canonical entity type universe that merge-engine supports. These types are used for:
- Request parsing (LLM maps natural language to canonical types)
- Entity table selection (LLM picks tables for each type)
- Alias matching (table names are mapped to canonical types)
- UI rendering (consistent labels and grouping)

## File

`src/orchestrator/utils/entity_types.py`

## Canonical Types

The supported canonical entity types are:

```python
ENTITY_TYPES = [
    "company",
    "person",
    "product",
    "location",
    "project",
    "employment",
    # ... additional types as needed
]
```

Each type has:
- A canonical name (snake_case)
- A display label (e.g., "Company" → "Companies")
- Associated alias groups (from `entity_aliases.py`)

## Entity Type Metadata

Some entity types carry additional metadata:

| Type | Plural Form | Description |
|------|-------------|-------------|
| `company` | Companies | Business organizations |
| `person` | People | Individual humans (customers, employees, contacts) |
| `product` | Products | Goods or services |
| `location` | Locations | Geographic places, addresses |
| `project` | Projects | Business projects, initiatives |
| `employment` | Employment | Employment relationships (junction between person and company) |

## Test Coverage

- `tests/unit/test_entity_aliases.py` — covers entity type alias resolution
