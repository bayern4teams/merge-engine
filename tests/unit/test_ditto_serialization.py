"""Unit test for Ditto COL/VAL serialization (Task B4.3)."""

from __future__ import annotations

from src.mcp_server.tools.resolve_entities import _format_ditto_col_val, _format_pairs_block


def test_format_ditto_col_val() -> None:
    rec = {
        "company_name": "The Boeing Company",
        "website": "https://www.boeing.com",
        "ticker": "BA",
        "_origin_id": "origin1",
    }
    formatted = _format_ditto_col_val(rec)
    assert "COL name VAL The Boeing Company" in formatted
    assert (
        "COL domain VAL https://www.boeing.com" in formatted
        or "COL website VAL https://www.boeing.com" in formatted
    )
    assert "_origin_id" not in formatted


def test_format_pairs_block() -> None:
    rec1 = {"company_name": "Boeing Co", "website": "boeing.com"}
    rec2 = {"name": "Boeing Corp", "homepage": "www.boeing.com"}
    borderline = [("id1", "id2", rec1, rec2, 0.85)]

    formatted_block = _format_pairs_block(borderline)
    assert "Pair 1:" in formatted_block
    assert "Record A: COL name VAL Boeing Co" in formatted_block or "Record A:" in formatted_block
    assert "Record B:" in formatted_block
