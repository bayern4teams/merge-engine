---
type: llm prompts
title: Prompt Templates
description: >
  Prompt template system for merge-engine: six stages each with a (system,
  human) prompt pair loaded from files in src/llm/prompts/. Covers the
  templates, their purpose, and which orchestrator nodes consume them.
tags: [llm, prompts, templates, contract]
---

# Prompt Templates

## Purpose

All LLM interactions in merge-engine use prompt templates loaded from text
files at module import time. This makes prompts version-controlled, easily
editable, and testable as golden data.

## File

`src/llm/__init__.py` — loads templates from `src/llm/prompts/`

```python
_PROMPTS_DIR = Path(__file__).parent / "prompts"
_STAGE_NAMES = (
    "request_parsing",
    "dependency_analysis",
    "entity_table_selection",
    "scope_planning",
    "entity_resolution",
    "field_mapping",
)

PROMPT_TEMPLATES: dict[str, dict[str, str]] = {
    name: {
        "system": _read_prompt(f"{name}.system.txt"),
        "human": _read_prompt(f"{name}.human.txt"),
    }
    for name in _STAGE_NAMES
}
```

## Template Pattern

Each stage has a **(system, human)** pair:

- **System message** — carries the role, rules, and output contract
- **Human message** — carries few-shot examples plus the live input template

## The Six Stages

### 1. Request Parsing (`request_parsing.*`)

| File | Size |
|------|------|
| `request_parsing.system.txt` | 6,752 bytes |
| `request_parsing.human.txt` | 3,177 bytes |

Used by: `src/orchestrator/nodes/parse_request.py`

Purpose: Extract target entity types from natural language user requests.
Constrains the LLM to respond with one of the supported canonical entity types.

### 2. Dependency Analysis (`dependency_analysis.*`)

| File | Size |
|------|------|
| `dependency_analysis.system.txt` | 2,525 bytes |
| `dependency_analysis.human.txt` | 826 bytes |

Used by: `src/orchestrator/nodes/analyze_dependencies.py`

Purpose: Build the entity dependency tree from discovered schemas, determining
which entities depend on which others for FK links.

### 3. Entity Table Selection (`entity_table_selection.*`)

| File | Size |
|------|------|
| `entity_table_selection.system.txt` | 3,366 bytes |
| `entity_table_selection.human.txt` | 2,246 bytes |

Used by: `src/mcp_server/tools/select_entity_tables.py` and `analyze_dependencies` node

Purpose: Select which source tables hold each target entity. Considers the
whole schema, not hardcoded synonym lists.

### 4. Scope Planning (`scope_planning.*`)

| File | Size |
|------|------|
| `scope_planning.system.txt` | 5,574 bytes |
| `scope_planning.human.txt` | 250 bytes |

Used by: `src/orchestrator/nodes/plan_scope.py`

Purpose: Build per-entity extraction plans for scoped merges. Determines
how to link the subject entity to its related entities.

### 5. Entity Resolution (`entity_resolution.*`)

| File | Size |
|------|------|
| `entity_resolution.system.txt` | 3,184 bytes |
| `entity_resolution.human.txt` | 181 bytes |

Used by: `src/mcp_server/tools/resolve_entities.py`

Purpose: LLM-assisted entity matching for borderline cases (similarity scores
in the 0.70-0.95 band). The heuristic layer handles high-confidence matches.

### 6. Field Mapping (`field_mapping.*`)

| File | Size |
|------|------|
| `field_mapping.system.txt` | 2,746 bytes |
| `field_mapping.human.txt` | 169 bytes |

Used by: `src/mcp_server/tools/map_fields.py`

Purpose: Map unified entity fields to destination columns.

## Test Coverage

- `tests/prompts/harness.py` — prompt golden-case scoring harness
- `tests/prompts/golden/` — golden prompt test data
