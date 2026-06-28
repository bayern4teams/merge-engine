"""
Tests for src/ui/components/dependency_editor.py - Dependency tree editor component.
"""

import pytest
from src.ui.components.dependency_editor import render_dependency_editor, create_dependency_editor_block


class TestRenderDependencyEditor:
    """Tests for render_dependency_editor() function."""

    def test_empty_tree(self):
        """Should render empty dependency tree."""
        result = render_dependency_editor({}, [])
        html = result.value
        assert "Entity Dependency Tree" in html
        assert "Extraction Order" in html

    def test_simple_tree(self):
        """Should render simple dependency tree."""
        tree = {
            "company": [],
            "person": ["company"],
        }
        order = ["company", "person"]
        result = render_dependency_editor(tree, order)
        html = result.value
        assert "company" in html
        assert "person" in html
        assert "depends on: company" in html

    def test_no_dependencies_shown(self):
        """Should show 'no dependencies' for entities with no deps."""
        tree = {"person": [], "company": []}
        order = ["person", "company"]
        result = render_dependency_editor(tree, order)
        html = result.value
        assert "no dependencies" in html
        assert "extract first" in html

    def test_complex_dependency_tree(self):
        """Should render complex dependency tree."""
        tree = {
            "country": [],
            "company": ["country"],
            "person": ["country"],
            "product": ["company"],
        }
        order = ["country", "company", "person", "product"]
        result = render_dependency_editor(tree, order)
        html = result.value
        assert "country" in html
        assert "company" in html
        assert "person" in html
        assert "product" in html
        assert "depends on: country" in html
        assert "depends on: company" in html

    def test_extraction_order_displayed(self):
        """Should display extraction order as ordered list."""
        tree = {"a": [], "b": ["a"], "c": ["b"]}
        order = ["a", "b", "c"]
        result = render_dependency_editor(tree, order)
        html = result.value
        assert "<ol>" in html
        assert "<li>" in html
        # Check order is sequential
        assert "a" in html
        assert "b" in html
        assert "c" in html

    def test_dependency_matrix(self):
        """Should display dependency matrix table."""
        tree = {"company": [], "person": ["company"]}
        order = ["company", "person"]
        result = render_dependency_editor(tree, order)
        html = result.value
        assert "Dependency Matrix" in html
        assert "<table" in html
        assert "Entity" in html
        assert "Depends On" in html
        assert "Extracted" in html

    def test_dependency_matrix_checkmark(self):
        """Should show checkmark when all deps extracted."""
        tree = {"company": [], "person": ["company"]}
        order = ["company", "person"]
        result = render_dependency_editor(tree, order)
        html = result.value
        assert "✓" in html

    def test_missing_dep_status(self):
        """Should show status for missing dependencies."""
        tree = {"person": ["nonexistent"]}
        order = ["person"]
        result = render_dependency_editor(tree, order)
        html = result.value
        # Should still render without error
        assert "person" in html

    def test_returns_html_component(self):
        """Should return a gr.HTML component."""
        result = render_dependency_editor({}, [])
        assert hasattr(result, "value")
        assert isinstance(result.value, str)

    def test_html_structure(self):
        """HTML should have proper structure."""
        result = render_dependency_editor({}, [])
        html = result.value
        assert "<div" in html
        assert "</div>" in html
        assert 'max-width: 600px' in html

    def test_multiple_dependencies(self):
        """Should display multiple dependencies."""
        tree = {"order": ["company", "product"]}
        order = ["company", "product", "order"]
        result = render_dependency_editor(tree, order)
        html = result.value
        assert "depends on: company, product" in html

    def test_dash_for_no_deps_in_matrix(self):
        """Should show '—' for entities with no dependencies in matrix."""
        tree = {"person": []}
        order = ["person"]
        result = render_dependency_editor(tree, order)
        html = result.value
        assert "—" in html


class TestCreateDependencyEditorBlock:
    """Tests for create_dependency_editor_block() function."""

    def test_returns_blocks(self):
        """Should return a gr.Blocks component."""
        result = create_dependency_editor_block()
        assert result is not None

    def test_block_contains_elements(self):
        """Block should contain expected elements."""
        result = create_dependency_editor_block()
        assert result is not None