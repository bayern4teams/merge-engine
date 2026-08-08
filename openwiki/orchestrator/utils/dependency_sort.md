---
type: utility
title: Dependency Sort
description: Topological sort implementation (Kahn's algorithm), execution group finding, extraction order validation, and dependency tree analysis for the merge pipeline.
tags: [dependency-sort, topology, execution-order]
---

# Dependency Sort

## Purpose

Provides topological sorting of entity dependencies and extraction order validation. Critical for ensuring entities are extracted in the correct order (dependent entities after their dependencies).

## File

`src/orchestrator/utils/dependency_sort.py`

## Key Functions

| Function | Purpose |
|----------|---------|
| `topological_sort(entity_tables, dependency_tree)` | Kahn's algorithm for entity ordering |
| `find_execution_groups(dependency_tree)` | Group entities into parallel execution levels |
| `validate_extraction_order(extraction_order, dependency_tree)` | Ensure no entity appears before its dependencies |

## Topological Sort

Uses Kahn's algorithm (BFS-based):

```python
def topological_sort(graph):
    in_degree = {node: 0 for node in graph}
    for node in graph:
        for neighbor in graph[node]:
            in_degree[neighbor] += 1
    
    queue = [node for node in graph if in_degree[node] == 0]
    order = []
    
    while queue:
        node = queue.pop(0)
        order.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    
    if len(order) != len(graph):
        raise ValueError("Circular dependency detected")
    
    return order
```

## Execution Groups

`find_execution_groups()` partitions entities into parallel execution levels:

```
Level 0: [companies]           # No dependencies
Level 1: [people, products]   # Depends on companies
Level 2: [employment]        # Depends on people and companies
```

This enables future parallel extraction of independent entities.

## Test Coverage

- `tests/unit/test_dependency_sort.py` — topological sort, cycle detection
