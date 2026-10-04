"""P11 acceptance (derived from the report's Evidence + Fix text only; no P11 implementation seen).

Report: "`map_fields` is 44 % of runtime, run serially".
  Fix: run per-entity mapping concurrently with a bounded pool, matching the existing
  `_LLM_CONCURRENCY` pattern already used in `resolve_entities`.  Ceiling: concurrency is bounded by
  the inference endpoint, not by this code.
Plan section 9: output identical to serial and in deterministic order; exceptions propagate;
concurrency never exceeds the bound; faster than serial against a stub with latency.

Observation point: the REAL `map_fields` node (src/orchestrator/nodes/map_fields_node.py) with only
`map_fields_impl` (the per-entity LLM call) and the destination connector faked.  The stub counts
in-flight calls, so the bound is measured, not assumed.

NOTE (recorded in .handoff/P11.acceptance.md): the file is NOT serial today.  P1.5-B.1 already put the
per-entity calls behind an UNBOUNDED `asyncio.gather`.  So the report's "serially" is stale and the
live defect is the missing bound: tests 1, 2, 4 pass today (regression guards); 3, 3b, 3c, 7 FAIL.

Run:  TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p11_acceptance.py -q -s -p no:cacheprovider

ADAPTATION POINTS (the report does not fix these; retarget HERE, never in the assertions):
  NODE                      : module that owns `map_fields`.
  STUB_TARGETS              : where `map_fields_impl` is looked up (node module attr, patched by name).
  REF_BOUND_MODULE/NAME     : the existing pattern the bound must mirror (resolve_entities._LLM_CONCURRENCY).
  BOUND_NAME_RE             : names in NODE that count as "the declared bound" (a new constant OR the
                              re-imported _LLM_CONCURRENCY).  If NODE declares none, REF value is the bound.
"""

from __future__ import annotations

import ast
import asyncio
import random
import re
import time
from pathlib import Path
from typing import Any

import pytest

from src.mcp_server.tools import resolve_entities as ref_mod
from src.orchestrator.nodes import map_fields_node as NODE

REPO = Path(__file__).resolve().parents[2]
NODE_FILE = REPO / "src" / "orchestrator" / "nodes" / "map_fields_node.py"
REF_BOUND_NAME = "_LLM_CONCURRENCY"
REF_BOUND = getattr(ref_mod, REF_BOUND_NAME)
BOUND_NAME_RE = re.compile(r"CONCURR|POOL|WORKERS|PARALLEL|SEMAPHORE", re.I)
STUB_TARGETS = [(NODE, "map_fields_impl")]


def declared_bound_names() -> list[str]:
    return [
        n
        for n in dir(NODE)
        if BOUND_NAME_RE.search(n)
        and isinstance(getattr(NODE, n), int)
        and not isinstance(getattr(NODE, n), bool)
    ]


def declared_bound() -> int:
    names = declared_bound_names()
    return getattr(NODE, names[0]) if names else REF_BOUND


class Fatal(BaseException):
    """A non-Exception failure: the node must re-raise it, not file it under errors."""


# --------------------------------------------------------------------------- harness
class Probe:
    """Instrumented stand-in for map_fields_impl: counts in-flight calls, randomises latency."""

    def __init__(self, latency=lambda e: 0.0, raises=None, fatal=()):
        self.latency = latency
        self.raises = raises or {}
        self.fatal = set(fatal)
        self.inflight = 0
        self.peak = 0
        self.calls: list[str] = []
        self.finished: list[str] = []
        self.schemas: dict[str, set[str]] = {}

    async def __call__(self, *, entity_type, unified_schema, **kw):
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        self.calls.append(entity_type)
        self.schemas[entity_type] = set(unified_schema)
        try:
            await asyncio.sleep(self.latency(entity_type))
            if entity_type in self.fatal:
                raise Fatal(entity_type)
            if entity_type in self.raises:
                raise RuntimeError(self.raises[entity_type])
            self.finished.append(entity_type)
            return payload(entity_type)
        finally:
            self.inflight -= 1


