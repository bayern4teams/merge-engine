"""DSPy metric functions lifted directly from tests/prompts/harness.py.

Guarantees 100% score comparability with the harness evaluation scorecard.
"""

from __future__ import annotations

from typing import Any


def request_parsing_metric(example: Any, pred: Any, trace: Any = None) -> float:
    """DSPy metric for request parsing stage.

    Set equality on entity_types and explicit_entity_types.
    Returns 1.0 if match, 0.0 otherwise.
    """
    expected = getattr(example, "expected", None)
    if expected is None and isinstance(example, dict):
        expected = example.get("expected", {})

    result = pred
    if hasattr(pred, "model_dump"):
        result = pred.model_dump()
    elif hasattr(pred, "output"):
        out = pred.output
        if hasattr(out, "model_dump"):
            result = out.model_dump()
        else:
            result = out

    if not isinstance(result, dict):
        return 0.0

    got = set(result.get("entity_types", []) or [])
    want = set(expected.get("entity_types", []))
    ok = got == want

    if ok and "explicit_entity_types" in expected:
        got_explicit = set(result.get("explicit_entity_types", []) or [])
        want_explicit = set(expected.get("explicit_entity_types", []))
        ok = got_explicit == want_explicit

    return 1.0 if ok else 0.0


def field_mapping_metric(example: Any, pred: Any, trace: Any = None) -> float:
    """DSPy metric for field mapping stage.

    Per-field action (and dest_column when action=map) equality.
    Returns 1.0 if all fields match expected, 0.0 otherwise.
    """
    expected = getattr(example, "expected", None)
    if expected is None and isinstance(example, dict):
        expected = example.get("expected", {})

    result = pred
    if hasattr(pred, "model_dump"):
        result = pred.model_dump()
    elif hasattr(pred, "output"):
        out = pred.output
        if hasattr(out, "model_dump"):
            result = out.model_dump()
        else:
            result = out

    if not isinstance(result, dict):
        return 0.0

    for field, exp in expected.items():
        dec = result.get(field)
        if not isinstance(dec, dict):
            return 0.0
        if dec.get("action") != exp["action"]:
            return 0.0
        if exp["action"] == "map" and dec.get("dest_column") != exp["dest_column"]:
            return 0.0

    return 1.0
