"""P7 attack (verifier, independent of the worker's tests).

Controls (plain asserts) pin behaviour that must hold; `xfail(strict=True)` marks a defect the
attack found: the test states the CORRECT behaviour, so it flips to XPASS (a failure) the day the
defect is fixed and the marker must then be removed.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p7_attack.py -q -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import signal
import time
from contextlib import contextmanager

import pytest

from src.mcp_server.tools import join_graph as jg
from src.mcp_server.tools import resolve_entities as tre

nre = importlib.import_module("src.orchestrator.nodes.resolve_entities")


# ── harness (multi-origin; own copy so this file does not depend on the acceptance helpers) ──
class Origin:
    def __init__(self, rows):
        self.rows = rows

    async def sample_distinct_values(self, table, column, limit=500, filter_values=None):
        vals = [v for v in dict.fromkeys(self.rows[table][column]) if v is not None]
        if filter_values is not None:
            keep = {jg.normalize_val(v) for v in filter_values}
            vals = [v for v in vals if jg.normalize_val(v) in keep]
        return vals[:limit]

    async def profile_column(self, table, column, mode="SAMPLED", sample_size=10_000):
        v = self.rows[table][column]
        nn = [x for x in v if x is not None]
        return {
            "mode": mode,
            "total_count": len(v),
            "null_count": len(v) - len(nn),
            "null_ratio": 0.0,
            "distinct_count": len(set(nn)),
            "min_value": None,
            "max_value": None,
        }

    async def extract(
        self, table_name, columns, filter=None, batch_size=1000, cursor=None, watermark=None
    ):
        cols = self.rows[table_name]
        names = list(cols) if columns in (None, ["*"]) else list(columns)
        total = len(next(iter(cols.values())))
        lo = int(cursor or 0)
        hi = min(lo + batch_size, total)
        batch = [{c: cols[c][i] for c in names} for i in range(lo, hi)]
        return {
            "rows": batch,
            "column_names": names,
            "total_count": total,
            "next_cursor": str(hi) if hi < total else None,
            "origin_ids": list(range(lo, hi)),
        }

    async def discover_indexes(self, table_name):
        return []

    async def discover_statistics(self, table_name):
        return {"row_count_estimate": len(next(iter(self.rows[table_name].values())))}

    def capabilities(self):
        return {
            "supports_declared_pk": False,
            "supports_declared_fk": False,
            "supports_sampling": True,
        }

    async def close(self):
        return None


class RunAway(BaseException):
    """Not an Exception: derive's own `except Exception` must not be able to swallow it."""


class StuckOrigin(Origin):
    """Misbehaving connector: ignores the cursor and always says there is more."""

    calls = 0

    async def extract(self, table_name, columns, filter=None, batch_size=1000, cursor=None, **kw):
        StuckOrigin.calls += 1
        if StuckOrigin.calls > 300:
            raise RunAway
        page = await super().extract(table_name, columns, batch_size=batch_size, cursor=None)
        page["next_cursor"] = "again"
        return page


class FailingOrigin(Origin):
    """Second page of any table raises (a connection dropped mid-pagination)."""

    async def extract(self, table_name, columns, filter=None, batch_size=1000, cursor=None, **kw):
        if cursor:
            raise ConnectionError("dropped mid-pagination")
        return await super().extract(table_name, columns, batch_size=batch_size, cursor=cursor)


class EmptyForeverOrigin(Origin):
    """Empty rows but a next_cursor, forever."""

    async def extract(self, table_name, columns, filter=None, batch_size=1000, cursor=None, **kw):
        if cursor:
            return {"rows": [], "column_names": [], "total_count": None, "next_cursor": "x"}
        return await super().extract(table_name, columns, batch_size=batch_size, cursor=cursor)


@contextmanager
def deadline(seconds=30):
    def boom(*_):
        raise TimeoutError(f"no result within {seconds}s")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def schemas_for(world):
    schemas, origins = {}, []
    for oid, rows in world.items():
        schemas[oid] = {
            "tables": [
                {"name": t, "columns": [{"name": c, "type": "text"} for c in cols]}
                for t, cols in rows.items()
            ]
        }
        origins.append({"id": oid, "type": "stub", "connection": {"id": oid}})
    return schemas, origins


def patch(monkeypatch, world, cls=Origin):
    def factory(_type, conn):
        return cls(world[conn["id"]])

    monkeypatch.setattr(jg, "create_connector", factory)
    monkeypatch.setattr("src.mcp_server.connectors.create_connector", factory)


