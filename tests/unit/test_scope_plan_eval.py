"""Golden eval for the scope planner (P.8).

Scores the deterministic heuristic planner against the golden cases in
tests/eval/scope_plans.json. The heuristic is what runs when the LLM is
unavailable, and it is the floor the LLM plan is only ever allowed to improve
on — so pinning it here pins the worst case the demo can produce. Add a case to
the JSON to lock in a new scenario; no code change is needed.

Structure is scored (which table, matched or linked, on which column), not the
subject value, because the plan's shape depends on the schema, not on whether
the company is called Nova or Globex.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.mcp_server.tools.plan_scope import _heuristic_plan

_CASES_FILE = Path(__file__).resolve().parents[1] / "eval" / "scope_plans.json"
_CASES = json.loads(_CASES_FILE.read_text(encoding="utf-8"))["cases"]


def _key(entry: dict) -> tuple:
    return (entry["origin_id"], entry["table"], entry["mode"], tuple(entry["columns"]))


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_scope_plan_matches_golden(case: dict) -> None:
    plan = _heuristic_plan(case["subject_entity"], case["target_entities"], case["schema"])
    for entity, expected_entries in case["expected"].items():
        got = {_key(e) for e in plan.get(entity, [])}
        want = {_key(e) for e in expected_entries}
        assert got == want, (
            f"[{case['name']}] entity '{entity}': expected {sorted(want)}, got {sorted(got)}"
        )
