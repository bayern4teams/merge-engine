"""P17 gate hook (lead): the pre-load gate payload names the defective tables before approval.

The live payload is built by WorkflowManager (the gate body never runs before the pause, plan 2.6);
the gate body's own payload carries the same key. Both read the destination read-only.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from src.orchestrator.nodes import human_nodes
from src.orchestrator.nodes import load_data as nld
from src.orchestrator.workflow_manager import WorkflowManager
from tests.part1.test_p17_acceptance import SHAPES, _dest, _state


def _payloads(state, dest):
    with patch.object(nld, "_get_destination_connector", lambda cfg: dest):
        live = WorkflowManager._build_interrupt_data_inner("human_final_approval", state)
        body = asyncio.run(human_nodes.human_final_approval(state))["interrupt_data"]
    return live["data"]["destination_defects"], body["data"]["destination_defects"]


@pytest.mark.parametrize("name", list(SHAPES))
def test_defective_table_is_named_at_the_pre_load_gate(name):
    shape = SHAPES[name]
    live, body = _payloads(_state(shape), _dest(shape))
    assert live == body
    assert [d["table"] for d in live] == [shape["table"]]
    assert set(live[0]["missing_columns"]) == set(shape["links"])
    assert live[0]["unprojectable_rows"] == shape["n"]
    assert "held_links" not in live[0]  # grows with the row count; the load report keeps it
    json.dumps(live)


@pytest.mark.parametrize("name", list(SHAPES))
def test_fixed_destination_shows_no_defect(name):
    shape = SHAPES[name]
    assert _payloads(_state(shape), _dest(shape, linked=True)) == ([], [])


def test_unreachable_destination_does_not_break_the_gate():
    def boom(cfg):
        raise ConnectionError("down")

    shape = next(iter(SHAPES.values()))
    with patch.object(nld, "_get_destination_connector", boom):
        data = WorkflowManager._build_interrupt_data_inner("human_final_approval", _state(shape))
    assert data["data"]["destination_defects"] == []


def test_preview_is_read_only():
    shape = next(iter(SHAPES.values()))
    dest = _dest(shape)
    _payloads(_state(shape), dest)
    assert dest.ddl == [] and dest.writes == [] and dest.cols == dest.cols0
