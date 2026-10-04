"""P1 attack checks (verifier). Expected: coalesce parity FAILS on the current implementation."""

from __future__ import annotations

import asyncio

from src.mcp_server.tools import map_fields as mf

DEST = {
    "table": "company",
    "columns": [{"name": c, "type": "text"} for c in ["id", "name", "legal_name", "postal_code"]],
}


def _map(fields: list[str]) -> dict:
    sch = {f: {"type": "text", "sample_value": "v", "sample_values": ["v"]} for f in fields}
    return asyncio.run(mf.map_fields_impl("company", sch, DEST, llm_config=None, connector=None))


def test_concat_twin_coalesces_like_camel_when_target_taken():
    """Two origins: another origin's `name` claims the column; the concatenated twin must
    coalesce exactly as its camelCase twin does (report: `companyname` vs `companyName`)."""
    camel = _map(["name", "companyName"])["field_mapping"]
    concat = _map(["name", "companyname"])["field_mapping"]
    assert "companyName" in camel  # control: today's behaviour for the delimited twin
    assert "companyname" in concat, f"companyname dropped while companyName coalesces: {concat}"


def test_very_long_composed_name_does_not_raise():
    """A 700-char name composed of vocabulary words must not crash the entity (RecursionError)."""
    mf._tokenize_field("a" * 700, ["a"])
