"""Golden eval for the scope planner (P.8).

Scores the deterministic heuristic planner against the golden cases in
tests/eval/scope_plans.json. The heuristic is what runs when the LLM is
unavailable, and it is the floor the LLM plan is only ever allowed to improve
on — so pinning it here pins the worst case the demo can produce. Add a case to
the JSON to lock in a new scenario; no code change is needed.

Structure is scored (which table, matched or linked, on which column /
join path), not the subject value, because the plan's shape depends on the
schema, not on whether the company is called Nova or Globex.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.mcp_server.tools.join_graph import build_join_graph
from src.mcp_server.tools.plan_scope import _heuristic_plan

_CASES_FILE = Path(__file__).resolve().parents[1] / "eval" / "scope_plans.json"
_CASES = json.loads(_CASES_FILE.read_text(encoding="utf-8"))["cases"]


def _key(entry: dict) -> tuple:
    if entry["mode"] == "match":
        return (entry["origin_id"], entry["table"], entry["mode"], tuple(entry["columns"]))
    hops = tuple(
        (h["from_table"], h["from_column"], h["to_table"], h["to_column"])
        for h in entry.get("path", [])
    )
    return (entry["origin_id"], entry["table"], entry["mode"], hops)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
async def test_scope_plan_matches_golden(case: dict) -> None:
    graph = await build_join_graph(case["schema"], origins=[])
    plan = _heuristic_plan(case["subject_entity"], case["target_entities"], case["schema"], graph)
    for entity, expected_entries in case["expected"].items():
        got = {_key(e) for e in plan.get(entity, [])}
        want = {_key(e) for e in expected_entries}
        assert got == want, (
            f"[{case['name']}] entity '{entity}': expected {sorted(want)}, got {sorted(got)}"
        )