def payload(e: str) -> dict[str, Any]:
    return {
        "field_mapping": {
            f"{e}_name": {
                "dest_column": f"{e}_col",
                "confidence": 0.9,
                "requires_schema_change": False,
            }
        },
        "schema_changes": [{"column": f"{e}_new", "entity_type": e}],
        "unmapped_fields": [{"field": f"{e}_u", "confidence": 0.1}],
    }


def names(n: int) -> list[str]:
    return [f"ent{i:02d}" for i in range(n)]


def make_state(ents: list[str], empty=()) -> dict[str, Any]:
    return {
        "target_entities": list(ents),
        "unified_entities": {
            e: ([] if e in empty else [{"id": 1, f"{e}_name": "v", "_internal": 1}]) for e in ents
        },
        "destination": {"type": "postgresql", "tables": {}},
        "field_mappings": {},
        "pending_schema_changes": [{"column": "pre_existing"}],
        "unmapped_fields": {},
        "errors": [],
    }


def expected(ents: list[str], bad=()) -> dict[str, Any]:
    """The serial reference, computed independently of the node: entities merged in target order."""
    fm, pend, un, errs = {}, [{"column": "pre_existing"}], {}, []
    for e in ents:
        if e in bad:
            fm[e] = {}
            errs.append(f"Field mapping failed for '{e}': boom-{e}")
            continue
        p = payload(e)
        fm[e] = p["field_mapping"]
        pend += p["schema_changes"]
        un[e] = p["unmapped_fields"]
    return {
        "field_mappings": fm,
        "pending_schema_changes": pend,
        "unmapped_fields": un,
        "errors": errs,
    }


def install(monkeypatch, probe: Probe) -> None:
    for mod, attr in STUB_TARGETS:
        monkeypatch.setattr(mod, attr, probe)
    import src.mcp_server.tools.load_data as ld

    def _no_db(*a, **k):
        raise RuntimeError("no destination in this test")

    monkeypatch.setattr(ld, "_get_destination_connector", _no_db)


def run_node(monkeypatch, probe, state, timeout=20):
    install(monkeypatch, probe)
    return asyncio.run(asyncio.wait_for(NODE.map_fields(state), timeout))


def view(out: dict[str, Any]) -> dict[str, Any]:
    return {
        k: out[k] for k in ("field_mappings", "pending_schema_changes", "unmapped_fields", "errors")
    }


def many() -> list[str]:
    """Enough entities that a bound is observable (3x the declared bound, at least 12)."""
    return names(max(12, declared_bound() * 3))


# --------------------------------------------------------------------------- 1. identical + deterministic
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_1_output_identical_to_serial_regardless_of_completion_order(monkeypatch, seed):
    ents = names(10)
    rng = random.Random(seed)
    lat = {e: rng.uniform(0.0, 0.04) for e in ents}
    probe = Probe(latency=lambda e: lat[e])
    out = run_node(monkeypatch, probe, make_state(ents))
    assert view(out) == expected(ents)
    # dict AND list order are those of target_entities, not of completion order
    assert list(out["field_mappings"]) == ents
    assert list(out["unmapped_fields"]) == ents
    assert [c["column"] for c in out["pending_schema_changes"]] == ["pre_existing"] + [
        f"{e}_new" for e in ents
    ]


def test_1b_reversed_latency_gives_the_same_output(monkeypatch):
    """Earlier entities slowest => completion order is the reverse of target order."""
    ents = names(8)
    probe = Probe(latency=lambda e: 0.002 * (len(ents) - ents.index(e)))
    out = run_node(monkeypatch, probe, make_state(ents))
    assert view(out) == expected(ents)
    assert list(out["field_mappings"]) == ents


