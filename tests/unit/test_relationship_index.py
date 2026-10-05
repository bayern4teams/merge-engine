"""Unit tests for build_relationship_index (audit §13 #3, fix-v3 B.5.1)."""

from __future__ import annotations

from src.mcp_server.tools.resolve_entities import build_relationship_index

# P7: the junction's FK columns are passed in (found by structure, not by name).
COLS = ("companyid", "relatedcompanyid")
KEY = "companyid"  # the referenced key column a record's own id is read from


class TestBuildRelationshipIndex:
    def test_a_relation_row_indexes_both_sides_symmetrically(self) -> None:
        rows = [{"companyid": "C1", "relatedcompanyid": "C2", "relationtype": "PARENT"}]
        index = build_relationship_index(rows, COLS, KEY)
        assert index["c1"] == {"c2"}
        assert index["c2"] == {"c1"}

    def test_multiple_rows_accumulate_neighbors(self) -> None:
        rows = [
            {"companyid": "C1", "relatedcompanyid": "C2"},
            {"companyid": "C1", "relatedcompanyid": "C3"},
        ]
        index = build_relationship_index(rows, COLS, KEY)
        assert index["c1"] == {"c2", "c3"}

    def test_rows_missing_either_id_are_skipped(self) -> None:
        rows = [
            {"companyid": "C1", "relatedcompanyid": None},
            {"companyid": None, "relatedcompanyid": "C2"},
            {"companyid": "", "relatedcompanyid": "C3"},
        ]
        assert build_relationship_index(rows, COLS, KEY) == {}

    def test_empty_input_produces_empty_index(self) -> None:
        assert build_relationship_index([], COLS, KEY) == {}
        assert build_relationship_index(None, COLS, KEY) == {}

    def test_ids_are_case_and_whitespace_normalized_for_lookup(self) -> None:
        rows = [{"companyid": " C1 ", "relatedcompanyid": "c2"}]
        index = build_relationship_index(rows, COLS, KEY)
        assert index["c1"] == {"c2"}
