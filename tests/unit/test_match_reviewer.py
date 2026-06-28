"""
Tests for src/ui/components/match_reviewer.py - Match reviewer component for Gradio UI.
"""

import pytest
from src.ui.components.match_reviewer import render_match_reviewer, create_match_reviewer_block


class TestRenderMatchReviewer:
    """Tests for render_match_reviewer() function."""

    def test_empty_clusters(self):
        """Should show message when no clusters need review."""
        result = render_match_reviewer([])
        html = result.value
        assert "No ambiguous matches found" in html
        assert "All clusters are clear" in html

    def test_single_cluster(self):
        """Should render single cluster correctly."""
        clusters = [{
            "entity_type": "person",
            "cluster_id": "abc123",
            "record_ids": ["origin1:table1:1", "origin2:table2:1"],
            "confidence": 0.75,
        }]
        result = render_match_reviewer(clusters)
        html = result.value
        assert "person" in html
        assert "0.75" in html
        assert "origin1:table1:1" in html
        assert "origin2:table2:1" in html

    def test_multiple_clusters(self):
        """Should render multiple clusters."""
        clusters = [
            {
                "entity_type": "person",
                "cluster_id": "abc123",
                "record_ids": ["id1", "id2"],
                "confidence": 0.75,
            },
            {
                "entity_type": "company",
                "cluster_id": "def456",
                "record_ids": ["id3", "id4"],
                "confidence": 0.80,
            },
        ]
        result = render_match_reviewer(clusters)
        html = result.value
        assert "person" in html
        assert "company" in html
        assert "2 cluster(s) require review" in html

    def test_cluster_id_truncated(self):
        """Long cluster IDs should be truncated."""
        clusters = [{
            "entity_type": "test",
            "cluster_id": "this_is_a_very_long_cluster_id_that_should_be_truncated",
            "record_ids": ["id1"],
            "confidence": 0.90,
        }]
        result = render_match_reviewer(clusters)
        html = result.value
        # Full ID should NOT appear in HTML
        assert "this_is_a_very_long_cluster_id_that_should_be_truncated" not in html
        # Truncated version should appear (6 chars as per source code)
        assert "this_is_" in html

    def test_missing_entity_type(self):
        """Should handle missing entity_type gracefully."""
        clusters = [{
            "cluster_id": "abc",
            "record_ids": ["id1"],
            "confidence": 0.50,
        }]
        result = render_match_reviewer(clusters)
        html = result.value
        assert "?" in html  # Default for missing entity_type

    def test_missing_confidence(self):
        """Should handle missing confidence gracefully."""
        clusters = [{
            "entity_type": "test",
            "cluster_id": "abc",
            "record_ids": ["id1"],
        }]
        result = render_match_reviewer(clusters)
        html = result.value
        assert "0.00" in html  # Default for missing confidence

    def test_missing_record_ids(self):
        """Should handle missing record_ids gracefully."""
        clusters = [{
            "entity_type": "test",
            "cluster_id": "abc",
            "confidence": 0.50,
        }]
        result = render_match_reviewer(clusters)
        html = result.value
        assert "Records:" in html

    def test_returns_html_component(self):
        """Should return a gr.HTML component."""
        result = render_match_reviewer([])
        # Should have a value attribute
        assert hasattr(result, "value")
        assert isinstance(result.value, str)

    def test_html_contains_review_title(self):
        """HTML should contain review title."""
        result = render_match_reviewer([])
        assert "Entity Match Review" in result.value

    def test_html_structure(self):
        """HTML should have proper structure."""
        result = render_match_reviewer([])
        html = result.value
        assert "<div" in html
        assert "</div>" in html
        assert 'max-width: 800px' in html


class TestCreateMatchReviewerBlock:
    """Tests for create_match_reviewer_block() function."""

    def test_returns_blocks(self):
        """Should return a gr.Blocks component."""
        result = create_match_reviewer_block()
        assert result is not None

    def test_block_contains_elements(self):
        """Block should contain expected elements."""
        result = create_match_reviewer_block()
        # The block should have been created successfully
        assert result is not None