def test_1c_each_call_gets_its_own_entitys_schema(monkeypatch):
    """No cross-contamination between concurrent calls (per-entity inputs are not shared state)."""
    ents = names(10)
    probe = Probe(latency=lambda e: 0.001)
    run_node(monkeypatch, probe, make_state(ents))
    for e in ents:
        assert probe.schemas[e] == {"id", f"{e}_name"}, probe.schemas[
            e
        ]  # "_internal" skipped, nothing foreign


def test_1d_key_order_deterministic_with_skipped_entities(monkeypatch):
    """An entity with no fields is skipped (mapping {}); output must still not depend on timing."""
    ents = names(9)
    empty = {ents[2], ents[5]}
    outs = []
    for seed in range(4):
        rng = random.Random(seed)
        lat = {e: rng.uniform(0, 0.03) for e in ents}
        outs.append(
            run_node(monkeypatch, Probe(latency=lambda e, lat=lat: lat[e]), make_state(ents, empty))
        )
    for o in outs:
        assert view(o) == view(outs[0])
        assert list(o["field_mappings"]) == list(outs[0]["field_mappings"])
        assert set(o["field_mappings"]) == set(ents)
        assert all(o["field_mappings"][e] == {} for e in empty)
    assert view(outs[0])["pending_schema_changes"] == [{"column": "pre_existing"}] + [
        {"column": f"{e}_new", "entity_type": e} for e in ents if e not in empty
    ]


@pytest.mark.parametrize("n", [0, 1, 2])
def test_1e_degenerate_sizes(monkeypatch, n):
    ents = names(n)
    probe = Probe()
    out = run_node(monkeypatch, probe, make_state(ents))
    assert view(out) == expected(ents)
    assert sorted(probe.calls) == ents
    assert out["interrupt_data"] is None


# --------------------------------------------------------------------------- 2. exceptions propagate
def test_2_failing_entity_lands_in_errors_exactly_as_in_serial(monkeypatch):
    ents = names(8)
    bad = {ents[3]: f"boom-{ents[3]}"}
    probe = Probe(latency=lambda e: 0.002 * (e != ents[3]), raises=bad)
    out = run_node(monkeypatch, probe, make_state(ents))
    assert view(out) == expected(ents, bad={ents[3]})
    assert out["errors"] == [f"Field mapping failed for '{ents[3]}': boom-{ents[3]}"]
    assert len(probe.finished) == 7, "a sibling was cancelled/skipped because one entity raised"


def test_2b_many_failures_do_not_leak_slots_or_deadlock_and_errors_are_in_target_order(monkeypatch):
    ents = many()
    bad_set = {e for i, e in enumerate(ents) if i % 2 == 0}
    rng = random.Random(7)
    lat = {e: rng.uniform(0, 0.02) for e in ents}
    probe = Probe(latency=lambda e: lat[e], raises={e: f"boom-{e}" for e in bad_set})
    out = run_node(
        monkeypatch, probe, make_state(ents), timeout=10
    )  # a leaked slot hangs -> TimeoutError
    assert view(out) == expected(ents, bad=bad_set)
    assert sorted(probe.calls) == ents


def test_2c_non_exception_failure_still_propagates(monkeypatch):
    """BaseException (not Exception) was never swallowed into errors; it must still escape."""
    ents = names(6)
    with pytest.raises(Fatal):
        run_node(monkeypatch, Probe(fatal={ents[2]}), make_state(ents))


def test_2d_all_entities_failing(monkeypatch):
    ents = names(6)
    out = run_node(monkeypatch, Probe(raises={e: f"boom-{e}" for e in ents}), make_state(ents))
    assert view(out) == expected(ents, bad=set(ents))


