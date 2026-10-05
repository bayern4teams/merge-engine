"""P19 acceptance (derived from the report's P19 text + plan section 9 P19 only; no P19 code seen).

Report P19: blocking must not be tuned against match quality (circular; pBlocking/BEER). Judge blocking
on zero-label quantities computed from the block collection (reduction ratio, K = sum|b|/2,
k = sum|b|/(|E1|+|E2|), per-stage pair counts); PC and PQ need ground truth, so they are reported
ONLY where ground truth exists and are ABSENT (not 0, not a guess) otherwise.

Run: TMPDIR=$PWD/.tmp .venv/bin/python -m pytest tests/part1/test_p19_acceptance.py -q -p no:cacheprovider

CONTRACT the implementation must satisfy (see .handoff/P19.acceptance.md):
  tests/eval/score.py::blocking_report(records, candidates, truth=None) -> dict
  keys always:   records, total_comparisons, candidate_pairs, reduction_ratio (absent if total_comparisons == 0),
                 block_assignments (= sum|b|), cep_K (= sum|b|/2), cnp_k (= sum|b|/N)
  keys only with a non-None truth (and a defined ratio): pair_completeness, pairs_quality

ADAPTATION POINTS: SCORE_MODULE / REPORT_FUNC / the key-name constants below.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MERGE_ENGINE_DISABLE_RESOLVE_CACHE", "1")

from src.mcp_server.tools import resolve_entities as re_
from tests.part1 import fixtures_matching as fx

ROOT = Path(__file__).resolve().parents[2]
ENGINE_SRC = Path(re_.__file__)
SCORE_SRC = Path(os.environ.get("P19_SCORE_SRC", ROOT / "tests" / "eval" / "score.py"))
BLOCKING_FUNC = "_generate_candidate_pairs"

# -- adaptation points ---------------------------------------------------------
SCORE_MODULE = os.environ.get(
    "P19_SCORE_MODULE", "tests.eval.score"
)  # env override: shim satisfiability only
REPORT_FUNC = "blocking_report"
K_PC, K_PQ = "pair_completeness", "pairs_quality"
K_RR, K_K, K_k = "reduction_ratio", "cep_K", "cnp_k"
K_CAND, K_TOTAL, K_N, K_SUM = "candidate_pairs", "total_comparisons", "records", "block_assignments"
GT_KEYS = {K_PC, K_PQ}
# P19 names the label-free quantities; a git ref after P5 and before any P19 edit (for the added-lines scans).
BASE_REF = "eeb280b"
SMALL_NUMBERS = {0, 1, 2, 0.5}  # structural only: the /2 in K, ratios 0 and 1
LABEL_RE = re.compile(
    r"truth|label|gold|ground|expected|same_entity|is_same|is_match|y_true|annotat|oracle|true_pairs",
    re.I,
)
# plan 2.4 corpus literals
CORPUS_RE = re.compile(
    r"\b(France|US|SAS|Berhad|Global|Group|Holdings|companyname|main_entity_id|company_relation|staff|"
    r"org_title|company_name|pagila|dvdrental)\b"
)
GROUND_TRUTH_MODULE_RE = re.compile(
    r"(^|\.)(tests|eval|golden|ground_?truth|truth|score|scorer)(\.|$)", re.I
)


def _report_fn():
    try:
        mod = importlib.import_module(SCORE_MODULE)
    except Exception as exc:
        pytest.fail(f"cannot import {SCORE_MODULE}: {exc!r}")
    fn = getattr(mod, REPORT_FUNC, None)
    if fn is None:
        pytest.fail(f"{SCORE_MODULE}.{REPORT_FUNC} does not exist (P19 contract)")
    return fn


def ID(row: str) -> str:
    return f"s:t:{row}"


def R(row: str, text: str) -> dict:
    return fx._rec("s", row, v=text)


def P(a: str, b: str) -> tuple[str, str]:
    return (ID(a), ID(b))


# Hand-checked example 1. N=5; tokens: a{x,y} b{x,z} c{y,z} d{w} e{x}.
# blocks (size>=2): x={a,b,e} y={a,c} z={b,c}; w={d} is a singleton and dropped.
# sum|b| = 7 -> K = 3.5, k = 7/5 = 1.4. all-pairs = 10.
EX1 = [R("a", "x y"), R("b", "x z"), R("c", "y z"), R("d", "w"), R("e", "x")]
EX1_CAND = [P("a", "b"), P("a", "c")]
EX1_ZERO = {K_N: 5, K_TOTAL: 10, K_CAND: 2, K_RR: 0.8, K_SUM: 7, K_K: 3.5, K_k: 1.4}
# truth {ab, cd, be}: retained true = {ab} -> PC = 1/3, PQ = 1/2
EX1_TRUTH = [P("a", "b"), P("c", "d"), P("b", "e")]

# Hand-checked example 2: blocks with an identical member set count once (the engine keeps one).
# N=3; p and q both = {a,b}; r singleton. one block {a,b}: sum|b| = 2 -> K = 1, k = 2/3; RR = 1 - 1/3.
EX2 = [R("a", "p q"), R("b", "p q"), R("c", "r")]
EX2_CAND = [P("a", "b")]


def _approx_eq(report: dict, want: dict) -> None:
    for k, v in want.items():
        assert k in report, f"missing key {k!r}; got {sorted(report)}"
        assert report[k] == pytest.approx(v), f"{k}: {report[k]!r} != {v!r}"


# ============================ (1) static: no labels reach the blocking path ========================
def _module_funcs(tree: ast.AST) -> dict[str, ast.AST]:
    return {
        n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _params(fn: ast.AST) -> list[str]:
    a = fn.args  # type: ignore[attr-defined]
    out = [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]
    return out + [x.arg for x in (a.vararg, a.kwarg) if x]


def _reachable(funcs: dict[str, ast.AST], root: str) -> set[str]:
    seen, todo = set(), [root]
    while todo:
        name = todo.pop()
        if name in seen or name not in funcs:
            continue
        seen.add(name)
        for n in ast.walk(funcs[name]):
            if isinstance(n, ast.Name) and n.id in funcs:
                todo.append(n.id)
    return seen


def label_params_on_path(source: str, root: str) -> list[str]:
    """Label-ish parameter names on any function reachable from ``root``."""
    funcs = _module_funcs(ast.parse(source))
    return sorted(
        f"{name}({p})"
        for name in _reachable(funcs, root)
        for p in _params(funcs[name])
        if LABEL_RE.search(p)
    )


def ground_truth_imports(source: str) -> list[str]:
    out = []
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Import):
            out += [a.name for a in n.names if GROUND_TRUTH_MODULE_RE.search(a.name)]
        elif isinstance(n, ast.ImportFrom):
            mods = [n.module or ""] + [f"{n.module or ''}.{a.name}" for a in n.names]
            out += [m for m in mods if GROUND_TRUTH_MODULE_RE.search(m)]
    return out


def test_1a_blocking_path_takes_no_label_argument() -> None:
    bad = label_params_on_path(ENGINE_SRC.read_text(), BLOCKING_FUNC)
    assert not bad, f"label-like inputs on the blocking path: {bad}"
    sig = inspect.signature(getattr(re_, BLOCKING_FUNC))
    assert not [p for p in sig.parameters if LABEL_RE.search(p)], list(sig.parameters)


def test_1b_engine_imports_no_ground_truth_or_eval_module() -> None:
    bad = ground_truth_imports(ENGINE_SRC.read_text())
    assert not bad, f"resolve_entities imports eval/ground-truth modules: {bad}"


def test_1c_no_src_blocking_or_tuning_function_takes_labels_and_no_src_imports_tests() -> None:
    name_re = re.compile(r"block|purg|candidate_pairs|tune|calibrat", re.I)
    offenders, leaks = [], []
    for path in sorted((ROOT / "src").rglob("*.py")):
        text = path.read_text()
        tree = ast.parse(text)
        for name, fn in _module_funcs(tree).items():
            if name_re.search(name):
                offenders += [f"{path.name}:{name}({p})" for p in _params(fn) if LABEL_RE.search(p)]
        leaks += [
            f"{path.name}: {m}" for m in ground_truth_imports(text) if m.split(".")[0] == "tests"
        ]
    assert not offenders, offenders
    assert not leaks, leaks


def test_1d_static_checkers_detect_a_violation() -> None:
    """Mutation proof: the detectors above are not vacuous."""
    bad_fn = "def _generate_candidate_pairs(records, truth=None):\n    return _helper(records)\n"
    assert label_params_on_path(bad_fn, BLOCKING_FUNC) == ["_generate_candidate_pairs(truth)"]
    via_helper = "def _generate_candidate_pairs(records):\n    return h(records)\ndef h(r, match_labels): ...\n"
    assert label_params_on_path(via_helper, BLOCKING_FUNC) == ["h(match_labels)"]
    assert ground_truth_imports("from tests.eval.golden import build_golden_pairs")
    assert ground_truth_imports("import tests.eval.score as s")
    assert not ground_truth_imports("import math\nfrom collections import Counter")


# ============================ (2) zero-label quantities from the block collection ===================
def test_2a_hand_example_zero_label_quantities() -> None:
    rep = _report_fn()(EX1, EX1_CAND)
    _approx_eq(rep, EX1_ZERO)


def test_2b_identical_member_set_blocks_count_once() -> None:
    rep = _report_fn()(EX2, EX2_CAND)
    _approx_eq(
        rep, {K_N: 3, K_TOTAL: 3, K_CAND: 1, K_SUM: 2, K_K: 1.0, K_k: 2 / 3, K_RR: 1 - 1 / 3}
    )


def test_2c_accepts_engine_four_tuples_and_dedups_order_and_self_pairs() -> None:
    fn = _report_fn()
    four = [(ID("a"), ID("b"), EX1[0], EX1[1]), (ID("c"), ID("a"), EX1[2], EX1[0])]
    noisy = [P("a", "b"), P("b", "a"), P("a", "c"), P("a", "a")]
    for cand in (four, noisy):
        _approx_eq(fn(EX1, cand), EX1_ZERO)


def test_2d_reduction_ratio_extremes_and_empty_input() -> None:
    fn = _report_fn()
    ids = [ID(r) for r in "abcde"]
    allp = [(x, y) for i, x in enumerate(ids) for y in ids[i + 1 :]]
    assert fn(EX1, allp)[K_RR] == pytest.approx(0.0)
    none = fn(EX1, [])
    assert none[K_RR] == pytest.approx(1.0) and none[K_CAND] == 0
    for recs in (
        [],
        EX1[:1],
    ):  # no comparisons exist: RR undefined -> absent, never a guess; no crash
        rep = fn(recs, [])
        assert rep[K_TOTAL] == 0 and K_RR not in rep


def test_2e_no_ground_truth_means_no_pc_pq_and_no_placeholder() -> None:
    rep = _report_fn()(EX1, EX1_CAND)
    assert not (GT_KEYS & set(rep)), f"PC/PQ present without ground truth: {rep}"
    assert not [k for k in rep if re.search(r"complete|quality|recall|precision|f1", k, re.I)], (
        sorted(rep)
    )
    assert all(v is not None and v == v for v in rep.values()), "None/NaN placeholder in report"


def test_2f_deterministic_across_order_repeat_and_hash_seed() -> None:
    fn = _report_fn()
    base = fn(EX1, EX1_CAND)
    assert fn(EX1, EX1_CAND) == base
    shuffled = EX1[:]
    random.Random(3).shuffle(shuffled)
    assert fn(shuffled, list(reversed(EX1_CAND))) == base
    prog = (
        "import json,importlib;from tests.part1 import test_p19_acceptance as t;"
        "s=importlib.import_module(t.SCORE_MODULE);"
        "print(json.dumps(getattr(s,t.REPORT_FUNC)(t.EX1,t.EX1_CAND),sort_keys=True))"
    )
    outs = set()
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "MERGE_ENGINE_DISABLE_RESOLVE_CACHE": "1"}
        r = subprocess.run(
            [sys.executable, "-c", prog], cwd=ROOT, env=env, capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr[-800:]
        outs.add(r.stdout.strip().splitlines()[-1])
    assert len(outs) == 1, outs


def test_2g_engine_stage_counts_reported_and_consistent_with_report() -> None:
    """Per-stage pair counts already live in ``stats``; pin them and tie them to blocking_report."""
    recs = fx.p2_big_block(n_records=120, n_dupes=10)["records"]
    stats: dict = {}
    pairs = getattr(re_, BLOCKING_FUNC)(recs, stats=stats)
    for key in (
        "candidate_pairs",
        "blocks_used",
        "blocks_purged",
        "block_memberships_filtered",
        "pairs_pruned_by_purging",
        "pairs_pruned_by_meta_blocking",
    ):
        assert isinstance(stats.get(key), int) and stats[key] >= 0, (key, stats)
    assert stats["candidate_pairs"] == len(pairs)
    rep = _report_fn()(recs, pairs)
    assert rep[K_CAND] == stats["candidate_pairs"] == len(pairs)
    n = len(recs)
    assert rep[K_RR] == pytest.approx(1 - len(pairs) / (n * (n - 1) // 2))
    assert rep[K_k] == pytest.approx(rep[K_SUM] / n) and rep[K_K] == pytest.approx(rep[K_SUM] / 2)
    assert not (GT_KEYS & set(rep))


# ============================ (3) scorer contract: PC / PQ only with ground truth ====================
def test_3a_pc_pq_hand_example() -> None:
    rep = _report_fn()(EX1, EX1_CAND, truth=EX1_TRUTH)
    _approx_eq(
        rep, {K_PC: 1 / 3, K_PQ: 1 / 2, **EX1_ZERO}
    )  # zero-label values unchanged by supplying truth


def test_3b_truth_order_and_duplicates_do_not_matter() -> None:
    fn = _report_fn()
    flipped = [(b, a) for a, b in EX1_TRUTH] + [EX1_TRUTH[0]]
    for t in (flipped, set(EX1_TRUTH), tuple(EX1_TRUTH)):
        rep = fn(EX1, EX1_CAND, truth=t)
        assert rep[K_PC] == pytest.approx(1 / 3) and rep[K_PQ] == pytest.approx(1 / 2)


def test_3c_truth_none_vs_given_only_adds_the_two_keys() -> None:
    fn = _report_fn()
    assert set(fn(EX1, EX1_CAND, truth=EX1_TRUTH)) - set(fn(EX1, EX1_CAND)) == GT_KEYS
    assert inspect.signature(fn).parameters["truth"].default is None


def test_3d_pc_pq_extremes_and_undefined_ratios_are_absent_not_zero() -> None:
    fn = _report_fn()
    full = fn(EX1, EX1_CAND, truth=EX1_CAND)
    assert full[K_PC] == pytest.approx(1.0) and full[K_PQ] == pytest.approx(1.0)
    miss = fn(
        EX1, EX1_CAND, truth=[P("d", "e")]
    )  # genuinely retained nothing true: 0.0 is a real value
    assert miss[K_PC] == pytest.approx(0.0) and miss[K_PQ] == pytest.approx(0.0)
    assert K_PQ not in fn(EX1, [], truth=EX1_TRUTH)  # 0 retained pairs: PQ is 0/0
    assert fn(EX1, [], truth=EX1_TRUTH)[K_PC] == pytest.approx(0.0)  # but PC = 0/3 is real
    assert K_PC not in fn(EX1, EX1_CAND, truth=set())  # 0 true pairs: PC is 0/0, undefined


def test_3e_planted_fixture_matches_independent_oracle() -> None:
    fx_ = fx.p2_big_block(n_records=200, n_dupes=15)
    recs = fx_["records"]
    truth = {tuple(sorted(c)) for c in fx_["truth"]}
    pairs = getattr(re_, BLOCKING_FUNC)(recs)
    got = {tuple(sorted((p[0], p[1]))) for p in pairs}
    rep = _report_fn()(recs, pairs, truth=truth)
    assert rep[K_PC] == pytest.approx(len(truth & got) / len(truth))
    assert rep[K_PQ] == pytest.approx(len(truth & got) / len(got))
    assert K_PC not in _report_fn()(recs, pairs)


def test_3f_scorer_is_the_only_label_consumer_blocking_never_imports_it() -> None:
    assert not [m for m in ground_truth_imports(ENGINE_SRC.read_text()) if "score" in m]


# ============================ (5) plan 2.4: no corpus literals, no fitted numbers =====================
def _added_lines(path: Path) -> dict[int, str]:
    if not path.is_relative_to(
        ROOT
    ):  # shim outside the repo (satisfiability run): every line is new
        return dict(enumerate(path.read_text().splitlines(), 1))
    diff = subprocess.run(
        ["git", "diff", "-U0", BASE_REF, "--", str(path.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    out: dict[int, str] = {}
    line = 0
    for ln in diff.splitlines():
        m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", ln)
        if m:
            line = int(m.group(1))
        elif ln.startswith("+") and not ln.startswith("+++"):
            out[line] = ln[1:]
            line += 1
    return out


def _touched_funcs(path: Path, only: set[str] | None) -> list[ast.AST]:
    added = _added_lines(path)
    tree = ast.parse(path.read_text())
    return [
        fn
        for name, fn in _module_funcs(tree).items()
        if (only is None or name in only) and any(fn.lineno <= ln <= fn.end_lineno for ln in added)  # type: ignore[attr-defined]
    ]


def numeric_literals(node: ast.AST) -> set[float]:
    return {
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)
    }


def test_5a_no_corpus_literals_in_added_lines() -> None:
    hits = []
    for path in (ENGINE_SRC, SCORE_SRC):
        hits += [
            f"{path.name}:{ln}: {t.strip()}"
            for ln, t in _added_lines(path).items()
            if CORPUS_RE.search(t)
        ]
    assert not hits, hits


def test_5b_no_fitted_numeric_constants_in_new_code() -> None:
    bad = []
    for path, only in ((SCORE_SRC, {REPORT_FUNC}), (ENGINE_SRC, {BLOCKING_FUNC, "_token_keys"})):
        for fn in _touched_funcs(path, only):
            extra = numeric_literals(fn) - SMALL_NUMBERS
            if extra:
                bad.append(f"{path.name}:{fn.name}: {sorted(extra)}")  # type: ignore[attr-defined]
    assert not bad, bad


def test_5c_report_function_has_no_numeric_literal_beyond_structural() -> None:
    fn = _module_funcs(ast.parse(SCORE_SRC.read_text())).get(REPORT_FUNC)
    assert fn is not None, f"{REPORT_FUNC} missing from tests/eval/score.py"
    assert numeric_literals(fn) <= SMALL_NUMBERS, numeric_literals(fn) - SMALL_NUMBERS
    assert not CORPUS_RE.search(ast.get_source_segment(SCORE_SRC.read_text(), fn) or "")


def test_5d_checkers_detect_a_violation() -> None:
    assert numeric_literals(ast.parse("def f(x):\n    return x * 0.37 + 200")) == {0.37, 200}
    assert CORPUS_RE.search("if name.endswith('Berhad'):") and CORPUS_RE.search("table = 'staff'")
    assert not CORPUS_RE.search("def blocking_report(records, candidates, truth=None):")
