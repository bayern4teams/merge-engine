"""
Integration tests for the UI module.

Tests render functions with real data to verify HTML output is correctly
generated. Also verifies the ChatState class and module structure.
"""

from __future__ import annotations


class TestRenderFunctionIntegration:
    """Integration tests for render functions with realistic data."""

    def test_full_dependency_render_to_html(self):
        """Full dependency tree renders to valid HTML string."""
        from src.ui.components.dependency_editor import render_dependency_editor

        dep_tree = {
            "companies": [],
            "contacts": ["companies"],
            "orders": ["companies", "contacts"],
            "payments": ["orders"],
        }
        order = ["companies", "contacts", "orders", "payments"]

        result = render_dependency_editor(dep_tree, order)
        assert isinstance(result.value, str)
        assert "Entity Dependency Tree" in result.value
        assert "companies" in result.value
        assert "payments" in result.value
        assert "depends on: orders" in result.value

    def test_full_match_reviewer_with_multiple_entity_types(self):
        """Match reviewer handles multiple entity types."""
        from src.ui.components.match_reviewer import render_match_reviewer

        clusters = [
            {
                "entity_type": "company",
                "cluster_id": "aaa111",
                "confidence": 0.62,
                "record_ids": ["pg-1", "mongo-1", "dbrx-1"],
            },
            {
                "entity_type": "person",
                "cluster_id": "bbb222",
                "confidence": 0.58,
                "record_ids": ["pg-2", "mongo-2"],
            },
            {
                "entity_type": "product",
                "cluster_id": "ccc333",
                "confidence": 0.71,
                "record_ids": ["pg-3"],
            },
        ]
        result = render_match_reviewer(clusters)
        assert isinstance(result.value, str)
        assert "3 cluster" in result.value
        assert "company" in result.value
        assert "person" in result.value
        assert "product" in result.value

    def test_full_mapping_reviewer_with_realistic_data(self):
        """Mapping reviewer handles realistic unmapped fields and schema changes."""
        from src.ui.components.mapping_reviewer import render_mapping_reviewer

        unmapped = [
            {"entity_type": "company", "source_field": "vat_number", "reason": "no matching destination column"},
            {"entity_type": "company", "source_field": "duns_number", "reason": "new field needed"},
            {"entity_type": "person", "source_field": "employee_id", "reason": "no matching destination column"},
        ]
        changes = [
            {"table": "companies", "column": "vat_number", "data_type": "varchar(20)"},
            {"table": "companies", "column": "duns_number", "data_type": "varchar(15)"},
            {"table": "persons", "column": "employee_id", "data_type": "varchar(30)"},
        ]
        result = render_mapping_reviewer(unmapped, changes)
        assert isinstance(result.value, str)
        assert "3 field" in result.value
        assert "3 new column" in result.value
        assert "vat_number" in result.value
        assert "employee_id" in result.value

    def test_full_summary_with_comprehensive_data(self):
        """Summary view renders comprehensive merge data."""
        from src.ui.components.summary_view import render_final_summary

        summary = {
            "total_entities": 150,
            "entity_counts": {
                "company": 50,
                "person": 75,
                "product": 25,
            },
            "total_conflicts": 12,
            "schema_changes": 5,
            "errors": 1,
        }
        changes = [
            {"table": "companies", "column": "tax_id", "data_type": "varchar(20)"},
            {"table": "persons", "column": "ssn_masked", "data_type": "varchar(11)"},
            {"table": "products", "column": "sku_normalized", "data_type": "varchar(50)"},
        ]
        result = render_final_summary(summary, changes)
        assert isinstance(result.value, str)
        assert "150" in result.value
        assert "company" in result.value
        assert "50 unified records" in result.value
        assert "12" in result.value  # conflicts
        assert "5 new column" in result.value  # schema changes
        assert "Schema Changes Detail" in result.value
        assert "tax_id" in result.value

    def test_all_render_functions_accept_empty_data(self):
        """All render functions handle empty data gracefully."""
        from src.ui.components.dependency_editor import render_dependency_editor
        from src.ui.components.match_reviewer import render_match_reviewer
        from src.ui.components.mapping_reviewer import render_mapping_reviewer
        from src.ui.components.summary_view import render_final_summary

        # All should return without errors
        r1 = render_dependency_editor({}, [])
        r2 = render_match_reviewer([])
        r3 = render_mapping_reviewer([], [])
        summary = {"total_entities": 0, "entity_counts": {}, "total_conflicts": 0, "schema_changes": 0, "errors": 0}
        r4 = render_final_summary(summary, [])

        assert all(isinstance(r.value, str) for r in [r1, r2, r3, r4])

    def test_render_functions_produce_unique_html_content(self):
        """Each render function produces distinct HTML content."""
        from src.ui.components.dependency_editor import render_dependency_editor
        from src.ui.components.match_reviewer import render_match_reviewer
        from src.ui.components.mapping_reviewer import render_mapping_reviewer
        from src.ui.components.summary_view import render_final_summary

        html1 = render_dependency_editor({}, []).value
        html2 = render_match_reviewer([]).value
        html3 = render_mapping_reviewer([], []).value
        summary = {"total_entities": 0, "entity_counts": {}, "total_conflicts": 0, "schema_changes": 0, "errors": 0}
        html4 = render_final_summary(summary, []).value

        assert "Entity Dependency Tree" in html1
        assert "Entity Match Review" in html2
        assert "Field Mapping Review" in html3
        assert "Merge Summary" in html4

        # All should be different
        assert html1 != html2
        assert html2 != html3
        assert html3 != html4

    def test_edge_case_empty_cluster_ids(self):
        """Render handles empty cluster IDs gracefully."""
        from src.ui.components.match_reviewer import render_match_reviewer

        clusters = [{"entity_type": "company", "cluster_id": "", "confidence": 0.0, "record_ids": []}]
        result = render_match_reviewer(clusters)
        assert isinstance(result.value, str)

    def test_edge_case_many_entities_in_summary(self):
        """Summary handles large entity counts."""
        from src.ui.components.summary_view import render_final_summary

        entity_counts = {f"entity_{i}": i * 100 for i in range(20)}
        summary = {"total_entities": 2000, "entity_counts": entity_counts, "total_conflicts": 0, "schema_changes": 0, "errors": 0}
        result = render_final_summary(summary, [])
        assert isinstance(result.value, str)
        assert "2000" in result.value

    def test_chat_state_class_works(self):
        """ChatState class initializes and updates correctly."""
        from src.ui.gradio_app import ChatState

        state = ChatState()
        assert state.thread_id is None
        assert state.status == "idle"
        assert state.last_log == ""

        # Simulate state updates
        state.thread_id = "thread-456"
        state.status = "running"
        assert state.thread_id == "thread-456"
        assert state.status == "running"

    def test_chat_state_status_transitions(self):
        """ChatState status transitions through expected states."""
        from src.ui.gradio_app import ChatState

        state = ChatState()
        assert state.status == "idle"

        state.status = "running"
        assert state.status == "running"

        state.status = "interrupted"
        assert state.status == "interrupted"

        state.status = "completed"
        assert state.status == "completed"

        state.status = "failed"
        assert state.status == "failed"

        state.status = "aborted"
        assert state.status == "aborted"

    def test_gradio_app_has_expected_structure(self):
        """gradio_app module has expected attributes and classes."""
        import src.ui.gradio_app as app

        assert hasattr(app, "ChatState")
        assert hasattr(app, "create_app")
        assert hasattr(app, "workflow_manager")
        assert app.workflow_manager is None  # Not initialized until create_app called
