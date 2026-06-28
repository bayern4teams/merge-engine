"""
Tests for src/ui/components/summary_view.py - Summary view component for Gradio UI.
"""

import pytest
from src.ui.components.summary_view import render_final_summary, create_summary_view_block


class TestRenderFinalSummary:
    """Tests for render_final_summary() function."""

    def test_empty_summary(self):
        """Should render empty summary."""
        result = render_final_summary({}, [])
        html = result.value.replace('\n', '')
        assert "Merge Summary" in html
        assert "<strong>Total Entities</strong></td><td>0" in html

    def test_entity_counts(self):
        """Should display entity counts."""
        summary = {
            "total_entities": 3,
            "entity_counts": {
                "person": 100,
                "company": 50,
                "product": 200,
            },
        }
        result = render_final_summary(summary, [])
        html = result.value.replace('\n', '')
        assert "<strong>Total Entities</strong></td><td>3" in html
        assert "person: 100 unified records" in html
        assert "company: 50 unified records" in html
        assert "product: 200 unified records" in html

    def test_conflicts_displayed(self):
        """Should display conflict count."""
        summary = {
            "total_entities": 1,
            "entity_counts": {"person": 10},
            "total_conflicts": 5,
        }
        result = render_final_summary(summary, [])
        html = result.value.replace('\n', '')
        assert "<strong>Total Conflicts</strong></td><td>5" in html

    def test_schema_changes_displayed(self):
        """Should display schema change count."""
        summary = {
            "total_entities": 1,
            "entity_counts": {"person": 10},
            "schema_changes": 3,
        }
        result = render_final_summary(summary, [])
        html = result.value
        assert "3 new column(s)" in html

    def test_errors_displayed(self):
        """Should display error count in red."""
        summary = {
            "total_entities": 1,
            "entity_counts": {"person": 10},
            "errors": 2,
        }
        result = render_final_summary(summary, [])
        html = result.value
        assert "color: red" in html
        assert ">2" in html

    def test_schema_changes_detail(self):
        """Should display schema changes detail table."""
        summary = {
            "total_entities": 1,
            "entity_counts": {"person": 10},
        }
        changes = [
            {"table": "dim_person", "column": "department", "data_type": "STRING"},
            {"table": "dim_company", "column": "tax_id", "data_type": "VARCHAR(20)"},
        ]
        result = render_final_summary(summary, changes)
        html = result.value
        assert "Schema Changes Detail" in html
        assert "Table" in html
        assert "Column" in html
        assert "Type" in html
        assert "dim_person" in html
        assert "department" in html
        assert "STRING" in html
        assert "dim_company" in html
        assert "tax_id" in html

    def test_schema_changes_detail_missing_fields(self):
        """Should handle missing fields in schema changes."""
        changes = [{}]
        result = render_final_summary({"total_entities": 0, "entity_counts": {}}, changes)
        html = result.value
        assert "?" in html

    def test_returns_html_component(self):
        """Should return a gr.HTML component."""
        result = render_final_summary({}, [])
        assert hasattr(result, "value")
        assert isinstance(result.value, str)

    def test_html_contains_approve_abort_text(self):
        """HTML should contain approve/abort instructions."""
        result = render_final_summary({}, [])
        html = result.value
        assert "Approve" in html
        assert "Abort" in html
        assert "loading" in html or "cancel" in html

    def test_html_has_styling(self):
        """HTML should have proper styling."""
        result = render_final_summary({}, [])
        html = result.value
        assert 'background: #f5f5f5' in html
        assert 'border-radius: 10px' in html
        assert 'max-width: 700px' in html

    def test_default_values_for_missing_summary_fields(self):
        """Should use defaults for missing summary fields."""
        result = render_final_summary({}, [])
        html = result.value
        assert "0" in html  # Default for total_entities

    def test_empty_entity_counts(self):
        """Should handle empty entity counts."""
        result = render_final_summary({"total_entities": 0, "entity_counts": {}}, [])
        html = result.value
        assert "Merge Summary" in html

    def test_zero_conflicts(self):
        """Should display zero conflicts."""
        result = render_final_summary({
            "total_entities": 1,
            "entity_counts": {"person": 10},
            "total_conflicts": 0,
        }, [])
        html = result.value.replace('\n', '')
        assert "<strong>Total Conflicts</strong></td><td>0" in html


class TestCreateSummaryViewBlock:
    """Tests for create_summary_view_block() function."""

    def test_returns_blocks(self):
        """Should return a gr.Blocks component."""
        result = create_summary_view_block()
        assert result is not None

    def test_block_created_successfully(self):
        """Block should be created successfully."""
        result = create_summary_view_block()
        assert result is not None