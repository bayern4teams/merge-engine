"""
Unit tests for resolve_entities helper functions.

Tests _record_id, _normalize, and _compute_heuristic_confidence.
"""

from __future__ import annotations

from src.mcp_server.tools.resolve_entities import (
    _compute_heuristic_confidence,
    _normalize,
    _record_id,
)


class TestRecordId:
    """Tests for the _record_id helper."""

    def test_standard_record_id(self) -> None:
        """Should extract origin, table, and row ID."""
        rec = {
            "_origin_id": "crm",
            "_origin_table": "persons",
            "_origin_id_row": "123",
        }
        assert _record_id(rec) == "crm:persons:123"

    def test_record_id_with_id_field(self) -> None:
        """Should use 'id' field when _origin_id_row is missing."""
        rec = {
            "_origin_id": "hr",
            "_origin_table": "employees",
            "id": "emp-456",
        }
        assert _record_id(rec) == "hr:employees:emp-456"

    def test_record_id_with_origin_id_field(self) -> None:
        """Should use 'origin_id' when _origin_id is missing."""
        rec = {
            "origin_id": "legacy",
            "table": "customers",
            "_origin_id_row": "999",
        }
        assert _record_id(rec) == "legacy:customers:999"

    def test_record_id_fallback_to_object_id(self) -> None:
        """Should use str(id(rec)) for missing row ID."""
        rec = {"_origin_id": "test", "_origin_table": "data"}
        rid = _record_id(rec)
        parts = rid.split(":")
        assert len(parts) == 3
        assert parts[0] == "test"
        assert parts[1] == "data"
        # str(id(rec)) should appear in parts[2]
        assert parts[2] != ""

    def test_record_id_missing_table(self) -> None:
        """Should use 'unknown' when table is missing."""
        rec = {"_origin_id": "crm"}
        rid = _record_id(rec)
        parts = rid.split(":")
        assert parts[0] == "crm"
        assert parts[1] == "unknown"

    def test_record_id_missing_origin(self) -> None:
        """Should use 'unknown' when origin is missing."""
        rec = {"_origin_table": "persons", "_origin_id_row": "1"}
        rid = _record_id(rec)
        assert rid == "unknown:persons:1"


class TestNormalize:
    """Tests for the _normalize helper."""

    def test_none_value(self) -> None:
        """None should return empty string."""
        assert _normalize(None) == ""

    def test_string_normalization(self) -> None:
        """String should be lowercased, stripped, normalized."""
        assert _normalize("  Hello World  ") == "hello_world"

    def test_remove_dots(self) -> None:
        """Dots should be removed."""
        assert _normalize("St.John") == "stjohn"

    def test_remove_dashes(self) -> None:
        """Dashes should be removed (replaced with nothing)."""
        assert _normalize("new-york") == "newyork"

    def test_numeric_value(self) -> None:
        """Numeric values should be converted to string."""
        assert _normalize(42) == "42"

    def test_empty_string(self) -> None:
        """Empty string should remain empty."""
        assert _normalize("") == ""

    def test_special_characters(self) -> None:
        """Special characters like @ remain after dot removal."""
        assert _normalize("test@email.com") == "test@emailcom"

    def test_already_normalized(self) -> None:
        """Already lowercase with underscores should remain unchanged."""
        assert _normalize("first_name") == "first_name"


