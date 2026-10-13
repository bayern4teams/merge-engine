"""D13 attack: the shared `_LLM_CONCURRENCY` semaphore must hold on every path the acceptance suite skips.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d13_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.mcp_server.tools import resolve_entities as tre
from tests.part1 import test_d13_acceptance as acc
from tests.part1.test_d13_acceptance import (
    ENTITIES,
    GOLDEN_NODE,
    NODE_SRC,
    TOOLS_SRC,
    Stub,
    assert_each_pair_once,
    band_for,
    node_scenario,
    run_node,
    set_bound,
)

install = acc.install  # the acceptance fixture, registered under its own name here

FIVE = {et: 30 for et in ENTITIES}
OK = lambda _e, _i: {"probability": 0.99, "reason": "ok"}  # noqa: E731


# ── process-pool path: the LLM step stays in the parent coroutine ────────────────────────────
class RecordingPool(ThreadPoolExecutor):
    """Stands in for ProcessPoolExecutor (fork + a patched closure cannot be pickled)."""

    submitted: list[tuple] = []

    def submit(self, fn, /, *args, **kw):
        RecordingPool.submitted.append((fn, args))
        return super().submit(fn, *args, **kw)


def test_pool_path_semaphore_never_crosses_and_bound_holds(install, monkeypatch):
    RecordingPool.submitted = []
    monkeypatch.setattr(acc.re_node, "ProcessPoolExecutor", RecordingPool)
    for mod in (tre, acc.re_node):
        monkeypatch.setattr(mod, "_PROCESS_POOL_MIN_RECORDS", 2)
    stub = install(Stub(OK))
    run_node(FIVE, monkeypatch)
    assert len(RecordingPool.submitted) == len(FIVE), "every entity must have gone through the pool"
    for _fn, args in RecordingPool.submitted:
        assert not any(isinstance(a, asyncio.Semaphore) for a in args), (
            "a semaphore reached the pool"
        )
    assert stub.max_in_flight == tre._LLM_CONCURRENCY
    assert_each_pair_once(stub, FIVE)


# ── other callers of resolve_entities_impl ───────────────────────────────────────────────────
def _impl(et: str, n: int, sem=None):
    return tre.resolve_entities_impl(
        entity_type=et,
        records=[r for _a, _b, ra, rb, _s in band_for(acc.LETTER[et], n) for r in (ra, rb)],
        llm_config={"provider": "openai"},
        llm_semaphore=sem,
    )


def _fake_core(monkeypatch, sizes):
    bands = {et: band_for(acc.LETTER[et], n) for et, n in sizes.items()}
    monkeypatch.setattr(
        tre,
        "_resolve_core",
        lambda et, *_a, **_k: {
            "auto_match": tre._AUTO_MATCH,
            "auto_exclude": tre._AUTO_EXCLUDE,
            "candidate_pair_count": len(bands[et]),
            "blocking_stats": {},
            "scored_pairs": [],
            "borderline": list(bands[et]),
            "pair_evidence": {},
            "model_fitted": False,
            "possibly_related": [],
            "blocking_seconds": 0.0,
            "fit_model_seconds": 0.0,
            "scoring_seconds": 0.0,
        },
    )


def test_impl_passes_the_given_semaphore_through_and_unshared_callers_are_per_call(
    install, monkeypatch
):
    """A caller that shares nothing (MCP tool entry, scripts) still gets the old per-call bound of 4;
    two such calls in one loop are NOT jointly bounded -- by design, characterised here."""
    _fake_core(monkeypatch, {"person": 30, "product": 30})
    stub = install(Stub(OK))

    async def go(sem):
        return await asyncio.gather(_impl("person", 30, sem), _impl("product", 30, sem))

    asyncio.run(go(asyncio.Semaphore(3)))
    assert stub.max_in_flight == 3, "impl ignored the semaphore it was given"
    stub2 = install(Stub(OK))
    asyncio.run(go(None))
    assert stub2.max_in_flight == 2 * tre._LLM_CONCURRENCY


def test_semaphore_is_truthy_even_when_exhausted():
    """`semaphore or Semaphore(n)` would silently replace a falsy semaphore."""
    assert bool(asyncio.Semaphore(0)) is True


def test_judge_honours_a_tighter_shared_semaphore(install):
    stub = install(Stub(OK))
    pairs = band_for("a", 20)

    async def go():
        return await tre._judge_pairs_with_llm(
            "company", pairs, {"provider": "openai"}, semaphore=asyncio.Semaphore(1)
        )

    asyncio.run(go())
    assert stub.max_in_flight == 1


# ── cancellation / exceptions return every permit ────────────────────────────────────────────
def test_cancelled_judge_releases_permits_and_sibling_finishes(install):
    stub = install(Stub(OK, delay=0.05))

    async def go():
        sem = asyncio.Semaphore(2)
        a = asyncio.create_task(
            tre._judge_pairs_with_llm("e", band_for("a", 20), {"provider": "x"}, semaphore=sem)
        )
        b = asyncio.create_task(
            tre._judge_pairs_with_llm("e", band_for("b", 6), {"provider": "x"}, semaphore=sem)
        )
        await asyncio.sleep(0.12)
        a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await a
        out = await asyncio.wait_for(b, timeout=10)
        return sem._value, out

    free, out = asyncio.run(go())
    assert free == 2, f"{2 - free} permit(s) leaked by the cancelled call"
    assert len(out) == 6
    assert stub.in_flight == 0


def test_raising_calls_leave_the_shared_semaphore_whole(install):
    install(Stub(lambda e, i: ("raise", RuntimeError("down"))))

    async def go():
        sem = asyncio.Semaphore(3)
        r = await asyncio.wait_for(
            asyncio.gather(
                tre._judge_pairs_with_llm("e", band_for("a", 25), {"provider": "x"}, semaphore=sem),
                tre._judge_pairs_with_llm("e", band_for("b", 25), {"provider": "x"}, semaphore=sem),
            ),
            timeout=15,
        )
        return sem._value, r

    free, r = asyncio.run(go())
    assert free == 3
    assert [len(x) for x in r] == [25, 25]  # heuristic kept for every pair


def test_cancelling_the_node_midway_then_running_it_again_is_clean(install, monkeypatch):
    stub = install(Stub(OK, delay=0.05))

    async def go():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(acc.re_node.resolve_entities(_state(monkeypatch)), timeout=0.2)

    asyncio.run(go())
    assert stub.in_flight == 0
    stub2 = install(Stub(OK))
    run_node(FIVE, monkeypatch)
    assert stub2.max_in_flight == tre._LLM_CONCURRENCY


def _state(monkeypatch):
    """A node state over FIVE with the fake core installed (run_node, minus the asyncio.run)."""
    _fake_core(monkeypatch, FIVE)
    recs = {
        et: [r for _a, _b, ra, rb, _s in band_for(acc.LETTER[et], n) for r in (ra, rb)]
        for et, n in FIVE.items()
    }
    return {
        "run_id": "d13a",
        "target_entities": list(FIVE),
        "extracted_data": recs,
        "llm_config": {"provider": "openai"},
        "entity_clusters": {},
        "errors": [],
    }


# ── loop binding on re-entry (HITL retry / resume runs the node again) ───────────────────────
def test_node_twice_in_one_loop_and_in_a_fresh_loop(install, monkeypatch):
    stub = install(Stub(OK))

    async def twice():
        for _ in range(2):
            await acc.re_node.resolve_entities(_state(monkeypatch))

    asyncio.run(twice())
    assert stub.max_in_flight == tre._LLM_CONCURRENCY
    asyncio.run(twice())  # a new loop, as a resumed run may have
    assert stub.max_in_flight == tre._LLM_CONCURRENCY


def test_node_creates_its_semaphore_inside_the_coroutine():
    tree = ast.parse(NODE_SRC.read_text())
    top = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign | ast.AnnAssign)
        and any(isinstance(c, ast.Call) and "Semaphore" in ast.unparse(c.func) for c in ast.walk(n))
    ]
    assert not top, "a module-level semaphore binds to whichever loop touches it first"
    fn = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "resolve_entities"
    )
    assert any(
        isinstance(c, ast.Call) and "Semaphore" in ast.unparse(c.func) for c in ast.walk(fn)
    ), "semaphore must be built in the node body"


def test_two_runs_in_one_process_are_bounded_per_run_not_per_process(install, monkeypatch):
    """Characterisation: the fix bounds ONE resolve run. Two concurrent runs (two apps' worth of
    work in one loop) reach 2x -- the cross-run/cross-process ceiling is the unverified-live item."""
    stub = install(Stub(OK))

    async def go():
        await asyncio.gather(
            acc.re_node.resolve_entities(_state(monkeypatch)),
            acc.re_node.resolve_entities({**_state(monkeypatch), "run_id": "d13b"}),
        )

    asyncio.run(go())
    assert stub.max_in_flight <= 2 * tre._LLM_CONCURRENCY


# ── map_fields and resolve never overlap in one run ──────────────────────────────────────────
def test_map_fields_and_resolve_are_sequential_graph_nodes():
    from src.orchestrator.graph import NODE_SUCCESSOR

    chain, node = [], "map_fields"
    while node != "resolve_entities":
        chain.append(node)
        node = NODE_SUCCESSOR[node]
    assert chain == ["map_fields", "plan_scope", "extract_data", "validate_data"]
    out = subprocess.run(
        ["grep", "-rn", r"Send(", "src/orchestrator"], capture_output=True, text=True, cwd=acc.REPO
    )
    assert out.stdout == "", "a Send() fan-out could run nodes in parallel:\n" + out.stdout


# ── same answers under the shared bound, whatever the bound ──────────────────────────────────
@pytest.mark.parametrize("bound", [1, 2, 50])
def test_answers_identical_at_any_bound(install, monkeypatch, bound):
    install(Stub())
    set_bound(monkeypatch, bound)
    assert node_scenario(monkeypatch) == GOLDEN_NODE


def test_small_entity_behind_a_large_one_is_not_starved_or_lost(install, monkeypatch):
    sizes = {"person": 150, "product": 3}
    stub = install(Stub(OK, delay=0.005))
    run_node(sizes, monkeypatch)
    assert_each_pair_once(stub, sizes)
    assert stub.max_in_flight == tre._LLM_CONCURRENCY


# ── no fitted constants: the diff adds no number ─────────────────────────────────────────────
def test_diff_vs_parent_adds_no_numeric_literal_in_the_two_files():
    out = subprocess.run(
        [
            "git",
            "diff",
            "-U0",
            "5c5f857",
            "--",
            str(TOOLS_SRC.relative_to(acc.REPO)),
            str(NODE_SRC.relative_to(acc.REPO)),
        ],
        capture_output=True,
        text=True,
        cwd=acc.REPO,
    ).stdout
    added = [ln[1:] for ln in out.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    code = [ln for ln in added if not ln.strip().startswith("#")]
    nums = []
    for ln in code:
        try:
            tree = ast.parse(ln.strip())
        except SyntaxError:
            continue
        nums += [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, int | float)
        ]
    assert not nums, nums
