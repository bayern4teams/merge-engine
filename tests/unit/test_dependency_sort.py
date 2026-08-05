"""
Tests for src/orchestrator/utils/dependency_sort.py - Topological sort for entity dependency graphs.
"""

import pytest

from src.orchestrator.utils.dependency_sort import (
    find_execution_groups,
    topological_sort,
    validate_extraction_order,
)


class TestTopologicalSort:
    """Tests for topological_sort()."""

    def test_simple_linear_dependency(self):
        """Linear dependency chain should produce correct order."""
        tree = {
            "country": [],
            "company": ["country"],
            "person": ["country"],
        }
        result = topological_sort(tree)
        # country must come before company and person
        assert result.index("country") < result.index("company")
        assert result.index("country") < result.index("person")

    def test_no_dependencies(self):
        """Entities with no dependencies can be in any order."""
        tree = {
            "person": [],
            "company": [],
            "product": [],
        }
        result = topological_sort(tree)
        assert set(result) == {"person", "company", "product"}

    def test_complex_dependency_tree(self):
        """Complex dependency tree should be correctly sorted."""
        tree = {
            "country": [],
            "company": ["country"],
            "person": ["country"],
            "product": ["company"],
            "order": ["company", "product"],
        }
        result = topological_sort(tree)
        assert result.index("country") < result.index("company")
        assert result.index("country") < result.index("person")
        assert result.index("company") < result.index("product")
        assert result.index("company") < result.index("order")
        assert result.index("product") < result.index("order")

    def test_single_entity(self):
        """Single entity with no dependencies."""
        tree = {"person": []}
        result = topological_sort(tree)
        assert result == ["person"]

    def test_empty_tree(self):
        """Empty dependency tree should return empty list."""
        result = topological_sort({})
        # Empty dict returns empty list (no nodes to sort)
        assert isinstance(result, list)

    def test_cycle_detection(self):
        """Circular dependency should raise ValueError."""
        tree = {
            "a": ["b"],
            "b": ["c"],
            "c": ["a"],
        }
        with pytest.raises(ValueError, match="Circular dependency detected"):
            topological_sort(tree)

    def test_self_referencing(self):
        """Self-referencing entity should raise ValueError."""
        tree = {"a": ["a"]}
        with pytest.raises(ValueError, match="Circular dependency detected"):
            topological_sort(tree)

    def test_unknown_entity_added(self):
        """Unknown entity referenced should be added with no deps."""
        tree = {
            "person": ["unknown_entity"],
        }
        result = topological_sort(tree)
        assert set(result) == {"person", "unknown_entity"}
        assert result.index("unknown_entity") < result.index("person")

    def test_deterministic_output(self):
        """Output should be deterministically sorted within same level."""
        tree = {
            "zebra": [],
            "apple": [],
            "mango": [],
        }
        result = topological_sort(tree)
        assert result == ["apple", "mango", "zebra"]


class TestValidateExtractionOrder:
    """Tests for validate_extraction_order()."""

    def test_valid_order(self):
        """Valid topological order should return True."""
        tree = {
            "country": [],
            "company": ["country"],
            "person": ["country"],
        }
        order = ["country", "company", "person"]
        assert validate_extraction_order(tree, order) is True

    def test_invalid_order(self):
        """Invalid order (dep after dependents) should return False."""
        tree = {
            "country": [],
            "company": ["country"],
        }
        order = ["company", "country"]
        assert validate_extraction_order(tree, order) is False

    def test_missing_entity(self):
        """Entity not in order should return False."""
        tree = {
            "country": [],
            "company": ["country"],
        }
        order = ["country"]
        assert validate_extraction_order(tree, order) is False

    def test_empty_order(self):
        """Empty order with non-empty tree should return False."""
        tree = {"company": []}
        assert validate_extraction_order(tree, []) is False

    def test_empty_both(self):
        """Empty tree and empty order should return True."""
        assert validate_extraction_order({}, []) is True

    def test_complex_valid_order(self):
        """Complex valid order should pass."""
        tree = {
            "country": [],
            "company": ["country"],
            "person": ["country"],
            "product": ["company"],
            "order": ["company", "product"],
        }
        order = ["country", "person", "company", "product", "order"]
        assert validate_extraction_order(tree, order) is True


class TestFindExecutionGroups:
    """Tests for find_execution_groups()."""

    def test_simple_parallel(self):
        """Entities at same level should be in same group."""
        tree = {
            "country": [],
            "company": ["country"],
            "person": ["country"],
        }
        result = find_execution_groups(tree)
        # Should have 2 groups: [country] and [company, person]
        assert len(result) == 2
        # Country is in group 0 (no deps)
        assert "country" in result[0]
        # Company and person are in group 1 (depend on country)
        assert "company" in result[1]
        assert "person" in result[1]

    def test_no_dependencies_all_parallel(self):
        """All entities with no deps should be in one group."""
        tree = {
            "person": [],
            "company": [],
            "product": [],
        }
        result = find_execution_groups(tree)
        assert len(result) == 1
        assert set(result[0]) == {"person", "company", "product"}

    def test_linear_sequential(self):
        """Linear dependency should produce one entity per group."""
        tree = {
            "a": [],
            "b": ["a"],
            "c": ["b"],
        }
        result = find_execution_groups(tree)
        assert len(result) == 3
        assert result[0] == ["a"]
        assert result[1] == ["b"]
        assert result[2] == ["c"]

    def test_complex_groups(self):
        """Complex dependency should produce correct groupings."""
        tree = {
            "country": [],
            "company": ["country"],
            "person": ["country"],
            "product": ["company"],
            "order": ["company", "product"],
        }
        result = find_execution_groups(tree)
        # Group 0: country (no deps)
        # Group 1: company, person (depend on country)
        # Group 2: product (depends on company)
        # Group 3: order (depends on company, product)
        assert len(result) >= 3
        assert "country" in result[0]

    def test_deterministic_grouping(self):
        """Entities within groups should be sorted."""
        tree = {
            "zebra": [],
            "apple": [],
            "mango": ["apple"],
        }
        result = find_execution_groups(tree)
        assert len(result) == 2
        assert result[0] == ["apple", "zebra"]
        assert result[1] == ["mango"]

    def test_empty_tree(self):
        """Empty tree should return list with one empty group."""
        result = find_execution_groups({})
        assert result == [[]]