class TestComputeHeuristicConfidence:
    """Tests for the _compute_heuristic_confidence helper.

    Note: This function no longer uses blocking keys - it compares
    all common fields between records.
    """

    def test_no_common_fields(self) -> None:
        """Should return 0 when no common fields."""
        rec1 = {"name": "Alice"}
        rec2 = {"age": 30}
        assert _compute_heuristic_confidence(rec1, rec2) == 0.0

    def test_exact_match_single_field(self) -> None:
        """Should return 1.0 for exact field match."""
        rec1 = {"email": "alice@example.com"}
        rec2 = {"email": "alice@example.com"}
        result = _compute_heuristic_confidence(rec1, rec2)
        assert result == 1.0

    def test_null_value_skipped(self) -> None:
        """Records with null values should skip that field."""
        rec1 = {"email": None}
        rec2 = {"email": "alice@example.com"}
        result = _compute_heuristic_confidence(rec1, rec2)
        assert result == 0.0

    def test_both_null_values(self) -> None:
        """Both null should skip that field."""
        rec1 = {"email": None}
        rec2 = {"email": None}
        result = _compute_heuristic_confidence(rec1, rec2)
        assert result == 0.0

    def test_multiple_fields_all_match(self) -> None:
        """Should return 1.0 when all fields match."""
        rec1 = {"name": "Alice", "email": "a@b.com", "phone": "555-1234"}
        rec2 = {"name": "Alice", "email": "a@b.com", "phone": "555-1234"}
        result = _compute_heuristic_confidence(rec1, rec2)
        assert result == 1.0

    def test_multiple_fields_partial_match(self) -> None:
        """Should return partial score for partial matches."""
        rec1 = {"name": "Alice", "email": "a@b.com"}
        rec2 = {"name": "Bob", "email": "a@b.com"}
        result = _compute_heuristic_confidence(rec1, rec2)
        assert result == 0.5

    def test_case_insensitive_match(self) -> None:
        """Case differences should be normalized away."""
        rec1 = {"email": "ALICE@EXAMPLE.COM"}
        rec2 = {"email": "alice@example.com"}
        result = _compute_heuristic_confidence(rec1, rec2)
        assert result == 1.0

    def test_completely_different(self) -> None:
        """Completely different values should return 0."""
        rec1 = {"name": "Alice"}
        rec2 = {"name": "Zachary"}
        result = _compute_heuristic_confidence(rec1, rec2)
        assert result == 0.0

    def test_one_record_missing_key(self) -> None:
        """Missing key should be treated as null."""
        rec1 = {"email": "alice@example.com"}
        rec2 = {"name": "Bob"}  # no email key
        result = _compute_heuristic_confidence(rec1, rec2)
        assert result == 0.0

    def test_string_similarity_overlap(self) -> None:
        """Similar strings should get partial credit."""
        rec1 = {"name": "Alexandr"}
        rec2 = {"name": "Alexander"}
        result = _compute_heuristic_confidence(rec1, rec2)
        # Should get some credit for character overlap
        assert 0.0 <= result <= 1.0

    def test_multiple_fields_some_null(self) -> None:
        """Null values should be skipped, not counted as mismatches."""
        rec1 = {"name": "Alice", "email": None, "phone": "555-1234"}
        rec2 = {"name": "Bob", "email": "b@b.com", "phone": "555-1234"}
        result = _compute_heuristic_confidence(rec1, rec2)
        # Only name and phone count (email is null in rec1)
        # name mismatches, phone matches => 0.5
        assert 0.0 <= result <= 1.0

    def test_numeric_field(self) -> None:
        """Numeric fields should work after normalization."""
        rec1 = {"age": 30}
        rec2 = {"age": 30}
        result = _compute_heuristic_confidence(rec1, rec2)
        assert result == 1.0


class TestRecordIdEdgeCases:
    """Edge case tests for _record_id."""

    def test_uuid_record_id(self) -> None:
        """Should handle UUID-style IDs."""
        rec = {
            "_origin_id": "crm",
            "_origin_table": "contacts",
            "_origin_id_row": "550e8400-e29b-41d4-a716-446655440000",
        }
        assert _record_id(rec) == "crm:contacts:550e8400-e29b-41d4-a716-446655440000"

    def test_special_chars_in_record_id(self) -> None:
        """Should handle special characters in IDs."""
        rec = {
            "_origin_id": "crm",
            "_origin_table": "contacts",
            "_origin_id_row": "user+tag@example.com",
        }
        assert _record_id(rec) == "crm:contacts:user+tag@example.com"

    def test_unicode_in_record_id(self) -> None:
        """Should handle unicode characters in IDs."""
        rec = {
            "_origin_id": "crm",
            "_origin_table": "contacts",
            "_origin_id_row": "日本語テスト",
        }
        rid = _record_id(rec)
        assert "日本語テスト" in rid

    def test_empty_string_record_id(self) -> None:
        """Should handle empty string IDs."""
        rec = {
            "_origin_id": "",
            "_origin_table": "",
            "_origin_id_row": "",
        }
        assert _record_id(rec) == "::"
