"""D3 acceptance (derive): a single-entity run can never be refused for a giant cluster.

Source: docs/plans/part1/LOCAL_VERIFY_REPORT.md D3 (Repro / Root cause / Fix shape / Verify) and the user
decision in HANDOFF.md Next action 4: rule 1 -- a SINGLE-entity run gets NO small-entity slack (the plain 1/2
cap applies); slack stays for multi-entity runs, judged against the OTHER (larger) entities (leave-one-out).
Written against the report text and the gate's public contract (``pre_load_gate`` payload); no fix was seen.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_d3_acceptance.py -q -p no:cacheprovider

Oracle: every per-entity share below is recomputed here from the cluster sizes
((largest - 1) / (records - 1)), never read back from the gate.
"""

from __future__ import annotations

import ast
import copy
import json
import random
import re
import statistics
import subprocess
import warnings
from pathlib import Path

import pytest

from src.orchestrator.nodes.load_data import _pre_load_failure, pre_load_gate

ROOT = Path(__file__).resolve().parents[2]
SRC_FILE = "src/orchestrator/nodes/load_data.py"
BASE_SHA = "2f90c83867319c5af1fd3edec98a26f0d1167037"  # HEAD of feat/part1-fixes when D3 was derived
CAP = 1 / 2  # "never a majority"
SHARE = "largest_cluster_share"


# ── builder: entity -> list of cluster sizes ───────────────────────────────────────────────────
def build(spec: dict[str, list[int]], *, null_names: dict[str, int] | None = None) -> dict:
    """spec = {entity: [cluster sizes]}. Members of one cluster share ONE name (so P16 flags nothing),
    singletons get distinct names. ``null_names`` = {entity: k} turns the first k singletons' name to None."""
    null_names = null_names or {}
    extracted, clusters, maps, unmapped = {}, {}, {}, {}
    for e, sizes in spec.items():
        recs, cls, row = [], [], 0
        k_null = null_names.get(e, 0)
        for ci, s in enumerate(sizes):
            ids = []
            for _ in range(s):
                row += 1
                nm = f"{e}-name-{ci}" if s > 1 else f"{e}-solo-{row}"
                if s == 1 and k_null > 0:
                    nm, k_null = None, k_null - 1
                recs.append(
                    {"_origin_id": "o", "_origin_table": e, "id": row, "name": nm, "val": f"v{row}"}
                )
                ids.append(f"o:{e}:{row}")
            cls.append({"cluster_id": f"{e}-c{ci}", "record_ids": ids})
        extracted[e], clusters[e] = recs, cls
        maps[e] = {"name": "name", "val": "val"}
        unmapped[e] = []
    return {
        "extracted_data": extracted,
        "entity_clusters": clusters,
        "unified_entities": {e: [] for e in spec},
        "field_mappings": maps,
        "unmapped_fields": unmapped,
    }


def share(sizes: list[int]) -> float:
    n = sum(sizes)
    return (max(sizes) - 1) / (n - 1) if n > 1 else 0.0


def gate(state, conf=None):
    return pre_load_gate(copy.deepcopy(state), conf)


def consistent(g: dict) -> list[str]:
    """Names of invariants whose payload contradicts itself: measured beyond the threshold but violated False."""
    bad = []
    for name, inv in g["invariants"].items():
        beyond = (
            inv["measured"] < inv["threshold"]
            if "cover" in name
            else inv["measured"] > inv["threshold"]
        )
        if beyond and not inv["violated"]:
            bad.append(f"{name}: measured {inv['measured']} vs threshold {inv['threshold']}")
    return bad


