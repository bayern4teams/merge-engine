"""
Unit tests for UI component render functions.

Tests each render_* function by inspecting the HTML value content,
mocking gr.HTML to avoid requiring a live Gradio environment.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import sys

import pytest


def _mock_gradio_modules():
    """Mock gradio and reload UI modules under mock."""
    mock_gr = MagicMock()

    class FakeHTML:
        def __init__(self, value="", **kw):
            self.value = value

        def __repr__(self):
            return f"FakeHTML(value={self.value!r})"

    mock_gr.HTML.side_effect = lambda value="", **kw: FakeHTML(value)
    mock_gr.Blocks.side_effect = lambda **kw: MagicMock()
    mock_gr.Markdown.side_effect = lambda x: None
    mock_gr.Row.side_effect = lambda **kw: MagicMock()
    mock_gr.Column.side_effect = lambda **kw: MagicMock()
    mock_gr.State.side_effect = lambda x: MagicMock()
    mock_gr.Ticker.side_effect = lambda **kw: MagicMock()
    mock_gr.Ticker.return_value.tick.side_effect = lambda **kw: None
    mock_gr.Button.side_effect = lambda **kw: MagicMock()
    mock_gr.Textbox.side_effect = lambda **kw: MagicMock()
    mock_gr.Accordion.side_effect = lambda **kw: MagicMock()

    return mock_gr


@pytest.fixture()
def ui_modules():
    """Fixture that mocks gradio and returns reloaded UI modules."""
    mock_gr = _mock_gradio_modules()

    # Remove cached UI modules so they get reimported under mock
    to_remove = [k for k in sys.modules if k.startswith("src.ui")]
    for k in to_remove:
        del sys.modules[k]

    with patch.dict("sys.modules", {"gradio": mock_gr, "gr": mock_gr}):
        import src.ui.components.dependency_editor as dep_mod
        import src.ui.components.match_reviewer as match_mod
        import src.ui.components.mapping_reviewer as map_mod
        import src.ui.components.summary_view as sum_mod

        yield {
            "mock_gr": mock_gr,
            "dep": dep_mod,
            "match": match_mod,
            "mapping": map_mod,
            "summary": sum_mod,
        }


class TestChatState:
    """Tests for ChatState class in gradio_app."""

    def test_chat_state_source_code_has_class(self):
        """ChatState class is defined in gradio_app source."""
        import inspect
        source = inspect.getsource(sys.modules.get("src.ui.gradio_app") or __import__("src.ui.gradio_app", fromlist=["ChatState"]))
        assert "class ChatState" in source

    def test_chat_state_default_values(self):
        """ChatState initializes with correct default values."""
        # Import gradio first to satisfy the module
        import gradio
        import src.ui.gradio_app as app_mod
        state = app_mod.ChatState()
        assert state.thread_id is None
        assert state.status == "idle"
        assert state.last_log == ""


class TestRenderDependencyEditor:
    """Tests for render_dependency_editor function."""

    def test_empty_dependency_tree(self, ui_modules):
        """Empty dependency tree renders with empty order message."""
        result = ui_modules["dep"].render_dependency_editor({}, [])
        assert result.value is not None
        assert "Entity Dependency Tree" in result.value
        assert "<ol>" in result.value
        assert "</ol>" in result.value

    def test_linear_dependencies(self, ui_modules):
        """Linear dependency chain renders correctly."""
        dep_tree = {
            "companies": [],
            "contacts": ["companies"],
            "orders": ["companies", "contacts"],
        }
        order = ["companies", "contacts", "orders"]

        result = ui_modules["dep"].render_dependency_editor(dep_tree, order)
        html = result.value

        assert "companies" in html
        assert "contacts" in html
        assert "orders" in html
        assert "depends on: companies" in html
        assert "depends on: companies, contacts" in html
        assert "no dependencies (extract first)" in html

    def test_complex_dependencies(self, ui_modules):
        """Complex multi-entity dependency tree."""
        dep_tree = {
            "a": [],
            "b": ["a"],
            "c": ["a"],
            "d": ["b", "c"],
        }
        order = ["a", "b", "c", "d"]

        result = ui_modules["dep"].render_dependency_editor(dep_tree, order)
        html = result.value

        assert "<table" in html
        assert "Dependency Matrix" in html
        assert "<tr>" in html

    def test_returns_html_with_value(self, ui_modules):
        """render_dependency_editor returns object with value attribute."""
        result = ui_modules["dep"].render_dependency_editor({}, [])
        assert hasattr(result, "value")
        assert isinstance(result.value, str)
        assert len(result.value) > 0

    def test_single_entity_no_deps(self, ui_modules):
        """Single entity with no dependencies renders correctly."""
        result = ui_modules["dep"].render_dependency_editor({"users": []}, ["users"])
        html = result.value
        assert "users" in html
        assert "no dependencies" in html


class TestRenderMatchReviewer:
    """Tests for render_match_reviewer function."""

    def test_no_clusters(self, ui_modules):
        """No clusters renders success message."""
        result = ui_modules["match"].render_match_reviewer([])
        html = result.value
        assert "Entity Match Review" in html
        assert "No ambiguous matches found" in html
        assert "All clusters are clear" in html

    def test_single_cluster(self, ui_modules):
        """Single low-confidence cluster renders correctly."""
        clusters = [
            {
                "entity_type": "company",
                "cluster_id": "abc123def456",
                "confidence": 0.65,
                "record_ids": ["pg-1", "mongo-42"],
            }
        ]
        result = ui_modules["match"].render_match_reviewer(clusters)
        html = result.value

        assert "company" in html
        assert "0.65" in html
        assert "pg-1" in html
        assert "mongo-42" in html
        assert "Cluster:" in html

    def test_multiple_clusters(self, ui_modules):
        """Multiple clusters all rendered."""
        clusters = [
            {
                "entity_type": "company",
                "cluster_id": "aaa",
                "confidence": 0.7,
                "record_ids": ["pg-1", "mongo-1"],
            },
            {
                "entity_type": "person",
                "cluster_id": "bbb",
                "confidence": 0.55,
                "record_ids": ["pg-2", "mongo-2"],
            },
        ]
        result = ui_modules["match"].render_match_reviewer(clusters)
        html = result.value

        assert "2 cluster" in html
        assert "company" in html
        assert "person" in html
        assert "0.70" in html
        assert "0.55" in html

    def test_high_confidence_cluster(self, ui_modules):
        """High confidence cluster still rendered."""
        clusters = [
            {
                "entity_type": "product",
                "cluster_id": "xyz",
                "confidence": 0.9,
                "record_ids": ["pg-100"],
            }
        ]
        result = ui_modules["match"].render_match_reviewer(clusters)
        html = result.value

        assert "1 cluster" in html
        assert "product" in html
        assert "0.90" in html

    def test_cluster_with_empty_record_ids(self, ui_modules):
        """Cluster with empty record_ids renders gracefully."""
        clusters = [
            {
                "entity_type": "company",
                "cluster_id": "abc",
                "confidence": 0.5,
                "record_ids": [],
            }
        ]
        result = ui_modules["match"].render_match_reviewer(clusters)
        html = result.value

        assert "company" in html
        assert "0.50" in html


class TestRenderMappingReviewer:
    """Tests for render_mapping_reviewer function."""

    def test_no_unmapped_no_schema_changes(self, ui_modules):
        """Clean state with no unmapped fields or schema changes."""
        result = ui_modules["mapping"].render_mapping_reviewer([], [])
        html = result.value

        assert "Field Mapping Review" in html
        assert "All fields have been mapped" in html
        assert "No schema changes needed" in html

    def test_unmapped_fields(self, ui_modules):
        """Unmapped fields rendered in table."""
        unmapped = [
            {"entity_type": "company", "source_field": "internal_ref", "reason": "no matching destination column"},
            {"entity_type": "person", "source_field": "legacy_code", "reason": "deprecated field"},
        ]
        result = ui_modules["mapping"].render_mapping_reviewer(unmapped, [])
        html = result.value

        assert "2 field" in html
        assert "internal_ref" in html
        assert "legacy_code" in html
        assert "company" in html
        assert "person" in html
        assert "<table" in html

    def test_schema_changes(self, ui_modules):
        """Pending schema changes rendered in table."""
        changes = [
            {"table": "companies", "column": "tax_id", "data_type": "varchar(20)"},
            {"table": "persons", "column": "employee_id", "data_type": "int"},
        ]
        result = ui_modules["mapping"].render_mapping_reviewer([], changes)
        html = result.value

        assert "2 new column" in html
        assert "tax_id" in html
        assert "employee_id" in html
        assert "varchar(20)" in html
        assert "int" in html
        assert "<table" in html

    def test_both_unmapped_and_schema_changes(self, ui_modules):
        """Both unmapped fields and schema changes rendered."""
        unmapped = [{"entity_type": "company", "source_field": "custom_field", "reason": "new"}]
        changes = [{"table": "companies", "column": "custom_field", "data_type": "text"}]
        result = ui_modules["mapping"].render_mapping_reviewer(unmapped, changes)
        html = result.value

        assert "1 field" in html
        assert "1 new column" in html
        assert "custom_field" in html

    def test_returns_html_with_value(self, ui_modules):
        """render_mapping_reviewer returns object with value attribute."""
        result = ui_modules["mapping"].render_mapping_reviewer([], [])
        assert hasattr(result, "value")
        assert isinstance(result.value, str)


class TestRenderFinalSummary:
    """Tests for render_final_summary function."""

    def test_zero_counts(self, ui_modules):
        """Summary with all zero counts renders correctly."""
        summary = {
            "total_entities": 0,
            "entity_counts": {},
            "total_conflicts": 0,
            "schema_changes": 0,
            "errors": 0,
        }
        result = ui_modules["summary"].render_final_summary(summary, [])
        html = result.value

        assert "Merge Summary" in html
        assert "Total Entities" in html
        assert "<td>0</td>" in html
        assert "Total Conflicts" in html
        assert "Schema Changes" in html
        assert "Errors" in html

    def test_with_entity_counts(self, ui_modules):
        """Summary with entity counts renders per-entity breakdown."""
        summary = {
            "total_entities": 5,
            "entity_counts": {
                "company": 3,
                "person": 2,
            },
            "total_conflicts": 0,
            "schema_changes": 0,
            "errors": 0,
        }
        result = ui_modules["summary"].render_final_summary(summary, [])
        html = result.value

        assert "5" in html
        assert "company" in html
        assert "3 unified records" in html
        assert "person" in html
        assert "2 unified records" in html

    def test_with_conflicts(self, ui_modules):
        """Summary with conflicts renders conflict count."""
        summary = {
            "total_entities": 10,
            "entity_counts": {"company": 10},
            "total_conflicts": 3,
            "schema_changes": 0,
            "errors": 0,
        }
        result = ui_modules["summary"].render_final_summary(summary, [])
        html = result.value

        assert "3" in html
        assert "Total Conflicts" in html

    def test_with_schema_changes(self, ui_modules):
        """Summary with schema changes renders detail table."""
        summary = {
            "total_entities": 5,
            "entity_counts": {"company": 5},
            "total_conflicts": 0,
            "schema_changes": 2,
            "errors": 0,
        }
        changes = [
            {"table": "companies", "column": "website", "data_type": "text"},
            {"table": "companies", "column": "founded_year", "data_type": "int"},
        ]
        result = ui_modules["summary"].render_final_summary(summary, changes)
        html = result.value

        assert "Schema Changes Detail" in html
        assert "website" in html
        assert "founded_year" in html
        assert "text" in html
        assert "int" in html

    def test_with_errors(self, ui_modules):
        """Summary with errors renders error count."""
        summary = {
            "total_entities": 5,
            "entity_counts": {"company": 5},
            "total_conflicts": 0,
            "schema_changes": 0,
            "errors": 2,
        }
        result = ui_modules["summary"].render_final_summary(summary, [])
        html = result.value

        assert "Errors" in html
        assert "2" in html

    def test_partial_entity_counts(self, ui_modules):
        """Summary with only total_entities and no entity_counts."""
        summary = {
            "total_entities": 100,
            "entity_counts": {},
            "total_conflicts": 0,
            "schema_changes": 0,
            "errors": 0,
        }
        result = ui_modules["summary"].render_final_summary(summary, [])
        html = result.value

        assert "100" in html

    def test_returns_html_with_value(self, ui_modules):
        """render_final_summary returns object with value attribute."""
        summary = {
            "total_entities": 0,
            "entity_counts": {},
            "total_conflicts": 0,
            "schema_changes": 0,
            "errors": 0,
        }
        result = ui_modules["summary"].render_final_summary(summary, [])
        assert hasattr(result, "value")
        assert isinstance(result.value, str)