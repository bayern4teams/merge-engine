"""Planning subgraph package for merge-engine (B2.1).

Exports run_planning_swarm for bounded multi-agent planning execution.
"""

from __future__ import annotations

from src.orchestrator.planning.bounded_runner import run_planning_swarm
from src.orchestrator.planning.swarm_builder import (
    SwarmPlannerState,
    create_planning_swarm,
    derive_fk_closure,
)

__all__ = [
    "SwarmPlannerState",
    "create_planning_swarm",
    "derive_fk_closure",
    "run_planning_swarm",
]