def giant_spec(n_records: int, giant: int, others: str = "singles") -> list[int]:
    rest = n_records - giant
    return [giant] + ([1] * rest if others == "singles" else [2] * (rest // 2) + [1] * (rest % 2))


# ═══ (a) single entity, giant cluster -> refused ══════════════════════════════════════════════════
def test_a1_the_reports_fixture_30_records_one_cluster_of_21_is_refused():
    st = build({"person": giant_spec(30, 21)})
    g = gate(st)
    inv = g["invariants"][SHARE]
    assert inv["measured"] == pytest.approx(20 / 29)
    assert inv["violated"] and g["would_fail"]
    assert g["entities_violating"][SHARE] == ["person"]
    assert consistent(g) == []
    fail = _pre_load_failure(g)  # what load_data enforces (not the gate body: resume trap)
    assert fail and fail["disposition"] == "fail" and SHARE in fail["error"]


@pytest.mark.parametrize(
    "n,giant",
    [(30, 16), (10, 7), (100, 52), (1000, 600), (1000, 999), (50, 50), (7, 5), (3, 3)],
)
def test_a2_every_share_above_one_half_is_refused_at_every_scale(n, giant):
    sizes = giant_spec(n, giant)
    assert share(sizes) > CAP
    g = gate(build({"e": sizes}))
    assert g["invariants"][SHARE]["violated"] and g["would_fail"], g["invariants"][SHARE]
    assert g["invariants"][SHARE]["threshold"] <= CAP
    assert consistent(g) == []


@pytest.mark.parametrize("others", ["singles", "pairs"])
def test_a3_giant_beside_pairs_or_singles_is_refused(others):
    g = gate(build({"e": giant_spec(400, 300, others)}))
    assert g["invariants"][SHARE]["violated"] and g["would_fail"]


def test_a4_ui_echoing_the_proposals_back_is_not_an_edit_and_does_not_lift_the_cap():
    st = build({"person": giant_spec(30, 21)})
    first = gate(st)
    echo = {k: v["threshold"] for k, v in first["invariants"].items()}
    again = gate(st, echo)
    assert again["would_fail"] and again["invariants"][SHARE]["violated"]


def test_a5_single_entity_violation_is_exactly_share_above_the_cap_random_worlds():
    """Oracle: with ONE entity the proposal is min(2*own, 1/2) >= own whenever own <= 1/2, so the entity
    is violated iff its share > 1/2 -- for any cluster-size mix. 300 random worlds."""
    rng = random.Random(20261013)
    refused = passed = 0
    for _ in range(300):
        k = rng.randint(1, 25)
        sizes = [rng.choice([1, 1, 1, 2, 2, 3, 5, 9, 17, 40]) for _ in range(k)]
        if sum(sizes) < 2:
            continue
        g = gate(build({"e": sizes}))
        inv = g["invariants"][SHARE]
        want = share(sizes) > CAP
        assert inv["violated"] == want, (sizes, share(sizes), inv)
        assert inv["measured"] == pytest.approx(share(sizes))
        assert consistent(g) == [], (sizes, consistent(g))
        refused += want
        passed += not want
    assert refused >= 30 and passed >= 30  # the sample really exercises both sides


def test_a6_exactly_one_half_is_not_a_majority():
    sizes = [11] + [1] * 10  # (11-1)/(21-1) = 0.5
    assert share(sizes) == CAP
    assert not gate(build({"e": sizes}))["invariants"][SHARE]["violated"]


# ═══ (b) single entity, healthy -> not refused ═══════════════════════════════════════════════════
@pytest.mark.parametrize(
    "sizes",
    [
        [2] * 500 + [1] * 20,  # Febrl-like: many two-record pairs, a few singles
        [2] * 15,  # 30 records, every cluster a pair
        [1] * 40,  # nothing merged
        [3] * 100 + [2] * 40 + [1] * 60,
        [5] + [2] * 100 + [1] * 300,  # one modest cluster among thousands
        list(range(1, 12)) + [1] * 400,  # staircase
    ],
)
def test_b1_healthy_single_entity_is_not_refused(sizes):
    g = gate(build({"e": sizes}))
    assert not g["would_fail"], {k: v for k, v in g["invariants"].items() if v["violated"]}
    assert g["entities_violating"][SHARE] == []
    assert consistent(g) == []
    assert _pre_load_failure(g) is None


def test_b2_healthy_single_entity_with_a_few_null_names_is_still_loaded():
    g = gate(build({"e": [2] * 200 + [1] * 50}, null_names={"e": 2}))
    assert not g["would_fail"], {k: v for k, v in g["invariants"].items() if v["violated"]}
    assert consistent(g) == []


def test_b3_the_defect_is_not_fixed_by_refusing_everything():
    """Negative control for the fix: a gate that refused every single-entity run would pass (a) but fail here."""
    healthy = [gate(build({"e": [2] * n + [1] * 5})) for n in (3, 30, 300)]
    giant = [gate(build({"e": giant_spec(n, n * 3 // 4)})) for n in (8, 40, 400)]
    assert [g["would_fail"] for g in healthy] == [False] * 3
    assert [g["would_fail"] for g in giant] == [True] * 3


# ═══ (c) multi-entity: slack stays, judged against the OTHER entities ════════════════════════════
def big_entities(n=6, per=500, largest=3):
    return {f"big{i}": [largest] * (per // largest) + [1] * (per % largest) for i in range(n)}


def test_c1_a_small_healthy_lookup_beside_large_entities_is_not_refused():
    """Slack must survive (existing P10 behaviour; this is the 'slack stays for multi-entity runs' half)."""
    spec = {**big_entities(), "lookup": [3] + [1] * 47}
    own = share(spec["lookup"])
    big_med = statistics.median(share(s) for s in spec.values())
    assert own > 2 * big_med  # without slack the lookup would be over the plain proposal
    g = gate(build(spec))
    assert not g["would_fail"], {k: v for k, v in g["invariants"].items() if v["violated"]}
    assert "lookup" not in g["entities_violating"][SHARE]


def test_c2_slack_is_judged_against_the_other_entities_largest_cluster():
    """Same 20-record lookup with one cluster of 6 (share 0.263). Beside entities whose clusters are <= 2 the
    slack (2*2-1)/(n-1) = 0.158 does not cover it; beside entities with clusters up to 30 it does."""
    lookup = [6] + [1] * 14
    small_world = {**big_entities(largest=2), "lookup": lookup}
    large_world = {
        **{f"big{i}": [30] * 20 + [1] * 400 for i in range(6)},
        "lookup": lookup,
    }
    assert "lookup" in gate(build(small_world))["entities_violating"][SHARE]
    assert "lookup" not in gate(build(large_world))["entities_violating"][SHARE]


def test_c3_an_entitys_own_giant_cluster_cannot_lift_its_own_threshold():
    """Two 30-record entities: A has a 21-record cluster, B is healthy. Slack for A comes from B only."""
    st = build({"A": giant_spec(30, 21), "B": [2] * 15})
    g = gate(st)
    assert "A" in g["entities_violating"][SHARE], g["invariants"][SHARE]
    assert g["would_fail"]


def test_c4_growing_ones_own_giant_never_turns_a_refusal_into_a_pass():
    """Monotone: with the sibling fixed, A's verdict can only go from pass to refused as its giant grows."""
    verdicts = []
    for g_size in range(2, 31):
        sizes = [g_size] + [1] * (30 - g_size)
        r = gate(build({"A": sizes, "B": [2] * 15}))
        verdicts.append("A" in r["entities_violating"][SHARE])
    assert verdicts == sorted(verdicts), verdicts  # False...False True...True
    assert verdicts[-1] is True and verdicts[0] is False


def test_c5_three_giant_entities_each_judged_without_themselves():
    """All three over-merged (0.62 / 0.55 / 0.52): the median of themselves must not excuse any of them."""
    spec = {"A": giant_spec(100, 63), "B": giant_spec(100, 56), "C": giant_spec(100, 53)}
    g = gate(build(spec))
    assert sorted(g["entities_violating"][SHARE]) == ["A", "B", "C"]


def test_c6_multi_entity_payload_is_consistent_when_no_small_entity_is_involved():
    for spec in (
        {**big_entities(), "giant": giant_spec(500, 400)},
        {"A": giant_spec(60, 40), "B": [2] * 30, "C": [2] * 30},
        big_entities(),
    ):
        g = gate(build(spec))
        assert consistent(g) == [], (spec.keys(), consistent(g))


# ═══ (d) never lifted above the 1/2 "never a majority" cap ═══════════════════════════════════════
def test_d1_no_entity_with_a_majority_cluster_is_ever_passed_random_multi_entity_worlds():
    rng = random.Random(424242)
    majority_seen = 0
    for _ in range(300):
        spec = {}
        for i in range(rng.randint(1, 6)):
            n_big = rng.choice([0, 0, 1])
            spec[f"e{i}"] = (
                [rng.randint(8, 60)] * n_big
                + [rng.choice([1, 2, 3, 4]) for _ in range(rng.randint(3, 40))]
                + [1] * rng.randint(0, 30)
            )
        g = gate(build(spec))
        for e, sizes in spec.items():
            if sum(sizes) > 1 and share(sizes) > CAP:
                majority_seen += 1
                assert e in g["entities_violating"][SHARE], (e, sizes, share(sizes), g["invariants"][SHARE])
        assert g["invariants"][SHARE]["threshold"] <= CAP
        assert g["invariants"]["null_identity_share"]["threshold"] <= CAP
    assert majority_seen >= 30


def test_d2_slack_from_huge_siblings_still_cannot_pass_a_majority_cluster():
    """Siblings with clusters of 1,000 records make 'twice the typical largest' astronomically large;
    a 12-record entity with a 9-record cluster (share 0.727) must still be refused."""
    spec = {f"big{i}": [1000] * 3 + [1] * 2000 for i in range(5)}
    spec["small"] = [9, 1, 1, 1]
    g = gate(build(spec))
    assert "small" in g["entities_violating"][SHARE]
    assert g["invariants"][SHARE]["threshold"] <= CAP


def test_d3_just_above_the_cap_beside_huge_siblings_is_refused_just_below_is_not():
    big = {f"big{i}": [800] * 3 + [1] * 3000 for i in range(5)}
    over = gate(build({**big, "s": [11, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]}))  # 10/20 = 0.5 -> ok
    over_more = gate(build({**big, "s": [12] + [1] * 10}))  # 11/21 = 0.524 -> refused
    assert "s" not in over["entities_violating"][SHARE]
    assert "s" in over_more["entities_violating"][SHARE]


# ═══ (e) plan 2.4: no fitted numeric constants in the diff of load_data.py ═══════════════════════
ALLOWED_NUMS = {0, 1, 2}  # 1/2 is written 1 / 2 in the existing cap; nothing else is sanctioned
FORBID_PARAM_RX = re.compile(r"(thresh|slack|cap|floor|ceil|limit|margin|ratio|share|min_|max_)", re.I)


def _num_counts(src: str) -> dict:
    out: dict = {}
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if isinstance(node.value, bool):
                continue
            out[node.value] = out.get(node.value, 0) + 1
    return out


def _numeric_param_defaults(src: str) -> set[str]:
    bad = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            pos = a.posonlyargs + a.args
            pairs = list(zip(pos[len(pos) - len(a.defaults) :], a.defaults)) + [
                (k, d) for k, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None
            ]
            for arg, d in pairs:
                if (
                    isinstance(d, ast.Constant)
                    and isinstance(d.value, (int, float))
                    and not isinstance(d.value, bool)
                    and FORBID_PARAM_RX.search(arg.arg)
                ):
                    bad.add(f"{node.name}({arg.arg}={d.value})")
    return bad


def new_numeric_literals(base_src: str, new_src: str) -> list:
    b, n = _num_counts(base_src), _num_counts(new_src)
    return sorted(v for v, c in n.items() if c > b.get(v, 0) and v not in ALLOWED_NUMS)


def _base_src() -> str:
    return subprocess.run(
        ["git", "show", f"{BASE_SHA}:{SRC_FILE}"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def test_e1_no_new_numeric_literal_and_no_numeric_threshold_default_in_load_data():
    base, new = _base_src(), (ROOT / SRC_FILE).read_text()
    assert new_numeric_literals(base, new) == []
    assert _numeric_param_defaults(new) <= _numeric_param_defaults(base)


def test_e2_added_diff_lines_carry_no_decimal_or_corpus_number():
    diff = subprocess.run(
        ["git", "diff", "-U0", BASE_SHA, "--", SRC_FILE], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    added = [
        ln[1:]
        for ln in diff.splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    code = [ln.split("#", 1)[0] for ln in added]
    hits = [ln for ln in code if re.search(r"(?<![\w.])\d+\.\d+|(?<![\w.])\d{2,}(?![\w.])", ln)]
    # numbers inside string/docstring text are ignored only when the whole line is prose
    hits = [ln for ln in hits if not re.match(r'\s*(["\']|[A-Za-z ,;:()\-]+$)', ln)]
    assert hits == [], hits


def test_e3_the_checker_itself_catches_a_fitted_constant():
    """Negative control for (e): both detectors must fire on a planted corpus-fitted constant."""
    base = "def f(x, share_cap=None):\n    return min(2 * x, 1 / 2)\n"
    fitted = "def f(x, share_cap=0.69):\n    return min(2 * x, 1 / 2) + 0.0074 + 21\n"
    assert new_numeric_literals(base, fitted) == [0.0074, 0.69, 21]
    assert _numeric_param_defaults(fitted) == {"f(share_cap=0.69)"}
    assert new_numeric_literals(base, base) == []


# ═══ guards: nothing else regressed ═══════════════════════════════════════════════════════════════
def test_g1_coverage_null_identity_and_flagged_still_refuse_a_single_entity():
    sizes = [2] * 20
    zero_map = build({"e": sizes})
    zero_map["field_mappings"] = {"e": {}}
    zero_map["unmapped_fields"] = {"e": []}
    assert gate(zero_map)["would_fail"]  # nothing maps -> floor
    nulls = gate(build({"e": [1] * 20}, null_names={"e": 16}))
    assert nulls["invariants"]["null_identity_share"]["violated"]


def test_g2_gate_is_read_only_and_json_clean():
    st = build({"A": giant_spec(30, 21), "B": [2] * 15})
    before = json.dumps(st, sort_keys=True)
    g = pre_load_gate(st)
    assert json.dumps(st, sort_keys=True) == before
    json.dumps(g, allow_nan=False)


def test_g3_operator_can_still_loosen_explicitly():
    """'A number the operator changed is used as given' is an existing, separate contract: only an explicit edit
    (not the echoed proposal) may lift the cap."""
    st = build({"person": giant_spec(30, 21)})
    g = gate(st, {SHARE: 0.9})
    assert not g["invariants"][SHARE]["violated"]


warnings.filterwarnings("ignore")
