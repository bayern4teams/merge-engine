"""
Unit tests for the dependency sort module.

Tests topological sorting, validation, and execution group finding.
"""

from __future__ import annotations

import pytest

from src.orchestrator.utils.dependency_sort import (
    find_execution_groups,
    topological_sort,
    validate_extraction_order,
)


class TestTopologicalSort:
    """Tests for the topological_sort function."""

    def test_no_dependencies(self) -> None:
        """Entities with no dependencies should return in sorted order."""
        tree = {"a": [], "b": [], "c": []}
        result = topological_sort(tree)
        # All should be present
        assert set(result) == {"a", "b", "c"}

    def test_linear_dependency(self) -> None:
        """A → B → C should produce C, B, A."""
        tree = {
            "A": ["B"],
            "B": ["C"],
            "C": [],
        }
        result = topological_sort(tree)
        # C must come before B, B must come before A
        assert result.index("C") < result.index("B")
        assert result.index("B") < result.index("A")

    def test_diamond_dependency(self) -> None:
        """Diamond: A→B, A→C, B→D, C→D."""
        tree = {
            "A": ["B", "C"],
            "B": ["D"],
            "C": ["D"],
            "D": [],
        }
        result = topological_sort(tree)
        assert result.index("D") < result.index("B")
        assert result.index("D") < result.index("C")
        assert result.index("B") < result.index("A")
        assert result.index("C") < result.index("A")

    def test_cycle_detected(self) -> None:
        """Circular dependencies should raise ValueError."""
        tree = {"A": ["B"], "B": ["C"], "C": ["A"]}
        with pytest.raises(ValueError, match="Circular dependency"):
            topological_sort(tree)

    def test_self_reference(self) -> None:
        """Self-reference is a cycle."""
        tree = {"A": ["A"]}
        with pytest.raises(ValueError, match="Circular dependency"):
            topological_sort(tree)

    def test_unknown_entity_added(self) -> None:
        """Unknown referenced entity should be added with no deps."""
        tree = {"A": ["unknown_entity"]}
        result = topological_sort(tree)
        assert "unknown_entity" in result
        # unknown_entity should come before A
        assert result.index("unknown_entity") < result.index("A")

    def test_empty_tree(self) -> None:
        """Empty dependency tree should return empty list."""
        assert topological_sort({}) == []

    def test_single_entity(self) -> None:
        """Single entity with no deps should return single-element list."""
        result = topological_sort({"A": []})
        assert result == ["A"]


class TestValidateExtractionOrder:
    """Tests for the validate_extraction_order function."""

    def test_valid_order(self) -> None:
        """Valid extraction order should return True."""
        tree = {"A": ["B"], "B": []}
        order = ["B", "A"]
        assert validate_extraction_order(tree, order) is True

    def test_invalid_order(self) -> None:
        """Invalid extraction order should return False."""
        tree = {"A": ["B"], "B": []}
        order = ["A", "B"]  # A before B violates dependency
        assert validate_extraction_order(tree, order) is False

    def test_order_with_extra_entities(self) -> None:
        """Order with entities not in tree should return True (extra entities allowed)."""
        tree = {"A": []}
        order = ["A", "B"]  # B not in tree but A is valid
        # Extra entities in order that aren't in tree are simply ignored
        assert validate_extraction_order(tree, order) is True

    def test_empty_order(self) -> None:
        """Empty order with non-empty tree should return False."""
        tree = {"A": []}
        order = []
        assert validate_extraction_order(tree, order) is False

    def test_empty_both(self) -> None:
        """Empty tree and empty order should return True."""
        assert validate_extraction_order({}, []) is True


class TestFindExecutionGroups:
    """Tests for the find_execution_groups function."""

    def test_no_dependencies(self) -> None:
        """All entities with no deps should be in one group."""
        tree = {"A": [], "B": [], "C": []}
        groups = find_execution_groups(tree)
        assert len(groups) == 1
        assert set(groups[0]) == {"A", "B", "C"}

    def test_linear_dependency(self) -> None:
        """Linear deps should produce separate groups."""
        tree = {
            "A": ["B"],
            "B": ["C"],
            "C": [],
        }
        groups = find_execution_groups(tree)
        assert len(groups) == 3
        assert groups[0] == ["C"]
        assert groups[1] == ["B"]
        assert groups[2] == ["A"]

    def test_parallel_groups(self) -> None:
        """Independent branches should be in the same group."""
        tree = {
            "A": ["B", "C"],
            "B": [],
            "C": [],
            "D": ["B", "C"],
        }
        groups = find_execution_groups(tree)
        # B and C are group 0, A and D are group 1
        assert len(groups) == 2
        assert set(groups[0]) == {"B", "C"}
        assert set(groups[1]) == {"A", "D"}

    def test_diamond(self) -> None:
        """Diamond dependency should produce 2 groups."""
        tree = {
            "A": ["B", "C"],
            "B": ["D"],
            "C": ["D"],
            "D": [],
        }
        groups = find_execution_groups(tree)
        assert len(groups) == 3
        assert groups[0] == ["D"]
        assert set(groups[1]) == {"B", "C"}
        assert groups[2] == ["A"]

    def test_empty_tree(self) -> None:
        """Empty tree should return a single empty group."""
        groups = find_execution_groups({})
        assert groups == [[]]