# --------------------------------------------------------------------------- 3. bound never exceeded (NEGATIVE on today's code)
def test_3_in_flight_never_exceeds_declared_bound(monkeypatch):
    ents = many()
    probe = Probe(latency=lambda e: 0.03)
    out = run_node(monkeypatch, probe, make_state(ents))
    bound = declared_bound()
    assert view(out) == expected(ents)
    assert probe.peak <= bound, (
        f"peak in-flight {probe.peak} > bound {bound} for {len(ents)} entities (unbounded gather)"
    )
    assert probe.peak >= min(2, bound, len(ents)), (
        f"peak {probe.peak}: not concurrent at all (serial)"
    )


def test_3b_bound_is_enforced_by_the_declared_constant(monkeypatch):
    """Shrinking the declared bound shrinks the observed peak => the constant is what bounds it."""
    targets = [(NODE, n) for n in declared_bound_names()]
    if hasattr(NODE, REF_BOUND_NAME):
        pass  # already in declared names when re-imported
    targets.append((ref_mod, REF_BOUND_NAME))  # honoured if the node reads it through the module
    if not declared_bound_names():
        pytest.skip(
            "node declares no bound constant of its own (bound lives in a shared helper): retarget"
        )
    for forced in (1, 2):
        for mod, name in targets:
            monkeypatch.setattr(mod, name, forced)
        probe = Probe(latency=lambda e: 0.01)
        run_node(monkeypatch, probe, make_state(names(10)))
        assert probe.peak <= forced, (
            f"bound forced to {forced}, observed peak {probe.peak}: bound is hardcoded elsewhere"
        )


def test_3c_bound_is_per_pool_not_a_one_off_burst(monkeypatch):
    """Throughout the run, not only at start: record every in-flight sample, all must be <= bound."""
    ents = many()
    samples: list[int] = []
    probe = Probe(latency=lambda e: random.Random(e).uniform(0.005, 0.03))
    orig = probe.__call__.__func__

    async def sampled(self, **kw):
        samples.append(self.inflight + 1)
        return await orig(self, **kw)

    monkeypatch.setattr(Probe, "__call__", sampled)
    run_node(monkeypatch, probe, make_state(ents))
    assert max(samples) <= declared_bound(), (max(samples), declared_bound())
    assert len(samples) == len(ents)


def test_3d_repeated_and_overlapping_runs_are_safe(monkeypatch):
    """No loop-bound module-level semaphore (breaks 2nd asyncio.run / overlapping runs); no cross-run leakage."""
    ents_a, ents_b = many(), [f"zz{e}" for e in many()]
    install(monkeypatch, probe := Probe(latency=lambda e: 0.01))
    for _ in range(2):  # two separate event loops
        out = asyncio.run(asyncio.wait_for(NODE.map_fields(make_state(ents_a)), 20))
        assert view(out) == expected(ents_a)
    assert probe.peak <= declared_bound()

    async def both():
        return await asyncio.gather(
            NODE.map_fields(make_state(ents_a)), NODE.map_fields(make_state(ents_b))
        )

    probe.peak = 0
    oa, ob = asyncio.run(asyncio.wait_for(both(), 20))
    assert view(oa) == expected(ents_a) and view(ob) == expected(ents_b)
    assert probe.peak <= 2 * declared_bound(), (
        "two overlapping runs may each use the bound, never more"
    )


# --------------------------------------------------------------------------- 4. faster than serial
def test_4_wall_time_well_below_serial_sum(monkeypatch):
    ents, lat = names(8), 0.2
    serial_sum = len(ents) * lat  # 1.6 s
    probe = Probe(latency=lambda e: lat)
    t0 = time.perf_counter()
    out = run_node(monkeypatch, probe, make_state(ents))
    wall = time.perf_counter() - t0
    assert view(out) == expected(ents)
    # bound 4 => ~0.4 s; unbounded => ~0.2 s; serial => 1.6 s.  Generous margin: 0.6 x serial.
    assert wall < 0.6 * serial_sum, (
        f"wall {wall:.2f}s vs serial sum {serial_sum:.2f}s (peak {probe.peak})"
    )
    assert probe.peak >= 2


