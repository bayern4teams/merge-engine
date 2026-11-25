"""
Topological sort for entity dependency graphs.

Provides functions to sort entities in extraction order based on their
dependency tree, ensuring prerequisite entities are processed first.
"""

from __future__ import annotations

from collections import deque


def topological_sort(dependency_tree: dict[str, list[str]]) -> list[str]:
    """Sort entities using Kahn's algorithm for topological ordering.

    Entities with no dependencies (leaf nodes) come first.  Entities
    that depend on others appear after their prerequisites.

    A self-dependency (``{"company": ["company"]}`` — a self-referencing
    FK such as ``parent_company_id``) is dropped: an entity cannot be
    extracted "before itself", so the self-edge constrains nothing, and
    keeping it makes Kahn report every table in a self-linking component
    as a cycle (APP_FINDINGS 2026-10 #1, first hit on google_v1).

    Parameters
    ----------
    dependency_tree : dict[str, list[str]]
        Mapping of ``{entity_type: [prerequisite_entity_types]}``.

    Returns
    -------
    list[str]
        Topologically sorted list of entity type names.

    Raises
    ------
    ValueError
        If the dependency tree contains a (genuine, multi-entity) cycle.

    Examples
    --------
    >>> tree = {"company": [], "person": ["company"], "product": ["company"]}
    >>> topological_sort(tree)
    ['company', 'person', 'product']  # order of person/product may vary
    """
    # Build in-degree map and adjacency list (self-edges dropped, see above)
    all_nodes: set[str] = set(dependency_tree.keys())
    in_degree: dict[str, int] = {node: 0 for node in all_nodes}
    dependents: dict[str, list[str]] = {node: [] for node in all_nodes}

    for node, deps in dependency_tree.items():
        for dep in deps:
            if dep == node:
                continue
            if dep not in all_nodes:
                # Unknown entity referenced — add it with no deps
                all_nodes.add(dep)
                in_degree[dep] = 0
                dependents[dep] = []
            in_degree[node] += 1
            dependents[dep].append(node)

    # Start with nodes that have zero in-degree
    queue: deque[str] = deque(sorted(n for n in all_nodes if in_degree[n] == 0))
    result: list[str] = []

    while queue:
        node = queue.popleft()
        result.append(node)
        for dependent in sorted(dependents[node]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(result) != len(all_nodes):
        # Find nodes involved in cycles for a clearer error
        cyclic = sorted(n for n in all_nodes if n not in set(result))
        raise ValueError(f"Circular dependency detected among entities: {cyclic}")

    return result


def validate_extraction_order(
    dependency_tree: dict[str, list[str]],
    extraction_order: list[str],
) -> bool:
    """Verify that *extraction_order* is a valid topological ordering of *dependency_tree*.

    Returns ``True`` if every entity appears after all of its prerequisites.
    """
    position: dict[str, int] = {entity: i for i, entity in enumerate(extraction_order)}
    for entity, deps in dependency_tree.items():
        if entity not in position:
            return False
        for dep in deps:
            if dep not in position:
                return False
            if position[dep] >= position[entity]:
                return False
    return True


def find_execution_groups(
    dependency_tree: dict[str, list[str]],
) -> list[list[str]]:
    """Group entities that can be extracted in parallel.

    Entities at the same dependency level can be processed concurrently.

    Parameters
    ----------
    dependency_tree : dict[str, list[str]]
        Mapping of ``{entity_type: [prerequisite_entity_types]}``.

    Returns
    -------
    list[list[str]]
        List of groups.  Group 0 contains leaf entities (no deps),
        group 1 contains entities whose deps are all in group 0, etc.

    Examples
    --------
    >>> tree = {"country": [], "company": ["country"], "person": ["country"]}
    >>> find_execution_groups(tree)
    [['company', 'person'], ['country']]  # or similar depending on sort
    """
    sorted_entities = topological_sort(dependency_tree)
    level: dict[str, int] = {}

    for entity in sorted_entities:
        deps = dependency_tree.get(entity, [])
        if not deps:
            level[entity] = 0
        else:
            level[entity] = max(level.get(d, 0) for d in deps) + 1

    # Group by level
    max_level = max(level.values()) if level else 0
    groups: list[list[str]] = [[] for _ in range(max_level + 1)]
    for entity, lvl in level.items():
        groups[lvl].append(entity)

    # Sort within groups for deterministic output
    for group in groups:
        group.sort()

    return groups
