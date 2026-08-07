"""Unit test for cross-origin golden dataset (Task B4.0)."""

from __future__ import annotations

from tests.eval.cross_origin_golden import build_cross_origin_golden_pairs


def test_cross_origin_golden_pairs_structure() -> None:
    pairs = build_cross_origin_golden_pairs()
    assert len(pairs) >= 8, f"Expected at least 8 cross-origin pairs, got {len(pairs)}"

    for rec_a, rec_b, is_same, why in pairs:
        assert isinstance(rec_a, dict)
        assert isinstance(rec_b, dict)
        assert isinstance(is_same, bool)
        assert isinstance(why, str) and len(why) > 0
        assert "_origin_id" in rec_a
        assert "_origin_id" in rec_b


def test_cross_origin_boeing_pair() -> None:
    pairs = build_cross_origin_golden_pairs()
    boeing_pairs = [p for p in pairs if "Boeing company" in p[3]]
    assert len(boeing_pairs) == 1
    rec_a, rec_b, is_same, _ = boeing_pairs[0]
    assert is_same is True
    assert rec_a["company_name"] == "The Boeing Company"
    assert rec_b["name"] == "Boeing Corp"