def graph_of(world, monkeypatch, **kw):
    patch(monkeypatch, world)
    schemas, origins = schemas_for(world)
    with deadline():
        graph = asyncio.run(jg.build_join_graph(schemas, origins, **kw))
    return graph, schemas, origins


def derive(world, monkeypatch, cls=Origin, *, build_kw=None, **kw):
    """Graph built with a well-behaved connector; edges derived with `cls`."""
    graph, schemas, origins = graph_of(world, monkeypatch, **(build_kw or {}))
    patch(monkeypatch, world, cls)
    with deadline(kw.pop("seconds", 30)):
        return asyncio.run(jg.derive_relationship_edges(graph, schemas, origins, **kw))


def N(v):
    return jg.normalize_val(v)


def hier(out, kind):
    return {(N(e["child"][1]), N(e["parent"][1])) for e in out["edges"] if e["type"] == kind}


def person(keys, ptrs, extra=None):
    t = {"pcode": keys, "above": ptrs}
    t.update(extra or {})
    return t


def chain_world(n=60):
    keys = [f"K{i:04d}" for i in range(n)]
    ptr = [None] + keys[:-1]
    return {"o1": {"person": person(keys, ptr)}}


# ───────────────────────────── A. hierarchy walk (unit level, no graph) ──────────────────────
def walk(up, roots=()):
    shown = {k: k for k in set(up) | set(up.values()) | set(roots)}
    return jg._walk_hierarchy("t", dict(up), set(roots), shown)


def test_a1_deep_chain_200k_no_recursion_error_and_linear_time():
    n = 200_000
    up = {f"n{i}": f"n{i - 1}" for i in range(1, n)}
    t0 = time.time()
    edges, cycles = walk(up, roots=["n0"])
    assert time.time() - t0 < 20
    assert cycles == []
    ult = [e for e in edges if e["type"] == "ULTIMATE_PARENT"]
    assert len(ult) == n - 1 and all(e["parent"][1] == "n0" for e in ult)


def test_a2_many_tails_into_one_ring_report_the_ring_once():
    up = {"a": "b", "b": "c", "c": "a"}
    for i in range(50):
        up[f"t{i}"] = "a" if i % 2 else f"t{i - 1}" if i else "c"
    edges, cycles = walk(up)
    assert len(cycles) == 1 and sorted(cycles[0]["members"]) == ["a", "b", "c"]
    assert not [e for e in edges if e["type"] == "ULTIMATE_PARENT"]
    assert len([e for e in edges if e["type"] == "DIRECT_PARENT"]) == len(up)


def test_a3_two_rings_and_a_self_loop_each_listed_once_with_a_root_tree_untouched():
    up = {"a": "b", "b": "a", "x": "y", "y": "z", "z": "x", "s": "s", "c": "r", "d": "c"}
    edges, cycles = walk(up, roots=["r"])
    assert sorted(sorted(c["members"]) for c in cycles) == [["a", "b"], ["s"], ["x", "y", "z"]]
    ult = hier({"edges": edges}, "ULTIMATE_PARENT")
    assert ult == {("c", "r"), ("d", "r")}


def test_a4_chain_ending_at_a_key_never_seen_gets_no_invented_root():
    edges, cycles = walk({"a": "b", "b": "ghost"}, roots=[])
    assert cycles == []
    assert not [e for e in edges if e["type"] == "ULTIMATE_PARENT"]


# ───────────────────────────── B. derive on rows ─────────────────────────────────────────────
def test_b1_numeric_keys_with_string_pointers(monkeypatch):
    keys = [1000 + 97 * i for i in range(60)]  # sparse ints: dense sequences are not INDs for P3
    ptr = [None] + [str(k) for k in keys[:-1]]  # text pointers against int keys
    out = derive({"o1": {"person": person(keys, ptr)}}, monkeypatch)
    assert (N(keys[1]), N(keys[0])) in hier(out, "DIRECT_PARENT")
    assert (N(keys[59]), N(keys[0])) in hier(out, "ULTIMATE_PARENT")


def test_b2_blank_and_null_keys_and_pointers_are_not_edges(monkeypatch):
    keys = ["K0", "K1", "K2", "", None, "K3"] + [f"K{i}" for i in range(4, 40)]
    ptr = [None, "K0", "K1", "K0", "K0", "  "] + [f"K{i - 1}" for i in range(4, 40)]
    out = derive({"o1": {"person": person(keys, ptr)}}, monkeypatch)
    for e in out["edges"]:
        for end in (e["child"], e["parent"]):
            assert N(end[1]) != "", e
    assert (N("K3"), N("K0")) not in hier(out, "DIRECT_PARENT")  # blank pointer = root