def test_4b_bound_is_not_serialising_work_beyond_what_the_bound_implies(monkeypatch):
    """Lower bound too: with bound B the ideal is ceil(N/B)*lat; allow 2x slack.  Catches a pool of 1."""
    ents, lat = many(), 0.05
    b = declared_bound()
    probe = Probe(latency=lambda e: lat)
    t0 = time.perf_counter()
    run_node(monkeypatch, probe, make_state(ents))
    wall = time.perf_counter() - t0
    ideal = -(-len(ents) // b) * lat
    assert wall < 2 * ideal + 0.3, f"wall {wall:.2f}s vs ideal {ideal:.2f}s for bound {b}"


# --------------------------------------------------------------------------- 6. no fitted constants (plan 2.4)
LITERALS = (
    r"France|US|SAS|Berhad|Global|Group|Holdings|companyname|main_entity_id|company_relation|staff"
)
BASELINE_MODULE_NUMERICS = {
    "_MAX_SAMPLE_RECORDS": 500,
    "_MAX_SAMPLE_VALUES": 10,
}  # today, 2026-10-04


def test_6a_no_corpus_literals_in_node():
    hits = [
        (i, line)
        for i, line in enumerate(NODE_FILE.read_text().splitlines(), 1)
        if re.search(rf"\b({LITERALS})\b", line)
    ]
    assert hits == [], hits  # baseline today: zero hits


def test_6b_bound_mirrors_the_existing_pattern_not_a_new_number():
    """Today's module numerics: _MAX_SAMPLE_RECORDS=500, _MAX_SAMPLE_VALUES=10 (pre-existing, not P11).
    P11 may add ONE name for its bound, and its value must be the existing _LLM_CONCURRENCY (re-imported or equal)."""
    tree = ast.parse(NODE_FILE.read_text())
    new: dict[str, Any] = {}
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            tgt = n.targets[0] if isinstance(n, ast.Assign) else n.target
            val = n.value
            if (
                isinstance(tgt, ast.Name)
                and isinstance(val, ast.Constant)
                and isinstance(val.value, (int, float))
                and BASELINE_MODULE_NUMERICS.get(tgt.id) != val.value
            ):
                new[tgt.id] = val.value
    for name, value in new.items():
        assert value == REF_BOUND, (
            f"new numeric constant {name}={value} is not the existing {REF_BOUND_NAME}={REF_BOUND}"
        )
    for name, value in BASELINE_MODULE_NUMERICS.items():
        assert getattr(NODE, name) == value, f"pre-existing {name} changed"
    assert isinstance(REF_BOUND, int) and REF_BOUND >= 1


def test_6c_no_inline_numeric_pool_size():
    """Semaphore(8) / ThreadPoolExecutor(max_workers=6) / chunk sizes inline would be a hidden fitted constant."""
    tree = ast.parse(NODE_FILE.read_text())
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            fn = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if re.search(r"Semaphore|Executor|Pool|Limiter|Queue", fn):
                args = list(n.args) + [k.value for k in n.keywords]
                if any(
                    isinstance(a, ast.Constant) and isinstance(a.value, (int, float)) for a in args
                ):
                    bad.append((n.lineno, fn))
    assert bad == [], bad


def test_6d_no_module_level_mutable_state_added():
    """Plan 2.5 / CLAUDE.md: a module-level semaphore/list/dict shared across runs cross-contaminates them."""
    tree = ast.parse(NODE_FILE.read_text())
    bad = []
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            v = n.value
            if isinstance(v, (ast.List, ast.Dict, ast.Set)) or (
                isinstance(v, ast.Call)
                and re.search(
                    r"Semaphore|Lock|Queue|defaultdict|dict|list|set",
                    getattr(v.func, "attr", getattr(v.func, "id", "")),
                )
            ):
                tgt = n.targets[0] if isinstance(n, ast.Assign) else n.target
                bad.append((n.lineno, getattr(tgt, "id", "?")))
    assert bad == [], bad