def test_b3_pointer_to_a_key_not_in_the_table_is_not_an_edge_to_a_phantom_row(monkeypatch):
    """P3 tolerates partial containment, so a pointer can dangle. A parent edge to a row that
    does not exist is a fact about no row."""
    g = chain_world(80)
    g["o1"]["person"]["above"][40] = "NOWHERE"
    graph, schemas, origins = graph_of(g, monkeypatch)
    assert any(e.child_column == "above" for lst in graph._adjacency.values() for e in lst), (
        "precondition: P3 keeps the self-IND despite one dangling pointer"
    )
    patch(monkeypatch, g)
    out = asyncio.run(jg.derive_relationship_edges(graph, schemas, origins))
    assert (N("K0040"), N("NOWHERE")) not in hier(out, "DIRECT_PARENT")


def test_b4_two_self_inds_on_one_table_are_distinguishable(monkeypatch):
    """`above` (a hierarchy) and `replaced_by` (a succession) both are self-INDs. Both come out
    as DIRECT_PARENT, and nothing on an edge says which pointer column made it."""
    n = 60
    keys = [f"K{i:03d}" for i in range(n)]
    above = [None] + keys[:-1]
    repl = [keys[(i + 7) % n] if i % 2 else None for i in range(n)]
    out = derive({"o1": {"person": person(keys, above, {"replaced_by": repl})}}, monkeypatch)
    dp = [e for e in out["edges"] if e["type"] == "DIRECT_PARENT"]
    assert len(dp) > n - 1  # both pointers emitted
    blob = json.dumps(dp)
    assert "above" in blob and "replaced_by" in blob


def test_b4b_two_self_inds_control_both_walked_without_error(monkeypatch):
    n = 60
    keys = [f"K{i:03d}" for i in range(n)]
    repl = [keys[(i + 7) % n] if i % 2 else None for i in range(n)]
    out = derive(
        {"o1": {"person": person(keys, [None] + keys[:-1], {"replaced_by": repl})}}, monkeypatch
    )
    assert (N("K001"), N("K000")) in hier(out, "DIRECT_PARENT")


def test_b5_junction_with_three_fk_columns_pairs_every_two(monkeypatch):
    a = [f"A{i}" for i in range(30)]
    b = [f"B{i}" for i in range(30)]
    c = [f"C{i}" for i in range(30)]
    world = {
        "o1": {
            "ta": {"acode": a},
            "tb": {"bcode": b},
            "tc": {"ccode": c},
            "tri": {
                "x": [a[i // 9] for i in range(27)],  # 3 x 3 x 3 combinations: no pair is unique
                "y": [b[(i // 3) % 3] for i in range(27)],
                "z": [c[i % 3] for i in range(27)],
            },
        }
    }
    out = derive(world, monkeypatch)
    rel = [e for e in out["edges"] if e["type"] == "RELATED"]
    assert len(rel) == 3 * 27
    assert {frozenset((e["a"][0], e["b"][0])) for e in rel} == {
        frozenset(("ta", "tb")),
        frozenset(("ta", "tc")),
        frozenset(("tb", "tc")),
    }


def test_b6_junction_across_two_tables_names_each_end_by_its_own_table(monkeypatch):
    a = [f"A{i}" for i in range(40)]
    g = [f"G{i}" for i in range(8)]
    world = {
        "o1": {
            "ta": {"acode": a},
            "tg": {"gcode": g},
            "m": {
                "p": [a[i] for i in range(40) for _ in range(2)],
                "q": [g[(i + j) % 8] for i in range(40) for j in range(2)],
            },
        }
    }
    out = derive(world, monkeypatch)
    rel = [e for e in out["edges"] if e["type"] == "RELATED"]
    assert rel and all({e["a"][0], e["b"][0]} == {"ta", "tg"} for e in rel)


def test_b7_edges_say_which_origin_they_came_from(monkeypatch):
    """Two origins with the same table names (the normal merge situation). Keys collide
    across origins; an edge [table, key] alone cannot say whose row it is."""
    keys = [f"K{i:03d}" for i in range(40)]
    o1 = {"person": person(keys, [None] + keys[:-1])}
    o2 = {"person": person(keys, [None, None] + keys[:-2])}
    out = derive({"o1": o1, "o2": o2}, monkeypatch)
    assert out["edges"]
    blob = json.dumps(out)
    assert "o1" in blob and "o2" in blob


def test_b7b_two_origins_control_each_origin_walked_with_its_own_rows(monkeypatch):
    keys = [f"K{i:03d}" for i in range(40)]
    o1 = {"person": person(keys, [None] + keys[:-1])}
    o2 = {"person": person(keys, [None, None] + keys[:-2])}
    out = derive({"o1": o1, "o2": o2}, monkeypatch)
    dp = [e for e in out["edges"] if e["type"] == "DIRECT_PARENT"]
    assert len(dp) == 39 + 38  # o1 has 39 pointers, o2 38


# ───────────────────────────── C. pagination robustness ──────────────────────────────────────
def test_c1_paging_stops_when_the_connector_ignores_the_cursor(monkeypatch):
    """A cursor that never advances (or never ends) must not loop forever and grow `rows`
    without bound. 300 extract calls is far beyond any 60-row table."""
    StuckOrigin.calls = 0
    try:
        derive(chain_world(60), monkeypatch, StuckOrigin, seconds=20)
    except RunAway:
        pytest.fail("derive_relationship_edges paged >300 times over a 60-row table")


def test_c2_empty_page_with_a_cursor_forever_terminates(monkeypatch):
    out = derive(chain_world(60), monkeypatch, EmptyForeverOrigin, seconds=10)
    assert (N("K0001"), N("K0000")) in hier(out, "DIRECT_PARENT")


def test_c3_extract_raising_midway_yields_no_partial_edges_and_does_not_raise(monkeypatch):
    out = derive(chain_world(60), monkeypatch, FailingOrigin, build_kw={}, sample_size=25)
    assert out["edges"] == [] and out["cycles"] == []


def test_c3b_a_failed_read_is_reported_above_debug(monkeypatch, caplog):
    """The pointer table could not be read in full: the result looks identical to 'no
    hierarchy'. CLAUDE.md: a truncated read must not pass silently."""
    with caplog.at_level(logging.DEBUG):
        derive(chain_world(60), monkeypatch, FailingOrigin, sample_size=25)
    assert [
        r for r in caplog.records if r.levelno >= logging.WARNING and "person" in r.getMessage()
    ]


def test_c4_small_page_size_gives_the_same_edges_as_one_big_page(monkeypatch):
    g = chain_world(60)
    big = derive(g, monkeypatch)
    small = derive(g, monkeypatch, sample_size=7)
    key = lambda o: sorted((e["type"], N(e["child"][1]), N(e["parent"][1])) for e in o["edges"])  # noqa: E731
    assert key(big) == key(small) and big["edges"]


def test_c5_empty_table_and_no_pointer_tables(monkeypatch):
    assert derive({"o1": {"t": {"a": [], "b": []}}}, monkeypatch) == {"edges": [], "cycles": []}
    assert derive({"o1": {}}, monkeypatch) == {"edges": [], "cycles": []}


# ───────────────────────────── D. the index ──────────────────────────────────────────────────
COLS = ("lk_a", "lk_b")


def pairs_rows(pairs):
    return [{"lk_a": a, "lk_b": b} for a, b in pairs]


def test_d1_numeric_ids_and_text_ids_meet(monkeypatch):
    idx = tre.build_relationship_index(pairs_rows([(1, "2"), (1, 3)]), COLS, "id")
    assert idx["1"] == {"2", "3"}


def test_d2_real_shared_neighbours_still_found_by_a_record_with_its_id_only():
    idx = tre.build_relationship_index(pairs_rows([(10, 1), (10, 2), (11, 1), (11, 2)]), COLS, "id")
    shared = tre._shared_neighbors_beyond_common_parent(
        idx, {"id": 10, "name": "A"}, {"id": 11, "name": "B"}
    )
    assert shared == {"1", "2"}


def test_d3_numeric_fields_of_unrelated_companies_do_not_make_them_related():
    """Index over small integer ids (1..9 related to each other). Two unrelated companies whose
    own ids (100, 200) are not in the index, but whose `employees` / `founded_rank` values
    happen to equal indexed ids, get 'shared neighbours'. The old code read the id only from
    the company-id column and returned set() here."""
    idx = tre.build_relationship_index(pairs_rows([(5, 1), (5, 2), (6, 1), (6, 2)]), COLS, "id")
    rec1 = {"id": 100, "name": "Alpha Ltd", "employees": 5}
    rec2 = {"id": 200, "name": "Beta Ltd", "employees": 6}
    assert tre._shared_neighbors_beyond_common_parent(idx, rec1, rec2) == set()
    assert tre._relational_agreement(rec1, rec2, idx) == 0.0


def test_d3b_numeric_fields_inflate_compute_heuristic_confidence_end_to_end():
    idx = tre.build_relationship_index(pairs_rows([(5, 1), (5, 2), (6, 1), (6, 2)]), COLS, "id")
    rec1 = {"id": 100, "name": "Alpha Ltd", "employees": 5}
    rec2 = {"id": 200, "name": "Beta Ltd", "employees": 6}
    with_idx = tre._compute_heuristic_confidence(rec1, rec2, relationship_index=idx)
    without = tre._compute_heuristic_confidence(rec1, rec2, relationship_index=None)
    assert with_idx == without


def test_d4_a_record_with_two_matching_values_does_not_double_count_ids():
    idx = tre.build_relationship_index(pairs_rows([(10, 1), (10, 2), (11, 1), (11, 2)]), COLS, "id")
    a = {"id": 10, "also": "10"}
    b = {"id": 11}
    assert tre._shared_neighbors_beyond_common_parent(idx, a, b) == {"1", "2"}


def test_d5_the_common_parent_guard_survives_the_rewrite():
    idx = tre.build_relationship_index(pairs_rows([("a", "p"), ("b", "p")]), COLS, "id")
    assert tre._shared_neighbors_beyond_common_parent(idx, {"id": "a"}, {"id": "b"}) == set()


def test_d6_self_pair_row_does_not_make_a_record_its_own_shared_neighbour():
    idx = tre.build_relationship_index(
        pairs_rows([("a", "a"), ("b", "b"), ("a", "x"), ("b", "x")]), COLS, "id"
    )
    assert tre._shared_neighbors_beyond_common_parent(idx, {"id": "a"}, {"id": "b"}) == set()


def role(table, cols, target, origin="o1"):
    return {
        "table": table,
        "origin": origin,
        "fk_columns": list(cols),
        "references": [f"{c} -> {t}" for c, t in zip(cols, target)],
    }


def test_d7_person_junction_does_not_pollute_the_company_index():
    """`_relationship_index_for_companies` pools every same-target junction. A person x person
    junction (and any other same-kind junction) lands in an index that only company records
    are matched against; its small integer ids then collide with company field values (d3)."""
    company_rows = [
        {"c1": 7, "c2": 8, "_origin_table": "firm_tie", "_origin_id": "o1"},
        {"c1": 7, "c2": 9, "_origin_table": "firm_tie", "_origin_id": "o1"},
    ]
    person_rows = [
        {"p1": 1, "p2": 2, "_origin_table": "friend_tie", "_origin_id": "o1"},
        {"p1": 1, "p2": 3, "_origin_table": "friend_tie", "_origin_id": "o1"},
    ]
    state = {
        "entity_tables": {"company": [{"origin_id": "o1", "table": "firm"}]},  # lead adaptation
        "table_roles": {
            "junction": [
                role("firm_tie", ("c1", "c2"), ("firm.id", "firm.id")),
                role("friend_tie", ("p1", "p2"), ("human.id", "human.id")),
            ]
        },
    }
    extracted = {"company": company_rows, "person": person_rows}
    out = nre._relationship_index_for_companies({**state, "extracted_data": extracted}, extracted)
    assert set(out) == {"7", "8", "9"}, sorted(out)


def test_d8_node_index_filters_by_origin_and_table_and_handles_missing_rows():
    rows = [
        {"c1": "a", "c2": "b", "_origin_table": "t", "_origin_id": "o1"},
        {"c1": "x", "c2": "y", "_origin_table": "t", "_origin_id": "o2"},
        {"c1": "m", "c2": "n", "_origin_table": "other", "_origin_id": "o1"},
    ]
    state = {"table_roles": {"junction": [role("t", ("c1", "c2"), ("k.id", "k.id"))]}}
    extracted = {"whatever": rows}
    out = nre._relationship_index_for_companies({**state, "extracted_data": extracted}, extracted)
    assert out == {"a": {"b"}, "b": {"a"}}
    assert (
        nre._relationship_index_for_companies({"table_roles": None, "extracted_data": {}}, {}) == {}
    )
    three = {"table_roles": {"junction": [role("t", ("c1", "c2", "c3"), ("k.id",) * 3)]}}
    assert (
        nre._relationship_index_for_companies({**three, "extracted_data": extracted}, extracted)
        == {}
    )


# ───────────────────────────── E. reachability ───────────────────────────────────────────────
def test_e1_derive_relationship_edges_is_called_somewhere_in_a_run():
    """The report's fix emits typed edges. If nothing outside join_graph.py calls
    derive_relationship_edges, no run ever produces one."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "src"
    callers = [
        p
        for p in root.rglob("*.py")
        if p.name != "join_graph.py" and re.search(r"\bderive_relationship_edges\b", p.read_text())
    ]
    assert callers, "derive_relationship_edges has no caller outside join_graph.py"
